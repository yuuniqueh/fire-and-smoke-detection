"""Readable cards and bounded-width tables for user-facing analysis results.

These widgets present caller-provided conclusions. They never calculate risk,
reinterpret an AI answer, or render dynamic text as HTML. An outer scroll area
may contain AnalysisSummary; none of these components owns a scroll area.
"""
import math

from PySide6.QtCore import Qt, QRect, QSize
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                              QSizePolicy, QVBoxLayout, QWidget)


class WrapLabel(QLabel):
    """Let long Chinese and source names wrap within a narrow sidebar."""
    def __init__(self, text='', *, size=18, color='#23354d', bold=False, parent=None):
        super().__init__(str(text), parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        self.setMinimumWidth(0)
        # Establish the native metrics before any parent layout measures this
        # label. A stylesheet alone can be polished after a scroll area's first
        # layout pass, leaving a 32px score in a 16px cached line box.
        from ui.theme import ui_font
        font = ui_font()
        font.setPixelSize(size)
        font.setWeight(QFont.Weight.DemiBold if bold else QFont.Weight.Normal)
        self.setFont(font)
        self.setMinimumHeight(self.fontMetrics().height())
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setStyleSheet(f'background:transparent;border:0;color:{color};font-size:{size}px;'
                           + ('font-weight:600;' if bold else 'font-weight:400;'))

    def minimumSizeHint(self):
        return QSize(0, self._line_height())

    def _line_height(self):
        margins = self.contentsMargins()
        return self.fontMetrics().height() + margins.top() + margins.bottom() + 2 * self.margin()

    def heightForWidth(self, width):
        # QLabel's native heightForWidth includes minimumHeight. Calling it after
        # setting a wrapped minimum can therefore only grow, never shrink when a
        # later record has shorter text. Measure independently with the same
        # native font and plain-text wrapping flags.
        margins = self.contentsMargins()
        inner = max(1, width - margins.left() - margins.right() - 2 * self.margin())
        flags = int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs)
        bounds = self.fontMetrics().boundingRect(QRect(0, 0, inner, 2_000_000), flags, self.text())
        return max(self.fontMetrics().height(), bounds.height()) + margins.top() + margins.bottom() + 2 * self.margin()

    def sizeHint(self):
        value = super().sizeHint()
        return QSize(value.width(), self.heightForWidth(max(1, value.width())))

    def setText(self, text):
        super().setText(str(text))
        self._sync_minimum_height()

    def _sync_minimum_height(self):
        height = self.heightForWidth(self.width())
        if self.minimumHeight() != height:
            self.setMinimumHeight(height)
            self.updateGeometry()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # A vertical scrollbar can reduce the final cell width after a grid's
        # first height-for-width pass. Reconcile against that actual width so a
        # newly wrapped second/third line cannot be compressed by stale geometry.
        self._sync_minimum_height()


class TextCard(QFrame):
    def __init__(self, title='', text='', *, accent='blue', parent=None):
        super().__init__(parent)
        self.setObjectName('analysisTextCard')
        background, border, title_color = {
            'blue': ('#eef4ff', '#a7c0ed', '#2457a6'),
            'amber': ('#fff7e9', '#dfc080', '#925810'),
            'neutral': ('#ffffff', '#c1cfe2', '#263e5e'),
        }.get(accent, ('#ffffff', '#c1cfe2', '#263e5e'))
        self.setStyleSheet(f'QFrame#analysisTextCard {{background:{background};border:1px solid {border};border-radius:15px;}}')
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 14, 15, 14)
        layout.setSpacing(10)
        self.header_layout = QHBoxLayout()
        self.header_layout.setSpacing(8)
        self.title_label = WrapLabel(title, size=22, color=title_color, bold=True)
        self.header_layout.addWidget(self.title_label, 1)
        layout.addLayout(self.header_layout)
        self.body = WrapLabel(text, size=18)
        layout.addWidget(self.body)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_text(self, text):
        self.body.setText(str(text or ''))


class ResultCard(QFrame):
    """A plain-language review recommendation, with an ordinal priority bar.

    The numeric rule index is retained in score_label/score_value for callers'
    technical details, but is never presented as a probability or main result.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('analysisResultCard')
        self.setStyleSheet('QFrame#analysisResultCard {background:#ffffff;border:1px solid #aebfd8;border-radius:15px;}')
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        self.title_label = WrapLabel('研判建议', size=22, color='#263e5e', bold=True)
        self.score_label = WrapLabel('— / 100', size=32, color='#2c5bc2', bold=True)
        self.score_value = None
        self.context_label = WrapLabel('', size=16, color='#65788f')
        self.context_label.hide()
        self.conclusion_label = WrapLabel('等待分析', size=32, color='#263e5e', bold=True)
        self.priority_title = WrapLabel('核查优先级', size=17, color='#526984')
        self.priority_row = QWidget()
        row = QHBoxLayout(self.priority_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        self.priority_segments = {}
        for key, caption in (('LOW', '一般'), ('MEDIUM', '较高'), ('HIGH', '高')):
            segment = WrapLabel(caption, size=17, color='#65788f')
            segment.setAlignment(Qt.AlignmentFlag.AlignCenter)
            segment.setMargin(5)
            row.addWidget(segment, 1)
            self.priority_segments[key] = segment
        self.priority_state = WrapLabel('等待分析', size=17, color='#526984')
        self.priority_level = None
        self.historical = False
        self.status_label = WrapLabel('', size=17, color='#526984')
        for widget in (self.title_label, self.context_label, self.conclusion_label, self.priority_title,
                       self.priority_row, self.priority_state, self.status_label, self.score_label):
            layout.addWidget(widget)
        self.score_label.hide()
        self._set_priority(None, historical=False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_result(self, conclusion, score, status='', level=None, priority_level=None, historical=False):
        from core.alert_presentation import LEVEL_COLORS
        color = LEVEL_COLORS.get(level, '#2c5bc2')
        valid_score = (not isinstance(score, bool) and isinstance(score, (int, float))
                       and math.isfinite(score))
        self.score_value = float(score) if valid_score else None
        self.score_label.setText(f'{score:.1f} / 100' if valid_score else '— / 100')
        self.score_label.hide()
        text = str(conclusion or ('持续观察' if level == 'NORMAL' else '等待分析'))
        self.historical = bool(historical or '当时' in text or '历史' in text)
        self.context_label.setText('历史记录 · 显示当时的研判建议' if self.historical else '')
        self.context_label.setVisible(self.historical)
        self.conclusion_label.setText(text)
        self.conclusion_label.setStyleSheet(f'background:transparent;border:0;color:{color};font-size:32px;font-weight:600;')
        effective = priority_level if priority_level is not None else level
        # A completed workflow can use NORMAL for muted historical text. Unless
        # its original level was supplied, do not turn that past alert into a
        # new recommendation to keep observing.
        if self.historical and priority_level is None and effective == 'NORMAL':
            effective = None
        self._set_priority(effective, historical=self.historical)
        self.status_label.setText(str(status or ''))
        self.status_label.setVisible(bool(status))

    def _set_priority(self, level, *, historical):
        from core.alert_presentation import LEVEL_COLORS, LEVEL_BACKGROUNDS, LEVEL_BORDERS
        self.priority_level = level if level in ('NORMAL', 'LOW', 'MEDIUM', 'HIGH') else None
        active = self.priority_level in self.priority_segments
        self.priority_row.setVisible(active)
        self.priority_state.setVisible(not active)
        self.priority_state.setText('以当时记录为准' if historical and not active else
            '持续观察' if self.priority_level == 'NORMAL' else '等待分析')
        self.priority_title.setText('当时核查优先级' if historical else '核查优先级')
        for key, segment in self.priority_segments.items():
            selected = key == self.priority_level
            segment.setProperty('selected', selected)
            color = LEVEL_COLORS[key] if selected else '#65788f'
            background = LEVEL_BACKGROUNDS[key] if selected else '#eef2f7'
            border = LEVEL_BORDERS[key] if selected else '#d7e0ec'
            font = segment.font()
            font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
            segment.setFont(font)
            segment.setStyleSheet(f'color:{color};background:{background};border:1px solid {border};'
                'border-radius:9px;font-size:17px;' + ('font-weight:600;' if selected else 'font-weight:400;'))
            segment._sync_minimum_height()


class KeyValueTable(QFrame):
    """Two fixed logical columns, naturally wrapping rows, no inner scrolling."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('analysisKeyValueTable')
        self.setStyleSheet('QFrame#analysisKeyValueTable {background:#ffffff;border:1px solid #bfcee2;border-radius:12px;}')
        self.setMinimumWidth(0)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(12, 3, 12, 3)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(0)
        self.grid.setColumnStretch(0, 2)
        self.grid.setColumnStretch(1, 3)
        self.rows = []
        self.key_labels = []
        self.value_labels = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def set_rows(self, rows):
        values = [(str(key), str(value if value is not None else '—')) for key, value in rows]
        if len(values) != len(self.key_labels):
            while self.grid.count():
                item = self.grid.takeAt(0)
                if item.widget():
                    # DeferredDelete need not run during a caller's manual
                    # processEvents(). Detach old cells immediately so repeated
                    # updates can never paint a previous value over the new row.
                    item.widget().hide()
                    item.widget().setParent(None)
                    item.widget().deleteLater()
            self.key_labels, self.value_labels = [], []
            for index in range(len(values)):
                key = WrapLabel(size=17, color='#536985')
                value = WrapLabel(size=18, color='#233c5c', bold=True)
                border = 'border-bottom:1px solid #d9e3f0;' if index + 1 < len(values) else ''
                key.setStyleSheet(key.styleSheet() + 'padding:11px 0;' + border)
                value.setStyleSheet(value.styleSheet() + 'padding:11px 0;' + border)
                self.grid.addWidget(key, index, 0)
                self.grid.addWidget(value, index, 1)
                self.key_labels.append(key)
                self.value_labels.append(value)
        for index, (key, value) in enumerate(values):
            self.key_labels[index].setText(key)
            self.value_labels[index].setText(value)
        self.rows = values
        self.setVisible(bool(values))


class _EvidenceList(QFrame):
    def __init__(self, title, *, uncertain=False, parent=None):
        super().__init__(parent)
        self.setObjectName('analysisEvidenceList')
        background = '#fff8ed' if uncertain else '#ffffff'
        border = '#dec087' if uncertain else '#c0cfe2'
        self.setStyleSheet(f'QFrame#analysisEvidenceList {{background:{background};border:1px solid {border};border-radius:13px;}}')
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(14, 13, 14, 13)
        self.box.setSpacing(8)
        self.title_label = WrapLabel(title, size=20, color='#8a5b18' if uncertain else '#263e5e', bold=True)
        self.box.addWidget(self.title_label)
        self.labels = []
        self.items = []

    def set_items(self, items):
        values = [str(item).strip() for item in items if str(item).strip()]
        if len(values) != len(self.labels):
            for label in self.labels:
                self.box.removeWidget(label)
                label.hide()
                label.setParent(None)
                label.deleteLater()
            self.labels = []
            for _ in values:
                label = WrapLabel(size=18)
                self.box.addWidget(label)
                self.labels.append(label)
        for index, value in enumerate(values):
            self.labels[index].setText(f'• {value}')
        self.items = values
        self.setVisible(bool(values))


class AnalysisSummary(QWidget):
    """Reusable analysis presentation; put this in the page's outer scroll area.

    set_content(description, conclusion, score, metrics, reasons, status='',
                uncertainties=None, details=None, level=None, priority_level=None,
                historical=False)
    metrics/details are (label, value) rows; reasons/uncertainties are text lists.
    clear_content(), set_details(rows), set_details_visible(bool) are public.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(13)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.result_card = ResultCard()
        self.description_card = TextCard('AI画面理解', accent='blue')
        self.metrics_title = WrapLabel('关键数据', size=20, color='#263e5e', bold=True)
        self.metrics_table = KeyValueTable()
        self.reasons_card = _EvidenceList('主要依据')
        self.uncertainties_card = _EvidenceList('待核对事项', uncertain=True)
        self.details_toggle = QPushButton('更多分析详情')
        self.details_toggle.setObjectName('quiet')
        self.details_toggle.setCheckable(True)
        self.details_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.details_toggle.setStyleSheet('text-align:left;font-size:16px;padding:9px 3px;')
        self.details_table = KeyValueTable()
        self.details_toggle.toggled.connect(self.set_details_visible)
        for widget in (self.result_card, self.description_card, self.metrics_title, self.metrics_table,
                       self.reasons_card, self.uncertainties_card, self.details_toggle, self.details_table):
            layout.addWidget(widget)
        self.clear_content()

    def set_content(self, description, conclusion, score, metrics, reasons, status='',
                    uncertainties=None, details=None, level=None, priority_level=None, historical=False):
        self.result_card.set_result(conclusion, score, status, level, priority_level, historical)
        self.description_card.set_text(description or '等待AI画面分析。')
        self.metrics_table.set_rows(metrics or [])
        self.metrics_title.setVisible(bool(metrics))
        self.reasons_card.set_items(reasons or [])
        self.uncertainties_card.set_items(uncertainties or [])
        self.set_details(details or [])

    def set_details(self, rows):
        was_visible = self.details_toggle.isChecked()
        self.details_table.set_rows(rows or [])
        self.details_toggle.setVisible(bool(rows))
        self.set_details_visible(bool(rows) and was_visible)

    def set_details_visible(self, visible):
        visible = bool(visible and self.details_table.rows)
        self.details_toggle.blockSignals(True)
        self.details_toggle.setChecked(visible)
        self.details_toggle.blockSignals(False)
        self.details_table.setVisible(visible)
        self.details_toggle.setText('收起分析详情' if visible else '更多分析详情')

    def clear_content(self):
        self.set_content('选择记录后查看AI画面理解。', '等待查看', None, [], [],
            status='尚未选择记录', uncertainties=[], details=[])
