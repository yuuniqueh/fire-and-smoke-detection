"""Behavior checks for temporal/spatial evidence and lifecycle boundaries."""
import unittest
from core.temporal import EventTracker, TemporalConfig


def detection(box=(100, 100, 220, 240), label='fire'):
    return {'label': label, 'confidence': 0.9, 'bbox_xyxy': list(box)}


class TemporalTests(unittest.TestCase):
    def feed(self, tracker, timestamps, choose):
        return [tracker.update(round(t, 6), choose(t), (640, 480)) for t in timestamps]

    def test_sustained_confirmation_uses_seconds_across_frame_rates(self):
        for fps in (10, 30, 60):
            with self.subTest(fps=fps):
                tracker = EventTracker()
                self.feed(tracker, [i/fps for i in range(2*fps)], lambda _: [detection()])
                tracker.finish(2.0)
                self.assertEqual(tracker.confirmed_count, 1)
                self.assertAlmostEqual(tracker.closed_events[0]['confirmation_delay_s'], 1.0, places=5)

    def test_short_flash_never_confirms(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(21)], lambda t: [detection()] if t < 0.3 else [])
        self.assertEqual(tracker.confirmed_count, 0)
        self.assertEqual(len(tracker.tracks), 0)

    def test_no_detection_has_no_candidates(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(21)], lambda _: [])
        self.assertEqual(tracker.candidates_started, 0)

    def test_short_dropout_keeps_one_event(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(31)],
                  lambda t: [] if 1.2 <= t <= 1.5 else [detection()])
        tracker.finish(3.1)
        self.assertEqual(tracker.confirmed_count, 1)
        self.assertEqual(tracker.alert_count, 1)

    def test_timeout_closes_with_last_observation_and_decision_time(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(21)], lambda t: [detection()] if t <= 1.1 else [])
        event = tracker.closed_events[0]
        self.assertEqual(event['last_seen_s'], 1.1)
        self.assertEqual(event['close_reason'], 'detection_timeout')
        self.assertAlmostEqual(event['closed_at_s'], 1.8)

    def test_alternating_far_regions_cannot_share_evidence(self):
        tracker = EventTracker()
        for i in range(31):
            box = (50, 80, 160, 200) if i % 2 == 0 else (450, 80, 560, 200)
            tracker.update(i/10, [detection(box)], (640, 480))
        self.assertEqual(tracker.confirmed_count, 0)

    def test_overlapping_fragments_are_one_event(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(21)],
                  lambda _: [detection(), detection((150, 130, 250, 250))])
        self.assertEqual(tracker.confirmed_count, 1)
        self.assertEqual(tracker.candidates_started, 1)

    def test_classes_have_independent_evidence(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(21)],
                  lambda t: [detection()] + ([detection(label='smoke')] if t < 0.2 else []))
        tracker.finish(2.1)
        self.assertEqual([event['label'] for event in tracker.closed_events], ['fire'])

    def test_large_unsampled_gap_does_not_invent_persistence(self):
        tracker = EventTracker()
        tracker.update(0.0, [detection()], (640, 480))
        snapshot = tracker.update(10.0, [detection()], (640, 480))
        self.assertEqual(tracker.confirmed_count, 0)
        self.assertEqual(snapshot['tracks'][0]['started_at_s'], 10.0)

    def test_dense_positive_samples_do_not_replace_time_weighted_evidence(self):
        tracker = EventTracker()
        timestamps = [i/10 for i in range(6)] + [i/100 for i in range(60, 131)]
        self.feed(tracker, timestamps, lambda t: [detection()] if t == 0 or t >= 0.6 else [])
        # Most samples are positive, but only 0.8 / 1.3 seconds are supported.
        self.assertEqual(tracker.confirmed_count, 0)

    def test_cooldown_suppresses_alert_but_keeps_confirmed_event(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(30)],
                  lambda t: [detection()] if t <= 1.0 or t >= 1.8 else [])
        tracker.finish(3.0)
        self.assertEqual(tracker.confirmed_count, 2)
        self.assertEqual(tracker.alert_count, 1)
        self.assertEqual([event['alert_emitted'] for event in tracker.closed_events], [True, False])

    def test_finish_is_a_stream_boundary_and_does_not_confirm_pending(self):
        tracker = EventTracker()
        tracker.update(0.0, [detection()], (640, 480))
        tracker.finish(0.1, 'stopped_by_user')
        self.assertEqual(tracker.confirmed_count, 0)
        self.assertEqual(tracker.closed_events, [])
        with self.assertRaises(ValueError):
            tracker.update(0.2, [detection()], (640, 480))

    def test_eof_does_not_claim_physical_event_ended(self):
        tracker = EventTracker()
        self.feed(tracker, [i/10 for i in range(21)], lambda _: [detection()])
        tracker.finish(2.1, 'stream_end')
        self.assertEqual(tracker.closed_events[0]['close_reason'], 'stream_end')
        self.assertEqual(tracker.closed_events[0]['last_seen_s'], 2.0)

    def test_fresh_tracker_resets_all_state(self):
        first = EventTracker()
        self.feed(first, [i/10 for i in range(21)], lambda _: [detection()])
        fresh = EventTracker()
        snapshot = fresh.update(0.0, [], (640, 480))
        self.assertEqual(snapshot['tracks'], [])
        self.assertEqual(fresh.confirmed_count, 0)

    def test_invalid_config_and_time_are_rejected(self):
        for kwargs in ({'window_s': 0}, {'min_presence_ratio': float('nan')},
                       {'min_observation_s': 2.0}, {'cooldown_s': -1}, {'match_iou': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TemporalConfig(**kwargs)
        tracker = EventTracker()
        tracker.update(0.0, [], (640, 480))
        for value in (0.0, -1.0, float('nan')):
            with self.assertRaises(ValueError):
                tracker.update(value, [], (640, 480))


if __name__ == '__main__':
    unittest.main()

