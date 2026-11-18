"""Deterministic event-board ordering without rewriting event identities.

Run-name dates describe local analysis/recording order. Adding a media offset
does not recover the wall-clock time of an actual fire in an imported video.
Rows without a known date follow dated rows and use their media position.
"""
from datetime import datetime, timezone
import json
import math
import re

from core.review_workflow import phase


MODES = ('priority', 'time_desc', 'time_asc', 'device')
LEVEL_RANK = {'NORMAL': 0, 'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
_LOCAL_ZONE = datetime.now().astimezone().tzinfo
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_RUN_NAME = re.compile(r'^run-(\d{8})-(\d{6})(?:-(\d{1,6}))?$')


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _date(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and 8 <= len(value) <= 128:
        try:
            result = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError:
            return None
    else:
        return None
    try:
        if result.tzinfo is None:
            result = result.replace(tzinfo=_LOCAL_ZONE)
        return (result.astimezone(timezone.utc) - _EPOCH).total_seconds()
    except (ValueError, OverflowError):
        return None


def _position(event):
    for field in ('confirmed_at_s', 'timestamp_s', 'reference_s', 'started_at_s'):
        value = event.get(field)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            return float(value)
    return 0.


def _run_start(event, key):
    for source in (event, _mapping(event.get('config')), _mapping(event.get('run'))):
        for field in ('recording_started_at', 'recording_start_time', 'run_started_at',
                      'record_started_at', 'run_created_at'):
            value = _date(source.get(field))
            if value is not None:
                return value
    paths = [event.get('output'), event.get('run_path'), event.get('run')]
    if isinstance(key, tuple) and len(key) > 1:
        paths.append(key[1])
    for path in paths:
        if not isinstance(path, str) or len(path) > 4096:
            continue
        name = re.split(r'[/\\]', path.rstrip('/\\'))[-1]
        match = _RUN_NAME.fullmatch(name)
        if not match:
            continue
        try:
            moment = datetime.strptime(match[1] + match[2], '%Y%m%d%H%M%S')
            if match[3]:
                moment = moment.replace(microsecond=int(match[3].ljust(6, '0')))
        except ValueError:
            continue
        return _date(moment)
    return None


def _time(event, key):
    # Only fields explicitly carrying a full date are treated as absolute.
    # A bare numeric timestamp remains media time rather than guessed Unix time.
    for field in ('event_datetime', 'confirmed_at', 'confirmed_at_utc', 'recorded_at',
                  'recorded_at_utc', 'recorded_datetime', 'timestamp_utc', 'occurred_at'):
        value = _date(event.get(field))
        if value is not None:
            return 0, value
    position = _position(event)
    start = _run_start(event, key)
    if start is not None and math.isfinite(start + position):
        return 0, start + position
    return 1, position


def _device(event, key):
    device = _mapping(event.get('device'))
    fallback_id = key[0] if isinstance(key, tuple) and key else ''
    return tuple(str(value or '').casefold() for value in
                 (device.get('name', ''), device.get('location', ''), device.get('id', fallback_id)))


def _identity(event, key):
    value = event.get('event_id', key[2] if isinstance(key, tuple) and len(key) > 2 else None)
    if type(value) is int:
        return 0, value
    if value is None:
        return 1, 0
    return 2, str(value)


def _key_text(key):
    return json.dumps(key, ensure_ascii=False, sort_keys=True, default=str)


def _level(event):
    assessment = _mapping(event.get('assessment'))
    record = _mapping(event.get('scene_record'))
    risk = _mapping(event.get('risk_summary'))
    # `priority` is the optional board projection of the exact displayed final
    # result. Preserve saved/hysteretic grades; never derive them from a score.
    for value in (_mapping(event.get('priority')), _mapping(assessment.get('fusion')), assessment,
                  _mapping(record.get('priority')), _mapping(event.get('historical_priority')), event,
                  _mapping(event.get('baseline'))):
        level = value.get('level')
        if isinstance(level, str) and level in LEVEL_RANK:
            return LEVEL_RANK[level]
    peak = risk.get('peak_level')
    return LEVEL_RANK.get(peak, 0) if isinstance(peak, str) else 0


def _entries(events):
    if not isinstance(events, dict):
        raise ValueError('事件集合必须为字典。')
    return [(key, _mapping(event)) for key, event in events.items()]


def sorted_event_keys(events, mode='priority'):
    """Return original keys ordered by workflow/grade, record time or point.

    Priority places unfinished work before completed work, then compares the
    saved final grade. Within an unfinished grade, items awaiting action precede
    pending reviews. Remaining ties use the newest record position.
    """
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError('未知事件排序方式。')

    def ordering(pair):
        key, event = pair
        dated, timestamp = _time(event, key)
        tie = (_device(event, key), _identity(event, key), _key_text(key))
        if mode == 'time_asc':
            return dated, timestamp, *tie
        if mode == 'time_desc':
            return dated, -timestamp, *tie
        if mode == 'device':
            return _device(event, key), dated, -timestamp, _identity(event, key), _key_text(key)
        review = event.get('review')
        state = phase(review if isinstance(review, str) else 'pending')
        return int(state == 'completed'), -_level(event), int(state == 'pending'), dated, -timestamp, *tie

    return [key for key, _ in sorted(_entries(events), key=ordering)]


def enumerate_event_keys(events):
    """Number the complete collection once before applying display filters.

    Sorting/filtering a view never changes this mapping. Loading additional
    earlier records can change the chronological numbering of the collection.
    The display number is distinct from per-run detector event_id (including
    the None identifier of genuine AI scene notifications).
    """
    return {key: number for number, key in enumerate(sorted_event_keys(events, 'time_asc'), 1)}
