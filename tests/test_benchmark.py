import unittest
from experiments.benchmark_firesense import aggregate


class BenchmarkAggregationTests(unittest.TestCase):
    def case(self, negatives, false_hints, delays, total, detected, excluded=False):
        values = {'ground_truth_scene_events': total, 'detected_scene_events': detected,
                  'missed_scene_events': total - detected, 'false_hint_episodes': false_hints,
                  'negative_duration_s': negatives, 'ground_truth_positive_frames': 0,
                  'active_hint_frames': 0, 'true_positive_hint_frames': 0,
                  'false_positive_hint_frames': 0, 'false_negative_hint_frames': 0,
                  'first_active_hint_delays_s': delays}
        return {'split': 'internal_holdout', 'metrics': {'smoke': {'excluded': excluded, 'temporal': values}}}

    def test_rates_use_total_time_and_delays_use_detected_events(self):
        cases = [self.case(60, 2, [1, 2], 3, 2), self.case(180, 1, [6], 1, 1),
                 self.case(300, 99, [99], 1, 1, excluded=True)]
        result = aggregate(cases, 'internal_holdout', 'smoke', 'temporal')
        self.assertEqual(result['videos_included'], 2)
        self.assertEqual(result['false_hint_episodes_per_negative_minute'], .75)
        self.assertEqual(result['scene_event_recall'], .75)
        self.assertEqual(result['mean_first_active_hint_delay_s'], 3)
        self.assertEqual(result['delay_denominator_detected_events'], 3)

    def test_empty_aggregate_has_no_fake_zero_rates(self):
        result = aggregate([], 'development', 'smoke', 'temporal')
        self.assertIsNone(result['scene_event_recall'])
        self.assertIsNone(result['false_hint_episodes_per_negative_minute'])
        self.assertIsNone(result['mean_first_active_hint_delay_s'])


if __name__ == '__main__':
    unittest.main()
