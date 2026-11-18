import math
import unittest

from core.trend import TrendConfig, TrendEngine


def packet(timestamp, coverage=.1, fresh=True, event_id=1, label='fire'):
    return {'timestamp_s': timestamp, 'dominant_event_id': event_id,
            'events': [{'event_id': event_id, 'label': label, 'fresh_evidence': fresh,
                        'features': {'fresh_evidence': fresh, 'coverage_ratio_smoothed': coverage}}]}


class TrendTests(unittest.TestCase):
    def feed(self, engine, values, start=0.0):
        result = None
        for index, value in enumerate(values):
            result = engine.update(packet(start + index * .2, value))
        return result['events'][0]

    def test_linear_coverage_growth_has_correct_units_and_horizon(self):
        engine = TrendEngine()
        result = self.feed(engine, [.1 + .01 * index * .2 for index in range(26)])
        self.assertEqual(result['status'], 'ready')
        self.assertAlmostEqual(result['growth_pp_per_s'], 1.0)
        self.assertAlmostEqual(result['coverage_ratio'], .15)
        self.assertAlmostEqual(result['predicted_coverage_ratio'], .18)
        self.assertAlmostEqual(result['projection'][-1]['timestamp_s'], 8.0)
        self.assertLess(result['lower_coverage_ratio'], result['predicted_coverage_ratio'])
        self.assertGreater(result['upper_coverage_ratio'], result['predicted_coverage_ratio'])
        self.assertIn('不是置信区间', result['reason'])

    def test_static_coverage_can_be_ready_without_artificial_growth(self):
        result = self.feed(TrendEngine(), [.1] * 21)
        self.assertEqual(result['status'], 'ready')
        self.assertAlmostEqual(result['growth_pp_per_s'], 0.0)
        self.assertAlmostEqual(result['predicted_coverage_ratio'], .1)

    def test_short_series_never_projects(self):
        result = self.feed(TrendEngine(), [.1, .11, .12, .13, .14])
        self.assertEqual(result['status'], 'warming_up')
        self.assertIsNone(result['predicted_coverage_ratio'])
        self.assertEqual(result['projection'], [])

    def test_missing_evidence_is_not_zero_or_forecast(self):
        engine = TrendEngine()
        previous = self.feed(engine, [.1] * 21)
        missing = engine.update(packet(4.2, None, fresh=False))['events'][0]
        self.assertEqual(missing['status'], 'temporarily_missing')
        self.assertEqual(missing['coverage_ratio'], previous['coverage_ratio'])
        self.assertTrue(missing['history'])
        self.assertIsNone(missing['growth_pp_per_s'])
        self.assertIsNone(missing['predicted_coverage_ratio'])

    def test_long_gap_restarts_fresh_history_even_with_same_event_id(self):
        engine = TrendEngine()
        self.feed(engine, [.1] * 21)
        engine.update(packet(4.7, None, fresh=False))
        resumed = engine.update(packet(4.8, .2))['events'][0]
        self.assertEqual(resumed['status'], 'warming_up')
        self.assertEqual(len(resumed['history']), 1)
        self.assertEqual(resumed['history'][0]['coverage_ratio'], .2)
        self.assertIsNone(resumed['predicted_coverage_ratio'])

    def test_large_gap_without_explicit_missing_frame_also_resets(self):
        engine = TrendEngine()
        self.feed(engine, [.1] * 21)
        resumed = engine.update(packet(8.0, .15))['events'][0]
        self.assertEqual(resumed['status'], 'warming_up')
        self.assertEqual(len(resumed['history']), 1)

    def test_oscillating_history_with_no_linear_explanation_is_unstable(self):
        result = self.feed(TrendEngine(), [.08 if index % 2 else .18 for index in range(31)])
        self.assertEqual(result['status'], 'unstable')
        self.assertIsNone(result['predicted_coverage_ratio'])
        self.assertEqual(result['projection'], [])

    def test_prediction_and_empirical_band_are_bounded(self):
        rising = self.feed(TrendEngine(), [.4 + index * .015 for index in range(31)])
        falling = self.feed(TrendEngine(), [.6 - index * .015 for index in range(31)])
        self.assertEqual(rising['predicted_coverage_ratio'], 1.0)
        self.assertEqual(falling['predicted_coverage_ratio'], 0.0)
        for event in (rising, falling):
            for point in event['projection']:
                for key in ('coverage_ratio', 'lower_coverage_ratio', 'upper_coverage_ratio'):
                    self.assertTrue(0 <= point[key] <= 1)

    def test_history_bounded_at_high_frame_rate_and_closed_events_removed(self):
        engine = TrendEngine()
        for index in range(4001):
            result = engine.update(packet(index / 100.0, .1))
        self.assertLessEqual(len(result['events'][0]['history']), engine.history_limit)
        self.assertGreaterEqual(result['events'][0]['history'][0]['timestamp_s'], 34.0 - 1e-8)
        engine.update({'timestamp_s': 40.01, 'events': [], 'dominant_event_id': None})
        self.assertEqual(engine.states, {})
        new = engine.update(packet(40.02, .5))['events'][0]
        self.assertEqual(len(new['history']), 1)

    def test_large_timestamp_does_not_change_fitted_slope(self):
        result = self.feed(TrendEngine(), [.1 + .002 * index for index in range(31)], start=1e9)
        self.assertEqual(result['status'], 'ready')
        self.assertAlmostEqual(result['growth_pp_per_s'], 1.0, places=5)

    def test_candidate_and_separate_event_histories_do_not_leak(self):
        engine = TrendEngine()
        candidate = engine.update({'timestamp_s': 0, 'events': [], 'dominant_event_id': None})
        self.assertEqual(candidate['events'], [])
        for index in range(1, 22):
            current = packet(index * .2, .1)
            other = packet(index * .2, .5, event_id=2, label='smoke')['events'][0]
            current['events'].append(other)
            result = engine.update(current)
        self.assertEqual([event['coverage_ratio'] for event in result['events']], [.1, .5])
        self.assertEqual(result['dominant_event_id'], 1)

    def test_invalid_config_inputs_and_nonmonotonic_timestamps_rejected(self):
        for kwargs in ({'window_s': 0}, {'min_samples': 1}, {'min_samples': 100},
                       {'sample_interval_s': 1}, {'min_span_s': 7}, {'horizon_s': math.inf},
                       {'min_r2': 2}, {'flat_range_ratio': -.1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TrendConfig(**kwargs)
        engine = TrendEngine()
        engine.update(packet(0))
        with self.assertRaises(ValueError):
            engine.update(packet(0))
        with self.assertRaises(ValueError):
            TrendEngine().update(packet(0, math.nan))
        with self.assertRaises(ValueError):
            TrendEngine().update(packet(0, 1.1))


if __name__ == '__main__':
    unittest.main()
