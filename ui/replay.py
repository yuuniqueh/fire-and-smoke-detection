"""Local event replay; decoding stays off the GUI thread and never runs a model."""
import math
from pathlib import Path
from threading import Condition
from time import monotonic

from PySide6.QtCore import QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                              QSlider, QVBoxLayout)

from ui.widgets import VideoView
from core.replay import ReplayTimeline
from ui.product import STYLE, video_time


class ReplayWorker(QThread):
    ready = Signal(dict)
    failed = Signal(str)

    def __init__(self, path, window, parent=None, *, timestamps=None):
        super().__init__(parent)
        self.path, self.review_range = str(path), dict(window)
        self.timestamps = timestamps
        self._condition = Condition()
        self._stopping = False
        self._playing = False
        self._seek = None
        self._latest = None
        self._speed = 1.0
        self.bounds = None

    def stop(self):
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def play(self, enabled):
        with self._condition:
            self._playing = bool(enabled)
            self._condition.notify_all()

    def seek(self, frame):
        with self._condition:
            if self.bounds is not None:
                self._seek = max(self.bounds[0], min(self.bounds[1], int(frame)))
                self._playing = False
                self._condition.notify_all()

    def speed(self, value):
        if value not in (.5, 1., 2.):
            raise ValueError('Unsupported review speed')
        with self._condition:
            self._speed = value
            self._condition.notify_all()

    def preview(self):
        with self._condition:
            packet, self._latest = self._latest, None
            return packet, self._playing

    def is_playing(self):
        with self._condition:
            return self._playing

    def run(self):
        import cv2
        capture = cv2.VideoCapture(self.path)
        try:
            if not capture.isOpened():
                raise ValueError('无法解码该视频，请切换回放来源或检查文件。')
            fps = float(capture.get(cv2.CAP_PROP_FPS))
            count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            if not math.isfinite(fps) or fps <= 0 or count <= 0:
                raise ValueError('视频帧率或帧数无效，不能可靠定位回放时间。')
            timeline = ReplayTimeline(fps, count, self.timestamps)
            start, end, focus, clamped = timeline.locate(self.review_range)
            with self._condition:
                self.bounds = (start, end)
                self._seek = focus
            self.ready.emit({'fps': fps, 'start_frame': start, 'end_frame': end,
                             'focus_frame': focus, 'total_frames': count, 'focus_clamped': clamped,
                             'start_s': timeline.time(start), 'end_s': timeline.time(end),
                             'live_recording': self.timestamps is not None})
            next_frame, deadline, last_frame = focus, monotonic(), None
            while True:
                with self._condition:
                    if self._stopping:
                        break
                    target, self._seek = self._seek, None
                    if target is None and not self._playing:
                        self._condition.wait(.1)
                        continue
                    if target is None and next_frame > end:
                        next_frame = start
                        target = start
                    if target is None and monotonic() < deadline:
                        self._condition.wait(deadline - monotonic())
                        continue
                    speed = self._speed
                if target is not None:
                    if not capture.set(cv2.CAP_PROP_POS_FRAMES, target):
                        raise ValueError('该视频不支持可靠跳转，请重新编码后再回放。')
                    next_frame, deadline = target, monotonic()
                ok, frame = capture.read()
                if not ok:
                    if last_frame is None:
                        raise ValueError('选定时间处没有可解码画面。')
                    with self._condition:
                        self._playing = False
                        self._latest = {'frame': None, 'frame_index': last_frame,
                                        'timestamp_s': timeline.time(last_frame), 'ended': True,
                                        'note': '已到可解码末尾；容器帧数可能与实际画面有差异。'}
                    next_frame = end + 1
                    continue
                last_frame = next_frame
                with self._condition:
                    if next_frame >= end:
                        self._playing = False
                    self._latest = {'frame': frame, 'frame_index': next_frame,
                                    'timestamp_s': timeline.time(next_frame), 'ended': next_frame >= end}
                interval = (timeline.time(next_frame + 1) - timeline.time(next_frame)) if next_frame < end else 1. / fps
                next_frame += 1
                deadline = max(deadline + interval / speed, monotonic())
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            capture.release()


class ReplayDialog(QDialog):
    """Review original or annotated footage without changing analysis state."""
    def __init__(self, media, window, event_id, parent=None, *, embedded=False):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f'事件 #{event_id} · 视频复核')
        self.setStyleSheet(STYLE)
        self.resize(900, 680)
        self.setMinimumSize(640, 480)
        self.media, self.review_range = list(media), dict(window)
        self.worker = None
        self.metadata = None
        self.current_frame = None
        self.closing = False
        self.pending_media = None
        layout = QVBoxLayout(self)
        top = QHBoxLayout()
        self.source = QComboBox()
        if embedded:
            self.source.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            self.source.setMinimumContentsLength(4)
        for item in self.media:
            self.source.addItem(item['label'])
        self.source.currentIndexChanged.connect(self._change_media)
        top.addWidget(QLabel('回放来源'))
        top.addWidget(self.source, 1)
        layout.addLayout(top)
        self.context = QLabel('正在读取视频…')
        self.context.setWordWrap(True)
        layout.addWidget(self.context)
        self.video = VideoView()
        if embedded:
            self.video.setMinimumWidth(160)
        layout.addWidget(self.video, 1)
        self.position = QLabel('定位中…')
        self.position.setWordWrap(True)
        layout.addWidget(self.position)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(lambda: self.worker.play(False) if self.worker else None)
        self.slider.sliderReleased.connect(lambda: self.worker.seek(self.slider.value()) if self.worker else None)
        layout.addWidget(self.slider)
        controls = QGridLayout() if embedded else QHBoxLayout()
        self.play_button = QPushButton('播放')
        self.play_button.clicked.connect(self._toggle_play)
        self.before_button = QPushButton('前一帧')
        self.before_button.clicked.connect(lambda: self._step(-1))
        self.after_button = QPushButton('后一帧')
        self.after_button.clicked.connect(lambda: self._step(1))
        self.focus_button = QPushButton('返回提示时刻')
        self.focus_button.clicked.connect(self._focus)
        self.speed_combo = QComboBox()
        for label, value in [('0.5 倍', .5), ('1 倍', 1.), ('2 倍', 2.)]:
            self.speed_combo.addItem(label, value)
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.currentIndexChanged.connect(lambda: self.worker.speed(self.speed_combo.currentData()) if self.worker else None)
        if embedded:
            self.focus_button.setText('回到事件')
            for row, pair in enumerate(((self.play_button, self.focus_button), (self.before_button, self.after_button))):
                for column, widget in enumerate(pair):
                    controls.addWidget(widget, row, column)
            controls.addWidget(self.speed_combo, 2, 0, 1, 2)
        else:
            for widget in (self.play_button, self.before_button, self.after_button, self.focus_button, self.speed_combo):
                controls.addWidget(widget)
        layout.addLayout(controls)
        note = QLabel('历史片段 · 时间为原视频 / 监看经过时间；按可用录像截取，不播放音频。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self._start_media(0)

    def _enable(self, enabled):
        for widget in (self.slider, self.play_button, self.before_button, self.after_button,
                       self.focus_button, self.speed_combo):
            widget.setEnabled(enabled)

    def _start_media(self, index):
        self.metadata = None
        self.current_frame = None
        self._enable(False)
        item = self.media[index]
        self.context.setText(Path(item['path']).name + ' · ' +
                             ('原视频复核 · 未叠加本系统预测' if item['kind'] == 'original' else
                              '检测结果视频 · 含模型标记；请区分观察与预测'))
        self.context.setToolTip(item['path'])
        self.video.clear_frame('正在读取视频…')
        self.worker = ReplayWorker(item['path'], self.review_range, self, timestamps=item.get('timestamps'))
        self.worker.ready.connect(self._ready)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.speed(self.speed_combo.currentData())
        self.worker.start()

    def _change_media(self, index):
        self.pending_media = index
        self._enable(False)
        if self.worker:
            self.worker.stop()
        else:
            self.pending_media = None
            self._start_media(index)

    def _ready(self, metadata):
        if self.closing or self.pending_media is not None:
            return
        self.metadata = metadata
        if metadata.get('focus_clamped'):
            self.context.setText(self.context.text() + ' · 提示时刻超出可用范围，已定位到视频边界')
        self.slider.setRange(metadata['start_frame'], metadata['end_frame'])
        self.slider.setValue(metadata['focus_frame'])
        self._enable(True)

    def _failed(self, message):
        self._enable(False)
        self.position.setText(message)
        self.video.clear_frame(message)

    def _finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        if self.closing:
            self.close()
        elif self.pending_media is not None:
            index, self.pending_media = self.pending_media, None
            self._start_media(index)

    def _poll(self):
        if not self.worker or self.pending_media is not None:
            return
        packet, playing = self.worker.preview()
        self.play_button.setText('暂停' if playing else '播放')
        if packet and self.metadata:
            self.current_frame = packet['frame_index']
            if packet['frame'] is not None:
                self.video.set_bgr(packet['frame'])
            if not self.slider.isSliderDown():
                self.slider.setValue(self.current_frame)
            fps = self.metadata['fps']
            suffix = ' · 已到片段末尾' if packet.get('ended') else ''
            self.position.setText(f'{video_time(packet["timestamp_s"])} · 片段 {video_time(self.metadata["start_s"])}–{video_time(self.metadata["end_s"])}'
                                  + suffix + (' · ' + packet['note'] if packet.get('note') else ''))
            self.video.set_context('历史回放 · 非实时画面')

    def _toggle_play(self):
        if self.worker:
            self.worker.play(not self.worker.is_playing())

    def _step(self, offset):
        if self.worker and self.current_frame is not None:
            self.worker.seek(self.current_frame + offset)

    def _focus(self):
        if self.worker and self.metadata:
            self.worker.seek(self.metadata['focus_frame'])

    def closeEvent(self, event):
        if self.worker:
            self.closing = True
            self._enable(False)
            self.worker.stop()
            event.ignore()
        else:
            self.timer.stop()
            event.accept()
