import unittest
from experiments.screen_brightlight_candidate import canonical_targets
from experiments.replay_brightlight_routing import route


class BrightlightCandidateTests(unittest.TestCase):
    def test_other_is_excluded_but_does_not_veto_overlapping_real_target(self):
        raw = [{'class_id': 0, 'label': 'fire', 'confidence': .8, 'bbox_xyxy': [1, 2, 30, 40]},
               {'class_id': 1, 'label': 'other', 'confidence': .9, 'bbox_xyxy': [1, 2, 30, 40]},
               {'class_id': 2, 'label': 'smoke', 'confidence': .7, 'bbox_xyxy': [1, 2, 30, 40]}]
        targets = canonical_targets(raw)
        self.assertEqual([(b['label'], b['class_id']) for b in targets], [('fire', 1), ('smoke', 0)])
        targets[0]['bbox_xyxy'][0] = 99
        self.assertEqual(raw[0]['bbox_xyxy'][0], 1)
        self.assertEqual([b['class_id'] for b in raw], [0, 1, 2])

    def test_unknown_label_is_rejected_instead_of_silently_routed(self):
        with self.assertRaises(ValueError):
            canonical_targets([{'label': 'red-object'}])

    def test_replay_routes_only_selected_branches_at_fixed_035_threshold(self):
        header = {'frame_index': 1, 'timestamp_s': .1, 'frame_size': [320, 240]}
        box = {'bbox_xyxy': [1, 2, 20, 30]}
        original = {**header, 'detections': [
            {**box, 'label': 'fire', 'class_id': 1, 'confidence': .99},
            {**box, 'label': 'smoke', 'class_id': 0, 'confidence': .35}]}
        candidate = {**header, 'detections': [
            {**box, 'label': 'fire', 'class_id': 0, 'confidence': .4},
            {**box, 'label': 'fire', 'class_id': 0, 'confidence': .349999},
            {**box, 'label': 'other', 'class_id': 1, 'confidence': .99},
            {**box, 'label': 'smoke', 'class_id': 2, 'confidence': .99}]}
        routed = route(original, candidate)
        self.assertEqual([(b['label'], b['class_id'], b['confidence']) for b in routed['detections']],
                         [('fire', 1, .4), ('smoke', 0, .35)])
        self.assertEqual(len(candidate['detections']), 4)

    def test_replay_rejects_misaligned_frame_streams(self):
        packet = {'frame_index': 1, 'timestamp_s': .1, 'frame_size': [320, 240], 'detections': []}
        for change in ({'frame_index': 2}, {'timestamp_s': .2}, {'frame_size': [640, 480]}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                route(packet, {**packet, **change})


if __name__ == '__main__':
    unittest.main()
