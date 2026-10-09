import unittest
from core.evaluation import score_states
from experiments.screen_hf_fire_candidates import filtered, gates


def case(fire_prediction, smoke_prediction=None):
    truth = [False] * 10 + [True] * 10 + [False] * 30 + [True] * 10 + [False] * 20
    smoke_prediction = smoke_prediction or [False] * len(truth)
    return {'filename': 'synthetic.avi', 'split': 'development', 'metrics': {
        'fire': {'excluded': False, 'temporal': score_states(truth, fire_prediction, 10)},
        'smoke': {'excluded': False, 'temporal': score_states([False] * len(truth), smoke_prediction, 10)}}}


class CandidateComparisonTests(unittest.TestCase):
    def test_equal_event_totals_cannot_hide_loss_of_an_original_event(self):
        old, new = [False] * 80, [False] * 80
        old[15], new[55] = True, True
        result = gates([case(old)], [case(new)])
        self.assertTrue(result['checks']['recovers_fire_event'])
        self.assertFalse(result['checks']['no_lost_events'])
        self.assertFalse(result['passed'])

    def test_recovery_must_also_respect_smoke_false_episode_budget(self):
        old, new, smoke = [False] * 80, [False] * 80, [False] * 80
        old[15] = new[15] = new[55] = True
        smoke[5] = smoke[35] = True
        self.assertTrue(gates([case(old)], [case(new)])['passed'])
        result = gates([case(old)], [case(new, smoke)])
        self.assertFalse(result['checks']['smoke_false_guard'])
        self.assertFalse(result['passed'])

    def test_threshold_filter_keeps_boundary_and_does_not_mutate_raw_log(self):
        raw = {'frame_index': 7, 'detections': [
            {'label': 'fire', 'confidence': .35}, {'label': 'smoke', 'confidence': .349999}]}
        self.assertEqual(filtered(raw, .35)['detections'], [raw['detections'][0]])
        self.assertEqual(len(raw['detections']), 2)
        self.assertEqual(filtered(raw, .35)['frame_index'], 7)


if __name__ == '__main__':
    unittest.main()
