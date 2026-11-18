"""Experimental confirmation on fixed region snapshots; no application wiring."""
from dataclasses import asdict, dataclass
import math

from core.temporal import TemporalConfig, iou


@dataclass(frozen=True)
class AccumulatorConfig:
    confirm_evidence_s: float = 1.0
    cap_s: float = 2.0
    missing_decay_per_s: float = .25
    confidence_floor: float = .35
    minimum_gain: float = .5

    def __post_init__(self):
        for value in asdict(self).values():
            if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value):
                raise ValueError('Accumulator parameters must be finite numbers')
        if not 0 < self.confirm_evidence_s <= self.cap_s or self.missing_decay_per_s <= 0:
            raise ValueError('Invalid evidence threshold, cap or decay')
        if not 0 <= self.confidence_floor < 1 or not 0 < self.minimum_gain <= 1:
            raise ValueError('Invalid confidence weighting')


class EvidenceAccumulator:
    """Bounded leaky support, independently per fire region, smoke unchanged.

    Uses the previous observation over each sample interval, as the original
    tracker does. Model confidence controls a heuristic gain, not probability.
    Region disappearance discards memory; neither gaps nor other regions can
    create a confirmation. Confirmation persists until the region closes.
    """
    def __init__(self, config=None, temporal=None):
        self.config = config or AccumulatorConfig()
        self.temporal = temporal or TemporalConfig()
        self.states = {}
        self.last_timestamp = None
        self.recent_alerts = []
        self.closed_events = []
        self.finished = False

    def gain(self, confidence):
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('Invalid confidence')
        c = self.config
        scale = max(0., min(1., (confidence-c.confidence_floor)/(1-c.confidence_floor)))
        return c.minimum_gain+(1-c.minimum_gain)*scale

    def _close(self, event_id, timestamp, reason):
        memory = self.states.pop(event_id)
        confirmed = memory['confirmed_at_s'] is not None
        track = memory['last_track']
        if confirmed:
            self.closed_events.append({'event_id':event_id,'label':'fire',
                'started_at_s':track['started_at_s'],'confirmed_at_s':memory['confirmed_at_s'],
                'last_seen_s':track['last_seen_s'],'closed_at_s':timestamp,'close_reason':reason,
                'alert_emitted':memory['alert_emitted'],'confirmation_evidence':memory['confirmation_evidence'],
                'last_bbox_normalized':track['bbox_normalized'],
                'association_bbox_normalized':track['association_bbox_normalized']})
        return {'type':'event_closed' if confirmed else 'candidate_discarded',
                'event_id':event_id,'label':'fire','timestamp_s':timestamp,'reason':reason}

    def update(self, snapshot):
        if self.finished:
            raise ValueError('Stream finished')
        timestamp = snapshot['timestamp_s']
        if (not math.isfinite(timestamp) or timestamp < 0 or
                self.last_timestamp is not None and timestamp <= self.last_timestamp):
            raise ValueError('Timestamps must increase strictly')
        fire = [t for t in snapshot['tracks'] if t['label']=='fire']
        active = {t['event_id'] for t in fire}
        if len(active)!=len(fire):
            raise ValueError('Duplicate fire region identifiers')
        transitions = [dict(t) for t in snapshot['transitions'] if t['label']=='smoke']
        reasons = {t['event_id']:t.get('reason','detection_timeout') for t in snapshot['transitions']
                   if t['type'] in ('event_closed','candidate_discarded')}
        for key in list(self.states):
            if key not in active:
                transitions.append(self._close(key,timestamp,reasons.get(key,'detection_timeout')))
        self.recent_alerts = [a for a in self.recent_alerts if timestamp-a['timestamp_s']<=self.temporal.cooldown_s]
        tracks = [dict(t) for t in snapshot['tracks'] if t['label']=='smoke']
        for track in fire:
            key = track['event_id']
            if key not in self.states:
                self.states[key] = {'energy':0.,'last_t':timestamp,'previous_present':False,
                    'previous_gain':0.,'confirmed_at_s':None,'alert_emitted':False,
                    'confirmation_evidence':None,'last_track':track}
                transitions.append({'type':'candidate_started','event_id':key,'label':'fire','timestamp_s':timestamp})
            memory = self.states[key]
            dt = timestamp-memory['last_t']
            if memory['previous_present']:
                memory['energy'] = min(self.config.cap_s,memory['energy']+dt*memory['previous_gain'])
            else:
                memory['energy'] = max(0.,memory['energy']-dt*self.config.missing_decay_per_s)
            present = track['present_this_frame']
            gain = self.gain(track['confidence']) if present else 0.
            evidence = {k:track[k] for k in ('observation_s','positive_s','presence_ratio')}
            evidence.update(method='bounded_leaky_support_v1',evidence_memory_s=round(memory['energy'],6),
                            evidence_threshold_s=self.config.confirm_evidence_s,
                            current_confidence_gain=round(gain,6))
            ready = (present and track['observation_s']+1e-6 >= self.temporal.min_observation_s
                     and track['positive_s']+1e-6 >= self.temporal.min_positive_s
                     and memory['energy']+1e-6 >= self.config.confirm_evidence_s)
            if memory['confirmed_at_s'] is None and ready:
                memory['confirmed_at_s'] = timestamp
                memory['confirmation_evidence'] = evidence.copy()
                box = track['association_bbox_normalized']
                suppressed = any(iou(box,a['box'])>=self.temporal.match_iou for a in self.recent_alerts)
                memory['alert_emitted'] = not suppressed
                if not suppressed:
                    self.recent_alerts.append({'timestamp_s':timestamp,'box':box.copy()})
                transitions.append({'type':'event_confirmed','event_id':key,'label':'fire','timestamp_s':timestamp,
                    'alert_emitted':not suppressed,'cooldown_suppressed':suppressed,'evidence':evidence.copy()})
            memory.update(last_t=timestamp,previous_present=present,previous_gain=gain,last_track=track)
            tracks.append({**track,'state':'confirmed' if memory['confirmed_at_s'] is not None else 'pending',
                'confirmed_at_s':memory['confirmed_at_s'],'alert_emitted':memory['alert_emitted'],
                'confirmation_method':evidence['method'],'evidence_memory_s':evidence['evidence_memory_s']})
        self.last_timestamp = timestamp
        return {'timestamp_s':timestamp,'tracks':tracks,'transitions':transitions}

    def finish(self, timestamp):
        if not math.isfinite(timestamp) or timestamp < (self.last_timestamp or 0):
            raise ValueError('Invalid stream end timestamp')
        if self.finished:
            return []
        transitions = [self._close(key,timestamp,'stream_end') for key in list(self.states)]
        self.finished = True
        return transitions
