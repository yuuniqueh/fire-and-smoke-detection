"""Real subprocess lifecycle and deterministic live monitoring integration.

Generated frames test engineering behavior only; no camera or model accuracy
claim is made. Physical USB/Android driver acceptance requires the user's device.
"""
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch
import zipfile

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import cv2
import numpy as np
from PySide6.QtWidgets import QApplication

from core.camera import CameraCapture, CameraDisconnected, validate_camera_index
from core.pipeline import AnalysisOptions, analyze_video
from core.records import export_run, load_run
from core.replay import ReplayTimeline, review_media
from core.runtime import ROOT
from core.storage import StorageLimitError
from ui.replay import ReplayWorker
from ui.window import MainWindow


def burst_camera(index, frames, messages, stopping):
    messages.put({'kind': 'ready', 'width': 160, 'height': 120, 'backend': 'synthetic'})
    from queue import Empty, Full
    for i in range(80):
        if stopping.is_set():
            break
        packet = (i, time.monotonic(), np.full((120, 160, 3), i, dtype=np.uint8))
        try:
            frames.put_nowait(packet)
        except Full:
            try:
                frames.get_nowait()
            except Empty:
                pass
            try:
                frames.put_nowait(packet)
            except Full:
                pass
        time.sleep(.003)
    messages.put({'kind': 'burst_complete'})
    stopping.wait(10)
    frames.cancel_join_thread()


def stalled_camera(index, frames, messages, stopping):
    messages.put({'kind': 'ready', 'width': 160, 'height': 120, 'backend': 'synthetic'})
    time.sleep(30)  # Simulate a driver that ignores cancellation.


def error_camera(index, frames, messages, stopping):
    messages.put({'kind': 'error', 'message': 'synthetic camera access denied'})


class DeterministicCamera:
    metadata = {'width': 160, 'height': 120, 'backend': 'synthetic'}
    dropped_frames = 25
    fail_at = None

    def __init__(self, index, stop_event=None):
        self.index = 0
        self.stop_event = stop_event
        self.timestamp_s = 0.
        self.origin = time.monotonic()
        self.released = False

    def read(self):
        if self.fail_at is not None and self.index == self.fail_at:
            raise CameraDisconnected('synthetic device disconnected')
        self.timestamp_s = round(self.index * .1, 6)
        frame = np.full((120, 160, 3), self.index, dtype=np.uint8)
        self.index += 1
        return True, frame

    def release(self):
        self.released = True


class FireDetector:
    device = 'cpu'

    def predict(self, frame, **kwargs):
        return frame.copy(), [{'class_id': 1, 'label': 'fire', 'confidence': .95,
                               'bbox_xyxy': [20, 20, 100, 100]}]


class CameraProcessTests(unittest.TestCase):
    def test_invalid_indices_are_rejected(self):
        for value in (True, -1, 16, 'rtsp://example', '0.0', 1.5):
            with self.assertRaises(ValueError):
                validate_camera_index(value)
        self.assertEqual(validate_camera_index('0'), 0)

    def test_latest_frames_replace_backlog_and_stop_releases_child(self):
        capture = CameraCapture(0, _target=burst_camera)
        try:
            self.assertEqual(capture._messages.get(timeout=5)['kind'], 'burst_complete')
            ok, frame = capture.read()
            self.assertTrue(ok)
            self.assertGreaterEqual(int(frame.mean()), 70)
            self.assertEqual(capture.timestamp_s, 0.)
        finally:
            capture.release()
        self.assertTrue(capture._released)
        capture.release()  # Idempotent cleanup.

    def test_driver_stall_has_bounded_timeout_and_process_termination(self):
        capture = CameraCapture(0, read_timeout=.15, _target=stalled_camera)
        started = time.monotonic()
        try:
            with self.assertRaises(CameraDisconnected):
                capture.read()
        finally:
            capture.release()
        self.assertLess(time.monotonic() - started, 3.)

    def test_open_failure_is_reported_without_model_or_run(self):
        with self.assertRaisesRegex(CameraDisconnected, 'access denied'):
            CameraCapture(0, _target=error_camera)

    def test_stop_interrupts_pending_driver_read(self):
        stopping = Event()
        capture = CameraCapture(0, stopping, _target=stalled_camera)
        try:
            stopping.set()
            self.assertEqual(capture.read(), (False, None))
        finally:
            capture.release()


class LiveWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'outputs').mkdir()
        self.options = AnalysisOptions(source='0', source_kind='camera', device='cpu',
                                       temporal_config=str(ROOT / 'configs' / 'temporal.json'),
                                       risk_config=str(ROOT / 'configs' / 'risk.json'), max_frames=17)

    def tearDown(self):
        self.temp.cleanup()

    def run_live(self, camera=DeterministicCamera, stop_event=None, on_frame=None):
        with patch('core.pipeline.ROOT', self.root), patch('core.camera.CameraCapture', camera), \
                patch('core.model_profiles.create_detector', return_value=FireDetector()):
            return analyze_video(self.options, self.root / 'fake.pt', stop_event=stop_event,
                                 on_frame=on_frame, realtime=True, report=lambda _: None)

    def test_live_timestamps_confirm_event_and_replay_does_not_use_recording_fps(self):
        result = self.run_live()
        record = load_run(result['output'])
        self.assertEqual(record['config']['source_kind'], 'camera')
        self.assertIsNone(record['config']['source_fps'])
        self.assertEqual(record['events'][0]['confirmed_at_s'], 1.)
        self.assertEqual(record['events'][0]['closed_at_s'], 1.6)
        self.assertEqual(record['summary']['capture_dropped_frames'], 25)
        media = review_media(result['output'], record['config'])
        self.assertEqual(len(media), 1)
        capture = cv2.VideoCapture(media[0]['path'])
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        capture.release()
        self.assertEqual(count, 17)
        timeline = ReplayTimeline(30, count, media[0]['timestamps'])
        self.assertEqual(timeline.locate({'start_s': .5, 'end_s': 1.5, 'focus_s': 1.})[2], 10)
        self.assertEqual(timeline.time(10), 1.)
        destination = self.root / 'record.zip'
        export_run(result['output'], destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertIn('frame-times.jsonl', archive.namelist())
            self.assertIn('电脑接收', archive.read('事件复核报告.md').decode('utf-8'))

    def test_disconnect_finalizes_records_without_claiming_fire_ended(self):
        class DisconnectingCamera(DeterministicCamera):
            fail_at = 15
        with self.assertRaisesRegex(CameraDisconnected, 'disconnected'):
            self.run_live(DisconnectingCamera)
        record = load_run(next((self.root / 'outputs').glob('run-*')))
        self.assertEqual(record['summary']['status'], 'camera_disconnected')
        self.assertEqual(record['events'][0]['close_reason'], 'camera_disconnected')
        self.assertEqual(record['events'][0]['closed_at_s'], 1.4)
        self.assertEqual(record['notifications'][-1]['close_reason'], 'camera_disconnected')

    def test_user_stop_persists_one_frame_and_no_fabricated_event(self):
        stopping = Event()
        result = self.run_live(stop_event=stopping, on_frame=lambda _: stopping.set())
        self.assertEqual(result['summary']['status'], 'stopped_by_user')
        self.assertEqual(result['summary']['processed_frames'], 1)
        self.assertEqual(result['summary']['temporal']['confirmed_events'], 0)

    def test_event_mode_saves_confirmation_clip_and_exported_time_indexes(self):
        self.options.recording_mode = 'events'
        result = self.run_live()
        record = load_run(result['output'])
        self.assertFalse((Path(result['output']) / 'annotated.mp4').exists())
        media = review_media(result['output'], record['config'], 1.)
        self.assertEqual(len(media), 1)
        self.assertTrue(Path(media[0]['timestamps']).is_file())
        destination = self.root / 'event-export.zip'
        export_run(result['output'], destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertIn('clips/clip-1.mp4', archive.namelist())
            self.assertIn('clips/clip-1.jsonl', archive.namelist())
            self.assertIn('clips.json', archive.namelist())

    def test_quota_failure_closes_source_removes_active_marker_and_saves_failure(self):
        self.options.storage_max_gb = 1
        with patch('core.storage.enforce_quota', side_effect=StorageLimitError('test quota reached')):
            with self.assertRaisesRegex(StorageLimitError, 'quota'):
                self.run_live()
        folder = next((self.root / 'outputs').glob('run-*'))
        self.assertFalse((folder / '.active').exists())
        self.assertEqual(load_run(folder)['summary']['status'], 'storage_limit_reached')

    def test_runtime_quota_guard_stops_current_record_without_deleting_it(self):
        from itertools import count
        self.options.storage_max_gb = 1
        with patch('core.pipeline.time.monotonic', side_effect=count(0, 11)), \
                patch('core.storage.enforce_quota', side_effect=[None, None, StorageLimitError('runtime quota reached')]):
            with self.assertRaisesRegex(StorageLimitError, 'runtime quota'):
                self.run_live()
        folder = next((self.root / 'outputs').glob('run-*'))
        record = load_run(folder)
        self.assertEqual(record['summary']['processed_frames'], 1)
        self.assertEqual(record['summary']['status'], 'storage_limit_reached')
        self.assertTrue((folder / 'annotated.mp4').exists())

    def test_disk_guard_stops_monitoring_with_readable_summary(self):
        with patch('core.pipeline.shutil.disk_usage') as usage:
            usage.return_value.free = 0
            result = self.run_live()
        self.assertEqual(result['summary']['status'], 'storage_limit_reached')
        self.assertTrue(result['summary']['warnings'])
        self.assertEqual(result['summary']['processed_frames'], 0)

    def test_missing_and_nonmonotonic_time_maps_are_rejected(self):
        path = self.root / 'times.jsonl'
        with self.assertRaisesRegex(ValueError, '缺失'):
            ReplayTimeline(30, 2, path)
        path.write_text('{"frame_index":0,"timestamp_s":1}\n{"frame_index":1,"timestamp_s":0}\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '无效'):
            ReplayTimeline(30, 2, path)

    def test_camera_controls_need_no_video_file_and_switch_back_preserves_file(self):
        window = MainWindow(settings_path=False)
        try:
            window.set_source(self.root / 'sample.mp4')
            window.input_kind.setCurrentIndex(window.input_kind.findData('camera'))
            self.assertTrue(window.start_button.isEnabled())
            self.assertEqual(window.start_button.text(), '开始监测')
            self.assertFalse(window.open_button.isEnabled())
            self.assertFalse(window.realtime.isEnabled())
            window.input_kind.setCurrentIndex(window.input_kind.findData('file'))
            self.assertEqual(window.source.text(), 'sample.mp4')
            self.assertEqual(window.start_button.text(), '开始分析')
        finally:
            window.close()

    def test_replay_worker_seeks_live_record_using_observation_time(self):
        result = self.run_live()
        record = load_run(result['output'])
        media = review_media(result['output'], record['config'])[0]
        worker = ReplayWorker(media['path'], {'start_s': .5, 'end_s': 1.5, 'focus_s': 1.},
                              timestamps=media['timestamps'])
        errors = []
        worker.failed.connect(errors.append)
        worker.start()
        packet = None
        try:
            deadline = time.monotonic() + 3
            while packet is None and time.monotonic() < deadline:
                self.app.processEvents()
                packet, _ = worker.preview()
                time.sleep(.01)
            self.assertFalse(errors)
            self.assertIsNotNone(packet)
            self.assertEqual(packet['frame_index'], 10)
            self.assertEqual(packet['timestamp_s'], 1.)
        finally:
            worker.stop()
            self.assertTrue(worker.wait(3000))


if __name__ == '__main__':
    unittest.main()
