"""Keep smoke/fire detection and complete AI scene observations independent.

The decision describes which evidence merits human review. It neither changes
the detector's index/grade nor treats an AI description as a confirmed fire.
"""
from copy import deepcopy
import math

from core.fusion_dataset import scene_record_key
from core.scene_review import validate_scene


POLICY = 'independent-review-v1'
CONTROLLED_SCENES = frozenset(('candle', 'cooking', 'fireplace'))


def _finite(value, *, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('时间或数值必须是有限数字。')
    if nonnegative and value < 0:
        raise ValueError('时间不能为负数。')
    return float(value)


def _detector(assessment):
    events = assessment.get('events')
    if events is None:
        events = []
    if not isinstance(events, list) or len(events) > 100:
        raise ValueError('检测事件列表无效。')
    if any(not isinstance(event, dict) or type(event.get('event_id')) is not int or event['event_id'] < 1
           for event in events):
        raise ValueError('已确认检测事件缺少有效编号。')
    score = assessment.get('score')
    if score is not None:
        _finite(score)
    status = ('confirmed' if events else 'not_observed' if assessment.get('status') == 'not_observed'
              else 'unconfirmed')
    return {'status': status, 'score': score, 'level': assessment.get('level'),
            'event_count': len(events)}, {event['event_id'] for event in events}


def _scene(record, event_ids, now, max_age_s):
    result = {'status': 'pending', 'description': '', 'scene': None, 'hazard_cues': [],
              'reference_s': None, 'age_s': None, 'record_key': None}
    if record is None:
        return result
    result['status'] = 'invalid'
    if not isinstance(record, dict):
        return result
    try:
        frames = record.get('evidence')
        frame_count = record.get('frame_count')
        span = _finite(record.get('span_s'), nonnegative=True)
        if not isinstance(frames, list) or not 1 <= len(frames) <= 16:
            return result
        if type(frame_count) is not int or frame_count != len(frames):
            return result
        stamps = [_finite(frame['timestamp_s'], nonnegative=True) for frame in frames]
        if stamps != sorted(set(stamps)):
            return result
        actual_span = stamps[-1] - stamps[0]
        # Stored spans are rounded to milliseconds by the existing scene client.
        if not math.isclose(span, actual_span, rel_tol=0., abs_tol=.002):
            return result
        scene = validate_scene(record.get('scene'), frame_count=frame_count, span_s=actual_span)
        result.update(description=scene['description'], scene=scene['scene'],
                      hazard_cues=deepcopy(scene['hazard_cues']), reference_s=stamps[-1],
                      record_key=scene_record_key(record))
        now = _finite(now, nonnegative=True)
        age = now - stamps[-1]
        result['age_s'] = round(age, 6)
        record_event = record.get('event_id')
        if record_event is not None and (type(record_event) is not int or record_event not in event_ids):
            return result
        if age < 0:
            return result
        if record.get('available_at_s') is not None:
            ready = _finite(record['available_at_s'], nonnegative=True)
            if ready < stamps[-1] or ready > now:
                return result
        if age > max_age_s:
            result['status'] = 'expired'
            return result
        if frame_count < 2 or actual_span < .8:
            result['status'] = 'uncertain'
            return result
        if (scene['hazard_cues'] or scene['spread'] == 'observed' or
                (scene['relation'] == 'suspected_abnormal' and scene['visible_evidence'])):
            result['status'] = 'anomaly'
        elif (scene['scene'] in CONTROLLED_SCENES and scene['relation'] == 'expected_burning'
              and scene['evidence_quality'] == 'adequate' and scene['spread'] == 'not_observed'
              and scene['visible_evidence']):
            result['status'] = 'controlled'
        else:
            result['status'] = 'uncertain'
    except (KeyError, TypeError, ValueError, OverflowError):
        # Incomplete text, failed observations and malformed metadata never
        # veto an existing detector event or become an AI attention signal.
        result['status'] = 'invalid'
    return result


def _decision(detector, scene):
    detected = detector['event_count'] > 0
    status = scene['status']
    if detected and status == 'anomaly':
        return {'status': 'corroborated', 'attention': True, 'title': '两路均提示需要核查',
                'reason': '烟火检测已确认视觉事件，AI也记录了异常线索。请人工核查；两路相符不等于已确认火灾。',
                'sources': ['detector', 'scene']}
    if detected and status == 'controlled':
        return {'status': 'disagreement', 'attention': True, 'title': '检测与AI判断不一致，请核查',
                'reason': '烟火检测已确认视觉事件，AI提示受控用火。保留原检测指数与等级，请人工核对火源及周边变化。',
                'sources': ['detector', 'scene']}
    if detected:
        context = {'pending': 'AI画面分析尚未完成', 'expired': 'AI画面分析已过期',
                   'invalid': 'AI画面或事件对应关系未通过核对', 'uncertain': 'AI证据不足以判断异常'}[status]
        return {'status': 'detector_attention', 'attention': True, 'title': '烟火检测提示，请核查',
                'reason': f'{context}，保留原检测指数与等级；请人工核查已确认的烟火视觉事件。',
                'sources': ['detector']}
    if status == 'anomaly':
        return {'status': 'scene_attention', 'attention': True, 'title': 'AI发现异常线索，请核查',
                'reason': '当前烟火模型尚未确认视觉事件，AI记录了可见异常线索。请人工核查，这不是已确认火灾。',
                'sources': ['scene']}
    if status == 'controlled':
        return {'status': 'clear', 'attention': False, 'title': '当前未确认烟火，AI提示受控用火',
                'reason': '当前烟火检测没有已确认事件，AI多帧画面提示受控用火；继续观察，不据此判断现场安全。',
                'sources': ['scene']}
    reason = {'pending': '等待完整AI画面分析，当前烟火检测没有已确认事件。',
              'expired': 'AI画面分析已过期，等待新的完整画面；当前烟火检测没有已确认事件。',
              'invalid': 'AI画面或事件对应关系未通过核对，等待有效分析；当前烟火检测没有已确认事件。',
              'uncertain': 'AI证据不足以判断异常，当前烟火检测没有已确认事件。'}[status]
    return {'status': 'pending', 'attention': False, 'title': '等待有效核查线索', 'reason': reason, 'sources': []}


def independent_review(assessment, record, *, timestamp_s=None, max_age_s=12.):
    """Return separate observations plus a human-review decision, without score fusion."""
    if not isinstance(assessment, dict):
        raise ValueError('检测结果必须是对象。')
    max_age = _finite(max_age_s, nonnegative=True)
    # Scene evidence cannot be made current indefinitely by a caller override.
    max_age = min(12., max_age)
    detector, event_ids = _detector(assessment)
    now = assessment.get('timestamp_s') if timestamp_s is None else timestamp_s
    scene = _scene(record, event_ids, now, max_age)
    return {'policy': POLICY, 'detector': detector, 'scene': scene,
            'decision': _decision(detector, scene), 'experimental': True, 'scores_combined': False}
