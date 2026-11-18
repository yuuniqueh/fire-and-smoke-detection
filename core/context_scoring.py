"""Versioned, untrained review-index rules; neither output is a fire probability."""
import math


POLICY = 'context-score-v2'
RULES = {'policy': POLICY, 'max_scene_age_s': 12., 'hazard_gain_base': .25,
         'hazard_gain_coverage': .10, 'hazard_gain_duration': .10, 'hazard_gain_growth': .20,
         'hazard_cue_support': .50, 'spread_support': .35, 'abnormal_relation_support': .15,
         'increase_cap': 25., 'normal_reduction_cap': 12., 'normal_reduction_ratio': .20,
         'normal_growth_limit_pp_s': .5, 'scene_only_base': 35., 'scene_only_support': 25.,
         'note': 'Explicit development rules, not fitted weights or calibrated fire probabilities.'}


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _unit(value):
    return max(0., min(1., value))


def scene_support(scene, *, frame_count, span_s, age_s, max_age_s=12.):
    from core.scene_review import validate_scene
    valid = (type(frame_count) is int and 2 <= frame_count <= 64 and _finite(span_s) and span_s >= .8
             and _finite(age_s) and _finite(max_age_s) and max_age_s > 0 and 0 <= age_s < max_age_s)
    if not valid:
        return {'gate': 0., 'hazard': 0., 'controlled': False, 'scene': None}
    max_age_s = min(max_age_s, RULES['max_scene_age_s'])
    if age_s >= max_age_s:
        return {'gate': 0., 'hazard': 0., 'controlled': False, 'scene': None}
    try:
        value = validate_scene(scene, frame_count, span_s)
    except (TypeError, ValueError, KeyError):
        return {'gate': 0., 'hazard': 0., 'controlled': False, 'scene': None}
    gate = (1. - age_s / max_age_s) * min(1., span_s) * min(1., (frame_count - 1.) / 2.)
    hazard = (RULES['hazard_cue_support'] * bool(value['hazard_cues']) +
              RULES['spread_support'] * (value['spread'] == 'observed') +
              RULES['abnormal_relation_support'] * (value['relation'] == 'suspected_abnormal' and bool(value['visible_evidence'])))
    controlled = (not hazard and value['scene'] in ('candle', 'cooking', 'fireplace') and
                  value['relation'] == 'expected_burning' and value['evidence_quality'] == 'adequate' and
                  value['spread'] == 'not_observed' and bool(value['visible_evidence']))
    return {'gate': gate, 'hazard': hazard, 'controlled': bool(controlled), 'scene': value}


def contextual_score(baseline, scene, *, frame_count, span_s, age_s=0., max_age_s=12.,
                     active_event_count=1, risk_config=None):
    from core.scene_review import LEVELS
    score, level = baseline.get('score', 0.), baseline.get('level', 'LOW')
    if not _finite(score) or not 0 <= score <= 100 or level not in LEVELS:
        raise ValueError('原始核查指数无效。')
    support = scene_support(scene, frame_count=frame_count, span_s=span_s, age_s=age_s, max_age_s=max_age_s)
    gate = support['gate'] if baseline.get('fresh_evidence', True) is True else 0.
    features = baseline.get('features') or {}
    coverage, duration, growth = (features.get('coverage_ratio_smoothed'), features.get('positive_duration_s'),
                                  features.get('growth_pp_per_s'))
    coverage_ok = _finite(coverage) and 0 <= coverage <= 1
    duration_ok = _finite(duration) and duration >= 0
    growth_ok = _finite(growth)
    coverage_scale = getattr(risk_config, 'coverage_full_scale', .2)
    duration_scale = getattr(risk_config, 'duration_full_scale_s', 30.)
    growth_scale = getattr(risk_config, 'growth_full_scale_pp_s', 3.)
    if any(not _finite(value) or value <= 0 for value in (coverage_scale, duration_scale, growth_scale)):
        raise ValueError('场景评分的范围配置必须为有限正数。')
    c = _unit(coverage / coverage_scale) if coverage_ok else 0.
    d = _unit(duration / duration_scale) if duration_ok else 0.
    g = _unit(max(0., growth) / growth_scale) if growth_ok else 0.
    delta = 0.
    reason = '场景证据不足以调整指数，当前综合结果沿用烟火检测。'
    if not gate:
        reason = '完整场景尚不可用、已过期或证据不足，当前综合结果沿用烟火检测。'
    elif support['hazard']:
        gain = (RULES['hazard_gain_base'] + RULES['hazard_gain_coverage'] * c +
                RULES['hazard_gain_duration'] * d + RULES['hazard_gain_growth'] * g)
        delta = gate * min(RULES['increase_cap'], (100. - score) * support['hazard'] * gain)
        reason = 'AI记录了异常线索，结合烟火范围、持续时间和增长趋势提高综合核查指数。'
    elif support['controlled']:
        if (active_event_count != 1 or baseline.get('label') != 'fire' or not coverage_ok or not duration_ok or
                not growth_ok or growth > RULES['normal_growth_limit_pp_s']):
            reason = '受控用火线索不足以解释当前烟雾、增长、多个目标或缺失特征，保留检测指数。'
        else:
            delta = -gate * min(RULES['normal_reduction_cap'], score * RULES['normal_reduction_ratio']) * (1. - .5 * c) * (.6 + .4 * d)
            reason = '多帧支持受控用火且火焰未明显增长，有限降低综合指数；检测事件仍保留并可核查。'
    adjusted = round(max(0., min(100., score + delta)), 2)
    medium = getattr(risk_config, 'medium_enter', 45.)
    high = getattr(risk_config, 'high_enter', 70.)
    if adjusted < score:
        high = getattr(risk_config, 'high_exit', 62.) if level == 'HIGH' else high
        medium = getattr(risk_config, 'medium_exit', 38.) if level in ('MEDIUM', 'HIGH') else medium
    target = 'HIGH' if adjusted >= high else 'MEDIUM' if adjusted >= medium else 'LOW'
    actual_delta = round(adjusted - score, 2)
    if not actual_delta:
        target = level
    elif actual_delta > 0:
        target = LEVELS[max(LEVELS.index(level), LEVELS.index(target))]
    else:
        target = LEVELS[max(LEVELS.index(level) - 1, min(LEVELS.index(level), LEVELS.index(target)))]
    return {'policy': POLICY, 'baseline_score': float(score), 'baseline_level': level,
            'context_delta': actual_delta, 'score': adjusted, 'level': target, 'reason': reason,
            'components': {'time_and_frames': round(gate, 6), 'semantic_support': round(support['hazard'], 6),
                           'coverage': round(c, 6), 'duration': round(d, 6), 'growth': round(g, 6)},
            'experimental': True, 'trained': False, 'calibrated': False, 'original_alert_retained': True}


def scene_only_score(scene, *, frame_count, span_s, age_s=0., max_age_s=12.):
    support = scene_support(scene, frame_count=frame_count, span_s=span_s, age_s=age_s, max_age_s=max_age_s)
    score = round(support['gate'] * (RULES['scene_only_base'] + RULES['scene_only_support'] * support['hazard']), 2) if support['hazard'] else 0.
    return {'score': score, 'level': 'MEDIUM' if score >= 45. else 'LOW' if score else 'NORMAL',
            'active': bool(score), 'reason': ('AI记录了异常线索，烟火模型尚未确认事件；综合提示需要人工核查。' if score
                                            else '当前没有可用于提高综合指数的完整AI异常线索。'),
            'policy': POLICY, 'experimental': True, 'trained': False, 'calibrated': False}
