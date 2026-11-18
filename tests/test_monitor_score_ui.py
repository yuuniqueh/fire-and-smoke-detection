"""One combined monitor result, with the supporting arithmetic confined to details."""
import os
import json
from pathlib import Path
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from ui.monitor_center import MonitorCenter


def record(**changes):
    scene = {'description': '桌面上可见蜡烛及局部火焰。', 'scene': 'candle',
             'relation': 'expected_burning', 'evidence_quality': 'adequate', 'spread': 'not_observed',
             'visible_evidence': ['火焰位于烛芯上方'], 'hazard_cues': [], 'uncertainties': [],
             'next_check': '核对周围物品。'}
    scene.update(changes)
    return {'scene': scene, 'event_id': None, 'frame_count': 3, 'span_s': 1.,
            'evidence': [{'timestamp_s': x} for x in (0., .5, 1.)], 'model': 'software-test-only', 'elapsed_s': .1}


class MonitorScoreUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.desk = MonitorCenter(registry_path=root / 'points.json')
        self.addCleanup(self.desk.close)
        self.addCleanup(self.desk.shutdown)
        self.desk.auto_ai.setChecked(False)
        self.point = {'id': 'point', 'name': '厨房监控', 'location': '一楼', 'kind': 'file', 'source': str(root / 'clip.mp4')}
        self.desk.set_devices([self.point])
        self.output = str((root / 'run').resolve())
        self.desk.outputs['point'] = self.output

    def event(self, policy='context-score-v2'):
        priority = {'policy': policy, 'score': 55.5, 'level': 'MEDIUM', 'baseline_score': 48.,
                    'baseline_level': 'MEDIUM', 'context_delta': 7.5, 'reason': '烟火变化与场景异常线索共同支持优先核查。'}
        return {'event_id': 1, 'device': self.point, 'output': self.output, 'label': 'fire',
                'confirmed_at_s': 1., 'reference_s': 1., 'last_timestamp_s': 2., 'closed': True,
                'baseline': {'score': 48., 'level': 'MEDIUM'}, 'assessment': {'score': 55.5, 'level': 'MEDIUM', 'fusion': priority},
                'review': 'pending', 'scene_status': '复核完成', 'scene_record': record(), 'frames': []}

    def test_primary_result_is_one_combined_score_and_details_keep_evidence(self):
        key = ('point', self.output, 1)
        self.desk.events[key] = self.event()
        self.desk._render_events()
        self.assertEqual(self.desk.context_enabled.text(), '场景辅助评分')
        self.assertIn('综合核查指数 55.5', self.desk.priority_detail.text())
        self.assertNotIn('＋', self.desk.priority_detail.text())
        self.assertNotIn('检测 48', self.desk.priority_detail.text())
        self.assertIn('共同支持优先核查', self.desk.scene_text.toPlainText())
        self.desk.scene_details_toggle.setChecked(True)
        self.desk._show_selected()
        self.assertIn('检测原始指数：48.0', self.desk.scene_text.toPlainText())
        self.assertIn('场景辅助影响：+7.5', self.desk.scene_text.toPlainText())

    def test_historical_fixed_score_is_identified_without_recalculating(self):
        key = ('point', self.output, 1)
        self.desk.events[key] = self.event('context-review-v1')
        self.desk._render_events()
        self.assertIn('历史综合核查指数 55.5', self.desk.priority_detail.text())
        self.assertEqual(self.desk._priority(self.desk.events[key])['score'], 55.5)

    def test_scene_only_hazard_remains_visible_without_creating_detector_event(self):
        key = ('point', self.output, None)
        self.desk.scenes[key] = {'event_id': None, 'device': self.point, 'output': self.output,
            'baseline': None, 'reference_s': 1., 'last_timestamp_s': 2., 'closed': True,
            'scene_record': record(hazard_cues=['周边物品出现火焰']), 'scene_status': '复核完成', 'frames': []}
        self.desk._render_events()
        self.desk.select_device_event('point')
        self.assertIn('AI异常线索', self.desk.priority_detail.text())
        self.assertIn('AI异常线索', self.desk.cards['point'].priority.text())
        self.assertEqual(self.desk.events, {})
        self.assertFalse(self.desk.save_review_button.isEnabled())

    def test_live_expired_scene_does_not_gain_attention(self):
        event = {'device': self.point, 'output': self.output, 'closed': False,
                 'scene_record': record(hazard_cues=['出现异常火焰']), 'last_timestamp_s': 20.}
        self.assertFalse(self.desk._scene_attention(event))

    def test_closed_record_keeps_saved_context_and_late_review_does_not_rescore(self):
        event = self.event()
        event['assessment'] = {'score': 48., 'level': 'MEDIUM'}
        event['scene_record']['priority'] = {'policy': 'context-review-v1', 'score': 38.4, 'level': 'LOW',
            'baseline_score': 48., 'baseline_level': 'MEDIUM', 'context_delta': -9.6, 'reason': '旧版已保存结果。'}
        self.assertEqual(self.desk._priority(event)['score'], 38.4)
        event['historical_priority'] = self.desk._priority(event)
        Path(self.output).mkdir()
        self.desk._save_closed_context(event)
        self.assertEqual(event['scene_record']['priority']['score'], 38.4)
        self.assertEqual(event['scene_record']['priority']['context_delta'], -9.6)

    def scene_run(self):
        root = Path(self.output)
        root.mkdir()
        config = {'source': self.point['source'], 'source_kind': 'file', 'monitoring_point': self.point}
        for filename, value in {'summary.json': {'status': 'completed'}, 'config.json': config,
                                 'events.json': {'events': []}}.items():
            (root / filename).write_text(json.dumps(value), encoding='utf-8')
        notice = {'notification_id': 'notice-scene-1', 'event_id': None, 'origin': 'ai_scene', 'label': 'scene',
                  'kind': 'scene_attention', 'timestamp_s': 3., 'evidence_reference_s': 1.,
                  'score': 52.7, 'level': 'MEDIUM', 'title': '综合异常提示',
                  'message': '多帧存在异常线索，需要人工核查。', 'description': '画面中周边物品出现火焰。'}
        (root / 'alerts.jsonl').write_text(json.dumps(notice) + '\n', encoding='utf-8')
        return root, config, notice

    def test_scene_composite_notice_has_one_none_work_item_and_round_trip_review(self):
        from core.records import load_run
        root, config, notice = self.scene_run()
        key = ('point', str(root), None)
        self.desk.index_source_events(str(root), config, [], [notice], {})
        self.desk._render_events()
        self.assertEqual(list(self.desk.events), [key])
        self.assertEqual(self.desk.event_table.rowCount(), 1)
        self.assertIn('场景异常线索', self.desk.event_table.item(0, 2).text())
        self.assertIn('综合核查指数 52.7', self.desk.priority_detail.text())
        self.assertTrue(self.desk.save_review_button.isEnabled())
        self.assertTrue(self.desk.review_toggle.isEnabled())
        self.assertNotIn('#None', self.desk.event_title.text())
        self.desk._save_review_action('confirmed_abnormal')
        self.assertEqual(self.desk.events[key]['review'], 'confirmed_abnormal')
        self.desk._save_review_action('resolved')
        self.assertEqual(self.desk.events[key]['review'], 'resolved')
        reopened = MonitorCenter(registry_path=Path(self.temp.name) / 'reopened.json')
        try:
            reopened.set_devices([self.point])
            reopened.set_event_mode(True)
            reopened.restore_saved_events([load_run(root)])
            self.assertEqual(reopened.events[key]['review'], 'resolved')
            self.assertEqual(reopened.events[key]['event_id'], None)
            self.assertEqual(reopened.events[key]['assessment']['score'], 52.7)
            self.assertEqual(reopened.event_table.rowCount(), 1)
            self.assertEqual(json.loads((root / 'events.json').read_text(encoding='utf-8'))['events'], [])
        finally:
            reopened.shutdown()
            reopened.close()

    def test_new_scene_notice_reopens_same_work_item_and_none_open_signal_is_valid(self):
        root, config, notice = self.scene_run()
        self.desk.index_source_events(str(root), config, [], [notice], {})
        self.desk._render_events()
        key = ('point', str(root), None)
        self.desk.events[key].update(closed=True, review='resolved')
        opened = []
        self.desk.openSavedEvent.connect(lambda path, event_id: opened.append((path, event_id)))
        self.desk.open_selected_record()
        self.assertEqual(opened, [(str(root), None)])
        later = {**notice, 'notification_id': 'notice-scene-2', 'timestamp_s': 8., 'score': 58.2}
        self.desk.index_source_events(str(root), config, [], [notice, later], {})
        self.assertEqual(len(self.desk.events), 1)
        self.assertEqual(self.desk.events[key]['review'], 'pending')
        self.assertEqual(self.desk.events[key]['assessment']['score'], 58.2)

    def test_live_scene_notice_is_consumed_into_same_pending_board(self):
        import numpy as np
        root, _, notice = self.scene_run()
        packet = {'raw_frame': np.zeros((32, 32, 3), dtype=np.uint8), 'timestamp_s': 3.,
                  'risk': {'timestamp_s': 3., 'score': 52.7, 'level': 'MEDIUM', 'events': [],
                           'fusion': {'scene_only_hazard': True}}}
        class Worker:
            output = str(root)
            def take_notifications(self):
                return [notice]
            def preview(self):
                return packet, None
            def scene_evidence(self):
                return []
            def monitor_events(self):
                return []
        self.desk._consume('point', Worker())
        self.desk._render_events()
        self.assertEqual(self.desk.event_table.rowCount(), 1)
        self.assertEqual(self.desk.events[('point', str(root), None)]['review'], 'pending')
        self.assertIn('优先核查', self.desk.cards['point'].priority.text())
        self.assertNotIn('52.7', self.desk.cards['point'].priority.text())

    def test_scene_level_upgrade_reopens_same_item_and_reviews_latest_notice(self):
        root, config, notice = self.scene_run()
        low = {**notice, 'score': 38., 'level': 'LOW'}
        self.desk.index_source_events(str(root), config, [], [low], {})
        self.desk._render_events()
        key = ('point', str(root), None)
        self.desk.events[key]['review'] = 'resolved'
        upgrade = {**notice, 'notification_id': 'notice-scene-upgrade', 'kind': 'level_upgrade',
                   'timestamp_s': 7., 'score': 54.5, 'level': 'MEDIUM', 'previous_level': 'LOW'}
        closed = {**upgrade, 'notification_id': 'notice-scene-closed', 'kind': 'event_closed'}
        notices = [low, upgrade, closed]
        (root / 'alerts.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in notices), encoding='utf-8')
        self.desk.index_source_events(str(root), config, [], notices, {})
        self.desk._render_events()
        row = self.desk.events[key]
        self.assertEqual(len(self.desk.events), 1)
        self.assertEqual(row['review'], 'pending')
        self.assertEqual(row['latest_notice_id'], upgrade['notification_id'])
        self.assertEqual(row['assessment']['score'], 54.5)
        self.assertEqual(self.desk._notification_for(row, notices)['notification_id'], upgrade['notification_id'])
        self.desk._save_review_action('false_positive')
        saved = json.loads((root / 'feedback.jsonl').read_text(encoding='utf-8').splitlines()[-1])
        self.assertEqual(saved['notification_id'], upgrade['notification_id'])


if __name__ == '__main__':
    unittest.main()
