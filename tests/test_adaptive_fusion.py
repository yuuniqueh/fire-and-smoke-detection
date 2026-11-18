"""Synthetic software checks only; these do not measure fire-detection accuracy."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from core.adaptive_fusion import (FEATURE_NAMES, FEATURE_VERSION, INTERACTION_FEATURES,
                                  FusionModel, build_features, fit_model)


def baseline(**changes):
    value = {'event_id': 1, 'label': 'fire', 'score': 48., 'level': 'MEDIUM', 'fresh_evidence': True,
             'features': {'coverage_ratio_smoothed': .02, 'positive_duration_s': 5.,
                          'growth_pp_per_s': 0., 'confidence': .9, 'fresh_evidence': True}}
    value.update(changes)
    return value


def record(**changes):
    scene = {'description': '桌面上可见蜡烛，火焰位于烛芯上方。', 'scene': 'candle',
             'relation': 'expected_burning', 'evidence_quality': 'adequate', 'spread': 'not_observed',
             'visible_evidence': ['火焰位于烛芯上方'], 'hazard_cues': [], 'uncertainties': ['画面外未知'],
             'next_check': '核对周边物品。'}
    scene.update(changes)
    return {'event_id': None, 'scene': scene, 'frame_count': 3, 'span_s': 1.,
            'evidence': [{'timestamp_s': t} for t in (0., .5, 1.)]}


def values(**changes):
    raw = baseline()
    raw['features'].update(changes)
    return build_features(raw, record(), timestamp_s=1.)['values']


def rows():
    negative = values()
    positive = build_features(baseline(), record(relation='suspected_abnormal',
                             hazard_cues=['周边物体出现火焰'], spread='observed'), timestamp_s=1.)['values']
    return [{'features': negative, 'label': 0, 'source_group': 'synthetic-group-a'},
            {'features': positive, 'label': 1, 'source_group': 'synthetic-group-b'}]


def model(**coefficients):
    artifact = fit_model(rows(), iterations=1).to_dict()
    artifact['coefficients'] = {name: 0. for name in FEATURE_NAMES}
    artifact['coefficients'].update(coefficients)
    artifact['intercept'] = 0.
    return FusionModel.from_dict(artifact)


class FeatureTests(unittest.TestCase):
    def test_explicit_completion_time_cannot_look_ahead(self):
        future = record()
        future['available_at_s'] = 3.
        self.assertFalse(build_features(baseline(), future, timestamp_s=2.)['eligible'])
        self.assertTrue(build_features(baseline(), future, timestamp_s=3.)['eligible'])
        future['available_at_s'] = float('nan')
        self.assertFalse(build_features(baseline(), future, timestamp_s=3.)['eligible'])

    def test_large_artifact_is_rejected_before_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'oversized.json'
            path.write_bytes(b' ' * (1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                FusionModel.load(path)

    def test_actual_inputs_and_binary_scene_opinions_are_separate(self):
        built = build_features(baseline(), record(), timestamp_s=1.)
        self.assertTrue(built['eligible'])
        self.assertEqual(set(built['values']), set(FEATURE_NAMES))
        self.assertAlmostEqual(built['values']['detector_coverage'], .1)
        self.assertEqual(built['values']['scene_expected'], 1.)
        self.assertEqual(built['values']['scene_candle'], 1.)
        self.assertEqual(built['gate'], 1.)
        self.assertEqual(built['blockers'], [])

    def test_hazard_word_count_and_adequate_do_not_become_confidence(self):
        one = build_features(baseline(), record(hazard_cues=['火焰接触纸张']), timestamp_s=1.)
        two = build_features(baseline(), record(hazard_cues=['火焰接触纸张', '大量烟雾']), timestamp_s=1.)
        self.assertEqual(one['values'], two['values'])
        limited = build_features(baseline(), record(evidence_quality='limited'), timestamp_s=1.)
        self.assertEqual(one['gate'], limited['gate'])
        self.assertEqual(limited['values']['scene_limited'], 1.)

    def test_gate_decays_with_video_age_and_no_scene_is_unavailable(self):
        opening = build_features(baseline(), record(), timestamp_s=1.)
        later = build_features(baseline(), record(), timestamp_s=7.)
        self.assertGreater(opening['gate'], later['gate'])
        self.assertGreater(later['gate'], 0.)
        self.assertFalse(build_features(baseline(), None, timestamp_s=1.)['eligible'])

    def test_future_expired_mismatch_and_incomplete_timeline_fail_safe(self):
        invalid_records = []
        wrong = record()
        wrong['event_id'] = 2
        invalid_records.append(wrong)
        wrong = record()
        wrong['frame_count'] = 2
        invalid_records.append(wrong)
        wrong = record()
        wrong['span_s'] = 2.
        invalid_records.append(wrong)
        wrong = record()
        wrong['evidence'][1]['timestamp_s'] = 0.
        invalid_records.append(wrong)
        wrong = record()
        wrong['scene']['made_up_probability'] = .99
        invalid_records.append(wrong)
        wrong = record()
        wrong['scene'].pop('relation')
        invalid_records.append(wrong)
        wrong = record()
        wrong['frame_count'] = 1
        wrong['span_s'] = 0.
        wrong['evidence'] = [{'timestamp_s': 1.}]
        invalid_records.append(wrong)
        for candidate in invalid_records:
            with self.subTest(record=candidate):
                self.assertFalse(build_features(baseline(), candidate, timestamp_s=1.)['eligible'])
        for timestamp in (.5, 13.001, float('nan'), True):
            self.assertFalse(build_features(baseline(), record(), timestamp_s=timestamp)['eligible'])

    def test_invalid_detector_features_fail_safe(self):
        for changes in ({'coverage_ratio_smoothed': float('nan')}, {'confidence': True},
                        {'confidence': 2.}, {'positive_duration_s': -1.}, {'event_id': 2},
                        {'fresh_evidence': False}, {'growth_pp_per_s': float('inf')}):
            raw = baseline()
            raw['features'].update(changes)
            self.assertFalse(build_features(raw, record(), timestamp_s=1.)['eligible'])
        self.assertFalse(build_features(baseline(fresh_evidence=False), record(), timestamp_s=1.)['eligible'])


class PredictionTests(unittest.TestCase):
    def test_continuous_residual_changes_with_detector_features(self):
        predictor = model(scene_expected=-2., detector_coverage=4.)
        first = predictor.predict(baseline(), record(), timestamp_s=1.)
        raw = baseline()
        raw['features']['coverage_ratio_smoothed'] = .1
        second = predictor.predict(raw, record(), timestamp_s=1.)
        self.assertEqual(first['status'], 'ready')
        self.assertNotEqual(first['context_delta'], second['context_delta'])
        self.assertLess(first['context_delta'], 0.)
        self.assertLess(second['context_delta'], 0.)
        self.assertEqual(first['calibrated'], False)
        self.assertEqual(first['feature_version'], FEATURE_VERSION)
        self.assertEqual(set(first['contributions'][0]), {'feature', 'value', 'coefficient', 'contribution'})

    def test_interactions_can_express_context_dependent_growth(self):
        predictor = model(controlled_x_growth=4.)
        stable = predictor.predict(baseline(), record(), timestamp_s=1.)
        raw = baseline()
        raw['features']['growth_pp_per_s'] = .3
        growing = predictor.predict(raw, record(), timestamp_s=1.)
        unknown = predictor.predict(raw, record(relation='unclear'), timestamp_s=1.)
        self.assertEqual(stable['context_delta'], 0.)
        self.assertGreater(growing['context_delta'], 0.)
        self.assertEqual(unknown['context_delta'], 0.)

    def test_negative_correction_guards_and_original_event_are_retained(self):
        predictor = model(scene_candle=-10.)
        raw = baseline()
        original = deepcopy(raw)
        self.assertLess(predictor.predict(raw, record(), timestamp_s=1.)['context_delta'], 0.)
        cases = [(baseline(label='smoke'), record(), 1), (raw, record(), 2),
                 (raw, record(evidence_quality='limited'), 1), (raw, record(spread='observed'), 1),
                 (raw, record(hazard_cues=['周边物体出现火焰']), 1),
                 (raw, record(relation='unclear'), 1), (raw, record(visible_evidence=[]), 1)]
        for growth in (None, .51):
            changed = baseline()
            changed['features']['growth_pp_per_s'] = growth
            cases.append((changed, record(), 1))
        for event, scene, count in cases:
            prediction = predictor.predict(event, scene, timestamp_s=1., active_event_count=count)
            self.assertEqual(prediction['score'], event['score'])
            self.assertEqual(prediction['context_delta'], 0.)
            self.assertTrue(prediction['blockers'])
        self.assertEqual(raw, original)

    def test_caps_and_no_scene_fallback(self):
        predictor = model(scene_hazard=100.)
        high = predictor.predict(baseline(score=99.), record(hazard_cues=['大量烟雾']), timestamp_s=1.)
        self.assertEqual(high['score'], 100.)
        self.assertEqual(high['context_delta'], 1.)
        missing = predictor.predict(baseline(), None, timestamp_s=1.)
        self.assertEqual((missing['status'], missing['score'], missing['context_delta']), ('skipped', 48., 0.))
        self.assertIsNone(predictor.predict(baseline(score=float('nan')), record(), timestamp_s=1.)['score'])
        json.dumps(missing, allow_nan=False)


class ArtifactAndTrainingTests(unittest.TestCase):
    def test_artifact_roundtrip_and_detached_metadata(self):
        trained = fit_model(rows(), iterations=5)
        artifact = trained.to_dict()
        self.assertEqual(artifact['training']['source_groups'], ['synthetic-group-a', 'synthetic-group-b'])
        self.assertEqual(artifact['status'], 'development')
        self.assertFalse(artifact['residual']['validated'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'candidate.json'
            path.write_text(json.dumps(artifact, allow_nan=False), encoding='utf-8')
            self.assertEqual(FusionModel.load(path).to_dict(), artifact)
            path.write_text('{"model_id":"duplicate",' + json.dumps(artifact)[1:], encoding='utf-8')
            with self.assertRaises(ValueError):
                FusionModel.load(path)
        artifact['training']['source_groups'].append('external-mutation')
        self.assertEqual(len(trained.training['source_groups']), 2)

    def test_incompatible_or_forged_artifacts_are_rejected(self):
        original = model().to_dict()
        bad_artifacts = []
        for key, value in [('feature_version', 'future-version'), ('calibrated', True),
                           ('status', 'production'), ('intercept', float('nan')),
                           ('artifact_version', True)]:
            wrong = deepcopy(original)
            wrong[key] = value
            bad_artifacts.append(wrong)
        wrong = deepcopy(original)
        wrong['coefficients']['invented'] = 1.
        bad_artifacts.append(wrong)
        wrong = deepcopy(original)
        wrong['coefficients']['scene_expected'] = float('inf')
        bad_artifacts.append(wrong)
        wrong = deepcopy(original)
        wrong['residual']['down_cap'] = 100.
        bad_artifacts.append(wrong)
        wrong = deepcopy(original)
        wrong['training']['class_counts']['0'] = 0
        bad_artifacts.append(wrong)
        for candidate in bad_artifacts:
            with self.assertRaises(ValueError):
                FusionModel.from_dict(candidate)

    def test_synthetic_fit_learns_software_example_only(self):
        trained = fit_model(rows(), l2=.1, iterations=400)
        safe = trained.predict(baseline(), record(), timestamp_s=1.)
        abnormal = trained.predict(baseline(), record(relation='suspected_abnormal',
                                  hazard_cues=['周边物体出现火焰'], spread='observed'), timestamp_s=1.)
        self.assertGreater(abnormal['model_output'], safe['model_output'])
        self.assertEqual(trained.training['validation_status'], 'not_validated')
        without = fit_model(rows(), iterations=5, include_interactions=False)
        self.assertTrue(all(without.to_dict()['coefficients'][name] == 0. for name in INTERACTION_FEATURES))

    def test_group_balancing_prevents_duplicate_group_dominance(self):
        original_rows = rows()
        first = fit_model(original_rows, iterations=50, l2=0.)
        duplicated = fit_model([original_rows[0]] * 10 + original_rows[1:], iterations=50, l2=0.)
        for name in FEATURE_NAMES:
            self.assertAlmostEqual(first.to_dict()['coefficients'][name], duplicated.to_dict()['coefficients'][name])

    def test_training_refuses_missing_invalid_or_unversioned_features(self):
        bad_samples = [[], rows()[:1]]
        wrong = rows()
        wrong[1]['source_group'] = wrong[0]['source_group']
        bad_samples.append(wrong)
        wrong = rows()
        wrong[0]['features'].pop('detector_score')
        bad_samples.append(wrong)
        wrong = rows()
        wrong[0]['features']['detector_confidence'] = float('nan')
        bad_samples.append(wrong)
        wrong = rows()
        wrong[0]['label'] = True
        bad_samples.append(wrong)
        wrong = rows()
        wrong[0]['features']['made_up'] = 1.
        bad_samples.append(wrong)
        for samples in bad_samples:
            with self.assertRaises(ValueError):
                fit_model(samples, iterations=1)


if __name__ == '__main__':
    unittest.main()
