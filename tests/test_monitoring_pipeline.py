"""Product integration checks with generated video and a deterministic fake detector.

These exercise scheduling, persistence and cancellation. They do not estimate
fire/smoke accuracy, forecast quality or hardware energy consumption.
"""
import json
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import zipfile

import cv2
import numpy as np

from core.pipeline import AnalysisOptions, analyze_video
from core.records import append_feedback, export_run, load_run
from core.runtime import ROOT as PROJECT_ROOT


class FakeDetector:
    device = 'cpu'

    def __init__(self, fire_interval=None, coarse_only=False):
        self.calls = []
        self.fire_interval = fire_interval
        self.coarse_only = coarse_only

    def predict(self, frame, *, annotate=True, imgsz=640):
        # Every source frame is a uniform grayscale marker. MJPG preserves this
        # value closely; rounding identifies the decoded source frame, not the
        # number of detector calls (which differs between monitoring modes).
        index = int(round(float(frame.mean())))
        self.calls.append((index, imgsz))
        active = self.fire_interval is not None and self.fire_interval[0] <= index < self.fire_interval[1]
        if self.coarse_only:
            active = active and imgsz == 416
        detections = []
        if active:
            detections = [{'class_id': 1, 'label': 'fire', 'confidence': .95,
                           'bbox_xyxy': [15, 15, min(145, 60 + index), 105]}]
        return frame.copy() if annotate else None, detections


class MonitoringPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / 'outputs').mkdir()
        (self.root / 'models').mkdir()
        self.source = self.root / 'synthetic.avi'
        writer = cv2.VideoWriter(str(self.source), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160, 120))
        if not writer.isOpened():
            writer.release()
            self.skipTest('OpenCV MJPG writer unavailable')
        for index in range(140):
            writer.write(np.full((120, 160, 3), index, dtype=np.uint8))
        writer.release()
        capture = cv2.VideoCapture(str(self.source))
        markers = []
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            markers.append(int(round(float(frame.mean()))))
        capture.release()
        self.assertEqual(markers, list(range(140)), 'Synthetic video must retain frame markers')

    def tearDown(self):
        self.temporary.cleanup()

    def run_pipeline(self, detector, profile='full', *, stop_event=None, on_frame=None, baseline=False,
                     temporal_only=False, confirmation_profile='standard', scene_context=None):
        options = AnalysisOptions(source=str(self.source), device='cpu', monitoring_profile=profile,
                                  temporal_config=str(PROJECT_ROOT / 'configs' / 'temporal.json'),
                                  risk_config=str(PROJECT_ROOT / 'configs' / 'risk.json'),
                                  baseline=baseline, temporal_only=temporal_only,
                                  confirmation_profile=confirmation_profile, scene_context=scene_context)
        packets = []

        def receive(packet):
            packets.append({key: value for key, value in packet.items() if key != 'frame'})
            if on_frame:
                on_frame(packet)

        with patch('core.pipeline.ROOT', self.root), \
                patch('core.model_profiles.create_detector', return_value=detector):
            result = analyze_video(options, self.root / 'models' / 'fake.pt',
                                   on_frame=receive, stop_event=stop_event, report=lambda _: None)
        return result, packets

    @staticmethod
    def rows(output, filename):
        return [json.loads(line) for line in (Path(output) / filename).read_text(encoding='utf-8').splitlines()]

    def test_scene_fusion_controls_final_notifications_and_keeps_original_detector_log(self):
        from core.scene_fusion import SceneContextStore
        context = SceneContextStore()
        context.publish({'scene': {'description': '周边物体出现火焰。', 'scene': 'cooking',
            'relation': 'suspected_abnormal', 'evidence_quality': 'adequate', 'spread': 'observed',
            'visible_evidence': ['灶台周边有火焰'], 'hazard_cues': ['周边物体出现火焰'],
            'uncertainties': [], 'next_check': '核对原画面。'},
            'frame_count': 3, 'span_s': 1., 'evidence': [{'timestamp_s': 0.}, {'timestamp_s': .5}, {'timestamp_s': 1.}]})
        result, packets = self.run_pipeline(FakeDetector((0, 140)), scene_context=context)
        self.assertTrue(result['summary']['status'].startswith('completed'))
        raw = {row['timestamp_s']: row for row in self.rows(result['output'], 'risk.jsonl')}
        fused = self.rows(result['output'], 'fusion.jsonl')
        affected = [row for row in fused if row['events'] and row['fusion']['context_delta'] > 0]
        self.assertTrue(affected)
        for row in affected:
            self.assertGreater(row['score'], raw[row['timestamp_s']]['score'])
            self.assertLessEqual(row['score'] - raw[row['timestamp_s']]['score'], 25.01)
        self.assertGreater(len({row['fusion']['context_delta'] for row in affected}), 1)
        by_time = {row['timestamp_s']: row for row in fused}
        notices = self.rows(result['output'], 'alerts.jsonl')
        for notice in notices:
            if notice['kind'] != 'event_closed':
                final = by_time[notice['timestamp_s']]
                if notice['event_id'] is None:
                    self.assertEqual(final['events'], [])
                    self.assertEqual(notice['level'], final['level'])
                else:
                    event = next(item for item in final['events'] if item['event_id'] == notice['event_id'])
                    self.assertEqual(notice['level'], event['level'])
        self.assertEqual(packets[-1]['risk'], fused[-1])
        saved = load_run(result['output'])
        self.assertIn('fusion_summary', saved['events'][0])
        self.assertEqual(saved['fusion'], fused[-1])

    def test_fast_confirmation_reaches_notifications_evidence_and_saved_configuration(self):
        result, _ = self.run_pipeline(FakeDetector((0, 30)), confirmation_profile='fast')
        output = Path(result['output'])
        config = json.loads((output / 'config.json').read_text(encoding='utf-8'))
        self.assertEqual(config['confirmation_profile'], 'fast')
        self.assertEqual(config['scene_selection']['profile'], config['model_profile'])
        self.assertIn('不确定', config['scene_selection']['scene'])
        self.assertEqual(config['fast_confirmation_policy']['window_s'], .5)
        self.assertIn('additional false', config['confirmation_validation_note'])
        records = json.loads((output / 'events.json').read_text(encoding='utf-8'))
        event = records['events'][0]
        self.assertEqual(records['confirmation_profile'], 'fast')
        self.assertEqual(event['confirmed_at_s'], .5)
        self.assertEqual(event['confirmation_evidence']['confirmation_route'], 'stable_fast')
        self.assertTrue((output / event['evidence_image']).is_file())
        source = event['evidence_source']
        self.assertEqual(source['timestamp_s'], event['confirmed_at_s'])
        self.assertTrue(source['detections'])
        original = cv2.imread(str(output / source['image']))
        self.assertEqual(original.shape, (120, 160, 3))
        self.assertAlmostEqual(float(original.mean()), 5., delta=1.)
        notices = self.rows(output, 'alerts.jsonl')
        confirmation = next(row for row in notices if row['kind'] == 'event_confirmed')
        self.assertEqual(confirmation['timestamp_s'], .5)

    def test_idle_reduces_detection_calls_without_dropping_video_or_fabricating_measurements(self):
        full_detector, adaptive_detector = FakeDetector(), FakeDetector()
        full, _ = self.run_pipeline(full_detector)
        adaptive, packets = self.run_pipeline(adaptive_detector, 'adaptive')
        self.assertEqual(len(full_detector.calls), 140)
        self.assertTrue(all(size == 640 for _, size in full_detector.calls))
        self.assertLess(len(adaptive_detector.calls), len(full_detector.calls))
        self.assertEqual([index for index, size in adaptive_detector.calls if size == 960], [0, 50, 100])
        self.assertEqual(adaptive['summary']['processed_frames'], 140)
        self.assertEqual(len(packets), 140)
        logs = self.rows(adaptive['output'], 'detections.jsonl')
        temporal = self.rows(adaptive['output'], 'temporal.jsonl')
        risk = self.rows(adaptive['output'], 'risk.jsonl')
        self.assertEqual(len(logs), 140)
        self.assertEqual(len(temporal), 140)
        self.assertEqual(len(risk), 140)
        skipped = [index for index, row in enumerate(logs) if not row['observed']]
        self.assertTrue(skipped)
        for index in skipped:
            self.assertIsNone(logs[index]['image_size'])
            self.assertFalse(temporal[index]['observed'])
            self.assertEqual(temporal[index]['transitions'], [])
            self.assertEqual(risk[index]['status'], 'not_observed')
            self.assertFalse(risk[index]['fresh_evidence'])
        self.assertEqual(adaptive['summary']['monitoring']['skipped_frames'], len(skipped))
        self.assertEqual(adaptive['summary']['temporal']['confirmed_events'], 0)
        capture = cv2.VideoCapture(str(Path(adaptive['output']) / 'annotated.mp4'))
        decoded = 0
        while capture.read()[0]:
            decoded += 1
        capture.release()
        self.assertEqual(decoded, 140)

    def test_candidate_wakes_same_frame_then_dense_observation_and_portable_alert_feedback(self):
        detector = FakeDetector(fire_interval=(5, 56))
        result, packets = self.run_pipeline(detector, 'adaptive')
        self.assertEqual(detector.calls[:3], [(0, 960), (5, 416), (5, 960)])
        logs = self.rows(result['output'], 'detections.jsonl')
        self.assertTrue(all(row['observed'] and row['image_size'] == 960 for row in logs[5:61]))
        self.assertEqual(result['summary']['temporal']['confirmed_events'], 1)
        self.assertTrue(any(packet['monitoring']['state'] == 'enhanced' for packet in packets))
        self.assertEqual(packets[-1]['monitoring']['state'], 'idle')
        record = load_run(result['output'])
        self.assertEqual(len(record['events']), 1)
        self.assertTrue(record['notifications'])
        self.assertTrue(any(notice['kind'] == 'level_upgrade' for notice in record['notifications']))
        self.assertEqual(record['notifications'][-1]['kind'], 'event_closed')
        trend_rows = self.rows(result['output'], 'trend.jsonl')
        ready = [event for row in trend_rows for event in row['events'] if event['status'] == 'ready']
        self.assertTrue(ready, 'Sustained, smoothly growing synthetic evidence should reach the trend display')
        self.assertTrue(all(0 <= event['lower_coverage_ratio'] <= event['predicted_coverage_ratio']
                            <= event['upper_coverage_ratio'] <= 1 for event in ready))
        self.assertTrue(Path(result['output'], record['events'][0]['evidence_image']).is_file())
        notice = record['notifications'][0]
        feedback = append_feedback(result['output'], notice, 'false_positive', 'Synthetic QA action')
        self.assertEqual(feedback['action'], 'false_positive')
        # Human feedback is an append-only user action. It cannot silently alter
        # the confirmed event or become a training label.
        self.assertEqual(load_run(result['output'])['events'], record['events'])
        destination = self.root / 'portable-record.zip'
        export_run(result['output'], destination)
        with zipfile.ZipFile(destination) as archive:
            self.assertIsNone(archive.testzip())
            for expected in ('alerts.jsonl', 'feedback.jsonl', 'trend.jsonl', 'monitoring.jsonl', 'events.json'):
                self.assertIn(expected, archive.namelist())
            self.assertNotIn('synthetic.avi', archive.namelist())
            self.assertNotIn('fake.pt', archive.namelist())

    def test_coarse_candidate_is_a_wakeup_signal_not_temporal_positive_evidence(self):
        detector = FakeDetector(fire_interval=(5, 6), coarse_only=True)
        result, _ = self.run_pipeline(detector, 'adaptive')
        self.assertEqual(detector.calls[:3], [(0, 960), (5, 416), (5, 960)])
        logs = self.rows(result['output'], 'detections.jsonl')
        self.assertTrue(logs[5]['observed'])
        self.assertEqual(logs[5]['image_size'], 960)
        self.assertEqual(logs[5]['detections'], [])
        self.assertEqual(result['summary']['temporal']['candidates_started'], 0)
        self.assertEqual(result['summary']['temporal']['confirmed_events'], 0)

    def test_cancel_saves_finished_readable_record_and_legacy_baseline_remains_available(self):
        stop = Event()
        result, packets = self.run_pipeline(FakeDetector(), 'adaptive', stop_event=stop,
                                           on_frame=lambda packet: stop.set() if packet['processed_frames'] == 7 else None)
        self.assertEqual(result['summary']['status'], 'stopped_by_user')
        self.assertEqual(result['summary']['processed_frames'], 7)
        self.assertEqual(len(packets), 7)
        self.assertEqual(load_run(result['output'])['summary']['status'], 'stopped_by_user')
        baseline, _ = self.run_pipeline(FakeDetector(), baseline=True)
        self.assertEqual(baseline['summary']['processed_frames'], 140)
        self.assertIsNone(baseline['summary']['temporal'])
        self.assertIsNone(baseline['summary']['risk'])
        self.assertEqual(load_run(baseline['output'])['events'], [])

    def test_temporal_only_adaptive_preserves_confirmation_without_risk_trend_or_notifications(self):
        result, packets = self.run_pipeline(FakeDetector(fire_interval=(5, 56)), 'adaptive', temporal_only=True)
        self.assertEqual(result['summary']['temporal']['confirmed_events'], 1)
        self.assertIsNone(result['summary']['risk'])
        self.assertIsNone(result['summary']['notifications'])
        self.assertTrue(all(packet['risk'] is None and packet['trend'] is None for packet in packets))
        self.assertEqual(self.rows(result['output'], 'risk.jsonl'), [])
        self.assertEqual(self.rows(result['output'], 'trend.jsonl'), [])
        self.assertEqual(self.rows(result['output'], 'alerts.jsonl'), [])
        record = load_run(result['output'])
        self.assertIsNone(record['trend'])
        self.assertEqual(len(record['events']), 1)

    def test_detector_failure_preserves_error_and_closes_open_event_in_readable_record(self):
        class FailingDetector(FakeDetector):
            def predict(self, frame, **kwargs):
                if int(round(float(frame.mean()))) == 20:
                    raise RuntimeError('Synthetic detector failure')
                return super().predict(frame, **kwargs)

        with self.assertRaisesRegex(RuntimeError, 'Synthetic detector failure'):
            self.run_pipeline(FailingDetector(fire_interval=(0, 140)))
        folders = list((self.root / 'outputs').glob('run-*'))
        self.assertEqual(len(folders), 1)
        record = load_run(folders[0])
        self.assertEqual(record['summary']['status'], 'failed')
        self.assertEqual(record['summary']['processed_frames'], 20)
        self.assertEqual(record['summary']['error'], 'Synthetic detector failure')
        self.assertEqual(record['events'][0]['close_reason'], 'processing_error')
        self.assertEqual(record['notifications'][-1]['kind'], 'event_closed')
        self.assertEqual(record['notifications'][-1]['close_reason'], 'processing_error')
        self.assertEqual(len(self.rows(folders[0], 'detections.jsonl')), 20)


if __name__ == '__main__':
    unittest.main()
