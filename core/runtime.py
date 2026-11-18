"""Separate bundled read-only assets from writable monitoring data."""
import os
import sys
from pathlib import Path

def runtime_paths(*, frozen, assets, executable, environ):
    assets = Path(assets).resolve()
    if not frozen:
        return assets, assets
    override = environ.get('FIREGUARD_DATA_DIR')
    if override:
        data = Path(override)
        if not data.is_absolute():
            raise ValueError('FIREGUARD_DATA_DIR must be an absolute path.')
    elif (Path(executable).resolve().parent / 'portable.flag').is_file():
        data = Path(executable).resolve().parent / 'data'
    else:
        data = Path(environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'FireGuard'
    return assets, data.resolve()


FROZEN = bool(getattr(sys, 'frozen', False))
ASSET_ROOT, ROOT = runtime_paths(
    frozen=FROZEN, assets=getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]),
    executable=sys.executable, environ=os.environ)


def configure_runtime():
    sys.dont_write_bytecode = True
    ROOT.mkdir(parents=True, exist_ok=True)
    # Windowed executables have no console streams. Libraries may still write
    # progress messages; startup failures are saved explicitly by desktop.py.
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    locations = {
        'YOLO_CONFIG_DIR': ROOT / '.cache' / 'ultralytics',
        'HF_HOME': ROOT / '.cache' / 'huggingface',
        'TORCH_HOME': ROOT / '.cache' / 'torch',
        'MPLCONFIGDIR': ROOT / '.cache' / 'matplotlib',
    }
    for key, path in locations.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(path)
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
    os.environ['YOLO_OFFLINE'] = 'true'
    os.environ['YOLO_AUTOINSTALL'] = 'false'
    for folder in ('videos', 'outputs') + (() if FROZEN else ('models',)):
        (ROOT / folder).mkdir(exist_ok=True)

