import unittest
from types import SimpleNamespace

from core.event_profiles import create_event_tracker, validate_association
from core.event_regions import StableRegionTracker
from core.pipeline import AnalysisOptions, analyze_video
from core.temporal import EventTracker, TemporalConfig


class EventProfilesTest(unittest.TestCase):
    def test_default_and_explicit_selection(self):
        config = TemporalConfig()
        self.assertEqual(AnalysisOptions('demo').association_profile, 'original')
        self.assertIs(type(create_event_tracker(config)), EventTracker)
        candidate = create_event_tracker(config, 'regions')
        self.assertIsInstance(candidate, StableRegionTracker)
        self.assertIs(candidate.config, config)

    def test_candidate_guard_before_any_video_or_model_io(self):
        validate_association('original')
        validate_association('regions', 'brightlight')
        for association, model, baseline in [('regions', 'original', False),
                                             ('regions', 'brightlight', True),
                                             ('unknown', 'brightlight', False)]:
            options = AnalysisOptions('does-not-exist', model_profile=model,
                                      association_profile=association, baseline=baseline)
            with self.assertRaises(ValueError):
                analyze_video(options, 'does-not-exist')
        with self.assertRaises(ValueError):
            create_event_tracker(TemporalConfig(), 'unknown')


if __name__ == '__main__':
    unittest.main()
