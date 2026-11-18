"""Behavioral safeguards for experimental event regions."""
import unittest

from core.event_regions import RegionConfig, StableRegionTracker
from core.temporal import EventTracker, TemporalConfig


def detection(box=(100, 100, 300, 300), label='fire'):
    return {'label': label, 'confidence': .9, 'bbox_xyxy': list(box)}


class RegionTests(unittest.TestCase):
    def test_local_fragments_share_an_initially_observed_region(self):
        tracker = StableRegionTracker()
        tracker.update(0., [detection()], (640, 480))
        for i in range(1, 21):
            box = (100, 100, 180, 300) if i % 2 else (220, 100, 300, 300)
            state = tracker.update(i/10, [detection(box)], (640, 480))
        self.assertEqual(tracker.summary()['candidates_started'], 1)
        self.assertEqual(tracker.summary()['confirmed_events'], 1)
        self.assertEqual(state['tracks'][0]['bbox_normalized'], [220/640, 100/480, 300/640, 300/480])

    def test_far_alternating_regions_cannot_pool_positive_time(self):
        tracker = StableRegionTracker()
        for i in range(31):
            box = (50, 80, 160, 200) if i % 2 else (450, 80, 560, 200)
            tracker.update(i/10, [detection(box)], (640, 480))
        self.assertEqual(tracker.summary()['confirmed_events'], 0)
        self.assertEqual(tracker.summary()['candidates_started'], 2)

    def test_no_detection_does_not_create_or_confirm_evidence(self):
        tracker = StableRegionTracker()
        for i in range(31):
            tracker.update(i/10, [detection()] if i % 2 else [], (640, 480))
        self.assertEqual(tracker.summary()['confirmed_events'], 0)
        fresh = StableRegionTracker()
        self.assertEqual(fresh.update(0., [], (640, 480))['tracks'], [])

    def test_timeout_drops_old_evidence_and_resets_region(self):
        tracker = StableRegionTracker()
        tracker.update(0., [detection()], (640, 480))
        state = tracker.update(1., [detection()], (640, 480))
        self.assertEqual(tracker.summary()['confirmed_events'], 0)
        self.assertEqual(state['tracks'][0]['started_at_s'], 1.)
        self.assertNotEqual(state['tracks'][0]['event_id'], 1)

    def test_direct_motion_moves_reference_without_trailing_growth(self):
        tracker = StableRegionTracker()
        for i in range(21):
            left = 50+20*i
            tracker.update(i/10, [detection((left, 100, left+100, 200))], (640, 480))
        self.assertEqual(tracker.summary()['candidates_started'], 1)
        self.assertEqual(tracker.summary()['confirmed_events'], 1)
        region = next(iter(tracker.regions.values()))
        self.assertLessEqual(region.envelope[2]-region.envelope[0], 150/640+1e-9)
        self.assertGreater(region.anchor[0], 50/640)

    def test_fragments_count_once_per_sample(self):
        tracker = StableRegionTracker()
        tracker.update(0., [detection()], (640, 480))
        for i in range(1, 6):
            state = tracker.update(i/10, [detection((100, 100, 180, 300)), detection((220, 100, 300, 300))], (640, 480))
        self.assertEqual(len(state['tracks']), 1)
        self.assertAlmostEqual(state['tracks'][0]['positive_s'], .5)
        self.assertEqual(tracker.summary()['confirmed_events'], 0)

    def test_translating_reference_cannot_create_duplicate_local_identifiers(self):
        tracker = StableRegionTracker()
        for timestamp, box in ((0., (100, 100, 120, 120)), (.1, (90, 90, 130, 130)),
                               (.2, (115, 90, 155, 130))):
            state = tracker.update(timestamp, [detection(box)], (640, 480))
            self.assertEqual(len(state['tracks']), 1)
        self.assertEqual(tracker.summary()['candidates_started'], 1)
        self.assertEqual(state['tracks'][0]['event_id'], 1)

    def test_smoke_behavior_is_unchanged_and_identifiers_are_unique(self):
        tracker, smoke = StableRegionTracker(), EventTracker()
        for i in range(31):
            detections = [detection()]+([detection((400, 100, 500, 300), 'smoke')] if i%5 else [])
            state = tracker.update(i/10, detections, (640, 480))
            original = smoke.update(i/10, [d for d in detections if d['label']=='smoke'], (640, 480))
            actual = [{**t, 'event_id': t['event_id']//2} for t in state['tracks'] if t['label']=='smoke']
            self.assertEqual(actual, original['tracks'])
            self.assertEqual(len({t['event_id'] for t in state['tracks']}), len(state['tracks']))

    def test_confirmed_events_finish_with_actual_box_and_cooldown(self):
        tracker = StableRegionTracker()
        for i in range(30):
            tracker.update(i/10, [detection()] if i<=10 or i>=18 else [], (640, 480))
        tracker.finish(3.)
        self.assertEqual(tracker.summary()['confirmed_events'], 2)
        self.assertEqual(tracker.summary()['alerts_emitted'], 1)
        self.assertEqual([e['alert_emitted'] for e in tracker.closed_events], [True, False])
        self.assertEqual(tracker.closed_events[0]['last_bbox_normalized'], [100/640, 100/480, 300/640, 300/480])
        with self.assertRaises(ValueError):
            tracker.update(3.1, [], (640, 480))

    def test_invalid_parameters_and_time_are_rejected(self):
        for kwargs in ({'anchor_padding': float('nan')}, {'max_extent_factor': .5}, {'min_containment': 0}):
            with self.assertRaises(ValueError):
                RegionConfig(**kwargs)
        tracker = StableRegionTracker()
        tracker.update(0., [], (640, 480))
        for timestamp in (0., -1., float('nan')):
            with self.assertRaises(ValueError):
                tracker.update(timestamp, [], (640, 480))


if __name__ == '__main__':
    unittest.main()
