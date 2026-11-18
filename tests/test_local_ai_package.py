"""Portable AI configuration and explicit payload exclusion."""
import json
from pathlib import Path
import tempfile
import unittest

from build_local_ai import keep_dependency_file, MODEL_FILES, SERVICE_FILES
from core.local_scene_server import load_runtime_config


class LocalAiPackageTests(unittest.TestCase):
    def test_relative_model_path_follows_relocated_component(self):
        with tempfile.TemporaryDirectory(prefix='焰鉴 AI ') as directory:
            folder = Path(directory).resolve()
            config = folder / 'config.json'
            config.write_text(json.dumps({'path': 'model', 'language_precision': 'nf4'}), encoding='utf-8')
            loaded = load_runtime_config(config)
            self.assertEqual(Path(loaded['path']), folder / 'model')
            self.assertNotIn('torch_site', loaded)

    def test_existing_absolute_source_configuration_remains_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory).resolve()
            value = {'path': str(folder / 'model'), 'torch_site': str(folder / 'site')}
            config = folder / 'config.json'
            config.write_text(json.dumps(value), encoding='utf-8')
            self.assertEqual(load_runtime_config(config), value)

    def test_payload_excludes_paths_caches_build_libraries_and_other_cuda_variants(self):
        for name in ('../private.txt', '../../Scripts/run.exe', '/secret', 'torch/__pycache__/a.pyc',
                     'torch/include/a.h', 'torch/lib/torch.lib', 'torch/torch.pdb', 'development.pth',
                     'package.dist-info/direct_url.json', 'bitsandbytes/libbitsandbytes_cuda130.dll'):
            self.assertFalse(keep_dependency_file(name), name)
        for name in ('torch/lib/torch_cuda.dll', 'torch/__init__.py', 'numpy.libs/openblas.dll',
                     'package.dist-info/licenses/LICENSE', 'bitsandbytes/libbitsandbytes_cuda128.dll'):
            self.assertTrue(keep_dependency_file(name), name)
        self.assertIn('model.safetensors', MODEL_FILES)
        self.assertNotIn('model.download-partial', MODEL_FILES)
        self.assertIn('scene_review.py', SERVICE_FILES)


if __name__ == '__main__':
    unittest.main()
