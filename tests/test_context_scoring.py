"""Dynamic-rule behavior checks; no claims about fire-classification accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from core.context_scoring import contextual_score, scene_only_score
from core.notifications import NotificationEngine, NotificationConfig
from core.records import append_feedback, event_review_state, render_review_report
from core.risk import RiskConfig
from core.scene_fusion import SceneContextStore, SceneFusionEngine
from tests.test_scene_fusion import assessment, observation


class DynamicScoreTests(unittest.TestCase):
    def base(self, **features):
        row = assessment()['events'][0]
        row['features'].update(features)
        return row

    def score(self, base=None, scene=None, **options):
        return contextual_score(base or self.base(), scene or observation(hazard_cues=['邻近物品出现火焰'])['scene'],
                                frame_count=3, span_s=1., **options)

    def test_adjustment_changes_with_growth_coverage_duration_and_age(self):
        steady = self.score()
        growing = self.score(self.base(growth_pp_per_s=2.))
        larger = self.score(self.base(coverage_ratio_smoothed=.15))
        longer = self.score(self.base(positive_duration_s=25.))
        older = self.score(age_s=8.)
        self.assertGreater(growing['context_delta'], steady['context_delta'])
        self.assertGreater(larger['context_delta'], steady['context_delta'])
        self.assertGreater(longer['context_delta'], steady['context_delta'])
        self.assertLess(older['context_delta'], steady['context_delta'])
        self.assertNotEqual(steady['context_delta'], 10.)

    def test_repeating_hazard_words_does_not_increase_score(self):
        one = observation(hazard_cues=['邻近物品出现火焰'])['scene']
        repeated = observation(hazard_cues=['邻近物品出现火焰'] * 8)['scene']
        self.assertEqual(self.score(scene=one)['score'], self.score(scene=repeated)['score'])

    def test_missing_future_and_expired_scene_do_not_adjust_even_with_longer_requested_ttl(self):
        for age in (-1., 12., 20.):
            self.assertEqual(self.score(age_s=age, max_age_s=60.)['context_delta'], 0.)

    def test_controlled_scene_never_discounts_smoke_growth_missing_or_multiple_events(self):
        scene = observation()['scene']
        for base, count in ((self.base(growth_pp_per_s=None), 1), (self.base(growth_pp_per_s=.6), 1),
                            ({**self.base(), 'label': 'smoke'}, 1), (self.base(), 2)):
            self.assertEqual(self.score(base, scene, active_event_count=count)['context_delta'], 0.)
        normal = self.score(self.base(), scene)
        self.assertLess(normal['context_delta'], 0.)
        self.assertGreaterEqual(normal['context_delta'], -12.)

    def test_near_expiry_small_change_respects_raw_exit_hysteresis(self):
        base = {**self.base(), 'score': 65., 'level': 'HIGH'}
        result = self.score(base, observation()['scene'], age_s=11.99)
        self.assertEqual(result['level'], 'HIGH')

    def test_final_grade_waits_for_sustained_corrected_evidence(self):
        store = SceneContextStore()
        store.publish(observation())
        engine = SceneFusionEngine(store)
        first = engine.update(assessment(2., score=40., level='MEDIUM'))
        self.assertLess(first['score'], 38.)
        self.assertEqual(first['level'], 'MEDIUM')
        later = engine.update(assessment(3.1, score=40., level='MEDIUM'))
        self.assertEqual(later['level'], 'LOW')

    def test_custom_risk_scale_is_used_and_invalid_scale_rejected(self):
        standard = self.score()
        scaled = self.score(risk_config=RiskConfig(coverage_full_scale=.4))
        self.assertLess(scaled['context_delta'], standard['context_delta'])
        class Invalid:
            coverage_full_scale = 0.
        with self.assertRaises(ValueError):
            self.score(risk_config=Invalid())

    def test_extremes_are_bounded_and_actual_delta_matches_score(self):
        for raw in (0., 1., 40., 65., 99., 100.):
            for change in ({'hazard_cues': ['异常线索'], 'spread': 'observed'}, {}):
                result = self.score({**self.base(growth_pp_per_s=0.), 'score': raw}, observation(**change)['scene'])
                self.assertTrue(0 <= result['score'] <= 100)
                self.assertTrue(-12 <= result['context_delta'] <= 25)
                self.assertAlmostEqual(result['context_delta'], result['score'] - raw, places=2)

    def test_single_frame_cannot_claim_multiframe_in_store(self):
        store = SceneContextStore()
        malformed = observation()
        malformed['evidence'] = malformed['evidence'][:1]
        with self.assertRaises(ValueError):
            store.publish(malformed)

    def test_ai_only_has_one_final_score_without_a_fabricated_detection(self):
        store = SceneContextStore()
        store.publish(observation(2., hazard_cues=['邻近物品出现火焰']))
        engine = SceneFusionEngine(store)
        raw = assessment(2.)
        raw.update(events=[], score=0., level='NORMAL', dominant_event_id=None, fresh_evidence=False)
        before = deepcopy(raw)
        first = engine.update(raw)
        self.assertEqual(raw, before)
        self.assertEqual(first['events'], [])
        self.assertTrue(0 < first['score'] <= 60.)
        self.assertTrue(first['fusion']['scene_only_hazard'])
        raw['timestamp_s'] = 2.6
        final = engine.update(raw)
        self.assertEqual(final['level'], 'MEDIUM')
        self.assertEqual(engine.summary()['assessed_events'], 0)

    def test_zero_scene_only_result_does_not_claim_an_anomaly(self):
        value = scene_only_score(observation()['scene'], frame_count=3, span_s=1.)
        self.assertFalse(value['active'])
        self.assertNotIn('AI记录了异常', value['reason'])


class SceneNoticeTests(unittest.TestCase):
    def packet(self, stamp, active=True, level='MEDIUM'):
        row = assessment(stamp)
        row.update(events=[], score=48. if active else 0., level=level if active else 'NORMAL',
                   fresh_evidence=active, dominant_event_id=None)
        row['fusion'] = {'scene_only_hazard': active, 'scene_record_key': 'scene-key',
                         'description': '周边物体可见异常线索。', 'evidence_reference_s': 1.}
        return row

    def test_scene_notice_deduplicates_and_never_invents_yolo_confirmation(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True))
        first = engine.update(self.packet(2.), {'timestamp_s': 2.})
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]['kind'], 'scene_attention')
        self.assertIsNone(first[0]['event_id'])
        self.assertEqual(first[0]['score'], 48.)
        self.assertIn('sound', first[0]['channels'])
        self.assertEqual(engine.update(self.packet(3.), {'timestamp_s': 3.}), [])
        closed = engine.update(self.packet(4., False), {'timestamp_s': 4.})
        self.assertEqual(closed[0]['kind'], 'event_closed')
        self.assertEqual(engine.summary()['active_events'], 0)

    def test_scene_notice_is_reviewable_persisted_and_exported_without_fake_event(self):
        engine = NotificationEngine()
        notice = engine.update(self.packet(2.), {'timestamp_s': 2.})[0]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            for name, value in {'summary.json': {}, 'config.json': {}, 'events.json': {'events': []}}.items():
                (path / name).write_text(json.dumps(value), encoding='utf-8')
            (path / 'alerts.jsonl').write_text(json.dumps(notice) + '\n', encoding='utf-8')
            feedback = append_feedback(path, notice, 'confirmed_abnormal', '需要继续处理')
            self.assertEqual(event_review_state(None, [notice], {notice['notification_id']: feedback}), 'confirmed_abnormal')
            report = render_review_report(path)
            self.assertIn('综合异常提示核查', report)
            self.assertNotIn('#None', report)


if __name__ == '__main__':
    unittest.main()
