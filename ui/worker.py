"""Background analysis with bounded preview memory and cooperative cancellation."""
from collections import deque
from threading import Event, Lock
from time import monotonic

from PySide6.QtCore import QThread, Signal

from core.pipeline import analyze_video
from core.records import export_run, load_curve, load_run
from core.runtime import ASSET_ROOT, ROOT


class CameraPreviewWorker(QThread):
    """Preview only: no model, recording, risk estimate or event creation."""
    outcome = Signal(dict)

    def __init__(self, index, parent=None):
        super().__init__(parent)
        self.index = index
        self.stop_event = Event()
        self._lock = Lock()
        self._latest = None

    def stop(self):
        self.stop_event.set()

    def preview(self):
        with self._lock:
            value, self._latest = self._latest, None
            return value

    def run(self):
        from core.camera import CameraCapture
        capture = None
        try:
            capture = CameraCapture(self.index, self.stop_event)
            while not self.stop_event.is_set():
                ok, frame = capture.read()
                if not ok:
                    break
                with self._lock:
                    self._latest = {'frame': frame, 'timestamp_s': capture.timestamp_s,
                                    'frame_age_s': monotonic() - capture.origin - capture.timestamp_s}
            self.outcome.emit({'ok': True})
        except Exception as exc:
            self.outcome.emit({'ok': False, 'error': str(exc)})
        finally:
            if capture:
                capture.release()


class AnalysisWorker(QThread):
    started_run = Signal(dict)
    outcome = Signal(dict)

    def __init__(self, options, realtime=True, parent=None):
        super().__init__(parent)
        self.options = options
        self.realtime = realtime
        self.stop_event = Event()
        self._lock = Lock()
        self._latest = None
        self._curve = deque(maxlen=1201)
        self._last_curve_time = -float('inf')
        self._notifications = deque(maxlen=500)
        self.output = None
        from core.scene_fusion import SceneContextStore
        self.scene_context = SceneContextStore()
        self.options.scene_context = self.scene_context
        self.scene_evidence_enabled = False
        self._scene_frames = deque(maxlen=3)
        self._scene_events = {}
        self._scene_last_s = -float('inf')

    def stop(self):
        self.stop_event.set()

    def _started(self, info):
        self.output = info['output']
        self.started_run.emit(info)

    def _frame(self, packet):
        scene_frame = None
        if (self.scene_evidence_enabled and packet.get('raw_frame') is not None
                and packet['timestamp_s'] - self._scene_last_s >= .5):
            from core.scene_review import encode_frame
            scene_frame = encode_frame(packet['raw_frame'], packet['timestamp_s'])
            self._scene_last_s = packet['timestamp_s']
        with self._lock:
            if scene_frame:
                self._scene_frames.append(scene_frame)
            if self.scene_evidence_enabled:
                for track in (packet.get('temporal') or {}).get('tracks', []):
                    if track['state'] == 'confirmed':
                        self._scene_events[track['event_id']] = list(self._scene_frames)
                while len(self._scene_events) > 200:
                    self._scene_events.pop(next(iter(self._scene_events)))
            self._latest = packet
            self._notifications.extend(packet.get('notifications') or [])
            risk = packet.get('risk')
            timestamp = packet['timestamp_s']
            if timestamp - self._last_curve_time + 1e-6 >= 0.1:
                self._curve.append((timestamp, risk['score'] if risk and risk['status'] == 'assessed' else None))
                self._last_curve_time = timestamp
            while self._curve and self._curve[0][0] < timestamp - 120:
                self._curve.popleft()

    def preview(self):
        with self._lock:
            packet = self._latest
            self._latest = None
            return packet, list(self._curve)

    def scene_evidence(self, event_id=None):
        with self._lock:
            return list(self._scene_frames if event_id is None else self._scene_events.get(event_id, []))

    def take_notifications(self):
        """Drain discrete alerts separately from replaceable preview frames."""
        with self._lock:
            notices = list(self._notifications)
            self._notifications.clear()
            return notices

    def run(self):
        try:
            result = analyze_video(self.options, ASSET_ROOT / 'models' / 'best.pt', on_frame=self._frame,
                                   on_started=self._started, stop_event=self.stop_event,
                                   realtime=self.realtime, report=lambda message: None)
            self.outcome.emit({'ok': True, **result})
        except Exception as exc:
            self.outcome.emit({'ok': False, 'error': str(exc), 'output': self.output})


class ExportWorker(QThread):
    outcome = Signal(dict)

    def __init__(self, run_path, destination, parent=None):
        super().__init__(parent)
        self.run_path = run_path
        self.destination = destination

    def run(self):
        try:
            self.outcome.emit({'ok': True, 'path': export_run(self.run_path, self.destination)})
        except Exception as exc:
            self.outcome.emit({'ok': False, 'error': str(exc)})


class HistoryWorker(QThread):
    outcome = Signal(dict)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path

    def run(self):
        try:
            self.outcome.emit({'ok': True, 'record': load_run(self.path), 'curve': load_curve(self.path)})
        except Exception as exc:
            self.outcome.emit({'ok': False, 'error': str(exc)})


class DiagnosticsWorker(QThread):
    outcome = Signal(dict)
    progress = Signal(str)

    def __init__(self, device, profile, parent=None):
        super().__init__(parent)
        self.device, self.profile = device, profile

    def run(self):
        try:
            from core.diagnostics import collect_diagnostics
            self.outcome.emit({'ok': True, 'report': collect_diagnostics(device=self.device, profile=self.profile, progress=self.progress.emit)})
        except Exception as exc:
            self.outcome.emit({'ok': False, 'error': str(exc)})


class StorageWorker(QThread):
    outcome = Signal(dict)

    def __init__(self, outputs, policy, *, exclude=(), approved=None, parent=None):
        super().__init__(parent)
        self.outputs, self.policy = outputs, policy
        self.exclude, self.approved = exclude, approved

    def run(self):
        try:
            from core.storage import cleanup_plan, execute_cleanup
            result = execute_cleanup(self.outputs, self.approved, exclude=self.exclude) if self.approved else None
            plan = cleanup_plan(self.outputs, exclude=self.exclude, **self.policy)
            self.outcome.emit({'ok': True, 'plan': plan, 'cleanup': result})
        except Exception as exc:
            self.outcome.emit({'ok': False, 'error': str(exc)})
