"""Explicit build input selection, dependency notices and private test manifest."""
from importlib import metadata
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

RELEASE_VERSION = '0.3.0-rc1'
QT_LIBRARIES = {'qt6core.dll', 'qt6gui.dll', 'qt6widgets.dll'}
QT_PLUGINS = {'platforms/qwindows.dll', 'platforms/qoffscreen.dll', 'platforms/qminimal.dll',
              'imageformats/qjpeg.dll', 'imageformats/qico.dll', 'imageformats/qgif.dll',
              'styles/qmodernwindowsstyle.dll'}


def retain_qt_artifact(destination):
    """Restrict the new build to the Widgets UI's actual Qt dependencies."""
    name = str(destination).replace('\\', '/').lower()
    if not name.startswith('pyside6/'):
        return True
    if '/qml/' in name or name.endswith('opengl32sw.dll'):
        return False
    if '/plugins/' in name:
        return name.split('/plugins/', 1)[1] in QT_PLUGINS
    leaf = name.rsplit('/', 1)[-1]
    if leaf.startswith('qt') and leaf.endswith('.pyd'):
        return leaf in {'qtcore.pyd', 'qtgui.pyd', 'qtwidgets.pyd'}
    if leaf.startswith('qt6') and leaf.endswith('.dll'):
        return leaf in QT_LIBRARIES
    return True


def is_license_file(entry):
    path = Path(str(entry))
    name = path.name.upper()
    if '__pycache__' in path.parts or path.suffix.lower() in ('.py', '.pyc', '.pyo', '.pyd', '.dll', '.so'):
        return False
    metadata_license_directory = (any(part.lower().endswith(('.dist-info', '.egg-info')) for part in path.parts)
                                  and any(part.lower() in ('licenses', 'licences') for part in path.parts))
    return (metadata_license_directory
            or name.startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'COPYRIGHT',
                                'QT_ATTRIBUTION', 'GPL-', 'LGPL-', 'AGPL-')))

from core.model_profiles import SCENE_MODEL_ASSETS, PROFILE_NAMES, profile_weight_specs

ASSETS = (
    'configs/temporal.json', 'configs/risk.json', 'models/best.pt', 'models/manifest.json',
    'models/candidates/brightlight-0da45ff9f6d2/best.pt',
    'models/candidates/brightlight-0da45ff9f6d2/source-manifest.json',
    'models/candidates/brightlight-0da45ff9f6d2/LICENSE',
    'models/candidates/brightlight-0da45ff9f6d2/README.md',
    'models/candidates/brightlight-0da45ff9f6d2/README.txt',
    'models/candidates/brightlight-0da45ff9f6d2/dataset.txt',
    'models/candidates/general26s-5397b43994c3/best.pt',
    'models/candidates/general26s-5397b43994c3/source-manifest.json',
    'models/candidates/general26s-5397b43994c3/README.md',
    'models/candidates/general26s-5397b43994c3/LICENSE-DECLARATION.md',
) + SCENE_MODEL_ASSETS
GUIDES = ('docs/PORTABLE_GUIDE.md', 'docs/DESKTOP_GUIDE.md', 'docs/LIVE_CAMERA_GUIDE.md',
          'docs/STORAGE_GUIDE.md', 'docs/THIRD_PARTY.md',
          'docs/SOURCE_ACCESS.md', 'docs/BUILD_FROM_SOURCE.md',
          'docs/SCENE_MODEL_SELECTION.md', 'docs/CONCISE_MONITORING_UI.md', 'docs/ALERT_DESIGN.md',
          'docs/EXISTING_MODEL_SCREEN_REPORT.md', 'docs/CLASS_ROUTING_VALIDATION_REPORT.md',
          'docs/MODEL_REPLACEMENT_DECISION.md', 'docs/MODERN_FIRE_MODEL_TRIAL.md')
GUIDES += ('docs/MONITOR_NAVIGATION_20261117.md', 'docs/COOPERATIVE_SCORE_20261117.md',
           'docs/MONITOR_MODELS_REOPEN_READABILITY_20261117.md', 'docs/PLAIN_RESULT_PRESENTATION_20261117.md',
           'docs/PACKAGE_QUICKSTART_030.md')
SOURCE_FILES = ('desktop.py', 'main.py', 'download_model.py', 'check_desktop.py',
                'build_windows.py', 'packaging/FireGuard.spec', 'packaging/requirements-cpu.txt',
                'packaging/fetch_cpu_wheels.py', 'packaging/fetch_scene_runtime.py', 'packaging/runtime_hook.py',
                'start_desktop.cmd', 'setup_desktop.ps1', 'requirements.txt', 'requirements-lock.txt',
                'LICENSE', 'NOTICE', 'README.md', 'packaging/collect_release_sources.py')
SOURCE_FILES += ('start_local_ai.ps1', 'start_monitor_center.cmd', 'build_local_ai.py',
                 'collect_scene_fusion.py', 'review_fusion_data.py', 'train_scene_fusion.py')


def safe_file(root, name):
    path = root / name
    if not path.is_file() or path.is_symlink() or path.resolve() != path or root not in path.parents:
        raise ValueError('Missing or linked build input: ' + str(name))
    # Reject linked intermediate directories too, including Windows junctions.
    for parent in path.parents:
        if parent == root:
            break
        if parent.resolve() != parent or parent.is_symlink():
            raise ValueError('Linked build input directory: ' + str(name))
    return path


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def bundle_assets(root):
    root = Path(root).resolve()
    expected = dict(spec for profile in PROFILE_NAMES for spec in profile_weight_specs(profile))
    for name in ASSETS:
        path = safe_file(root, name)
        if name in expected and digest(path) != expected[name]:
            raise ValueError('Model SHA256 mismatch: ' + name)
    return ASSETS


def application_sources(root, assets):
    """One allowlist for packaged source and independent rebuild staging."""
    from core.transfer import RUNTIME_FILES
    root = Path(root).resolve()
    names = list(SOURCE_FILES) + list(RUNTIME_FILES) + list(GUIDES)
    names += [name for name in assets if not name.endswith('.pt')]
    for folder, pattern in (('core', '*.py'), ('ui', '*.py'), ('tests', '*.py'), ('licenses', '*')):
        paths = (root / folder).rglob(pattern) if folder == 'licenses' else (root / folder).glob(pattern)
        names.extend(p.relative_to(root).as_posix() for p in paths if p.is_file())
    for name in ('evidence_accumulator', 'screen_hf_fire_candidates', 'benchmark_firesense',
                 'screen_brightlight_candidate', 'replay_brightlight_routing',
                 'probe_class_routing', 'freeze_firesense_plan'):
        relative = 'experiments/' + name + '.py'
        if (root / relative).is_file():
            names.append(relative)
    # Include transitive project helper imports needed by the shipped tests.
    pending = [n for n in names if n.startswith('experiments/') and n.endswith('.py')]
    visited = set()
    while pending:
        name = pending.pop()
        if name in visited:
            continue
        visited.add(name)
        tree = ast.parse(safe_file(root, name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            modules = ([node.module] if isinstance(node, ast.ImportFrom) and node.module else
                       [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            for module in modules:
                if module.startswith('experiments.'):
                    relative = module.replace('.', '/') + '.py'
                    safe_file(root, relative)
                    names.append(relative)
                    pending.append(relative)
    return tuple(sorted(set(names)))


def write_release_files(root, package, assets, *, refresh=False):
    root, package = Path(root).resolve(), Path(package).resolve()
    (package / 'portable.flag').write_text('Store generated records in ./data.\n', encoding='utf-8')
    for name in GUIDES:
        source = safe_file(root, name)
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    extra_sources = []
    for name in ('LICENSE', 'NOTICE'):
        shutil.copyfile(safe_file(root, name), package / name)
    for path in sorted((root / 'licenses').rglob('*')) if (root / 'licenses').exists() else ():
        if path.is_file():
            name = path.relative_to(root).as_posix()
            target = package / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(safe_file(root, name), target)
            extra_sources.append(name)
    for name in ('SOURCE_ACCESS.md', 'BUILD_FROM_SOURCE.md'):
        shutil.copyfile(safe_file(root, 'docs/' + name), package / name)
    (package / '开始使用.txt').write_text(
        '焰鉴 Windows CPU 交付候选版 ' + RELEASE_VERSION + '\n\n'
        '1. 完整解压到当前用户可写目录，例如 D:\\FireGuard。不要直接在 ZIP 内运行。\n'
        '2. 双击 FireGuard.exe。首次启动请等待；无需安装 Python。\n'
        '3. 首页点击“添加监控点”，选择本地录像或摄像头；支持多路管理和每路模型选择。\n'
        '4. 记录和设置保存在本目录的 data 中。重要记录请备份；删除整包也会删除 data。\n'
        '5. data\\outputs 是监控记录；存储管理默认不自动删除。\n'
        '6. 不要单独移动 EXE，也不要删除 _internal 或 portable.flag。\n\n'
        '支持目标：Windows 10/11 x64；仍需另一台电脑和安卓摄像头实际验收。\n'
        '这是 CPU 兼容测试包，不能使用 CUDA；实时速度以本机实测为准。\n'
        '包含通用、家庭室内、林地远景、无人机巡林及两套高级对照方案；初始默认通用模型。\n'
        '场景画面理解使用独立AI组件。将AI组件ZIP解压到本目录，双击“启动完整版.cmd”。\n'
        '主程序烟火检测使用CPU；本地AI组件按NVIDIA显卡环境验证，首次加载需要片刻。\n'
        '各方案的适用画面、主要优点和易错情况请查看模型介绍；本包未复制开发机个人设置。\n'
        '未包含私人视频、训练标注、历史记录、开发环境或 Git 信息。\n'
        '本应用采用 AGPL-3.0-only，第三方保留各自许可。源码与取得方式见 SOURCE_ACCESS.md。\n'
        '第三方文本见 third-party-notices 和 licenses；对应源码随 corresponding-sources 提供。\n'
        '软件为视觉辅助核查工具，非认证消防报警设备。\n', encoding='utf-8')
    shutil.copyfile(safe_file(root, 'start_local_ai.ps1'), package / 'start_local_ai.ps1')
    if (package / '_internal').is_dir():
        shutil.copyfile(safe_file(root, 'start_local_ai.ps1'), package / '_internal/start_local_ai.ps1')
    (package / '启动完整版.cmd').write_text('@echo off\r\nsetlocal\r\ncd /d "%~dp0"\r\n'
        'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_local_ai.ps1"\r\n'
        'if errorlevel 1 echo Local AI is not ready. Check data\\outputs\\local-ai.\r\n'
        'start "" "%~dp0FireGuard.exe"\r\n', encoding='ascii')
    (package / '检查软件.cmd').write_text('@echo off\r\ncd /d "%~dp0"\r\n'
        'start "" /wait "%~dp0FireGuard.exe" --self-test "%~dp0self-test-report.json"\r\n'
        'echo Report: "%~dp0self-test-report.json"\r\npause\r\n', encoding='ascii')
    notices = package / 'third-party-notices'
    inventory = []
    for dist in sorted(metadata.distributions(), key=lambda d: d.metadata.get('Name', '').lower()):
        name, version = dist.metadata.get('Name', 'unknown'), dist.version
        item = {'name': name, 'version': version, 'license': dist.metadata.get('License-Expression') or dist.metadata.get('License', ''),
                'homepage': dist.metadata.get('Home-page', ''), 'notice_files': []}
        for entry in dist.files or ():
            if not is_license_file(entry):
                continue
            source = Path(dist.locate_file(entry))
            if source.is_file():
                relative = Path(str(entry))
                if '..' in relative.parts or relative.is_absolute():
                    continue
                target = notices / (name + '-' + version) / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                item['notice_files'].append(target.relative_to(package).as_posix())
        if name.lower() == 'nvidia-ml-py' and not item['notice_files']:
            supplement = package / 'licenses/nvidia-ml-py-13.615.71-copyright.txt'
            if supplement.is_file():
                item['notice_files'].append(supplement.relative_to(package).as_posix())
        if name.lower().startswith(('pyside6', 'shiboken6')):
            item['supplemental_notice_files'] = [n for n in (
                'licenses/GPL-2.0-only.txt', 'licenses/GPL-3.0-only.txt', 'licenses/LGPL-3.0-only.txt')
                if (package / n).is_file()]
        inventory.append(item)
    notices.mkdir(exist_ok=True)
    (notices / 'dependency-inventory.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding='utf-8')
    # Source snapshot contains application/build code only; no user data/settings.
    with zipfile.ZipFile(package / 'application-source.zip', 'w' if refresh else 'x', zipfile.ZIP_DEFLATED) as archive:
        for name in application_sources(root, assets):
            archive.write(safe_file(root, name), 'FireGuard/' + name)
    # Source archives accompany the executable, rather than a future source offer.
    source_root = Path(os.environ.get('FIREGUARD_CORRESPONDING_SOURCE_ROOT',
                                     str(root / '.cache/release-compliance'))).resolve()
    corresponding = source_root / 'source-manifest.json'
    if corresponding.is_file():
        entries = json.loads(corresponding.read_text(encoding='utf-8'))
        if not all(entry.get('ok') for entry in entries):
            raise ValueError('Corresponding source archives are incomplete.')
        for entry in entries:
            source = safe_file(source_root / 'sources', entry['file'])
            if digest(source) != entry['sha256']:
                raise ValueError('Corresponding source archive hash mismatch: ' + entry['file'])
            target = package / 'corresponding-sources' / entry['file']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        (package / 'corresponding-sources/source-manifest.json').write_text(
            json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
    files = [{'path': p.relative_to(package).as_posix(), 'bytes': p.stat().st_size, 'sha256': digest(p)}
             for p in sorted(package.rglob('*')) if p.is_file() and p != package / 'release-manifest.json']
    manifest = {'schema_version': 1, 'version': RELEASE_VERSION, 'kind': 'windows_x64_portable_cpu_release_candidate',
                'clean_machine_validated': False, 'public_distribution_review_complete': False,
                'application_license': 'AGPL-3.0-only', 'corresponding_sources_bundled': corresponding.is_file(),
                'default_model': 'original',
                'available_models': [profile for profile in PROFILE_NAMES
                                     if all(relative in assets for relative, _ in profile_weight_specs(profile))],
                'data_directory': './data', 'model_assets': list(assets), 'files': files}
    (package / 'release-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    return manifest
