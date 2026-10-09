import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.model_profiles import create_detector, route_detections, verify_weight


class ModelProfileTests(unittest.TestCase):
    def test_brightlight_routing_keeps_correct_sources_and_excludes_other(self):
        def box(label, native_id, score):
            return {'label': label, 'class_id': native_id, 'confidence': score, 'bbox_xyxy': [1, 2, 30, 40]}
        original = [box('smoke', 0, .35), box('fire', 1, .99)]
        candidate = [box('fire', 0, .4), box('smoke', 2, .99), box('other', 1, .99), box('fire', 0, .349999)]
        result = route_detections(original, candidate)
        self.assertEqual([(b['label'], b['class_id'], b['confidence']) for b in result],
                         [('fire', 1, .4), ('smoke', 0, .35)])
        result[0]['bbox_xyxy'][0] = 99
        self.assertEqual(candidate[0]['bbox_xyxy'][0], 1)

    def test_unvalidated_profile_or_candidate_threshold_is_rejected(self):
        with self.assertRaises(ValueError):
            create_detector(Path('unused'), profile='unknown')
        for confidence in (.5, float('nan'), float('inf')):
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                create_detector(Path('unused'), confidence=confidence, profile='brightlight')

    def test_original_profile_preserves_original_detector_options(self):
        with patch('core.detector.Detector') as detector:
            created = create_detector(Path('custom.pt'), device='cpu', confidence=.6)
            detector.assert_called_once_with(Path('custom.pt'), 'cpu', .6)
            self.assertIs(created, detector.return_value)

    def test_changed_or_missing_weights_do_not_silently_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'weight.pt'
            with self.assertRaises(FileNotFoundError):
                verify_weight(path, '0'*64)
            path.write_bytes(b'first')
            expected = hashlib.sha256(b'first').hexdigest()
            verify_weight(path, expected)
            path.write_bytes(b'changed')
            with self.assertRaises(ValueError):
                verify_weight(path, expected)


if __name__ == '__main__':
    unittest.main()
