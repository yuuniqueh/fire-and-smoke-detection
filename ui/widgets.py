"""Lightweight Qt widgets: keep native fonts, aspect ratio and a bounded curve."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget
from ui.product import video_time


class VideoView(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet('background:transparent; border:0; color:#8fa4c0;')
        self.image = None
        self.context = QLabel(self)
        self.context.setStyleSheet('background:#223651; color:#d6e2f3; padding:6px 11px; border-radius:10px; font-size:15px;')
        self.context.hide()
        self.clear_frame()

    def clear_frame(self, message='打开视频，开始分析\n画面与检测结果会显示在这里'):
        self.image = None
        self.clear()
        self.setText(message)

    def set_context(self, text, stale=False):
        self.context.setText(text)
        self.context.setStyleSheet(f'background:{"#a24a4a" if stale else "#223651"}; color:#edf3ff; padding:6px 11px; border-radius:10px; font-size:15px;')
        self.context.adjustSize()
        self.context.move(12, 12)
        self.context.setVisible(bool(text))
        self.context.raise_()

    def set_bgr(self, frame, detections=None):
        height, width = frame.shape[:2]
        self.image = QImage(frame.data, width, height, frame.strides[0], QImage.Format.Format_BGR888).copy()
        self._draw_detections(detections)
        self._render()

    def _draw_detections(self, detections):
        if detections:
            width = self.image.width()
            painter = QPainter(self.image)
            font = painter.font()
            font.setPixelSize(max(14, round(width / 65)))
            painter.setFont(font)
            painter.setPen(QPen(QColor('#f4b02e'), max(2, width / 600)))
            for detection in detections:
                x1, y1, x2, y2 = detection['bbox_xyxy']
                painter.drawRect(QRectF(x1, y1, x2 - x1, y2 - y1))
                title = {'fire': '火焰', 'smoke': '烟雾'}.get(detection.get('label'), '检测目标')
                box = painter.fontMetrics().boundingRect(title)
                top = max(0, y1 - box.height() - 10)
                painter.fillRect(QRectF(x1, top, box.width() + 14, box.height() + 10), QColor('#f4b02e'))
                painter.setPen(QColor('#352309'))
                painter.drawText(QPointF(x1 + 7, top + box.height()), title)
                painter.setPen(QPen(QColor('#f4b02e'), max(2, width / 600)))
            painter.end()

    def set_file(self, path, detections=None):
        image = QImage(str(path))
        if image.isNull():
            self.clear_frame('截图不可读取；可打开结果目录查看输出视频。')
        else:
            self.image = image
            self._draw_detections(detections)
            self._render()

    def _render(self):
        self.update()

    def paintEvent(self, event):
        # A stylesheet radius alone does not clip QLabel's video pixmap.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 18, 18)
        painter.setClipPath(clip)
        background = QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QColor('#182b46'))
        background.setColorAt(1, QColor('#101d31'))
        painter.fillRect(bounds, background)
        if self.image is not None:
            scaled = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            target = QRectF((self.width() - scaled.width()) / 2., (self.height() - scaled.height()) / 2.,
                            scaled.width(), scaled.height())
            painter.drawImage(target, self.image)
        else:
            from ui.icons import icon
            center_y = max(40., self.height() / 2. - 30.)
            painter.setBrush(QColor('#243b59'))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(QRectF(self.width() / 2. - 30, center_y - 30, 60, 60), 20, 20)
            painter.drawPixmap(round(self.width() / 2. - 17), round(center_y - 17), icon('camera', '#829cc2', 34).pixmap(34, 34))
            painter.setPen(QColor('#a8b9d1'))
            painter.drawText(QRectF(20, center_y + 45, max(0, self.width() - 40), max(30., self.height() - center_y - 50)),
                             Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, self.text())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._render()


class EventTimeline(QWidget):
    """Clickable event intervals in source time, without implying safe gaps."""
    eventSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(100)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('事件时间轴，左右键选择事件，回车查看')
        self.events, self.hits = [], []
        self.end_s = 0
        self.focused_event = 0
        self.setToolTip('点击色块查看事件；空白区间不代表现场安全。')

    def set_events(self, events, end_s=None):
        self.events = sorted(events, key=lambda e: e.get('started_at_s', e.get('confirmed_at_s', 0)))
        self.end_s = max([float(end_s or 0)] + [float(e.get('closed_at_s') or e.get('confirmed_at_s', 0)) for e in self.events])
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        chart = QRectF(20, 16, max(1, self.width() - 40), 22)
        end = max(10, self.end_s)
        start = max(0, end - 300)
        span = end - start
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor('#e7edf7'))
        p.drawRoundedRect(chart, 7, 7)
        self.hits = []
        for index, item in enumerate(self.events):
            a = float(item.get('started_at_s', item.get('confirmed_at_s', 0)))
            b = float(item.get('closed_at_s') if item.get('closed_at_s') is not None else self.end_s)
            if b < start or a > end:
                continue
            rect = QRectF(chart.left() + (max(a, start) - start) / span * chart.width(),
                          chart.top(), max(5, (min(b, end) - max(a, start)) / span * chart.width()), chart.height())
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor('#eab158' if item.get('label') == 'fire' else '#8ea3c5'))
            p.drawRoundedRect(rect, 6, 6)
            if self.hasFocus() and index == self.focused_event:
                p.setPen(QPen(QColor('#20334c'), 2))
                p.drawRect(rect)
            self.hits.append((rect, item['event_id']))
        p.setPen(QColor('#64758a'))
        for i in range(5):
            x = chart.left() + i / 4 * chart.width()
            p.drawText(QRectF(x - 28, 44, 56, 20), Qt.AlignmentFlag.AlignCenter, video_time(start + i / 4 * span))
        caption = '点击时间片段回看' if self.events else '事件将显示在这里'
        p.drawText(QRectF(20, 71, chart.width(), 22), Qt.AlignmentFlag.AlignLeft, caption)

    def mousePressEvent(self, event):
        for rect, identifier in reversed(self.hits):
            if rect.adjusted(-3, -4, 3, 4).contains(event.position()):
                self.eventSelected.emit(identifier)
                break
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if self.events and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.focused_event = (self.focused_event + (1 if event.key() == Qt.Key.Key_Right else -1)) % len(self.events)
            self.update()
        elif self.events and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.eventSelected.emit(self.events[self.focused_event % len(self.events)]['event_id'])
        else:
            super().keyPressEvent(event)


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
        painter.fillRect(self.rect(), QColor('#ffffff'))
        chart = QRectF(38, 12, max(1, self.width() - 60), max(1, self.height() - 43))
        last_time = self.points[-1][0] if self.points else 0
        first_time = max(0, last_time - 120)
        span = max(10, last_time - first_time)
        painter.setPen(QColor('#798aa8'))
        for score in (0, 50, 100):
            y = chart.bottom() - score / 100 * chart.height()
            painter.drawText(QRectF(0, y - 8, 30, 18), Qt.AlignmentFlag.AlignRight, str(score))
            painter.setPen(QPen(QColor('#dce4ee'), 1))
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
        painter.setPen(QPen(QColor('#078c98'), 2))
        painter.drawPath(path)
        painter.setPen(QColor('#a1afc7'))
        painter.drawText(QRectF(chart.left(), chart.bottom() + 8, chart.width(), 20),
                         Qt.AlignmentFlag.AlignLeft, f'{first_time:.1f}s')
        painter.drawText(QRectF(chart.left(), chart.bottom() + 8, chart.width(), 20),
                         Qt.AlignmentFlag.AlignRight, f'{first_time + span:.1f}s · 观测时间')
        if not any(score is not None for _, score in self.points):
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, '持续检测到火焰或烟雾后显示视觉指数')


class CoverageTrend(QWidget):
    """Detector-box coverage history and a deliberately short extrapolation."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.trend_event = None

    def set_event(self, event):
        self.trend_event = event
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#ffffff'))
        chart = QRectF(52, 20, max(1, self.width() - 76), max(1, self.height() - 62))
        data = self.trend_event or {}
        history = data.get('history') or []
        projection = data.get('projection') or []
        points = history + projection
        if not points:
            painter.setPen(QColor('#98aac6'))
            painter.drawText(chart, Qt.AlignmentFlag.AlignCenter, '观测不足，暂不显示框占比变化')
            return
        first = min(float(p['timestamp_s']) for p in points)
        last = max(float(p['timestamp_s']) for p in points)
        span = max(3.0, last - first)
        coverage_values = [float(p['coverage_ratio']) for p in points if p.get('coverage_ratio') is not None]
        upper = data.get('upper_coverage_ratio')
        lower = data.get('lower_coverage_ratio')
        if upper is not None:
            coverage_values.append(float(upper))
        maximum = min(1.0, max(.05, max(coverage_values or [.05]) * 1.2))

        def position(t, ratio):
            return QPointF(chart.left() + (float(t) - first) / span * chart.width(),
                           chart.bottom() - max(0, min(maximum, float(ratio))) / maximum * chart.height())

        for tick in (0, maximum / 2, maximum):
            y = position(first, tick).y()
            painter.setPen(QPen(QColor('#dce4ee'), 1))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
            painter.setPen(QColor('#98aac6'))
            painter.drawText(QRectF(0, y - 9, 45, 20), Qt.AlignmentFlag.AlignRight, f'{tick:.1%}')
        painter.save()
        painter.setClipRect(chart)
        if len(projection) > 1 and lower is not None and upper is not None:
            band = QPainterPath()
            for index, point in enumerate(projection):
                bound = point.get('upper_coverage_ratio')
                if bound is None:
                    bound = point['coverage_ratio'] + (upper - projection[-1]['coverage_ratio']) * index / (len(projection) - 1)
                pixel = position(point['timestamp_s'], bound)
                if index == 0:
                    band.moveTo(pixel)
                else:
                    band.lineTo(pixel)
            for index, point in reversed(list(enumerate(projection))):
                bound = point.get('lower_coverage_ratio')
                if bound is None:
                    bound = point['coverage_ratio'] - (projection[-1]['coverage_ratio'] - lower) * index / (len(projection) - 1)
                band.lineTo(position(point['timestamp_s'], bound))
            band.closeSubpath()
            painter.fillPath(band, QColor(239, 186, 89, 35))
        for series, color, style in ((history, '#078c98', Qt.PenStyle.SolidLine),
                                     (projection, '#bc7610', Qt.PenStyle.DashLine)):
            path = QPainterPath()
            started = False
            for point in series:
                if point.get('coverage_ratio') is None:
                    started = False
                    continue
                pixel = position(point['timestamp_s'], point['coverage_ratio'])
                if started:
                    path.lineTo(pixel)
                else:
                    path.moveTo(pixel)
                started = True
            painter.setPen(QPen(QColor(color), 2, style))
            painter.drawPath(path)
        painter.restore()
        painter.setPen(QColor('#98aac6'))
        painter.drawText(QRectF(chart.left(), chart.bottom() + 8, chart.width(), 22),
                         Qt.AlignmentFlag.AlignLeft, f'{first:.1f}s')
        painter.drawText(QRectF(chart.left(), chart.bottom() + 8, chart.width(), 22),
                         Qt.AlignmentFlag.AlignRight, f'{first + span:.1f}s · 观测时间')
        painter.drawText(QRectF(chart.left(), 1, chart.width(), 18), Qt.AlignmentFlag.AlignRight,
                         '实线：观测    虚线：外推    阴影：经验残差范围' if projection else '实线：实际观测的检测框占比')
