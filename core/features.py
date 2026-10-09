"""Image-box coverage and time features, not physical fire measurements."""
import math
from collections import deque


def normalized_box(box, frame_size):
    width, height = frame_size
    if width <= 0 or height <= 0 or len(box) != 4 or not all(math.isfinite(v) for v in box):
        raise ValueError('invalid dimensions or box')
    result = [max(0.0, min(1.0, value / scale))
              for value, scale in zip(box, (width, height, width, height))]
    return result if result[2] > result[0] and result[3] > result[1] else None


def intersection_area(a, b):
    return max(0.0, min(a[2], b[2]) - max(a[0], b[0])) * max(0.0, min(a[3], b[3]) - max(a[1], b[1]))


def union_coverage(boxes):
    """Exact union area of clipped normalized axis-aligned rectangles."""
    valid = []
    for box in boxes:
        if len(box) != 4 or not all(math.isfinite(v) for v in box):
            raise ValueError('invalid normalized box')
        x1, y1, x2, y2 = [max(0.0, min(1.0, value)) for value in box]
        if x2 > x1 and y2 > y1:
            valid.append((x1, y1, x2, y2))
    edges = sorted({x for box in valid for x in (box[0], box[2])})
    total = 0.0
    for left, right in zip(edges, edges[1:]):
        spans = sorted((y1, y2) for x1, y1, x2, y2 in valid if x1 < right and x2 > left)
        covered = 0.0
        end = -1.0
        for bottom, top in spans:
            covered += max(0.0, top - max(end, bottom))
            end = max(end, top)
        total += (right - left) * covered
    return max(0.0, min(1.0, total))


class FeatureExtractor:
    def __init__(self, smoothing_tau_s=0.75, trend_window_s=1.5, trend_deadband_pp_s=0.3):
        if not all(math.isfinite(v) for v in (smoothing_tau_s, trend_window_s, trend_deadband_pp_s)):
            raise ValueError('feature parameters must be finite')
        if smoothing_tau_s <= 0 or trend_window_s <= 0 or trend_deadband_pp_s < 0:
            raise ValueError('invalid feature parameters')
        self.tau = smoothing_tau_s
        self.window = trend_window_s
        self.deadband = trend_deadband_pp_s
        self.states = {}
        self.last_timestamp = None

    def update(self, timestamp, detections, frame_size, tracks):
        if not math.isfinite(timestamp) or timestamp < 0 or (self.last_timestamp is not None and timestamp <= self.last_timestamp):
            raise ValueError('feature timestamps must increase strictly')
        boxes_by_id = {track['event_id']: [] for track in tracks}
        for item in detections:
            box = normalized_box(item['bbox_xyxy'], frame_size)
            if box is None:
                continue
            area = (box[2] - box[0]) * (box[3] - box[1])
            candidates = [(intersection_area(box, track['bbox_normalized']) / area, track['event_id'])
                          for track in tracks if track['present_this_frame'] and track['label'] == item['label']]
            if candidates:
                fraction, event_id = max(candidates)
                if fraction >= 0.5:
                    boxes_by_id[event_id].append(box)
        self.last_timestamp = timestamp
        active_ids = set(boxes_by_id)
        for event_id in list(self.states):
            if event_id not in active_ids:
                del self.states[event_id]
        features = {}
        for track in tracks:
            event_id = track['event_id']
            state = self.states.setdefault(event_id, {
                'last_t': timestamp, 'previous_present': False, 'positive_duration_s': 0.0,
                'smoothed_coverage': None, 'last_area_t': None, 'history': deque(),
            })
            dt = timestamp - state['last_t']
            if state['previous_present']:
                state['positive_duration_s'] += dt
            present = track['present_this_frame'] and bool(boxes_by_id[event_id])
            state['last_t'] = timestamp
            state['previous_present'] = present
            growth = None
            coverage = None
            if present:
                coverage = union_coverage(boxes_by_id[event_id])
                if state['last_area_t'] is None:
                    state['smoothed_coverage'] = coverage
                else:
                    alpha = -math.expm1(-(timestamp - state['last_area_t']) / self.tau)
                    state['smoothed_coverage'] += alpha * (coverage - state['smoothed_coverage'])
                state['last_area_t'] = timestamp
                history = state['history']
                history.append((timestamp, state['smoothed_coverage']))
                left = timestamp - self.window
                while len(history) > 2 and history[1][0] <= left:
                    history.popleft()
                if len(history) >= 2 and history[0][0] <= left + 1e-9:
                    t0, a0 = history[0]
                    t1, a1 = history[1]
                    weight = max(0.0, min(1.0, (left - t0) / (t1 - t0)))
                    reference = a0 + weight * (a1 - a0)
                    growth = 100 * (state['smoothed_coverage'] - reference) / self.window
            else:
                # Missing detections are not a measured zero coverage or measured shrinkage.
                state['history'].clear()
                state['last_area_t'] = None
            trend = ('temporarily_missing' if not present else 'warming_up' if growth is None
                     else 'growing' if growth > self.deadband else 'shrinking' if growth < -self.deadband else 'stable')
            features[event_id] = {
                'event_id': event_id, 'label': track['label'], 'fresh_evidence': present,
                'coverage_ratio_raw': round(coverage, 8) if coverage is not None else None,
                'coverage_ratio_smoothed': round(state['smoothed_coverage'], 8) if state['smoothed_coverage'] is not None else None,
                'growth_pp_per_s': round(growth, 6) if growth is not None else None,
                'trend': trend, 'positive_duration_s': round(state['positive_duration_s'], 6),
                'confidence': track['confidence'] if present else None,
                'missing_for_s': round(timestamp - track['last_seen_s'], 6),
                'presence_ratio': track['presence_ratio'],
            }
        return features
