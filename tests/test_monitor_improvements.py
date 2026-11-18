"""Per-camera model choices, visible evidence sections and reversible completion."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication, QDialog, QTextEdit
from core.records import append_feedback, load_run
from core.review_workflow import phase
from core.runtime import ASSET_ROOT
from ui.monitor_center import MonitorCenter, PointDialog
from ui.point_models import PointModelSelector


class StubWorker(QThread):
    started_run = Signal(dict)
    outcome = Signal(dict)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self.running = False

    def start(self):
        self.running = True

    def isRunning(self):
        return self.running

    def stop(self):
        self.running = False


class MonitorImprovementsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.desks = []

    def tearDown(self):
        for desk in self.desks:
            desk.workers.clear()
            desk.shutdown()
            desk.close()
        self.app.processEvents()

    def desk(self):
        desk = MonitorCenter(registry_path=self.root / f'points-{len(self.desks)}.json', worker_class=StubWorker)
        desk.timer.stop()
        self.desks.append(desk)
        return desk

    def point(self, identifier, **settings):
        return {'id': identifier, 'name': '监控' + identifier, 'location': '本地演示位置',
                'kind': 'file', 'source': str(ASSET_ROOT / 'videos/fireplace_cc0.webm'), **settings}

    def make_run(self, identifier=1):
        root = self.root / ('source-run-' + str(identifier))
        root.mkdir()
        notice = {'notification_id': 'n1', 'event_id': identifier, 'label': 'fire' if identifier else 'scene',
                  'level': 'MEDIUM', 'score': 44.1, 'kind': 'event_confirmed' if identifier else 'scene_attention',
                  'timestamp_s': 2., 'channels': [], **({'origin': 'ai_scene'} if identifier is None else {})}
        events = [] if identifier is None else [{'event_id': identifier, 'label': 'fire', 'confirmed_at_s': 2.,
            'started_at_s': 1., 'closed_at_s': 3., 'risk_summary': {'peak_score': 44.1, 'peak_level': 'MEDIUM'}}]
        for name, value in {'summary.json': {'status': 'completed'}, 'config.json': {'source': str(ASSET_ROOT / 'videos/fireplace_cc0.webm'),
            'source_kind': 'file', 'model_profile': 'original', 'model_profile_name': '通用监看'},
            'events.json': {'events': events}}.items():
            (root / name).write_text(json.dumps(value), encoding='utf-8')
        (root / 'alerts.jsonl').write_text(json.dumps(notice) + '\n', encoding='utf-8')
        scene = {'event_id': identifier, 'scene': {'description': '厨房水槽内可见火焰，旁边有人。', 'scene': 'cooking',
            'relation': 'unclear', 'evidence_quality': 'adequate', 'spread': 'unknown',
            'visible_evidence': ['水槽中有火焰'], 'hazard_cues': ['火焰超出局部区域'],
            'uncertainties': ['是否扩散需要继续核查'], 'next_check': '核对周围物体。'},
            'model': 'software-fixture-vision', 'elapsed_s': .1, 'frame_count': 3, 'span_s': 1.,
            'evidence': [{'timestamp_s': t} for t in (1., 1.5, 2.)]}
        (root / 'scene-reviews.jsonl').write_text(json.dumps(scene) + '\n', encoding='utf-8')
        return root, notice

    def test_each_worker_gets_its_own_saved_model_and_actual_model_stays_visible(self):
        desk = self.desk()
        desk.set_devices([self.point('a'), self.point('b', model_profile='domestic'), self.point('c')])
        desk.analysis_preferences = lambda: {'model_profile': 'original', 'association_profile': 'original',
                                           'confirmation_profile': 'standard', 'conf': .4}
        desk.start_device('a')
        desk.start_device('b')
        self.assertEqual(desk.workers['a'].options.model_profile, 'original')
        self.assertEqual(desk.workers['b'].options.model_profile, 'domestic')
        self.assertEqual(desk.workers['a'].options.conf, .4)
        self.assertEqual(desk.workers['b'].options.conf, .35)
        self.assertFalse(desk.cards['a'].model_button.isEnabled())
        self.assertTrue(desk.cards['c'].model_button.isEnabled())
        desk.analysis_preferences = lambda: {'model_profile': 'forest'}
        desk._refresh_model_labels()
        self.assertIn('通用监看', desk.cards['a'].model_label.text())
        self.assertIn('家庭室内', desk.cards['b'].model_label.text())

    def test_configuring_stopped_point_preserves_other_running_workers(self):
        desk = self.desk()
        desk.set_devices([self.point('a'), self.point('b')])
        desk.start_device('a')
        worker, card = desk.workers['a'], desk.cards['a']
        def choose(dialog):
            selector = dialog.findChild(PointModelSelector)
            selector.set_model_profile('forest')
            return QDialog.DialogCode.Accepted
        with patch.object(QDialog, 'exec', new=choose):
            desk.configure_model('b')
        self.assertIs(desk.workers['a'], worker)
        self.assertIs(desk.cards['a'], card)
        self.assertTrue(worker.isRunning())
        self.assertEqual(desk._device('b')['model_profile'], 'forest')
        self.assertEqual(next(p for p in desk.registry.load() if p['id'] == 'b')['model_profile'], 'forest')

    def test_add_point_dialog_retains_the_selected_model(self):
        form = PointDialog(self.point('a'))
        form.model_settings.set_model_profile('domestic')
        form._accept()
        self.assertEqual(form.result(), QDialog.DialogCode.Accepted)
        self.assertEqual(form.value['model_profile'], 'domestic')
        form.close()

    def test_monitor_undo_returns_to_action_or_review_and_survives_reload(self):
        for identifier in (1, None):
            with self.subTest(identifier=identifier):
                root, notice = self.make_run(identifier)
                append_feedback(root, notice, 'confirmed_abnormal')
                append_feedback(root, notice, 'resolved')
                original = (root / 'events.json').read_bytes()
                desk = self.desk()
                desk.restore_saved_events([load_run(root)])
                desk.set_event_mode(True)
                key = next(iter(desk.events))
                desk.selected_key = key
                desk._render_events()
                self.assertEqual(desk.reopen_button.text(), '撤回处理')
                self.assertFalse(desk.reopen_button.isHidden())
                desk.reopen_button.click()
                self.assertEqual(phase(desk.events[key]['review']), 'action')
                self.assertIn('#a45b10', desk.handling_badge.styleSheet())
                self.assertEqual(desk.workflow_tabs.currentIndex(), 2)
                self.assertEqual(desk.selected_key, key)
                self.assertTrue(desk.reopen_button.isHidden())
                self.assertEqual((root / 'events.json').read_bytes(), original)
                record = load_run(root)
                self.assertEqual(record['feedback'][-1]['action'], 'reopened')
                reloaded = self.desk()
                reloaded.restore_saved_events([record])
                self.assertEqual(phase(next(iter(reloaded.events.values()))['review']), 'action')

    def test_withdrawing_normal_conclusion_resets_choice_and_red_pending_state(self):
        root, notice = self.make_run()
        append_feedback(root, notice, 'normal_use')
        desk = self.desk()
        desk.restore_saved_events([load_run(root)])
        desk.set_event_mode(True)
        desk.selected_key = next(iter(desk.events))
        desk._render_events()
        self.assertEqual(desk.reopen_button.text(), '撤回结论')
        desk.reopen_button.click()
        self.assertEqual(phase(desk.events[desk.selected_key]['review']), 'pending')
        self.assertEqual(desk.review_choice.currentData(), 'needs_verification')
        self.assertIn('#b82f48', desk.handling_badge.styleSheet())

    def test_description_and_numbers_have_separate_visible_cards_not_a_text_dump(self):
        root, _ = self.make_run()
        desk = self.desk()
        desk.restore_saved_events([load_run(root)])
        desk.selected_key = next(iter(desk.events))
        desk._show_selected()
        summary = desk.analysis_summary
        self.assertIn('厨房水槽', summary.description_card.body.text())
        self.assertIn('44.1', summary.result_card.score_label.text())
        self.assertIn(('检测模型', '通用监看'), summary.metrics_table.rows)
        self.assertTrue(desk.scene_text.isHidden())
        self.assertEqual(summary.findChildren(QTextEdit), [])

    def test_historical_model_is_not_taken_from_current_device_selection(self):
        root, _ = self.make_run()
        config = json.loads((root / 'config.json').read_text(encoding='utf-8'))
        config.pop('model_profile_name')
        config['model_profile'] = 'domestic'
        (root / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        desk = self.desk()
        desk.restore_saved_events([load_run(root)])
        desk.selected_key = next(iter(desk.events))
        desk._show_selected()
        self.assertIn(('检测模型', '家庭室内'), desk.analysis_summary.metrics_table.rows)

    def test_reopen_failure_is_visible_outside_the_collapsed_form(self):
        root, notice = self.make_run()
        append_feedback(root, notice, 'normal_use')
        desk = self.desk()
        desk.restore_saved_events([load_run(root)])
        desk.selected_key = next(iter(desk.events))
        desk._show_selected()
        self.assertTrue(desk.review_form.isHidden())
        with patch('ui.monitor_center.append_feedback', side_effect=OSError('写入暂不可用')):
            desk.reopen_button.click()
        self.assertFalse(desk.handling_notice.isHidden())
        self.assertIn('写入暂不可用', desk.handling_notice.text())
        self.assertEqual(phase(desk.events[desk.selected_key]['review']), 'completed')

    def test_plain_priority_uses_saved_grade_and_keeps_rule_score_in_details(self):
        root, _ = self.make_run()
        desk = self.desk()
        desk.restore_saved_events([load_run(root)])
        desk.selected_key = next(iter(desk.events))
        desk._show_selected()
        summary = desk.analysis_summary
        self.assertEqual(summary.result_card.priority_level, 'MEDIUM')  # 44.1 remains MEDIUM during hysteresis.
        self.assertEqual(summary.result_card.score_value, 44.1)
        self.assertTrue(summary.result_card.score_label.isHidden())
        self.assertEqual(summary.result_card.conclusion_label.text(), '优先核查')
        self.assertNotIn('44.1', str(summary.metrics_table.rows))
        self.assertIn(('辅助评分（规则分）', '44.1 / 100'), summary.details_table.rows)

    def test_detection_match_is_not_replaced_by_a_fire_probability(self):
        root, _ = self.make_run()
        desk = self.desk()
        desk.restore_saved_events([load_run(root)])
        desk.selected_key = next(iter(desk.events))
        event = desk.events[desk.selected_key]
        event['assessment'] = {'score': 44.1, 'level': 'MEDIUM', 'fresh_evidence': True,
                               'features': {'confidence': .91, 'fresh_evidence': True}}
        desk._show_selected()
        self.assertIn(('检测匹配分', '0.91 / 1'), desk.analysis_summary.details_table.rows)
        self.assertIn('不是现场发生火灾的概率', str(desk.analysis_summary.details_table.rows))
        event['assessment']['features']['fresh_evidence'] = False
        desk._show_selected()
        self.assertIn(('检测匹配分', '暂无有效检测值'), desk.analysis_summary.details_table.rows)


if __name__ == '__main__':
    unittest.main()
