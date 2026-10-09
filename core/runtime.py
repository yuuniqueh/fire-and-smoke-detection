"""Keep generated files in the project, not the system drive."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configure_runtime():
    sys.dont_write_bytecode = True
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
    for folder in ('models', 'videos', 'outputs'):
        (ROOT / folder).mkdir(exist_ok=True)

