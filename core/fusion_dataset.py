"""Bounded, auditable candidates and independent labels for scene fusion.

Detector labels and operator workflow states are deliberately never labels here.
Old runs without a causal availability log remain reviewable, but their estimated
AI timing excludes them from supervised fitting.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_LOG_BYTES = 256 * 1024 * 1024
MAX_LINE_BYTES = 512 * 1024
MAX_LOG_ROWS = 1_000_000
MAX_SCENE_ROWS = 2000
MAX_SAMPLES = 2000
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGE_TOTAL = 512 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024 * 1024
MAX_SOURCE_TOTAL = 8 * 1024 * 1024 * 1024
INTERVAL_S = 5.0
LABELS = {'normal', 'abnormal', 'uncertain'}


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('时间与数值必须是有限数字。')
    return float(value)


def _stamp(value):
    value = _number(value)
    if value < 0:
        raise ValueError('视频时间不能为负数。')
    return value


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def scene_record_key(record):
    """Identity shared with the causal fusion log; paths and UI fields do not count."""
    return _digest({'evidence': [{'timestamp_s': _stamp(item['timestamp_s']),
                                 'sha256': item.get('sha256', '')}
                                for item in record.get('evidence', [])],
                    'model': record.get('model'), 'created_at': record.get('created_at'),
                    'review_profile': record.get('review_profile')})


def _read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError(f'数据文件不存在、过大或路径无效：{path.name}')
    with path.open(encoding='utf-8-sig') as stream:
        return json.load(stream)


def _rows(path, *, max_rows=MAX_LOG_ROWS, optional=False):
    if optional and not path.exists():
        return
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_LOG_BYTES:
        raise ValueError(f'日志不存在、过大或路径无效：{path.name}')
    with path.open('rb') as stream:
        count = 0
        while True:
            raw = stream.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_LINE_BYTES:
                raise ValueError(f'日志单行过大：{path.name}')
            if not raw.strip():
                continue
            count += 1
            if count > max_rows:
                raise ValueError(f'日志行数超出上限：{path.name}')
            row = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(row, dict):
                raise ValueError('日志记录必须是对象。')
            yield row


def _is_qa(path, config, records):
    pattern = re.compile(r'(^|[-_ ])(qa|fixture|test_fixture|synthetic|engineering)([-_ ]|$)', re.I)
    if any(pattern.search(part) for part in path.parts):
        return True
    if any(config.get(key) is True for key in ('is_qa', 'test_fixture', 'synthetic')):
        return True
    if Path(str(config.get('source', ''))).name.lower().startswith(('engineering_', 'synthetic_')):
        return True
    for record in records:
        device = record.get('device') or {}
        if str(device.get('id', '')).lower().startswith(('qa', 'fixture')):
            return True
        if record.get('test_fixture') is True or str(record.get('model', '')).startswith('fixture'):
            return True
    return False


def _source(config, run, cache, budget):
    source = config.get('source')
    if source is None or source == '':
        raise ValueError('运行配置缺少原始视频或相机来源。')
    kind = config.get('source_kind', 'file')
    if kind in ('file', 'video', 'local_video'):
        path = Path(str(source)).expanduser()
        path = (path if path.is_absolute() else run / path).resolve()
        identity = os.path.normcase(str(path)).casefold()
        digest = None
        reason = 'source_missing'
        if path.is_file():
            before = path.stat()
            key = (str(path), before.st_size, before.st_mtime_ns)
            if before.st_size <= MAX_SOURCE_BYTES:
                if key not in cache:
                    budget[0] += before.st_size
                    if budget[0] > MAX_SOURCE_TOTAL:
                        raise ValueError('来源视频校验总量超过8 GiB，请减少运行目录。')
                    hasher = hashlib.sha256()
                    bytes_read = 0
                    with path.open('rb') as stream:
                        for block in iter(lambda: stream.read(1024 * 1024), b''):
                            bytes_read += len(block)
                            if bytes_read > before.st_size:
                                raise ValueError('来源视频在校验期间发生变化。')
                            hasher.update(block)
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('来源视频在校验期间发生变化。')
                    cache[key] = hasher.hexdigest()
                digest = cache[key]
                reason = None
            else:
                reason = 'source_exceeds_2_gib_limit'
        group = _digest({'kind': 'file_content_sha256', 'identity': digest}) if digest else _digest(
            {'kind': 'unverified_file_path', 'identity': identity})
        return {'source': str(path), 'source_group': group, 'source_exists': path.is_file(),
                'source_sha256': digest, 'identity_unverified': digest is None,
                'identity_reason': reason, 'source_kind': 'file'}
    # Camera identity contains neither credentials nor mutable URL query tokens.
    value = str(source)
    parsed = urlsplit(value)
    identity = f'{parsed.scheme}://{parsed.hostname}:{parsed.port}{parsed.path}' if parsed.scheme else value
    camera = config.get('camera_id') or config.get('monitor_id') or identity
    return {'source': str(camera), 'source_group': _digest({'kind': 'camera', 'identity': str(camera)}),
            'source_exists': None, 'source_sha256': None, 'identity_unverified': False,
            'identity_reason': None, 'source_kind': 'camera'}


def _availability(run):
    result = {}
    previous = -1.0
    for row in _rows(run / 'fusion.jsonl', optional=True):
        timestamp = _stamp(row.get('timestamp_s'))
        if timestamp < previous:
            raise ValueError('融合日志时间必须递增。')
        previous = timestamp
        meta = row.get('fusion') or {}
        key, ready = meta.get('scene_record_key'), meta.get('scene_available_at_s')
        if key is None or ready is None:
            continue
        ready = _stamp(ready)
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key) or ready > timestamp:
            raise ValueError('融合日志的AI可用时间无效。')
        result[key] = min(result.get(key, ready), ready)
    return result


def _scenes(run):
    causal = _availability(run)
    result = []
    scene_identities = {}
    for record in _rows(run / 'scene-reviews.jsonl', max_rows=MAX_SCENE_ROWS, optional=True):
        frames = record.get('evidence') or []
        if not isinstance(frames, list) or not 1 <= len(frames) <= 16:
            continue
        stamps = [_stamp(frame.get('timestamp_s')) for frame in frames]
        if stamps != sorted(set(stamps)):
            raise ValueError('AI原画面时间必须严格递增。')
        reference = stamps[-1]
        key = scene_record_key(record)
        # The public identity intentionally omits postprocessing/UI fields.
        # Identical evidence/model/time cannot silently identify two answers.
        answer = _canonical(record.get('scene'))
        if key in scene_identities and scene_identities[key] != answer:
            raise ValueError('同一AI记录身份对应不同场景答案，无法安全关联可用时间。')
        scene_identities[key] = answer
        explicit = record.get('available_at_s')
        ready = causal.get(key)
        if explicit is not None:
            explicit = _stamp(explicit)
            ready = max(explicit, ready) if ready is not None else explicit
        mode = 'exact' if ready is not None else 'estimated'
        if ready is None:
            elapsed = max(_stamp(record.get('elapsed_s', 0)), _stamp(record.get('total_elapsed_s', 0)))
            ready = reference + elapsed
        if ready < reference:
            raise ValueError('AI不能在所用原画面之前完成。')
        result.append({'record': record, 'reference_s': reference, 'ready_s': ready,
                       'availability': mode, 'key': key})
    return sorted(result, key=lambda item: item['ready_s'])


def _images(scene, run, source, cache, budget):
    images, errors = [], []
    for item in scene.get('evidence', []) if scene else []:
        relative = Path(str(item.get('image', '')))
        path = (run / relative).resolve()
        valid_path = bool(item.get('image')) and not relative.is_absolute() and path.is_relative_to(run)
        digest = str(item.get('sha256', ''))
        valid_hash = bool(re.fullmatch('[0-9a-f]{64}', digest))
        verified = False
        if valid_path and valid_hash and path.is_file() and not (run / relative).is_symlink():
            size = path.stat().st_size
            if size <= MAX_IMAGE_BYTES:
                cache_key = str(path)
                if cache_key not in cache:
                    budget[0] += size
                    if budget[0] > MAX_IMAGE_TOTAL:
                        raise ValueError('原画面校验总量超过上限，请减少候选数量。')
                    cache[cache_key] = hashlib.sha256(path.read_bytes()).hexdigest()
                verified = cache[cache_key] == digest
        if not verified:
            errors.append('evidence_integrity')
        images.append({'path': str(path) if valid_path else '', 'timestamp_s': item['timestamp_s'],
                       'sha256': digest, 'verified': verified, 'source': source})
    return images, sorted(set(errors))


def prepare_dataset(run_paths, destination, *, max_samples=200, source_groups=None):
    """Create candidates only from explicitly supplied run directories; never overwrite."""
    if type(max_samples) is not int or not 1 <= max_samples <= MAX_SAMPLES:
        raise ValueError(f'候选数量应为1至{MAX_SAMPLES}。')
    if isinstance(run_paths, (str, Path)):
        run_paths = [run_paths]
    else:
        run_paths = list(run_paths)
    if not 1 <= len(run_paths) <= 64:
        raise ValueError('请明确选择1至64个运行目录。')
    target = Path(destination).resolve()
    if target.exists():
        raise ValueError('候选目录已经存在，请选择新目录。')
    if source_groups is not None and (not isinstance(source_groups, dict) or len(source_groups) > 1000):
        raise ValueError('人工来源组映射必须是有限路径字典。')
    overrides = {}
    for path, group_name in (source_groups or {}).items():
        if not isinstance(group_name, str) or not group_name.strip() or len(group_name) > 200:
            raise ValueError('人工来源组名称无效。')
        overrides[os.path.normcase(str(Path(path).resolve())).casefold()] = group_name.strip()
    # Resolve content identities before sampling so a copied source inherits the
    # same optional manual group even if only one of its paths was mapped.
    prepared, content_overrides, source_cache, source_budget = [], {}, {}, [0]
    for raw_path in run_paths:
        run = Path(raw_path).resolve()
        if not run.is_dir():
            raise ValueError('运行目录不存在。')
        config = _read_json(run / 'config.json')
        identity = _source(config, run, source_cache, source_budget)
        manual = overrides.get(os.path.normcase(identity['source']).casefold())
        if manual:
            previous = content_overrides.get(identity['source_group'])
            if previous is not None and previous != manual:
                raise ValueError('相同内容的视频不能分配到不同人工来源组。')
            content_overrides[identity['source_group']] = manual
        prepared.append((run, config, identity))
    samples, exclusions, groups, seen, image_cache, budget = [], [], {}, set(), {}, [0]
    for run, config, identity in prepared:
        if len(samples) >= max_samples:
            break
        source = identity['source']
        manual = content_overrides.get(identity['source_group'])
        group = _digest({'kind': 'manual_source_group', 'identity': manual}) if manual else identity['source_group']
        scenes = _scenes(run)
        qa = _is_qa(run, config, [item['record'] for item in scenes])
        groups.setdefault(group, {'sources': [], 'source_kind': identity['source_kind'],
                                  'manual_group': manual, 'identity_unverified': False})
        if source not in groups[group]['sources']:
            groups[group]['sources'].append(source)
        groups[group]['identity_unverified'] |= identity['identity_unverified']
        previous, sampled = -1.0, set()
        for row in _rows(run / 'risk.jsonl'):
            timestamp = _stamp(row.get('timestamp_s'))
            if timestamp < previous:
                raise ValueError('检测日志时间必须递增。')
            previous = timestamp
            events = row.get('events') or [None]
            if not isinstance(events, list) or len(events) > 100:
                raise ValueError('检测日志的事件数量无效。')
            bucket = int(timestamp // INTERVAL_S)
            for event in events:
                if event is not None and not isinstance(event, dict):
                    raise ValueError('事件记录必须是对象。')
                event_id = event.get('event_id') if event else None
                local_key = (event_id, bucket)
                if local_key in sampled:
                    continue
                sampled.add(local_key)
                # Local event numbers are not stable across replays. Overlapping
                # windows of the same source/class remain one candidate.
                dedup = (identity['source_group'], bucket, event.get('label') if event else 'scene')
                if dedup in seen:
                    if len(exclusions) < MAX_SAMPLES:
                        exclusions.append({'run_path': str(run), 'timestamp_s': timestamp,
                                           'reason': 'duplicate_source_segment'})
                    continue
                seen.add(dedup)
                candidates = [item for item in scenes if item['ready_s'] <= timestamp
                              and 0 <= timestamp - item['reference_s'] <= 12.0
                              and item['record'].get('event_id') in (None, event_id)]
                selected = max(candidates, key=lambda item: item['reference_s'], default=None)
                scene = deepcopy(selected['record']) if selected else None
                baseline = deepcopy(event if event else row)
                if event is None:
                    baseline.pop('events', None)
                images, errors = _images(scene, run, source, image_cache, budget)
                sample = {**identity, 'sample_id': _digest(list(dedup)), 'source_group': group, 'source': source,
                          'content_group': identity['source_group'],
                          'run_path': str(run), 'event_id': event_id,
                          'timestamp_s': timestamp, 'segment_start_s': max(0., timestamp - 2.),
                          'segment_end_s': timestamp, 'baseline': baseline, 'scene_record': scene,
                          'active_event_count': len(row.get('events') or []),
                          'scene_record_key': selected['key'] if selected else None,
                          'availability': selected['availability'] if selected else 'baseline_only',
                          'scene_available_at_s': selected['ready_s'] if selected else None,
                          'estimated_ready_time': selected['ready_s'] if selected and selected['availability'] == 'estimated' else None,
                          'evidence_images': images, 'integrity_errors': errors, 'is_qa': qa,
                          'test_fixture': qa, 'label': None}
                samples.append(sample)
                if len(samples) >= max_samples:
                    break
            if len(samples) >= max_samples:
                break
    manifest = {'schema_version': SCHEMA_VERSION, 'created_at': datetime.now(timezone.utc).isoformat(),
                'status': 'pending_independent_annotations', 'sampling_interval_s': INTERVAL_S,
                'label_definition': {'normal': '可见受控用火或无异常燃烧', 'abnormal': '可见异常燃烧，需升级核查',
                                     'uncertain': '原画面不足以判断'},
                'timing_policy': 'Only causally available AI; estimated old-run timing excluded from fitting.',
                'groups': groups, 'samples': samples, 'preparation_exclusions': exclusions}
    # Validate size before creating anything; destination creation is exclusive.
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False)
    if len(payload.encode('utf-8')) > MAX_JSON_BYTES:
        raise ValueError('候选清单过大，请减少数量。')
    target.mkdir(parents=True, exist_ok=False)
    (target / 'manifest.json').write_text(payload, encoding='utf-8')
    return manifest


def _manifest(path):
    root = Path(path).resolve()
    if root.is_file():
        root = root.parent
    doc = _read_json(root / 'manifest.json')
    if doc.get('schema_version') != SCHEMA_VERSION or not isinstance(doc.get('samples'), list):
        raise ValueError('候选清单版本或格式无效。')
    samples = doc['samples']
    if len(samples) > MAX_SAMPLES or len({item['sample_id'] for item in samples}) != len(samples):
        raise ValueError('候选数量无效或编号重复。')
    for sample in samples:
        if sample.get('source_kind') not in ('file', 'camera') or (sample.get('source_kind') == 'file'
                and not re.fullmatch('[0-9a-f]{64}', str(sample.get('source_sha256', '')))):
            # Earlier candidates remain reviewable without retroactively
            # claiming their path-only identity was content-verified.
            sample['identity_unverified'] = True
            sample.setdefault('identity_reason', 'legacy_or_missing_content_identity')
    return root, doc


def _latest(root, samples):
    known = {item['sample_id'] for item in samples}
    latest, ledger = {}, []
    for entry in _rows(root / 'annotations.jsonl', max_rows=10_000, optional=True):
        if entry.get('sample_id') not in known or entry.get('label') not in LABELS:
            raise ValueError('审核记录引用未知候选或标签。')
        reviewer = entry.get('reviewer')
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100:
            raise ValueError('审核人标识无效。')
        latest.setdefault(entry['sample_id'], {})[reviewer.strip().casefold()] = entry
        ledger.append(entry)
    return latest, ledger


def _summary(sample, reviews):
    result = {'status': 'pending', 'label': None, 'reviews': reviews, 'reason': '等待两位不同审核人独立审核。'}
    if sample.get('is_qa') or sample.get('test_fixture'):
        result.update(status='excluded', reason='QA或工程测试候选不得用于训练。')
    elif sample.get('identity_unverified'):
        result.update(status='excluded', reason='原始视频内容身份尚未核实，不能用于训练。')
    elif sample.get('integrity_errors'):
        result.update(status='excluded', reason='原画面校验未通过。')
    elif sample.get('availability') == 'estimated':
        result.update(status='excluded', reason='旧记录仅有估计AI完成时间，不能用于因果训练。')
    elif reviews:
        labels = {entry['label'] for entry in reviews.values()}
        if 'uncertain' in labels:
            result.update(status='uncertain', reason='存在证据不足结论，需复核。')
        elif len(labels) > 1:
            result.update(status='disagreement', reason='独立审核结论不一致。')
        elif len(reviews) >= 2:
            result.update(status='agreed', label=int(next(iter(labels)) == 'abnormal'), reason='两位以上审核人结论一致。')
    return result


def load_dataset(path):
    root, doc = _manifest(path)
    latest, ledger = _latest(root, doc['samples'])
    doc['annotations'] = ledger
    doc['annotation_summary'] = {item['sample_id']: _summary(item, latest.get(item['sample_id'], {}))
                                 for item in doc['samples']}
    return doc


def annotation_summary(path):
    """Compact review states for callers; exclusions remain explicit."""
    doc = load_dataset(path)
    return {sample_id: {'reviewers': {reviewer: item['label'] for reviewer, item in value['reviews'].items()},
                       'status': value['status'], 'label': ('abnormal' if value['label'] == 1 else
                                                          'normal' if value['label'] == 0 else None),
                       'reason': value['reason']}
            for sample_id, value in doc['annotation_summary'].items()}


def save_annotation(dataset_path, sample_id, *, reviewer, label, note=''):
    root, doc = _manifest(dataset_path)
    if sample_id not in {item['sample_id'] for item in doc['samples']}:
        raise ValueError('候选编号不存在。')
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100:
        raise ValueError('请填写有效审核人标识。')
    if label not in LABELS or not isinstance(note, str) or len(note) > 2000:
        raise ValueError('审核结论或备注无效。')
    _, ledger = _latest(root, doc['samples'])
    if len(ledger) >= 10_000:
        raise ValueError('审核日志已达上限。')
    entry = {'schema_version': SCHEMA_VERSION, 'sample_id': sample_id, 'reviewer': reviewer.strip(),
             'label': label, 'note': note, 'created_at': datetime.now(timezone.utc).isoformat()}
    path = root / 'annotations.jsonl'
    if path.is_symlink():
        raise ValueError('审核日志路径无效。')
    with path.open('a', encoding='utf-8') as stream:
        stream.write(_canonical(entry) + '\n')
    return entry


def adjudicated_samples(path):
    """Return consensus labels only; workflow acknowledgements are never consulted."""
    doc = load_dataset(path)
    accepted, exclusions, seen = [], [], set()
    cache, budget, source_cache, source_budget = {}, [0], {}, [0]
    for sample in doc['samples']:
        summary = doc['annotation_summary'][sample['sample_id']]
        reason = summary['reason'] if summary['status'] != 'agreed' else None
        if reason is None:
            if sample.get('source_kind') == 'file':
                identity = _source({'source': sample['source'], 'source_kind': 'file'},
                                   Path(sample['run_path']), source_cache, source_budget)
                if identity['identity_unverified'] or identity['source_sha256'] != sample.get('source_sha256'):
                    reason = '原始视频内容已变化或不可校验。'
            scene = sample.get('scene_record')
            _, errors = _images(scene, Path(sample['run_path']).resolve(), sample['source'], cache, budget)
            if errors:
                reason = '原画面校验未通过。'
            key = (sample.get('content_group', sample['source_group']), int(_stamp(sample['timestamp_s']) // INTERVAL_S),
                   sample['baseline'].get('label', 'scene'))
            if key in seen:
                reason = '同一来源的重复片段。'
            seen.add(key)
        if reason is not None:
            exclusions.append({'sample_id': sample['sample_id'], 'status': summary['status'], 'reason': reason})
        else:
            accepted.append({**deepcopy(sample), 'label': summary['label'],
                             'reviewers': sorted(summary['reviews'])})
    return accepted, exclusions
