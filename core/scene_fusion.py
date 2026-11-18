"""Join asynchronous, validated scene observations to current visual evidence."""
from copy import deepcopy
from threading import Lock
import math

from core.scene_review import contextual_priority, validate_scene


class SceneContextStore:
    """One store per video run; neither Qt objects nor partial text cross threads."""
    def __init__(self):
        self._lock = Lock()
        self._record = None
        self.enabled = True

    def publish(self, record):
        value = deepcopy(record)
        frames, span = value.get('frame_count'), value.get('span_s')
        evidence = value.get('evidence')
        if (type(frames) is not int or not 1 <= frames <= 64 or not isinstance(evidence, list) or len(evidence) != frames
                or type(span) not in (int, float) or not math.isfinite(span) or span < 0):
            raise ValueError('场景原画面数量或时间跨度无效。')
        stamps = [item.get('timestamp_s') if isinstance(item, dict) else None for item in evidence]
        if (any(type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0 for stamp in stamps)
                or stamps != sorted(set(stamps)) or not math.isclose(span, stamps[-1] - stamps[0], rel_tol=0., abs_tol=.002)):
            raise ValueError('场景融合需要可核对的原画面时间。')
        value['scene'] = validate_scene(value['scene'], frames, span)
        with self._lock:
            if self._record is None or stamps[-1] >= self._record['evidence'][-1]['timestamp_s']:
                self._record = value

    def snapshot(self):
        with self._lock:
            return deepcopy(self._record) if self.enabled else None


class SceneFusionEngine:
    """One dynamic review index; raw evidence and untrained rules stay auditable."""
    def __init__(self, store, *, shadow_model_path=None):
        from core.fusion_shadow import FusionShadow
        from core.risk import RiskConfig
        self.store = store
        self.previous = {}
        self.peaks = {}
        self.latest = None
        self._scene_availability = {}
        self.shadow = FusionShadow()
        self.shadow.status = 'disabled_by_current_policy'
        self.risk_config = RiskConfig()
        self.controllers = {}
        self.scene_controller = None
        self.overall_peak = {'peak_level': 'NORMAL', 'peak_score': 0.}


    def update(self, assessment):
        result = deepcopy(assessment)
        record = self.store.snapshot()
        timestamp = assessment['timestamp_s']
        reference = record['evidence'][-1]['timestamp_s'] if record else None
        age = timestamp - reference if reference is not None else None
        fresh = record is not None and 0 <= age <= 12.
        record_key, available_at = None, None
        if record:
            from core.fusion_dataset import scene_record_key
            record_key = scene_record_key(record)
            # First actual consumption is a causal upper bound on availability.
            # Wall inference duration cannot be added to media time when playback
            # may run faster or slower than real time.
            if fresh:
                available_at = self._scene_availability.setdefault(record_key, timestamp)
            while len(self._scene_availability) > 32:
                self._scene_availability.pop(next(iter(self._scene_availability)))
        from core.independent_review import independent_review
        evidence = independent_review(assessment, record)
        valid_scene = evidence['scene']['status'] in ('anomaly', 'controlled', 'uncertain')
        changes = []
        for event in result['events']:
            base = deepcopy(event)
            matches = record and record.get('event_id') in (None, event['event_id'])
            if matches and valid_scene:
                priority = contextual_priority(base, record['scene'], frame_count=record['frame_count'],
                                               span_s=record['span_s'], age_s=age, active_event_count=len(result['events']),
                                               risk_config=self.risk_config)
                # A scene containing a candle cannot explain a separate fire
                # elsewhere. Multiple active targets require individual review.
                if priority['context_delta'] < 0 and len(result['events']) != 1:
                    priority = self._unchanged(base, '存在多个烟火事件，场景线索不能逐一对应，保留检测等级。')
            else:
                priority = self._unchanged(base, '等待本路视频的完整AI场景结果，沿用检测等级。')
            from core.risk import LevelController
            controller = self.controllers.get(event['event_id'])
            if controller is None:
                controller = LevelController(self.risk_config)
                controller.level = base['level']
                self.controllers[event['event_id']] = controller
            if priority['context_delta'] == 0:
                controller.level = base['level']
                controller.pending_target = controller.pending_since = None
                controller.last_timestamp = timestamp
            else:
                controller.update(timestamp, priority['score'], fresh=base.get('fresh_evidence', True))
                if priority['context_delta'] > 0:
                    controller.level = max((controller.level, base['level']), key=self._rank)
                else:
                    raw_rank = self._rank(base['level'])
                    rank = max(raw_rank - 1, min(raw_rank, self._rank(controller.level)))
                    controller.level = ('NORMAL', 'LOW', 'MEDIUM', 'HIGH')[rank]
            priority['level'] = controller.level
            priority['pending_target'] = controller.pending_target
            event['pending_target'] = controller.pending_target
            event.update(score=priority['score'], level=priority['level'],
                         baseline_score=base['score'], baseline_level=base['level'], fusion=priority)
            event['score_components'] = {**base['score_components'], 'scene_context': priority['context_delta']}
            event['reasons'] = [*base['reasons'], priority['reason']]
            old_level = self.previous.get(event['event_id'], 'NORMAL')
            if old_level != event['level']:
                changes.append({'event_id': event['event_id'], 'label': event['label'], 'from': old_level,
                                'to': event['level'], 'timestamp_s': timestamp, 'score': event['score']})
            self.previous[event['event_id']] = event['level']
            peak = self.peaks.setdefault(event['event_id'], {'event_id': event['event_id'], 'peak_score': 0., 'peak_level': 'LOW'})
            peak['peak_score'] = max(peak['peak_score'], event['score'])
            if self._rank(event['level']) > self._rank(peak['peak_level']):
                peak['peak_level'] = event['level']
        if result['events']:
            dominant = max(result['events'], key=lambda event: (self._rank(event['level']), event['score']))
            result.update(score=dominant['score'], level=dominant['level'], dominant_event_id=dominant['event_id'],
                          reasons=dominant['reasons'])
        active_ids = {event['event_id'] for event in result['events']}
        self.controllers = {key: value for key, value in self.controllers.items() if key in active_ids}
        self.previous = {event['event_id']: event['level'] for event in result['events']}
        scene_only = False
        if not result['events'] and valid_scene and evidence['scene']['status'] == 'anomaly':
            from core.context_scoring import scene_only_score
            from core.risk import LevelController
            priority = scene_only_score(record['scene'], frame_count=record['frame_count'],
                                        span_s=record['span_s'], age_s=age)
            if priority['active']:
                if self.scene_controller is None:
                    self.scene_controller = LevelController(self.risk_config)
                priority['score'] = min(priority['score'], self.risk_config.high_enter - .01)
                self.scene_controller.update(timestamp, priority['score'])
                if self.scene_controller.level == 'HIGH':
                    self.scene_controller.level = 'MEDIUM'
                scene_only = True
                result.update(score=priority['score'], level=self.scene_controller.level,
                              status='scene_attention', fresh_evidence=True, reasons=[priority['reason']])
            else:
                self.scene_controller = None
        else:
            self.scene_controller = None

        result['level_changes'] = changes
        result['fusion'] = {'policy': 'scene-score-v2', 'baseline_score': assessment['score'],
            'baseline_level': assessment['level'], 'scene_status': 'current' if fresh and valid_scene else 'expired' if age is not None and age > 12. else 'invalid' if record else 'pending',
            'evidence_reference_s': reference, 'age_s': round(age, 3) if age is not None else None,
            'scene_record_key': record_key, 'scene_available_at_s': available_at,
            'description': record['scene']['description'] if record else '',
            'scene': record['scene']['scene'] if record else None,
            'scene_only_hazard': scene_only,
            'context_delta': round(result['score'] - assessment['score'], 2),
            'experimental': True, 'original_detection_retained': True}
        result['fusion']['score_kind'] = 'review_priority_index'
        result['fusion']['trained'] = False
        result['fusion']['calibrated'] = False
        result['adaptive_shadow'] = self.shadow.metadata()
        self.overall_peak['peak_score'] = max(self.overall_peak['peak_score'], result['score'])
        if self._rank(result['level']) > self._rank(self.overall_peak['peak_level']):
            self.overall_peak['peak_level'] = result['level']
        self.latest = result
        return result

    @staticmethod
    def _rank(level):
        return {'NORMAL': 0, 'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}[level]

    @staticmethod
    def _unchanged(base, reason):
        return {'policy': 'context-score-v2', 'baseline_score': base['score'], 'baseline_level': base['level'], 'score': base['score'],
                'level': base['level'], 'context_delta': 0., 'reason': reason, 'experimental': True,
                'original_alert_retained': True}

    def summary(self):
        return {**self.overall_peak, 'assessed_events': len(self.peaks),
                'policy': 'scene-score-v2', 'experimental': True, 'trained': False, 'calibrated': False,
                'adaptive_shadow': self.shadow.metadata()}
