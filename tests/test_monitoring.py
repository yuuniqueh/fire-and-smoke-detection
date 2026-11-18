import unittest

from core.monitoring import MonitoringConfig, MonitoringScheduler


class MonitoringTests(unittest.TestCase):
    def test_full_preserves_every_frame_and_resolution(self):
        scheduler = MonitoringScheduler()
        for i in range(30):
            plan = scheduler.plan(i / 10)
            self.assertEqual((plan['perform'], plan['image_size']), (True, 640))
            scheduler.observe(i / 10, plan, passes=1)
        self.assertEqual(scheduler.summary()['analyzed_frames'], 30)
        self.assertEqual(scheduler.summary()['skipped_frames'], 0)

    def test_idle_skips_but_high_resolution_audits_do_not_depend_on_candidates(self):
        scheduler = MonitoringScheduler('adaptive')
        audits, probes = [], []
        for i in range(101):
            t = i / 10
            plan = scheduler.plan(t)
            if plan['image_size'] == 960:
                audits.append(t)
            if plan['perform']:
                probes.append(t)
            scheduler.observe(t, plan, passes=int(plan['perform']))
        self.assertEqual(audits, [0, 5, 10])
        self.assertEqual(len(probes), 21)
        self.assertEqual(scheduler.summary()['skipped_frames'], 80)

    def test_enhancement_is_immediate_and_kept_while_tracks_exist(self):
        scheduler = MonitoringScheduler('adaptive')
        plan = scheduler.plan(0)
        scheduler.escalate(0)
        scheduler.observe(0, plan, detections=True, active_tracks=True, passes=1)
        for t in range(1, 15):
            plan = scheduler.plan(t)
            self.assertEqual(plan['image_size'], 960)
            scheduler.observe(t, plan, active_tracks=True, passes=1)
        self.assertEqual(scheduler.state, 'enhanced')
        for t in (15, 16, 17):
            plan = scheduler.plan(t)
            scheduler.observe(t, plan, passes=1)
        self.assertEqual(scheduler.state, 'idle')

    def test_time_and_configuration_validation(self):
        scheduler = MonitoringScheduler()
        scheduler.plan(0)
        with self.assertRaises(ValueError):
            scheduler.plan(0)
        for kwargs in ({'idle_hz': 0}, {'idle_image_size': 417}, {'audit_interval_s': float('nan')}):
            with self.assertRaises(ValueError):
                MonitoringConfig(**kwargs)


if __name__ == '__main__':
    unittest.main()
