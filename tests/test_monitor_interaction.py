"""Monitor video selection works without confirmed events or button side effects."""
import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ui.monitor_interaction import ClickableVideoView
from ui.widgets import VideoView


class ClickableVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = ClickableVideoView()
        self.view.resize(400, 220)
        self.view.clear_frame('尚无烟火事件，仍可查看AI画面分析')
        self.view.show()
        self.app.processEvents()
        self.clicks = []
        self.view.clicked.connect(lambda: self.clicks.append(True))

    def tearDown(self):
        self.view.clicked.disconnect()
        self.view.close()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(self.view, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_left_click_selects_without_an_image_or_confirmed_event(self):
        self.assertIsNone(self.view.image)
        QTest.mouseClick(self.view, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        self.assertEqual(len(self.clicks), 1)
        self.assertEqual(self.view.focusPolicy(), Qt.FocusPolicy.StrongFocus)

    def test_drag_and_release_outside_are_not_selection(self):
        start = QPoint(100, 100)
        moved = QPoint(100 + QApplication.startDragDistance() + 15, 100)
        QTest.mousePress(self.view, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(self.view, moved)
        QTest.mouseMove(self.view, start)
        QTest.mouseRelease(self.view, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(self.clicks, [])
        QTest.mousePress(self.view, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseRelease(self.view, Qt.MouseButton.LeftButton, pos=QPoint(self.view.width() + 5, 100))
        self.assertEqual(self.clicks, [])
        QTest.mouseClick(self.view, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(len(self.clicks), 1)

    def test_right_click_or_release_without_a_press_is_not_selection(self):
        QTest.mouseClick(self.view, Qt.MouseButton.RightButton, pos=QPoint(100, 100))
        QTest.mouseRelease(self.view, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        self.assertEqual(self.clicks, [])

    def test_context_badge_is_mouse_transparent_and_its_area_selects_video(self):
        self.view.set_context('本地录像 · AI画面分析')
        self.app.processEvents()
        self.assertTrue(self.view.context.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        QTest.mouseClick(self.view, Qt.MouseButton.LeftButton, pos=self.view.context.geometry().center())
        self.assertEqual(len(self.clicks), 1)

    def test_enter_space_select_once_and_auto_repeat_is_ignored(self):
        self.view.setFocus()
        QTest.keyClick(self.view, Qt.Key.Key_Return)
        QTest.keyClick(self.view, Qt.Key.Key_Space)
        self.assertEqual(len(self.clicks), 2)
        repeated = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
            Qt.KeyboardModifier.NoModifier, ' ', True, 1)
        QApplication.sendEvent(self.view, repeated)
        self.assertEqual(len(self.clicks), 2)
        QTest.keyClick(self.view, Qt.Key.Key_Right)
        self.assertEqual(len(self.clicks), 2)

    def test_disabled_video_and_other_video_views_are_unchanged(self):
        self.view.setEnabled(False)
        QTest.mouseClick(self.view, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        QTest.keyClick(self.view, Qt.Key.Key_Return)
        self.assertEqual(self.clicks, [])
        original = VideoView()
        self.assertFalse(hasattr(original, 'clicked'))
        self.assertFalse(original.context.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        original.close()
        original.deleteLater()
        QCoreApplication.sendPostedEvents(original, QEvent.Type.DeferredDelete)


if __name__ == '__main__':
    unittest.main()
