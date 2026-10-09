import copy
import unittest
from experiments.probe_class_routing import route_packet


def packet(boxes):
    return {'frame_index': 8, 'timestamp_s': .8, 'frame_size': [640, 480], 'detections': boxes}


def box(label, class_id, score):
    return {'label': label, 'class_id': class_id, 'confidence': score, 'bbox_xyxy': [10, 10, 30, 30]}


class ClassRoutingTests(unittest.TestCase):
    def test_conflicting_source_ids_are_normalized_and_only_authorized_branches_survive(self):
        reference = packet([box('fire', 1, .99), box('smoke', 0, .35)])
        candidate = packet([box('smoke', 1, .99), box('fire', 0, .50)])
        before = copy.deepcopy((reference, candidate))
        routed = route_packet(reference, candidate)
        self.assertEqual([(b['label'], b['class_id'], b['confidence']) for b in routed['detections']],
                         [('fire', 1, .50), ('smoke', 0, .35)])
        self.assertEqual((reference, candidate), before)
        routed['detections'][0]['bbox_xyxy'][0] = 0
        self.assertEqual(candidate['detections'][1]['bbox_xyxy'][0], 10)

    def test_unaligned_streams_are_rejected_instead_of_combining_other_frames(self):
        reference = packet([])
        for name, value in (('frame_index', 9), ('timestamp_s', .9), ('frame_size', [480, 640])):
            candidate = {**packet([]), name: value}
            with self.subTest(field=name), self.assertRaises(ValueError):
                route_packet(reference, candidate)


if __name__ == '__main__':
    unittest.main()
