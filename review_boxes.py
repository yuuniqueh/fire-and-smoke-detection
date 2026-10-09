"""Draw human fire/smoke boxes on raw pilot images; does not load a detector."""
import copy
import json
import sys
from pathlib import Path

from core.runtime import ROOT, configure_runtime
configure_runtime()
from core.box_review import STATES, digest_bytes, load, save, training_eligible
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QImage, QKeySequence, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QScrollArea,
    QSplitter, QTextEdit, QVBoxLayout, QWidget)

PILOT = ROOT / 'data/firesense/box-review-pilot-v1'
STATE_TEXT = ('未判断', '无', '有', '不确定')


class BoxCanvas(QWidget):
    boxAdded = Signal(list)
    boxSelected = Signal(int)

    def __init__(self):
        super().__init__()
        self.image = None
        self.boxes = []
        self.zoom = 1.0
        self.selected = -1
        self.start = self.end = None
        self.label = 'fire'
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def set_image(self, image):
        self.image = image
        self.start = self.end = None
        self.set_zoom(self.zoom)

    def set_zoom(self, zoom):
        self.zoom = zoom
        if self.image is not None:
            self.setFixedSize(round(self.image.width() * zoom), round(self.image.height() * zoom))
        self.update()

    def point(self, event):
        return QPointF(max(0, min(self.image.width(), event.position().x() / self.zoom)),
                       max(0, min(self.image.height(), event.position().y() / self.zoom)))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.image is not None:
            self.start = self.end = self.point(event)
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = self.point(event)
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        end = self.point(event)
        x1, x2 = sorted((round(self.start.x(), 2), round(end.x(), 2)))
        y1, y2 = sorted((round(self.start.y(), 2), round(end.y(), 2)))
        self.start = self.end = None
        if x2 - x1 >= 1 and y2 - y1 >= 1:
            self.boxAdded.emit([x1, y1, x2, y2])
        else:
            selected = -1
            for index in range(len(self.boxes) - 1, -1, -1):
                a, b, c, d = self.boxes[index]['xyxy']
                if a <= end.x() <= c and b <= end.y() <= d:
                    selected = index
                    break
            self.boxSelected.emit(selected)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if self.image is None:
            return
        painter.drawImage(QRectF(0, 0, self.width(), self.height()), self.image)
        for index, box in enumerate(self.boxes):
            color = QColor('#ff6b45' if box['label'] == 'fire' else '#32c4e2')
            painter.setPen(QPen(color, 3 if index == self.selected else 2))
            x1, y1, x2, y2 = [v * self.zoom for v in box['xyxy']]
            painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))
            text = f'{index + 1} ' + ('火焰' if box['label'] == 'fire' else '烟雾')
            ty = max(16, y1 - 4)
            painter.fillRect(QRectF(x1, ty - 16, 75, 20), QColor('#141c28'))
            painter.drawText(QPointF(x1 + 3, ty), text)
        if self.start is not None:
            painter.setPen(QPen(QColor('#ffcc55'), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(self.start * self.zoom, self.end * self.zoom).normalized())


class BoxReviewWindow(QMainWindow):
    def __init__(self, pilot=PILOT, annotation_path=None):
        super().__init__()
        self.pilot = Path(pilot)
        raw = (self.pilot / 'manifest.json').read_bytes()
        self.manifest = json.loads(raw.decode('utf-8'))
        self.manifest_sha = digest_bytes(raw)
        self.path = Path(annotation_path) if annotation_path else self.pilot / 'annotations.json'
        self.document, self.revision = load(self.path, self.manifest, self.manifest_sha)
        self.selected = -1
        self.loading = False
        self.dirty = False
        self.setWindowTitle('焰鉴 · 火焰 / 烟雾框标注（27 张试点原图）')
        self.resize(1240, 860)
        self.setMinimumSize(1000, 720)
        outer = QVBoxLayout()
        container = QWidget()
        container.setLayout(outer)
        self.setCentralWidget(container)
        title = QLabel('选类别 → 鼠标拖出矩形 → 分别判断火焰和烟雾 → 勾选完成 → 保存并下一张')
        title.setStyleSheet('font-size:17px;font-weight:bold;padding:6px;')
        outer.addWidget(title)
        split = QSplitter()
        outer.addWidget(split, 1)
        self.files = QListWidget()
        self.files.setMinimumWidth(190)
        self.files.setMaximumWidth(260)
        split.addWidget(self.files)
        right = QWidget()
        body = QVBoxLayout(right)
        split.addWidget(right)
        self.info = QLabel()
        self.info.setWordWrap(True)
        body.addWidget(self.info)
        tools = QHBoxLayout()
        tools.addWidget(QLabel('画框类别'))
        self.label = QComboBox()
        self.label.addItems(['fire / 火焰', 'smoke / 烟雾'])
        self.label.currentIndexChanged.connect(lambda i: setattr(self.canvas, 'label', ('fire', 'smoke')[i]))
        tools.addWidget(self.label)
        self.zoom = QComboBox()
        self.zoom.addItems(['适应画面', '1 倍', '2 倍', '4 倍'])
        self.zoom.currentIndexChanged.connect(self.apply_zoom)
        tools.addWidget(self.zoom)
        delete = QPushButton('删除选中框')
        delete.clicked.connect(self.delete_box)
        tools.addWidget(delete)
        undo = QPushButton('撤销最后一个框')
        undo.clicked.connect(self.undo_box)
        tools.addWidget(undo)
        body.addLayout(tools)
        self.scroll = QScrollArea()
        self.scroll.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.scroll.setStyleSheet('QScrollArea{background:#111923;}')
        self.canvas = BoxCanvas()
        self.canvas.boxAdded.connect(self.add_box)
        self.canvas.boxSelected.connect(self.select_box)
        self.scroll.setWidget(self.canvas)
        body.addWidget(self.scroll, 1)
        self.box_list = QListWidget()
        self.box_list.setMaximumHeight(95)
        self.box_list.currentRowChanged.connect(self.select_box)
        body.addWidget(self.box_list)
        flags = QHBoxLayout()
        self.class_boxes = {}
        for label, text in [('fire', '火焰'), ('smoke', '烟雾')]:
            flags.addWidget(QLabel(text))
            box = QComboBox()
            box.addItems(STATE_TEXT)
            box.currentIndexChanged.connect(self.changed)
            self.class_boxes[label] = box
            flags.addWidget(box)
        flags.addWidget(QLabel('审查人代号'))
        self.reviewer = QLineEdit()
        self.reviewer.setPlaceholderText('例如 R1')
        self.reviewer.textChanged.connect(self.changed)
        flags.addWidget(self.reviewer)
        body.addLayout(flags)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText('不确定时写原因。只框可直接看见的目标，不依据燃烧常识推断。')
        self.notes.setMaximumHeight(65)
        self.notes.textChanged.connect(self.changed)
        body.addWidget(self.notes)
        self.completed = QCheckBox('已检查整张图，两类均已判断，并框出所有可见目标')
        self.completed.toggled.connect(self.completion_changed)
        body.addWidget(self.completed)
        buttons = QHBoxLayout()
        for text, callback in [('上一张', lambda: self.move(-1)), ('保存（Ctrl+S）', self.save_current),
                               ('保存并下一张', lambda: self.move(1))]:
            button = QPushButton(text)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        body.addLayout(buttons)
        self.status = QLabel('红框：火焰；蓝框：烟雾。点击框或列表可选中，框画错可删除重画。')
        self.status.setWordWrap(True)
        outer.addWidget(self.status)
        action = QAction(self)
        action.setShortcut(QKeySequence.StandardKey.Save)
        action.triggered.connect(self.save_current)
        self.addAction(action)
        self.refresh_files()
        self.files.currentRowChanged.connect(self.choose)
        self.files.setCurrentRow(0)

    def refresh_files(self):
        current = self.files.currentRow()
        self.files.blockSignals(True)
        self.files.clear()
        for i, row in enumerate(self.document['images']):
            state = '待标注'
            if row['completed']:
                state = '完成 · 暂不训练' if not training_eligible(row) else '完成'
            self.files.addItem(f'{i + 1:02d} {Path(row["image"]).name}\n{state}')
        self.files.setCurrentRow(current)
        self.files.blockSignals(False)

    def changed(self, *args):
        if self.loading:
            return
        self.dirty = True
        self.completed.blockSignals(True)
        self.completed.setChecked(False)
        self.completed.blockSignals(False)

    def completion_changed(self, *args):
        if not self.loading:
            self.dirty = True

    def apply_zoom(self, *args):
        if self.canvas.image is None:
            return
        if self.zoom.currentIndex() == 0:
            scale = min(max(1, self.scroll.viewport().width() - 8) / self.canvas.image.width(),
                        max(1, self.scroll.viewport().height() - 8) / self.canvas.image.height(), 4)
        else:
            scale = (1, 2, 4)[self.zoom.currentIndex() - 1]
        self.canvas.set_zoom(scale)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'canvas'):
            self.apply_zoom()

    def choose(self, index):
        if index < 0 or index == self.selected:
            return
        if self.selected >= 0 and not self.save_current():
            self.files.blockSignals(True)
            self.files.setCurrentRow(self.selected)
            self.files.blockSignals(False)
            return
        row = self.document['images'][index]
        path = (self.pilot / row['image']).resolve()
        try:
            if self.pilot.resolve() not in path.parents or digest_bytes(path.read_bytes()) != row['image_sha256']:
                raise ValueError('原图校验失败。')
            image = QImage(str(path))
            if image.isNull() or image.width() != row['width'] or image.height() != row['height']:
                raise ValueError('原图无法读取或尺寸改变。')
        except Exception as exc:
            QMessageBox.warning(self, '原图不可用', str(exc))
            self.files.blockSignals(True)
            self.files.setCurrentRow(self.selected)
            self.files.blockSignals(False)
            return
        self.selected = index
        self.loading = True
        self.boxes = copy.deepcopy(row['boxes'])
        self.canvas.set_image(image)
        for label, box in self.class_boxes.items():
            box.setCurrentIndex(STATES.index(row['class_status'][label]))
        previous_reviewer = self.reviewer.text()
        self.reviewer.setText(row['reviewer_id'] or previous_reviewer)
        self.notes.setPlainText(row['notes'])
        self.completed.setChecked(row['completed'])
        self.info.setText(f'{index + 1} / {len(self.document["images"])} · {row["image"]}\n'
                          f'原视频：{row["source"]} · 帧 {row["frame_index"]} · {row["timestamp_s"]:.3f} 秒')
        self.refresh_boxes()
        self.apply_zoom()
        self.loading = False
        self.dirty = False

    def refresh_boxes(self):
        self.box_list.blockSignals(True)
        self.box_list.clear()
        for i, box in enumerate(self.boxes):
            self.box_list.addItem(f'{i + 1} · {"火焰" if box["label"] == "fire" else "烟雾"} · {box["xyxy"]}')
        self.box_list.blockSignals(False)
        self.canvas.boxes = self.boxes
        self.canvas.selected = -1
        self.canvas.update()

    def select_box(self, index):
        self.canvas.selected = index
        if self.box_list.currentRow() != index:
            self.box_list.setCurrentRow(index)
        self.canvas.update()

    def add_box(self, coords):
        label = self.canvas.label
        self.boxes.append({'label': label, 'xyxy': coords})
        self.class_boxes[label].setCurrentIndex(STATES.index('present'))
        self.changed()
        self.refresh_boxes()
        self.select_box(len(self.boxes) - 1)

    def delete_box(self):
        index = self.box_list.currentRow()
        if index >= 0:
            label = self.boxes.pop(index)['label']
            if not any(box['label'] == label for box in self.boxes):
                self.class_boxes[label].setCurrentIndex(0)
            self.changed()
            self.refresh_boxes()

    def undo_box(self):
        if self.boxes:
            self.select_box(len(self.boxes) - 1)
            self.delete_box()

    def save_current(self, *args):
        if self.selected < 0 or not self.dirty:
            return True
        proposed = copy.deepcopy(self.document)
        row = proposed['images'][self.selected]
        row.update({'boxes': copy.deepcopy(self.boxes), 'reviewer_id': self.reviewer.text().strip(),
                    'class_status': {label: STATES[box.currentIndex()] for label, box in self.class_boxes.items()},
                    'notes': self.notes.toPlainText().strip(), 'completed': self.completed.isChecked()})
        try:
            revision = save(self.path, proposed, self.manifest, self.manifest_sha, self.revision)
        except Exception as exc:
            QMessageBox.warning(self, '尚未保存', str(exc))
            return False
        self.document, self.revision = proposed, revision
        self.dirty = False
        self.refresh_files()
        count = sum(r['completed'] for r in self.document['images'])
        self.status.setText(f'已保存 · 完成 {count}/{len(self.document["images"])} · {self.path}')
        return True

    def move(self, step):
        if self.save_current():
            index = max(0, min(len(self.document['images']) - 1, self.selected + step))
            self.files.setCurrentRow(index)

    def closeEvent(self, event):
        if self.save_current():
            event.accept()
        else:
            # Saving failure should never discard the unsaved record silently.
            answer = QMessageBox.question(self, '保留未保存修改',
                '本窗口的修改尚未保存。仍要退出并放弃这些未保存修改吗？',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            event.accept() if answer == QMessageBox.StandardButton.Yes else event.ignore()


def main():
    app = QApplication(sys.argv)
    app.setFont(QFont('Microsoft YaHei UI', 10))
    try:
        window = BoxReviewWindow()
    except Exception as exc:
        QMessageBox.critical(None, '无法打开框标注', str(exc))
        return 1
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
