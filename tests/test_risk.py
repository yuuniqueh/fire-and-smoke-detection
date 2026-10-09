import math
import unittest

from core.features import FeatureExtractor, union_coverage
from core.risk import LevelController, RiskConfig, RiskEngine, score_features
from core.temporal import EventTracker


def detection(box=(10, 10, 50, 50), label='fire', confidence=0.9):
    return {'label': label, 'confidence': confidence, 'bbox_xyxy': list(box)}


def feature_track(present=True, timestamp=0.0):
    return {'event_id': 1, 'label': 'fire', 'state': 'confirmed', 'present_this_frame': present,
            'bbox_normalized': [0, 0, 1, 1], 'confidence': 0.9, 'presence_ratio': 1.0,
            'last_seen_s': timestamp if present else 0.9}


def score_input(**kwargs):
    value = {'label': 'fire', 'fresh_evidence': True, 'coverage_ratio_smoothed': 0.01,
             'positive_duration_s': 1.0, 'growth_pp_per_s': 0.0, 'trend': 'stable', 'confidence': 0.9}
    value.update(kwargs)
    return value


class CoverageTests(unittest.TestCase):
    def test_duplicate_rectangles_not_double_counted(self):
        box = [0, 0, 0.5, 0.5]
        self.assertAlmostEqual(union_coverage([box, box]), 0.25)

    def test_partial_overlap_union_and_disjoint(self):
        self.assertAlmostEqual(union_coverage([[0, 0, .5, .5], [.25, 0, .75, .5]]), .375)
        self.assertAlmostEqual(union_coverage([[0, 0, .1, .1], [.9, .9, 1, 1]]), .02)

    def test_clipping_degenerate_and_empty(self):
        self.assertEqual(union_coverage([]), 0)
        self.assertAlmostEqual(union_coverage([[-1, -1, .5, .5]]), .25)
        self.assertEqual(union_coverage([[.5, .5, .1, .1]]), 0)

    def test_nonfinite_boxes_are_rejected(self):
        with self.assertRaises(ValueError):
            union_coverage([[0, 0, float('nan'), 1]])


class FeatureTests(unittest.TestCase):
    def test_time_constant_is_frame_rate_independent(self):
        values = []
        for fps in (10, 30, 60):
            extractor = FeatureExtractor()
            for i in range(fps + 1):
                t = i/fps
                box = (0, 0, 10, 10) if i == 0 else (0, 0, 20, 20)
                output = extractor.update(t, [detection(box)], (100, 100), [feature_track(timestamp=t)])
            values.append(output[1]['coverage_ratio_smoothed'])
        expected = .04 + (.01 - .04) * math.exp(-1/.75)
        for value in values:
            self.assertAlmostEqual(value, expected, places=7)

    def test_static_box_is_stable_after_trend_window(self):
        extractor = FeatureExtractor()
        for i in range(21):
            t = i/10
            output = extractor.update(t, [detection()], (100, 100), [feature_track(timestamp=t)])
        self.assertEqual(output[1]['growth_pp_per_s'], 0)
        self.assertEqual(output[1]['trend'], 'stable')

    def test_dropout_is_not_zero_area_or_shrinkage_and_restarts_trend(self):
        extractor = FeatureExtractor()
        for i in range(10):
            t = i/10
            extractor.update(t, [detection()], (100, 100), [feature_track(timestamp=t)])
        first = extractor.update(1.0, [], (100, 100), [feature_track(False)])
        second = extractor.update(1.1, [], (100, 100), [feature_track(False)])
        self.assertIsNone(first[1]['coverage_ratio_raw'])
        self.assertIsNone(first[1]['growth_pp_per_s'])
        self.assertEqual(first[1]['trend'], 'temporarily_missing')
        self.assertEqual(first[1]['positive_duration_s'], second[1]['positive_duration_s'])
        resumed = extractor.update(1.2, [detection()], (100, 100), [feature_track(timestamp=1.2)])
        self.assertEqual(resumed[1]['trend'], 'warming_up')

    def test_boxes_in_other_regions_are_not_added(self):
        extractor = FeatureExtractor()
        track = feature_track()
        track['bbox_normalized'] = [0, 0, .5, .5]
        output = extractor.update(0, [detection((0,0,10,10)), detection((70,70,90,90))], (100,100), [track])
        self.assertAlmostEqual(output[1]['coverage_ratio_raw'], .01)

    def test_growth_uses_percentage_points_per_second(self):
        extractor = FeatureExtractor(smoothing_tau_s=.0001)
        for i in range(21):
            t = i/10
            output = extractor.update(t, [detection((0,0,10+20*t,10))], (100,100), [feature_track(timestamp=t)])
        self.assertAlmostEqual(output[1]['growth_pp_per_s'], 2.0, places=5)
        self.assertEqual(output[1]['trend'], 'growing')

    def test_shrinking_coverage_has_negative_trend(self):
        extractor = FeatureExtractor(smoothing_tau_s=.0001)
        for i in range(21):
            t = i/10
            output = extractor.update(t, [detection((0,0,80-20*t,10))], (100,100), [feature_track(timestamp=t)])
        self.assertAlmostEqual(output[1]['growth_pp_per_s'], -2.0, places=5)
        self.assertEqual(output[1]['trend'], 'shrinking')


class ScoreTests(unittest.TestCase):
    def test_saturation_is_bounded_and_components_explain_total(self):
        value, terms, reasons = score_features(score_input(coverage_ratio_smoothed=1, positive_duration_s=100,
                                                         growth_pp_per_s=100, confidence=1), RiskConfig())
        self.assertEqual(value, 100)
        self.assertAlmostEqual(sum(terms.values()), value, places=3)
        self.assertTrue(reasons)

    def test_shrinkage_and_unknown_trend_add_no_growth_score(self):
        for growth in (-10, None, .2):
            _, components, _ = score_features(score_input(growth_pp_per_s=growth), RiskConfig())
            self.assertEqual(components['positive_growth'], 0)

    def test_pending_or_stale_evidence_cannot_be_scored_as_fresh(self):
        with self.assertRaises(ValueError):
            score_features(score_input(fresh_evidence=False), RiskConfig())

    def test_nonfinite_features_and_invalid_parameters_are_rejected(self):
        with self.assertRaises(ValueError):
            score_features(score_input(confidence=float('nan')), RiskConfig())
        for kwargs in ({'coverage_full_scale':0}, {'medium_exit':50}, {'high_exit':40},
                       {'confidence_floor':1}, {'coverage_weight':100}, {'trend_window_s':float('nan')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                RiskConfig(**kwargs)


class HysteresisTests(unittest.TestCase):
    def test_short_high_score_spike_does_not_upgrade(self):
        controller = LevelController(RiskConfig())
        controller.update(0, 20)
        controller.update(.1, 80)
        controller.update(.2, 20)
        self.assertEqual(controller.level, 'LOW')
        controller.update(.3, 80)
        controller.update(.8, 80)
        self.assertEqual(controller.level, 'HIGH')

    def test_medium_deadband_and_downgrade_wait(self):
        controller = LevelController(RiskConfig())
        controller.update(0, 46)
        controller.update(.5, 46)
        controller.update(.6, 40)
        self.assertEqual(controller.level, 'MEDIUM')
        controller.update(.7, 37)
        controller.update(.9, 40)
        controller.update(1.0, 37)
        controller.update(2.0, 37)
        self.assertEqual(controller.level, 'LOW')

    def test_high_deadband(self):
        controller = LevelController(RiskConfig())
        controller.update(0, 80)
        controller.update(.5, 80)
        controller.update(.6, 65)
        self.assertEqual(controller.level, 'HIGH')
        controller.update(.7, 60)
        controller.update(1.7, 60)
        self.assertEqual(controller.level, 'MEDIUM')

    def test_stale_evidence_interrupts_upgrade_wait(self):
        controller = LevelController(RiskConfig())
        controller.update(0, 80)
        controller.update(.4, 80, fresh=False)
        controller.update(.5, 80)
        controller.update(.9, 80)
        self.assertEqual(controller.level, 'LOW')
        controller.update(1.0, 80)
        self.assertEqual(controller.level, 'HIGH')


class RiskEngineTests(unittest.TestCase):
    def feed(self, tracker, engine, rows):
        outputs=[]
        for t, detections in rows:
            snapshot=tracker.update(t,detections,(256,256))
            outputs.append(engine.update(snapshot,detections,(256,256)))
        return outputs

    def test_unconfirmed_candidates_have_no_assessed_risk(self):
        tracker,engine=EventTracker(),RiskEngine()
        output=self.feed(tracker,engine,[(0,[detection((0,0,256,256))]),(.1,[])])[-1]
        self.assertEqual(output['status'],'awaiting_confirmation')
        self.assertEqual(output['events'],[])
        self.assertEqual(output['score'],0)

    def test_no_detection_has_zero_index_without_safety_claim(self):
        output=RiskEngine().update({'timestamp_s':0,'tracks':[]},[],(256,256))
        self.assertEqual(output['level'],'NORMAL')
        self.assertEqual(output['score'],0)
        self.assertIn('not a calibrated',output['index_note'])

    def test_confirmed_dropout_holds_score_until_event_closes(self):
        tracker=EventTracker()
        engine=RiskEngine(RiskConfig(duration_full_scale_s=1))
        output=self.feed(tracker,engine,[(i/10,[detection((0,0,256,256))]) for i in range(16)])[-1]
        self.assertEqual(output['level'],'HIGH')
        old_score=output['score']
        gap=self.feed(tracker,engine,[(1.6,[]),(1.7,[])])[-1]
        self.assertEqual(gap['score'],old_score)
        self.assertEqual(gap['level'],'HIGH')
        self.assertFalse(gap['fresh_evidence'])
        end=self.feed(tracker,engine,[(2.2,[])])[-1]
        self.assertEqual(end['events'],[])
        self.assertEqual(engine.event_summaries[0]['close_reason'],'detection_timeout')

    def test_independent_event_scores_are_not_summed(self):
        tracker=EventTracker()
        engine=RiskEngine(RiskConfig(duration_full_scale_s=1,upgrade_hold_s=0))
        boxes=[detection((10,10,50,50)),detection((200,200,240,240))]
        output=self.feed(tracker,engine,[(i/10,boxes) for i in range(13)])[-1]
        self.assertEqual(len(output['events']),2)
        self.assertEqual(output['score'],max(e['score'] for e in output['events']))

    def test_finish_archives_evidence_and_new_engine_resets(self):
        tracker,engine=EventTracker(),RiskEngine()
        self.feed(tracker,engine,[(i/10,[detection()]) for i in range(16)])
        engine.finish(1.6,'stream_end')
        self.assertEqual(len(engine.event_summaries),1)
        self.assertEqual(engine.event_summaries[0]['close_reason'],'stream_end')
        self.assertTrue(engine.event_summaries[0]['peak_evidence'])
        self.assertTrue(engine.event_summaries[0]['level_changes'][0]['reasons'])
        self.assertEqual(RiskEngine().summary()['peak_level'],'NORMAL')


if __name__=='__main__':
    unittest.main()
