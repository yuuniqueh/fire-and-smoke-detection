"""Build a relocatable, isolated local-AI component from verified installed files."""
import argparse
from datetime import datetime
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import zipfile

ROOT = Path(__file__).resolve().parent
MODEL_FILES = ('chat_template.json', 'config.json', 'generation_config.json', 'merges.txt',
               'model.safetensors', 'preprocessor_config.json', 'README.md', 'tokenizer_config.json',
               'tokenizer.json', 'video_preprocessor_config.json', 'vocab.json')
SERVICE_FILES = ('__init__.py', 'local_scene_server.py', 'scene_protocol.py', 'scene_review.py')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 ** 2), b''):
            result.update(block)
    return result.hexdigest()


def normalized(name):
    return name.lower().replace('_', '-').replace('.', '-')


def keep_dependency_file(relative):
    path = PurePosixPath(str(relative).replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or not path.parts:
        return False
    if '__pycache__' in path.parts or path.name == 'direct_url.json' or path.suffix.lower() in ('.pyc', '.pyo', '.pdb', '.lib', '.pth'):
        return False
    if path.parts[:2] == ('torch', 'include') or path.parts[:2] == ('torch', 'test'):
        return False
    if path.name.startswith('libbitsandbytes_cuda') and path.name != 'libbitsandbytes_cuda128.dll':
        return False
    return True


def distributions(receipt):
    from packaging.requirements import Requirement
    from packaging.markers import default_environment
    local = Path(sys.prefix) / 'Lib/site-packages'
    cuda = Path(receipt['torch_site'])
    candidates = {}
    for site in (cuda, local):
        for dist in metadata.distributions(path=[str(site)]):
            if dist.metadata.get('Name'):
                candidates[normalized(dist.metadata['Name'])] = dist
    # Qwen's environment intentionally uses the host's tested CUDA torch build.
    for dist in metadata.distributions(path=[str(cuda)]):
        if normalized(dist.metadata.get('Name', '')) in ('torch', 'torchvision'):
            candidates[normalized(dist.metadata['Name'])] = dist
    pending = ['torch', 'torchvision', 'transformers', 'accelerate', 'bitsandbytes', 'numpy', 'pillow']
    chosen = {}
    environment = {**default_environment(), 'extra': ''}
    while pending:
        name = normalized(pending.pop())
        if name in chosen:
            continue
        dist = candidates.get(name)
        if dist is None:
            raise RuntimeError('Missing local AI dependency: ' + name)
        chosen[name] = dist
        for value in dist.requires or ():
            requirement = Requirement(value)
            if requirement.marker and not requirement.marker.evaluate(environment):
                continue
            other = candidates.get(normalized(requirement.name))
            if other is None or other.version not in requirement.specifier:
                raise RuntimeError('Unsatisfied local AI dependency: ' + value)
            pending.append(requirement.name)
    return chosen


def copy_python(runtime):
    base = Path(sys.base_prefix)
    for source in base.iterdir():
        if source.is_file() and (source.name in ('python.exe', 'pythonw.exe', 'LICENSE_PYTHON.txt')
                                 or source.suffix.lower() == '.dll'):
            shutil.copy2(source, runtime / source.name)
    dlls = runtime / 'DLLs'
    dlls.mkdir()
    native_sources = []
    for source in (base / 'DLLs').glob('*.pyd'):
        if source.name.startswith('_test') or source.name in ('_ctypes_test.pyd', '_tkinter.pyd'):
            continue
        shutil.copy2(source, dlls / source.name)
        native_sources.append(source)
    # Copy exactly the non-system DLL dependencies of stdlib extension modules.
    if str(base / 'Lib/site-packages') not in sys.path:
        sys.path.append(str(base / 'Lib/site-packages'))
    try:
        import pefile
    except ImportError:
        # Build-time reader from the separate packaging environment; it is not
        # a dependency of the delivered local-AI component.
        sys.path.append(str(ROOT / '.venv-package-cpu/Lib/site-packages'))
        import pefile
    available = {p.name.lower(): p for folder in (base, base / 'DLLs', base / 'Library/bin')
                 for p in folder.glob('*.dll')}
    seen = set()
    while native_sources:
        source = native_sources.pop()
        if source.name.lower() in seen:
            continue
        seen.add(source.name.lower())
        with pefile.PE(str(source), fast_load=True) as binary:
            binary.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT']])
            for entry in getattr(binary, 'DIRECTORY_ENTRY_IMPORT', ()):
                dependency = available.get(entry.dll.decode('ascii').lower())
                if dependency:
                    target = dlls / dependency.name
                    if not target.exists() and not (runtime / dependency.name).exists():
                        shutil.copy2(dependency, target)
                    native_sources.append(dependency)
    with zipfile.ZipFile(runtime / 'python39.zip', 'x', zipfile.ZIP_DEFLATED) as archive:
        for source in (base / 'Lib').rglob('*.py'):
            relative = source.relative_to(base / 'Lib')
            if any(part in ('site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip')
                   for part in relative.parts):
                continue
            archive.write(source, relative.as_posix())
    (runtime / 'python39._pth').write_text('python39.zip\nDLLs\nLib/site-packages\n../service\nimport site\n', encoding='ascii')


def archive_component(output, *, split_model=False, destination=None):
    from core.packaging import RELEASE_VERSION
    component = output / 'dist/ai-component'
    manifest = json.loads((component / 'component-manifest.json').read_text(encoding='utf-8'))
    destination = (destination or output).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    groups = [('AI-Runtime-NVIDIA', False), ('AI-Model-Qwen', True)] if split_model else [('Local-AI-NVIDIA', None)]
    for caption, model_only in groups:
        archive_path = destination / f'FireGuard-{caption}-{RELEASE_VERSION}.zip'
        with zipfile.ZipFile(archive_path, 'x', zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            for entry in manifest['files']:
                if model_only is not None and entry['path'].startswith('model/') != model_only:
                    continue
                relative = PurePosixPath(entry['path'])
                path = component / relative
                if relative.is_absolute() or '..' in relative.parts or not keep_dependency_file(relative):
                    raise ValueError('Unsafe AI archive entry: ' + str(relative))
                if path.stat().st_size != entry['bytes'] or digest(path) != entry['sha256']:
                    raise ValueError('Changed AI archive input: ' + str(relative))
                archive.write(path, 'ai-component/' + relative.as_posix())
            if model_only is not True:
                archive.write(component / 'component-manifest.json', 'ai-component/component-manifest.json')
        (archive_path.with_suffix('.zip.sha256')).write_text(digest(archive_path) + '  ' + archive_path.name + '\n', encoding='ascii')
        print(json.dumps({'zip': str(archive_path), 'bytes': archive_path.stat().st_size}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--no-archive', action='store_true')
    parser.add_argument('--archive-only', action='store_true')
    parser.add_argument('--refresh-service', action='store_true', help='Refresh only application service sources in a staging component.')
    parser.add_argument('--split-model', action='store_true', help='Produce ordinary runtime/model ZIPs, each usable on FAT32 media.')
    parser.add_argument('--archive-destination', type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    if args.refresh_service:
        component = output / 'dist/ai-component'
        path = component / 'component-manifest.json'
        manifest = json.loads(path.read_text(encoding='utf-8'))
        for name in SERVICE_FILES:
            source = ROOT / 'core' / name
            relative = 'service/core/' + name
            shutil.copy2(source, component / relative)
            entry = next((item for item in manifest['files'] if item['path'] == relative), None)
            if entry is None:
                entry = {'path': relative}
                manifest['files'].append(entry)
            entry.update(bytes=source.stat().st_size, sha256=digest(source))
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Refreshed staged service sources', flush=True)
        return
    if args.archive_only:
        archive_component(output, split_model=args.split_model, destination=args.archive_destination)
        return
    if output.exists():
        raise ValueError('Use a new AI component directory; never overwrite a delivery.')
    receipt = json.loads((ROOT / '.cache/qwen-local-runtime.json').read_text(encoding='utf-8'))
    checkpoint = Path(receipt['path']) / 'model.safetensors'
    if checkpoint.stat().st_size != receipt['weights_bytes'] or digest(checkpoint) != receipt['weights_sha256']:
        raise ValueError('Local Qwen checkpoint does not match the verified receipt')
    chosen = distributions(receipt)
    component = output / 'dist/ai-component'
    runtime = component / 'runtime'
    site = runtime / 'Lib/site-packages'
    site.mkdir(parents=True)
    copy_python(runtime)
    inventory = []
    for name, dist in sorted(chosen.items()):
        count = 0
        for relative in dist.files or ():
            if not keep_dependency_file(relative):
                continue
            source = Path(dist.locate_file(relative))
            if not source.is_file() or source.is_symlink():
                continue
            target = site / str(relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            count += 1
        inventory.append({'name': dist.metadata['Name'], 'version': dist.version, 'files': count})
        print('Copied ' + name + ' ' + dist.version, flush=True)
    service = component / 'service/core'
    service.mkdir(parents=True)
    for name in SERVICE_FILES:
        shutil.copy2(ROOT / 'core' / name, service / name)
    model = component / 'model'
    model.mkdir()
    for name in MODEL_FILES:
        shutil.copy2(Path(receipt['path']) / name, model / name)
    # The Qwen model card declares Apache-2.0; include the actual license text.
    apache = next((p for p in (site / 'transformers-4.57.1.dist-info').rglob('*')
                   if p.is_file() and p.name.upper().startswith('LICENSE')), None)
    if apache:
        shutil.copy2(apache, model / 'LICENSE')
    config = {key: receipt[key] for key in ('provider', 'model', 'revision', 'weights_sha256', 'weights_bytes')}
    config.update(path='model', language_precision='nf4', endpoint='http://127.0.0.1:11435')
    (component / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    (component / 'dependency-inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    (component / '开始使用.txt').write_text('焰鉴本地AI组件\n\n将整个ai-component目录放在FireGuard.exe旁边，运行“启动完整版.cmd”。\n'
        '无需安装Python或联网下载模型。按本机NVIDIA 8GB显存环境验证，CPU主程序单独运行。\n'
        '不要拆开runtime、service和model；不要覆盖个人data目录。\n'
        '日志位于主程序data/outputs/local-ai。模型与依赖保留各自许可，应用服务代码采用AGPL-3.0-only。\n', encoding='utf-8')
    shutil.copy2(ROOT / 'LICENSE', component / 'LICENSE-APPLICATION')
    files = [{'path': p.relative_to(component).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
             for p in sorted(component.rglob('*')) if p.is_file()]
    manifest = {'schema_version': 1, 'built_at': datetime.now().isoformat(timespec='seconds'),
                'kind': 'windows_x64_local_ai_nvidia', 'isolated_python': True,
                'model': config, 'dependencies': inventory, 'files': files,
                'scope': 'Relocatable component; acceptance results are recorded separately.'}
    (component / 'component-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'component': str(component), 'files': len(files), 'bytes': sum(f['bytes'] for f in files)}, ensure_ascii=False), flush=True)
    if not args.no_archive:
        archive_component(output, split_model=args.split_model, destination=args.archive_destination)


if __name__ == '__main__':
    main()
