"""Streaming safety, compact field parity and bounded raw evidence capture."""
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from core.scene_protocol import expand_scene, partial_description
from core.scene_review import analyze_scene, SCHEMA, validate_scene


def scene_fixture():
    return {'description': '桌面上可见蜡烛火焰。', 'scene': 'candle', 'relation': 'expected_burning',
            'evidence_quality': 'adequate', 'spread': 'not_observed', 'visible_evidence': ['烛芯上方可见火焰'],
            'hazard_cues': [], 'uncertainties': ['画面外未知'], 'next_check': '核对周边可燃物。'}


class WireResponse(io.BytesIO):
    def __init__(self, events):
        super().__init__(b''.join(json.dumps(event, ensure_ascii=False).encode('utf-8') + b'\n' for event in events))
        self.headers = {'Content-Type': 'application/x-ndjson'}


class SceneSpeedTests(unittest.TestCase):
    def frames(self):
        return [{'timestamp_s': t, 'jpeg': data, 'sha256': hashlib.sha256(data).hexdigest()}
                for t, data in ((0., b'fixture1'), (1., b'fixture2'))]

    def test_field_normalization_preserves_provided_values_without_inventing_missing_fields(self):
        long = scene_fixture()
        self.assertEqual(expand_scene(long), long)
        scalar = {**long, 'visible_evidence': '烛芯上方可见火焰', 'hazard_cues': '无'}
        self.assertEqual(expand_scene(scalar)['visible_evidence'], ['烛芯上方可见火焰'])
        self.assertEqual(expand_scene(scalar)['hazard_cues'], [])
        del long['evidence_quality']
        with self.assertRaises(ValueError):
            validate_scene(expand_scene(long), 2, 1.)

    def test_partial_description_decodes_escapes_without_exposing_schema_or_json_markup(self):
        self.assertEqual(partial_description('{"d":"桌面上的火'), '桌面上的火')
        self.assertEqual(partial_description('{"d":"桌面\\n蜡烛"'), '桌面\n蜡烛')
        self.assertEqual(partial_description('{"d":"桌面\\u70'), '桌面')
        self.assertEqual(partial_description('{"description":{"type":"string"}}'), '')
        self.assertEqual(partial_description('{"other":"not a description"}'), '')

    def test_stream_preview_is_delivered_before_final_validated_result(self):
        observed = []
        response = WireResponse([{'event': 'description', 'description': '桌面上可见蜡烛'},
            {'event': 'result', 'result': {'model': 'fixture-only', 'message': {'content': json.dumps(scene_fixture())}}}])
        with patch('core.scene_review.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            result = analyze_scene(self.frames(), on_progress=lambda value: observed.append(value))
        self.assertTrue(observed[0]['preliminary'])
        self.assertEqual(result['scene'], scene_fixture())
        self.assertIsNotNone(result['first_description_s'])
        self.assertEqual(result['frame_count'], 2)

    def test_partial_stream_cannot_be_returned_as_a_complete_review(self):
        response = WireResponse([{'event': 'description', 'description': '桌面上可见蜡烛'}])
        with patch('core.scene_review.urllib.request.build_opener') as opener:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, '完整复核'):
                analyze_scene(self.frames(), on_progress=lambda value: None)

    def test_single_frame_limit_is_idempotent_across_server_and_client_validation(self):
        once = validate_scene(scene_fixture(), 1, 0.)
        self.assertEqual(validate_scene(once, 1, 0.), once)

    def test_live_worker_captures_only_confirmed_event_frames_in_a_bounded_buffer(self):
        from core.pipeline import AnalysisOptions
        from ui.worker import AnalysisWorker
        worker = AnalysisWorker(AnalysisOptions(source='fixture-only'))
        worker.scene_evidence_enabled = True
        with patch('core.scene_review.encode_frame', side_effect=lambda frame, t: {'timestamp_s': t, 'jpeg': b'fixture', 'sha256': 'fixture-only'}):
            for index in range(10):
                worker._frame({'timestamp_s': index * .5, 'raw_frame': object(), 'risk': None,
                               'temporal': {'tracks': [{'event_id': 9, 'state': 'confirmed'}]}, 'notifications': []})
        frames = worker.scene_evidence(9)
        self.assertEqual(len(frames), 3)
        self.assertEqual([f['timestamp_s'] for f in frames], [3.5, 4., 4.5])
        self.assertEqual(worker.scene_evidence(10), [])


if __name__ == '__main__':
    unittest.main()
