"""Independent evidence paths never alter or veto confirmed detector results."""
from copy import deepcopy
import json
import math
import unittest

from core.fusion_dataset import scene_record_key
from core.independent_review import independent_review


def assessment(*, confirmed=True, timestamp=3.):
    return {'timestamp_s': timestamp, 'score': 72.5 if confirmed else 0.,
            'level': 'HIGH' if confirmed else 'NORMAL', 'status': 'assessed',
            'events': [{'event_id': 7, 'score': 72.5, 'level': 'HIGH',
                        'features': {'confidence': .91}}] if confirmed else []}


def scene_record(**changes):
    scene = {'description': '桌面可见蜡烛，火焰位于烛芯上方。', 'scene': 'candle',
             'relation': 'expected_burning', 'evidence_quality': 'adequate',
             'spread': 'not_observed', 'visible_evidence': ['火焰局限于烛芯上方'],
             'hazard_cues': [], 'uncertainties': ['画面外情况未知'], 'next_check': '继续观察周边物品。'}
    scene.update(changes)
    return {'scene': scene, 'frame_count': 3, 'span_s': 1., 'event_id': None,
            'model': 'vision-model', 'created_at': '2026-11-17T20:00:00+08:00', 'review_profile': 'fast',
            'evidence': [{'timestamp_s': stamp, 'sha256': str(index) * 64}
                         for index, stamp in enumerate((1., 1.5, 2.))]}


class IndependentReviewTests(unittest.TestCase):
    def test_controlled_scene_cannot_reduce_any_original_score_or_level(self):
        original = assessment()
        record = scene_record()
        before_assessment, before_record = deepcopy(original), deepcopy(record)
        result = independent_review(original, record)
        self.assertEqual(original, before_assessment)
        self.assertEqual(record, before_record)
        self.assertEqual(result['detector']['score'], original['score'])
        self.assertEqual(result['detector']['level'], original['level'])
        self.assertFalse(result['scores_combined'])
        self.assertEqual(result['decision']['status'], 'disagreement')
        self.assertTrue(result['decision']['attention'])
        self.assertEqual(result['scene']['record_key'], scene_record_key(record))
        json.dumps(result, allow_nan=False)

    def test_detector_keeps_attention_while_ai_pending_expired_or_invalid(self):
        for record, timestamp, expected in ((None, 3., 'pending'),
                (scene_record(), 15., 'expired'), ({'description': 'partial'}, 3., 'invalid')):
            with self.subTest(status=expected):
                result = independent_review(assessment(timestamp=timestamp), record)
                self.assertEqual(result['scene']['status'], expected)
                self.assertEqual(result['decision']['status'], 'detector_attention')
                self.assertEqual(result['decision']['sources'], ['detector'])
                self.assertEqual(result['detector']['score'], 72.5)

    def test_general_ai_anomaly_gets_attention_without_detector_confirmation(self):
        record = scene_record(relation='suspected_abnormal', hazard_cues=['火焰接触桌布'])
        result = independent_review(assessment(confirmed=False), record)
        self.assertEqual(result['scene']['status'], 'anomaly')
        self.assertEqual(result['decision']['status'], 'scene_attention')
        self.assertEqual(result['decision']['sources'], ['scene'])
        self.assertTrue(result['decision']['attention'])
        self.assertEqual(result['detector']['score'], 0.)

    def test_both_attention_paths_are_corroborated_but_not_fire_confirmation(self):
        result = independent_review(assessment(), scene_record(spread='observed'))
        self.assertEqual(result['decision']['status'], 'corroborated')
        self.assertEqual(result['decision']['sources'], ['detector', 'scene'])
        self.assertIn('不等于已确认火灾', result['decision']['reason'])

    def test_clear_requires_controlled_multiframe_scene_and_no_detector_event(self):
        result = independent_review(assessment(confirmed=False), scene_record())
        self.assertEqual(result['decision']['status'], 'clear')
        self.assertFalse(result['decision']['attention'])
        self.assertEqual(result['decision']['title'], '当前未确认烟火，AI提示受控用火')
        for record in (None, scene_record(relation='unclear'), scene_record(evidence_quality='limited')):
            result = independent_review(assessment(confirmed=False), record)
            self.assertEqual(result['decision']['status'], 'pending')

    def test_single_frame_or_short_span_cannot_claim_anomaly_or_control(self):
        for anomaly in (False, True):
            for count, span in ((1, 0.), (2, .5)):
                with self.subTest(anomaly=anomaly, count=count):
                    record = scene_record(hazard_cues=['可见异常线索'] if anomaly else [])
                    record['frame_count'], record['span_s'] = count, span
                    record['evidence'] = [{'timestamp_s': 2. - span + index * span, 'sha256': str(index) * 64}
                                          for index in range(count)]
                    result = independent_review(assessment(confirmed=False), record)
                    self.assertEqual(result['scene']['status'], 'uncertain')
                    self.assertFalse(result['decision']['attention'])

    def test_each_independent_anomaly_signal_and_visible_evidence_requirement(self):
        for changes in ({'hazard_cues': ['火焰接触周边物品']}, {'spread': 'observed'},
                        {'relation': 'suspected_abnormal'}):
            with self.subTest(changes=changes):
                result = independent_review(assessment(), scene_record(**changes))
                self.assertEqual(result['scene']['status'], 'anomaly')
        result = independent_review(assessment(), scene_record(relation='suspected_abnormal', visible_evidence=[]))
        self.assertEqual(result['scene']['status'], 'uncertain')

    def test_scene_name_alone_or_unknown_spread_cannot_claim_control(self):
        for changes in ({'visible_evidence': []}, {'spread': 'unknown'},
                        {'evidence_quality': 'limited'}, {'scene': 'outdoor_fire'}, {'relation': 'unclear'}):
            with self.subTest(changes=changes):
                result = independent_review(assessment(confirmed=False), scene_record(**changes))
                self.assertEqual(result['scene']['status'], 'uncertain')

    def test_event_review_must_match_current_confirmed_event(self):
        record = scene_record(hazard_cues=['火焰接触桌布'])
        for confirmed, event_id, expected in ((True, 7, 'corroborated'), (True, 8, 'detector_attention'),
                                               (False, 7, 'pending')):
            with self.subTest(confirmed=confirmed, event=event_id):
                record['event_id'] = event_id
                result = independent_review(assessment(confirmed=confirmed), record)
                self.assertEqual(result['decision']['status'], expected)
                if event_id == 8 or not confirmed:
                    self.assertEqual(result['scene']['status'], 'invalid')

    def test_future_frames_unsorted_stamps_bad_count_or_span_are_invalid(self):
        records = []
        future = scene_record()
        future['evidence'][-1]['timestamp_s'] = 4.
        future['span_s'] = 3.
        records.append(future)
        unsorted = scene_record()
        unsorted['evidence'].reverse()
        records.append(unsorted)
        duplicate = scene_record()
        duplicate['evidence'][1]['timestamp_s'] = 1.
        records.append(duplicate)
        wrong_count = scene_record()
        wrong_count['frame_count'] = 2
        records.append(wrong_count)
        wrong_span = scene_record()
        wrong_span['span_s'] = 2.
        records.append(wrong_span)
        nonfinite = scene_record()
        nonfinite['evidence'][0]['timestamp_s'] = math.nan
        records.append(nonfinite)
        for record in records:
            with self.subTest(record=record):
                result = independent_review(assessment(), record)
                self.assertEqual(result['scene']['status'], 'invalid')
                self.assertEqual(result['decision']['status'], 'detector_attention')

    def test_complete_scene_structure_is_required(self):
        for field in scene_record()['scene']:
            record = scene_record()
            del record['scene'][field]
            with self.subTest(field=field):
                self.assertEqual(independent_review(assessment(), record)['scene']['status'], 'invalid')

    def test_age_boundary_and_caller_limit_do_not_extend_freshness(self):
        record = scene_record()
        self.assertEqual(independent_review(assessment(timestamp=14.), record)['scene']['status'], 'controlled')
        self.assertEqual(independent_review(assessment(timestamp=14.0001), record)['scene']['status'], 'expired')
        self.assertEqual(independent_review(assessment(timestamp=15.), record, max_age_s=50.)['scene']['status'], 'expired')
        self.assertEqual(independent_review(assessment(), record, max_age_s=.5)['scene']['status'], 'expired')

    def test_explicit_timestamp_and_availability_are_checked(self):
        record = scene_record()
        self.assertEqual(independent_review(assessment(timestamp=20.), record, timestamp_s=3.)['scene']['status'], 'controlled')
        record['available_at_s'] = 4.
        self.assertEqual(independent_review(assessment(), record)['scene']['status'], 'invalid')
        record['available_at_s'] = 2.5
        self.assertEqual(independent_review(assessment(), record)['scene']['status'], 'controlled')
        for value in (math.nan, math.inf, -1., True):
            with self.subTest(value=value):
                result = independent_review(assessment(), record, timestamp_s=value)
                self.assertEqual(result['scene']['status'], 'invalid')
        with self.assertRaises(ValueError):
            independent_review(assessment(), record, max_age_s=math.nan)

    def test_outputs_are_detached_from_input_lists(self):
        record = scene_record(hazard_cues=['可见异常'])
        result = independent_review(assessment(), record)
        result['scene']['hazard_cues'].append('修改输出')
        self.assertEqual(record['scene']['hazard_cues'], ['可见异常'])


if __name__ == '__main__':
    unittest.main()
