"""Single-video scene results keep event identity and tolerate AI failure."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QScrollArea, QTextEdit
from core.scene_review import save_scene_review
from ui.scene_panel import SceneDescriptionPanel
from ui.analysis_cards import AnalysisSummary


class SingleScenePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.panel = SceneDescriptionPanel()
        self.event_row = {'event_id': 1, 'label': 'fire', 'risk_summary': {'peak_score': 48, 'peak_level': 'MEDIUM'}}
        self.frame = {'timestamp_s': 1., 'jpeg': b'fixture-only', 'sha256': hashlib.sha256(b'fixture-only').hexdigest()}
        self.result = {'scene': {'description': '测试画面中可见桌面上的蜡烛。', 'scene': 'candle',
            'relation': 'expected_burning', 'spread': 'unknown', 'evidence_quality': 'limited',
            'visible_evidence': ['蜡烛'], 'hazard_cues': [], 'uncertainties': ['单帧'], 'next_check': '人工核对周边。'},
            'model': 'test-fixture-only', 'elapsed_s': .1, 'frame_count': 1, 'span_s': 0.}

    def tearDown(self):
        self.panel.shutdown()
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def make_run(self, name):
        run = self.root / name
        run.mkdir()
        (run / 'summary.json').write_text(json.dumps({'status': 'completed', 'processed_frames': 10}), encoding='utf-8')
        return run

    def test_saved_description_loads_and_late_previous_video_result_cannot_replace_current_video(self):
        first, second = self.make_run('first'), self.make_run('second')
        stored = save_scene_review(first, 1, [self.frame], self.result)
        self.panel.set_target(first, self.event_row, {'source': 'first.avi'})
        self.assertEqual(self.panel.description.text(), self.result['scene']['description'])
        self.panel.set_target(second, self.event_row, {'source': 'second.avi'})
        self.panel._done({'ok': True, 'key': (str(first.resolve()), 1), 'record': stored})
        self.assertIn('尚未生成', self.panel.description.text())
        self.assertFalse(self.panel.details.isEnabled())
        self.assertEqual(self.panel.target_key, (str(second.resolve()), 1))

    def test_ai_failure_clears_busy_state_and_keeps_original_record_for_retry(self):
        run = self.make_run('failure')
        original = (run / 'summary.json').read_bytes()
        self.panel.set_target(run, self.event_row, {'source': 'fixture.avi'})
        with patch('ui.monitor_worker.saved_event_frames', return_value=[self.frame]), \
             patch('ui.monitor_worker.analyze_scene', side_effect=RuntimeError('测试：本机AI暂不可用')):
            self.panel.generate.click()
            deadline = time.monotonic() + 3
            while self.panel.is_busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertFalse(self.panel.is_busy)
        self.assertIn('本机AI暂不可用', self.panel.status.text())
        self.assertTrue(self.panel.generate.isEnabled())
        self.assertEqual((run / 'summary.json').read_bytes(), original)
        self.assertFalse((run / 'scene-reviews.jsonl').exists())
        self.assertFalse((run / '.scene-active').exists())

    def test_automatic_description_waits_for_record_completion_and_other_ai_work(self):
        run = self.make_run('automatic')
        self.panel.set_target(run, self.event_row, {}, running=True)
        self.panel.request_auto()
        with patch.object(self.panel, 'start_review') as start:
            self.panel._tick()
            start.assert_not_called()
            self.panel.set_target(run, self.event_row, {}, running=False)
            self.panel.can_start = lambda: False
            self.panel._tick()
            start.assert_not_called()
            self.panel.can_start = lambda: True
            self.panel._tick()
            start.assert_called_once()

    def test_live_evidence_can_start_analysis_before_video_summary_exists(self):
        run = self.make_run('live')
        (run / 'summary.json').unlink()
        self.panel.set_target(run, self.event_row, {}, running=True)
        self.assertFalse(self.panel.available)
        frames = [self.frame, {**self.frame, 'timestamp_s': 2.}]
        self.panel.observe_live(run, self.event_row, frames)
        self.assertTrue(self.panel.available)
        with patch.object(self.panel, 'start_review') as start:
            self.panel._tick()
            start.assert_called_once()
        self.assertFalse((run / 'summary.json').exists())

    def test_preview_followed_by_invalid_final_output_never_becomes_a_saved_review(self):
        run = self.make_run('partial')
        self.panel.set_target(run, self.event_row, {})
        def incomplete(*args, on_progress=None, **kwargs):
            on_progress({'description': '桌面上可见蜡烛火焰。', 'elapsed_s': .01, 'preliminary': True})
            raise ValueError('AI未返回完整复核结果。')
        with patch('ui.monitor_worker.saved_event_frames', return_value=[self.frame]), \
             patch('ui.monitor_worker.analyze_scene', side_effect=incomplete):
            self.panel.generate.click()
            deadline = time.monotonic() + 3
            while self.panel.is_busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
        self.assertIn('蜡烛', self.panel.description.text())
        self.assertIn('尚未完成复核', self.panel.status.text())
        self.assertFalse(self.panel.details.isEnabled())
        self.assertFalse(self.panel.records.get(self.panel.target_key))
        self.assertFalse((run / 'scene-reviews.jsonl').exists())

    def combined_assessment(self, *, score=63.4, level='MEDIUM', status='current', delta=5.4):
        return {'score': score, 'level': level, 'dominant_event_id': 1,
            'events': [{'event_id': 1, 'label': 'fire', 'score': score, 'level': level,
                'fusion': {'reason': '根据场景证据与持续变化共同计算。'}}],
            'fusion': {'policy': 'dynamic-scene-review-test', 'baseline_score': 58.,
                'baseline_level': 'MEDIUM', 'context_delta': delta,
                'scene_status': status, 'scene_only_hazard': False, 'scene': 'cooking'},
            'independent_review': {'decision': {'title': '内部证据结论，不能作为主界面结论'}}}

    def test_primary_card_shows_one_final_score_and_level_without_weight_equation(self):
        value = self.combined_assessment()
        self.panel.show_fusion(value)
        self.assertFalse(self.panel.fusion_card.isHidden())
        self.assertEqual(self.panel.fusion.text(), '63.4 / 100')
        self.assertIn('优先核查', self.panel.final_level.text())
        self.assertIn('异常线索', self.panel.fusion_state.text())
        visible = ' '.join(label.text() for label in (self.panel.fusion_title, self.panel.fusion,
            self.panel.final_level, self.panel.fusion_state))
        self.assertNotIn('58.0', visible)
        self.assertNotIn('5.4', visible)
        self.assertNotIn('→', visible)
        self.assertNotIn('＋', visible)
        self.assertNotIn('内部证据结论', visible)
        self.assertIn('58.0', self.panel.fusion.toolTip())
        self.assertIn('烹饪', self.panel.fusion.toolTip())

    def test_waiting_expired_and_normal_use_keep_recorded_final_result(self):
        for state, word in (('pending', '等待'), ('expired', '过期'), ('invalid', '不可用')):
            with self.subTest(state=state):
                value = self.combined_assessment(score=58., status=state, delta=0.)
                self.panel.show_fusion(value)
                self.assertEqual(self.panel.fusion.text(), '58.0 / 100')
                self.assertIn(word, self.panel.fusion_state.text())
        value = self.combined_assessment(score=46.2, delta=-11.8)
        value['fusion']['scene'] = 'candle'
        self.panel.show_fusion(value)
        self.assertEqual(self.panel.fusion.text(), '46.2 / 100')
        self.assertIn('受控用火', self.panel.fusion_state.text())
        self.assertNotIn('安全', self.panel.fusion_state.text())
        self.assertNotIn('降低等级', self.panel.fusion_state.text())

    def test_historical_final_value_is_displayed_without_reinterpreting_it(self):
        value = self.combined_assessment(score=38.4, level='LOW', delta=-9.6)
        value['fusion'].update(policy='scene-fusion-v1', baseline_score=48.)
        before = json.dumps(value, sort_keys=True)
        self.panel.show_fusion(value)
        self.assertEqual(self.panel.fusion_title.text(), '历史研判建议')
        self.assertEqual(self.panel.fusion.text(), '38.4 / 100')
        self.assertIn('请核查', self.panel.final_level.text())
        self.assertIn('未按当前规则重算', self.panel.fusion_state.text())
        self.assertEqual(before, json.dumps(value, sort_keys=True))

    def test_no_confirmed_event_still_has_one_final_card_and_clear_resets_it(self):
        value = self.combined_assessment(score=0., level='NORMAL', delta=0.)
        value.update(events=[], dominant_event_id=None)
        value['fusion']['scene_only_hazard'] = True
        self.panel.show_fusion(value)
        self.assertFalse(self.panel.fusion_card.isHidden())
        self.assertEqual(self.panel.fusion.text(), '0.0 / 100')
        self.assertIn('核查原画面', self.panel.fusion_state.text())
        self.panel.show_fusion(None)
        self.assertTrue(self.panel.fusion_card.isHidden())

    def test_details_explain_contributions_and_mark_old_joint_records_as_history(self):
        value = dict(self.result)
        value['priority'] = {'policy': 'dynamic-scene-review-test', 'baseline_score': 58.,
            'score': 63.4, 'reason': '根据场景证据与持续变化共同计算。'}
        text = self.panel._detail_text(value)
        self.assertIn('烟火检测原始指数：58.0', text)
        self.assertIn('综合核查指数：63.4', text)
        self.assertNotIn('→', text)
        self.assertNotIn('两路独立判断', text)
        value['priority']['policy'] = 'context-review-v1'
        text = self.panel._detail_text(value)
        self.assertIn('历史联合结果（保留当时记录）', text)
        self.assertIn('当时联合指数：63.4', text)

    def test_detail_dialog_uses_classified_cards_and_only_one_outer_scroll_area(self):
        run = self.make_run('detail-cards')
        self.panel.set_target(run, self.event_row, {'source': 'fixture.avi'})
        self.panel.records[self.panel.target_key] = dict(self.result, event_id=1)
        inspected = []
        def inspect(dialog):
            summaries = dialog.findChildren(AnalysisSummary)
            self.assertEqual(len(summaries), 1)
            self.assertEqual(len(dialog.findChildren(QScrollArea)), 1)
            self.assertEqual(dialog.findChildren(QTextEdit), [])
            self.assertEqual(summaries[0].description_card.body.text(), self.result['scene']['description'])
            self.assertTrue(summaries[0].details_table.isHidden())
            inspected.append(True)
            dialog.close()
            return 0
        with patch('ui.scene_panel.QDialog.exec', new=inspect):
            self.panel.show_details()
        self.assertEqual(inspected, [True])

    def test_narrow_panel_keeps_large_result_and_normal_button_height(self):
        self.panel.resize(320, 1000)
        self.panel.show_fusion(self.combined_assessment())
        self.panel.show()
        self.app.processEvents()
        self.assertGreaterEqual(self.panel.fusion.font().pixelSize(), 28)
        self.assertLessEqual(self.panel.generate.height(), 52)
        self.assertLessEqual(self.panel.details.height(), 52)
        self.assertLessEqual(self.panel.final_level.heightForWidth(self.panel.final_level.width()),
                             self.panel.final_level.height() + 1)


if __name__ == '__main__':
    unittest.main()
