"""Explicit local handoff bundle; never walk personal data or runtime caches."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import zipfile

from core.model_profiles import SCENE_MODEL_ASSETS


RUNTIME_FILES = ('desktop.py', 'main.py', 'download_model.py', 'check_desktop.py', 'build_transfer.py', 'start_desktop.cmd',
                 'check_environment.cmd', 'setup_desktop.ps1', 'requirements.txt', 'requirements-lock.txt',
                 'requirements-candidates.txt', 'configs/temporal.json', 'configs/risk.json',
                 'docs/DESKTOP_GUIDE.md', 'docs/PRODUCT_FRAMEWORK.md', 'docs/EVENT_REVIEW_GUIDE.md',
                 'docs/CONFIRMATION_SPEED_REPORT.md', 'docs/MODERN_FIRE_MODEL_TRIAL.md',
                 'experiments/fetch_modern_fire_candidates.py',
                 'docs/SCENE_MODEL_SELECTION.md', 'docs/CONCISE_MONITORING_UI.md',
                 'docs/SETTINGS_AND_TRANSFER_GUIDE.md', 'docs/LIVE_CAMERA_GUIDE.md',
                 'docs/LIVE_CAMERA_TEST_REPORT.md', 'docs/STORAGE_GUIDE.md', 'docs/STORAGE_TEST_REPORT.md',
                 'docs/THIRD_PARTY.md', 'models/manifest.json')
MODEL_FILES = ('models/best.pt', 'models/candidates/brightlight-0da45ff9f6d2/best.pt',
               'models/candidates/brightlight-0da45ff9f6d2/source-manifest.json',
               'models/candidates/brightlight-0da45ff9f6d2/LICENSE',
               'models/candidates/brightlight-0da45ff9f6d2/README.md',
               'models/candidates/brightlight-0da45ff9f6d2/dataset.txt') + SCENE_MODEL_ASSETS


def build_transfer(root, destination, include_models=False):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if destination.suffix.lower() != '.zip':
        raise ValueError('迁移包目标必须是 .zip，不能覆盖模型、配置或其他文件。')
    files = []
    def include(relative, required=True):
        path = root / relative
        if not path.is_file():
            if required:
                raise ValueError(f'迁移所需文件缺失：{relative}')
            return
        if path.is_symlink() or root not in path.resolve().parents:
            raise ValueError(f'迁移文件不能是外部链接：{relative}')
        files.append(path)
    for name in RUNTIME_FILES:
        include(name)
    for folder in ('core', 'ui'):
        if (root / folder).is_symlink():
            raise ValueError(f'运行代码目录不能是链接：{folder}')
        for path in sorted((root / folder).glob('*.py')):
            include(path.relative_to(root))
    if include_models:
        from core.model_profiles import CANDIDATE_SHA256, ORIGINAL_SHA256, verify_weight
        verify_weight(root / 'models/best.pt', ORIGINAL_SHA256)
        candidate = root / 'models/candidates/brightlight-0da45ff9f6d2/best.pt'
        if candidate.is_file():
            verify_weight(candidate, CANDIDATE_SHA256)
        from core.model_profiles import SCENE_WEIGHTS
        for spec in SCENE_WEIGHTS.values():
            path = root / spec['path']
            if path.is_file():
                verify_weight(path, spec['sha256'])
        for name in MODEL_FILES:
            include(name, required=name == 'models/best.pt')
    if destination in files:
        raise ValueError('导出目标不能覆盖迁移源文件。')
    manifest = {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'kind': 'local_source_handoff', 'contains_models': bool(include_models),
                'contains_python_environment': False,
                'note': '本地源码迁移包，非安装程序；未包含私人视频、标注、历史结果、缓存、Git 或本机设置。',
                'files': []}
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='fireguard-transfer-', suffix='.tmp', dir=destination.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in sorted(files):
                data = path.read_bytes()
                name = path.relative_to(root).as_posix()
                archive.writestr('FireGuard/' + name, data)
                manifest['files'].append({'path': name, 'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            archive.writestr('FireGuard/transfer-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            archive.writestr('FireGuard/开始使用.txt',
                             '焰鉴本地源码迁移包\n\n先阅读 docs/SETTINGS_AND_TRANSFER_GUIDE.md。\n'
                             '已有 PyTorch：使用其 python.exe 执行 setup_desktop.ps1 -TorchMode reuse。\n'
                             '新环境：可显式选择 -TorchMode cpu 或 cu128，安装过程需要联网。\n'
                             '安装成功后双击 check_environment.cmd，再运行 start_desktop.cmd。\n'
                             '不要复制旧 .venv 或 .venv-pytorch；不要覆盖原电脑已有环境。\n'
                             '如未包含权重，另行复制已登记 models/best.pt。测试视频由用户自行选择合法素材。\n'
                             '这是本地开发迁移包，未经独立新电脑验收，不是安装包或已审核的公开分发包。\n')
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return manifest
