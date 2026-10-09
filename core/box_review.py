"""Human box records with source identity, draft status and safe revisioned saving."""
import copy
import hashlib
import json
import math
import os
import uuid
from pathlib import Path

STATES = ('unknown', 'absent', 'present', 'uncertain')
IDENTITY = ('image', 'image_sha256', 'width', 'height', 'source', 'source_sha256',
            'frame_index', 'timestamp_s', 'split')


def digest_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def initialize(manifest, manifest_sha256):
    rows = []
    for source in manifest['images']:
        if source['split'] != 'development':
            raise ValueError('本工具只允许标注开发组原图。')
        rows.append({**{key: source[key] for key in IDENTITY}, 'reviewer_id': '',
                     'class_status': {'fire': 'unknown', 'smoke': 'unknown'},
                     'boxes': [], 'notes': '', 'completed': False})
    return {'schema_version': 1, 'manifest_sha256': manifest_sha256,
            'coordinate_basis': 'original image pixels; xyxy within [0,width] x [0,height]',
            'images': rows}


def validate_row(row):
    for box in row['boxes']:
        coords = box['xyxy']
        if box['label'] not in ('fire', 'smoke') or len(coords) != 4:
            raise ValueError('矩形框类别或坐标无效。')
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in coords):
            raise ValueError('框坐标必须是有限数值。')
        x1, y1, x2, y2 = coords
        if not (0 <= x1 < x2 <= row['width'] and 0 <= y1 < y2 <= row['height']):
            raise ValueError('矩形框越界或面积为零。')
    for label in ('fire', 'smoke'):
        state = row['class_status'][label]
        if state not in STATES:
            raise ValueError('类别判断无效。')
        if row['completed']:
            count = sum(box['label'] == label for box in row['boxes'])
            if state == 'unknown':
                raise ValueError('完成前须分别判断火焰和烟雾。')
            if state == 'present' and not count:
                raise ValueError(f'{label} 选“有”时，请框出所有可见目标。')
            if state == 'absent' and count:
                raise ValueError(f'{label} 选“无”时不能保留该类框。')
            if state == 'uncertain' and not row['notes'].strip():
                raise ValueError('不确定时请在备注中写明原因。')
    if row['completed'] and not row['reviewer_id'].strip():
        raise ValueError('完成前请填写审查人代号，例如 R1。')


def validate_document(document, manifest, manifest_sha256):
    if document['schema_version'] != 1 or document['manifest_sha256'] != manifest_sha256:
        raise ValueError('标注与原图清单不一致。')
    if len(document['images']) != len(manifest['images']):
        raise ValueError('原图数量不一致。')
    for row, original in zip(document['images'], manifest['images']):
        if any(row[key] != original[key] for key in IDENTITY) or row['split'] != 'development':
            raise ValueError('原图身份或分组被修改。')
        validate_row(row)


def load(path, manifest, manifest_sha256):
    path = Path(path)
    if not path.exists():
        return initialize(manifest, manifest_sha256), None
    raw = path.read_bytes()
    document = json.loads(raw.decode('utf-8'))
    validate_document(document, manifest, manifest_sha256)
    return document, digest_bytes(raw)


def save(path, document, manifest, manifest_sha256, expected_revision):
    validate_document(document, manifest, manifest_sha256)
    path = Path(path)
    previous = path.read_bytes() if path.exists() else None
    actual_revision = digest_bytes(previous) if previous is not None else None
    if actual_revision != expected_revision:
        raise ValueError('文件已被另一个窗口修改；请重新打开，避免覆盖已有标注。')
    if previous is not None:
        history = path.parent / 'box-history'
        history.mkdir(exist_ok=True)
        backup = history / f'{path.stem}-{actual_revision}.json'
        if not backup.exists():
            backup.write_bytes(previous)
    raw = (json.dumps(document, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('xb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            temp.unlink()
    return digest_bytes(raw)


def training_eligible(row):
    validate_row(row)
    return row['completed'] and all(state in ('absent', 'present') for state in row['class_status'].values())
