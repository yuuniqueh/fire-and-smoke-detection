import unittest
from core.box_metrics import count_boxes, sum_counts


class BoxMetricTests(unittest.TestCase):
    def test_one_prediction_cannot_cover_two_truths(self):
        truth = [{'label': 'fire', 'xyxy': [0, 0, 10, 10]}] * 2
        result = count_boxes(truth, [{'label': 'fire', 'bbox_xyxy': [0, 0, 10, 10]}])
        self.assertEqual(result['fire'], {'tp': 1, 'fp': 0, 'fn': 1})

    def test_wrong_class_is_false_and_missed(self):
        result = count_boxes([{'label': 'fire', 'xyxy': [0, 0, 10, 10]}],
                             [{'label': 'smoke', 'bbox_xyxy': [0, 0, 10, 10]}])
        self.assertEqual(result['fire'], {'tp': 0, 'fp': 0, 'fn': 1})
        self.assertEqual(result['smoke'], {'tp': 0, 'fp': 1, 'fn': 0})
        summary = sum_counts([result])
        self.assertEqual(summary['fire']['recall'], 0)
        self.assertIsNone(summary['fire']['precision'])


if __name__ == '__main__':
    unittest.main()
