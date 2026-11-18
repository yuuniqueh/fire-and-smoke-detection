"""Initialize writable portable paths before third-party PyInstaller hooks."""
import os
import sys
import tempfile
import traceback

from core.runtime import ROOT, configure_runtime

configure_runtime()
temporary = ROOT / '.cache/temp'
temporary.mkdir(parents=True, exist_ok=True)
for key in ('TEMP', 'TMP', 'TMPDIR'):
    os.environ[key] = str(temporary)
tempfile.tempdir = str(temporary)


def startup_exception(kind, value, tb):
    path = ROOT / 'outputs/desktop-startup-error.txt'
    path.write_text(''.join(traceback.format_exception(kind, value, tb)), encoding='utf-8')
    sys.__excepthook__(kind, value, tb)


sys.excepthook = startup_exception
