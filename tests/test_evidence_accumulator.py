import unittest

from core.event_regions import StableRegionTracker
from experiments.evidence_accumulator import AccumulatorConfig, EvidenceAccumulator


def detection(left=100, confidence=.8, label='fire'):
    return {'label':label,'confidence':confidence,'bbox_xyxy':[left,100,left+100,200]}


class AccumulatorTests(unittest.TestCase):
    def simulate(self, fps, duration, present, confidence=.8):
        region,accumulator=StableRegionTracker(),EvidenceAccumulator()
        states=[]
        for i in range(round(duration*fps)+1):
            t=i/fps
            state=region.update(t,[detection(confidence=confidence)] if present(t) else [],(640,480))
            states.append(accumulator.update(state))
        return accumulator,states

    def test_time_units_are_independent_of_sample_rate(self):
        results=[]
        for fps in (10,25,50):
            _,states=self.simulate(fps,3.,lambda t:True)
            first=next(s['timestamp_s'] for s in states if s['tracks'][0]['state']=='confirmed')
            results.append(first)
        self.assertLessEqual(max(results)-min(results),.1+1e-9)

    def test_absence_does_not_confirm_and_decays_prior_support(self):
        accumulator,states=self.simulate(10,1.,lambda t:t<.5)
        self.assertFalse(any(tr['type']=='event_confirmed' for s in states for tr in s['transitions']))
        self.assertLess(states[-1]['tracks'][0]['evidence_memory_s'],states[6]['tracks'][0]['evidence_memory_s'])

    def test_region_timeout_discards_old_memory(self):
        accumulator,states=self.simulate(10,2.,lambda t:t<.5 or t>1.5)
        self.assertEqual(states[-1]['tracks'][0]['started_at_s'],1.6)
        self.assertLess(states[-1]['tracks'][0]['evidence_memory_s'],.5)
        self.assertFalse(any(tr['type']=='event_confirmed' for s in states for tr in s['transitions']))

    def test_far_regions_cannot_share_energy(self):
        region,accumulator=StableRegionTracker(),EvidenceAccumulator()
        for i in range(11):
            state=accumulator.update(region.update(i/10,[detection(100 if i%2 else 450)],(640,480)))
        self.assertEqual(len(state['tracks']),2)
        self.assertTrue(all(t['evidence_memory_s']<.5 for t in state['tracks']))

    def test_energy_is_bounded(self):
        accumulator,states=self.simulate(10,5.,lambda t:True)
        self.assertEqual(states[-1]['tracks'][0]['evidence_memory_s'],accumulator.config.cap_s)

    def test_lower_confidence_accumulates_more_slowly(self):
        _,high=self.simulate(10,1.,lambda t:True,.9)
        _,low=self.simulate(10,1.,lambda t:True,.4)
        self.assertGreater(high[-1]['tracks'][0]['evidence_memory_s'],low[-1]['tracks'][0]['evidence_memory_s'])

    def test_smoke_snapshots_and_transitions_preserved(self):
        region,accumulator=StableRegionTracker(),EvidenceAccumulator()
        for i in range(31):
            original=region.update(i/10,[detection(label='smoke')],(640,480))
            actual=accumulator.update(original)
            self.assertEqual(actual,original)

    def test_closed_confirmed_event_has_actual_geometry(self):
        accumulator,states=self.simulate(10,2.,lambda t:True)
        accumulator.finish(2.1)
        event=accumulator.closed_events[0]
        self.assertEqual(event['last_bbox_normalized'],[100/640,100/480,200/640,200/480])
        self.assertEqual(event['confirmation_evidence']['method'],'bounded_leaky_support_v1')
        self.assertEqual(accumulator.finish(2.1),[])
        with self.assertRaises(ValueError):
            accumulator.update({'timestamp_s':2.2,'tracks':[],'transitions':[]})

    def test_does_not_inherit_old_confirmation_without_its_own_evidence(self):
        region=StableRegionTracker()
        for i in range(21):
            state=region.update(i/10,[detection()],(640,480))
        self.assertEqual(state['tracks'][0]['state'],'confirmed')
        actual=EvidenceAccumulator().update(state)
        self.assertEqual(actual['tracks'][0]['state'],'pending')
        self.assertIsNone(actual['tracks'][0]['confirmed_at_s'])

    def test_invalid_parameters_and_timestamps_rejected(self):
        for kwargs in ({'cap_s':.5},{'missing_decay_per_s':0},{'confidence_floor':1},{'minimum_gain':float('nan')}):
            with self.assertRaises(ValueError):
                AccumulatorConfig(**kwargs)
        accumulator=EvidenceAccumulator()
        accumulator.update({'timestamp_s':0.,'tracks':[],'transitions':[]})
        with self.assertRaises(ValueError):
            accumulator.update({'timestamp_s':0.,'tracks':[],'transitions':[]})


if __name__=='__main__':
    unittest.main()
