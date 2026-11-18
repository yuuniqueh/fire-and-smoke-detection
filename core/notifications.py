"""Local visual-event notifications; no external messaging or alarm actuation."""
from collections import Counter, OrderedDict, deque
from dataclasses import dataclass
import math
from core.alert_presentation import LEVEL_NAMES


LEVEL_RANK = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
LABEL_NAMES = {'fire': '火焰', 'smoke': '烟雾', 'scene': '场景异常线索'}


@dataclass(frozen=True)
class NotificationConfig:
    sound_enabled: bool = False
    repeat_cooldown_s: float = 15.0
    history_limit: int = 200

    def __post_init__(self):
        if not isinstance(self.sound_enabled, bool):
            raise ValueError('sound_enabled must be boolean')
        if (isinstance(self.repeat_cooldown_s, bool) or not isinstance(self.repeat_cooldown_s, (int, float))
                or not math.isfinite(self.repeat_cooldown_s) or self.repeat_cooldown_s < 0):
            raise ValueError('repeat_cooldown_s must be finite and nonnegative')
        if isinstance(self.history_limit, bool) or not isinstance(self.history_limit, int) or self.history_limit < 1:
            raise ValueError('history_limit must be an integer >= 1')


class NotificationEngine:
    """Emit state changes once, keep bounded local history and sound deduplication.

    Unchanged levels never generate per-frame reminders. Every state change is
    retained in the caller's log. The cooldown suppresses duplicate same-grade
    sounds (for example a repeated confirmation with the same event identity);
    a genuine level upgrade is never hidden by this cooldown. Tracker-level
    alert suppression and stale evidence always suppress sound.
    """
    def __init__(self, config=None):
        self.config = config or NotificationConfig()
        self.states = {}
        self.history = deque(maxlen=self.config.history_limit)
        self._last_audible = OrderedDict()
        self._counts = Counter()
        self._level_counts = Counter()
        self._sound_count = 0
        self._next_id = 1
        self.last_timestamp = None
        self.finished = False
        self.scene_state = None

    def update(self, assessment, snapshot):
        if self.finished:
            raise ValueError('notification stream already finished')
        timestamp = assessment['timestamp_s']
        self._validate_timestamp(timestamp, strictly=True)
        if (isinstance(snapshot['timestamp_s'], bool)
                or not isinstance(snapshot['timestamp_s'], (int, float))
                or not math.isfinite(snapshot['timestamp_s'])):
            raise ValueError('notification snapshot timestamp must be finite')
        if abs(snapshot['timestamp_s'] - timestamp) > 1e-8:
            raise ValueError('notification assessment and snapshot timestamps differ')
        events = assessment.get('events', [])
        event_ids = [event['event_id'] for event in events]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError('duplicate notification event id')
        self.last_timestamp = timestamp
        transitions = snapshot.get('transitions', [])
        confirmation = {item['event_id']: item for item in transitions if item.get('type') == 'event_confirmed'}
        closures = {item['event_id']: item for item in transitions if item.get('type') == 'event_closed'}
        tracks = {item['event_id']: item for item in snapshot.get('tracks', [])}
        notices = []
        for event_id in list(self.states):
            if event_id not in event_ids:
                state = self.states.pop(event_id)
                notices.append(self._notice(event_id, state, timestamp, 'event_closed',
                                            reason=closures.get(event_id, {}).get('reason', 'event_no_longer_active')))
        for event in events:
            event_id, label, level = event['event_id'], event['label'], event['level']
            if label not in ('fire', 'smoke') or level not in LEVEL_RANK:
                raise ValueError('unsupported notification class or level')
            state = self.states.get(event_id)
            signal = confirmation.get(event_id, tracks.get(event_id, {}))
            allowed = signal.get('alert_emitted', True) and not signal.get('cooldown_suppressed', False)
            evidence = {'score': event.get('score'), 'features': dict(event.get('features') or {}),
                        'score_components': dict(event.get('score_components') or {})}
            if state is None:
                state = {'label': label, 'level': level, 'sound_allowed': bool(allowed), **evidence}
                self.states[event_id] = state
                notices.append(self._notice(event_id, state, timestamp, 'event_confirmed',
                                            fresh=event.get('fresh_evidence', False)))
            else:
                if state['label'] != label:
                    raise ValueError('notification event class cannot change')
                state.update(evidence)
                if event_id in confirmation:
                    state['sound_allowed'] = state['sound_allowed'] and bool(allowed)
                previous = state['level']
                if previous != level:
                    state['level'] = level
                    kind = 'level_upgrade' if LEVEL_RANK[level] > LEVEL_RANK[previous] else 'level_downgrade'
                    notices.append(self._notice(event_id, state, timestamp, kind, previous_level=previous,
                                                fresh=event.get('fresh_evidence', False)))
        info = assessment.get('fusion') or {}
        scene_active = (info.get('scene_only_hazard') is True and assessment.get('level') in LEVEL_RANK
                        and assessment.get('fresh_evidence') is True)
        if scene_active:
            scene = {'label': 'scene', 'level': assessment['level'], 'score': assessment.get('score'),
                     'sound_allowed': True, 'origin': 'ai_scene',
                     'scene_record_key': info.get('scene_record_key'),
                     'evidence_reference_s': info.get('evidence_reference_s'),
                     'description': info.get('description', '')}
            if self.scene_state is None:
                notices.append(self._notice(None, scene, timestamp, 'scene_attention', fresh=True))
            elif self.scene_state['level'] != scene['level']:
                previous = self.scene_state['level']
                kind = 'level_upgrade' if LEVEL_RANK[scene['level']] > LEVEL_RANK[previous] else 'level_downgrade'
                notices.append(self._notice(None, scene, timestamp, kind, previous_level=previous, fresh=True))
            self.scene_state = scene
        elif self.scene_state is not None:
            notices.append(self._notice(None, self.scene_state, timestamp, 'event_closed',
                                        reason='scene_evidence_no_longer_current'))
            self.scene_state = None
        return notices

    def _notice(self, event_id, state, timestamp, kind, previous_level=None, fresh=False, reason=None):
        level, label = state['level'], state['label']
        grade, subject = LEVEL_NAMES[level], LABEL_NAMES[label]
        channels = ['panel', 'log']
        sound_suppressed_reason = None
        eligible = kind in ('event_confirmed', 'scene_attention', 'level_upgrade') and level in ('MEDIUM', 'HIGH')
        if eligible and self.config.sound_enabled:
            key = (event_id, level)
            previous_sound = self._last_audible.get(key)
            if not fresh:
                sound_suppressed_reason = 'stale_evidence'
            elif not state['sound_allowed']:
                sound_suppressed_reason = 'tracker_cooldown'
            elif (kind != 'level_upgrade' and previous_sound is not None
                  and timestamp - previous_sound < self.config.repeat_cooldown_s):
                sound_suppressed_reason = 'repeat_cooldown'
            else:
                channels.append('sound')
                self._last_audible[key] = timestamp
                self._last_audible.move_to_end(key)
                while len(self._last_audible) > self.config.history_limit:
                    self._last_audible.popitem(last=False)
        if label == 'scene':
            title = ('场景异常线索观察已结束' if kind == 'event_closed' else f'综合研判 · {grade}')
            message = ('当前场景异常线索已过期、更新或观察结束，不代表现场已安全。' if kind == 'event_closed' else
                       'AI记录了可见异常线索，当前烟火检测尚无确认事件；请查看原画面并记录核查结果。')
        elif kind == 'event_confirmed':
            title = f'检测到{subject} · {grade}'
            message = f'同一区域多次检测到{subject}特征，请查看画面并记录核查结果；现场情况需人工核实。'
        elif kind == 'level_upgrade':
            title = f'检测到{subject} · {grade}'
            message = f'核查优先程度从“{LEVEL_NAMES[previous_level]}”调整为“{grade}”，请查看最新画面与检测依据。'
        elif kind == 'level_downgrade':
            title = f'{subject}提示调整 · {grade}'
            message = f'当前核查优先程度为“{grade}”；提示变化不代表已排除火情。'
        else:
            title = f'本次{subject}持续检测已结束'
            message = (f'事件 #{event_id} 的本次观察已结束（{reason}）；'
                       '这表示事件跟踪或视频结束，不代表现场已安全。')
        notice = {
            'notification_id': f'notice-{self._next_id:06d}', 'event_id': event_id,
            'label': label, 'level': level, 'timestamp_s': timestamp, 'kind': kind,
            'title': title, 'message': message, 'channels': channels,
            'previous_level': previous_level, 'close_reason': reason,
            'sound_suppressed_reason': sound_suppressed_reason,
            'score': state.get('score'), 'features': dict(state.get('features') or {}),
            'score_components': dict(state.get('score_components') or {}),
        }
        if state.get('origin') == 'ai_scene':
            notice.update(origin='ai_scene', scene_record_key=state.get('scene_record_key'),
                          evidence_reference_s=state.get('evidence_reference_s'), description=state.get('description', ''))
        self._next_id += 1
        self.history.append(notice)
        self._counts[kind] += 1
        self._level_counts[level] += 1
        if 'sound' in channels:
            self._sound_count += 1
        return notice

    def _validate_timestamp(self, timestamp, strictly):
        if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp) or timestamp < 0):
            raise ValueError('notification timestamp must be finite and nonnegative')
        if self.last_timestamp is not None and (timestamp < self.last_timestamp or (strictly and timestamp == self.last_timestamp)):
            raise ValueError('notification timestamps must increase')

    def finish(self, timestamp_s, reason='stream_end'):
        self._validate_timestamp(timestamp_s, strictly=False)
        if self.finished:
            return []
        notices = [self._notice(event_id, state, timestamp_s, 'event_closed', reason=reason)
                   for event_id, state in self.states.items()]
        if self.scene_state is not None:
            notices.append(self._notice(None, self.scene_state, timestamp_s, 'event_closed', reason=reason))
            self.scene_state = None
        self.states.clear()
        self.last_timestamp = timestamp_s
        self.finished = True
        return notices

    def summary(self):
        return {
            'total_notifications': self._next_id - 1, 'active_events': len(self.states),
            'active_scene_attention': self.scene_state is not None,
            'by_kind': dict(self._counts), 'by_level': dict(self._level_counts),
            'history_retained': len(self.history), 'history_limit': self.config.history_limit,
            'sound_enabled': self.config.sound_enabled, 'sound_requests': self._sound_count,
            'note': '本地视觉核查提示，不发送外部消息，不执行消防设备控制。',
        }
