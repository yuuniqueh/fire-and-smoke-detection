"""Deletion stays inside generated temporary records; actual project history is never cleaned."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from core.storage import (StorageLimitError, cleanup_plan, enforce_quota,
                          execute_cleanup, inventory, protect_record)
from core.preferences import DEFAULTS, PreferencesStore, validate_preferences


OLD = 'run-20200101-000000-000001'
NEW = 'run-20261012-000000-000001'


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.outputs = self.root / 'outputs'
        self.outputs.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def record(self, name=OLD, size=1000, completed=True):
        folder = self.outputs / name
        folder.mkdir()
        (folder / 'config.json').write_text('{}')
        (folder / 'annotated.mp4').write_bytes(b'x' * size)
        if completed:
            (folder / 'summary.json').write_text('{"status":"completed"}')
        return folder

    def test_preview_is_read_only_and_retention_targets_only_completed_old_records(self):
        old = self.record()
        new = self.record(NEW)
        plan = cleanup_plan(self.outputs, max_bytes=1000000, now=datetime(2026, 10, 13).timestamp())
        self.assertEqual([r['name'] for r in plan['candidates']], [OLD])
        self.assertTrue(old.exists() and new.exists())
        result = execute_cleanup(self.outputs, plan)
        self.assertEqual(len(result['removed']), 1)
        self.assertFalse(old.exists())
        self.assertTrue(new.exists())

    def test_quota_evicts_oldest_eligible_record_and_counts_protected_bytes(self):
        self.record(OLD)
        self.record(NEW)
        protect_record(self.outputs, NEW, True)
        plan = cleanup_plan(self.outputs, retention_days=3650, max_bytes=1500)
        self.assertEqual([r['name'] for r in plan['candidates']], [OLD])
        self.assertGreater(plan['remaining_bytes'], 1000)
        self.assertFalse(plan['over_limit'])

    def test_active_excluded_protected_unfinished_and_unfamiliar_records_are_never_candidates(self):
        folders = []
        for number in range(5):
            folders.append(self.record(f'run-20200101-00000{number}-000001', completed=number != 3))
        (folders[0] / '.active').write_text('123')
        protect_record(self.outputs, folders[2].name, True)
        (folders[4] / 'my-notes.txt').write_text('manual user data')
        plan = cleanup_plan(self.outputs, max_bytes=1, exclude=[folders[1]])
        self.assertEqual(plan['candidates'], [])
        self.assertTrue(plan['over_limit'])
        self.assertGreater(plan['total_bytes'], 5000)

    def test_changes_after_preview_are_rechecked_and_skipped(self):
        folder = self.record()
        plan = cleanup_plan(self.outputs, max_bytes=1)
        (folder / 'feedback.jsonl').write_text('{"comment":"new feedback"}\n')
        result = execute_cleanup(self.outputs, plan)
        self.assertEqual(result['removed'], [])
        self.assertEqual(len(result['skipped']), 1)
        self.assertTrue(folder.exists())

    def test_new_protection_or_active_marker_after_preview_prevents_deletion(self):
        folder = self.record()
        plan = cleanup_plan(self.outputs, max_bytes=1)
        protect_record(self.outputs, OLD, True)
        self.assertFalse(execute_cleanup(self.outputs, plan)['removed'])
        protect_record(self.outputs, OLD, False)
        plan = cleanup_plan(self.outputs, max_bytes=1)
        (folder / '.active').write_text('123')
        self.assertFalse(execute_cleanup(self.outputs, plan)['removed'])

    def test_external_paths_and_unknown_folders_cannot_be_deleted_by_forged_plan(self):
        self.record()
        outside = self.root / 'private'
        outside.mkdir()
        (outside / 'private.txt').write_text('keep')
        plan = cleanup_plan(self.outputs, max_bytes=1)
        plan['candidates'] = [{'name': '../private', 'signature': 'forged'}]
        self.assertFalse(execute_cleanup(self.outputs, plan)['removed'])
        self.assertEqual((outside / 'private.txt').read_text(), 'keep')
        with self.assertRaises(ValueError):
            protect_record(self.outputs, '../private', True)

    def test_symlink_record_is_skipped_without_traversing_target(self):
        outside = self.root / 'private'
        outside.mkdir()
        (outside / 'summary.json').write_text('{}')
        link = self.outputs / OLD
        try:
            os.symlink(outside, link, target_is_directory=True)
        except OSError:
            if os.name != 'nt':
                raise
            import _winapi
            try:
                _winapi.CreateJunction(str(outside), str(link))
            except OSError:
                self.skipTest('Sandbox disallows creating symlinks and junctions')
        try:
            self.assertFalse(cleanup_plan(self.outputs, max_bytes=1)['candidates'])
            self.assertTrue((outside / 'summary.json').exists())
        finally:
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()  # Remove the junction itself, never its target contents.

    def test_automatic_cleanup_requires_explicit_enable_and_full_protected_storage_stops(self):
        old = self.record()
        with self.assertRaises(StorageLimitError):
            enforce_quota(self.outputs, max_bytes=1, automatic=False)
        self.assertTrue(old.exists())
        enforce_quota(self.outputs, max_bytes=1, automatic=True)
        self.assertFalse(old.exists())
        self.record()
        protect_record(self.outputs, OLD, True)
        with self.assertRaises(StorageLimitError):
            enforce_quota(self.outputs, max_bytes=1, automatic=True)
        self.assertTrue((self.outputs / OLD).exists())

    def test_simultaneous_storage_operations_do_not_overlap(self):
        self.record()
        lock = self.outputs / '.storage-operation.lock'
        lock.write_text('test lock')
        with self.assertRaises(ValueError):
            protect_record(self.outputs, OLD, True)
        with self.assertRaises(ValueError):
            execute_cleanup(self.outputs, cleanup_plan(self.outputs, max_bytes=1))
        self.assertEqual(lock.read_text(), 'test lock')

    def test_old_settings_gain_safe_defaults_and_new_settings_roundtrip(self):
        store = PreferencesStore(self.root / 'settings.json')
        store.save({'device': 'cpu'})
        values, _ = store.load()
        self.assertFalse(values['storage_automatic'])
        self.assertEqual(values['recording_mode'], 'events')
        store.save({**DEFAULTS, 'storage_max_gb': 12, 'storage_retention_days': 7, 'storage_automatic': True})
        self.assertEqual(store.load()[0]['storage_max_gb'], 12)
        for name, value in [('storage_max_gb', True), ('storage_retention_days', 0),
                            ('storage_automatic', 'yes'), ('recording_mode', 'bad')]:
            with self.assertRaises(ValueError):
                validate_preferences({name: value})


if __name__ == '__main__':
    unittest.main()
