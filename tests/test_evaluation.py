import unittest
from core.evaluation import episodes, evaluate_video, score_states


class EvaluationTests(unittest.TestCase):
    def test_same_gap_policy_at_boundary(self):
        flags = [True] + [False] * 10 + [True]
        self.assertEqual(episodes(flags, 10, 1), [[0, 12]])
        self.assertEqual(episodes(flags, 10, .99), [[0, 1], [11, 12]])

    def test_predicted_only_in_merged_truth_gap_is_not_a_hit(self):
        truth = [True, False, False, True]
        score = score_states(truth, [False, True, False, False], 10)
        self.assertEqual(score['missed_scene_events'], 1)
        self.assertEqual(score['false_hint_episodes'], 1)

    def test_missing_prediction_and_delay(self):
        score = score_states([False, True, True, True], [False, False, False, True], 10)
        self.assertEqual(score['first_active_hint_delays_s'], [.2])
        self.assertEqual(score['false_negative_hint_frames'], 2)
        self.assertIsNone(score_states([False] * 3, [False] * 3, 10)['visible_state_recall'])

    def test_no_negative_duration_not_zero_false_rate(self):
        result = score_states([True] * 5, [True] * 5, 10)
        self.assertIsNone(result['false_hint_episodes_per_negative_minute'])

    def test_stale_confirmed_state_still_counts_as_visible_hint(self):
        row = {'decoded_frames': 2, 'fps': 10, 'intervals': [], 'class_status': {'fire': 'absent', 'smoke': 'uncertain'},
               'full_video_reviewed': True, 'reviewer': 'R1', 'notes': 'unknown smoke',
               'group_verified': False, 'development_seen': True, 'split': 'development_seen', 'quality': 'pending_content_review'}
        detections = [{'frame_index': i, 'timestamp_s': i / 10, 'detections': []} for i in range(2)]
        temporal = [{'timestamp_s': i / 10, 'tracks': [{'label': 'fire', 'state': 'confirmed', 'present_this_frame': False}],
                     'transitions': []} for i in range(2)]
        result = evaluate_video(row, detections, temporal)
        self.assertEqual(result['fire']['temporal']['false_positive_hint_frames'], 2)
        self.assertEqual(result['smoke'], {'excluded': True, 'reason': 'uncertain'})
        with self.assertRaises(ValueError):
            evaluate_video(row, detections[:-1], temporal)


if __name__ == '__main__':
    unittest.main()
