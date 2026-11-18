import math
import unittest

from core.notifications import NotificationConfig, NotificationEngine


def inputs(timestamp, rows=(), transitions=()):
    events = [{'event_id': event_id, 'label': label, 'level': level, 'fresh_evidence': fresh}
              for event_id, label, level, fresh in rows]
    return ({'timestamp_s': timestamp, 'events': events},
            {'timestamp_s': timestamp, 'transitions': list(transitions),
             'tracks': [{'event_id': event['event_id'], 'state': 'confirmed'} for event in events]})


class NotificationTests(unittest.TestCase):
    def test_candidates_do_not_notify_and_default_is_silent(self):
        engine = NotificationEngine()
        assessment, snapshot = inputs(0)
        snapshot['tracks'].append({'event_id': 8, 'state': 'candidate'})
        self.assertEqual(engine.update(assessment, snapshot), [])
        notice = engine.update(*inputs(.2, [(1, 'fire', 'HIGH', True)]))[0]
        self.assertEqual(notice['channels'], ['panel', 'log'])
        self.assertIn('红色提示 · 立即核查', notice['title'])
        self.assertNotIn('已确认火灾', notice['message'])

    def test_notification_keeps_its_own_score_evidence_snapshot(self):
        engine = NotificationEngine()
        assessment, snapshot = inputs(0, [(1, 'fire', 'LOW', True)])
        event = assessment['events'][0]
        event.update(score=40.0, features={'coverage_ratio_smoothed': .05}, score_components={'class_base': 25})
        notice = engine.update(assessment, snapshot)[0]
        event['features']['coverage_ratio_smoothed'] = .9
        event['score_components']['class_base'] = 99
        self.assertEqual(notice['features']['coverage_ratio_smoothed'], .05)
        self.assertEqual(notice['score_components']['class_base'], 25)
        self.assertEqual(notice['score'], 40)

    def test_sound_only_on_fresh_medium_high_open_and_upgrade(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True))
        low = engine.update(*inputs(0, [(1, 'fire', 'LOW', True)]))[0]
        medium = engine.update(*inputs(1, [(1, 'fire', 'MEDIUM', True)]))[0]
        high = engine.update(*inputs(2, [(1, 'fire', 'HIGH', True)]))[0]
        down = engine.update(*inputs(3, [(1, 'fire', 'MEDIUM', True)]))[0]
        self.assertNotIn('sound', low['channels'])
        self.assertIn('sound', medium['channels'])
        self.assertIn('sound', high['channels'])
        self.assertNotIn('sound', down['channels'])
        self.assertEqual([medium['kind'], high['kind'], down['kind']],
                         ['level_upgrade', 'level_upgrade', 'level_downgrade'])
        self.assertEqual(high['previous_level'], 'MEDIUM')

    def test_same_level_never_produces_repeated_frame_notices(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True, repeat_cooldown_s=1))
        engine.update(*inputs(0, [(1, 'fire', 'MEDIUM', True)]))
        for timestamp in (.1, 1.0, 10.0, 100.0):
            duplicate_confirmation = [{'type': 'event_confirmed', 'event_id': 1, 'alert_emitted': True}]
            self.assertEqual(engine.update(*inputs(timestamp, [(1, 'fire', 'MEDIUM', True)], duplicate_confirmation)), [])
        self.assertEqual(engine.summary()['total_notifications'], 1)

    def test_upgrade_not_suppressed_by_repeat_sound_cooldown(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True, repeat_cooldown_s=15))
        engine.update(*inputs(0, [(1, 'fire', 'MEDIUM', True)]))
        engine.update(*inputs(1, [(1, 'fire', 'LOW', True)]))
        repeat_upgrade = engine.update(*inputs(2, [(1, 'fire', 'MEDIUM', True)]))[0]
        self.assertEqual(repeat_upgrade['kind'], 'level_upgrade')
        self.assertIn('sound', repeat_upgrade['channels'])

    def test_same_identity_reopening_same_grade_cooldown_preserves_log(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True, repeat_cooldown_s=15))
        engine.update(*inputs(0, [(1, 'fire', 'MEDIUM', True)]))
        engine.update(*inputs(1))
        reopened = engine.update(*inputs(2, [(1, 'fire', 'MEDIUM', True)]))[0]
        self.assertEqual(reopened['channels'], ['panel', 'log'])
        self.assertEqual(reopened['sound_suppressed_reason'], 'repeat_cooldown')
        engine.update(*inputs(3))
        later = engine.update(*inputs(16, [(1, 'fire', 'MEDIUM', True)]))[0]
        self.assertIn('sound', later['channels'])

    def test_tracker_alert_suppression_still_logs_confirmation(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True))
        transition = [{'type': 'event_confirmed', 'event_id': 1, 'alert_emitted': False, 'cooldown_suppressed': True}]
        notice = engine.update(*inputs(0, [(1, 'smoke', 'HIGH', True)], transition))[0]
        self.assertEqual(notice['kind'], 'event_confirmed')
        self.assertEqual(notice['channels'], ['panel', 'log'])
        self.assertEqual(notice['sound_suppressed_reason'], 'tracker_cooldown')

    def test_stale_evidence_never_requests_sound(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True))
        notice = engine.update(*inputs(0, [(1, 'fire', 'HIGH', False)]))[0]
        self.assertNotIn('sound', notice['channels'])
        self.assertEqual(notice['sound_suppressed_reason'], 'stale_evidence')

    def test_independent_events_receive_their_own_notices_and_sound(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True))
        notices = engine.update(*inputs(0, [(1, 'fire', 'HIGH', True), (2, 'smoke', 'MEDIUM', True)]))
        self.assertEqual(len(notices), 2)
        self.assertEqual({notice['event_id'] for notice in notices}, {1, 2})
        self.assertTrue(all('sound' in notice['channels'] for notice in notices))
        changed = engine.update(*inputs(1, [(1, 'fire', 'HIGH', True), (2, 'smoke', 'LOW', True)]))
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]['event_id'], 2)

    def test_event_close_and_finish_are_silent_and_not_safety_claim(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True))
        engine.update(*inputs(0, [(1, 'fire', 'HIGH', True), (2, 'smoke', 'LOW', True)]))
        closed = engine.update(*inputs(1, [(2, 'smoke', 'LOW', True)],
                                      [{'event_id': 1, 'type': 'event_closed', 'reason': 'evidence_gap'}]))[0]
        self.assertEqual(closed['kind'], 'event_closed')
        self.assertEqual(closed['close_reason'], 'evidence_gap')
        self.assertNotIn('sound', closed['channels'])
        final = engine.finish(1, 'user_stop')
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]['event_id'], 2)
        self.assertNotIn('sound', final[0]['channels'])
        self.assertIn('不代表现场已安全', final[0]['message'])
        self.assertEqual(engine.finish(1), [])
        self.assertEqual(engine.summary()['active_events'], 0)
        self.assertEqual(engine.summary()['total_notifications'], 4)
        with self.assertRaises(ValueError):
            engine.update(*inputs(2))

    def test_history_and_deduplication_memory_bounded(self):
        engine = NotificationEngine(NotificationConfig(sound_enabled=True, history_limit=3))
        for index in range(10):
            engine.update(*inputs(index * 2, [(index, 'fire', 'HIGH', True)]))
            engine.update(*inputs(index * 2 + 1))
        summary = engine.summary()
        self.assertEqual(summary['total_notifications'], 20)
        self.assertEqual(summary['history_retained'], 3)
        self.assertEqual(summary['active_events'], 0)
        self.assertEqual(summary['sound_requests'], 10)
        self.assertEqual(sum(summary['by_kind'].values()), summary['total_notifications'])
        self.assertLessEqual(len(engine._last_audible), 3)

    def test_timestamp_alignment_and_config_validation(self):
        for kwargs in ({'sound_enabled': 1}, {'repeat_cooldown_s': math.nan},
                       {'repeat_cooldown_s': -1}, {'history_limit': 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                NotificationConfig(**kwargs)
        engine = NotificationEngine()
        assessment, snapshot = inputs(0)
        snapshot['timestamp_s'] = .1
        with self.assertRaises(ValueError):
            engine.update(assessment, snapshot)
        snapshot['timestamp_s'] = math.nan
        with self.assertRaises(ValueError):
            engine.update(assessment, snapshot)
        engine.update(*inputs(1))
        with self.assertRaises(ValueError):
            engine.update(*inputs(1))


if __name__ == '__main__':
    unittest.main()
