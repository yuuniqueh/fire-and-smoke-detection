"""Development-only, trainable scene residual; inference uses the standard library.

The sigmoid output is NOT a calibrated fire probability.  Candidate scores never
replace detector evidence or send notifications.  A v1 artifact contains the exact
feature list, coefficients/intercept, unvalidated residual limits and training
source groups.  Training labels must come from human-reviewed independent events.
"""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path

from core.scene_review import SCENES, validate_scene


FEATURE_VERSION = 'adaptive-fusion-v1'
ARTIFACT_TYPE = 'fireguard.adaptive_fusion'
DETECTOR_FEATURES = (
    'detector_score', 'detector_coverage', 'detector_duration', 'detector_growth',
    'detector_growth_missing', 'detector_confidence', 'detector_smoke',
)
SCENE_FEATURES = tuple('scene_' + name for name in SCENES) + (
    'scene_expected', 'scene_abnormal', 'scene_unclear', 'scene_spread',
    'scene_no_spread', 'scene_limited', 'scene_hazard', 'scene_uncertainty',
)
INTERACTION_FEATURES = (
    'controlled_x_growth', 'controlled_x_smoke', 'controlled_x_duration',
    'abnormal_x_small_fire', 'spread_x_growth', 'hazard_x_growth',
)
FEATURE_NAMES = DETECTOR_FEATURES + SCENE_FEATURES + INTERACTION_FEATURES
_SCENE_FIELDS = {'description', 'scene', 'relation', 'evidence_quality', 'spread',
                 'visible_evidence', 'hazard_cues', 'uncertainties', 'next_check'}
_CONTROLLED_SCENES = {'candle', 'cooking', 'fireplace'}


def _number(value, name, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{name} is out of range')
    return float(value)


def _integer(value, name, minimum=1):
    if type(value) is not int or value < minimum:
        raise ValueError(f'{name} must be an integer >= {minimum}')
    return value


def _unit(value):
    return max(0., min(1., value))


def _raw_score(baseline):
    return _number(baseline['score'], 'baseline score', minimum=0., maximum=100.)


def _unavailable(reason):
    return {'eligible': False, 'reason': reason, 'gate': 0., 'values': {}, 'blockers': [reason]}


def build_features(baseline, record, *, timestamp_s, active_event_count=1):
    """Validate per-event evidence and build normalized, versioned features.

    Freshness and frame geometry are objective gates.  Qwen's ``adequate`` is a
    categorical opinion, never a probability.  Hazard presence is binary rather
    than proportional to how many strings a language model generated.
    """
    try:
        if not isinstance(baseline, dict):
            raise ValueError('baseline must be an event object')
        score = _raw_score(baseline)
        now = _number(timestamp_s, 'timestamp_s', minimum=0.)
        count = _integer(active_event_count, 'active_event_count')
        event_id = _integer(baseline.get('event_id'), 'event_id')
        if baseline.get('label') not in ('fire', 'smoke') or baseline.get('fresh_evidence') is not True:
            return _unavailable('当前检测缺少新鲜、有效的烟火事件证据。')
        if record is None:
            return _unavailable('等待本路视频完整的AI场景结果。')
        if not isinstance(record, dict):
            raise ValueError('scene record must be an object')
        for availability_field in ('available_at_s', 'scene_available_at_s'):
            if availability_field in record:
                available = _number(record[availability_field], availability_field, minimum=0.)
                if available > now:
                    return _unavailable('完整AI结果在当前视频时刻尚不可用，保留原检测结果。')
        record_event = record.get('event_id')
        if record_event is not None:
            _integer(record_event, 'scene event_id')
            if record_event != event_id:
                return _unavailable('场景结果与当前事件不对应。')
        frame_count = _integer(record.get('frame_count'), 'frame_count')
        span = _number(record.get('span_s'), 'span_s', minimum=0.)
        evidence = record.get('evidence')
        if not isinstance(evidence, list) or len(evidence) != frame_count or frame_count > 64:
            raise ValueError('frame_count must match scene evidence')
        stamps = []
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError('invalid evidence item')
            stamps.append(_number(item.get('timestamp_s'), 'evidence timestamp', minimum=0.))
        if stamps != sorted(set(stamps)) or not math.isclose(span, stamps[-1] - stamps[0], abs_tol=.001):
            raise ValueError('scene evidence timestamps/span do not agree')
        age = now - stamps[-1]
        if age < 0 or age > 12.:
            return _unavailable('场景画面来自未来或已超过12秒，保留原检测结果。')
        raw_scene = record.get('scene')
        if not isinstance(raw_scene, dict) or set(raw_scene) != _SCENE_FIELDS:
            raise ValueError('scene fields do not match the complete schema')
        scene = validate_scene(raw_scene, frame_count=frame_count, span_s=span)
        if frame_count < 2 or span < .8:
            return _unavailable('原画面数量或时间跨度不足，暂不参与候选融合。')
        features = baseline.get('features')
        if not isinstance(features, dict):
            raise ValueError('detector features are missing')
        if 'event_id' in features and (type(features['event_id']) is not int or features['event_id'] != event_id):
            raise ValueError('detector feature event_id does not match')
        if 'label' in features and features['label'] != baseline['label']:
            raise ValueError('detector feature label does not match')
        if 'fresh_evidence' in features and features['fresh_evidence'] is not True:
            return _unavailable('检测特征已不新鲜，保留原检测结果。')
        coverage = _number(features.get('coverage_ratio_smoothed'), 'coverage', minimum=0., maximum=1.)
        duration = _number(features.get('positive_duration_s'), 'duration', minimum=0.)
        confidence = _number(features.get('confidence'), 'detector confidence', minimum=0., maximum=1.)
        growth = features.get('growth_pp_per_s')
        if growth is not None:
            growth = _number(growth, 'growth')
        values = {name: 0. for name in FEATURE_NAMES}
        values.update(detector_score=score / 100., detector_coverage=_unit(coverage / .2),
                      detector_duration=_unit(duration / 30.),
                      detector_growth=_unit(max(0., growth or 0.) / 3.),
                      detector_growth_missing=float(growth is None),
                      detector_confidence=confidence, detector_smoke=float(baseline['label'] == 'smoke'))
        values['scene_' + scene['scene']] = 1.
        values.update(scene_expected=float(scene['relation'] == 'expected_burning'),
                      scene_abnormal=float(scene['relation'] == 'suspected_abnormal'),
                      scene_unclear=float(scene['relation'] == 'unclear'),
                      scene_spread=float(scene['spread'] == 'observed'),
                      scene_no_spread=float(scene['spread'] == 'not_observed'),
                      scene_limited=float(scene['evidence_quality'] == 'limited'),
                      scene_hazard=float(bool(scene['hazard_cues'])),
                      scene_uncertainty=float(bool(scene['uncertainties'])))
        controlled = float(scene['scene'] in _CONTROLLED_SCENES and scene['relation'] == 'expected_burning')
        values.update(controlled_x_growth=controlled * values['detector_growth'],
                      controlled_x_smoke=controlled * values['detector_smoke'],
                      controlled_x_duration=controlled * values['detector_duration'],
                      abnormal_x_small_fire=values['scene_abnormal'] * (1. - values['detector_coverage']) * (1. - values['detector_smoke']),
                      spread_x_growth=values['scene_spread'] * values['detector_growth'],
                      hazard_x_growth=values['scene_hazard'] * values['detector_growth'])
        blockers = []
        if count != 1:
            blockers.append('multiple_events')
        if baseline['label'] == 'smoke':
            blockers.append('smoke_event')
        if growth is None:
            blockers.append('growth_missing')
        elif growth > .5:
            blockers.append('growth_above_limit')
        if not controlled:
            blockers.append('controlled_burning_not_supported')
        if scene['evidence_quality'] != 'adequate' or not scene['visible_evidence']:
            blockers.append('controlled_evidence_insufficient')
        if scene['spread'] != 'not_observed':
            blockers.append('spread_not_excluded')
        if scene['hazard_cues']:
            blockers.append('visible_hazard')
        gate = math.exp(-age / 12.) * min(1., span) * min(1., (frame_count - 1.) / 2.)
        return {'eligible': True, 'reason': '完整场景与当前烟火事件共同形成实验候选指数。',
                'gate': gate, 'values': values, 'blockers': blockers}
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _unavailable('证据校验未通过，保留原检测结果：' + str(error))


def _validated_values(value):
    if not isinstance(value, dict) or set(value) != set(FEATURE_NAMES):
        raise ValueError('feature names must exactly match ' + FEATURE_VERSION)
    return {name: _number(value[name], name, minimum=0., maximum=1.) for name in FEATURE_NAMES}


def _sigmoid(value):
    if value >= 0:
        return 1. / (1. + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1. + exp_value)


class FusionModel:
    """Strictly versioned development artifact; no notification side effects."""
    def __init__(self, value):
        self._value = self._validate(value)

    @classmethod
    def load(cls, path):
        def unique_object(pairs):
            obj = {}
            for key, value in pairs:
                if key in obj:
                    raise ValueError('duplicate artifact key: ' + key)
                obj[key] = value
            return obj
        with Path(path).open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('adaptive fusion artifact exceeds 1 MiB')
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object)
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value):
        return cls(value)

    @staticmethod
    def _validate(value):
        expected = {'artifact_type', 'artifact_version', 'feature_version', 'model_id', 'status',
                    'experimental', 'calibrated', 'feature_names', 'coefficients', 'intercept', 'residual', 'training'}
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError('invalid adaptive fusion artifact fields')
        if (value['artifact_type'] != ARTIFACT_TYPE or type(value['artifact_version']) is not int
                or value['artifact_version'] != 1 or value['feature_version'] != FEATURE_VERSION
                or value['feature_names'] != list(FEATURE_NAMES)):
            raise ValueError('incompatible adaptive fusion artifact version/features')
        if value['status'] != 'development' or value['experimental'] is not True or value['calibrated'] is not False:
            raise ValueError('only uncalibrated development models are supported')
        if not isinstance(value['model_id'], str) or not value['model_id'].strip() or len(value['model_id']) > 160:
            raise ValueError('invalid model_id')
        coefficients = value['coefficients']
        if not isinstance(coefficients, dict) or set(coefficients) != set(FEATURE_NAMES):
            raise ValueError('model coefficient feature names do not match')
        for name in FEATURE_NAMES:
            _number(coefficients[name], name, minimum=-1e6, maximum=1e6)
        _number(value['intercept'], 'intercept', minimum=-1e6, maximum=1e6)
        residual = value['residual']
        if not isinstance(residual, dict) or set(residual) != {'scale', 'down_cap', 'up_cap', 'validated'} or residual['validated'] is not False:
            raise ValueError('residual limits must explicitly be unvalidated')
        _number(residual['scale'], 'residual scale', minimum=.000001, maximum=100.)
        _number(residual['down_cap'], 'down_cap', minimum=0., maximum=12.)
        _number(residual['up_cap'], 'up_cap', minimum=0., maximum=15.)
        train = value['training']
        train_fields = {'source_groups', 'sample_count', 'class_counts', 'group_weighting', 'l2',
                        'iterations', 'learning_rate', 'include_interactions', 'validation_status'}
        if not isinstance(train, dict) or set(train) != train_fields:
            raise ValueError('incomplete training provenance')
        groups = train['source_groups']
        if (not isinstance(groups, list) or len(groups) < 2 or any(not isinstance(g, str) or not g.strip() for g in groups)
                or len(set(groups)) != len(groups) or groups != sorted(groups)):
            raise ValueError('at least two unique sorted source groups are required')
        samples = _integer(train['sample_count'], 'sample_count', minimum=2)
        if samples < len(groups):
            raise ValueError('source groups exceed training sample count')
        counts = train['class_counts']
        if not isinstance(counts, dict) or set(counts) != {'0', '1'}:
            raise ValueError('both training classes must be recorded')
        if sum(_integer(counts[key], 'class count') for key in ('0', '1')) != samples:
            raise ValueError('class counts do not match sample count')
        if train['group_weighting'] != 'equal-source-group' or train['validation_status'] != 'not_validated':
            raise ValueError('invalid training/validation status')
        _number(train['l2'], 'l2', minimum=0., maximum=1e6)
        _integer(train['iterations'], 'iterations')
        _number(train['learning_rate'], 'learning_rate', minimum=.000001, maximum=10.)
        if type(train['include_interactions']) is not bool:
            raise ValueError('include_interactions must be boolean')
        if not train['include_interactions'] and any(coefficients[key] != 0 for key in INTERACTION_FEATURES):
            raise ValueError('interaction coefficients contradict training configuration')
        # Reject non-JSON or nonfinite metadata, and detach from caller mutation.
        return json.loads(json.dumps(value, allow_nan=False))

    @property
    def model_id(self):
        return self._value['model_id']

    @property
    def training(self):
        return deepcopy(self._value['training'])

    def to_dict(self):
        return deepcopy(self._value)

    def predict(self, baseline, record, *, timestamp_s, active_event_count=1):
        try:
            raw = _raw_score(baseline)
        except (KeyError, TypeError, ValueError, OverflowError):
            raw = None
        built = build_features(baseline, record, timestamp_s=timestamp_s, active_event_count=active_event_count)
        result = {'status': 'skipped', 'model_id': self.model_id, 'score': raw, 'context_delta': 0.,
                  'model_output': None, 'neutral_output': None, 'contributions': [],
                  'reason': built['reason'], 'feature_version': FEATURE_VERSION,
                  'experimental': True, 'calibrated': False, 'gate': built['gate'],
                  'blockers': built['blockers']}
        if not built['eligible']:
            return result
        values = built['values']
        coefficients = self._value['coefficients']
        terms = [{'feature': 'intercept', 'value': 1., 'coefficient': self._value['intercept'],
                  'contribution': self._value['intercept']}]
        terms += [{'feature': name, 'value': values[name], 'coefficient': coefficients[name],
                   'contribution': coefficients[name] * values[name]} for name in FEATURE_NAMES
                  if coefficients[name] * values[name] != 0.]
        full_logit = self._value['intercept'] + sum(coefficients[name] * values[name] for name in FEATURE_NAMES)
        neutral_logit = self._value['intercept'] + sum(coefficients[name] * values[name] for name in DETECTOR_FEATURES)
        full, neutral = _sigmoid(full_logit), _sigmoid(neutral_logit)
        limits = self._value['residual']
        delta = built['gate'] * max(-limits['down_cap'], min(limits['up_cap'], limits['scale'] * (full - neutral)))
        if delta < 0 and built['blockers']:
            delta = 0.
            result['reason'] = '场景提出降权意见，但保护条件要求保留原检测指数。'
        candidate = max(0., min(100., raw + delta))
        result.update(status='ready', score=round(candidate, 6), context_delta=round(candidate - raw, 6),
                      model_output=full, neutral_output=neutral,
                      contributions=sorted(terms, key=lambda term: abs(term['contribution']), reverse=True))
        return result


def fit_model(samples, *, l2=1.0, iterations=800, learning_rate=0.1, include_interactions=True):
    """Fit deterministic group-balanced logistic regression, development only.

    Two groups/classes are an input floor, not evidence of statistical validity.
    This function does not split data or claim holdout accuracy/calibration.
    """
    penalty = _number(l2, 'l2', minimum=0., maximum=1e6)
    steps = _integer(iterations, 'iterations')
    rate = _number(learning_rate, 'learning_rate', minimum=.000001, maximum=10.)
    if type(include_interactions) is not bool:
        raise ValueError('include_interactions must be boolean')
    rows = list(samples)
    if not rows:
        raise ValueError('training samples are required')
    prepared = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'features', 'label', 'source_group'}:
            raise ValueError('training rows require features, label and source_group only')
        if type(row['label']) is not int or row['label'] not in (0, 1):
            raise ValueError('training labels must be integer 0 or 1')
        group = row['source_group']
        if not isinstance(group, str) or not group.strip():
            raise ValueError('a nonempty source_group is required')
        values = _validated_values(row['features'])
        prepared.append((values, row['label'], group))
    groups = Counter(row[2] for row in prepared)
    classes = Counter(row[1] for row in prepared)
    if len(groups) < 2 or set(classes) != {0, 1}:
        raise ValueError('training requires at least two source groups and both classes')
    active_names = FEATURE_NAMES if include_interactions else DETECTOR_FEATURES + SCENE_FEATURES
    matrix = [([values[name] for name in active_names], label, 1. / (len(groups) * groups[group]))
              for values, label, group in prepared]
    coefficients = [0.] * len(active_names)
    intercept = 0.
    regularization = penalty / len(prepared)
    for _ in range(steps):
        gradient = [regularization * value for value in coefficients]
        bias_gradient = 0.
        for values, label, weight in matrix:
            logit = intercept + sum(coefficient * value for coefficient, value in zip(coefficients, values))
            error = (_sigmoid(logit) - label) * weight
            bias_gradient += error
            for index, value in enumerate(values):
                gradient[index] += error * value
        intercept -= rate * bias_gradient
        coefficients = [coefficient - rate * grad for coefficient, grad in zip(coefficients, gradient)]
        if not math.isfinite(intercept) or not all(math.isfinite(value) for value in coefficients):
            raise ValueError('training diverged; reduce learning rate')
    params = {name: 0. for name in FEATURE_NAMES}
    params.update(zip(active_names, coefficients))
    provenance = {'source_groups': sorted(groups), 'sample_count': len(prepared),
                  'class_counts': {str(label): classes[label] for label in (0, 1)},
                  'group_weighting': 'equal-source-group', 'l2': penalty, 'iterations': steps,
                  'learning_rate': rate, 'include_interactions': include_interactions,
                  'validation_status': 'not_validated'}
    digest_data = {'coefficients': params, 'intercept': intercept, 'training': provenance,
                   'feature_version': FEATURE_VERSION}
    digest = hashlib.sha256(json.dumps(digest_data, sort_keys=True, allow_nan=False).encode()).hexdigest()[:16]
    return FusionModel.from_dict({'artifact_type': ARTIFACT_TYPE, 'artifact_version': 1,
        'feature_version': FEATURE_VERSION, 'model_id': 'development-' + digest,
        'status': 'development', 'experimental': True, 'calibrated': False,
        'feature_names': list(FEATURE_NAMES), 'coefficients': params, 'intercept': intercept,
        'residual': {'scale': 30., 'down_cap': 12., 'up_cap': 15., 'validated': False},
        'training': provenance})
