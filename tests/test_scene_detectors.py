"""Scene selection must load pinned weights and preserve class semantics."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from core.model_profiles import (ROOT, SCENE_WEIGHTS, ORIGINAL_SHA256, create_detector,
                                 SceneDetector, UAVFireFusionDetector)


class SceneDetectorTests(unittest.TestCase):
    def test_indoor_and_forest_load_their_own_pinned_model_and_normalize_class_ids(self):
        boxes = [{'class_id': 0, 'label': 'fire', 'confidence': .8, 'bbox_xyxy': [1, 2, 8, 9]},
                 {'class_id': 1, 'label': 'smoke', 'confidence': .7, 'bbox_xyxy': [2, 3, 9, 10]}]
        for profile in ('domestic', 'forest'):
            with self.subTest(profile=profile):
                native = Mock(device='cpu')
                native.predict.return_value = (None, boxes)
                with patch('core.model_profiles.verify_weight') as verify, \
                        patch('core.detector.Detector', return_value=native) as loader:
                    detector = create_detector(Path('unused-baseline.pt'), 'cpu', profile=profile)
                spec = SCENE_WEIGHTS[profile]
                verify.assert_called_once_with(ROOT / spec['path'], spec['sha256'])
                loader.assert_called_once_with(ROOT / spec['path'], 'cpu', .35)
                self.assertIsInstance(detector, SceneDetector)
                _, actual = detector.predict(object(), annotate=False)
                self.assertEqual([box['class_id'] for box in actual], [1, 0])
                self.assertEqual([box['class_id'] for box in boxes], [0, 1])
                for branch in detector.model_manifest['branches'].values():
                    self.assertEqual((branch['revision'], branch['sha256']), (spec['revision'], spec['sha256']))

    def test_uav_routes_fire_at_point_five_and_retains_original_smoke(self):
        rows = [[1, 2, 8, 9, .49, 0], [2, 3, 9, 10, .50, 0], [4, 5, 11, 12, .99, 1]]
        candidate = Mock(names={0: 'fire', 1: 'smoke'})
        candidate.predict.return_value = [SimpleNamespace(boxes=SimpleNamespace(
            data=SimpleNamespace(cpu=lambda: SimpleNamespace(tolist=lambda: rows))))]
        smoke = {'class_id': 0, 'label': 'smoke', 'confidence': .35, 'bbox_xyxy': [1, 1, 8, 8]}
        original = Mock(device='cpu')
        original.predict.return_value = (None, [smoke, {**smoke, 'label': 'fire'}])
        with patch('core.model_profiles.verify_weight') as verify, \
                patch('core.detector.Detector', return_value=original), \
                patch('ultralytics.YOLO', return_value=candidate):
            detector = create_detector(Path('base.pt'), 'cpu', profile='uav')
        self.assertIsInstance(detector, UAVFireFusionDetector)
        self.assertEqual(len(verify.call_args_list), 2)
        _, output = detector.predict(object(), annotate=False)
        self.assertEqual([(b['label'], b['confidence']) for b in output], [('fire', .5), ('smoke', .35)])
        self.assertEqual(candidate.predict.call_args.kwargs['conf'], .5)
        self.assertEqual(detector.model_manifest['branches']['smoke']['sha256'], ORIGINAL_SHA256)

    def test_mismatch_prevents_loading_and_scene_thresholds_are_not_silently_changed(self):
        for profile in SCENE_WEIGHTS:
            with self.subTest(profile=profile), patch('core.model_profiles.verify_weight', side_effect=ValueError('checksum')), \
                    patch('core.detector.Detector') as loader:
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    create_detector(Path('base.pt'), profile=profile)
                loader.assert_not_called()
            with self.assertRaises(ValueError):
                create_detector(Path('base.pt'), confidence=.9, profile=profile)
