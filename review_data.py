"""Raw-video human annotation tool. Does not load models or run inference."""
import copy
import hashlib
import json
import sys
from pathlib import Path

from core.runtime import ROOT, configure_runtime
configure_runtime()
import cv2
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QSlider,
    QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget)
from core.data_review import STATES, read_review, replace_video, save_review
from ui.widgets import VideoView

REVIEW_PATH = ROOT / 'data/firesense/review.json'
STATE_TEXT = ('未判断', '无', '有', '不确定')


class ReviewWindow(QMainWindow):
    def __init__(self, path=REVIEW_PATH):
        super().__init__()
        self.path = Path(path)
        self.document, self.revision = read_review(self.path)
        self.selected = -1
        self.capture = None
        self.frame_index = 0
        self.loading = False
        self.dirty = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.advance)
        self.setWindowTitle('焰鉴 · 原视频标注')
        self.resize(1260, 840)
        self.setMinimumSize(1020, 740)
        container = QWidget()
        self.setCentralWidget(container)
        layout = QVBoxLayout(container)
        title = QLabel('素材审查：先看原视频，分别判断火焰与烟雾；不显示模型预测')
        title.setStyleSheet('font-size:18px;font-weight:bold;padding:8px;')
        layout.addWidget(title)
        split = QSplitter()
        layout.addWidget(split, 1)
        self.files = QListWidget()
        self.files.setMinimumWidth(205)
        self.files.setMaximumWidth(310)
        split.addWidget(self.files)
        right = QWidget()
        split.addWidget(right)
        body = QVBoxLayout(right)
        self.info = QLabel()
        self.info.setWordWrap(True)
        body.addWidget(self.info)
        self.view = VideoView()
        body.addWidget(self.view, 1)
        self.clock = QLabel()
        body.addWidget(self.clock)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(self.pause)
        self.slider.sliderReleased.connect(lambda: self.seek(self.slider.value()))
        body.addWidget(self.slider)
        controls = QHBoxLayout()
        self.play_button = QPushButton('播放')
        self.play_button.clicked.connect(self.toggle_play)
        controls.addWidget(self.play_button)
        for text, step in [('前一帧', -1), ('后一帧', 1), ('后退 1 秒', -2), ('前进 1 秒', 2)]:
            button = QPushButton(text)
            button.clicked.connect(lambda checked=False, amount=step: self.step(amount))
            controls.addWidget(button)
        self.speed = QComboBox()
        self.speed.addItems(['0.5 倍', '1 倍', '2 倍'])
        self.speed.setCurrentIndex(1)
        self.speed.currentIndexChanged.connect(self.update_speed)
        controls.addWidget(self.speed)
        body.addLayout(controls)
        form = QFormLayout()
        flags = QHBoxLayout()
        self.class_boxes = {}
        for label, text in [('fire', '火焰'), ('smoke', '烟雾')]:
            flags.addWidget(QLabel(text))
            box = QComboBox()
            box.addItems(STATE_TEXT)
            box.currentIndexChanged.connect(self.changed)
            self.class_boxes[label] = box
            flags.addWidget(box)
        form.addRow('整段类别判断', flags)
        self.reviewer = QLineEdit()
        self.reviewer.setPlaceholderText('填写代号即可，不需学校或真实姓名')
        self.reviewer.textChanged.connect(self.changed)
        form.addRow('审查人代号', self.reviewer)
        group_row = QHBoxLayout()
        self.group = QLineEdit()
        self.group.setPlaceholderText('比较场地/来源后填写；未知可留空')
        self.group.textChanged.connect(self.changed)
        self.group_verified = QCheckBox('同源关系已核查')
        self.group_verified.toggled.connect(self.changed)
        group_row.addWidget(self.group, 1)
        group_row.addWidget(self.group_verified)
        form.addRow('同源组', group_row)
        self.reviewed = QCheckBox('已完整观看并标注；没有用模型预测代替人工判断')
        self.reviewed.toggled.connect(self.changed)
        form.addRow('完成状态', self.reviewed)
        body.addLayout(form)
        boundaries = QHBoxLayout()
        self.label = QComboBox()
        self.label.addItems(['fire / 火焰', 'smoke / 烟雾'])
        boundaries.addWidget(self.label)
        self.start_frame = QSpinBox()
        self.end_frame = QSpinBox()
        boundaries.addWidget(QLabel('起始帧'))
        boundaries.addWidget(self.start_frame)
        start_button = QPushButton('取当前帧')
        start_button.clicked.connect(lambda: self.start_frame.setValue(self.frame_index))
        boundaries.addWidget(start_button)
        boundaries.addWidget(QLabel('结束帧（不含）'))
        boundaries.addWidget(self.end_frame)
        end_button = QPushButton('取当前帧之后')
        end_button.clicked.connect(lambda: self.end_frame.setValue(self.frame_index + 1))
        boundaries.addWidget(end_button)
        add_button = QPushButton('添加区间')
        add_button.clicked.connect(self.add_interval)
        boundaries.addWidget(add_button)
        body.addLayout(boundaries)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['类别', '起始帧', '结束帧（不含）', '时间区间（秒）'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMaximumHeight(145)
        self.table.horizontalHeader().setStretchLastSection(True)
        body.addWidget(self.table)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText('场景、难点、不可判断的原因、第三方水印等。不要填写危险等级。')
        self.notes.setMaximumHeight(70)
        self.notes.textChanged.connect(self.changed)
        body.addWidget(self.notes)
        actions = QHBoxLayout()
        remove = QPushButton('删除选中区间')
        remove.clicked.connect(self.remove_interval)
        actions.addWidget(remove)
        save = QPushButton('保存标注')
        save.clicked.connect(self.save)
        actions.addWidget(save)
        self.message = QLabel('标注仅存本地；切换视频和关闭时保存有效修改。')
        actions.addWidget(self.message, 1)
        body.addLayout(actions)
        split.setSizes([250, 1010])
        for row in self.document['videos']:
            self.files.addItem(self.list_text(row))
        self.files.currentRowChanged.connect(self.select_video)
        initial = next((i for i, r in enumerate(self.document['videos']) if r['filename'].endswith('posVideo1.868.avi')), 0)
        self.files.setCurrentRow(initial)

    @staticmethod
    def list_text(row):
        status = '异常暂缓' if row['quality'].startswith('quarantined') else '已审查' if row['full_video_reviewed'] else '待审查'
        seen = ' · 已开发' if row['development_seen'] else ''
        return f'{Path(row["filename"]).name}\n{status}{seen}'

    def changed(self, *args):
        if not self.loading:
            self.dirty = True
            self.message.setText('有未保存修改。')

    def pause(self):
        self.timer.stop()
        self.play_button.setText('播放')

    def select_video(self, index):
        if index < 0:
            return
        self.pause()
        if self.dirty and not self.save():
            self.files.blockSignals(True)
            self.files.setCurrentRow(self.selected)
            self.files.blockSignals(False)
            return
        if self.capture is not None:
            self.capture.release()
        self.selected = index
        self.row = copy.deepcopy(self.document['videos'][index])
        self.loading = True
        try:
            self.intervals = copy.deepcopy(self.row['intervals'])
            for label, box in self.class_boxes.items():
                box.setCurrentIndex(STATES.index(self.row['class_status'][label]))
            self.reviewer.setText(self.row['reviewer'])
            self.group.setText(self.row['source_group'])
            self.group_verified.setChecked(self.row['group_verified'])
            self.reviewed.setChecked(self.row['full_video_reviewed'])
            self.reviewed.setEnabled(not self.row['quality'].startswith('quarantined'))
            self.notes.setPlainText(self.row['notes'])
            count = self.row['decoded_frames']
            self.slider.setRange(0, count - 1)
            self.start_frame.setRange(0, count - 1)
            self.end_frame.setRange(1, count)
            self.start_frame.setValue(0)
            self.end_frame.setValue(1)
            self.fill_table()
        finally:
            self.loading = False
        self.dirty = False
        source = (ROOT / 'videos' / self.row['filename']).resolve()
        if (ROOT / 'videos').resolve() not in source.parents:
            raise ValueError('素材路径越界。')
        sha = hashlib.sha256()
        with source.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                sha.update(block)
        if sha.hexdigest() != self.row['source_sha256']:
            self.capture = None
            self.view.clear_frame('源视频已改变，停止标注；请重新核查素材。')
            return
        self.capture = cv2.VideoCapture(str(source))
        extra = '【帧数异常：仅可记草稿，暂缓正式评价】' if self.row['quality'].startswith('quarantined') else ''
        role = '已用于开发' if self.row['development_seen'] else '尚未分配用途'
        self.info.setText(f'{self.row["filename"]}\n{role} · {count} 帧 · {self.row["fps"]:.3f} FPS {extra}')
        self.message.setText('保存有效修改后再切换。时间采用帧号 / FPS。')
        self.seek(0)

    def seek(self, index):
        self.pause()
        if self.capture is None:
            return
        index = max(0, min(int(index), self.row['decoded_frames'] - 1))
        if not self.capture.set(cv2.CAP_PROP_POS_FRAMES, index):
            self.message.setText('解码器不支持定位，当前帧未改变。')
            return
        ok, frame = self.capture.read()
        if ok:
            actual = round(self.capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            if actual != index:
                self.message.setText(f'定位未到指定帧：请求 {index}，解码器返回 {actual}；请核查。')
            self.display(frame, actual)
        else:
            self.message.setText('该位置无法解码，当前帧未改变。')

    def display(self, frame, index):
        self.frame_index = index
        self.view.set_bgr(frame)
        self.slider.setValue(index)
        self.clock.setText(f'帧 {index} / {self.row["decoded_frames"] - 1} · {index / self.row["fps"]:.3f} 秒'
                           f'（区间最晚结束：{self.row["decoded_frames"] / self.row["fps"]:.3f} 秒）')

    def step(self, amount):
        delta = amount if abs(amount) == 1 else round(self.row['fps']) * (1 if amount > 0 else -1)
        self.seek(self.frame_index + delta)

    def update_speed(self):
        if self.selected >= 0:
            speed = (0.5, 1, 2)[self.speed.currentIndex()]
            self.timer.setInterval(max(1, round(1000 / self.row['fps'] / speed)))

    def toggle_play(self):
        if self.timer.isActive():
            self.pause()
        elif self.capture is not None:
            if self.frame_index == self.row['decoded_frames'] - 1:
                self.seek(0)
            self.update_speed()
            self.timer.start()
            self.play_button.setText('暂停')

    def advance(self):
        if self.capture is None or self.frame_index >= self.row['decoded_frames'] - 1:
            self.pause()
            return
        ok, frame = self.capture.read()
        if not ok:
            self.pause()
            self.message.setText('读取结束或失败，请核对预期帧数。')
            return
        self.display(frame, self.frame_index + 1)

    def fill_table(self):
        self.table.setRowCount(len(self.intervals))
        for index, interval in enumerate(self.intervals):
            start, end = interval['start_frame'], interval['end_frame_exclusive']
            for column, value in enumerate([interval['label'], start, end, f'{start / self.row["fps"]:.3f}–{end / self.row["fps"]:.3f}']):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))

    def add_interval(self):
        label = ('fire', 'smoke')[self.label.currentIndex()]
        start, end = self.start_frame.value(), self.end_frame.value()
        candidate = self.form_row()
        candidate['intervals'].append({'label': label, 'start_frame': start, 'end_frame_exclusive': end})
        candidate['class_status'][label] = 'present'
        candidate['full_video_reviewed'] = False
        try:
            replace_video(self.document, candidate)
        except ValueError as exc:
            QMessageBox.warning(self, '区间未添加', str(exc))
            return
        self.intervals = candidate['intervals']
        self.class_boxes[label].setCurrentIndex(STATES.index('present'))
        self.reviewed.setChecked(False)
        self.fill_table()
        self.changed()

    def remove_interval(self):
        index = self.table.currentRow()
        if index >= 0:
            self.intervals.pop(index)
            self.reviewed.setChecked(False)
            self.fill_table()
            self.changed()

    def form_row(self):
        row = copy.deepcopy(self.row)
        row.update({'reviewer': self.reviewer.text().strip(), 'source_group': self.group.text().strip(),
                    'group_verified': self.group_verified.isChecked(),
                    'full_video_reviewed': self.reviewed.isChecked(),
                    'class_status': {label: STATES[box.currentIndex()] for label, box in self.class_boxes.items()},
                    'intervals': copy.deepcopy(self.intervals), 'notes': self.notes.toPlainText().strip()})
        return row

    def save(self):
        if self.selected < 0 or not self.dirty:
            return True
        self.pause()
        try:
            updated = replace_video(self.document, self.form_row())
            revision = save_review(self.path, updated, self.revision)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, '未保存', str(exc))
            return False
        self.document, self.revision = updated, revision
        self.row = copy.deepcopy(updated['videos'][self.selected])
        self.files.item(self.selected).setText(self.list_text(self.row))
        self.dirty = False
        self.message.setText('标注已保存。原视频未修改；测试划分尚未冻结。')
        return True

    def closeEvent(self, event):
        self.pause()
        if not self.save():
            event.ignore()
            return
        if self.capture is not None:
            self.capture.release()
        event.accept()


def main():
    if not REVIEW_PATH.exists():
        from experiments.prepare_firesense_review import main as prepare
        prepare()
    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    window = ReviewWindow()
    screen = app.primaryScreen()
    if screen:
        available = screen.availableGeometry()
        window.resize(max(1020, min(1260, available.width() - 60)), max(740, min(840, available.height() - 60)))
    window.show()
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
