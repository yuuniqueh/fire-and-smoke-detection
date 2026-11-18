"""Independent monitor channels, retaining bounded raw evidence for scene review."""
from collections import deque
from copy import deepcopy
from time import monotonic
from PySide6.QtCore import QThread, Signal

from core.scene_review import analyze_scene, encode_frame, save_scene_review, saved_event_frames
from ui.worker import AnalysisWorker


class MonitorWorker(AnalysisWorker):
    def __init__(self, options, parent=None):
        super().__init__(options, realtime=True, parent=parent)
        self.scene_evidence_enabled = True
        self._raw_frames = deque(maxlen=4)
        self._monitor_events = {}
        self._last_evidence_s = -float('inf')

    def _frame(self, packet):
        super()._frame(packet)
        timestamp = packet['timestamp_s']
        raw = None
        if timestamp - self._last_evidence_s >= .5:
            raw = encode_frame(packet['raw_frame'], timestamp)
            self._last_evidence_s = timestamp
        with self._lock:
            if raw:
                self._raw_frames.append(raw)
            temporal = packet.get('temporal') or {}
            assessments = {r['event_id']: r for r in (packet.get('risk') or {}).get('events', [])}
            peaks = packet.get('risk_peaks') or {}
            for track in temporal.get('tracks', []):
                if track['state'] != 'confirmed':
                    continue
                identifier = track['event_id']
                if identifier not in self._monitor_events:
                    self._monitor_events[identifier] = {'event_id': identifier, 'label': track['label'],
                        'confirmed_at_s': track.get('confirmed_at_s', timestamp), 'frames': list(self._raw_frames)[-3:],
                        'reference_s': timestamp, 'closed': False}
                row = self._monitor_events[identifier]
                row['assessment'] = deepcopy(assessments.get(identifier) or row.get('assessment') or {})
                row['risk_summary'] = deepcopy(peaks.get(identifier) or row.get('risk_summary') or {})
                row['last_timestamp_s'] = timestamp
                row['latest_frames'] = list(self._raw_frames)[-3:]
                row['output'] = str(self.output or '')
            for event in packet.get('closed_events', []):
                row = self._monitor_events.get(event['event_id'])
                if row:
                    row['closed'] = True
            for notice in packet.get('notifications', []):
                row = self._monitor_events.get(notice['event_id'])
                if row and notice.get('kind') != 'event_closed':
                    row['latest_notice_id'] = notice['notification_id']
                    if notice.get('kind') == 'level_upgrade':
                        row.update(frames=list(self._raw_frames)[-3:], reference_s=timestamp)
            while len(self._monitor_events) > 200:
                self._monitor_events.pop(next(iter(self._monitor_events)))

    def monitor_events(self):
        with self._lock:
            # JPEG bytes are immutable. Copy metadata without duplicating video buffers.
            return [{**row, 'assessment': deepcopy(row.get('assessment', {})),
                     'risk_summary': deepcopy(row.get('risk_summary', {})), 'frames': list(row['frames']),
                     'latest_frames': list(row.get('latest_frames', row['frames']))}
                    for row in self._monitor_events.values()]


class SceneReviewWorker(QThread):
    """Network/file work off the GUI; outcomes are queued to the GUI owner."""
    outcome = Signal(dict)
    progress = Signal(dict)

    def __init__(self, event_key, event, device, endpoint, model, parent=None, *, profile='fast', use_cache=True, context_store=None):
        super().__init__(parent)
        # QObject.event() is a Qt virtual method: never shadow it with data.
        self.event_key, self.review_event, self.device = event_key, dict(event), dict(device)
        self.endpoint, self.model = endpoint, model
        self.review_profile, self.use_cache = profile, use_cache
        self.context_store = context_store

    def run(self):
        marker = None
        outcome = None
        started = monotonic()
        try:
            from pathlib import Path
            marker = Path(self.review_event['output']) / '.scene-active'
            marker.write_text('AI scene review in progress', encoding='ascii')
            frames = self.review_event['frames'] or saved_event_frames(self.review_event['output'], self.review_event['event_id'])
            prepared_s = monotonic() - started
            first_seen = None
            def report_progress(value):
                nonlocal first_seen
                if self.isInterruptionRequested():
                    raise InterruptedError('AI复核已取消。')
                if first_seen is None and len(value.get('description', '')) >= 4:
                    first_seen = round(monotonic() - started, 3)
                self.progress.emit({**value, 'key': self.event_key, 'elapsed_s': round(monotonic() - started, 3)})
            fallback = False
            try:
                result = analyze_scene(frames, endpoint=self.endpoint, model=self.model, timeout=90., on_progress=report_progress,
                                       profile=self.review_profile, use_cache=self.use_cache)
            except ValueError:
                if self.review_profile != 'fast' or self.isInterruptionRequested():
                    raise
                # A quick preview is never promoted to a verdict. Retry the
                # original full review once if the short output is incomplete.
                fallback = True
                result = analyze_scene(frames, endpoint=self.endpoint, model=self.model, timeout=90., on_progress=report_progress,
                                       profile='detailed', use_cache=self.use_cache)
            result['fallback_used'] = fallback
            result['frame_preparation_s'] = round(prepared_s, 3)
            result['total_elapsed_s'] = round(monotonic() - started, 3)
            result['first_description_total_s'] = first_seen
            if self.isInterruptionRequested():
                outcome = {'ok': False, 'cancelled': True, 'key': self.event_key}
                return
            record = save_scene_review(self.review_event['output'], self.review_event['event_id'], frames, result,
                                       device={k: self.device[k] for k in ('id', 'name', 'location')},
                                       baseline=self.review_event['baseline'])
            if self.context_store is not None:
                self.context_store.publish(record)
            outcome = {'ok': True, 'key': self.event_key, 'record': record}
        except Exception as exc:
            outcome = {'ok': False, 'key': self.event_key, 'error': str(exc), 'cancelled': self.isInterruptionRequested()}
        finally:
            if marker is not None:
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass
            self.outcome.emit(outcome or {'ok': False, 'key': self.event_key, 'error': 'AI复核未完成。'})
