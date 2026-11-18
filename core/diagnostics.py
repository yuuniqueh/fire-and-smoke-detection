"""Local environment checks; no downloads, weight deserialization or inference."""
import hashlib
import importlib
from importlib import metadata
import json
from pathlib import Path
import platform
import sys
import tempfile

from core.model_profiles import profile_weight_specs
from core.runtime import ASSET_ROOT, ROOT


def collect_diagnostics(root=None, device='auto', profile='original',
                        module_loader=importlib.import_module, version_reader=metadata.version, progress=None):
    assets = ASSET_ROOT if root is None else Path(root)
    root = ROOT if root is None else Path(root)
    checks = []
    def add(name, status, detail, advice=''):
        checks.append({'name': name, 'status': status, 'detail': str(detail), 'advice': advice})
    def stage(name):
        if progress:
            progress('正在检查：' + name)
    add('Python', 'ok' if (3, 9) <= sys.version_info[:2] <= (3, 11) else 'warning',
        platform.python_version(), '当前重建脚本支持 Python 3.9–3.11；其他版本需验证兼容性。')
    for distribution, module in [('numpy', 'numpy'), ('opencv-python', 'cv2'), ('PySide6', 'PySide6.QtWidgets'),
                                 ('ultralytics', 'ultralytics'), ('torchvision', 'torchvision')]:
        stage(distribution)
        try:
            module_loader(module)
            add(distribution, 'ok', version_reader(distribution))
        except Exception as exc:
            add(distribution, 'error', exc, '使用 setup_desktop.ps1 在项目环境中安装依赖；不要覆盖原有 PyTorch 环境。')
    try:
        stage('PyTorch 与推理设备')
        torch = module_loader('torch')
        available = torch.cuda.is_available()
        add('PyTorch', 'ok', torch.__version__)
        if available:
            add('推理设备', 'ok', 'CPU（手动选择）' if device == 'cpu' else torch.cuda.get_device_name(0))
        else:
            add('推理设备', 'error' if device == '0' else 'ok', 'GPU 不可用；CPU 可用',
                '在分析配置中选择“仅 CPU”或“自动选择设备”。')
    except Exception as exc:
        add('PyTorch', 'error', exc, '检查 PyTorch 安装与系统运行库；重建脚本支持复用已有环境或独立安装 CPU 版。')
    for index, (relative, expected) in enumerate(profile_weight_specs(profile)):
        path, name = assets / relative, '基础模型' if relative == 'models/best.pt' else '所选模型 ' + str(index + 1)
        stage(name)
        try:
            digest = hashlib.sha256()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            matched = digest.hexdigest() == expected
            add(name, 'ok' if matched else 'error', '文件哈希与已登记版本一致' if matched else '文件与已登记版本不一致',
                '恢复对应 models 目录的已登记权重，或重新核对模型来源。')
        except OSError as exc:
            add(name, 'error', exc, '复制已登记模型到对应 models 目录；启动和诊断不会自动下载。')
    for filename, module_name, class_name in [('temporal.json', 'core.temporal', 'TemporalConfig'),
                                               ('risk.json', 'core.risk', 'RiskConfig')]:
        stage(filename)
        try:
            payload = json.loads((assets / 'configs' / filename).read_text(encoding='utf-8'))
            getattr(importlib.import_module(module_name), class_name)(**payload)
            add(filename, 'ok', '配置可读取且参数合法')
        except Exception as exc:
            add(filename, 'error', exc, '从迁移包恢复对应配置；“恢复默认设置”只恢复界面选项，不改算法配置文件。')
    for name in ('outputs', '.cache'):
        stage(name + ' 写入')
        try:
            path = root / name
            path.mkdir(exist_ok=True)
            with tempfile.TemporaryFile(dir=path) as stream:
                stream.write(b'fireguard-write-check')
            add(name + ' 写入', 'ok', '可保存本地结果')
        except OSError as exc:
            add(name + ' 写入', 'error', exc, '把项目放在当前用户可写的普通文件夹，例如 D:\\FireGuard。')
    return {'schema_version': 1, 'python': sys.executable, 'project_root': str(root.resolve()),
            'device': device, 'model_profile': profile, 'checks': checks,
            'ready': all(c['status'] != 'error' for c in checks),
            'note': '只检查依赖导入、GPU 可用性、权重哈希、配置和目录写入；不证明推理、准确率或长期稳定性。'}


def diagnostics_text(report):
    names = {'ok': '通过', 'warning': '注意', 'error': '需处理'}
    lines = ['环境检查：' + ('基础条件通过' if report['ready'] else '存在需处理项目')]
    for check in report['checks']:
        lines.append(f'[{names[check["status"]]}] {check["name"]}：{check["detail"]}')
        if check.get('advice') and check['status'] != 'ok':
            lines.append('  处理建议：' + check['advice'])
    lines.extend(['', report['note']])
    return '\n'.join(lines)
