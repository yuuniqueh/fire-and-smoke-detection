"""Record portability and export failure behavior, isolated from model inference."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from core.records import export_run, list_runs, load_curve, load_run


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = self.root / 'run-20260101-120000'
        self.run.mkdir()
        (self.run / 'summary.json').write_text(json.dumps({'status': 'completed', 'processed_frames': 3}), encoding='utf-8')
        (self.run / 'config.json').write_text(json.dumps({'source': '中文视频.mp4'}), encoding='utf-8')

    def tearDown(self):
        self.temporary.cleanup()

    def test_export_has_relative_evidence_and_excludes_unrelated_files(self):
        (self.run / 'evidence').mkdir()
        (self.run / 'evidence' / 'event-1.jpg').write_bytes(b'example')
        (self.run / 'private-notes.txt').write_text('excluded', encoding='utf-8')
        target = self.root / '导出.zip'
        export_run(self.run, target)
        with zipfile.ZipFile(target) as archive:
            self.assertIn('evidence/event-1.jpg', archive.namelist())
            self.assertNotIn('private-notes.txt', archive.namelist())
            self.assertIn('summary.json', archive.namelist())
            self.assertIsNone(archive.testzip())
            self.assertTrue(all(not name.startswith('/') and '..' not in Path(name).parts for name in archive.namelist()))

    def test_export_to_run_itself_is_rejected(self):
        with self.assertRaises(ValueError):
            export_run(self.run, self.run / 'export.zip')

    def test_incomplete_export_does_not_overwrite_existing_destination(self):
        target = self.root / 'existing.zip'
        target.write_bytes(b'previous export')
        (self.run / 'summary.json').unlink()
        with self.assertRaises(ValueError):
            export_run(self.run, target)
        self.assertEqual(target.read_bytes(), b'previous export')

    def test_corrupted_history_is_skipped_and_valid_record_remains_readable(self):
        broken = self.root / 'run-broken'
        broken.mkdir()
        (broken / 'summary.json').write_text('{bad', encoding='utf-8')
        self.assertEqual(len(list_runs(self.root)), 1)
        record = load_run(self.run)
        self.assertEqual(record['config']['source'], '中文视频.mp4')
        self.assertEqual(record['events'], [])

    def test_curve_uses_video_time_gaps_and_bounded_display_window(self):
        (self.run / 'risk.csv').write_text('timestamp_s,status,score\n0,awaiting_confirmation,0\n'
                                          '0.05,assessed,35\n0.1,assessed,40\n1,no_confirmed_event,0\n'
                                          '130,assessed,70\n', encoding='utf-8')
        self.assertEqual(load_curve(self.run, window_s=120), [(130.0, 70.0)])
        points = load_curve(self.run, window_s=200)
        self.assertEqual(points, [(0.0, None), (0.1, 40.0), (1.0, None), (130.0, 70.0)])


if __name__ == '__main__':
    unittest.main()
