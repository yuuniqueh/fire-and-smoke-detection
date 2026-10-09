import unittest

from core.local_fire import (LocalFireConfig, corner_crops, map_fire_candidate,
                             merge_preserving_reference)


def box(coords, score=.7, label='fire'):
    return {'class_id': 1 if label == 'fire' else 0, 'label': label,
            'confidence': score, 'bbox_xyxy': coords}


class LocalFireTests(unittest.TestCase):
    def setUp(self):
        self.config = LocalFireConfig()

    def test_crops_cover_every_pixel_and_remain_in_bounds(self):
        for size in ((13, 7), (640, 480), (1, 1)):
            crops = corner_crops(size, self.config)
            self.assertLessEqual(len(crops), 4)
            for x, y in ((x, y) for x in range(size[0]) for y in range(size[1])):
                self.assertTrue(any(a <= x < c and b <= y < d for a, b, c, d in crops))
            self.assertTrue(all(0 <= a < c <= size[0] and 0 <= b < d <= size[1]
                                for a, b, c, d in crops))

    def test_offset_maps_to_original_pixels(self):
        mapped = map_fire_candidate(box([10, 20, 30, 40]), (40, 40, 100, 100), (100, 100), self.config)
        self.assertEqual(mapped['bbox_xyxy'], [50, 60, 70, 80])

    def test_cut_edge_fragments_rejected_but_image_edge_allowed(self):
        self.assertIsNone(map_fire_candidate(box([0, 10, 10, 20]), (40, 0, 100, 60), (100, 100), self.config))
        self.assertIsNotNone(map_fire_candidate(box([0, 10, 10, 20]), (0, 0, 60, 60), (100, 100), self.config))

    def test_wrong_class_low_score_large_and_invalid_boxes_rejected(self):
        for candidate in (box([10, 10, 20, 20], label='smoke'), box([10, 10, 20, 20], .2),
                          box([10, 10, 40, 40]), box([20, 10, 10, 20]),
                          box([float('nan'), 10, 20, 20])):
            self.assertIsNone(map_fire_candidate(candidate, (0, 0, 60, 60), (100, 100), self.config))

    def test_reference_preserved_even_if_local_score_higher(self):
        reference = [box([10, 10, 20, 20], .4), box([10, 10, 20, 20], label='smoke')]
        merged = merge_preserving_reference(reference, [box([10, 10, 20, 20], .9),
                            box([50, 50, 60, 60]), box([50, 50, 60, 60], .5)], self.config)
        self.assertEqual(merged[:2], reference)
        self.assertEqual(len(merged), 3)
        self.assertEqual(len(reference), 2)


if __name__ == '__main__':
    unittest.main()
