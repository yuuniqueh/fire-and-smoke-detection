"""The development model cannot alter production grades or notification changes."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.adaptive_fusion import build_features, fit_model
from core.fusion_dataset import scene_record_key
from core.scene_fusion import SceneContextStore, SceneFusionEngine
from tests.test_scene_fusion import assessment, observation


class FusionShadowTests(unittest.TestCase):
    def event(self, timestamp=2.):
        return assessment(timestamp, features={'coverage_ratio_smoothed': .04, 'positive_duration_s': 2.,
                                               'confidence': .9, 'growth_pp_per_s': 0.})

    def test_shadow_prediction_keeps_production_score_changes_and_raw_evidence(self):
        store = SceneContextStore()
        store.publish(observation())
        raw = self.event()
        before = deepcopy(raw)
        plain = SceneFusionEngine(store).update(raw)
        built = build_features(raw['events'][0], observation(), timestamp_s=2.)
        model = fit_model([{'features': built['values'], 'label': label, 'source_group': f'fixture-{label}'}
                           for label in (0, 1)], iterations=5)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'software-fixture-model.json'
            path.write_text(json.dumps(model.to_dict()), encoding='utf-8')
            shadow = SceneFusionEngine(store, shadow_model_path=path).update(raw)
        self.assertEqual(raw, before)
        self.assertEqual(shadow['score'], plain['score'])
        self.assertEqual(shadow['events'], plain['events'])
        self.assertEqual(shadow['level_changes'], plain['level_changes'])
        self.assertFalse(shadow['adaptive_shadow']['affects_notifications'])
        self.assertEqual(shadow['adaptive_shadow']['status'], 'disabled_by_current_policy')
        self.assertNotIn('events', shadow['adaptive_shadow'])

    def test_failed_artifact_does_not_stop_production_fusion(self):
        store = SceneContextStore()
        store.publish(observation())
        actual = SceneFusionEngine(store, shadow_model_path='missing-development-model.json').update(self.event())
        self.assertLess(actual['score'], 48.)
        self.assertEqual(actual['adaptive_shadow']['status'], 'disabled_by_current_policy')

    def test_consumption_time_is_first_actual_use_and_new_scene_is_separate(self):
        store = SceneContextStore()
        record = observation()
        store.publish(record)
        engine = SceneFusionEngine(store)
        first = engine.update(self.event(3.))['fusion']
        self.assertEqual(first['scene_record_key'], scene_record_key(record))
        self.assertEqual(first['scene_available_at_s'], 3.)
        self.assertEqual(engine.update(self.event(4.))['fusion']['scene_available_at_s'], 3.)
        store.publish(observation(5.))
        self.assertEqual(engine.update(self.event(6.))['fusion']['scene_available_at_s'], 6.)

    def test_future_and_expired_scene_have_no_causal_available_time(self):
        store = SceneContextStore()
        store.publish(observation(4.))
        engine = SceneFusionEngine(store)
        self.assertIsNone(engine.update(self.event(2.))['fusion']['scene_available_at_s'])
        self.assertIsNone(engine.update(self.event(17.))['fusion']['scene_available_at_s'])

    def test_shadow_receives_detector_score_before_fixed_fusion(self):
        store = SceneContextStore()
        store.publish(observation())
        engine = SceneFusionEngine(store)
        with patch.object(engine.shadow, 'update', return_value={'mode': 'shadow'}) as predict:
            actual = engine.update(self.event())
        self.assertLess(actual['score'], 48.)
        predict.assert_not_called()  # Retired trainable fusion never participates.


if __name__ == '__main__':
    unittest.main()
