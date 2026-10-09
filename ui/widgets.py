"""Lightweight Qt widgets: keep native fonts, aspect ratio and a bounded curve."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget


class VideoView(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet('background:#0c1320; border:1px solid #26344c; border-radius:10px; color:#899bb9;')
        self.image = None
        self.clear_frame()

    def clear_frame(self, message='打开视频，开始分析\n画面与检测结果会显示在这里'):
        self.image = None
        self.clear()
        self.setText(message)

    def set_bgr(self, frame):
        height, width = frame.shape[:2]
        self.image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self._render()

    def set_file(self, path):
        image = QImage(str(path))
        if image.isNull():
            self.clear_frame('截图不可读取；可打开结果目录查看输出视频。')
        else:
            self.image = image
            self._render()

    def _render(self):
        if self.image is not None:
            self.setPixmap(QPixmap.fromImage(self.image).scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                              Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()


class RiskCurve(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.points = []
        self.thresholds = (45.0, 70.0)

    def set_points(self, points, thresholds=None):
        self.points = points
        if thresholds:
            self.thresholds = thresholds
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#101a2b'))
        chart = QRectF(38, 12, max(1, self.width() - 60), max(1, self.height() - 43))
        last_time = self.points[-1][0] if self.points else 0
        first_time = max(0, last_time - 120)
        span = max(10, last_time - first_time)
        painter.setPen(QColor('#798aa8'))
        for score in (0, 50, 100):
            y = chart.bottom() - score / 100 * chart.height()
            painter.drawText(QRectF(0, y - 8, 30, 18), Qt.AlignmentFlag.AlignRight, str(score))
            painter.setPen(QPen(QColor('#26344c'), 1))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QColor('#798aa8'))
        for score, color in zip(self.thresholds, ('#d9a441', '#dd6878')):
            y = chart.bottom() - score / 100 * chart.height()
            painter.setPen(QPen(QColor(color), 1, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
        path = QPainterPath()
        continuing = False
        for timestamp, score in self.points:
            if score is None:
                continuing = False
                continue
            point = QPointF(chart.left() + (timestamp - first_time) / span * chart.width(),
                            chart.bottom() - score / 100 * chart.height())
            if continuing:
                path.lineTo(point)
            else:
                path.moveTo(point)
            continuing = True
        painter.setPen(QPen(QColor('#55d8c3'), 2))
        painter.drawPath(path)
        painter.setPen(QColor('#a1afc7'))
        painter.drawText(QRectF(chart.left(), chart.bottom() + 8, chart.width(), 20),
                         Qt.AlignmentFlag.AlignLeft, f'{first_time:.1f}s')
        painter.drawText(QRectF(chart.left(), chart.bottom() + 8, chart.width(), 20),
                         Qt.AlignmentFlag.AlignRight, f'{first_time + span:.1f}s · 视频时间')
        if not any(score is not None for _, score in self.points):
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, '尚无已确认事件的风险曲线')
