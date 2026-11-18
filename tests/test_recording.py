import json
from pathlib import Path
import tempfile
import unittest

import cv2
import numpy as np

from core.recording import VideoRecorder
from core.replay import ReplayTimeline, review_media


class RecordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.frame = np.zeros((120, 160, 3), dtype=np.uint8)
        self.recorders = []

    def tearDown(self):
        for recorder in self.recorders:
            recorder.close()
        self.temp.cleanup()

    def recorder(self, mode, **kwargs):
        recorder = VideoRecorder(self.root, 30, mode, **kwargs)
        self.recorders.append(recorder)
        return recorder

    def test_quiet_event_mode_saves_no_video_and_buffer_is_bounded(self):
        recorder = self.recorder('events')
        for index in range(220):
            recorder.write(self.frame, index * .01)
        self.assertLessEqual(len(recorder.buffer), 180)
        self.assertLessEqual(recorder.buffer_bytes, 32 * 1024 ** 2)
        recorder.close()
        self.assertEqual(list(self.root.rglob('*.mp4')), [])
        self.assertEqual(json.loads((self.root / 'clips.json').read_text())['clips'], [])

    def test_event_clip_has_pre_post_time_and_merges_nearby_triggers(self):
        recorder = self.recorder('events')
        for timestamp in range(25):
            recorder.write(self.frame, float(timestamp), [1] if timestamp == 7 else [2] if timestamp == 9 else [])
        recorder.close()
        clips = json.loads((self.root / 'clips.json').read_text())['clips']
        self.assertEqual(len(clips), 1)
        self.assertEqual(clips[0]['start_s'], 2.)
        self.assertEqual(clips[0]['end_s'], 19.)
        self.assertEqual(clips[0]['event_ids'], [1, 2])
        self.assertFalse((self.root / 'annotated.mp4').exists())
        media = review_media(self.root, {'source_kind': 'camera'}, 7.)
        self.assertEqual(len(media), 1)
        capture = cv2.VideoCapture(media[0]['path'])
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        capture.release()
        timeline = ReplayTimeline(30, count, media[0]['timestamps'])
        self.assertEqual(timeline.time(timeline.locate({'start_s': 2., 'end_s': 17., 'focus_s': 7.})[2]), 7.)

    def test_full_and_no_recording_modes_remain_distinct(self):
        recorder = self.recorder('none')
        recorder.write(self.frame, 0., [1])
        recorder.close()
        self.assertEqual(list(self.root.iterdir()), [])
        recorder = self.recorder('full')
        recorder.write(self.frame, 0.)
        recorder.write(self.frame, .5)
        recorder.close()
        self.assertTrue((self.root / 'annotated.mp4').exists())
        self.assertEqual(len((self.root / 'frame-times.jsonl').read_text().splitlines()), 2)

    def test_separate_events_generate_separate_clips_and_focus_selects_correct_clip(self):
        recorder = self.recorder('events', before_s=2., after_s=2.)
        for timestamp in range(30):
            recorder.write(self.frame, float(timestamp), [timestamp] if timestamp in (5, 25) else [])
        recorder.close()
        media = review_media(self.root, {'source_kind': 'camera'}, 25.)
        self.assertTrue(media[0]['path'].endswith('clip-2.mp4'))
        self.assertEqual(len(media), 2)

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            self.recorder('invalid')

    def test_segment_rotation_preserves_remaining_post_trigger_time(self):
        recorder = self.recorder('events', before_s=1., after_s=3., max_clip_s=5.)
        for timestamp in range(15):
            recorder.write(self.frame, float(timestamp), [1] if timestamp in (2, 4, 6) else [])
        recorder.close()
        clips = json.loads((self.root / 'clips.json').read_text())['clips']
        self.assertEqual(len(clips), 2)
        self.assertEqual(clips[1]['end_s'], 9.)
        self.assertEqual(clips[1]['event_ids'], [1])


if __name__ == '__main__':
    unittest.main()
