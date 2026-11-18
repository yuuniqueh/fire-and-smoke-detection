"""Stored combined levels remain authoritative; matching scores stay in details."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QLabel
from core.alert_presentation import LEVEL_ACTIONS
from core.records import load_run
from ui.window import MainWindow, COMPONENT_NAMES


class PlainResultWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run-plain-result'
        self.run.mkdir()
        self.features = {'coverage_ratio_smoothed': .04, 'positive_duration_s': 3.,
                         'growth_pp_per_s': .1, 'confidence': .87, 'trend': 'stable'}
        self.raw = {'peak_level': 'HIGH', 'peak_score': 91., 'peak_evidence': {
            'timestamp_s': 4., 'features': self.features, 'components': {'model_confidence': 5.}}}
        self.event = {'event_id': 1, 'label': 'fire', 'started_at_s': 1., 'confirmed_at_s': 2.,
                      'closed_at_s': 5., 'risk_summary': self.raw,
                      'fusion_summary': {'peak_score': 44.1, 'peak_level': 'MEDIUM'}}
        self.notice = {'notification_id': 'n1', 'event_id': 1, 'label': 'fire', 'kind': 'event_confirmed',
                       'timestamp_s': 2., 'level': 'MEDIUM', 'score': 44.1, 'channels': []}
        self.write_record()
        with patch('ui.window.list_runs', return_value=[]):
            self.window = MainWindow(settings_path=False)
        self.window.timer.stop()
        self.window.monitor_center.timer.stop()
        self.addCleanup(self.window.close)
        self.window._apply_record(load_run(self.run), historical=True)

    def write_record(self):
        for filename, value in {
            'summary.json': {'status': 'completed', 'processed_frames': 150, 'risk': self.raw,
                             'scene_fusion': {'policy': 'scene-score-v2', 'peak_score': 44.1, 'peak_level': 'MEDIUM'}},
            'config.json': {'source': str(self.root / 'clip.mp4'), 'source_kind': 'file'},
            'events.json': {'events': [self.event]}}.items():
            (self.run / filename).write_text(json.dumps(value), encoding='utf-8')
        (self.run / 'alerts.jsonl').write_text(json.dumps(self.notice) + '\n', encoding='utf-8')

    def test_select_history_uses_combined_level_not_raw_or_score_threshold(self):
        self.window.focus_event(1)
        self.window.select_event()
        self.assertEqual(self.window.level.text(), '当时建议：' + LEVEL_ACTIONS['MEDIUM'])
        self.assertIn('44.1', self.window.score.text())
        self.assertNotIn('91.0', self.window.score.text())
        self.assertIn('检测匹配分', self.window.detail.toPlainText())
        self.assertIn('原始检测评分峰值时刻', self.window.detail.toPlainText())
        self.assertEqual(self.window.feature_labels['confidence'].text(), '0.87')

    def test_combined_history_without_raw_peak_still_keeps_saved_medium_level(self):
        self.window.events[1]['risk_summary'].pop('peak_evidence')
        self.window.focus_event(1)
        self.window.select_event()
        self.assertEqual(self.window.level.text(), '当时建议：' + LEVEL_ACTIONS['MEDIUM'])
        self.assertIn('44.1', self.window.score.text())
        self.assertEqual(self.window.feature_labels['confidence'].text(), '—')

    def test_live_advice_uses_saved_level_and_auxiliary_score_is_collapsed(self):
        event = {'event_id': 1, 'label': 'fire', 'fresh_evidence': True, 'features': self.features,
                 'score_components': {'model_confidence': 5.}, 'score': 44.1, 'level': 'MEDIUM'}
        self.window._show_assessment({'status': 'assessed', 'timestamp_s': 3., 'score': 44.1,
                                      'level': 'MEDIUM', 'events': [event], 'dominant_event_id': 1})
        self.assertEqual(self.window.level.text(), LEVEL_ACTIONS['MEDIUM'])
        self.assertNotIn('44.1', self.window.level.text())
        self.assertTrue(self.window.analysis_details.isHidden())
        self.assertIs(self.window.score.parentWidget(), self.window.analysis_details)
        self.assertIn('44.1', self.window.score.text())
        self.assertNotIn('%', self.window.feature_labels['confidence'].text())
        self.assertEqual(COMPONENT_NAMES['model_confidence'], '检测匹配分')
        self.assertIn('检测匹配分', [label.text() for label in self.window.analysis_details.findChildren(QLabel)])


if __name__ == '__main__':
    unittest.main()
