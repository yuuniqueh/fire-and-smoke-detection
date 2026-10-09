"""Configurable, explainable heuristic index; not a calibrated probability."""
from dataclasses import asdict, dataclass
import math

from core.features import FeatureExtractor

LEVEL_RANK = {'NORMAL': 0, 'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}


@dataclass(frozen=True)
class RiskConfig:
    smoothing_tau_s: float = 0.75
    trend_window_s: float = 1.5
    trend_deadband_pp_s: float = 0.3
    coverage_full_scale: float = 0.2
    duration_full_scale_s: float = 30.0
    growth_full_scale_pp_s: float = 3.0
    confidence_floor: float = 0.35
    fire_base: float = 25.0
    smoke_base: float = 15.0
    coverage_weight: float = 25.0
    duration_weight: float = 20.0
    growth_weight: float = 20.0
    confidence_weight: float = 10.0
    medium_enter: float = 45.0
    medium_exit: float = 38.0
    high_enter: float = 70.0
    high_exit: float = 62.0
    upgrade_hold_s: float = 0.5
    downgrade_hold_s: float = 1.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be finite')
            if value < 0:
                raise ValueError(f'{name} cannot be negative')
        for name in ('smoothing_tau_s', 'trend_window_s', 'coverage_full_scale', 'duration_full_scale_s', 'growth_full_scale_pp_s'):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if not 0 < self.coverage_full_scale <= 1 or not 0 <= self.confidence_floor < 1:
            raise ValueError('invalid coverage scale/confidence floor')
        if not 0 <= self.medium_exit < self.medium_enter < self.high_exit < self.high_enter <= 100:
            raise ValueError('risk thresholds must be ordered: medium exit < enter < high exit < enter')
        weights = self.coverage_weight + self.duration_weight + self.growth_weight + self.confidence_weight
        if max(self.fire_base, self.smoke_base) + weights > 100 + 1e-9:
            raise ValueError('base and maximum component sum cannot exceed 100')


def clamp01(value):
    return max(0.0, min(1.0, value))


def score_features(features, config):
    """Score only fresh, confirmed visual evidence; caller gates confirmation."""
    coverage = features['coverage_ratio_smoothed']
    confidence = features['confidence']
    if not features['fresh_evidence'] or coverage is None or confidence is None:
        raise ValueError('fresh image evidence required for scoring')
    growth = features['growth_pp_per_s']
    values = (coverage, confidence, features['positive_duration_s'])
    if not all(math.isfinite(v) for v in values) or (growth is not None and not math.isfinite(growth)):
        raise ValueError('risk features must be finite')
    if not 0 <= coverage <= 1 or not 0 <= confidence <= 1 or features['positive_duration_s'] < 0:
        raise ValueError('invalid risk feature range')
    if features['label'] not in ('fire', 'smoke'):
        raise ValueError('unsupported risk class')
    components = {
        'class_base': config.fire_base if features['label'] == 'fire' else config.smoke_base,
        'box_coverage': config.coverage_weight * clamp01(coverage / config.coverage_full_scale),
        'positive_duration': config.duration_weight * clamp01(features['positive_duration_s'] / config.duration_full_scale_s),
        'positive_growth': config.growth_weight * clamp01(max(0.0, (growth or 0) - config.trend_deadband_pp_s) / config.growth_full_scale_pp_s),
        'model_confidence': config.confidence_weight * clamp01((confidence - config.confidence_floor) / (1 - config.confidence_floor)),
    }
    reasons = [
        f'Confirmed {features["label"]} visual event.',
        f'Smoothed box-union coverage {coverage:.1%}.',
        f'Positive observation time {features["positive_duration_s"]:.2f}s.',
        f'Model confidence {confidence:.2f}; not a probability of fire.',
    ]
    if growth is None:
        reasons.append('Coverage trend warming up; no growth contribution yet.')
    else:
        reasons.append(f'Coverage trend {features["trend"]}: {growth:+.2f} percentage points/s.')
    return round(sum(components.values()), 4), {key: round(value, 4) for key, value in components.items()}, reasons


class LevelController:
    """Independent time-based level hysteresis, held while evidence is stale."""
    def __init__(self, config):
        self.config = config
        self.level = 'LOW'
        self.pending_target = None
        self.pending_since = None
        self.last_timestamp = None

    def target(self, score):
        c = self.config
        if score >= c.high_enter or (self.level == 'HIGH' and score >= c.high_exit):
            return 'HIGH'
        if score >= c.medium_enter or (self.level in ('MEDIUM', 'HIGH') and score >= c.medium_exit):
            return 'MEDIUM'
        return 'LOW'

    def update(self, timestamp, score, fresh=True):
        if not math.isfinite(timestamp) or timestamp < 0 or not math.isfinite(score) or not 0 <= score <= 100:
            raise ValueError('invalid level-controller input')
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            raise ValueError('level timestamps must increase strictly')
        self.last_timestamp = timestamp
        if not fresh:
            self.pending_target = self.pending_since = None
            return None
        target = self.target(score)
        if target == self.level:
            self.pending_target = self.pending_since = None
            return None
        if target != self.pending_target:
            self.pending_target, self.pending_since = target, timestamp
        hold = self.config.upgrade_hold_s if LEVEL_RANK[target] > LEVEL_RANK[self.level] else self.config.downgrade_hold_s
        if timestamp - self.pending_since + 1e-9 >= hold:
            previous = self.level
            self.level = target
            self.pending_target = self.pending_since = None
            return {'from': previous, 'to': target, 'timestamp_s': timestamp, 'score': score}
        return None


class RiskEngine:
    def __init__(self, config=None):
        self.config = config or RiskConfig()
        self.extractor = FeatureExtractor(self.config.smoothing_tau_s, self.config.trend_window_s, self.config.trend_deadband_pp_s)
        self.states = {}
        self.event_summaries = []
        self.peak_level = 'NORMAL'
        self.peak_score = 0.0
        self.last_timestamp = None
        self.finished = False

    def _archive(self, event_id, timestamp, reason):
        state = self.states.pop(event_id)
        self.event_summaries.append({
            'event_id': event_id, 'label': state['label'], 'peak_level': state['peak_level'],
            'peak_score': state['peak_score'], 'last_level': state['controller'].level,
            'last_score': state['last']['score'], 'last_fresh_timestamp_s': state['last_fresh_timestamp_s'],
            'level_changes': state['level_changes'], 'peak_evidence': state['peak_evidence'],
            'closed_at_s': timestamp, 'close_reason': reason,
        })

    def update(self, snapshot, detections, frame_size):
        if self.finished:
            raise ValueError('risk stream already finished')
        timestamp = snapshot['timestamp_s']
        features = self.extractor.update(timestamp, detections, frame_size, snapshot['tracks'])
        self.last_timestamp = timestamp
        active_confirmed = {t['event_id'] for t in snapshot['tracks'] if t['state'] == 'confirmed'}
        closure_reasons = {item['event_id']: item.get('reason', 'event_no_longer_active')
                           for item in snapshot.get('transitions', []) if item['type'] == 'event_closed'}
        for event_id in list(self.states):
            if event_id not in active_confirmed:
                self._archive(event_id, timestamp, closure_reasons.get(event_id, 'event_no_longer_active'))
        results, changes = [], []
        for track in snapshot['tracks']:
            if track['state'] != 'confirmed':
                continue
            event_id = track['event_id']
            feature = features[event_id]
            is_new = event_id not in self.states
            state = self.states.setdefault(event_id, {
                'label': track['label'], 'controller': LevelController(self.config), 'last': None,
                'peak_level': 'LOW', 'peak_score': 0.0, 'peak_evidence': None,
                'last_fresh_timestamp_s': None, 'level_changes': [],
            })
            if feature['fresh_evidence']:
                score, components, reasons = score_features(feature, self.config)
                state['last_fresh_timestamp_s'] = timestamp
            elif state['last']:
                score = state['last']['score']
                components = state['last']['score_components'].copy()
                reasons = [f'Temporary detection gap {feature["missing_for_s"]:.2f}s; holding last assessed level and score.']
            else:
                raise ValueError('confirmed event has no prior fresh risk evidence')
            controller = state['controller']
            change = controller.update(timestamp, score, feature['fresh_evidence'])
            if is_new:
                change = {'from': 'NORMAL', 'to': controller.level, 'timestamp_s': timestamp, 'score': score}
            if change:
                change = {'event_id': event_id, 'label': track['label'], **change,
                          'features': feature.copy(), 'score_components': components.copy(), 'reasons': reasons.copy()}
                changes.append(change)
                state['level_changes'].append(change)
            if controller.pending_target:
                reasons.append(f'Level change to {controller.pending_target} awaiting sustained evidence.')
            assessment = {
                'event_id': event_id, 'label': track['label'], 'level': controller.level,
                'score': score, 'score_components': components, 'features': feature,
                'reasons': reasons, 'fresh_evidence': feature['fresh_evidence'],
                'pending_target': controller.pending_target,
                'last_fresh_timestamp_s': state['last_fresh_timestamp_s'],
            }
            state['last'] = assessment
            if state['peak_evidence'] is None or score > state['peak_score']:
                state['peak_score'] = score
                state['peak_evidence'] = {'timestamp_s': timestamp, 'components': components.copy(), 'features': feature.copy()}
            if LEVEL_RANK[controller.level] > LEVEL_RANK[state['peak_level']]:
                state['peak_level'] = controller.level
            if LEVEL_RANK[controller.level] > LEVEL_RANK[self.peak_level]:
                self.peak_level = controller.level
            self.peak_score = max(self.peak_score, score)
            results.append(assessment)
        dominant = max(results, key=lambda r: (LEVEL_RANK[r['level']], r['score'])) if results else None
        return {
            'timestamp_s': timestamp, 'status': 'assessed' if dominant else 'awaiting_confirmation' if snapshot['tracks'] else 'no_confirmed_event',
            'level': dominant['level'] if dominant else 'NORMAL', 'score': dominant['score'] if dominant else 0.0,
            'dominant_event_id': dominant['event_id'] if dominant else None,
            'fresh_evidence': dominant['fresh_evidence'] if dominant else False,
            'reasons': dominant['reasons'] if dominant else ['Awaiting event confirmation.'] if snapshot['tracks'] else ['No confirmed visual event.'],
            'events': results, 'level_changes': changes,
            'index_note': 'Heuristic visual index (0-100), not a calibrated fire probability or safety certification.',
        }

    def finish(self, timestamp, reason='stream_end'):
        if not math.isfinite(timestamp) or timestamp < (self.last_timestamp or 0):
            raise ValueError('invalid risk stream end timestamp')
        for event_id in list(self.states):
            self._archive(event_id, timestamp, reason)
        self.finished = True
        return self.summary()

    def summary(self):
        return {'peak_level': self.peak_level, 'peak_score': self.peak_score,
                'assessed_events': len(self.event_summaries) + len(self.states),
                'index_note': 'A heuristic visual index, not a probability.'}


def draw_risk_panel(frame, snapshot):
    """Append a readable status panel without covering the video."""
    import cv2
    import numpy as np
    panel_width = 360
    height, width = frame.shape[:2]
    canvas = np.full((max(height, 520), width + panel_width, 3), (24, 24, 24), dtype=np.uint8)
    canvas[:height, :width] = frame
    x = width + 18
    colors = {'NORMAL': (110, 230, 110), 'LOW': (110, 230, 210), 'MEDIUM': (40, 190, 255), 'HIGH': (70, 90, 255)}
    color = colors[snapshot['level']]
    title = 'PENDING' if snapshot['status'] == 'awaiting_confirmation' else snapshot['level']
    def text(value, y, scale=0.48, tint=(220, 220, 220), thick=1):
        cv2.putText(canvas, value, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, tint, thick)
    text('VISUAL RISK INDICATORS', 28, 0.59, (240, 240, 240), 2)
    text(title, 70, 0.88, color, 2)
    score_text = f'Index: {snapshot["score"]:.1f} / 100' if snapshot['events'] else 'Index: not assessed' if title == 'PENDING' else 'No confirmed visual event'
    text(score_text, 99, 0.58, color)
    text('Heuristic index; not fire probability', 124, 0.42, (170, 170, 170))
    if snapshot['events']:
        event = next(e for e in snapshot['events'] if e['event_id'] == snapshot['dominant_event_id'])
        feature = event['features']
        text(f'Event #{event["event_id"]} | {event["label"]}', 162, 0.54)
        text('Evidence: fresh' if event['fresh_evidence'] else 'Evidence: temporarily missing', 187, 0.44)
        text(f'Box union: {(feature["coverage_ratio_smoothed"] or 0):.1%}', 215)
        text(f'Positive time: {feature["positive_duration_s"]:.2f}s', 240)
        text(f'Trend: {feature["trend"]}', 265)
        growth = feature['growth_pp_per_s']
        text(f'Change: {growth:+.2f} pp/s' if growth is not None else 'Change: not available', 290)
        text('Score contributions', 325, 0.49, (240, 240, 240), 2)
        labels = {'class_base': 'Confirmed class', 'box_coverage': 'Box coverage', 'positive_duration': 'Observation time', 'positive_growth': 'Coverage growth', 'model_confidence': 'Model confidence'}
        for index, (key, value) in enumerate(event['score_components'].items()):
            text(f'{labels[key]}: +{value:.1f}', 350 + index * 24, 0.44)
        if event['pending_target']:
            text(f'Level change to {event["pending_target"]} pending', 486, 0.44, color)
        elif not event['fresh_evidence']:
            text('Holding previous assessment', 486, 0.44, color)
        else:
            text('Confirmed event + measured indicators', 486, 0.42)
    else:
        text('Waiting for sustained visual evidence.' if title == 'PENDING' else 'No active confirmed event to assess.', 180, 0.43)
    return canvas
