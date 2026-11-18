"""Stable board numbering and actual-grade sorting across monitoring runs."""
from copy import deepcopy
import math
import unittest

from core.event_order import enumerate_event_keys, sorted_event_keys


def event(run, identifier=1, *, device='north', name='北门', second=1., level='MEDIUM',
          score=50., review='pending', **extra):
    key = (device, run, identifier)
    value = {'event_id': identifier, 'output': run, 'confirmed_at_s': second,
             'device': {'id': device, 'name': name, 'location': '教学楼'}, 'review': review,
             'assessment': {'score': score, 'level': level}}
    value.update(extra)
    return key, value


class EventOrderTests(unittest.TestCase):
    def test_same_event_number_across_runs_and_ai_none_are_distinct_and_orderable(self):
        events = dict([event('run-20261117-100000-000000', 1),
                       event('run-20261117-100000-000000', None),
                       event('run-20261118-100000-000000', 1)])
        for mode in ('priority', 'time_desc', 'time_asc', 'device'):
            keys = sorted_event_keys(events, mode)
            self.assertEqual(len(keys), 3)
            self.assertEqual(set(keys), set(events))
        numbers = enumerate_event_keys(events)
        self.assertEqual(set(numbers.values()), {1, 2, 3})
        self.assertEqual(numbers[('north', 'run-20261118-100000-000000', 1)], 3)

    def test_priority_uses_saved_grade_not_equal_or_misleading_scores(self):
        high = event('run-20261117-100000-000000', 1, level='HIGH', score=44.)
        medium = event('run-20261117-100000-000000', 2, level='MEDIUM', score=44.)
        low = event('run-20261117-100000-000000', 3, level='LOW', score=99.)
        events = dict([low, medium, high])
        self.assertEqual(sorted_event_keys(events), [high[0], medium[0], low[0]])

    def test_exact_displayed_priority_and_fusion_grade_override_baseline(self):
        first = event('run-20261117-100000-000000', 1, level='HIGH',
                      priority={'score': 99., 'level': 'LOW'})
        second = event('run-20261117-100000-000000', 2, level='LOW')
        second[1]['assessment']['fusion'] = {'score': 20., 'level': 'HIGH'}
        self.assertEqual(sorted_event_keys(dict([first, second])), [second[0], first[0]])

    def test_unfinished_group_orders_by_grade_before_action_and_pending_status(self):
        action = event('run-20261117-100000-000000', 1, level='LOW', review='confirmed_abnormal')
        pending = event('run-20261117-100000-000000', 2, level='HIGH')
        completed = event('run-20261117-100000-000000', 3, level='HIGH', review='resolved')
        self.assertEqual(sorted_event_keys(dict([completed, pending, action])),
                         [pending[0], action[0], completed[0]])

    def test_same_unfinished_grade_prefers_action_then_newest_within_status(self):
        action = event('run-20261117-100000-000000', 1, level='HIGH', second=1.,
                       review='confirmed_abnormal')
        old_pending = event('run-20261117-100000-000000', 2, level='HIGH', second=2.)
        new_pending = event('run-20261117-100000-000000', 3, level='HIGH', second=3.)
        self.assertEqual(sorted_event_keys(dict([old_pending, new_pending, action])),
                         [action[0], new_pending[0], old_pending[0]])

    def test_completed_group_orders_by_saved_grade_then_time(self):
        high_old = event('run-20261117-100000-000000', 1, level='HIGH', second=1., review='resolved')
        high_new = event('run-20261117-100000-000000', 2, level='HIGH', second=2., review='normal_use')
        low = event('run-20261117-100000-000000', 3, level='LOW', second=3., review='false_positive')
        self.assertEqual(sorted_event_keys(dict([low, high_old, high_new])),
                         [high_new[0], high_old[0], low[0]])

    def test_record_position_rolls_across_days_and_preserves_run_microseconds(self):
        first = event(r'D:\records\run-20261117-235959-900000', 1, second=1.)
        second = event('/records/run-20261118-000000-000000', 2, second=.5)
        third = event('/records/run-20261118-000000-000001', 3, second=.5)
        events = dict([third, second, first])
        self.assertEqual(sorted_event_keys(events, 'time_asc'), [second[0], third[0], first[0]])
        self.assertEqual(sorted_event_keys(events, 'time_desc'), [first[0], third[0], second[0]])

    def test_explicit_dates_override_run_name_and_are_timezone_aware(self):
        first = event('run-20261117-100000-000000', 1,
                      confirmed_at='2026-11-19T08:00:00+08:00')
        second = event('run-20261120-100000-000000', 2,
                       event_datetime='2026-11-19T00:00:01Z')
        events = dict([second, first])
        self.assertEqual(sorted_event_keys(events, 'time_asc'), [first[0], second[0]])

    def test_recording_start_metadata_is_combined_with_media_offset(self):
        first = event('legacy-a', 1, second=30., recording_started_at='2026-11-17T23:59:50+08:00')
        second = event('legacy-b', 1, second=2., config={'recording_started_at': '2026-11-18T00:00:00+08:00'})
        self.assertEqual(sorted_event_keys(dict([first, second]), 'time_asc'), [second[0], first[0]])

    def test_unknown_dates_fall_back_to_media_position_without_filesystem_reads(self):
        first = event('unknown-a', 1, second=10.)
        second = event('unknown-b', None, second=2.)
        dated = event('run-20261117-100000-000000', 1, second=1.)
        events = dict([first, dated, second])
        self.assertEqual(sorted_event_keys(events, 'time_asc'), [dated[0], second[0], first[0]])
        self.assertEqual(sorted_event_keys(events, 'time_desc'), [dated[0], first[0], second[0]])

    def test_numbering_is_independent_of_view_sort_and_filter(self):
        events = dict([event('run-20261117-100000-000000', 5, second=3., level='HIGH'),
                       event('run-20261117-100000-000000', None, second=2., review='resolved'),
                       event('run-20261117-100000-000000', 1, second=1., level='LOW')])
        before = deepcopy(events)
        numbers = enumerate_event_keys(events)
        for mode in ('priority', 'time_desc', 'device'):
            ordered = sorted_event_keys(events, mode)
            filtered = [key for key in ordered if events[key]['review'] != 'resolved']
            self.assertEqual([numbers[key] for key in filtered],
                             [enumerate_event_keys(events)[key] for key in filtered])
        self.assertEqual(events, before)
        self.assertEqual(numbers[('north', 'run-20261117-100000-000000', 1)], 1)

    def test_equal_times_are_stable_across_insertion_order_and_devices(self):
        entries = [event('run-20261117-100000-000000', identifier, device=device, name=device)
                   for device in ('zeta', 'alpha') for identifier in (None, 2, 1)]
        first, second = dict(entries), dict(reversed(entries))
        self.assertEqual(enumerate_event_keys(first), enumerate_event_keys(second))
        self.assertEqual(sorted_event_keys(first, 'device'), sorted_event_keys(second, 'device'))
        self.assertTrue(all(key[0] == 'alpha' for key in sorted_event_keys(first, 'device')[:3]))

    def test_unknown_fields_and_bad_time_values_do_not_mix_none_and_integers(self):
        entries = [event('run-20269999-999999-000000', None, second=math.nan,
                         assessment={'level': ['invalid'], 'score': 99.}, review={'unexpected': True}),
                   event('unknown', 1, second=math.inf, device=None),
                   ('other', None)]
        events = dict(entries)
        for mode in ('priority', 'time_desc', 'time_asc', 'device'):
            self.assertEqual(set(sorted_event_keys(events, mode)), set(events))
        self.assertEqual(len(enumerate_event_keys(events)), 3)

    def test_invalid_sort_mode_is_reported_and_empty_collection_is_supported(self):
        self.assertEqual(sorted_event_keys({}), [])
        self.assertEqual(enumerate_event_keys({}), {})
        with self.assertRaises(ValueError):
            sorted_event_keys({}, 'score')
        with self.assertRaises(ValueError):
            sorted_event_keys([])


if __name__ == '__main__':
    unittest.main()
