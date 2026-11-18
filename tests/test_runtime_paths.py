"""Bundled assets must remain separate from user-owned records."""
from pathlib import Path
import tempfile
import unittest

from core.runtime import runtime_paths


class RuntimePathsTests(unittest.TestCase):
    def test_source_run_preserves_project_directories(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            self.assertEqual(runtime_paths(frozen=False, assets=root,
                             executable=root / 'python.exe', environ={}), (root, root))

    def test_installed_app_uses_per_user_data_outside_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            assets = root / 'install/_internal'
            result = runtime_paths(frozen=True, assets=assets,
                                   executable=root / 'install/FireGuard.exe',
                                   environ={'LOCALAPPDATA': str(root / 'user')})
            self.assertEqual(result, (assets, root / 'user/FireGuard'))

    def test_portable_marker_and_explicit_override(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / 'portable.flag').touch()
            args = dict(frozen=True, assets=root / '_internal', executable=root / 'FireGuard.exe')
            self.assertEqual(runtime_paths(**args, environ={})[1], root / 'data')
            self.assertEqual(runtime_paths(**args, environ={'FIREGUARD_DATA_DIR': str(root / 'other')})[1], root / 'other')

    def test_relative_override_is_rejected(self):
        with self.assertRaises(ValueError):
            runtime_paths(frozen=True, assets='.', executable='FireGuard.exe',
                          environ={'FIREGUARD_DATA_DIR': 'relative/data'})


if __name__ == '__main__':
    unittest.main()
