"""Human review must be explicit, independent, and robust to missing evidence."""
from copy import deepcopy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication

from review_fusion_data import FusionReviewWindow, current_source_frames


def dataset():
    return {'samples': [
        {'sample_id': 'one', 'source': 'original.avi', 'timestamp_s': 3.,
         'segment_start_s': 1., 'segment_end_s': 3.,
         'evidence_images': [{'path': 'missing.jpg', 'verified': True, 'timestamp_s': 2.}],
         'scene_record': {'scene': {'description': '桌面可见一处火焰。'}}},
        {'sample_id': 'two', 'source': 'another.avi', 'timestamp_s': 5.,
         'evidence_images': [], 'scene_record': None}],
        'annotation_summary': {}}


class FusionReviewUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_reviewer_and_explicit_choice_required(self):
        with patch('review_fusion_data.load_dataset', return_value=dataset()), \
                patch('review_fusion_data.save_annotation') as save:
            window = FusionReviewWindow('fixture')
            self.assertEqual(window.own_review.text(), '尚未提交判断')
            self.assertTrue(window.description.isHidden())
            self.assertFalse(window.save_label('normal'))
            save.assert_not_called()
            self.assertIn('审核人', window.message.text())
            window.close()

    def test_explicit_choice_saves_reviewer_note_and_refreshes_status(self):
        value = dataset()
        def save_value(path, sample_id, **kwargs):
            value['annotation_summary'][sample_id] = {'status': 'pending',
                'reviews': {kwargs['reviewer']: {'label': kwargs['label'], 'note': kwargs['note']}}}
        with tempfile.TemporaryDirectory() as folder, \
                patch('review_fusion_data.load_dataset', side_effect=lambda path: deepcopy(value)), \
                patch('review_fusion_data.save_annotation', side_effect=save_value) as save:
            window = FusionReviewWindow(folder)
            window.reviewer.setText('甲')
            window.note.setPlainText('已回看原录像，证据不足。')
            self.assertTrue(window.save_label('uncertain'))
            save.assert_called_once_with(Path(folder).resolve(), 'one', reviewer='甲',
                label='uncertain', note='已回看原录像，证据不足。')
            self.assertIn('证据不足', window.own_review.text())
            self.assertIn('1 / 2', window.progress.text())
            self.assertIn('另一位', window.review_status.text())
            window.close()

    def test_navigation_missing_images_and_new_reviewer_remain_independent(self):
        value = dataset()
        value['annotation_summary'] = {'one': {'status': 'agreed', 'label': 1, 'reviews': {
            '甲': {'label': 'abnormal', 'note': '甲的意见'},
            '乙': {'label': 'abnormal', 'note': '乙的意见'}}}}
        with patch('review_fusion_data.load_dataset', return_value=value):
            window = FusionReviewWindow('fixture')
            self.assertFalse(window.has_image)
            self.assertIn('原画面暂不可用', window.image.text())
            window.reviewer.setText('丙')
            self.assertEqual(window.own_review.text(), '尚未提交判断')
            self.assertNotIn('意见一致', window.review_status.text())
            self.assertNotIn('异常', window.own_review.text())
            self.assertEqual(window.note.toPlainText(), '')
            window.move_sample(1)
            self.assertEqual(window.current_sample['sample_id'], 'two')
            self.assertFalse(window.next.isEnabled())
            self.assertFalse(window.frame_next.isEnabled())
            window.reviewer.setText('甲')
            window.move_sample(-1)
            self.assertIn('异常烟火线索', window.own_review.text())
            window.jump_unreviewed()
            self.assertEqual(window.current_sample['sample_id'], 'two')
            window.close()

    def test_empty_dataset_and_open_error_do_not_discard_open_reviews(self):
        with patch('review_fusion_data.load_dataset', return_value={'samples': []}):
            window = FusionReviewWindow('empty')
            self.assertFalse(window.buttons['normal'].isEnabled())
            self.assertFalse(window.next_unreviewed.isEnabled())
            with patch('review_fusion_data.load_dataset', side_effect=ValueError('格式不完整')):
                self.assertFalse(window.open_dataset('bad'))
            self.assertEqual(window.samples, [])
            self.assertIn('格式不完整', window.message.text())
            window.close()

    def test_reviewer_lookup_matches_casefolded_backend_identity(self):
        value = dataset()
        value['annotation_summary'] = {'one': {'status': 'pending', 'reviews': {
            'alice': {'label': 'normal', 'note': '自己的判断'}}}}
        with patch('review_fusion_data.load_dataset', return_value=value):
            window = FusionReviewWindow('fixture')
            window.reviewer.setText(' Alice ')
            self.assertIn('正常用火或干扰', window.own_review.text())
            self.assertIn('1 / 2', window.progress.text())
            window.jump_unreviewed()
            self.assertEqual(window.current_sample['sample_id'], 'two')
            window.close()

    def test_source_sampling_floors_frame_positions_and_never_uses_future_frame(self):
        import cv2
        import numpy as np

        class Capture:
            def __init__(self):
                self.cursor = 0
                self.requested = []
                self.released = False
            def isOpened(self):
                return True
            def get(self, prop):
                return {cv2.CAP_PROP_FPS: 4., cv2.CAP_PROP_FRAME_COUNT: 20.,
                    cv2.CAP_PROP_FRAME_WIDTH: 8., cv2.CAP_PROP_FRAME_HEIGHT: 6.,
                    cv2.CAP_PROP_POS_FRAMES: self.cursor + 1.,
                    cv2.CAP_PROP_POS_MSEC: self.cursor * 250.}.get(prop, 0.)
            def set(self, prop, value):
                self.cursor = value
                self.requested.append(value)
                return True
            def read(self):
                return True, np.full((6, 8, 3), self.cursor, dtype=np.uint8)
            def release(self):
                self.released = True

        capture = Capture()
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'software-test-video.avi'
            source.write_bytes(b'Only a mocked decoder software contract; not training data.')
            with patch('cv2.VideoCapture', return_value=capture):
                frames, error = current_source_frames({'source': str(source), 'timestamp_s': 1.13})
        self.assertEqual(error, '')
        self.assertEqual(capture.requested, [0, 2, 4])
        self.assertEqual([item['timestamp_s'] for item in frames], [0., .5, 1.])
        self.assertTrue(all(item['timestamp_s'] <= 1.13 for item in frames))
        self.assertEqual(len(frames), 3)
        self.assertTrue(capture.released)

    def test_mispositioned_decoder_frame_is_rejected(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'software-test-video.avi'
            source.write_bytes(b'Mocked decoder only.')
            capture = unittest.mock.Mock()
            capture.isOpened.return_value = True
            capture.get.side_effect = lambda prop: {cv2.CAP_PROP_FPS: 4.,
                cv2.CAP_PROP_FRAME_COUNT: 20., cv2.CAP_PROP_FRAME_WIDTH: 8.,
                cv2.CAP_PROP_FRAME_HEIGHT: 6., cv2.CAP_PROP_POS_FRAMES: 100.,
                cv2.CAP_PROP_POS_MSEC: 25_000.}.get(prop, 0.)
            capture.set.return_value = True
            capture.read.return_value = (True, np.zeros((6, 8, 3), dtype=np.uint8))
            with patch('cv2.VideoCapture', return_value=capture):
                frames, error = current_source_frames({'source': str(source), 'timestamp_s': 1.13})
            self.assertEqual(frames, [])
            self.assertTrue(error)
            capture.release.assert_called_once()

    def test_fallback_ai_frame_is_explicitly_older_and_future_evidence_is_hidden(self):
        value = dataset()
        value['samples'][0]['timestamp_s'] = 10.
        value['samples'][0]['evidence_images'] = [
            {'path': 'missing.jpg', 'verified': True, 'timestamp_s': 2.},
            {'path': 'future.jpg', 'verified': True, 'timestamp_s': 11.}]
        with patch('review_fusion_data.load_dataset', return_value=value):
            window = FusionReviewWindow('fixture')
            self.assertFalse(window.current_frames)
            self.assertFalse(window.evidence_notice.isHidden())
            self.assertIn('非当前片段', window.evidence_notice.text())
            self.assertIn('较早AI取帧', window.frame_info.text())
            self.assertIn('早 8.0 秒', window.frame_info.text())
            self.assertIn('00:02.000', window.frame_info.text())
            self.assertEqual(len(window.images), 1)
            window.close()

    def test_current_source_frames_are_preferred_to_old_ai_evidence(self):
        import numpy as np
        frames = [{'frame': np.zeros((6, 8, 3), dtype=np.uint8), 'frame_index': 8,
            'timestamp_s': 2.75, 'kind': 'decision_raw', 'verified': True}]
        with patch('review_fusion_data.load_dataset', return_value=dataset()), \
                patch('review_fusion_data.current_source_frames', return_value=(frames, '')):
            window = FusionReviewWindow('fixture')
            self.assertTrue(window.has_image)
            self.assertTrue(window.current_frames)
            self.assertTrue(window.evidence_notice.isHidden())
            self.assertIn('当前片段', window.frame_info.text())
            self.assertIn('00:02.750', window.frame_info.text())
            self.assertTrue(window.description.isHidden())
            window.close()


if __name__ == '__main__':
    unittest.main()
