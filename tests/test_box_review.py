import copy
import tempfile
import unittest
from pathlib import Path

from core.box_review import initialize, load, save, training_eligible, validate_document, validate_row


class BoxReviewTests(unittest.TestCase):
    def setUp(self):
        self.manifest = {'images': [{'image': 'images/a.png', 'image_sha256': 'a' * 64,
            'width': 100, 'height': 50, 'source': 'dev.avi', 'source_sha256': 'b' * 64,
            'frame_index': 3, 'timestamp_s': .3, 'split': 'development'}]}
        self.sha = 'c' * 64
        self.document = initialize(self.manifest, self.sha)

    def test_pending_and_uncertain_are_not_training_negatives(self):
        row = self.document['images'][0]
        self.assertFalse(training_eligible(row))
        row.update(completed=True, reviewer_id='R1', notes='遮挡看不清',
                   class_status={'fire': 'uncertain', 'smoke': 'absent'})
        self.assertFalse(training_eligible(row))
        row['class_status']['fire'] = 'absent'
        self.assertTrue(training_eligible(row))

    def test_completed_positive_requires_a_box(self):
        row = self.document['images'][0]
        row.update(completed=True, reviewer_id='R1', class_status={'fire': 'present', 'smoke': 'absent'})
        with self.assertRaises(ValueError):
            validate_row(row)
        row['boxes'] = [{'label': 'fire', 'xyxy': [0, 0, 100, 50]}]
        self.assertTrue(training_eligible(row))
        row['class_status']['fire'] = 'absent'
        with self.assertRaises(ValueError):
            validate_row(row)

    def test_bad_box_and_changed_source_refused(self):
        for coords in ([0, 0, 0, 10], [-1, 0, 20, 10], [0, 0, 101, 10], [0, 0, float('nan'), 10]):
            row = copy.deepcopy(self.document['images'][0])
            row['boxes'] = [{'label': 'fire', 'xyxy': coords}]
            with self.assertRaises(ValueError):
                validate_row(row)
        self.document['images'][0]['split'] = 'internal_holdout'
        with self.assertRaises(ValueError):
            validate_document(self.document, self.manifest, self.sha)

    def test_completed_requires_reviewer_and_both_decisions(self):
        row = self.document['images'][0]
        row['completed'] = True
        with self.assertRaises(ValueError):
            validate_row(row)
        row['class_status'] = {'fire': 'absent', 'smoke': 'absent'}
        with self.assertRaises(ValueError):
            validate_row(row)

    def test_revision_guard_and_previous_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'annotations.json'
            rev1 = save(path, self.document, self.manifest, self.sha, None)
            self.document['images'][0]['notes'] = '草稿'
            rev2 = save(path, self.document, self.manifest, self.sha, rev1)
            with self.assertRaises(ValueError):
                save(path, self.document, self.manifest, self.sha, rev1)
            doc, revision = load(path, self.manifest, self.sha)
            self.assertEqual(revision, rev2)
            self.assertEqual(doc['images'][0]['notes'], '草稿')
            self.assertTrue((path.parent / 'box-history' / f'annotations-{rev1}.json').exists())


if __name__ == '__main__':
    unittest.main()
