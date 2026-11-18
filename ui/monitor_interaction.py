"""Opt-in monitor selection without changing the app's other video views."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication

from ui.widgets import VideoView


class ClickableVideoView(VideoView):
    """Select a monitor with a genuine left click or Enter/Space.

    The context badge is mouse-transparent. Dragging, right clicking, disabled
    views and key auto-repeat never emit selection; the caller owns selection
    state and decides whether to show video-level AI or a confirmed event.
    """
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._press_position = None
        self._dragged = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('选择监控画面，查看画面分析')
        self.context.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def mousePressEvent(self, event):
        self._press_position = None
        self._dragged = False
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            if self.rect().contains(point):
                self._press_position = point
                self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_position is not None:
            distance = (event.position().toPoint() - self._press_position).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._dragged = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        start, self._press_position = self._press_position, None
        point = event.position().toPoint()
        valid = (self.isEnabled() and start is not None and not self._dragged
                 and self.rect().contains(point)
                 and (point - start).manhattanLength() < QApplication.startDragDistance())
        self._dragged = False
        event.accept()
        if valid:
            self.clicked.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            event.accept()
            if self.isEnabled() and not event.isAutoRepeat():
                self.clicked.emit()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            event.accept()
            return
        super().keyReleaseEvent(event)

    def focusOutEvent(self, event):
        self._press_position = None
        self._dragged = False
        super().focusOutEvent(event)
