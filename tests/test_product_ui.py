"""Product UI contracts without loading models or opening research videos."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontInfo

from core.notifications import NotificationEngine
from core.pipeline import AnalysisOptions
from ui.window import MainWindow
from ui.worker import AnalysisWorker, CameraPreviewWorker


def notice(identifier, level='MEDIUM', kind='event_confirmed', sound=True):
    return {'notification_id': identifier, 'event_id': 7, 'label': 'fire', 'level': level,
            'timestamp_s': 2.0, 'kind': kind, 'title': '测试视觉提示', 'message': '界面契约检查',
            'channels': ['panel', 'log'] + (['sound'] if sound else [])}


class ProductUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.history_patch = patch('ui.window.list_runs', return_value=[])
        self.history_patch.start()
        self.window = MainWindow(settings_path=False)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.history_patch.stop()

    def test_minimum_desktop_has_all_product_pages(self):
        self.window.resize(980, 740)
        self.window.show()
        self.app.processEvents()
        self.assertEqual((self.window.width(), self.window.height()), (980, 740))
        self.assertEqual([self.window.tabs.tabText(i) for i in range(self.window.tabs.count())],
                         ['画面分析', '趋势观察', '提示中心（最近 500 条）',
                          '事件核查', '历史记录', '设置', '存储管理', '监控中心'])
        self.assertTrue(self.window.tabs.tabBar().isHidden())
        self.assertEqual(set(self.window.nav_buttons), {'camera', 'file', 'events', 'history', 'settings', 'storage', 'center'})

    def test_core_pages_use_readable_native_sans_text(self):
        from ui.product import ui_font
        expected_family = QFontInfo(ui_font()).family()
        for name in ('review_choice', 'review_save', 'review_comment', 'storage_table', 'history_table'):
            widget = getattr(self.window, name)
            widget.ensurePolished()
            info = QFontInfo(widget.font())
            self.assertEqual(info.family(), expected_family, name)
            self.assertGreaterEqual(info.pixelSize(), 16, name)

    def test_compact_event_page_switches_picture_and_actions_without_squeezing_columns(self):
        self.window.resize(980, 740)
        self.window.navigate('events')
        self.window.show()
        self.app.processEvents()
        self.assertTrue(self.window.event_detail_tabs.isVisible())
        self.assertTrue(self.window.evidence_scroll.isVisible())
        self.assertFalse(self.window.review_scroll.isVisible())
        self.window.event_detail_tabs.setCurrentIndex(1)
        self.app.processEvents()
        self.assertTrue(self.window.review_scroll.isVisible())
        self.assertFalse(self.window.evidence_scroll.isVisible())
        self.window.resize(1440, 900)
        self.app.processEvents()
        self.assertTrue(self.window.review_scroll.isVisible())
        self.assertTrue(self.window.evidence_scroll.isVisible())

    def test_default_monitoring_is_idle_and_recording_tools_are_secondary(self):
        self.assertEqual(self.window.input_kind.currentData(), 'camera')
        self.assertIsNone(self.window.task)
        self.assertTrue(self.window.tools_panel.isHidden())
        self.assertTrue(self.window.source.isHidden())
        self.assertTrue(self.window.current_event_button.isHidden())
        self.assertTrue(self.window.alert_banner.isHidden())
        self.window.tools_toggle.click()
        self.assertFalse(self.window.tools_panel.isHidden())
        self.window.nav_buttons['file'].click()
        self.assertEqual(self.window.input_kind.currentData(), 'file')
        self.window.nav_buttons['camera'].click()
        self.assertEqual(self.window.input_kind.currentData(), 'camera')
        self.assertTrue(self.window.tools_panel.isHidden())

    def test_compact_sound_control_respects_run_configuration_and_mute_keeps_working(self):
        self.window.sound_status.click()
        self.assertTrue(self.window.alert_sound.isChecked())
        self.window.task = AnalysisWorker(AnalysisOptions(source='0', source_kind='camera', alert_sound=True))
        try:
            self.window._set_controls()
            self.assertFalse(self.window.sound_status.isEnabled())
            self.window.sound_status.click()
            self.assertTrue(self.window.alert_sound.isChecked())
            self.window.mute_button.click()
            self.assertGreater(self.window.muted_until, 0)
            self.assertIn('静音', self.window.sound_status.text())
        finally:
            self.window.task = None

    def test_hidden_idle_guidance_does_not_hide_stale_or_unobserved_state(self):
        self.assertTrue(self.window.evidence.isHidden())
        self.window._show_stale_frame()
        self.assertFalse(self.window.evidence.isHidden())
        self.assertFalse(self.window.next_step.isHidden())
        self.assertIn('未知', self.window.evidence.text())
        self.window._show_assessment({'status': 'not_observed'})
        self.assertFalse(self.window.evidence.isHidden())
        self.assertIn('本帧未检测', self.window.evidence.text())
        self.assertTrue(self.window.next_step.isHidden())
        self.window._preview_done({'ok': False, 'error': 'connection lost'})
        self.assertIn('中断', self.window.level.text())
        self.assertIn('未运行', self.window.evidence.text())
        self.assertFalse(self.window.error_panel.isHidden())
        self.window._preview_done({'ok': True})
        self.assertIn('已停止', self.window.level.text())
        self.assertIn('未运行', self.window.evidence.text())

    def test_notifications_survive_preview_replacement_with_bounded_memory(self):
        worker = AnalysisWorker(AnalysisOptions(source='not-opened.mp4'))
        for index in range(700):
            worker._frame({'timestamp_s': float(index), 'risk': None,
                           'notifications': [notice(index, sound=False)]})
        packet, curve = worker.preview()
        self.assertEqual(packet['timestamp_s'], 699.0)
        notifications = worker.take_notifications()
        self.assertEqual(len(notifications), 500)
        self.assertEqual(notifications[0]['notification_id'], 200)
        self.assertEqual(notifications[-1]['notification_id'], 699)
        self.assertEqual(worker.take_notifications(), [])
        self.assertIsNone(worker.preview()[0])
        self.assertLessEqual(len(curve), 121)

    def test_unobserved_frame_has_no_stale_risk(self):
        self.window.score.setText('指数 88 / 100')
        self.window._show_assessment({'status': 'not_observed'})
        self.assertEqual(self.window.level.text(), '巡检间隔')
        self.assertEqual(self.window.score.text(), '辅助评分 —（0—100）')
        self.assertIn('本帧未检测', self.window.evidence.text())
        self.assertTrue(all(label.text() == '—' for label in self.window.feature_labels.values()))

    def test_populated_score_card_allocates_all_text_lines_after_layout(self):
        event = {'label': 'fire', 'fresh_evidence': True,
                 'features': {'coverage_ratio_smoothed': .069, 'growth_pp_per_s': None,
                              'positive_duration_s': 1.03, 'confidence': .75},
                 'score_components': {'class_base': 25., 'box_coverage': 5.,
                                      'positive_duration': 3., 'positive_growth': 0.,
                                      'model_confidence': 7.4}}
        for width, height in ((980, 740), (1280, 920)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.window.show()
                self.window._show_features(None)
                self.app.processEvents()
                self.window._show_features(event)
                # Text updates schedule Qt's layout pass; screenshots must wait
                # for that pass rather than capture the previous empty label.
                for _ in range(3):
                    self.app.processEvents()
                label = self.window.contributions
                self.assertEqual(len(label.text().splitlines()), 6)
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
                self.assertGreaterEqual(self.window.explanation.height(),
                                        self.window.explanation.heightForWidth(self.window.explanation.width()))

    def test_historical_alerts_and_trends_are_archived_and_silent(self):
        with patch('ui.window.QApplication.beep') as beep:
            self.window._receive_notifications([notice('history')], historical=True)
            beep.assert_not_called()
        self.window._show_trend({'timestamp_s': 4.0, 'dominant_event_id': None, 'events': []}, historical=True)
        self.assertEqual(self.window.trend_context.text(), '历史最后观察 @ 4.00s')

    def test_high_upgrade_bypasses_medium_sound_rate_limit(self):
        with patch('ui.window.monotonic', return_value=100.0), patch('ui.window.QApplication.beep') as beep:
            self.window._receive_notifications([notice('medium')])
            self.window._receive_notifications([notice('high', level='HIGH', kind='level_upgrade')])
            self.assertEqual(beep.call_count, 2)
            self.window._receive_notifications([notice('other')])
            self.assertEqual(beep.call_count, 2)
            self.window._receive_notifications([notice('high', level='HIGH', kind='level_upgrade')])
            self.assertEqual(beep.call_count, 2)

    def test_default_sound_disabled_notice_never_beeps(self):
        engine = NotificationEngine()
        assessment = {'timestamp_s': 1.0, 'events': [{'event_id': 1, 'label': 'fire',
                      'level': 'HIGH', 'fresh_evidence': True}]}
        snapshot = {'timestamp_s': 1.0, 'tracks': [], 'transitions': []}
        notices = engine.update(assessment, snapshot)
        self.assertNotIn('sound', notices[0]['channels'])
        with patch('ui.window.QApplication.beep') as beep:
            self.window._receive_notifications(notices)
            beep.assert_not_called()

    def test_closed_event_in_same_buffer_does_not_sound_stale_alert(self):
        with patch('ui.window.QApplication.beep') as beep:
            self.window._receive_notifications([notice('open'), notice('close', kind='event_closed', sound=False)])
            beep.assert_not_called()

    def test_monitoring_options_forwarded_without_starting_worker(self):
        self.window.set_source(Path('ui-check.mp4'))
        self.window.monitoring_profile.setCurrentIndex(self.window.monitoring_profile.findData('adaptive'))
        self.window.alert_sound.setChecked(True)
        with patch('ui.window.Path.is_file', return_value=True), \
                patch('ui.window.AnalysisWorker') as worker_type, \
                patch.object(self.window, '_attach_task') as attach:
            self.window.start_analysis()
            options = worker_type.call_args[0][0]
            self.assertEqual(options.monitoring_profile, 'adaptive')
            self.assertTrue(options.alert_sound)
            attach.assert_called_once()

    def test_feedback_written_only_on_explicit_action(self):
        self.window.current_run = Path('unused-test-run')
        with patch('ui.window.append_feedback') as append:
            item = {**notice('feedback', sound=False), 'score': 41.0,
                    'features': {'coverage_ratio_smoothed': .05, 'positive_duration_s': 2},
                    'score_components': {'class_base': 25}}
            self.window._receive_notifications([item], historical=True)
            self.window.notification_table.selectRow(0)
            self.app.processEvents()
            self.assertIn('41.0 / 100', self.window.notification_detail.toPlainText())
            self.assertIn('5.00%', self.window.notification_detail.toPlainText())
            append.assert_not_called()
            append.return_value = {'notification_id': 'feedback', 'action': 'acknowledged'}
            self.window.acknowledge_button.click()
            append.assert_called_once_with(self.window.current_run, self.window.notifications['feedback'],
                                           'acknowledged', '')
            self.assertIn('已知悉', self.window.feedback_status.text())

    def test_temporary_mute_suppresses_sound_but_preserves_visual_alerts(self):
        self.window.toggle_mute()
        with patch('ui.window.QApplication.beep') as beep:
            self.window._receive_notifications([notice('muted')])
            beep.assert_not_called()
            self.assertIn('muted', self.window.notifications)
            self.assertEqual(self.window.active_notification, 'muted')
            self.window.toggle_mute()
            self.window._receive_notifications([notice('restored')])
            beep.assert_called_once()
        self.assertIn('静音', self.window.mute_button.text())

    def test_live_priorities_pair_color_names_with_explicit_actions_and_distinct_card_styles(self):
        styles = set()
        for level, color, action in [('LOW', '黄色', '请核查'), ('MEDIUM', '橙色', '优先核查'), ('HIGH', '红色', '立即核查')]:
            event = {'event_id': 1, 'label': 'fire', 'fresh_evidence': True,
                     'features': {'positive_duration_s': 2.}, 'score_components': {}}
            self.window._show_assessment({'status': 'assessed', 'level': level, 'score': 50.,
                                          'events': [event], 'dominant_event_id': 1})
            self.assertEqual(self.window.level.text(), action)
            self.assertIn(color, self.window.alert_badge.text())
            self.assertEqual(self.window.alert_symbol.text(), '!')
            self.assertIn('检测到火焰', self.window.evidence.text())
            styles.add(self.window.alert_card.styleSheet())
        self.assertEqual(len(styles), 3)

    def test_sound_preview_is_explicit_and_does_not_change_sound_or_mute_preferences(self):
        self.window.alert_sound.setChecked(False)
        muted = self.window.muted_until
        with patch('ui.window.QApplication.beep') as beep:
            self.window.test_sound_button.click()
            beep.assert_called_once()
        self.assertFalse(self.window.alert_sound.isChecked())
        self.assertEqual(self.window.muted_until, muted)
        self.assertIn('试听请求', self.window.sound_test_result.text())

    def test_old_notice_titles_are_presented_with_new_vocabulary_without_rewriting_records(self):
        old = {**notice('old', level='LOW'), 'title': '火焰视觉事件 · 关注'}
        self.window._receive_notifications([old], historical=True)
        self.assertIn('黄色提示', self.window.notification_table.item(0, 3).text())
        self.assertNotIn('关注', self.window.notification_table.item(0, 3).text())
        self.assertEqual(self.window.notifications['old']['title'], '火焰视觉事件 · 关注')

    def test_open_event_timeline_does_not_extend_into_unobserved_future(self):
        timeline = self.window.timeline
        timeline.resize(500, 100)
        timeline.set_events([{'event_id': 1, 'label': 'fire', 'started_at_s': 0, 'confirmed_at_s': 1}], end_s=1)
        timeline.grab()
        self.assertEqual(len(timeline.hits), 1)
        self.assertLess(timeline.hits[0][0].width() / (timeline.width() - 40), .11)

    def test_stale_live_frame_clears_current_risk_and_marks_display(self):
        from time import monotonic
        worker = AnalysisWorker(AnalysisOptions(source='0', source_kind='camera'))
        self.window.task = worker
        self.window.run_config = {'source_kind': 'camera'}
        self.window._last_live_frame_at = monotonic() - 3
        self.window._last_frame_clock = '14:32:08'
        self.window.score.setText('指数 88 / 100')
        self.window._refresh_preview()
        self.assertIn('过时', self.window.level.text())
        self.assertEqual(self.window.score.text(), '辅助评分 —（0—100）')
        self.assertIn('14:32:08', self.window.video.context.text())
        self.assertTrue(all(label.text() == '—' for label in self.window.feature_labels.values()))
        self.window.task = None

    def test_preview_can_transition_to_analysis_without_an_extra_manual_stop(self):
        preview = CameraPreviewWorker(0)
        self.window.task = preview
        self.window.start_analysis()
        self.assertTrue(preview.stop_event.is_set())
        self.assertTrue(self.window._start_after_preview)
        with patch.object(self.window, 'start_analysis') as start:
            self.window._task_finished()
            self.app.processEvents()
            start.assert_called_once()


if __name__ == '__main__':
    unittest.main()
