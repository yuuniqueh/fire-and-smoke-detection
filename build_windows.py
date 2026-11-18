"""Build a private Windows portable test package in a fresh output directory."""
import argparse
from datetime import datetime
from importlib import metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

from core.packaging import RELEASE_VERSION, bundle_assets, write_release_files
from core.runtime import configure_runtime


def main():
    configure_runtime()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--workpath', type=Path, help='Optional reusable build cache; distribution output must still be new.')
    parser.add_argument('--finish-existing', action='store_true', help='Finish an interrupted, not-yet-archived build.')
    parser.add_argument('--corresponding-sources', type=Path,
                        help='Directory containing source-manifest.json and sources/.')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if sys.platform != 'win32':
        raise RuntimeError('Build on Windows with the dedicated CPU environment.')
    if metadata.version('torch') != '2.8.0+cpu':
        raise RuntimeError('Use .venv-package-cpu: this build deliberately excludes CUDA libraries.')
    assets = bundle_assets(root)  # Includes fixed SHA256 checks before building.
    destination = (args.output or root / 'outputs' / ('portable-' + datetime.now().strftime('%Y%m%d-%H%M%S'))).resolve()
    if destination.exists() and not args.finish_existing:
        raise ValueError('Use a new output directory; existing packages are never overwritten.')
    if args.finish_existing:
        if (not (destination / 'dist/FireGuard/FireGuard.exe').is_file()
                or not (destination / 'build.log').is_file()
                or list(destination.glob('*.zip'))):
            raise ValueError('Only a built, not-yet-archived staging directory can be resumed.')
    else:
        destination.mkdir(parents=True)
    os.environ['FIREGUARD_BUILD_ROOT'] = str(root)
    if args.corresponding_sources:
        os.environ['FIREGUARD_CORRESPONDING_SOURCE_ROOT'] = str(args.corresponding_sources.resolve())
    log_path = destination / 'build.log'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm',
               '--distpath', str(destination / 'dist'), '--workpath', str(args.workpath.resolve() if args.workpath else destination / 'work'),
               str(root / 'packaging/FireGuard.spec')]
    if not args.finish_existing:
        with log_path.open('w', encoding='utf-8') as log:
            result = subprocess.run(command, cwd=root, stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise RuntimeError('PyInstaller failed; see ' + str(log_path))
    package = destination / 'dist/FireGuard'
    manifest = write_release_files(root, package, assets, refresh=args.finish_existing)
    archive_path = destination / f'FireGuard-Windows-CPU-{RELEASE_VERSION}.zip'
    with zipfile.ZipFile(archive_path, 'x', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in sorted(package.rglob('*')):
            if path.is_file():
                archive.write(path, 'FireGuard/' + path.relative_to(package).as_posix())
    print(json.dumps({'directory': str(package), 'zip': str(archive_path),
                      'archive_bytes': archive_path.stat().st_size,
                      'package_bytes': sum(p.stat().st_size for p in package.rglob('*') if p.is_file()),
                      'manifest_files': len(manifest['files']), 'scope': 'Local portable test; clean-PC acceptance pending.'},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
