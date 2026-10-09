"""Background analysis with bounded preview memory and cooperative cancellation."""
from collections import deque
from threading import Event, Lock

from PySide6.QtCore import QThread, Signal

from core.pipeline import analyze_video
from core.records import export_run, load_curve, load_run
from core.runtime import ROOT


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
        self.output = None

    def stop(self):
        self.stop_event.set()

    def _started(self, info):
        self.output = info['output']
        self.started_run.emit(info)

    def _frame(self, packet):
        with self._lock:
            self._latest = packet
            risk = packet['risk']
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

    def run(self):
        try:
            result = analyze_video(self.options, ROOT / 'models' / 'best.pt', on_frame=self._frame,
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
