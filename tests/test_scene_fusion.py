"""Scene timing, target identity and the actual final-alert fusion path."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from core.scene_fusion import SceneContextStore, SceneFusionEngine
from core.scene_review import save_scene_review
from core.records import render_review_report, export_run, load_run
from ui.scene_panel import SceneDescriptionPanel


def observation(timestamp=1., event_id=None, **changes):
    scene = {'description': '桌面上可见蜡烛，火焰位于烛芯上方。', 'scene': 'candle',
        'relation': 'expected_burning', 'evidence_quality': 'adequate', 'spread': 'not_observed',
        'visible_evidence': ['火焰位于烛芯上方'], 'hazard_cues': [], 'uncertainties': ['画面外未知'],
        'next_check': '人工核对周边可燃物。'}
    scene.update(changes)
    return {'scene': scene, 'event_id': event_id, 'frame_count': 3, 'span_s': 1.,
            'evidence': [{'timestamp_s': timestamp - 1.}, {'timestamp_s': timestamp - .5}, {'timestamp_s': timestamp}],
            'model': 'fixture-only', 'elapsed_s': .01}


def assessment(timestamp=2., **changes):
    row = {'event_id': 1, 'label': 'fire', 'score': 48., 'level': 'MEDIUM', 'fresh_evidence': True,
           'features': {'growth_pp_per_s': 0., 'coverage_ratio_smoothed': .04, 'positive_duration_s': 2.},
           'score_components': {'class_base': 25.}, 'reasons': ['visual event']}
    row.update(changes)
    return {'timestamp_s': timestamp, 'status': 'assessed', 'events': [row], 'score': row['score'],
            'level': row['level'], 'fresh_evidence': row['fresh_evidence'], 'dominant_event_id': 1,
            'reasons': [], 'level_changes': []}


class FusionTests(unittest.TestCase):
    def setUp(self):
        self.store = SceneContextStore()
        self.engine = SceneFusionEngine(self.store)

    def test_scene_changes_final_level_and_keeps_original_evidence(self):
        self.store.publish(observation())
        raw = assessment()
        final = self.engine.update(raw)
        self.assertLess(final['score'], raw['score'])
        self.assertEqual(final['level'], 'MEDIUM')  # Retains the detector's exit hysteresis.
        self.assertEqual((raw['score'], raw['level']), (48., 'MEDIUM'))
        self.assertEqual(final['events'][0]['baseline_score'], 48.)

    def test_stale_future_uncertain_growth_smoke_and_multiple_events_do_not_reduce(self):
        self.store.publish(observation())
        for raw in (assessment(14.), assessment(.5), assessment(label='smoke'),
                    assessment(features={'growth_pp_per_s': 1.})):
            self.assertEqual(self.engine.update(raw)['score'], raw['score'])
        raw = assessment()
        raw['events'].append({**raw['events'][0], 'event_id': 2})
        self.assertEqual(self.engine.update(raw)['score'], 48.)
        self.store.publish(observation(2., relation='unclear'))
        self.assertEqual(self.engine.update(assessment(3.))['score'], 48.)

    def test_event_and_video_identity_and_late_result_are_isolated(self):
        self.store.publish(observation(3., event_id=2))
        self.store.publish(observation(1.))
        self.assertEqual(self.store.snapshot()['event_id'], 2)
        self.assertEqual(self.engine.update(assessment(4.))['score'], 48.)
        independent = SceneFusionEngine(SceneContextStore())
        self.assertEqual(independent.update(assessment(4.))['score'], 48.)

    def test_ai_only_anomaly_is_visible_without_inventing_a_confirmed_event(self):
        self.store.publish(observation(hazard_cues=['周边纸张出现火焰']))
        raw = assessment()
        raw.update(events=[], level='NORMAL', score=0., dominant_event_id=None)
        final = self.engine.update(raw)
        self.assertTrue(final['fusion']['scene_only_hazard'])
        self.assertEqual(final['events'], [])
        current = self.engine.update(assessment())
        self.assertGreater(current['score'], 48.)
        self.assertNotEqual(current['fusion']['context_delta'], 10.)

    def test_normal_use_cannot_increase_a_level_during_raw_hysteresis(self):
        self.store.publish(observation())
        final = self.engine.update(assessment(level='LOW', score=75.))
        self.assertEqual(final['level'], 'LOW')


class NoEventPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_live_video_without_any_event_starts_and_refreshes_scene_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            panel = SceneDescriptionPanel()
            panel.set_target(folder, None, {'source': 'local.avi'}, running=True)
            frames = [{'timestamp_s': t, 'jpeg': b'fixture', 'sha256': 'test'} for t in (0., .5, 1.)]
            panel.observe_live(folder, None, frames)
            self.assertTrue(panel.available)
            with patch.object(panel, 'start_review') as start:
                panel._tick()
                start.assert_called_once()
            panel.auto_attempted.add(panel.target_key)
            panel.last_requested[panel.target_key] = 1.
            panel.observe_live(folder, None, [{**f, 'timestamp_s': f['timestamp_s'] + 8.} for f in frames])
            with patch.object(panel, 'start_review') as start:
                panel._tick()
                start.assert_called_once()
            panel.shutdown()
            panel.close()

    def test_no_event_scene_evidence_and_export_have_no_fake_event_number(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / 'run-20261117-000000-000001'
            run.mkdir()
            for name, value in {'summary.json': {'status': 'completed'}, 'config.json': {}, 'events.json': {'events': []}}.items():
                (run / name).write_text(json.dumps(value), encoding='utf-8')
            data = b'fixture-only'
            frames = [{'timestamp_s': t, 'jpeg': data, 'sha256': hashlib.sha256(data).hexdigest()} for t in (0., .5, 1.)]
            record = save_scene_review(run, None, frames, observation())
            self.assertEqual(record['review_scope'], 'scene')
            report = render_review_report(run)
            self.assertIn('视频场景（无需事件触发）', report)
            self.assertNotIn('事件 #None', report)
            export_run(run, root / 'result.zip')
            from ui.monitor_center import MonitorCenter
            point = {'id': 'noeventpoint', 'name': '测试入口', 'location': '本地工程录像',
                     'kind': 'file', 'source': str(root / 'fixture.mp4')}
            (run / 'config.json').write_text(json.dumps({'monitoring_point': point}), encoding='utf-8')
            desk = MonitorCenter(registry_path=root / 'points.json')
            desk.set_devices([point])
            desk.restore_saved_events([load_run(run)])
            self.assertEqual(desk.events, {})
            desk.select_device_event(point['id'])
            self.assertEqual(desk.selected_key, (point['id'], str(run.resolve()), None))
            self.assertIn('蜡烛', desk.scene_text.toPlainText())
            desk.shutdown()
            desk.close()

    def test_event_refresh_keeps_the_opening_scene_description_visible(self):
        with tempfile.TemporaryDirectory() as folder:
            panel = SceneDescriptionPanel()
            key = (str(Path(folder).resolve()), None)
            panel.records[key] = observation()
            panel.set_target(folder, {'event_id': 1, 'label': 'fire'}, {}, running=True)
            self.assertIn('蜡烛', panel.description.text())
            self.assertIn('无需事件触发', panel.status.text())
            panel.shutdown()
            panel.close()

    def test_live_scene_targets_current_event_even_if_an_older_event_is_selected(self):
        from ui.window import MainWindow
        from ui.worker import AnalysisWorker
        from core.pipeline import AnalysisOptions
        with tempfile.TemporaryDirectory() as folder:
            window = MainWindow(settings_path=False)
            window.task = AnalysisWorker(AnalysisOptions(source='fixture.avi'))
            window.current_run = Path(folder)
            window.events = {2: {'event_id': 2, 'label': 'fire'}}
            window.scene_description.latest_assessment = {'dominant_event_id': 2}
            window._show_event_review(None)
            self.assertEqual(window.scene_description.target_key[1], 2)
            window.task = None
            window.close()


if __name__ == '__main__':
    unittest.main()
