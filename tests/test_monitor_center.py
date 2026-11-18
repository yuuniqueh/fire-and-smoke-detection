"""Point identity, evidence provenance, bounded context changes and review persistence."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from core.devices import DeviceRegistry, validate_device
from core.scene_review import contextual_priority, local_endpoint, save_scene_review, validate_scene
from core.records import append_feedback, event_review_state, export_run, render_review_report
from core.storage import inventory


def scene(**changes):
    return {**{'description': '画面中可见桌面上的蜡烛和火焰。', 'scene': 'candle', 'relation': 'expected_burning',
        'evidence_quality': 'adequate', 'spread': 'not_observed', 'visible_evidence': ['火焰位于蜡烛烛芯上方'],
        'hazard_cues': [], 'uncertainties': ['画面外情况未知'], 'next_check': '核对周围是否存在可燃物。'}, **changes}


class ContextReviewTests(unittest.TestCase):
    def test_scene_cannot_erase_detection_or_downgrade_multiple_levels(self):
        base = {'score': 48., 'level': 'MEDIUM', 'label': 'fire', 'fresh_evidence': True,
                'features': {'coverage_ratio_smoothed': .02, 'positive_duration_s': 5., 'growth_pp_per_s': 0.}}
        result = contextual_priority(base, scene(), frame_count=3, span_s=1.2)
        self.assertGreater(result['score'], 36.)
        self.assertLess(result['score'], 48.)
        self.assertEqual(result['level'], 'MEDIUM')
        self.assertTrue(result['original_alert_retained'])
        self.assertEqual(base['score'], 48.)
        result = contextual_priority({**base, 'level': 'HIGH', 'score': 70}, scene(), frame_count=3, span_s=1.2)
        self.assertIn(result['level'], ('MEDIUM', 'HIGH'))

    def test_uncertain_single_frame_stale_growth_and_smoke_do_not_get_candle_discount(self):
        base = {'score': 48., 'level': 'MEDIUM', 'label': 'fire', 'fresh_evidence': True}
        cases = [(base, scene(relation='unclear'), 3, 1.2, 0), (base, scene(), 1, 0, 0),
                 (base, scene(), 3, 1.2, 20), ({**base, 'features': {'growth_pp_per_s': 1.}}, scene(), 3, 1.2, 0),
                 ({**base, 'label': 'smoke'}, scene(), 3, 1.2, 0), ({**base, 'fresh_evidence': False}, scene(), 3, 1.2, 0)]
        for baseline, value, count, span, age in cases:
            with self.subTest(baseline=baseline, age=age, count=count):
                result = contextual_priority(baseline, value, frame_count=count, span_s=span, age_s=age)
                self.assertEqual(result['context_delta'], 0)

    def test_hazard_cue_prevents_normal_scene_discount_and_raises_priority(self):
        result = contextual_priority({'score': 40, 'level': 'LOW'}, scene(hazard_cues=['邻近纸张已出现火焰']), frame_count=3, span_s=1.2)
        self.assertGreater(result['context_delta'], 0.)
        self.assertNotEqual(result['context_delta'], 10.)
        self.assertEqual(result['level'], 'MEDIUM')

    def test_single_frame_forces_unknown_spread_even_when_model_claims_it(self):
        result = validate_scene(scene(spread='observed'), 1, 0)
        self.assertEqual((result['spread'], result['evidence_quality']), ('unknown', 'limited'))
        with self.assertRaises(ValueError):
            validate_scene(scene(description=''))

    def test_schema_wrapper_is_accepted_only_when_actual_values_are_valid(self):
        result = validate_scene({'type': 'object', 'properties': scene()}, 3, 1.2)
        self.assertEqual(result['scene'], 'candle')
        with self.assertRaises(ValueError):
            validate_scene({'type': 'object', 'properties': {'description': {'type': 'string'}}}, 3, 1.2)

    def test_only_loopback_service_is_allowed(self):
        self.assertEqual(local_endpoint('http://127.0.0.1:11435/'), 'http://127.0.0.1:11435')
        for value in ('https://example.com:443', 'http://192.168.1.2:11435', 'http://localhost:11435/api',
                      'http://u:p@localhost:11435', 'http://localhost:bad', 'http://localhost:11435?q=1'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                local_endpoint(value)


class PointAndRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def point(self, identifier='one'):
        return {'id': identifier, 'name': '东侧入口', 'location': '教学楼一层', 'kind': 'file', 'source': str(self.root / '录像.mp4')}

    def test_simultaneous_channels_get_distinct_runs_with_identical_clock_values(self):
        from core.pipeline import create_run_directory
        moment = datetime(2026, 11, 16, 12, 0, 0, 100)
        with ThreadPoolExecutor(max_workers=4) as pool:
            paths = list(pool.map(lambda _: create_run_directory(self.root, moment), range(12)))
        self.assertEqual(len(set(paths)), 12)
        self.assertTrue(all(path.is_dir() for path in paths))

    def test_device_locations_round_trip_and_bad_lists_are_not_silently_accepted(self):
        registry = DeviceRegistry(self.root / 'points.json')
        registry.save([self.point(), self.point('two')])
        self.assertEqual([d['location'] for d in registry.load()], ['教学楼一层', '教学楼一层'])
        with self.assertRaises(ValueError):
            registry.save([self.point(), self.point()])
        for value in ({**self.point(), 'location': ''}, {**self.point(), 'kind': 'rtsp'},
                      {**self.point(), 'kind': 'camera', 'source': '16'}):
            with self.assertRaises(ValueError):
                validate_device(value)

    def make_run(self):
        run = self.root / 'run-20261116-000000-000001'
        run.mkdir()
        for filename, obj in {'summary.json': {'status': 'completed'}, 'config.json': {'monitoring_point': self.point()},
            'events.json': {'events': [{'event_id': 1, 'label': 'fire', 'confirmed_at_s': 1., 'risk_summary': {'peak_score': 48., 'peak_level': 'MEDIUM'}}]}}.items():
            (run / filename).write_text(json.dumps(obj), encoding='utf-8')
        notices = [{'event_id': 1, 'notification_id': 'n1', 'kind': 'event_confirmed', 'level': 'MEDIUM'}]
        (run / 'alerts.jsonl').write_text(json.dumps(notices[0]) + '\n', encoding='utf-8')
        return run, notices

    def test_normal_use_survives_acknowledgement_but_upgrade_requires_new_review(self):
        run, notices = self.make_run()
        append_feedback(run, notices[0], 'normal_use', '人工回看为正常炉火')
        acknowledgement = append_feedback(run, notices[0], 'acknowledged')
        feedback = {'n1': acknowledgement}
        self.assertEqual(event_review_state(1, notices, feedback), 'normal_use')
        notices.append({'event_id': 1, 'notification_id': 'n2', 'kind': 'level_upgrade'})
        self.assertEqual(event_review_state(1, notices, feedback), 'pending')

    def test_scene_evidence_is_hashed_exported_and_report_includes_location(self):
        run, _ = self.make_run()
        data = b'local generated evidence'
        frame = {'timestamp_s': 1., 'jpeg': data, 'sha256': hashlib.sha256(data).hexdigest()}
        record = {'scene': scene(), 'model': 'test-fixture-only', 'frame_count': 1, 'span_s': 0, 'elapsed_s': .1}
        save_scene_review(run, 1, [frame], record, device=self.point(), baseline={'score': 48, 'level': 'MEDIUM'})
        report = render_review_report(run)
        self.assertIn('教学楼一层', report)
        self.assertIn('AI场景复核', report)
        destination = self.root / 'export.zip'
        export_run(run, destination)
        with zipfile.ZipFile(destination) as archive:
            names = archive.namelist()
            self.assertIn('scene-reviews.jsonl', names)
            self.assertTrue(any(n.startswith('scene-evidence/') for n in names))
        with self.assertRaises(ValueError):
            save_scene_review(run, 1, [{**frame, 'sha256': 'wrong'}], record)
        (run / '.scene-active').write_text('review')
        self.assertFalse(inventory(self.root)['records'][0]['eligible'])


class MonitorDeskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_scene_worker_keeps_native_qt_event_dispatch_and_cleanup_working(self):
        from PySide6.QtCore import QCoreApplication, QEvent, QObject
        from ui.monitor_worker import SceneReviewWorker
        worker = SceneReviewWorker(('point', 'run', 1), {'event_id': 1}, {}, 'http://127.0.0.1:11435', 'test-only')
        child = QObject(worker)
        self.assertIs(child.parent(), worker)
        QCoreApplication.sendEvent(worker, QEvent(QEvent.Type.User))
        worker.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_events_with_identical_numbers_stay_with_their_own_points_and_shutdown_is_safe(self):
        from ui.monitor_center import MonitorCenter
        with tempfile.TemporaryDirectory() as directory:
            desk = MonitorCenter(registry_path=Path(directory) / 'points.json')
            desk.auto_ai.setChecked(False)
            points = [{'id': i, 'name': '设备' + i, 'location': '位置' + i, 'kind': 'file', 'source': str(Path(directory) / (i + '.mp4'))} for i in ('a', 'b')]
            desk.set_devices(points)
            for point in points:
                key = (point['id'], 'run-' + point['id'], 1)
                desk.events[key] = {'event_id': 1, 'device': point, 'output': key[1], 'label': 'fire',
                    'confirmed_at_s': 1., 'reference_s': 1., 'last_timestamp_s': 1., 'closed': True,
                    'baseline': {'score': 25., 'level': 'LOW'}, 'review': 'pending', 'scene_status': '未复核', 'scene_record': None,
                    'frames': [{'sha256': point['id'], 'jpeg': b'', 'timestamp_s': 1.}]}
            desk.events[('b', 'old-run-b', 1)] = {**desk.events[('b', 'run-b', 1)], 'output': 'old-run-b'}
            desk.outputs['b'] = 'run-b'
            desk.set_event_mode(True)
            desk._render_events()
            self.assertEqual(desk.event_table.rowCount(), 3)
            row = next(row for row in range(desk.event_table.rowCount())
                       if desk.event_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == ('b', 'run-b', 1))
            desk._event_clicked(desk.event_table.item(row, 0))
            self.assertEqual(desk.selected_key, ('b', 'run-b', 1))
            self.assertIn('位置b', desk.event_location.text())
            desk._priority(desk.events[('a', 'run-a', 1)])
            self.assertEqual(desk.evidence_frames[0]['sha256'], 'b')
            desk.event_search.setText('位置a')
            self.assertEqual(desk.selected_key, ('a', 'run-a', 1))
            self.assertEqual(desk.evidence_frames[0]['sha256'], 'a')
            desk.event_search.setText('不存在的位置')
            self.assertIsNone(desk.selected_key)
            self.assertEqual(desk.evidence_frames, [])
            self.assertEqual(desk.event_location.text(), '设备与位置会显示在这里')
            self.assertTrue(desk.shutdown())
            desk.close()


if __name__ == '__main__':
    unittest.main()
