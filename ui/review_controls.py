"""Shared review vocabulary, stage colors and a durable human activity record."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QTextBrowser, QPushButton, QStyledItemDelegate, QStyle
from core.review_workflow import CONCLUSIONS, LEGACY_CONCLUSIONS, CONCLUSION_NAMES, presentation, history_action_label


def fill_conclusions(combo):
    combo.addItem('请选择核查结果', '')
    for label, value in CONCLUSIONS + LEGACY_CONCLUSIONS:
        combo.addItem(label, value)
    for _, value in LEGACY_CONCLUSIONS:
        index = combo.findData(value)
        combo.view().setRowHidden(index, True)
        combo.model().item(index).setEnabled(False)


def paint_status(label, action):
    style = presentation(action)
    label.setText(style['label'])
    label.setStyleSheet(f'background:{style["background"]};color:{style["color"]};border:1px solid {style["color"]};'
                       'border-radius:10px;padding:9px 12px;font-size:17px;font-weight:600;')


def paint_status_cell(item, action):
    style = presentation(action)
    item.setForeground(QBrush(QColor(style['color'])))
    item.setBackground(QBrush(QColor(style['background'])))
    font = item.font()
    font.setBold(True)
    item.setFont(font)
    item.setData(Qt.ItemDataRole.UserRole + 1, action)


class StatusDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        action = index.data(Qt.ItemDataRole.UserRole + 1)
        if action is None:
            return super().paint(painter, option, index)
        style = presentation(action)
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.fillRect(option.rect, QColor('#e5edff') if option.state & QStyle.StateFlag.State_Selected else option.palette.base())
        rect = option.rect.adjusted(5, 7, -5, -7)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(style['background']))
        painter.drawRoundedRect(rect, 9, 9)
        font = option.font
        font.setBold(True)
        font.setPixelSize(16)
        painter.setFont(font)
        painter.setPen(QColor(style['color']))
        painter.drawText(rect.adjusted(4, 2, -4, -2), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, index.data() or '')
        painter.restore()


def show_handling_history(parent, run_path, event_id):
    from core.records import read_jsonl_tail
    from pathlib import Path
    from html import escape
    rows = [row for row in read_jsonl_tail(Path(run_path) / 'feedback.jsonl', 5000) if row.get('event_id') == event_id]
    notices = [row for row in read_jsonl_tail(Path(run_path) / 'alerts.jsonl', 10000)
               if row.get('event_id') == event_id and row.get('kind') != 'event_closed']
    entries = [(row.get('created_at', ''), index, row) for index, row in enumerate(rows)]
    dialog = QDialog(parent)
    dialog.setWindowTitle(f'事件 #{event_id} · 处理记录' if event_id is not None else '综合异常提示 · 处理记录')
    dialog.resize(620, 520)
    layout = QVBoxLayout(dialog)
    heading = QLabel('核查与处理记录')
    heading.setObjectName('sectionTitle')
    layout.addWidget(heading)
    text = QTextBrowser()
    text.document().setDefaultStyleSheet('p { font-size:16px; }')
    blocks = []
    current_notice = notices[-1].get('notification_id') if notices else None
    for stamp, _, row in reversed(entries):
        state = presentation(row.get('review_action') or row.get('action'))
        action = row.get('action')
        title = history_action_label(row)
        original = CONCLUSION_NAMES.get(row.get('conclusion'), '')
        detail = row.get('review_comment') or row.get('comment') or ''
        extra = f'<p>核查结论：{escape(original)}</p>' if original and action == 'resolved' else ''
        if action == 'reopened' and row.get('reopened_conclusion'):
            previous = CONCLUSION_NAMES.get(row['reopened_conclusion'], row['reopened_conclusion'])
            extra += f'<p>撤回前结论：{escape(previous)}</p>'
        blocks.append(f'<div style="margin-bottom:18px"><p style="color:{state["color"]};font-weight:600">{escape(title)} · {state["label"]}</p>'
            f'<p>{escape(stamp.replace("T", " "))}</p>{extra}<p>{escape(detail) or "未填写备注"}</p>'
            + ('<p>该记录对应此前的提示；后续新提示需重新核查。</p>' if current_notice and row.get('notification_id') != current_notice else '') + '</div>')
    text.setHtml(''.join(blocks) or '<p>尚无人工处理记录。</p>')
    layout.addWidget(text, 1)
    close = QPushButton('关闭')
    close.clicked.connect(dialog.accept)
    layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
    dialog.exec()
