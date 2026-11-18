"""Readable native typography and consistent product surfaces."""
from pathlib import Path

_font_family = None


def ui_font():
    """Resolve a real CJK sans font through Qt, never a CSS fallback list."""
    from PySide6.QtGui import QFont, QFontDatabase
    global _font_family
    if _font_family is None:
        for name in ('msyh.ttc', 'msyhbd.ttc'):
            path = Path('C:/Windows/Fonts') / name
            if path.is_file():
                QFontDatabase.addApplicationFont(str(path))
        available = set(QFontDatabase.families())
        _font_family = next((name for name in ('Microsoft YaHei UI', 'Microsoft YaHei',
                            'Noto Sans CJK SC', 'Noto Sans SC', 'PingFang SC') if name in available), 'Sans Serif')
    font = QFont(_font_family)
    font.setPixelSize(16)
    font.setStyleHint(QFont.StyleHint.SansSerif)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


STYLE = '''
QWidget { font-size:16px; color:#23354d; }
QMainWindow, QDialog { background:#edf2f8; }
QLabel { background:transparent; }
QLabel#brand { font-size:26px; font-weight:700; color:#1c3151; }
QLabel#brandCaption { color:#65778f; font-size:14px; }
QLabel#pageTitle { font-size:30px; font-weight:700; color:#1c3151; }
QLabel#sectionTitle { font-size:20px; font-weight:700; color:#223c60; }
QLabel#muted { color:#576c86; font-size:15px; }
QLabel#eyebrow { color:#576c86; font-size:15px; padding:10px 14px 4px; }
QFrame#sidebar { background:#ffffff; border:0; border-right:1px solid #cdd8e7; }
QFrame#sidebar QLabel { color:#65778f; }
QFrame#sidebar QLabel#brand { color:#1c3151; }
QPushButton#nav, QPushButton#navGroup { text-align:left; background:transparent; color:#526985; border:0; border-radius:12px; padding:13px 12px; font-size:16px; min-height:22px; }
QPushButton#nav:hover, QPushButton#toolNav:hover, QPushButton#navGroup:hover { background:#edf3ff; color:#315bd1; }
QPushButton#nav:checked { background:#e3ecff; color:#2854cb; font-weight:700; border:0; }
QPushButton#nav:disabled { color:#91a0b5; }
QPushButton#toolNav { text-align:left; background:transparent; color:#5b708c; border:0; border-radius:10px; padding:10px 10px; font-size:15px; }
QPushButton#toolNav:checked { background:#e3ecff; color:#2854cb; font-weight:600; }
QPushButton#quiet { background:transparent; border:0; color:#4c6485; border-radius:9px; padding:7px 9px; font-size:15px; }
QPushButton#quiet:hover { background:#e4edff; color:#2854cb; }
QFrame#card, QFrame#setupCard, QFrame#panel { background:#ffffff; border:1px solid #c6d3e4; border-radius:16px; }
QFrame#reviewPanel { background:#f8faff; border:1px solid #b9cae3; border-radius:16px; }
QFrame#sectionHeader { background:#eef3fa; border:0; border-bottom:1px solid #d4deed; border-radius:10px; }
QFrame#filterBar { background:white; border:1px solid #c6d3e4; border-radius:14px; }
QLabel#statusBadge { background:#e8efff; color:#315ac2; border-radius:10px; padding:8px 12px; font-size:16px; font-weight:600; }
QLabel#metricValue { font-size:29px; font-weight:700; color:#244770; }
QLabel#metricCaption { font-size:16px; color:#536b89; }
QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QTextEdit { background:#ffffff; border:1px solid #bfcddd; border-radius:9px; padding:10px 12px; selection-background-color:#dce7ff; selection-color:#204ba9; font-size:16px; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus, QTextEdit:focus { border:1px solid #416bd8; background:#ffffff; }
QLineEdit:disabled, QComboBox:disabled { background:#f1f4f8; color:#8192a9; }
QComboBox::drop-down { border:0; width:26px; }
QComboBox QAbstractItemView { background:white; border:1px solid #bfcddd; selection-background-color:#e4edff; selection-color:#284fb2; padding:6px; outline:0; }
QPushButton { background:#ffffff; border:1px solid #c0cfe3; border-radius:10px; padding:10px 13px; min-height:22px; font-weight:500; font-size:16px; }
QPushButton:hover { background:#edf3ff; border-color:#89a4df; color:#2854cb; }
QPushButton:pressed { background:#dce7ff; }
QPushButton:disabled { color:#8a9bb2; background:#f0f3f8; border-color:#dde5ef; }
QPushButton#primary { background:#3867df; color:#ffffff; border:1px solid #3867df; font-weight:600; }
QPushButton#primary:hover { background:#2c55c2; border-color:#2c55c2; }
QPushButton#primary:pressed { background:#244ab0; }
QPushButton#primary:disabled { background:#bdcdf0; border-color:#bdcdf0; color:#f8faff; }
QPushButton#stop { background:#fff4f4; color:#b93d4d; border-color:#e8bcc4; }
QPushButton#stop:hover { background:#ffe8eb; }
QToolButton { border:1px solid #bfcee3; background:#ffffff; border-radius:9px; padding:6px; font-size:16px; }
QToolButton:hover { border-color:#8ba6df; background:#edf3ff; }
QTableWidget { background:#ffffff; alternate-background-color:#f4f7fb; gridline-color:#dbe4ef; border:1px solid #c6d3e4; border-radius:12px; selection-background-color:#e6eeff; outline:0; font-size:16px; }
QTableWidget::item { padding:10px 8px; border-bottom:1px solid #e0e7f0; }
QTableWidget::item:selected { background:#e5edff; color:#254fae; }
QHeaderView::section { background:#edf2f9; color:#435c7b; font-size:15px; font-weight:600; padding:13px 8px; border:0; }
QTabWidget::pane { border:0; }
QTabBar::tab { background:#e5edf8; border-radius:9px; margin:3px; padding:9px 16px; }
QTabBar::tab:selected { background:#ffffff; color:#2854cb; }
QLabel#alertBanner { background:#fff5e6; color:#986019; border:1px solid #e9cda5; border-radius:12px; padding:12px 15px; }
QLabel#statusStrip { background:#ffffff; border:1px solid #c6d3e4; border-radius:12px; padding:12px; color:#496280; }
QLabel#statePill { background:#e4ecff; color:#3d60ab; border-radius:11px; padding:7px 12px; font-size:15px; }
QLabel#pendingPill { background:#fff0d7; color:#966015; border-radius:11px; padding:7px 12px; font-size:15px; }
QProgressBar { border:0; border-radius:3px; background:#dce5f1; min-height:6px; max-height:6px; }
QProgressBar::chunk { background:#547cdc; border-radius:3px; }
QScrollArea { background:transparent; border:0; }
QScrollArea > QWidget > QWidget { background:transparent; }
QScrollBar:vertical { background:transparent; width:9px; margin:3px 0; }
QScrollBar::handle:vertical { background:#b5c4d9; min-height:34px; border-radius:4px; }
QScrollBar::handle:vertical:hover { background:#8da5c7; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:transparent; }
QScrollBar:horizontal { background:transparent; height:9px; }
QScrollBar::handle:horizontal { background:#b5c4d9; min-width:34px; border-radius:4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background:transparent; }
QSplitter::handle { background:#d2ddeb; width:8px; height:8px; border-radius:4px; margin:3px; }
QCheckBox { color:#435c7b; spacing:9px; font-size:16px; }
QCheckBox::indicator { width:18px; height:18px; }
QToolTip { background:#243c5c; color:#ffffff; border:0; padding:9px 12px; font-size:15px; }
'''
