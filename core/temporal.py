"""Time-based, spatially associated events; no claim of physical fire truth."""
from collections import deque
from dataclasses import asdict, dataclass, field
import math


@dataclass(frozen=True)
class TemporalConfig:
    window_s: float = 1.5
    min_observation_s: float = 1.0
    min_positive_s: float = 0.8
    min_presence_ratio: float = 0.7
    max_gap_s: float = 0.6
    match_iou: float = 0.1
    cluster_padding: float = 0.025
    cooldown_s: float = 2.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f'{name} must be a finite number')
        for name in ('window_s', 'min_observation_s', 'min_positive_s', 'max_gap_s'):
            if getattr(self, name) <= 0:
                raise ValueError(f'{name} must be positive')
        if not 0 < self.min_presence_ratio <= 1 or not 0 < self.match_iou <= 1:
            raise ValueError('presence ratio and match IoU must be in (0, 1]')
        if not 0 <= self.cluster_padding <= 0.1 or self.cooldown_s < 0:
            raise ValueError('cluster padding must be in [0, 0.1]; cooldown must be nonnegative')
        if self.min_observation_s > self.window_s or self.min_positive_s > self.window_s:
            raise ValueError('minimum observation/positive time cannot exceed window')


def iou(a, b):
    intersection = max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def group_detections(detections, frame_size, padding):
    """Merge nearby same-class fragments; coordinates normalized to frame size."""
    width, height = frame_size
    if width <= 0 or height <= 0:
        raise ValueError('invalid frame dimensions')
    groups = []
    for item in detections:
        if item['label'] not in ('fire', 'smoke'):
            continue
        box = item['bbox_xyxy']
        if len(box) != 4 or not all(math.isfinite(v) for v in box):
            raise ValueError('invalid detection box')
        confidence = float(item['confidence'])
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('invalid confidence')
        normalized = [max(0.0, min(1.0, value / scale))
                      for value, scale in zip(box, (width, height, width, height))]
        if normalized[2] <= normalized[0] or normalized[3] <= normalized[1]:
            continue
        groups.append({'label': item['label'], 'box': normalized, 'confidence': confidence})
    # Iterate merges so fragments linked through a third fragment form one region.
    changed = True
    while changed:
        changed = False
        for left in range(len(groups)):
            for right in range(left + 1, len(groups)):
                a, b = groups[left], groups[right]
                if a['label'] != b['label']:
                    continue
                x, y = a['box'], b['box']
                near = (x[0] <= y[2] + 2 * padding and y[0] <= x[2] + 2 * padding
                        and x[1] <= y[3] + 2 * padding and y[1] <= x[3] + 2 * padding)
                if near:
                    a['box'] = [min(x[0], y[0]), min(x[1], y[1]), max(x[2], y[2]), max(x[3], y[3])]
                    a['confidence'] = max(a['confidence'], b['confidence'])
                    groups.pop(right)
                    changed = True
                    break
            if changed:
                break
    return groups


@dataclass
class Track:
    event_id: int
    label: str
    box: list
    confidence: float
    started_at_s: float
    last_seen_s: float
    evidence: deque = field(default_factory=deque)
    confirmed_at_s: object = None
    alert_emitted: bool = False
    confirmation_evidence: object = None


class EventTracker:
    def __init__(self, config=None):
        self.config = config or TemporalConfig()
        self.tracks = {}
        self.closed_events = []
        self.recent_alerts = deque()
        self.next_id = 1
        self.last_timestamp = None
        self.candidates_started = 0
        self.confirmed_count = 0
        self.alert_count = 0
        self.finished = False

    def _evidence(self, track, timestamp):
        left = max(track.started_at_s, timestamp - self.config.window_s)
        points = list(track.evidence)
        positive = 0.0
        # An observation applies until the next sample (zero-order hold).
        for (start, present), (end, _) in zip(points, points[1:]):
            if present:
                positive += max(0.0, min(timestamp, end) - max(left, start))
        span = max(0.0, timestamp - left)
        return {'observation_s': round(span, 6), 'positive_s': round(positive, 6),
                'presence_ratio': round(positive / span, 6) if span else 0.0}

    def _close(self, track, timestamp, reason):
        confirmed = track.confirmed_at_s is not None
        if confirmed:
            self.closed_events.append({
                'event_id': track.event_id, 'label': track.label,
                'started_at_s': track.started_at_s, 'confirmed_at_s': track.confirmed_at_s,
                'confirmation_delay_s': round(track.confirmed_at_s - track.started_at_s, 6),
                'last_seen_s': track.last_seen_s, 'closed_at_s': timestamp,
                'observed_span_s': round(track.last_seen_s - track.started_at_s, 6),
                'close_reason': reason, 'alert_emitted': track.alert_emitted,
                'confirmation_evidence': track.confirmation_evidence,
                'last_bbox_normalized': track.box,
                'boundary_note': 'Closure is a software event boundary, not proof that a physical fire ended.',
            })
        del self.tracks[track.event_id]
        return {'type': 'event_closed' if confirmed else 'candidate_discarded',
                'event_id': track.event_id, 'label': track.label, 'timestamp_s': timestamp, 'reason': reason}

    def update(self, timestamp, detections, frame_size):
        if self.finished:
            raise ValueError('stream finished; create a new tracker for a new input')
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError('timestamp must be finite and nonnegative')
        if self.last_timestamp is not None and timestamp <= self.last_timestamp:
            raise ValueError('timestamps must increase strictly; new videos need a fresh tracker')
        groups = group_detections(detections, frame_size, self.config.cluster_padding)
        self.last_timestamp = timestamp
        transitions = []
        for track in list(self.tracks.values()):
            if timestamp - track.last_seen_s > self.config.max_gap_s + 1e-9:
                transitions.append(self._close(track, timestamp, 'detection_timeout'))
        # Greedy one-to-one spatial matching; different image regions do not share votes.
        pairs = []
        for track in self.tracks.values():
            for index, group in enumerate(groups):
                overlap = iou(track.box, group['box'])
                if track.label == group['label'] and overlap >= self.config.match_iou:
                    pairs.append((overlap, track.event_id, index))
        matched_tracks, matched_groups = set(), set()
        matches = {}
        for _, event_id, index in sorted(pairs, reverse=True):
            if event_id not in matched_tracks and index not in matched_groups:
                matches[event_id] = groups[index]
                matched_tracks.add(event_id)
                matched_groups.add(index)
        for index, group in enumerate(groups):
            if index not in matched_groups:
                track = Track(self.next_id, group['label'], group['box'], group['confidence'], timestamp, timestamp)
                self.tracks[self.next_id] = track
                matches[self.next_id] = group
                self.candidates_started += 1
                transitions.append({'type': 'candidate_started', 'event_id': self.next_id,
                                    'label': group['label'], 'timestamp_s': timestamp})
                self.next_id += 1
        while self.recent_alerts and timestamp - self.recent_alerts[0]['timestamp_s'] > self.config.cooldown_s:
            self.recent_alerts.popleft()
        snapshots = []
        for track in self.tracks.values():
            group = matches.get(track.event_id)
            present = group is not None
            if present:
                track.box = group['box']
                track.confidence = group['confidence']
                track.last_seen_s = timestamp
            track.evidence.append((timestamp, present))
            left = timestamp - self.config.window_s
            while len(track.evidence) > 2 and track.evidence[1][0] <= left:
                track.evidence.popleft()
            evidence = self._evidence(track, timestamp)
            ready = (present and evidence['observation_s'] + 1e-6 >= self.config.min_observation_s
                     and evidence['positive_s'] + 1e-6 >= self.config.min_positive_s
                     and evidence['presence_ratio'] + 1e-6 >= self.config.min_presence_ratio)
            if track.confirmed_at_s is None and ready:
                track.confirmed_at_s = timestamp
                track.confirmation_evidence = evidence.copy()
                self.confirmed_count += 1
                suppressed = any(alert['label'] == track.label and iou(alert['box'], track.box) >= self.config.match_iou
                                 for alert in self.recent_alerts)
                track.alert_emitted = not suppressed
                if track.alert_emitted:
                    self.alert_count += 1
                    self.recent_alerts.append({'label': track.label, 'box': track.box.copy(), 'timestamp_s': timestamp})
                transitions.append({'type': 'event_confirmed', 'event_id': track.event_id,
                                    'label': track.label, 'timestamp_s': timestamp,
                                    'alert_emitted': track.alert_emitted, 'cooldown_suppressed': suppressed,
                                    'evidence': evidence.copy()})
            snapshots.append({
                'event_id': track.event_id, 'label': track.label,
                'state': 'confirmed' if track.confirmed_at_s is not None else 'pending',
                'present_this_frame': present, 'bbox_normalized': track.box.copy(),
                'confidence': track.confidence, 'started_at_s': track.started_at_s,
                'last_seen_s': track.last_seen_s, 'confirmed_at_s': track.confirmed_at_s,
                'observed_span_s': round(timestamp - track.started_at_s, 6),
                'alert_emitted': track.alert_emitted, **evidence,
            })
        return {'timestamp_s': timestamp, 'tracks': snapshots, 'transitions': transitions}

    def finish(self, timestamp, reason='stream_end'):
        if not math.isfinite(timestamp) or timestamp < (self.last_timestamp or 0):
            raise ValueError('invalid stream end timestamp')
        transitions = [self._close(track, timestamp, reason) for track in list(self.tracks.values())]
        self.finished = True
        return transitions

    def summary(self):
        return {'candidates_started': self.candidates_started, 'confirmed_events': self.confirmed_count,
                'alerts_emitted': self.alert_count, 'cooldown_s': self.config.cooldown_s}


def draw_event_status(frame, snapshot):
    """OpenCV-only overlay; confirmed describes sustained visual evidence."""
    import cv2
    height, width = frame.shape[:2]
    tracks = snapshot['tracks']
    confirmed = [track for track in tracks if track['state'] == 'confirmed']
    cv2.rectangle(frame, (0, 0), (width, 62), (22, 22, 22), -1)
    title = f'CONFIRMED EVENTS: {len(confirmed)}' if confirmed else ('PENDING: verifying persistence' if tracks else 'NO ACTIVE EVENT')
    color = (70, 90, 255) if confirmed else (60, 215, 255) if tracks else (110, 230, 110)
    cv2.putText(frame, title, (12, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)
    cv2.putText(frame, f'Video time: {snapshot["timestamp_s"]:.2f}s | sustained visual evidence',
                (12, 49), cv2.FONT_HERSHEY_SIMPLEX, 0.43, (220, 220, 220), 1)
    for track in tracks:
        x1, y1, x2, y2 = [round(v * scale) for v, scale in zip(track['bbox_normalized'], (width, height, width, height))]
        color = (70, 90, 255) if track['state'] == 'confirmed' else (60, 215, 255)
        if not track['present_this_frame']:
            color = (0, 150, 255)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        text = f'{track["label"]} #{track["event_id"]} {track["state"]} | presence {track["presence_ratio"]:.0%}'
        (text_width, text_height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.43, 1)
        text_x = max(0, min(x1, width - text_width - 6))
        text_y = min(height - baseline - 3, y2 + 18)
        cv2.rectangle(frame, (text_x, text_y - text_height - 3),
                      (min(width - 1, text_x + text_width + 4), text_y + baseline + 2), (22, 22, 22), -1)
        cv2.putText(frame, text, (text_x + 2, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.43, color, 1)
    return frame
