"""Review workflow checks using local generated footage, without model inference."""
import json
import math
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import cv2
import numpy as np
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from core.records import append_feedback, event_review_state, export_run, load_run, render_review_report
from core.replay import review_media, review_window
from ui.replay import ReplayDialog, ReplayWorker
from ui.window import MainWindow


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


class EventWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.run = self.root / 'run-review'
        self.run.mkdir()
        self.source = self.root / '原始视频.avi'
        writer = cv2.VideoWriter(str(self.source), cv2.VideoWriter_fourcc(*'MJPG'), 10., (160, 120))
        self.assertTrue(writer.isOpened())
        for index in range(60):
            writer.write(np.full((120, 160, 3), index * 3, dtype=np.uint8))
        writer.release()
        self.events = [{'event_id': 1, 'label': 'fire', 'started_at_s': 1., 'confirmed_at_s': 2.,
                        'closed_at_s': 4., 'risk_summary': {'peak_level': 'HIGH', 'peak_score': 72.}},
                       {'event_id': 2, 'label': 'smoke', 'started_at_s': 2., 'confirmed_at_s': 3.,
                        'closed_at_s': 5., 'risk_summary': {'peak_level': 'LOW', 'peak_score': 29.}}]
        self.notices = [{'notification_id': 'n1', 'event_id': 1, 'label': 'fire', 'level': 'MEDIUM',
                         'kind': 'event_confirmed', 'timestamp_s': 2., 'channels': []},
                        {'notification_id': 'n2', 'event_id': 1, 'label': 'fire', 'level': 'HIGH',
                         'kind': 'level_upgrade', 'timestamp_s': 3., 'channels': []},
                        {'notification_id': 'n3', 'event_id': 1, 'label': 'fire', 'level': 'HIGH',
                         'kind': 'event_closed', 'timestamp_s': 4., 'channels': []},
                        {'notification_id': 'n4', 'event_id': 2, 'label': 'smoke', 'level': 'LOW',
                         'kind': 'event_confirmed', 'timestamp_s': 3., 'channels': []}]
        write_json(self.run / 'summary.json', {'status': 'completed', 'processed_frames': 60,
                                              'temporal': {'confirmed_events': 2}})
        write_json(self.run / 'config.json', {'source': str(self.source), 'source_fps': 10.})
        write_json(self.run / 'events.json', {'events': self.events})
        self._save_notices(self.notices)
        self.workers = []
        self.dialogs = []
        self.windows = []

    def _save_notices(self, notices):
        (self.run / 'alerts.jsonl').write_text(''.join(json.dumps(n) + '\n' for n in notices), encoding='utf-8')

    def wait_until(self, predicate, timeout=3.):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            if time.monotonic() >= deadline:
                self.fail('Review operation timed out')
            QTest.qWait(5)
        self.app.processEvents()

    def tearDown(self):
        for worker in self.workers:
            worker.stop()
            self.assertTrue(worker.wait(2000))
        for dialog in self.dialogs:
            dialog.close()
        for window in self.windows:
            window.close()
        self.wait_until(lambda: all(d.worker is None for d in self.dialogs)
                        and all(not w.replays for w in self.windows))
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for window in self.windows:
            window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temp.cleanup()

    def window(self):
        with patch('ui.window.list_runs', return_value=[]):
            window = MainWindow(settings_path=False)
        window.timer.stop()
        window._apply_record(load_run(self.run), historical=True)
        window._set_controls()
        self.windows.append(window)
        return window

    def select_notice(self, window, identifier):
        for row in range(window.notification_table.rowCount()):
            if window.notification_table.item(row, 0).data(256) == identifier:
                window.notification_table.selectRow(row)
                return
        self.fail('Missing notice')

    def test_window_validates_times_and_clamps_at_video_start(self):
        self.assertEqual(review_window(self.events[0]), {'focus_s': 2., 'start_s': 0., 'end_s': 12.})
        self.assertEqual(review_window(self.events[0], focus_s=8.)['start_s'], 3.)
        for value in (math.nan, math.inf, -1., True):
            with self.assertRaises(ValueError):
                review_window(self.events[0], focus_s=value)

    def test_review_media_falls_back_without_using_unrelated_source(self):
        media = review_media(self.run, {'source': str(self.source)})
        self.assertEqual([m['kind'] for m in media], ['original'])
        (self.run / 'annotated.mp4').write_bytes(b'placeholder')
        self.assertEqual([m['kind'] for m in review_media(self.run, {'source': 'missing.mp4'})], ['annotated'])
        self.assertEqual(review_media(self.run, {}), [{'kind': 'annotated', 'label': '检测结果视频 · 含模型标记',
                                                     'path': str((self.run / 'annotated.mp4').resolve())}])

    def test_latest_upgrade_needs_its_own_review_and_closure_does_not(self):
        feedback = {'n1': {'action': 'acknowledged'}}
        self.assertEqual(event_review_state(1, self.notices, feedback), 'pending')
        feedback['n2'] = {'action': 'false_positive'}
        self.assertEqual(event_review_state(1, self.notices, feedback), 'false_positive')
        self.assertEqual(event_review_state(3, self.notices, feedback), 'unavailable')

    def test_notification_jump_resets_filters_and_event_returns_to_latest_actionable_notice(self):
        window = self.window()
        window.event_class.setCurrentIndex(window.event_class.findData('smoke'))
        self.select_notice(window, 'n1')
        window.notice_event.click()
        self.assertEqual(window._selected_event()['event_id'], 1)
        self.assertEqual(window.event_class.currentData(), '')
        self.assertIs(window.tabs.currentWidget(), window.event_page)
        window.event_notice.click()
        self.assertEqual(window._selected_notification()['notification_id'], 'n2')
        self.assertIs(window.tabs.currentWidget(), window.notification_table.parentWidget())

    def test_filters_clear_hidden_details_and_feedback_survives_reload_and_export(self):
        window = self.window()
        self.select_notice(window, 'n2')
        window.feedback_comment.setText('回放后需要现场核查')
        window.acknowledge_button.click()
        window.event_handling.setCurrentIndex(window.event_handling.findData('acknowledged'))
        visible = [row for row in range(window.event_table.rowCount()) if not window.event_table.isRowHidden(row)]
        self.assertEqual(len(visible), 1)
        window.event_table.selectRow(visible[0])
        self.assertIn('已知悉', window.detail.toPlainText())
        window.event_class.setCurrentIndex(window.event_class.findData('smoke'))
        self.assertIsNone(window._selected_event())
        self.assertFalse(window.event_replay.isEnabled())
        self.assertEqual(window.detail.toPlainText(), '')
        window._apply_record(load_run(self.run), historical=True)
        self.assertEqual(window.feedback['n2']['comment'], '回放后需要现场核查')
        destination = self.root / 'review.zip'
        export_run(self.run, destination)
        with zipfile.ZipFile(destination) as archive:
            report = archive.read('事件复核报告.md').decode('utf-8')
            self.assertIn('回放后需要现场核查', report)
            self.assertIn('已知悉', report)
            self.assertNotIn(str(self.root), report)
            self.assertIsNone(archive.testzip())

    def test_closed_notice_is_silent_record_without_feedback_action(self):
        window = self.window()
        self.select_notice(window, 'n3')
        self.assertFalse(window.acknowledge_button.isEnabled())
        self.assertFalse(window.false_positive_button.isEnabled())
        self.assertIn('无需处理', window.notification_detail.toPlainText())

    def test_report_includes_events_and_feedback_older_than_ui_tail(self):
        expanded = [self.notices[0]] + [{**self.notices[3], 'notification_id': f'x{i}', 'event_id': i + 10}
                                       for i in range(600)]
        self._save_notices(expanded)
        append_feedback(self.run, self.notices[0], 'false_positive', '旧事件的人工意见')
        self.assertNotIn('n1', [n['notification_id'] for n in load_run(self.run)['notifications']])
        report = render_review_report(self.run)
        self.assertIn('| #1 | 火焰 | 1.0 | 2.0 | 4.0 | 红色提示 · 立即核查 | 人工标为疑似误报 |', report)
        self.assertIn('旧事件的人工意见', report)

    def test_archived_priority_is_explicitly_historical_and_never_styled_as_live_red_alert(self):
        window = self.window()
        window._paint_alert('HIGH')
        live_style = window.alert_card.styleSheet()
        record = load_run(self.run)
        record['summary']['risk'] = {'peak_level': 'HIGH', 'peak_score': 72.}
        with patch('ui.window.QApplication.beep') as beep:
            window._apply_record(record, historical=True)
            beep.assert_not_called()
        self.assertIn('历史记录', window.alert_context.text())
        self.assertIn('当时提示：红色提示', window.alert_badge.text())
        self.assertNotEqual(window.alert_card.styleSheet(), live_style)
        self.assertNotIn('立即核查', window.level.text())
        self.assertIn('历史', window.next_step.text())

    def test_replay_worker_seeks_real_frame_and_stops_at_window_end(self):
        worker = ReplayWorker(self.source, review_window(self.events[0], before_s=1., after_s=.3))
        self.workers.append(worker)
        packets = []
        def collect():
            packet, _ = worker.preview()
            if packet:
                packets.append(packet)
            return bool(packets)
        worker.start()
        self.wait_until(collect)
        self.assertEqual(packets[-1]['frame_index'], 20)
        self.assertAlmostEqual(float(packets[-1]['frame'].mean()), 60., delta=3.)
        worker.seek(12)
        self.wait_until(lambda: collect() and packets[-1]['frame_index'] == 12)
        self.assertAlmostEqual(float(packets[-1]['frame'].mean()), 36., delta=3.)
        worker.speed(2.)
        worker.play(True)
        self.wait_until(lambda: collect() and packets[-1].get('ended'))
        self.assertEqual(packets[-1]['frame_index'], 22)
        self.assertFalse(worker.is_playing())
        self.assertLessEqual(packets[-1]['timestamp_s'], 2.3)

    def test_replay_dialog_step_focus_and_source_switch_release_decoder(self):
        media = [{'kind': 'original', 'label': '原视频', 'path': str(self.source)},
                 {'kind': 'annotated', 'label': '备用回放', 'path': str(self.source)}]
        dialog = ReplayDialog(media, review_window(self.events[0]), 1)
        # Keep the wrapper alive for deterministic test cleanup.
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.dialogs.append(dialog)
        dialog.show()
        self.wait_until(lambda: dialog.current_frame == 20)
        dialog.after_button.click()
        self.wait_until(lambda: dialog.current_frame == 21)
        dialog.focus_button.click()
        self.wait_until(lambda: dialog.current_frame == 20)
        old = dialog.worker
        dialog.source.setCurrentIndex(1)
        self.wait_until(lambda: dialog.worker is not None and dialog.worker is not old and dialog.current_frame == 20)
        self.assertIn('检测结果视频', dialog.context.text())
        dialog.close()
        self.wait_until(lambda: dialog.worker is None)

    def test_missing_replay_does_not_crash_or_hang_worker(self):
        worker = ReplayWorker(self.root / 'missing.avi', review_window(self.events[0]))
        self.workers.append(worker)
        errors = []
        worker.failed.connect(errors.append)
        worker.start()
        self.wait_until(lambda: bool(errors) and not worker.isRunning())
        self.assertIn('无法解码', errors[0])

    def test_main_window_close_waits_for_active_review_decoder(self):
        window = self.window()
        window.show()
        self.select_notice(window, 'n2')
        window.notice_replay.click()
        self.wait_until(lambda: window.replays and window.replays[0].current_frame == 30)
        window.close()
        self.wait_until(lambda: not window.replays and not window.isVisible())

    def test_history_filter_combines_run_status_and_text(self):
        window = self.window()
        entries = [{'output': str(self.root / f'run-{i}'),
                    'config': {'source': str(self.source)},
                    'summary': {'status': status, 'processed_frames': 3}}
                   for i, status in enumerate(('completed', 'completed_with_frame_count_warning',
                                                'stopped_by_user', 'failed'))]
        with patch('ui.window.list_runs', return_value=entries):
            window.refresh_history()
        window.history_status.setCurrentIndex(window.history_status.findData('completed'))
        self.assertEqual([window.history_table.isRowHidden(row) for row in range(4)], [False, False, True, True])
        window.history_filter.setText('原始视频')
        self.assertFalse(window.history_table.isRowHidden(0))
        window.history_filter.setText('unknown')
        self.assertTrue(all(window.history_table.isRowHidden(row) for row in range(4)))

    def test_notification_replay_uses_saved_run_source_and_notice_time(self):
        window = self.window()
        window.set_source(self.root / 'unrelated.mp4')
        self.select_notice(window, 'n2')
        with patch('ui.replay.ReplayDialog', wraps=ReplayDialog) as dialog_type:
            window.notice_replay.click()
            args = dialog_type.call_args[0]
            self.assertEqual(args[0][0]['path'], str(self.source))
            self.assertEqual(args[1]['focus_s'], 3.)
            self.assertEqual(args[2], 1)

    def test_acknowledgement_remains_pending_and_new_judgement_survives_reload(self):
        window = self.window()
        window.focus_event(1)
        original_events = (self.run / 'events.json').read_bytes()
        window.review_ack.click()
        window.event_handling.setCurrentIndex(window.event_handling.findData('pending'))
        self.assertIsNotNone(window._selected_event())
        self.assertIn('待核查', window.review_state.text())
        window.review_choice.setCurrentIndex(window.review_choice.findData('uncertain'))
        window.review_comment.setPlainText('画面遮挡，需进一步检查')
        window.review_save.click()
        self.assertIn('待跟进', window.review_save_status.text())
        record = load_run(self.run)
        self.assertEqual(record['feedback'][-1]['review_action'], 'uncertain')
        window._apply_record(record, historical=True)
        window.focus_event(1)
        self.assertEqual(window.review_choice.currentData(), 'uncertain')
        self.assertEqual(window.review_comment.toPlainText(), '画面遮挡，需进一步检查')
        self.assertEqual((self.run / 'events.json').read_bytes(), original_events)
        self.assertIn('无法判断', render_review_report(self.run))
        self.assertIn('画面遮挡', render_review_report(self.run))

    def test_acknowledging_after_review_preserves_judgement_and_review_note(self):
        window = self.window()
        window.focus_event(1)
        window.review_choice.setCurrentIndex(window.review_choice.findData('false_positive'))
        window.review_comment.setPlainText('复核现场的人工意见')
        window.review_save.click()
        window.review_ack.click()
        feedback = {f['notification_id']: f for f in load_run(self.run)['feedback']}
        self.assertTrue(feedback['n2']['acknowledged'])
        self.assertEqual(event_review_state(1, self.notices, feedback), 'false_positive')
        self.assertEqual(feedback['n2']['review_comment'], '复核现场的人工意见')
        self.assertIn('复核现场的人工意见', render_review_report(self.run))
        window.event_handling.setCurrentIndex(window.event_handling.findData('pending'))
        self.assertEqual(sum(not window.event_table.isRowHidden(r) for r in range(2)), 1)
        self.assertIn('1 条', window.pending_summary.text())
        newer = {**self.notices[1], 'notification_id': 'n-new', 'timestamp_s': 6.}
        window._receive_notifications([newer], historical=True)
        window._fill_events()
        self.assertEqual(event_review_state(1, window.notifications.values(), window.feedback), 'pending')
        self.assertEqual(sum(not window.event_table.isRowHidden(r) for r in range(2)), 2)

    def test_draft_is_not_written_or_erased_by_preview_refresh_and_empty_result_is_rejected(self):
        window = self.window()
        window.focus_event(1)
        window.review_comment.setPlainText('尚未提交的观察')
        window._fill_events()
        self.assertEqual(window.review_comment.toPlainText(), '尚未提交的观察')
        self.assertFalse((self.run / 'feedback.jsonl').exists())
        window.review_save.click()
        self.assertFalse((self.run / 'feedback.jsonl').exists())
        self.assertIn('先选择', window.review_save_status.text())
        window.review_choice.setCurrentIndex(window.review_choice.findData('uncertain'))
        window.review_comment.setPlainText('字' * 1001)
        window.review_save.click()
        self.assertFalse((self.run / 'feedback.jsonl').exists())

    def test_inline_replay_matches_selected_event_and_filter_change_releases_decoder(self):
        window = self.window()
        window.show()
        window.focus_event(2)
        self.select_notice(window, 'n2')
        window.notice_replay.click()
        self.wait_until(lambda: window.inline_replay and window.inline_replay.current_frame == 30)
        window.resize(980, 740)
        self.app.processEvents()
        self.assertEqual(window.width(), 980)
        self.assertLessEqual(window.inline_replay.video.width(), window.inline_replay.width())
        self.assertLessEqual(window.inline_replay.focus_button.geometry().right(), window.inline_replay.width())
        self.assertEqual(window._selected_event()['event_id'], 1)
        self.assertIs(window.inline_replay.parentWidget(), window.replay_host)
        self.assertIs(window.tabs.currentWidget(), window.event_page)
        window.event_class.setCurrentIndex(window.event_class.findData('smoke'))
        self.wait_until(lambda: not window.replays)
        self.assertIsNone(window.inline_replay)

    def test_timeline_keyboard_selects_event_and_report_export_uses_readable_content(self):
        window = self.window()
        window.show()
        window.timeline.setFocus()
        QTest.keyClick(window.timeline, Qt.Key.Key_Right)
        QTest.keyClick(window.timeline, Qt.Key.Key_Return)
        self.assertEqual(window._selected_event()['event_id'], 2)
        destination = self.root / '可读报告.md'
        with patch('ui.window.QFileDialog.getSaveFileName', return_value=(str(destination), '')):
            window.report_button.click()
        self.assertIn('已知悉仅表示已查看', destination.read_text(encoding='utf-8'))
        self.assertNotIn(str(self.root), destination.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
