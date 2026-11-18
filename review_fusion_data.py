"""Review original scene-fusion evidence; never infer training labels automatically."""
import argparse
import math
from pathlib import Path
import sys

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (QApplication, QFrame, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QMainWindow, QPushButton, QSizePolicy, QSplitter, QTextEdit,
    QVBoxLayout, QWidget)

from core.fusion_dataset import load_dataset, save_annotation
from ui.product import STYLE, ui_font


LABELS = {
    'normal': ('正常用火或干扰', '#19764b', '#e7f6ee'),
    'abnormal': ('异常烟火线索', '#b82f48', '#ffedf1'),
    'uncertain': ('证据不足', '#a45b10', '#fff1dd'),
}
STATUS_NAMES = {'pending': '等待另一位审核人独立判断', 'agreed': '两位审核人意见一致',
    'disagreement': '审核存在分歧，暂不用于训练', 'uncertain': '证据不足，暂不用于训练',
    'excluded': '演示或排除样本，不用于训练'}


def frame_time(value):
    """Keep subsecond source time visible; adjacent frames must not look identical."""
    milliseconds = max(0, math.floor(float(value or 0) * 1000. + 1e-6))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    prefix = f'{hours:02d}:' if hours else ''
    return f'{prefix}{minutes:02d}:{seconds:02d}.{ms:03d}'


def current_source_frames(sample):
    """Decode at most three local original frames, never after the decision time.

    FPS-based frame indices are floored. A backend returning a different frame
    position, or a later reported presentation time, is rejected. Frames remain
    in memory and are reduced to at most 1280x960; no video is written or changed.
    """
    source = Path(str(sample.get('source', ''))).expanduser()
    timestamp = sample.get('timestamp_s')
    if (isinstance(timestamp, bool) or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp) or timestamp < 0 or not source.is_file()):
        return [], '原录像或待判断时刻不可用。'
    import cv2
    capture = None
    try:
        capture = cv2.VideoCapture(str(source.resolve()))
        if not capture.isOpened():
            return [], '无法打开原录像。'
        fps = capture.get(cv2.CAP_PROP_FPS)
        count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        width = capture.get(cv2.CAP_PROP_FRAME_WIDTH)
        height = capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if (not all(math.isfinite(v) for v in (fps, count, width, height))
                or not 0 < fps <= 240 or count < 1 or width < 1 or height < 1
                or width > 4096 or height > 4096 or width * height > 8_388_608):
            return [], '原录像尺寸或时间信息不适合本次审核。'
        indices = sorted({min(int(count) - 1, math.floor(max(0., timestamp - offset) * fps))
                          for offset in (1., .5, 0.)})
        result = []
        for index in indices:
            if not capture.set(cv2.CAP_PROP_POS_FRAMES, index):
                continue
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            position = capture.get(cv2.CAP_PROP_POS_FRAMES)
            reported_ms = capture.get(cv2.CAP_PROP_POS_MSEC)
            if not math.isfinite(position) or abs(position - (index + 1)) > .01:
                continue
            expected_time = index / fps
            # Some decoders report zero at every frame. Use the confirmed frame
            # position in that case; reject any positive PTS beyond this decision.
            if expected_time > timestamp or (math.isfinite(reported_ms)
                    and reported_ms > 0 and reported_ms / 1000. > timestamp + 1e-6):
                continue
            if (len(frame.shape) != 3 or frame.shape[2] != 3 or str(frame.dtype) != 'uint8'
                    or frame.shape[0] < 1 or frame.shape[1] < 1
                    or frame.shape[0] * frame.shape[1] > 8_388_608):
                continue
            scale = min(1., 1280. / frame.shape[1], 960. / frame.shape[0])
            if scale < 1:
                frame = cv2.resize(frame, (max(1, math.floor(frame.shape[1] * scale)),
                    max(1, math.floor(frame.shape[0] * scale))), interpolation=cv2.INTER_AREA)
            result.append({'frame': frame, 'frame_index': index, 'timestamp_s': expected_time,
                           'kind': 'decision_raw', 'verified': True})
        return result, '' if result else '未能读取待审核时刻之前的原画面。'
    except (OSError, ValueError, TypeError, cv2.error) as exc:
        return [], '原录像读取失败：' + str(exc)[:120]
    finally:
        if capture is not None:
            capture.release()


def _label(text='', name=None):
    value = QLabel(text)
    value.setWordWrap(True)
    value.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        value.setObjectName(name)
    return value


class EvidenceView(QLabel):
    def __init__(self):
        super().__init__()
        self.original = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet('background:#14243b;color:#e0e9f5;border-radius:14px;padding:6px;')

    def set_evidence(self, path):
        self.original = QPixmap()
        if path:
            reader = QImageReader(str(path))
            size = reader.size()
            if size.isValid() and size.width() * size.height() <= 8_388_608:
                scale = min(1., 1280. / size.width(), 960. / size.height())
                if scale < 1:
                    reader.setScaledSize(QSize(max(1, math.floor(size.width() * scale)),
                        max(1, math.floor(size.height() * scale))))
                self.original = QPixmap.fromImage(reader.read())
        self._fit()
        return not self.original.isNull()

    def set_frame(self, frame):
        height, width = frame.shape[:2]
        value = QImage(frame.data, width, height, int(frame.strides[0]),
                       QImage.Format.Format_BGR888).copy()
        self.original = QPixmap.fromImage(value)
        self._fit()
        return not self.original.isNull()

    def _fit(self):
        if self.original.isNull():
            self.clear()
            self.setText('原画面暂不可用\n请回看原录像，或选择“证据不足”')
        else:
            self.setPixmap(self.original.scaled(QSize(max(1, self.width() - 12), max(1, self.height() - 12)),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()


class FusionReviewWindow(QMainWindow):
    def __init__(self, dataset_path=None):
        super().__init__()
        self.dataset_path = None
        self.dataset = {'samples': [], 'annotation_summary': {}}
        self.index = 0
        self.image_index = 0
        self.images = []
        self.has_image = False
        self.current_frames = False
        self.setWindowTitle('焰鉴 · 融合样本审核')
        self.resize(1120, 820)
        self.setMinimumSize(920, 680)
        self.setFont(ui_font())
        self.setStyleSheet(STYLE)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        heading = QHBoxLayout()
        heading.addWidget(_label('融合样本审核', 'pageTitle'))
        heading.addStretch()
        self.progress = _label('尚未打开样本集', 'statusBadge')
        heading.addWidget(self.progress)
        layout.addLayout(heading)

        open_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText('选择待审核的样本集文件夹')
        self.path_edit.returnPressed.connect(lambda: self.open_dataset(self.path_edit.text()))
        browse = QPushButton('选择样本集')
        browse.clicked.connect(self.choose_dataset)
        open_row.addWidget(self.path_edit, 1)
        open_row.addWidget(browse)
        layout.addLayout(open_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        evidence_card = QFrame()
        evidence_card.setObjectName('card')
        evidence_layout = QVBoxLayout(evidence_card)
        evidence_layout.setContentsMargins(18, 16, 18, 16)
        evidence_layout.setSpacing(12)
        self.sample_title = _label('原始画面', 'sectionTitle')
        evidence_layout.addWidget(self.sample_title)
        self.source_label = _label('请先打开样本集。', 'muted')
        self.source_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        evidence_layout.addWidget(self.source_label)
        self.evidence_notice = _label('', 'alertBanner')
        self.evidence_notice.hide()
        evidence_layout.addWidget(self.evidence_notice)
        self.image = EvidenceView()
        evidence_layout.addWidget(self.image, 1)
        frame_row = QHBoxLayout()
        self.frame_previous = QPushButton('上一帧')
        self.frame_previous.clicked.connect(lambda: self.move_frame(-1))
        self.frame_next = QPushButton('下一帧')
        self.frame_next.clicked.connect(lambda: self.move_frame(1))
        self.frame_info = _label('暂无可核验画面')
        self.frame_info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_row.addWidget(self.frame_previous)
        frame_row.addWidget(self.frame_info, 1)
        frame_row.addWidget(self.frame_next)
        evidence_layout.addLayout(frame_row)
        self.open_video = QPushButton('打开原录像')
        self.open_video.clicked.connect(self.open_original_video)
        evidence_layout.addWidget(self.open_video)
        self.ai_toggle = QPushButton('查看AI画面描述（模型意见）')
        self.ai_toggle.setCheckable(True)
        self.ai_toggle.toggled.connect(self.toggle_description)
        evidence_layout.addWidget(self.ai_toggle)
        self.description = QTextEdit()
        self.description.setReadOnly(True)
        self.description.setMaximumHeight(150)
        self.description.setPlaceholderText('该片段没有完整AI描述。')
        self.description.hide()
        evidence_layout.addWidget(self.description)
        splitter.addWidget(evidence_card)

        review_card = QFrame()
        review_card.setObjectName('reviewPanel')
        review_layout = QVBoxLayout(review_card)
        review_layout.setContentsMargins(20, 18, 20, 18)
        review_layout.setSpacing(14)
        review_layout.addWidget(_label('你的独立判断', 'sectionTitle'))
        review_layout.addWidget(_label('请依据原画面判断，无法确定时选择“证据不足”。'))
        self.reviewer = QLineEdit()
        self.reviewer.setMaxLength(100)
        self.reviewer.setPlaceholderText('填写审核人姓名或固定代号')
        self.reviewer.textChanged.connect(self.refresh_reviewer)
        review_layout.addWidget(self.reviewer)
        self.buttons = {}
        for key, (title, color, background) in LABELS.items():
            button = QPushButton(title)
            button.setMinimumHeight(48)
            button.setStyleSheet(f'QPushButton {{color:{color};background:{background};'
                f'border:1px solid {color};font-size:18px;font-weight:600;border-radius:12px;}}'
                f'QPushButton:hover {{border:2px solid {color};}}'
                'QPushButton:disabled {color:#8797ac;background:#f0f3f8;border-color:#d8e1ed;}')
            button.clicked.connect(lambda checked=False, label=key: self.save_label(label))
            self.buttons[key] = button
            review_layout.addWidget(button)
        review_layout.addWidget(_label('备注（可选）'))
        self.note = QTextEdit()
        self.note.setMaximumHeight(110)
        self.note.setPlaceholderText('记录判断依据或无法确定的原因')
        review_layout.addWidget(self.note)
        self.own_review = _label('尚未提交判断', 'statePill')
        review_layout.addWidget(self.own_review)
        self.review_status = _label('需要两位不同审核人独立判断一致，样本才可用于训练。', 'muted')
        review_layout.addWidget(self.review_status)
        review_layout.addStretch()
        review_layout.addWidget(_label('点击结论即保存；修改备注后可再次点击结论。', 'muted'))
        splitter.addWidget(review_card)
        splitter.setSizes([700, 370])
        layout.addWidget(splitter, 1)

        navigation = QHBoxLayout()
        self.message = _label('', 'muted')
        navigation.addWidget(self.message, 1)
        self.previous = QPushButton('上一条')
        self.previous.clicked.connect(lambda: self.move_sample(-1))
        self.next_unreviewed = QPushButton('下一条未审核')
        self.next_unreviewed.setObjectName('primary')
        self.next_unreviewed.clicked.connect(self.jump_unreviewed)
        self.next = QPushButton('下一条')
        self.next.clicked.connect(lambda: self.move_sample(1))
        for button in (self.previous, self.next_unreviewed, self.next):
            navigation.addWidget(button)
        layout.addLayout(navigation)
        self.render_sample()
        if dataset_path:
            self.open_dataset(dataset_path)

    @property
    def samples(self):
        return self.dataset.get('samples', [])

    @property
    def current_sample(self):
        return self.samples[self.index] if self.samples else None

    def choose_dataset(self):
        value = QFileDialog.getExistingDirectory(self, '选择融合样本集')
        if value:
            self.open_dataset(value)

    def open_dataset(self, value):
        if not str(value).strip():
            self.message.setText('请选择样本集文件夹。')
            return False
        try:
            dataset = load_dataset(Path(value))
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.message.setText(f'无法打开样本集：{exc}')
            return False
        self.dataset_path = Path(value).resolve()
        self.path_edit.setText(str(self.dataset_path))
        self.dataset = dataset
        self.index = 0
        self.message.setText('请先独立查看原画面，再提交判断。')
        self.render_sample()
        return True

    def render_sample(self):
        sample = self.current_sample
        self.image_index = 0
        self.images, error = current_source_frames(sample) if sample else ([], '')
        self.current_frames = bool(self.images)
        if sample and not self.current_frames:
            # AI frames may be many seconds older. Retain their identity and
            # explicit age rather than presenting them as the current clip.
            timestamp = sample.get('timestamp_s', 0.)
            self.images = [dict(item, kind='ai_evidence')
                for item in sample.get('evidence_images', [])
                if isinstance(item, dict) and isinstance(item.get('timestamp_s'), (int, float))
                and not isinstance(item.get('timestamp_s'), bool)
                and math.isfinite(item['timestamp_s']) and 0 <= item['timestamp_s'] <= timestamp][-3:]
        self.evidence_notice.setVisible(bool(sample and not self.current_frames))
        self.evidence_notice.setText('当前片段未能解码。下方为较早AI取帧，非当前片段；请打开原录像核对。')
        self.evidence_notice.setToolTip(error)
        self.ai_toggle.setChecked(False)
        if sample:
            source = sample.get('source') or sample.get('run_path') or '来源未记录'
            self.sample_title.setText(f'待审核片段 · 第 {self.index + 1} / {len(self.samples)} 条')
            self.source_label.setText(f'{Path(str(source)).name or source}\n'
                f'待判断时刻 {frame_time(sample.get("timestamp_s"))} · '
                f'片段 {frame_time(sample.get("segment_start_s", sample.get("timestamp_s")))} — '
                f'{frame_time(sample.get("segment_end_s", sample.get("timestamp_s")))}')
            self.source_label.setToolTip(str(source))
            record = sample.get('scene_record') or {}
            self.description.setPlainText((record.get('scene') or {}).get('description', ''))
        else:
            self.sample_title.setText('暂无待审核片段')
            self.source_label.setText('请打开样本集；没有候选片段时，先完成视频分析再准备样本。')
            self.description.clear()
        self.previous.setEnabled(bool(sample and self.index > 0))
        self.next.setEnabled(bool(sample and self.index + 1 < len(self.samples)))
        self.next_unreviewed.setEnabled(bool(sample))
        source = Path(str(sample.get('source', ''))) if sample else None
        self.open_video.setEnabled(bool(source and source.is_file()))
        self.ai_toggle.setEnabled(bool(sample and self.description.toPlainText()))
        self.render_frame()
        self.refresh_reviewer()

    def render_frame(self):
        image = self.images[self.image_index] if self.images else None
        if image and image.get('kind') == 'decision_raw':
            self.has_image = self.image.set_frame(image['frame'])
        else:
            path = image.get('path') if image and image.get('verified') is True else None
            self.has_image = self.image.set_evidence(path)
        if image:
            state = '' if self.has_image else ' · 画面不可用'
            origin = '当前片段' if self.current_frames else '较早AI取帧'
            age = max(0., self.current_sample['timestamp_s'] - image['timestamp_s'])
            older = '' if self.current_frames else f' · 早 {age:.1f} 秒'
            self.frame_info.setText(f'{origin} · {self.image_index + 1} / {len(self.images)}\n'
                f'视频 {frame_time(image.get("timestamp_s"))}{older}{state}')
        else:
            self.frame_info.setText('暂无可核验画面')
        self.frame_previous.setEnabled(self.image_index > 0)
        self.frame_next.setEnabled(self.image_index + 1 < len(self.images))

    def refresh_reviewer(self):
        name = self.reviewer.text().strip().casefold()
        summary = self.dataset.get('annotation_summary', {})
        own_count = sum(bool(summary.get(s['sample_id'], {}).get('reviews', {}).get(name))
            for s in self.samples) if name else 0
        self.progress.setText(f'已审核 {own_count} / {len(self.samples)} 条')
        sample = self.current_sample
        row = summary.get(sample['sample_id'], {}) if sample else {}
        own = row.get('reviews', {}).get(name) if name else None
        self.note.setPlainText(own.get('note', '') if own else '')
        self.own_review.setText('你的判断：' + LABELS[own['label']][0] if own and own.get('label') in LABELS
            else '尚未提交判断')
        # Keep the second person's decision independent: no other conclusion is revealed.
        self.review_status.setText(STATUS_NAMES.get(row.get('status'), '等待复核') if own
            else '需要两位不同审核人独立判断一致，样本才可用于训练。')
        for button in self.buttons.values():
            button.setEnabled(bool(sample))

    def save_label(self, label):
        sample = self.current_sample
        name = self.reviewer.text().strip()
        if not sample or not self.dataset_path:
            self.message.setText('请先打开待审核样本集。')
            return False
        if not name:
            self.message.setText('请先填写审核人姓名或固定代号。')
            self.reviewer.setFocus()
            return False
        if label not in LABELS:
            return False
        try:
            save_annotation(self.dataset_path, sample['sample_id'], reviewer=name,
                label=label, note=self.note.toPlainText().strip())
            self.dataset = load_dataset(self.dataset_path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.message.setText(f'未能保存：{exc}')
            return False
        self.refresh_reviewer()
        self.message.setText(f'已保存“{LABELS[label][0]}”。可继续下一条，也可修改本条判断。')
        return True

    def move_sample(self, offset):
        if not self.samples:
            return
        self.index = max(0, min(len(self.samples) - 1, self.index + offset))
        self.message.setText('请依据原画面独立判断。')
        self.render_sample()

    def jump_unreviewed(self):
        name = self.reviewer.text().strip().casefold()
        if not name:
            self.message.setText('请先填写审核人姓名或固定代号。')
            self.reviewer.setFocus()
            return
        summary = self.dataset.get('annotation_summary', {})
        for offset in range(1, len(self.samples) + 1):
            index = (self.index + offset) % len(self.samples)
            if name not in summary.get(self.samples[index]['sample_id'], {}).get('reviews', {}):
                self.index = index
                self.render_sample()
                return
        self.message.setText('你已完成本样本集的全部审核。')

    def move_frame(self, offset):
        if self.images:
            self.image_index = max(0, min(len(self.images) - 1, self.image_index + offset))
            self.render_frame()

    def toggle_description(self, visible):
        self.description.setVisible(visible)
        self.ai_toggle.setText('收起AI画面描述（模型意见）' if visible else '查看AI画面描述（模型意见）')

    def open_original_video(self):
        sample = self.current_sample
        source = Path(str(sample.get('source', ''))) if sample else None
        if source and source.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(source.resolve())))


def main(argv=None):
    parser = argparse.ArgumentParser(description='查看原始画面并人工审核场景融合训练样本')
    parser.add_argument('dataset', nargs='?', help='样本集文件夹或manifest路径')
    args = parser.parse_args(argv)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    window = FusionReviewWindow(args.dataset)
    window.show()
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
