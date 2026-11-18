# Read-only assets are explicitly selected; never collect the workspace tree.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from core.packaging import bundle_assets, retain_qt_artifact

root = Path(os.environ['FIREGUARD_BUILD_ROOT'])
datas = [(str(root / name), str(Path(name).parent)) for name in bundle_assets(root)]
datas += collect_data_files('ultralytics', includes=['cfg/**/*.yaml'])
datas += [(str(root / 'start_local_ai.ps1'), '.')]
for name in ('ultralytics', 'torch', 'torchvision', 'numpy', 'opencv-python',
             'PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'dill'):
    datas += copy_metadata(name)
# Pickled official Ultralytics checkpoints and runtime-selectable model adapters.
hidden = collect_submodules('ultralytics.nn') + [
    'ultralytics.models.yolo.detect.predict', 'core.diagnostics', 'core.temporal', 'core.risk',
    'core.package_check', 'torchvision', 'backports.tarfile', 'dill',
    'PySide6.QtWidgets', 'PySide6.QtGui', 'PySide6.QtCore']
a = Analysis([str(root / 'desktop.py')], pathex=[str(root)], datas=datas,
             hiddenimports=hidden, hooksconfig={'matplotlib': {'backends': ['Agg']}},
             runtime_hooks=[str(root / 'packaging/runtime_hook.py')],
             excludes=['tkinter', 'PyQt5', 'PyQt6', 'PySide2',
             'IPython', 'pytest', 'tensorboard', 'tensorflow', 'triton'], noarchive=False)
# Remove plugin-driven optional Qt modules before creating a NEW distribution.
# The existing acceptance package is never edited in place.
a.binaries = [entry for entry in a.binaries if retain_qt_artifact(entry[0])]
a.datas = [entry for entry in a.datas if retain_qt_artifact(entry[0])]
# Verify the retained Qt binaries do not import a Qt DLL we filtered out.
import pefile
qt_names = {Path(entry[0]).name.lower() for entry in a.binaries if entry[0].replace('\\', '/').startswith('PySide6/')}
for destination, source, kind in a.binaries:
    if destination.replace('\\', '/').startswith('PySide6/') and str(source).lower().endswith(('.dll', '.pyd')):
        image = pefile.PE(source, fast_load=True)
        image.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
        for dependency in getattr(image, 'DIRECTORY_ENTRY_IMPORT', ()):
            name = dependency.dll.decode('ascii').lower()
            if name.startswith('qt6') and name.endswith('.dll') and name not in qt_names:
                raise ValueError('Retained Qt binary requires excluded library: ' + destination + ' -> ' + name)
        image.close()
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='FireGuard',
          debug=bool(os.environ.get('FIREGUARD_BUILD_CONSOLE')), bootloader_ignore_signals=False, strip=False, upx=False,
          console=bool(os.environ.get('FIREGUARD_BUILD_CONSOLE')), disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='FireGuard')
