"""Experimental bounded fire regions; original time gates remain unchanged."""
from dataclasses import dataclass
import math

from core.temporal import EventTracker, TemporalConfig, group_detections, iou


@dataclass(frozen=True)
class RegionConfig:
    anchor_padding: float = .25
    max_extent_factor: float = 2.0
    min_containment: float = .6

    def __post_init__(self):
        for value in (self.anchor_padding, self.max_extent_factor, self.min_containment):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError('Region parameters must be finite numbers')
        if not 0 <= self.anchor_padding <= .5 or not 1 <= self.max_extent_factor <= 3:
            raise ValueError('Invalid bounded region extent')
        if not 0 < self.min_containment <= 1:
            raise ValueError('Invalid containment ratio')


def union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def containment(a, b):
    intersection = max(0, min(a[2], b[2])-max(a[0], b[0])) * max(0, min(a[3], b[3])-max(a[1], b[1]))
    smaller = min((a[2]-a[0])*(a[3]-a[1]), (b[2]-b[0])*(b[3]-b[1]))
    return intersection/smaller if smaller > 0 else 0.


@dataclass
class Region:
    event_id: int
    anchor: list
    envelope: list
    actual: list
    last_seen_s: float
    tracker: EventTracker
    captured_closed: int = 0


class StableRegionTracker:
    """Fire-only regional evidence; smoke uses the unchanged EventTracker.

    Multiple current fragments can support one region once per sample. Missing
    detections never supply positive evidence. Anchors cannot drift indefinitely.
    This class is not selected by the application without a separate adoption.
    """
    def __init__(self, config=None, region_config=None, confirmation_profile='standard'):
        self.config = config or TemporalConfig()
        self.region_config = region_config or RegionConfig()
        self.regions = {}
        self.confirmation_profile = confirmation_profile
        self.smoke = EventTracker(self.config, confirmation_profile)
        self.next_fire_id = 1
        self.last_timestamp = None
        self.finished = False
        self.closed_events = []
        self.recent_fire_alerts = []
        self.counts = {'candidates_started': 0, 'confirmed_events': 0, 'alerts_emitted': 0}
        self.smoke_closed_captured = 0

    def _limits(self, anchor):
        pad = self.region_config.anchor_padding
        width, height = anchor[2]-anchor[0], anchor[3]-anchor[1]
        return [max(0., anchor[0]-pad*width), max(0., anchor[1]-pad*height),
                min(1., anchor[2]+pad*width), min(1., anchor[3]+pad*height)]

    def _score(self, region, box):
        direct = self._direct_match(region.actual, box)
        if direct is not None:
            return 1.+direct
        anchor = region.anchor
        limits = self._limits(anchor)
        center_x, center_y = (box[0]+box[2])/2, (box[1]+box[3])/2
        if not (limits[0] <= center_x <= limits[2] and limits[1] <= center_y <= limits[3]):
            return None
        if (box[2]-box[0] > (anchor[2]-anchor[0])*self.region_config.max_extent_factor or
                box[3]-box[1] > (anchor[3]-anchor[1])*self.region_config.max_extent_factor):
            return None
        overlap, contained = iou(region.envelope, box), containment(region.envelope, box)
        if overlap < self.config.match_iou and contained < self.region_config.min_containment:
            return None
        return overlap + .1*contained

    def _direct_match(self, previous, box):
        limit = self.region_config.max_extent_factor
        ratios = [(box[i+2]-box[i])/(previous[i+2]-previous[i]) for i in (0, 1)]
        overlap = iou(previous, box)
        if overlap >= self.config.match_iou and all(1/limit <= ratio <= limit for ratio in ratios):
            return overlap
        return None

    def _follow_motion(self, region, actual):
        limits = self._limits(region.anchor)
        cx, cy = (actual[0]+actual[2])/2, (actual[1]+actual[3])/2
        if limits[0] <= cx <= limits[2] and limits[1] <= cy <= limits[3]:
            return
        if self._direct_match(region.actual, actual) is None:
            return
        dx = cx-(region.actual[0]+region.actual[2])/2
        dy = cy-(region.actual[1]+region.actual[3])/2
        # Translate the bounded reference; never leave a growing trail behind it.
        for name in ('anchor', 'envelope'):
            old = getattr(region, name)
            setattr(region, name, [max(0., min(1., value+delta))
                                   for value, delta in zip(old, (dx, dy, dx, dy))])

    def _expand(self, region, actual):
        candidate = union([region.envelope, actual])
        limits = self._limits(region.anchor)
        region.envelope = [max(candidate[0], limits[0]), max(candidate[1], limits[1]),
                           min(candidate[2], limits[2]), min(candidate[3], limits[3])]

    def _fire_transition(self, transition, region, timestamp):
        result = {**transition, 'event_id': region.event_id}
        if result['type'] == 'event_confirmed':
            suppressed = any(timestamp-a['timestamp_s'] <= self.config.cooldown_s and
                             iou(a['box'], region.envelope) >= self.config.match_iou
                             for a in self.recent_fire_alerts)
            emitted = not suppressed
            result.update(alert_emitted=emitted, cooldown_suppressed=suppressed)
            for track in region.tracker.tracks.values():
                track.alert_emitted = emitted
            if emitted:
                self.recent_fire_alerts.append({'timestamp_s': timestamp, 'box': region.envelope.copy()})
        return result

    def _capture_fire(self, region):
        events = region.tracker.closed_events
        for event in events[region.captured_closed:]:
            self.closed_events.append({**event, 'event_id': region.event_id,
                                       'last_bbox_normalized': region.actual.copy(),
                                       'association_bbox_normalized': region.envelope.copy()})
        region.captured_closed = len(events)

    def _count(self, transitions):
        for tr in transitions:
            if tr['type'] == 'candidate_started':
                self.counts['candidates_started'] += 1
            elif tr['type'] == 'event_confirmed':
                self.counts['confirmed_events'] += 1
                self.counts['alerts_emitted'] += bool(tr['alert_emitted'])

    def update(self, timestamp, detections, frame_size):
        if self.finished:
            raise ValueError('Stream finished')
        if (not math.isfinite(timestamp) or timestamp < 0 or
                self.last_timestamp is not None and timestamp <= self.last_timestamp):
            raise ValueError('Timestamps must increase strictly')
        groups = group_detections(detections, frame_size, self.config.cluster_padding)
        self.last_timestamp = timestamp
        transitions, snapshots = [], []
        for key, region in list(self.regions.items()):
            if timestamp-region.last_seen_s > self.config.max_gap_s+1e-9:
                expired = region.tracker.update(timestamp, [], frame_size)
                transitions.extend(self._fire_transition(t, region, timestamp) for t in expired['transitions'])
                self._capture_fire(region)
                del self.regions[key]
        assignments = {}
        unmatched = []
        # Match against regions that existed before this sample; no chain growth.
        for group in (g for g in groups if g['label'] == 'fire'):
            pairs = [(self._score(region, group['box']), key) for key, region in self.regions.items()]
            pairs = [(score, key) for score, key in pairs if score is not None]
            if pairs:
                _, key = max(pairs, key=lambda item: (item[0], -item[1]))
                assignments.setdefault(key, []).append(group)
            else:
                unmatched.append(group)
        for group in unmatched:
            key = self.next_fire_id
            self.next_fire_id += 2
            box = group['box'].copy()
            self.regions[key] = Region(key, box.copy(), box.copy(), box.copy(), timestamp,
                                       EventTracker(self.config, self.confirmation_profile))
            assignments[key] = [group]
        width, height = frame_size
        self.recent_fire_alerts = [a for a in self.recent_fire_alerts if timestamp-a['timestamp_s'] <= self.config.cooldown_s]
        for key, region in self.regions.items():
            supported = assignments.get(key, [])
            proxy = []
            if supported:
                actual = union([g['box'] for g in supported])
                self._follow_motion(region, actual)
                region.actual = actual
                region.last_seen_s = timestamp
                self._expand(region, region.actual)
                proxy = [{'label': 'fire', 'confidence': max(g['confidence'] for g in supported),
                          # Geometry is handled above. Each independent time
                          # accumulator gets a fixed proxy, never a moving box.
                          'bbox_xyxy': [0, 0, width, height]}]
            state = region.tracker.update(timestamp, proxy, frame_size)
            transitions.extend(self._fire_transition(t, region, timestamp) for t in state['transitions'])
            for track in state['tracks']:
                actual_track = next(iter(region.tracker.tracks.values()))
                snapshots.append({**track, 'event_id': key, 'bbox_normalized': region.actual.copy(),
                                  'association_bbox_normalized': region.envelope.copy(),
                                  'alert_emitted': actual_track.alert_emitted})
            self._capture_fire(region)
        smoke_state = self.smoke.update(timestamp, [d for d in detections if d['label'] == 'smoke'], frame_size)
        snapshots.extend({**t, 'event_id': 2*t['event_id']} for t in smoke_state['tracks'])
        transitions.extend({**t, 'event_id': 2*t['event_id']} for t in smoke_state['transitions'])
        self._capture_smoke()
        self._count(transitions)
        return {'timestamp_s': timestamp, 'tracks': snapshots, 'transitions': transitions}

    def _capture_smoke(self):
        for event in self.smoke.closed_events[self.smoke_closed_captured:]:
            self.closed_events.append({**event, 'event_id': 2*event['event_id']})
        self.smoke_closed_captured = len(self.smoke.closed_events)

    def finish(self, timestamp, reason='stream_end'):
        if not math.isfinite(timestamp) or timestamp < (self.last_timestamp or 0):
            raise ValueError('Invalid stream end timestamp')
        if self.finished:
            return []
        transitions = []
        for region in self.regions.values():
            transitions.extend(self._fire_transition(t, region, timestamp)
                               for t in region.tracker.finish(timestamp, reason))
            self._capture_fire(region)
        self.regions.clear()
        transitions.extend({**t, 'event_id': 2*t['event_id']} for t in self.smoke.finish(timestamp, reason))
        self._capture_smoke()
        self.finished = True
        return transitions

    def summary(self):
        return {**self.counts, 'cooldown_s': self.config.cooldown_s}
