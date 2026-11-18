"""Persistence, recovery, diagnostics and handoff safety without model inference."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from core.diagnostics import collect_diagnostics
from core.preferences import DEFAULTS, PreferencesStore, validate_preferences
from core.transfer import RUNTIME_FILES, build_transfer
from ui.window import MainWindow


class SettingsDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / '.cache' / 'desktop-settings.json'
        self.store = PreferencesStore(self.path)
        self.windows = []

    def tearDown(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def window(self):
        with patch('ui.window.list_runs', return_value=[]):
            window = MainWindow(settings_path=self.path)
        self.windows.append(window)
        return window

    def test_settings_roundtrip_excludes_unknown_paths_and_normalizes_profile(self):
        self.store.save({**DEFAULTS, 'device': 'cpu', 'source': 'private.mp4',
                         'association_profile': 'regions'})
        saved, _ = self.store.load()
        self.assertEqual(saved['device'], 'cpu')
        self.assertEqual(saved['association_profile'], 'original')
        self.assertNotIn('private.mp4', self.path.read_text(encoding='utf-8'))
        normalized = validate_preferences({**DEFAULTS, 'model_profile': 'brightlight', 'confidence': .8})
        self.assertEqual(normalized['confidence'], .35)

    def test_invalid_settings_preserve_original_until_explicit_recovery(self):
        self.path.parent.mkdir()
        self.path.write_text('{broken', encoding='utf-8')
        window = self.window()
        self.assertEqual(window.device.currentData(), 'auto')
        self.assertIn('原文件保留', window.settings_status.text())
        window.close()
        self.assertEqual(self.path.read_text(encoding='utf-8'), '{broken')
        window.reset_preferences()
        self.assertEqual(self.store.load()[0], DEFAULTS)

    def test_atomic_save_failure_preserves_previous_settings(self):
        self.store.save(DEFAULTS)
        previous = self.path.read_bytes()
        with patch('core.preferences.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.store.save({**DEFAULTS, 'alert_sound': True})
        self.assertEqual(self.path.read_bytes(), previous)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_invalid_values_rejected(self):
        for field, value in [('confidence', float('nan')), ('confidence', True), ('alert_sound', 'yes'),
                              ('device', 'cuda:99'), ('window_width', 1), ('model_profile', 'unknown')]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.store.save({**DEFAULTS, field: value})

    def test_ui_auto_save_restore_and_reset_keep_algorithm_files_untouched(self):
        marker = self.root / 'algorithm.json'
        marker.write_text('unchanged', encoding='utf-8')
        first = self.window()
        first.device.setCurrentIndex(first.device.findData('cpu'))
        first.monitoring_profile.setCurrentIndex(first.monitoring_profile.findData('adaptive'))
        first.alert_sound.setChecked(True)
        self.assertTrue(first.preferences_timer.isActive())
        first._save_preferences()
        second = self.window()
        self.assertEqual(second.device.currentData(), 'cpu')
        self.assertEqual(second.monitoring_profile.currentData(), 'adaptive')
        self.assertTrue(second.alert_sound.isChecked())
        second.reset_preferences()
        self.assertEqual(self.store.load()[0], DEFAULTS)
        self.assertEqual(marker.read_text(encoding='utf-8'), 'unchanged')

    def test_save_error_does_not_disable_current_settings(self):
        window = self.window()
        window.device.setCurrentIndex(window.device.findData('cpu'))
        with patch.object(window.preferences, 'save', side_effect=OSError('read only')):
            window._save_preferences()
        self.assertIn('当前选项仍可使用', window.settings_status.text())
        self.assertEqual(window.device.currentData(), 'cpu')
        self.assertTrue(window.device.isEnabled())

    def test_cpu_recovery_requires_explicit_click_and_new_analysis(self):
        window = self.window()
        window.set_source(self.root / 'some-video.mp4')
        window.device.setCurrentIndex(window.device.findData('0'))
        with patch.object(window, 'start_analysis') as start:
            window._notify_error('CUDA out of memory')
            start.assert_not_called()
            self.assertFalse(window.cpu_retry.isHidden())
            window.cpu_retry.click()
            self.assertEqual(window.device.currentData(), 'cpu')
            start.assert_called_once()
        self.assertTrue(window.error_panel.isHidden())

    def test_non_gpu_error_has_no_cpu_retry(self):
        window = self.window()
        window._notify_error('视频文件不存在，请重新选择。')
        self.assertTrue(window.cpu_retry.isHidden())
        self.assertEqual(window.error_message.text(), window.last_error)
        window.dismiss_error.click()
        self.assertTrue(window.error_panel.isHidden())

    def test_diagnostics_gpu_choice_is_actionable_but_cpu_is_supported(self):
        (self.root / 'configs').mkdir()
        (self.root / 'configs/temporal.json').write_text('{}', encoding='utf-8')
        (self.root / 'configs/risk.json').write_text('{}', encoding='utf-8')
        fake_torch = SimpleNamespace(__version__='2.8.0', cuda=SimpleNamespace(is_available=lambda: False))
        loader = lambda name: fake_torch if name == 'torch' else SimpleNamespace()
        report = collect_diagnostics(self.root, device='0', module_loader=loader, version_reader=lambda name: 'test')
        device = next(c for c in report['checks'] if c['name'] == '推理设备')
        self.assertEqual(device['status'], 'error')
        self.assertIn('CPU', device['advice'])
        self.assertFalse(report['ready'])  # Missing model is also explicitly reported.
        report = collect_diagnostics(self.root, device='cpu', module_loader=loader, version_reader=lambda name: 'test')
        self.assertEqual(next(c for c in report['checks'] if c['name'] == '推理设备')['status'], 'ok')

    def test_diagnostics_import_failure_is_reported_without_crash(self):
        def fail(name):
            raise ImportError('missing package')
        report = collect_diagnostics(self.root, module_loader=fail)
        self.assertFalse(report['ready'])
        self.assertTrue(any(c['name'] == 'PySide6' and c['status'] == 'error' for c in report['checks']))

    def _bundle_fixture(self):
        for name in RUNTIME_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('runtime fixture', encoding='utf-8')
        for folder in ('core', 'ui'):
            (self.root / folder).mkdir()
            (self.root / folder / '__init__.py').write_text('', encoding='utf-8')
        for name in ('videos/private.mp4', 'data/annotations.json', '.cache/desktop-settings.json',
                     'outputs/history.json', '.env', 'models/best.pt', '.git/config'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('private marker', encoding='utf-8')

    def test_handoff_excludes_private_inputs_and_environments(self):
        self._bundle_fixture()
        target = self.root / 'outputs/package.zip'
        manifest = build_transfer(self.root, target)
        with zipfile.ZipFile(target) as archive:
            names = archive.namelist()
            self.assertIn('FireGuard/start_desktop.cmd', names)
            self.assertIn('FireGuard/transfer-manifest.json', names)
            self.assertFalse(any(any(part in name for part in ('private.mp4', 'annotations.json', '.cache/', '.env', '.git/', 'history.json', 'best.pt'))
                                 for name in names))
            self.assertNotIn(str(self.root), archive.read('FireGuard/transfer-manifest.json').decode('utf-8'))
            self.assertIsNone(archive.testzip())
        self.assertFalse(manifest['contains_python_environment'])
        self.assertTrue(all('sha256' in f for f in manifest['files']))

    def test_handoff_rejects_unregistered_model_before_overwriting_package(self):
        self._bundle_fixture()
        target = self.root / 'existing.zip'
        target.write_bytes(b'previous')
        with self.assertRaises(ValueError):
            build_transfer(self.root, target, include_models=True)
        self.assertEqual(target.read_bytes(), b'previous')
        weight = self.root / 'models/best.pt'
        previous_weight = weight.read_bytes()
        with self.assertRaises(ValueError):
            build_transfer(self.root, weight)
        self.assertEqual(weight.read_bytes(), previous_weight)

    def test_incomplete_handoff_preserves_existing_destination(self):
        self._bundle_fixture()
        (self.root / 'desktop.py').unlink()
        target = self.root / 'existing.zip'
        target.write_bytes(b'previous')
        with self.assertRaises(ValueError):
            build_transfer(self.root, target)
        self.assertEqual(target.read_bytes(), b'previous')


if __name__ == '__main__':
    unittest.main()
