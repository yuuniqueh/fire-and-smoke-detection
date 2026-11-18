import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication, QMessageBox
from ui.window import MainWindow


class StorageUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        outputs = self.root / 'outputs'
        outputs.mkdir()
        self.folder = outputs / 'run-20200101-000000-000001'
        self.folder.mkdir()
        (self.folder / 'summary.json').write_text('{"status":"completed","processed_frames":0}')
        (self.folder / 'config.json').write_text('{}')
        self.patch = patch('ui.window.ROOT', self.root)
        self.patch.start()
        self.window = MainWindow(settings_path=False)

    def tearDown(self):
        if self.window.busy:
            self.wait()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.patch.stop()
        self.temp.cleanup()

    def wait(self):
        deadline = time.monotonic() + 5
        while self.window.busy:
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail('Storage UI task timed out')
            time.sleep(.01)
        self.app.processEvents()

    def test_default_scan_protect_unprotect_and_confirmed_cleanup_workflow(self):
        w = self.window
        self.assertFalse(w.storage_automatic.isChecked())
        self.assertEqual(w.recording_mode.currentData(), 'events')
        w.storage_scan.click()
        self.wait()
        self.assertTrue(self.folder.exists())
        self.assertEqual(len(w.storage_plan['candidates']), 1)
        w.storage_table.selectRow(0)
        w.storage_protect.click()
        self.wait()
        self.assertTrue((self.folder / 'protected.json').exists())
        self.assertFalse(w.storage_plan['candidates'])
        w.storage_table.selectRow(0)
        w.storage_protect.click()
        self.wait()
        self.assertFalse((self.folder / 'protected.json').exists())
        with patch('ui.window.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
            w.storage_cleanup.click()
        self.assertTrue(self.folder.exists())
        with patch('ui.window.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
            w.storage_cleanup.click()
        self.wait()
        self.assertFalse(self.folder.exists())
        self.assertEqual(w.storage_table.rowCount(), 0)
        self.assertEqual(w.storage_metrics['count'].text(), '0')

    def test_current_record_is_excluded_and_policy_change_invalidates_preview(self):
        w = self.window
        w.current_run = self.folder
        w.scan_storage()
        self.wait()
        self.assertFalse(w.storage_plan['candidates'])
        self.assertIn('使用中', w.storage_records[0]['reason'])
        w.storage_days.setValue(7)
        self.assertIsNone(w.storage_plan)
        self.assertFalse(w.storage_cleanup.isEnabled())
        w.release_current_record()
        self.wait()
        self.assertIsNone(w.current_run)
        self.assertEqual(len(w.storage_plan['candidates']), 1)


if __name__ == '__main__':
    unittest.main()
