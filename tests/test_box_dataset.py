import copy
import unittest

from core.box_dataset import assign_grouped_splits, to_yolo_text


class BoxDatasetTests(unittest.TestCase):
    def setUp(self):
        self.row = {'width': 640, 'height': 320, 'completed': True, 'reviewer_id': 'R1', 'notes': '',
                    'class_status': {'fire': 'present', 'smoke': 'present'},
                    'boxes': [{'label': 'smoke', 'xyxy': [0, 0, 640, 320]},
                              {'label': 'fire', 'xyxy': [160, 80, 320, 160]}]}

    def test_model_class_order_and_normalization(self):
        text = to_yolo_text(self.row)
        self.assertEqual(text, '0 0.50000000 0.50000000 1.00000000 1.00000000\n'
                               '1 0.37500000 0.37500000 0.25000000 0.25000000\n')

    def test_reviewed_empty_negative_and_unknown_draft(self):
        row = copy.deepcopy(self.row)
        row.update(boxes=[], class_status={'fire': 'absent', 'smoke': 'absent'})
        self.assertEqual(to_yolo_text(row), '')
        row['completed'] = False
        with self.assertRaises(ValueError):
            to_yolo_text(row)

    def test_uncertain_whole_image_not_exported_as_partial_truth(self):
        self.row['class_status']['smoke'] = 'uncertain'
        self.row['notes'] = '烟雾边界无法确认'
        with self.assertRaises(ValueError):
            to_yolo_text(self.row)

    def test_correlated_video_stays_with_validation_group(self):
        entries = {source: {'constraint_group': group, 'split': 'development', 'source_sha256': 'hash'}
                   for source, group in [('a', 'A'), ('b', 'A'), ('c', 'C')]}
        rows = [{**self.row, 'source': source, 'source_sha256': 'hash'} for source in ('a', 'b', 'c')]
        self.assertEqual(assign_grouped_splits(rows, entries, ('a',)), ['val', 'val', 'train'])
        entries['c']['split'] = 'internal_holdout'
        with self.assertRaises(ValueError):
            assign_grouped_splits(rows, entries, ('a',))


if __name__ == '__main__':
    unittest.main()
