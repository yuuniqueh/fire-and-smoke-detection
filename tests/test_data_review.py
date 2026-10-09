import copy
import tempfile
import unittest
from pathlib import Path

from core.data_review import initialize_review, read_review, replace_video, save_review, validate_video


class DataReviewTests(unittest.TestCase):
    def setUp(self):
        manifest = {'dataset': 'test', 'videos': [
            {'filename': 'test.avi', 'sha256': 'fixed', 'fps': 10, 'decoded_frames': 100, 'frame_count_matches': True}]}
        self.document = initialize_review(manifest, {'test.avi'})
        self.row = copy.deepcopy(self.document['videos'][0])

    def test_publisher_label_not_assumed(self):
        self.assertEqual(self.row['class_status'], {'fire': 'unknown', 'smoke': 'unknown'})
        self.assertFalse(self.row['full_video_reviewed'])
        self.assertEqual(self.row['split'], 'development_seen')

    def test_invalid_or_overlapping_boundaries_rejected(self):
        self.row['class_status']['fire'] = 'present'
        for intervals in [
            [{'label': 'fire', 'start_frame': 10, 'end_frame_exclusive': 10}],
            [{'label': 'fire', 'start_frame': 99, 'end_frame_exclusive': 101}],
            [{'label': 'fire', 'start_frame': -1, 'end_frame_exclusive': 10}],
            [{'label': 'fire', 'start_frame': 0, 'end_frame_exclusive': 50},
             {'label': 'fire', 'start_frame': 49, 'end_frame_exclusive': 70}]]:
            self.row['intervals'] = intervals
            with self.assertRaises(ValueError):
                validate_video(self.row)

    def test_cross_class_overlap_and_end_boundary_valid(self):
        self.row['class_status'] = {'fire': 'present', 'smoke': 'present'}
        self.row['intervals'] = [{'label': label, 'start_frame': 0, 'end_frame_exclusive': 100}
                                 for label in ('fire', 'smoke')]
        validate_video(self.row)

    def test_completed_review_requires_human_and_both_classes(self):
        self.row['full_video_reviewed'] = True
        with self.assertRaises(ValueError):
            validate_video(self.row)
        self.row['reviewer'] = 'R1'
        with self.assertRaises(ValueError):
            validate_video(self.row)
        self.row['class_status'] = {'fire': 'absent', 'smoke': 'absent'}
        validate_video(self.row)
        self.row['class_status']['fire'] = 'present'
        with self.assertRaises(ValueError):
            validate_video(self.row)

    def test_seen_samples_cannot_become_test_samples(self):
        self.row['split'] = 'test'
        with self.assertRaises(ValueError):
            replace_video(self.document, self.row)

    def test_stale_edit_rejected_and_previous_annotation_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'review.json'
            revision = save_review(path, self.document)
            self.row['notes'] = 'first saved review'
            updated = replace_video(self.document, self.row)
            save_review(path, updated, revision)
            backup, _ = read_review(path.parent / 'history' / f'review-{revision}.json')
            self.assertEqual(backup, self.document)
            with self.assertRaises(ValueError):
                save_review(path, self.document, revision)
            actual, _ = read_review(path)
            self.assertEqual(actual['videos'][0]['notes'], 'first saved review')


if __name__ == '__main__':
    unittest.main()
