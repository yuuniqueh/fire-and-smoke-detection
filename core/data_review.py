"""Human video review records, separate from predictions and source files."""
import copy
import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

STATES = ('unknown', 'absent', 'present', 'uncertain')


def initialize_review(manifest, seen_sources):
    return {'schema_version': 1, 'dataset': manifest['dataset'],
            'time_basis': 'frame_index / publisher_fps; intervals [start_frame, end_frame_exclusive)',
            'source_manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
            'split_frozen': False, 'videos': [
                {'filename': row['filename'], 'source_sha256': row['sha256'],
                 'fps': row['fps'], 'decoded_frames': row['decoded_frames'],
                 'quality': 'quarantined_frame_count' if not row['frame_count_matches'] else 'pending_content_review',
                 'development_seen': row['filename'] in seen_sources,
                 'split': 'development_seen' if row['filename'] in seen_sources else 'unassigned',
                 'source_group': '', 'group_verified': False,
                 'training_overlap': 'unverified', 'full_video_reviewed': False,
                 'reviewer': '', 'class_status': {'fire': 'unknown', 'smoke': 'unknown'},
                 'intervals': [], 'notes': '', 'updated_at_utc': None}
                for row in manifest['videos']]}


def validate_video(row):
    count = row['decoded_frames']
    if count <= 0 or row['fps'] <= 0:
        raise ValueError('素材帧数或帧率无效。')
    by_class = {'fire': [], 'smoke': []}
    for interval in row['intervals']:
        label, start, end = interval['label'], interval['start_frame'], interval['end_frame_exclusive']
        if label not in by_class or type(start) is not int or type(end) is not int or not 0 <= start < end <= count:
            raise ValueError('区间必须满足：类别有效，0 ≤ 起始帧 < 结束帧 ≤ 视频帧数。')
        by_class[label].append((start, end))
    for label, intervals in by_class.items():
        status = row['class_status'][label]
        if status not in STATES:
            raise ValueError('类别状态无效。')
        if intervals and status in ('absent', 'unknown'):
            raise ValueError(f'{label} 已有区间，不能标记为“无”或“未判断”。')
        ordered = sorted(intervals)
        if any(right[0] < left[1] for left, right in zip(ordered, ordered[1:])):
            raise ValueError(f'{label} 的区间重叠，请合并或修正。')
        if row['full_video_reviewed'] and status == 'present' and not intervals:
            raise ValueError(f'{label} 标记“有”时，必须记录可见区间。')
    if row['full_video_reviewed']:
        if not row['reviewer'].strip():
            raise ValueError('完成审查必须填写审查人代号。')
        if 'unknown' in row['class_status'].values():
            raise ValueError('完成审查前须分别判断 fire 和 smoke。')
        if 'uncertain' in row['class_status'].values() and not row['notes'].strip():
            raise ValueError('存在“不确定”类别时，请记录原因。')
    if row['group_verified'] and not row['source_group'].strip():
        raise ValueError('同源组核查完成时必须填写组名。')
    if row['development_seen'] and row['split'] != 'development_seen':
        raise ValueError('已用于开发的视频必须保留 development_seen 标记。')


def read_review(path):
    raw = Path(path).read_bytes()
    return json.loads(raw.decode('utf-8')), hashlib.sha256(raw).hexdigest()


def save_review(path, document, expected_revision=None):
    path = Path(path)
    previous_raw = None
    if path.exists():
        previous_raw = path.read_bytes()
        if expected_revision is None or hashlib.sha256(previous_raw).hexdigest() != expected_revision:
            raise ValueError('标注文件已被其他窗口修改，请关闭并重新打开后继续。')
    elif expected_revision is not None:
        raise ValueError('原标注文件已不存在，请重新打开。')
    for row in document['videos']:
        validate_video(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    if previous_raw is not None:
        history = path.parent / 'history'
        history.mkdir(exist_ok=True)
        backup = history / f'{path.stem}-{expected_revision}.json'
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(previous_raw)
    raw = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return hashlib.sha256(raw).hexdigest()


def replace_video(document, row):
    validate_video(row)
    updated = copy.deepcopy(document)
    for index, previous in enumerate(updated['videos']):
        if previous['filename'] == row['filename']:
            if previous['source_sha256'] != row['source_sha256']:
                raise ValueError('源视频身份发生变化，请核查。')
            row = copy.deepcopy(row)
            row['updated_at_utc'] = datetime.now(timezone.utc).isoformat()
            updated['videos'][index] = row
            return updated
    raise ValueError('视频不在素材登记中。')
