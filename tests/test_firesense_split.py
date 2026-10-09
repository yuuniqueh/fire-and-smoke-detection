import unittest
from experiments.freeze_firesense_plan import design_groups, propose_split


class SplitTests(unittest.TestCase):
    def rows(self):
        return [{'filename': str(i), 'development_seen': i < 4, 'quality': 'quarantined_frame_count' if i == 39 else 'ok',
                 'class_status': {'fire': 'present' if i % 3 == 0 else 'absent',
                                  'smoke': 'present' if i % 4 == 0 else 'absent'}} for i in range(40)]

    def test_transitive_grouping_and_known_members(self):
        groups = design_groups(self.rows(), [['1', '2'], ['2', '3']])
        self.assertIn(['1', '2', '3'], groups)
        with self.assertRaises(ValueError):
            design_groups(self.rows(), [['not-registered']])

    def test_no_seen_or_group_leakage_and_deterministic(self):
        rows = self.rows()
        groups = design_groups(rows, [['0', '15'], ['8', '9'], ['9', '10']])
        splits = propose_split(rows, groups)
        self.assertEqual(splits, propose_split(rows, groups))
        self.assertEqual(splits['0'], 'development')
        self.assertEqual(splits['15'], 'development')
        self.assertEqual(splits['39'], 'excluded')
        for group in groups:
            self.assertLessEqual(len({splits[name] for name in group if splits[name] != 'excluded'}), 1)


if __name__ == '__main__':
    unittest.main()
