"""Small original vector icons, drawn with Qt to stay sharp at every scale."""
from functools import lru_cache
import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap


@lru_cache(maxsize=80)
def icon(name, color='#78869a', size=20):
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 12., size / 12.)
    painter.setPen(QPen(QColor(color), 1.65, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    def line(x, y, a, b):
        painter.drawLine(QPointF(x, y), QPointF(a, b))
    def path(points, close=False):
        shape = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            shape.lineTo(*point)
        if close:
            shape.closeSubpath()
        painter.drawPath(shape)
    if name == 'brand':
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#3867e8'))
        painter.drawRoundedRect(QRectF(0, 0, 24, 24), 7, 7)
        flame = QPainterPath(QPointF(13, 4))
        flame.cubicTo(14, 9, 19, 10, 18, 15)
        flame.cubicTo(17, 21, 7, 21, 6, 15)
        flame.cubicTo(5, 12, 8, 8, 10, 7)
        flame.cubicTo(9, 12, 12, 12, 13, 4)
        painter.setBrush(QColor('white'))
        painter.drawPath(flame)
    elif name == 'center':
        for x in (3.5, 13.5):
            for y in (3.5, 13.5):
                painter.drawRoundedRect(QRectF(x, y, 7, 7), 2, 2)
    elif name == 'camera':
        painter.drawRoundedRect(QRectF(2.5, 6, 13, 12), 3, 3)
        path([(15.5, 10), (21, 7), (21, 17), (15.5, 14)])
        painter.drawEllipse(QPointF(9, 12), 2.5, 2.5)
    elif name in ('file', 'play'):
        if name == 'file':
            painter.drawRoundedRect(QRectF(3, 4, 18, 16), 3, 3)
        path([(10, 8), (16, 12), (10, 16)], True)
    elif name == 'events':
        painter.drawRoundedRect(QRectF(5, 3, 14, 18), 3, 3)
        for y in (8, 12, 16):
            line(9, y, 15, y)
    elif name == 'history':
        painter.drawEllipse(QRectF(3, 3, 18, 18))
        path([(12, 7), (12, 12), (16, 14)])
    elif name == 'storage':
        painter.drawRoundedRect(QRectF(4, 9, 16, 12), 2, 2)
        painter.drawRoundedRect(QRectF(3, 3, 18, 6), 2, 2)
        line(9, 13, 15, 13)
    elif name == 'settings':
        painter.drawEllipse(QPointF(12, 12), 6, 6)
        painter.drawEllipse(QPointF(12, 12), 2.4, 2.4)
        for i in range(8):
            angle = math.pi * i / 4
            line(12 + 7 * math.cos(angle), 12 + 7 * math.sin(angle),
                 12 + 9 * math.cos(angle), 12 + 9 * math.sin(angle))
    elif name == 'more':
        for x in (5, 12, 19):
            painter.drawEllipse(QPointF(x, 12), .8, .8)
    elif name == 'folder':
        path([(3, 7), (3, 5), (10, 5), (12, 8), (21, 8), (21, 20), (3, 20)], True)
    elif name in ('down', 'right'):
        path([(7, 10), (12, 15), (17, 10)] if name == 'down' else [(10, 7), (15, 12), (10, 17)])
    painter.end()
    pixmap.setDevicePixelRatio(2.)
    return QIcon(pixmap)
