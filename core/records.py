"""Read completed local runs and export portable evidence without external services."""
import csv
import json
import os
from pathlib import Path
import tempfile
import zipfile


def read_json(path):
    return Path(path).read_text(encoding='utf-8')


def load_run(path):
    path = Path(path).resolve()
    summary = json.loads(read_json(path / 'summary.json'))
    config = json.loads(read_json(path / 'config.json')) if (path / 'config.json').is_file() else {}
    events = json.loads(read_json(path / 'events.json')).get('events', []) if (path / 'events.json').is_file() else []
    return {'output': str(path), 'summary': summary, 'config': config, 'events': events}


def list_runs(root):
    runs = []
    for folder in sorted(Path(root).glob('run-*'), reverse=True):
        if not (folder / 'summary.json').is_file():
            continue
        try:
            # History listing does not load videos or full frame logs.
            summary = json.loads(read_json(folder / 'summary.json'))
            config = json.loads(read_json(folder / 'config.json')) if (folder / 'config.json').is_file() else {}
            runs.append({'output': str(folder.resolve()), 'summary': summary, 'config': config})
        except (ValueError, OSError):
            continue
    return runs


def load_curve(path, interval_s=0.1, window_s=120.0):
    from collections import deque
    points = deque()
    csv_path = Path(path) / 'risk.csv'
    if not csv_path.is_file():
        return []
    last_time = -float('inf')
    with csv_path.open(encoding='utf-8', newline='') as stream:
        for row in csv.DictReader(stream):
            timestamp = float(row['timestamp_s'])
            if timestamp - last_time + 1e-6 < interval_s:
                continue
            points.append((timestamp, float(row['score']) if row['status'] == 'assessed' else None))
            last_time = timestamp
            while points and points[0][0] < timestamp - window_s:
                points.popleft()
    return list(points)


def export_run(run_path, destination):
    """Atomic ZIP, fixed allowlist, relative entries; no model or source video copy."""
    root = Path(run_path).resolve()
    destination = Path(destination).resolve()
    if root == destination or root in destination.parents:
        raise ValueError('请将导出包保存到本次结果目录之外。')
    if not (root / 'summary.json').is_file():
        raise ValueError('分析尚未结束，不能导出未完成的记录。')
    allowed = {'annotated.mp4', 'detections.jsonl', 'temporal.jsonl', 'risk.jsonl', 'risk.csv',
               'events.json', 'risk-events.json', 'config.json', 'summary.json'}
    files = [p for p in root.iterdir() if p.name in allowed and p.is_file() and not p.is_symlink()]
    evidence = root / 'evidence'
    if evidence.is_dir() and not evidence.is_symlink():
        files.extend(p for p in evidence.iterdir() if p.is_file() and not p.is_symlink() and p.suffix.lower() in ('.jpg', '.png'))
    fd, temporary = tempfile.mkstemp(prefix='fireguard-export-', suffix='.tmp', dir=str(destination.parent))
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
            for path in sorted(files):
                archive.write(path, path.relative_to(root).as_posix())
            archive.writestr('README.txt', '焰鉴分析记录\n时间以源视频为准；风险指数不是火灾概率。\n'
                             '事件结束是软件边界，不代表实际火情结束。\n'
                             '包含参数、模型来源、日志、事件和输出视频；原视频及模型权重未打包。\n'
                             '配置中的输入路径是原机器路径；运行复现需要合法取得对应素材与模型。\n')
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(destination)
