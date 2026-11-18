"""Check release selection and data exclusion without building PyInstaller."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from core.packaging import (ASSETS, GUIDES, SOURCE_FILES, safe_file, write_release_files,
                            is_license_file, retain_qt_artifact)
from core.transfer import RUNTIME_FILES


class PackagingTests(unittest.TestCase):
    def test_widgets_release_retains_platforms_but_excludes_optional_gpl_module(self):
        for name in ('PySide6/Qt6Core.dll', 'PySide6/Qt6Gui.dll', 'PySide6/Qt6Widgets.dll',
                     'PySide6/QtCore.pyd', 'PySide6/plugins/platforms/qwindows.dll', 'PySide6/plugins/platforms/qoffscreen.dll'):
            self.assertTrue(retain_qt_artifact(name), name)
        for name in ('PySide6/Qt6VirtualKeyboard.dll', 'PySide6/Qt6Pdf.dll', 'PySide6/QtOpenGL.pyd',
                     'PySide6/Qt6Quick.dll', 'PySide6/plugins/imageformats/qpdf.dll',
                     'PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll',
                     'PySide6/qml/QtQuick/Controls/Basic/Button.qml'):
            self.assertFalse(retain_qt_artifact(name), name)

    def test_license_directory_keeps_spdx_named_and_exception_files(self):
        for name in ('package.dist-info/licenses/GPL-3.0-only.txt',
                     'package.dist-info/licenses/Qt-GPL-exception-1.0.txt',
                     'package/COPYING', 'package/LICENSE'):
            self.assertTrue(is_license_file(name), name)
        self.assertFalse(is_license_file('package/__init__.py'))
        self.assertFalse(is_license_file('packaging/licenses/__pycache__/_spdx.cpython-39.pyc'))
        self.assertFalse(is_license_file('packaging/licenses/__init__.py'))
    def test_explicit_assets_exclude_videos_history_and_experiments(self):
        self.assertEqual(sum(n.endswith('.pt') for n in ASSETS), 6)
        for name in ASSETS:
            self.assertTrue(name.startswith(('models/', 'configs/')))
            self.assertNotIn('rtdetr', name)

    def test_source_snapshot_excludes_user_data_and_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root, package = Path(folder).resolve() / 'source', Path(folder).resolve() / 'package'
            package.mkdir()
            for name in set(SOURCE_FILES + RUNTIME_FILES + GUIDES + ASSETS +
                            ('core/__init__.py', 'ui/__init__.py', 'tests/test_example.py',
                             'videos/private.mp4', 'outputs/history.json', '.cache/desktop-settings.json', '.env')):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('fixture', encoding='utf-8')
            with patch('core.packaging.metadata.distributions', return_value=[]):
                report = write_release_files(root, package, ASSETS)
            with zipfile.ZipFile(package / 'application-source.zip') as archive:
                names = archive.namelist()
                self.assertIn('FireGuard/core/__init__.py', names)
                self.assertIn('FireGuard/build_windows.py', names)
                self.assertIn('FireGuard/LICENSE', names)
                self.assertIn('FireGuard/NOTICE', names)
                self.assertTrue(all(not name.endswith('.pt') for name in names))
                for forbidden in ('private.mp4', 'history.json', 'desktop-settings.json', '.env'):
                    self.assertFalse(any(name.endswith(forbidden) for name in names))
            self.assertFalse((package / 'data').exists())
            self.assertFalse(report['clean_machine_validated'])
            self.assertEqual(report['available_models'], ['original', 'brightlight', 'modern', 'domestic', 'forest', 'uav'])
            saved = json.loads((package / 'release-manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(saved, report)

    def test_outside_input_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve() / 'root'
            root.mkdir()
            (root.parent / 'private.txt').write_text('private', encoding='utf-8')
            with self.assertRaises(ValueError):
                safe_file(root, '../private.txt')


if __name__ == '__main__':
    unittest.main()
