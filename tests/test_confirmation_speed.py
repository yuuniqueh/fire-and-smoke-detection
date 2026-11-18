"""Fast response must retain spatial identity and real temporal evidence."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from core.event_profiles import create_event_tracker
from core.pipeline import AnalysisOptions, analyze_video
from core.preferences import PreferencesStore
from core.temporal import EventTracker, TemporalConfig
from tests.test_temporal import detection
from ui.window import MainWindow


class ConfirmationTests(unittest.TestCase):
    def confirmations(self, tracker, timestamps, choose):
        return [transition for t in timestamps
                for transition in tracker.update(round(t, 6), choose(t), (640, 480))['transitions']
                if transition['type'] == 'event_confirmed']

    def test_stable_target_confirms_in_half_second_across_fps_and_associations(self):
        for fps in (10, 30, 60):
            for association in ('original', 'regions'):
                for label in ('fire', 'smoke'):
                    with self.subTest(fps=fps, association=association, label=label):
                        tracker = create_event_tracker(TemporalConfig(), association, 'fast')
                        transitions = self.confirmations(tracker, [i/fps for i in range(2*fps)],
                                                         lambda _: [detection(label=label)])
                        self.assertEqual(len(transitions), 1)
                        self.assertAlmostEqual(transitions[0]['timestamp_s'], .5, places=5)
                        self.assertEqual(transitions[0]['evidence']['confirmation_route'], 'stable_fast')

    def test_flash_and_alternating_distant_regions_never_fast_confirm(self):
        for choose in (lambda t: [detection()] if t < .3 else [],
                       lambda t: [detection((50, 80, 160, 200) if round(t*10) % 2 == 0
                                            else (450, 80, 560, 200))]):
            tracker = EventTracker(confirmation_profile='fast')
            self.assertEqual(self.confirmations(tracker, [i/10 for i in range(31)], choose), [])

    def test_sparse_or_two_samples_do_not_fabricate_fast_confirmation(self):
        for timestamps in ([0, .5], [0, .05, .1, .5], [0, 10]):
            with self.subTest(timestamps=timestamps):
                tracker = EventTracker(confirmation_profile='fast')
                self.assertEqual(self.confirmations(tracker, timestamps, lambda _: [detection()]), [])

    def test_intermittent_target_still_has_standard_confirmation_route(self):
        # 80% duty cycle: adequate standard evidence, no 90%-positive half-second window.
        choose = lambda t: [] if round(t*10) % 5 == 4 else [detection()]
        timestamps = [i/10 for i in range(31)]
        standard = self.confirmations(EventTracker(), timestamps, choose)
        fast = self.confirmations(EventTracker(confirmation_profile='fast'), timestamps, choose)
        self.assertEqual(fast, standard)
        self.assertEqual(len(fast), 1)

    def test_default_keeps_historical_behavior_and_bad_profile_fails_before_io(self):
        self.assertEqual(AnalysisOptions('none').confirmation_profile, 'standard')
        with self.assertRaises(ValueError):
            analyze_video(AnalysisOptions('does-not-exist', confirmation_profile='typo'), 'no-model')

    def test_recent_user_trace_replays_earlier_without_changing_detections(self):
        path = Path(__file__).resolve().parents[1] / 'outputs/run-20261011-163122-725247/detections.jsonl'
        if not path.is_file():
            self.skipTest('Local diagnostic trace is not distributed.')
        data = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
        first = {}
        for profile in ('standard', 'fast'):
            tracker = EventTracker(confirmation_profile=profile)
            transitions = [t for p in data for t in tracker.update(p['timestamp_s'], p['detections'], p['frame_size'])['transitions']
                           if t['type'] == 'event_confirmed']
            first[profile] = transitions[0]['timestamp_s']
        self.assertAlmostEqual(first['standard'], 6.2062)
        self.assertAlmostEqual(first['fast'], 2.635967)

    def test_ui_selection_is_persisted_passed_to_worker_and_locked_during_run(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'settings.json'
            with patch('ui.window.list_runs', return_value=[]):
                window = MainWindow(settings_path=path)
            try:
                self.assertEqual(window.confirmation_profile.currentData(), 'standard')
                window.confirmation_profile.setCurrentIndex(window.confirmation_profile.findData('fast'))
                window._save_preferences()
                self.assertEqual(PreferencesStore(path).load()[0]['confirmation_profile'], 'fast')
                source = Path(directory) / 'video.avi'
                source.touch()
                window.set_source(source)
                with patch.object(window, '_attach_task') as attach:
                    window.start_analysis()
                worker = attach.call_args.args[0]
                self.assertEqual(worker.options.confirmation_profile, 'fast')
                window.task = worker
                window._set_controls()
                self.assertFalse(window.confirmation_profile.isEnabled())
                window.task = None
                window.reset_preferences()
                self.assertEqual(window.confirmation_profile.currentData(), 'standard')
            finally:
                window.task = None
                window.close()
                window.deleteLater()
                app.processEvents()


if __name__ == '__main__':
    unittest.main()
