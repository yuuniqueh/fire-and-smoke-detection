"""The new checkpoint is a fire-only branch; original smoke remains intact."""
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from core.runtime import configure_runtime
configure_runtime()
from PySide6.QtWidgets import QApplication
from core.model_profiles import (MODERN_PATH, MODERN_SHA256, ORIGINAL_SHA256,
                                 ModernFireFusionDetector, create_detector)
from core.event_profiles import validate_association
from core.preferences import DEFAULTS, validate_preferences
from ui.window import MainWindow


class ModernProfileTests(unittest.TestCase):
    def test_fire_branch_source_mapping_and_original_smoke_are_exact(self):
        rows = [[1, 2, 30, 40, .8, 0], [1, 2, 30, 40, .99, 2], [1, 2, 30, 40, .95, 1]]
        data = SimpleNamespace(cpu=lambda: SimpleNamespace(tolist=lambda: rows))
        candidate = Mock(names={0: 'fire', 1: 'other', 2: 'smoke'})
        candidate.predict.return_value = [SimpleNamespace(boxes=SimpleNamespace(data=data))]
        smoke = {'class_id': 0, 'label': 'smoke', 'confidence': .36, 'bbox_xyxy': [50, 60, 90, 100]}
        original = Mock(device='cpu')
        original.predict.return_value = (None, [smoke, {**smoke, 'class_id': 1, 'label': 'fire'}])
        with patch('core.model_profiles.verify_weight') as verify, \
                patch('core.detector.Detector', return_value=original), \
                patch('ultralytics.YOLO', return_value=candidate) as loader:
            detector = create_detector(Path('base.pt'), profile='modern')
        self.assertIsInstance(detector, ModernFireFusionDetector)
        self.assertEqual(verify.call_args_list[0].args, (Path('base.pt'), ORIGINAL_SHA256))
        self.assertEqual(verify.call_args_list[1].args, (MODERN_PATH, MODERN_SHA256))
        loader.assert_called_once_with(str(MODERN_PATH))
        frame = object()
        _, output = detector.predict(frame, annotate=False, imgsz=960)
        self.assertEqual(output, [{'class_id': 1, 'label': 'fire', 'confidence': .8, 'bbox_xyxy': [1, 2, 30, 40]}, smoke])
        original.predict.assert_called_once_with(frame, annotate=False, imgsz=960)
        self.assertEqual(candidate.predict.call_args.kwargs['imgsz'], 960)
        manifest = detector.model_manifest
        self.assertEqual(manifest['profile'], 'modern')
        self.assertEqual(manifest['branches']['fire']['sha256'], MODERN_SHA256)
        self.assertEqual(manifest['branches']['smoke']['sha256'], ORIGINAL_SHA256)

    def test_threshold_and_unvalidated_association_are_rejected(self):
        for confidence in (.25, .5, float('nan')):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                create_detector(Path('missing'), confidence=confidence, profile='modern')
        with self.assertRaises(ValueError):
            validate_association('regions', 'modern')

    def test_modern_preferences_normalize_threshold_and_association(self):
        settings = validate_preferences({**DEFAULTS, 'model_profile': 'modern', 'confidence': .9,
                                         'association_profile': 'regions'})
        self.assertEqual(settings['confidence'], .35)
        self.assertEqual(settings['association_profile'], 'original')

    def test_ui_locks_threshold_and_reports_missing_candidate_without_fallback(self):
        app = QApplication.instance() or QApplication([])
        with patch('ui.window.list_runs', return_value=[]):
            window = MainWindow(settings_path=False)
        try:
            window.model_profile.setCurrentIndex(window.model_profile.findData('modern'))
            self.assertEqual(window.confidence.value(), .35)
            self.assertFalse(window.confidence.isEnabled())
            self.assertFalse(window.association_profile.isEnabled())
            window.set_source(Path(__file__))
            with patch('ui.window.MODERN_PATH', Path('missing-modern-file.pt')), \
                    patch('ui.window.AnalysisWorker') as worker:
                window.start_analysis()
                worker.assert_not_called()
            self.assertIn('缺少 YOLO26', window.last_error)
            self.assertEqual(window.model_profile.currentData(), 'modern')
        finally:
            window.close()
            window.deleteLater()
            app.processEvents()


if __name__ == '__main__':
    unittest.main()
