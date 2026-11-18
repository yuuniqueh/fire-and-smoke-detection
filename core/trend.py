"""Short-horizon image-box coverage extrapolation, not physical fire spread."""
from collections import deque
from dataclasses import asdict, dataclass
import math


METHOD_NOTE = (
    '对已确认视觉事件的平滑检测框并集占比作短时线性外推；'
    '范围来自历史拟合残差，不是统计置信区间、燃烧面积或火势蔓延预测。'
)


@dataclass(frozen=True)
class TrendConfig:
    window_s: float = 6.0
    min_span_s: float = 3.0
    min_samples: int = 8
    horizon_s: float = 3.0
    max_gap_s: float = 0.6
    sample_interval_s: float = 0.2
    # Generic numerical safeguards; these values are not fitted to a dataset.
    min_r2: float = 0.55
    max_residual_fraction: float = 0.35
    flat_range_ratio: float = 0.002  # Up to 0.2 percentage points is near-flat.
    min_band_ratio: float = 0.001  # Display at least +/- 0.1 percentage points.

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be finite')
            if value < 0:
                raise ValueError(f'{name} cannot be negative')
        for name in ('window_s', 'min_span_s', 'horizon_s', 'max_gap_s', 'sample_interval_s'):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if not isinstance(self.min_samples, int) or self.min_samples < 2:
            raise ValueError('min_samples must be an integer >= 2')
        if self.min_span_s > self.window_s or self.sample_interval_s > self.max_gap_s:
            raise ValueError('invalid trend window, span or sampling interval')
        if self.min_samples > math.floor(self.window_s / self.sample_interval_s) + 1:
            raise ValueError('min_samples cannot fit in the trend window')
        for name in ('min_r2', 'max_residual_fraction', 'flat_range_ratio', 'min_band_ratio'):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f'{name} must be between 0 and 1')


def _bounded(value):
    return max(0.0, min(1.0, value))


class TrendEngine:
    """Maintain bounded histories for active confirmed events only.

    A centered, time-normalized least-squares fit avoids timestamp magnitude
    problems. Non-flat histories require R2 >= min_r2 and residual RMS / observed
    range <= max_residual_fraction. A near-flat history can pass without a high
    R2, which is undefined or uninformative for constant signals. The empirical
    display band is +/- max(2 * RMS, min_band_ratio), widened with horizon/span.
    This is a descriptive envelope, with no calibrated probabilistic coverage.
    """
    def __init__(self, config=None):
        self.config = config or TrendConfig()
        self.states = {}
        self.last_timestamp = None
        self.history_limit = math.ceil(self.config.window_s / self.config.sample_interval_s) + 2

    def update(self, assessment):
        timestamp = assessment['timestamp_s']
        if (not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool)
                or not math.isfinite(timestamp) or timestamp < 0
                or (self.last_timestamp is not None and timestamp <= self.last_timestamp)):
            raise ValueError('trend timestamps must increase strictly')
        events = assessment.get('events', [])
        event_ids = [event['event_id'] for event in events]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError('duplicate trend event id')
        self.last_timestamp = timestamp
        for event_id in list(self.states):
            if event_id not in event_ids:
                del self.states[event_id]
        results = []
        for event in events:
            label = event['label']
            if label not in ('fire', 'smoke'):
                raise ValueError('unsupported trend class')
            event_id = event['event_id']
            state = self.states.setdefault(event_id, {
                'label': label, 'history': deque(maxlen=self.history_limit),
                'last_fresh_s': None, 'last_coverage': None,
            })
            if state['label'] != label:
                raise ValueError('trend event class cannot change')
            feature = event.get('features', {})
            fresh = event.get('fresh_evidence', False) and feature.get('fresh_evidence', False)
            coverage = feature.get('coverage_ratio_smoothed')
            if fresh and (isinstance(coverage, bool) or not isinstance(coverage, (int, float))
                          or not math.isfinite(coverage) or not 0 <= coverage <= 1):
                raise ValueError('fresh trend coverage must be finite and between 0 and 1')
            history = state['history']
            if state['last_fresh_s'] is not None and timestamp - state['last_fresh_s'] > self.config.max_gap_s + 1e-9:
                history.clear()
            while history and history[0][0] < timestamp - self.config.window_s - 1e-9:
                history.popleft()
            if fresh:
                state['last_fresh_s'] = timestamp
                state['last_coverage'] = coverage
                if not history or timestamp - history[-1][0] >= self.config.sample_interval_s - 1e-9:
                    history.append((timestamp, coverage))
            item = {
                'event_id': event_id, 'label': label,
                'status': 'warming_up' if fresh else 'temporarily_missing',
                'coverage_ratio': state['last_coverage'], 'predicted_coverage_ratio': None,
                'lower_coverage_ratio': None, 'upper_coverage_ratio': None,
                'growth_pp_per_s': None, 'horizon_s': self.config.horizon_s,
                'history': [{'timestamp_s': t, 'coverage_ratio': area} for t, area in history],
                'projection': [], 'reason': '',
            }
            if not fresh:
                item['reason'] = '当前检测证据暂缺，保留最近观测，不把缺失当作面积缩小，也不作外推。'
            elif len(history) < self.config.min_samples or history[-1][0] - history[0][0] < self.config.min_span_s - 1e-9:
                span = history[-1][0] - history[0][0] if history else 0.0
                item['reason'] = (f'积累连续新鲜观测：{len(history)}/{self.config.min_samples} 个采样点，'
                                  f'{span:.1f}/{self.config.min_span_s:.1f} 秒。较长缺口会重新积累。')
            else:
                self._fit(item, history, timestamp)
            results.append(item)
        dominant = assessment.get('dominant_event_id')
        return {
            'timestamp_s': timestamp, 'events': results,
            'dominant_event_id': dominant if dominant in event_ids else None,
            'note': METHOD_NOTE,
        }

    def _fit(self, item, history, timestamp):
        span = history[-1][0] - history[0][0]
        # Subtract first, then normalize: seconds since stream start never enter
        # the normal equations directly.
        xs = [(t - history[0][0]) / span for t, _ in history]
        ys = [value for _, value in history]
        mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
        denominator = sum((x - mean_x) ** 2 for x in xs)
        slope_normalized = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denominator
        intercept = mean_y - slope_normalized * mean_x
        residual_sum = sum((y - (intercept + slope_normalized * x)) ** 2 for x, y in zip(xs, ys))
        total_sum = sum((y - mean_y) ** 2 for y in ys)
        rms = math.sqrt(max(0.0, residual_sum / len(xs)))
        observed_range = max(ys) - min(ys)
        r2 = max(0.0, 1.0 - residual_sum / total_sum) if total_sum > 1e-20 else 1.0
        item['fit_r2'], item['residual_ratio'] = r2, rms
        if observed_range > self.config.flat_range_ratio and (
                r2 < self.config.min_r2 or rms / observed_range > self.config.max_residual_fraction):
            item['status'] = 'unstable'
            item['reason'] = (f'历史变化不适合线性外推（R²={r2:.2f}，残差均方根={rms * 100:.2f} 个百分点）；'
                              '显示观测历史，暂不提供未来值。')
            return
        slope = slope_normalized / span
        fitted_now = intercept + slope_normalized * ((timestamp - history[0][0]) / span)
        predicted = _bounded(fitted_now + slope * self.config.horizon_s)
        base_band = max(2.0 * rms, self.config.min_band_ratio)
        band = base_band * math.sqrt(1.0 + (self.config.horizon_s / span) ** 2)
        item.update({
            'status': 'ready', 'predicted_coverage_ratio': predicted,
            'lower_coverage_ratio': _bounded(predicted - band),
            'upper_coverage_ratio': _bounded(predicted + band),
            'growth_pp_per_s': slope * 100.0,
            'reason': (f'基于最近 {span:.1f} 秒的平滑框并集占比，线性外推 {self.config.horizon_s:.1f} 秒。'
                       '阴影范围来自历史残差，不是置信区间；视角、遮挡和检测框抖动会改变结果。'),
        })
        steps = 6
        for index in range(steps + 1):
            offset = self.config.horizon_s * index / steps
            value = _bounded(fitted_now + slope * offset)
            width = base_band * math.sqrt(1.0 + (offset / span) ** 2)
            item['projection'].append({
                'timestamp_s': timestamp + offset, 'coverage_ratio': value,
                'lower_coverage_ratio': _bounded(value - width),
                'upper_coverage_ratio': _bounded(value + width),
            })
