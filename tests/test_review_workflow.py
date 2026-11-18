"""Durable operator workflow, shared status colors and unified monitoring views."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox
from core.records import append_feedback, event_review_state, load_run, render_review_report
from core.review_workflow import phase
from ui.review_controls import fill_conclusions
from ui.monitor_center import MonitorCenter
from ui.window import MainWindow


class ReviewWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / 'run-20261117-110000-000001'
        self.run.mkdir()
        self.notice = {'notification_id': 'n1', 'event_id': 1, 'label': 'fire', 'level': 'MEDIUM',
                       'kind': 'event_confirmed', 'timestamp_s': 2., 'channels': []}
        for name, value in {'summary.json': {'status': 'completed', 'processed_frames': 30},
            'config.json': {'source': str(self.root / 'local.avi'), 'source_kind': 'file', 'source_fps': 10.},
            'events.json': {'events': [{'event_id': 1, 'label': 'fire', 'started_at_s': 1., 'confirmed_at_s': 2., 'closed_at_s': 3.,
                                       'risk_summary': {'peak_score': 48., 'peak_level': 'MEDIUM'}}]}}.items():
            (self.run / name).write_text(json.dumps(value), encoding='utf-8')
        (self.run / 'alerts.jsonl').write_text(json.dumps(self.notice) + '\n', encoding='utf-8')
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            if hasattr(window, 'workers'):
                window.workers.clear()
                window.shutdown()
            window.close()
        self.app.processEvents()
        self.temp.cleanup()

    def desk(self):
        desk = MonitorCenter(registry_path=self.root / 'points.json')
        desk.timer.stop()
        desk.restore_saved_events([load_run(self.run)])
        desk.set_event_mode(True)
        self.windows.append(desk)
        return desk

    def window(self):
        with patch('ui.window.list_runs', return_value=[]):
            window = MainWindow(settings_path=False)
        window.monitor_center.timer.stop()
        window.timer.stop()
        self.windows.append(window)
        return window

    def test_four_visible_conclusions_keep_legacy_values_readable_but_not_selectable(self):
        combo = QComboBox()
        fill_conclusions(combo)
        visible = [combo.itemText(i) for i in range(1, combo.count()) if not combo.view().isRowHidden(i)]
        self.assertEqual(visible, ['正常用火', '识别误报', '确认异常', '继续核查'])
        for value in ('resolved', 'uncertain'):
            index = combo.findData(value)
            self.assertFalse(combo.model().item(index).isEnabled())

    def test_confirm_resolve_ack_reload_preserves_original_conclusion_and_history(self):
        original = (self.run / 'events.json').read_bytes()
        append_feedback(self.run, self.notice, 'confirmed_abnormal', '人工发现异常')
        append_feedback(self.run, self.notice, 'resolved', '现场处置完成')
        saved = append_feedback(self.run, self.notice, 'acknowledged')
        self.assertEqual(saved['conclusion'], 'confirmed_abnormal')
        self.assertEqual(saved['workflow_status'], 'completed')
        record = load_run(self.run)
        feedback = {row['notification_id']: row for row in record['feedback']}
        self.assertEqual(phase(event_review_state(1, record['notifications'], feedback)), 'completed')
        self.assertEqual(len(record['feedback']), 3)
        self.assertEqual((self.run / 'events.json').read_bytes(), original)
        self.assertIn('核查与处理记录', render_review_report(self.run))
        self.assertIn('确认异常', render_review_report(self.run))

    def test_new_upgrade_reopens_work_without_erasing_completed_history(self):
        append_feedback(self.run, self.notice, 'normal_use')
        record = load_run(self.run)
        feedback = {row['notification_id']: row for row in record['feedback']}
        newer = {**self.notice, 'notification_id': 'n2', 'kind': 'level_upgrade', 'level': 'HIGH', 'timestamp_s': 4.}
        self.assertEqual(event_review_state(1, record['notifications'] + [newer], feedback), 'pending')
        self.assertEqual(record['feedback'][0]['review_action'], 'normal_use')

    def test_unregistered_record_can_be_handled_and_completion_stays_selected_and_green(self):
        desk = self.desk()
        key = next(iter(desk.events))
        desk.selected_key = key
        desk._render_events()
        desk.review_choice.setCurrentIndex(desk.review_choice.findData('confirmed_abnormal'))
        desk.save_review()
        self.assertEqual(desk.events[key]['review'], 'confirmed_abnormal')
        self.assertEqual(desk.workflow_tabs.currentIndex(), 2)
        desk._save_review_action('resolved')
        self.assertEqual(desk.workflow_tabs.currentIndex(), 3)
        self.assertEqual(desk.selected_key, key)
        self.assertEqual(desk.handling_badge.text(), '已处理')
        self.assertIn('#19764b', desk.handling_badge.styleSheet())
        self.assertEqual(desk.events[key]['conclusion'], 'confirmed_abnormal')
        desk.review_note.setText('补充处理说明')
        desk.save_review()
        self.assertEqual(desk.events[key]['review'], 'resolved')
        self.assertEqual(desk.workflow_tabs.currentIndex(), 3)
        reopened = self.desk()
        self.assertEqual(reopened.events[key]['review'], 'resolved')
        self.assertEqual(reopened.events[key]['conclusion'], 'confirmed_abnormal')

    def test_single_and_multiple_views_share_registered_workers_and_one_navigation_entry(self):
        window = self.window()
        self.assertEqual(window.tabs.currentIndex(), 7)
        self.assertEqual(window.page_title.text(), '监控台')
        self.assertTrue(window.nav_buttons['camera'].isHidden())
        desk = window.monitor_center
        devices = [{'id': value, 'name': value, 'location': '测试位置', 'source': str(self.root / (value + '.avi')), 'kind': 'file'} for value in ('one', 'two')]
        desk.registry.path = self.root / 'test-points.json'
        desk.set_devices(devices)
        workers = {name: Mock() for name in ('one', 'two')}
        desk.workers = workers.copy()
        desk.view_mode.setCurrentIndex(1)
        self.assertTrue(desk.cards['two'].isHidden())
        desk.focus_point.setCurrentIndex(1)
        self.assertTrue(desk.cards['one'].isHidden())
        self.assertFalse(desk.cards['two'].isHidden())
        desk.view_mode.setCurrentIndex(0)
        self.assertTrue(all(not card.isHidden() for card in desk.cards.values()))
        for worker in workers.values():
            worker.stop.assert_not_called()
        desk.workers.clear()

    def test_saved_conclusion_switches_local_filter_and_is_reachable_in_completed_board(self):
        window = self.window()
        window._apply_record(load_run(self.run), historical=True)
        window.focus_event(1)
        window.event_handling.setCurrentIndex(window.event_handling.findData('pending'))
        window.review_choice.setCurrentIndex(window.review_choice.findData('false_positive'))
        window.save_event_review()
        self.assertIsNotNone(window._selected_event())
        self.assertEqual(window.review_state.text(), '已处理')
        self.assertIn('#19764b', window.review_state.styleSheet())
        window.show_completed_events()
        self.assertTrue(window.monitor_center.event_mode)
        self.assertEqual(window.monitor_center.workflow_tabs.currentIndex(), 3)
        self.assertEqual(window.monitor_center.events[window.monitor_center.selected_key]['review'], 'false_positive')

    def test_unified_monitoring_keeps_global_sound_and_mute_controls(self):
        import time
        window = self.window()
        desk = window.monitor_center
        desk.sound_control.click()
        self.assertTrue(window.alert_sound.isChecked())
        self.assertTrue(desk.analysis_preferences()['alert_sound'])
        worker = Mock()
        worker.preview.return_value = (None, [])
        worker.monitor_events.return_value = []
        worker.take_notifications.return_value = [{'channels': ['sound'], 'kind': 'level_upgrade'}]
        with patch('ui.monitor_center.QApplication.beep') as beep:
            desk._consume('fixture', worker)
            beep.assert_called_once()
            beep.reset_mock()
            desk.mute_control.click()
            self.assertGreater(window.muted_until, time.monotonic())
            desk._last_sound_at = 0.
            desk._consume('fixture', worker)
            beep.assert_not_called()

    def test_completed_source_record_shows_conclusion_and_note_edit_keeps_completion(self):
        window = self.window()
        window._apply_record(load_run(self.run), historical=True)
        window.focus_event(1)
        window.review_choice.setCurrentIndex(window.review_choice.findData('confirmed_abnormal'))
        window.save_event_review()
        window.complete_event_review()
        window._apply_record(load_run(self.run), historical=True)
        window.focus_event(1)
        self.assertEqual(window.review_choice.currentData(), 'confirmed_abnormal')
        self.assertEqual(window.review_state.text(), '已处理')
        window.review_comment.setPlainText('完成后的补充说明')
        window.save_event_review()
        saved = load_run(self.run)['feedback'][-1]
        self.assertEqual(saved['review_action'], 'resolved')
        self.assertEqual(saved['conclusion'], 'confirmed_abnormal')

    def test_reopen_resolved_restores_pending_action_and_ack_keeps_it(self):
        original = (self.run / 'events.json').read_bytes()
        append_feedback(self.run, self.notice, 'confirmed_abnormal', '原异常判断')
        append_feedback(self.run, self.notice, 'resolved', '原完成说明')
        saved = append_feedback(self.run, self.notice, 'reopened')
        self.assertEqual(saved['action'], 'reopened')
        self.assertEqual(saved['review_action'], 'confirmed_abnormal')
        self.assertEqual(saved['workflow_status'], 'action')
        self.assertEqual(saved['conclusion'], 'confirmed_abnormal')
        self.assertEqual(saved['reopened_from'], 'resolved')
        acknowledged = append_feedback(self.run, self.notice, 'acknowledged')
        self.assertEqual(acknowledged['review_action'], 'confirmed_abnormal')
        self.assertEqual(acknowledged['workflow_status'], 'action')
        self.assertEqual(acknowledged['conclusion'], 'confirmed_abnormal')
        record = load_run(self.run)
        self.assertEqual([row['action'] for row in record['feedback']],
                         ['confirmed_abnormal', 'resolved', 'reopened', 'acknowledged'])
        self.assertEqual((self.run / 'events.json').read_bytes(), original)
        self.assertIn('撤回处理', render_review_report(self.run))

    def test_reopen_normal_or_false_positive_returns_review_without_old_conclusion(self):
        for action in ('normal_use', 'false_positive'):
            with self.subTest(action=action):
                append_feedback(self.run, self.notice, action, '原核查说明仍须保留')
                saved = append_feedback(self.run, self.notice, 'reopened')
                self.assertEqual(saved['review_action'], 'needs_verification')
                self.assertEqual(saved['workflow_status'], 'pending')
                self.assertIsNone(saved['conclusion'])
                self.assertEqual(saved['reopened_conclusion'], action)
                acknowledged = append_feedback(self.run, self.notice, 'acknowledged')
                self.assertEqual(acknowledged['review_action'], 'needs_verification')
                self.assertNotIn(acknowledged['conclusion'], ('normal_use', 'false_positive'))
                feedback = {row['notification_id']: row for row in load_run(self.run)['feedback']}
                self.assertEqual(event_review_state(1, [self.notice], feedback), 'needs_verification')
        report = render_review_report(self.run)
        self.assertIn('撤回结论', report)
        self.assertIn('原核查说明仍须保留', report)

    def test_reopen_rejects_noncompleted_duplicate_and_obsolete_notice_without_writing(self):
        path = self.run / 'feedback.jsonl'
        with self.assertRaises(ValueError):
            append_feedback(self.run, self.notice, 'reopened')
        self.assertFalse(path.exists())
        append_feedback(self.run, self.notice, 'normal_use')
        append_feedback(self.run, self.notice, 'reopened')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            append_feedback(self.run, self.notice, 'reopened')
        self.assertEqual(path.read_bytes(), before)
        append_feedback(self.run, self.notice, 'normal_use')
        newer = {**self.notice, 'notification_id': 'n2', 'kind': 'level_upgrade', 'timestamp_s': 4.}
        with (self.run / 'alerts.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(newer) + '\n')
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            append_feedback(self.run, self.notice, 'reopened')
        self.assertEqual(path.read_bytes(), before)

    def test_reopen_none_scene_notice_is_durable(self):
        notice = {**self.notice, 'notification_id': 'scene-1', 'event_id': None,
                  'origin': 'ai_scene', 'label': 'scene', 'kind': 'scene_attention'}
        with (self.run / 'alerts.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(notice) + '\n')
        append_feedback(self.run, notice, 'false_positive')
        saved = append_feedback(self.run, notice, 'reopened')
        self.assertIsNone(saved['event_id'])
        self.assertEqual(saved['review_action'], 'needs_verification')
        record = load_run(self.run)
        feedback = {row['notification_id']: row for row in record['feedback']}
        self.assertEqual(event_review_state(None, record['notifications'], feedback), 'needs_verification')

    def test_source_reopen_conclusion_immediately_switches_filter_and_color(self):
        append_feedback(self.run, self.notice, 'normal_use')
        window = self.window()
        window._apply_record(load_run(self.run), historical=True)
        window.focus_event(1)
        self.assertEqual(window.review_reopen.text(), '撤回结论')
        self.assertFalse(window.review_reopen.isHidden())
        self.assertIn('#19764b', window.review_state.styleSheet())
        window.review_reopen.click()
        self.assertEqual(window.review_state.text(), '待核查')
        self.assertIn('#b82f48', window.review_state.styleSheet())
        self.assertEqual(window.event_handling.currentData(), 'review')
        self.assertEqual(window._selected_event()['event_id'], 1)
        self.assertEqual(window.review_choice.currentData(), 'needs_verification')
        self.assertTrue(window.review_reopen.isHidden())
        saved = load_run(self.run)['feedback'][-1]
        self.assertEqual(saved['action'], 'reopened')

    def test_source_reopen_completion_returns_to_action_and_keeps_conclusion(self):
        append_feedback(self.run, self.notice, 'confirmed_abnormal')
        append_feedback(self.run, self.notice, 'resolved')
        window = self.window()
        window._apply_record(load_run(self.run), historical=True)
        window.focus_event(1)
        self.assertEqual(window.review_reopen.text(), '撤回处理')
        window.review_reopen.click()
        self.assertEqual(window.review_state.text(), '待处理')
        self.assertIn('#a45b10', window.review_state.styleSheet())
        self.assertEqual(window.event_handling.currentData(), 'action')
        self.assertEqual(window.review_choice.currentData(), 'confirmed_abnormal')
        self.assertEqual(window._selected_event()['event_id'], 1)
        self.assertEqual(load_run(self.run)['feedback'][-1]['conclusion'], 'confirmed_abnormal')


if __name__ == '__main__':
    unittest.main()
