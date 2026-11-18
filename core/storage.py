"""Retention and quota for completed run directories, with conservative deletion.

Only recognized generated files under direct run-* children are removable.
Active, protected, externally linked, changed or unfamiliar records are skipped.
No source video, model, annotation, cache or exported ZIP is managed here.
"""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import time

RUN_NAME = re.compile(r'run-\d{8}-\d{6}(?:-\d{6})?\Z')
FILES = {'annotated.mp4', 'frame-times.jsonl', 'config.json', 'summary.json',
         'detections.jsonl', 'temporal.jsonl', 'risk.jsonl', 'risk.csv', 'events.json',
         'risk-events.json', 'trend.jsonl', 'trend-latest.json', 'monitoring.jsonl',
         'alerts.jsonl', 'feedback.jsonl', 'clips.json', '.active', 'protected.json', 'scene-reviews.jsonl', '.scene-active',
         'fusion.jsonl', 'fusion-latest.json', 'fusion-events.json'}


def _linked(path):
    stat = path.lstat()
    return path.is_symlink() or bool(getattr(stat, 'st_file_attributes', 0) & 1024)


def _root(value):
    path = Path(value).absolute()
    if not path.is_dir() or _linked(path) or path.resolve() != path:
        raise ValueError('存储目录不存在或包含链接，不能执行清理。')
    return path


@contextmanager
def _lock(root):
    path = root / '.storage-operation.lock'
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise ValueError('另一个存储操作正在进行；如程序异常退出，请先检查锁文件。') from None
    try:
        with os.fdopen(fd, 'w', encoding='ascii') as stream:
            stream.write(str(os.getpid()))
        yield
    finally:
        path.unlink(missing_ok=True)


def _inspect(folder):
    rows, directories = [], []
    unsafe = False
    stack = [folder]
    while stack:
        directory = stack.pop()
        for path in directory.iterdir():
            if _linked(path):
                unsafe = True
                continue
            relative = path.relative_to(folder).as_posix()
            if path.is_dir():
                if relative not in ('evidence', 'clips', 'scene-evidence'):
                    unsafe = True
                directories.append(relative)
                stack.append(path)
            elif path.is_file():
                stat = path.stat()
                allowed = (relative in FILES or
                           (path.parent == folder / 'evidence' and re.fullmatch(r'event-\d+(?:-original)?\.(jpg|png)', path.name)) or
                           (path.parent == folder / 'scene-evidence' and re.fullmatch(r'(?:event-\d+|scene)-[0-9a-f]{20}\.jpg', path.name)) or
                           (path.parent == folder / 'clips' and re.fullmatch(r'clip-\d+\.(mp4|jsonl)', path.name)))
                unsafe |= not bool(allowed)
                rows.append((relative, stat.st_size, stat.st_mtime_ns))
            else:
                unsafe = True
    signature = hashlib.sha256(json.dumps(sorted(rows)).encode('utf-8')).hexdigest()
    return rows, directories, unsafe, signature


def inventory(outputs, exclude=()):
    root = _root(outputs)
    excluded = {str(Path(p).resolve()) for p in exclude}
    records = []
    for folder in sorted(root.iterdir()):
        if not RUN_NAME.fullmatch(folder.name):
            continue
        if _linked(folder) or not folder.is_dir():
            records.append({'name': folder.name, 'bytes': 0, 'eligible': False, 'reason': '链接或非目录',
                            'protected': False, 'active': False, 'created_at': 0, 'signature': ''})
            continue
        rows, _, unsafe, signature = _inspect(folder)
        active = (folder / '.active').exists() or (folder / '.scene-active').exists() or str(folder.resolve()) in excluded
        protected = (folder / 'protected.json').exists()
        complete = False
        try:
            summary = folder / 'summary.json'
            if summary.is_file() and not _linked(summary):
                complete = isinstance(json.loads(summary.read_text(encoding='utf-8')), dict)
        except (OSError, ValueError):
            pass
        reason = ('使用中 / 当前查看' if active else '重要记录' if protected else
                  '包含链接或非生成文件' if unsafe else '未正常保存总结' if not complete else '')
        try:
            stamp = datetime.strptime(folder.name[4:19], '%Y%m%d-%H%M%S').timestamp()
        except ValueError:
            stamp = 0
            reason = reason or '记录日期无效'
        records.append({'name': folder.name, 'bytes': sum(row[1] for row in rows),
                        'eligible': not reason, 'reason': reason, 'active': active,
                        'protected': protected, 'created_at': stamp, 'signature': signature})
    return {'root': str(root), 'records': records, 'total_bytes': sum(r['bytes'] for r in records)}


def cleanup_plan(outputs, *, retention_days=30, max_bytes=5 * 1024 ** 3, exclude=(), now=None):
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or not 1 <= retention_days <= 3650:
        raise ValueError('保留天数应为 1–3650 的整数。')
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError('容量上限应为正整数。')
    result = inventory(outputs, exclude)
    remaining = result['total_bytes']
    candidates = []
    now = time.time() if now is None else now
    for record in sorted(result['records'], key=lambda r: (r['created_at'], r['name'])):
        expired = now - record['created_at'] > retention_days * 86400
        if record['eligible'] and (expired or remaining >= max_bytes):
            candidates.append({**record, 'cleanup_reason': '超过保留期限' if expired else '超过容量上限'})
            remaining -= record['bytes']
    return {**result, 'candidates': candidates, 'remaining_bytes': remaining,
            'reclaim_bytes': result['total_bytes'] - remaining, 'over_limit': remaining >= max_bytes,
            'retention_days': retention_days, 'max_bytes': max_bytes}


def execute_cleanup(outputs, plan, *, exclude=()):
    root = _root(outputs)
    if str(root) != plan.get('root'):
        raise ValueError('清理计划与目录不一致。')
    removed, skipped = [], []
    with _lock(root):
        current = {r['name']: r for r in inventory(root, exclude)['records']}
        for approved in plan['candidates']:
            record = current.get(approved['name'])
            if (not record or not record['eligible'] or record['signature'] != approved['signature']):
                skipped.append({'name': approved['name'], 'reason': '记录已改变、被保护或正在使用'})
                continue
            target = root / record['name']
            # Verify the final absolute target before any recursive traversal or deletion.
            if target.resolve() != target or target.parent != root or not RUN_NAME.fullmatch(target.name):
                raise ValueError('清理路径不在指定存储目录内。')
            rows, directories, unsafe, signature = _inspect(target)
            if unsafe or signature != approved['signature'] or (target / '.active').exists() or (target / 'protected.json').exists():
                skipped.append({'name': target.name, 'reason': '删除前复核未通过'})
                continue
            try:
                for relative, _, _ in rows:
                    path = target / relative
                    if _linked(path) or target not in path.resolve().parents:
                        raise ValueError('发现外部链接，已中止本条清理。')
                    path.unlink()
                for relative in sorted(directories, key=len, reverse=True):
                    (target / relative).rmdir()
                target.rmdir()
                removed.append({'name': record['name'], 'bytes': record['bytes']})
            except (OSError, ValueError) as exc:
                skipped.append({'name': target.name, 'reason': str(exc)})
    return {'removed': removed, 'skipped': skipped, 'reclaimed_bytes': sum(r['bytes'] for r in removed)}


def protect_record(outputs, name, protected):
    root = _root(outputs)
    if not isinstance(protected, bool) or not RUN_NAME.fullmatch(name):
        raise ValueError('记录保护参数无效。')
    folder = root / name
    if folder.parent != root or not folder.is_dir() or _linked(folder) or folder.resolve() != folder:
        raise ValueError('记录目录无效。')
    with _lock(root):
        path = folder / 'protected.json'
        if protected:
            if path.exists():
                return
            with path.open('x', encoding='utf-8') as stream:
                json.dump({'protected': True, 'created_at': datetime.now().astimezone().isoformat()}, stream)
        else:
            if path.exists() and _linked(path):
                raise ValueError('保护标记不能是链接。')
            path.unlink(missing_ok=True)


class StorageLimitError(RuntimeError):
    pass


def enforce_quota(outputs, *, max_bytes, retention_days=30, automatic=False, exclude=()):
    plan = cleanup_plan(outputs, retention_days=retention_days, max_bytes=max_bytes, exclude=exclude)
    if automatic and plan['candidates']:
        execute_cleanup(outputs, plan, exclude=exclude)
        plan = cleanup_plan(outputs, retention_days=retention_days, max_bytes=max_bytes, exclude=exclude)
    if plan['total_bytes'] >= max_bytes:
        raise StorageLimitError('监控记录已达到容量上限；请在“存储管理”清理记录、调整上限或解除部分重要保护后重试。')
    return plan
