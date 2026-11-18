"""A four-channel local monitoring desk with location-aware event review."""
import json
import math
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QTimer, Qt, Signal, Slot, QProcess, QSize, QEvent
from PySide6.QtGui import QIcon, QPixmap, QImage
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QScrollArea, QSizePolicy, QSplitter, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QToolButton, QTabBar, QApplication)

from core.devices import DeviceRegistry, validate_device, resolve_device_analysis
from core.model_profiles import PROFILE_NAMES, profile_details
from core.pipeline import AnalysisOptions
from core.records import append_feedback, read_jsonl_tail, event_review_state, REVIEW_COMPLETE, load_run
from core.runtime import ASSET_ROOT, ROOT
from core.scene_review import SCENES, contextual_priority, local_endpoint
from ui.monitor_worker import MonitorWorker, SceneReviewWorker
from ui.product import video_time
from core.alert_presentation import LEVEL_ACTIONS, LEVEL_COLORS
from core.review_workflow import phase, matches, presentation, conclusion_label, reopen_label
from core.event_order import sorted_event_keys, enumerate_event_keys
from ui.review_controls import fill_conclusions, paint_status, paint_status_cell, StatusDelegate, show_handling_history
from ui.point_models import PointModelSelector
from ui.analysis_cards import AnalysisSummary
from ui.monitor_interaction import ClickableVideoView

REVIEW_LABELS = {'pending': '待核查', 'acknowledged': '已查看 · 待核查', 'normal_use': '正常用火',
    'confirmed_abnormal': '确认异常 · 待处理', 'resolved': '人工登记已处理', 'false_positive': '疑似视觉误识别',
    'needs_verification': '需进一步核实', 'uncertain': '无法判断'}


def make_table(headers):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.setShowGrid(False)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    table.horizontalHeader().setHighlightSections(False)
    return table


class PointDialog(QDialog):
    def __init__(self, device=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('监控点信息')
        self.resize(640, 530)
        self.original = dict(device or {})
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(self.original.get('name', ''))
        self.location = QLineEdit(self.original.get('location', ''))
        self.location.setPlaceholderText('例如：教学楼一层东侧／实验室入口')
        self.kind = QComboBox()
        self.kind.addItem('本地录像 · 演示／分析', 'file')
        self.kind.addItem('USB／手机虚拟摄像头', 'camera')
        self.kind.setCurrentIndex(self.kind.findData(self.original.get('kind', 'file')))
        self.source = QLineEdit(self.original.get('source', ''))
        self.source.setPlaceholderText('选择录像；摄像头填写编号0—15')
        choose = QPushButton('选择录像')
        def pick():
            path, _ = QFileDialog.getOpenFileName(self, '选择本地录像', str(ASSET_ROOT / 'videos'), '录像 (*.mp4 *.avi *.webm *.mov *.mkv);;全部文件 (*)')
            if path:
                self.source.setText(path)
                if not self.name.text().strip():
                    self.name.setText(Path(path).stem[:80])
        choose.clicked.connect(pick)
        source_row = QHBoxLayout()
        source_row.addWidget(self.source, 1)
        source_row.addWidget(choose)
        for title, widget in (('设备名称', self.name), ('实际位置', self.location), ('输入类型', self.kind)):
            form.addRow(title, widget)
        form.addRow('录像／设备编号', source_row)
        layout.addLayout(form)
        self.model_settings = PointModelSelector(self.original, self)
        layout.addWidget(self.model_settings)
        note = QLabel('位置由你登记。AI只描述画面中的相对位置；本地录像会明确标注为演示或历史输入。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        try:
            self.value = validate_device({**self.original, 'name': self.name.text(), 'location': self.location.text(),
                                          'kind': self.kind.currentData(), 'source': self.source.text(),
                                          **self.model_settings.value()})
            self.accept()
        except ValueError as exc:
            QMessageBox.information(self, '请检查监控点', str(exc))


class MonitorCard(QFrame):
    selected = Signal(str)
    startRequested = Signal(str)
    stopRequested = Signal(str)
    modelRequested = Signal(str)

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = dict(device)
        self._selection_press = None
        self.setObjectName('card')
        self.setStyleSheet('QFrame#card {background:#ffffff;border:1px solid #cbd8e9;border-radius:16px;} QFrame#card[selected="true"] {background:#f7faff;border:2px solid #4875e8;}')
        self.setMinimumHeight(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel(device['name'])
        title.setStyleSheet('font-size:16px;font-weight:600;color:#23324a;')
        title.setWordWrap(True)
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title.setToolTip(device['name'])
        title.setMaximumHeight(45)
        header.addWidget(title, 1)
        self.status = QLabel('待启动')
        self.status.setWordWrap(False)
        self.status.setMinimumWidth(56)
        self.status.setMaximumWidth(150)
        self.status.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status.setStyleSheet('font-size:15px;color:#64758b;background:#f1f4fa;border-radius:10px;padding:4px 9px;')
        header.addWidget(self.status)
        layout.addLayout(header)
        location = QLabel(device['location'])
        location.setObjectName('muted')
        location.setWordWrap(True)
        location.setMaximumHeight(38)
        location.setToolTip(device['location'])
        layout.addWidget(location)
        model_row = QHBoxLayout()
        self.model_label = QLabel('检测方案：跟随全局设置')
        self.model_label.setTextFormat(Qt.TextFormat.PlainText)
        self.model_label.setWordWrap(True)
        self.model_label.setMinimumWidth(0)
        self.model_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.model_label.setStyleSheet('font-size:16px;color:#344a68;')
        self.model_button = QPushButton('模型设置')
        self.model_button.setObjectName('quiet')
        self.model_button.clicked.connect(lambda: self.modelRequested.emit(device['id']))
        model_row.addWidget(self.model_label, 1)
        model_row.addWidget(self.model_button)
        layout.addLayout(model_row)
        self.video = ClickableVideoView()
        self.video.clicked.connect(lambda: self.selected.emit(device['id']))
        self.video.setMinimumSize(180, 105)
        self.video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.video.setMaximumHeight(16777215)
        self.video.set_context('本地录像' if device['kind'] == 'file' else '摄像头')
        self.video.setToolTip('本地录像为演示或历史输入，不是实时现场。' if device['kind'] == 'file' else '摄像头画面')
        self.video.clear_frame('等待开始监测')
        layout.addWidget(self.video, 1)
        self.priority = QLabel('等待监测')
        self.priority.setStyleSheet('font-size:15px;font-weight:600;color:#52647d;')
        self.priority.setWordWrap(True)
        self.priority.setMaximumHeight(42)
        self.priority.setMinimumHeight(22)
        layout.addWidget(self.priority)
        self.work_state = QLabel()
        self.work_state.hide()
        layout.addWidget(self.work_state)
        self.scene_description = QLabel('开始后显示AI画面描述')
        self.scene_description.setTextFormat(Qt.TextFormat.PlainText)
        self.scene_description.setWordWrap(True)
        self.scene_description.setMaximumHeight(86)
        self.scene_description.setMinimumHeight(66)
        self.scene_description.setStyleSheet('background:#edf3ff;color:#263f62;border:1px solid #d5e2f7;border-radius:12px;padding:10px;font-size:17px;')
        layout.addWidget(self.scene_description)
        row = QHBoxLayout()
        self.start_button = QPushButton('开始监测')
        self.start_button.setObjectName('primary')
        self.stop_button = QPushButton('停止')
        self.stop_button.setToolTip('停止监测并保存记录')
        self.stop_button.setEnabled(False)
        details = QPushButton('查看详情')
        details.setToolTip('查看画面描述和事件')
        details.setObjectName('quiet')
        self.start_button.clicked.connect(lambda: self.startRequested.emit(device['id']))
        self.stop_button.clicked.connect(lambda: self.stopRequested.emit(device['id']))
        details.clicked.connect(lambda: self.selected.emit(device['id']))
        for widget in (self.start_button, self.stop_button, details):
            row.addWidget(widget)
        layout.addLayout(row)
        for target in (self, title, location, self.model_label, self.priority, self.work_state, self.scene_description):
            target.setCursor(Qt.CursorShape.PointingHandCursor)
            target.installEventFilter(self)
        self.scene_description.setToolTip('点击查看本路完整AI画面理解，没有烟火事件也可查看。')

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self._selection_press = (watched, event.globalPosition().toPoint())
            return True
        if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            pressed, self._selection_press = self._selection_press, None
            if (pressed and pressed[0] is watched and watched.rect().contains(event.position().toPoint())
                    and (event.globalPosition().toPoint() - pressed[1]).manhattanLength() <= QApplication.startDragDistance()):
                self.selected.emit(self.device['id'])
            return True
        return super().eventFilter(watched, event)


class MonitorCenter(QWidget):
    openSavedEvent = Signal(str, object)
    runSaved = Signal()
    reviewChanged = Signal(str)
    viewChanged = Signal()
    soundToggleRequested = Signal()
    muteRequested = Signal()

    def __init__(self, parent=None, *, registry_path=None, worker_class=MonitorWorker):
        super().__init__(parent)
        self.registry = DeviceRegistry(registry_path or ROOT / '.cache' / 'monitoring-points.json')
        self.worker_class = worker_class
        self.workers, self.cards, self.events, self.outputs = {}, {}, {}, {}
        self.packets, self.states = {}, {}
        self.run_configs = {}
        self.scenes = {}
        self.scene_worker = None
        self.scene_queue = []
        self.external_scene_busy = lambda: False
        self.external_context_for_run = lambda path: None
        self.analysis_preferences = lambda: {}
        self.sound_allowed = lambda: False
        self.selected_key = None
        self.selected_scene_locked = False
        self._event_numbers = {}
        self._next_event_number = 1
        self._events_expanded = False
        self._compact_event_height = 265
        self.event_mode = False
        self.focus_id = None
        self._queue_keys = set()
        self._closing = False
        self._card_layout_timer = QTimer(self)
        self._card_layout_timer.setSingleShot(True)
        self._card_layout_timer.timeout.connect(self._arrange_after_resize)
        self._event_layout_timer = QTimer(self)
        self._event_layout_timer.setSingleShot(True)
        self._event_layout_timer.timeout.connect(self._restore_events_layout)
        self._analysis_reveal_timer = QTimer(self)
        self._analysis_reveal_timer.setSingleShot(True)
        self._analysis_reveal_timer.timeout.connect(self._scroll_to_analysis)
        self._thumb_key = None
        self.evidence_frames = []
        self.endpoint, self.model = 'http://127.0.0.1:11435', 'qwen3-vl:2b'
        self.devices = []
        self._build()
        try:
            self.devices = self.registry.load()
        except (OSError, ValueError) as exc:
            self.message.setText('设备清单读取失败，原文件保留：' + str(exc))
        self._rebuild_cards()
        self.timer = QTimer(self)
        self.timer.setInterval(120)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    @property
    def active(self):
        return any(w.isRunning() for w in self.workers.values()) or bool(self.scene_worker and self.scene_worker.isRunning())

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        top = QHBoxLayout()
        heading = QVBoxLayout()
        heading.setSpacing(3)
        title = QLabel('监控总览')
        title.setStyleSheet('font-size:19px;font-weight:600;color:#23324a;')
        title.hide()
        self.summary = QLabel('0个监控点 · 0路分析中')
        self.summary.setObjectName('muted')
        heading.addWidget(self.summary)
        top.addLayout(heading, 1)
        self.add_button = QPushButton('添加监控点')
        self.add_button.clicked.connect(self.add_point)
        self.edit_button = QPushButton('管理')
        self.edit_button.setToolTip('管理监控点名称、位置与输入')
        self.edit_button.clicked.connect(self.manage_points)
        self.start_all_button = QPushButton('开始全部')
        self.start_all_button.setObjectName('primary')
        self.start_all_button.clicked.connect(self.start_all)
        self.stop_all_button = QPushButton('停止全部')
        self.stop_all_button.clicked.connect(self.stop_all)
        for widget in (self.add_button, self.edit_button, self.start_all_button, self.stop_all_button):
            top.addWidget(widget)
        layout.addLayout(top)
        self.monitor_actions = (self.add_button, self.edit_button, self.start_all_button, self.stop_all_button)
        self.view_bar = QWidget()
        view_layout = QHBoxLayout(self.view_bar)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.addWidget(QLabel('画面模式'))
        self.view_mode = QComboBox()
        self.view_mode.addItems(['多画面', '单画面'])
        view_layout.addWidget(self.view_mode)
        self.focus_point = QComboBox()
        self.focus_point.setMinimumWidth(200)
        self.focus_point.hide()
        view_layout.addWidget(self.focus_point)
        view_layout.addStretch()
        self.view_mode.currentIndexChanged.connect(self._arrange_cards)
        self.focus_point.currentIndexChanged.connect(self._arrange_cards)
        layout.addWidget(self.view_bar)
        ai_row = QHBoxLayout()
        ai_row.setSpacing(10)
        self.auto_ai = QCheckBox('自动分析')
        self.auto_ai.setToolTip('开播后分析场景，并持续更新画面描述')
        self.auto_ai.setChecked(True)
        self.context_enabled = QCheckBox('场景辅助评分')
        self.context_enabled.setChecked(True)
        self.context_enabled.setToolTip('根据AI场景证据与烟火变化辅助形成综合核查指数；结果需人工核查。')
        self.context_enabled.toggled.connect(self._toggle_context)
        self.ai_status = QLabel('本机画面分析')
        self.ai_status.setObjectName('muted')
        settings = QPushButton('服务设置')
        settings.clicked.connect(self.ai_settings)
        ai_row.addWidget(self.ai_status)
        ai_row.addStretch()
        self.sound_control = QPushButton('提示音：关闭')
        self.sound_control.setObjectName('quiet')
        self.sound_control.clicked.connect(lambda: self.soundToggleRequested.emit())
        ai_row.addWidget(self.sound_control)
        self.mute_control = QPushButton('静音5分钟')
        self.mute_control.setObjectName('quiet')
        self.mute_control.clicked.connect(lambda: self.muteRequested.emit())
        self.mute_control.hide()
        ai_row.addWidget(self.mute_control)
        self.analysis_options_toggle = QPushButton('分析选项')
        self.analysis_options_toggle.setCheckable(True)
        self.analysis_options_toggle.setObjectName('quiet')
        ai_row.addWidget(self.analysis_options_toggle)
        layout.addLayout(ai_row)
        self.analysis_options = QFrame()
        self.analysis_options.setObjectName('card')
        options = QHBoxLayout(self.analysis_options)
        options.setContentsMargins(16, 8, 16, 8)
        options.addWidget(self.auto_ai)
        options.addWidget(self.context_enabled)
        options.addStretch()
        options.addWidget(settings)
        self.analysis_options.hide()
        self.analysis_options_toggle.toggled.connect(self.analysis_options.setVisible)
        layout.addWidget(self.analysis_options)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(12)
        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_widget)
        self.monitor_scroll = scroll
        self.monitor_scroll.viewport().installEventFilter(self)
        self.event_splitter = QSplitter(Qt.Orientation.Vertical)
        self.event_splitter.setChildrenCollapsible(False)
        self.event_splitter.setHandleWidth(8)
        scroll.setMinimumHeight(160)
        self.event_splitter.addWidget(scroll)
        left_layout.addWidget(self.event_splitter, 1)
        self.event_panel = QWidget()
        event_layout = QVBoxLayout(self.event_panel)
        event_layout.setContentsMargins(0, 0, 0, 0)
        event_layout.setSpacing(8)
        events_heading = QLabel('事件动态')
        self.events_heading = events_heading
        events_heading.setStyleSheet('font-size:16px;font-weight:600;')
        event_heading_row = QHBoxLayout()
        event_heading_row.addWidget(events_heading, 1)
        self.expand_events_button = QPushButton('展开事件区')
        self.expand_events_button.setCheckable(True)
        self.expand_events_button.setObjectName('quiet')
        self.expand_events_button.setToolTip('扩大事件列表，方便选择；也可拖动画面与列表之间的分隔条。')
        self.expand_events_button.toggled.connect(self._set_events_expanded)
        event_heading_row.addWidget(self.expand_events_button)
        event_layout.addLayout(event_heading_row)
        self.workflow_tabs = QTabBar()
        for title, value in (('全部', 'all'), ('待核查', 'review'), ('待处理', 'action'), ('已处理', 'completed')):
            index = self.workflow_tabs.addTab(title)
            self.workflow_tabs.setTabData(index, value)
        self.workflow_tabs.setExpanding(True)
        self.workflow_tabs.currentChanged.connect(self._render_events)
        event_layout.addWidget(self.workflow_tabs)
        filter_row = QHBoxLayout()
        self.event_search = QLineEdit()
        self.event_search.setPlaceholderText('搜索监控点或位置')
        self.pending_only = QCheckBox('仅待核查')
        self.pending_only.hide()
        self.event_search.textChanged.connect(self._render_events)
        self.pending_only.toggled.connect(self._render_events)
        filter_row.addWidget(self.event_search, 1)
        filter_row.addWidget(self.pending_only)
        event_layout.addLayout(filter_row)
        sort_row = QHBoxLayout()
        sort_row.addWidget(QLabel('排序'))
        self.event_sort = QComboBox()
        for caption, mode in (('优先级：高到低', 'priority'), ('发生时间：最新在前', 'time_desc'),
                              ('发生时间：最早在前', 'time_asc'), ('监控点名称', 'device')):
            self.event_sort.addItem(caption, mode)
        self.event_sort.setToolTip('优先级排序：未完成事件优先，再按已保存等级从高到低。时间排序按记录时间与录像位置，不代表实际起火日期。')
        self.event_sort.currentIndexChanged.connect(self._render_events)
        sort_row.addWidget(self.event_sort, 1)
        event_layout.addLayout(sort_row)
        self.event_table = make_table(['序号', '监控点', '发现时间', '核查等级', '处理状态'])
        self.event_table.verticalHeader().setDefaultSectionSize(54)
        self.event_table.setMinimumHeight(130)
        self.event_table.setMaximumHeight(16777215)
        self.event_table.itemSelectionChanged.connect(self._selection_changed)
        self.event_table.itemClicked.connect(self._event_clicked)
        self.event_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.event_table.setColumnWidth(0, 56)
        self.event_table.horizontalHeader().setSectionsClickable(True)
        self.event_table.horizontalHeader().sectionClicked.connect(self._sort_header_clicked)
        self.event_table.setItemDelegateForColumn(4, StatusDelegate(self.event_table))
        event_layout.addWidget(self.event_table, 1)
        self.event_splitter.addWidget(self.event_panel)
        self.event_splitter.setStretchFactor(0, 3)
        self.event_splitter.setStretchFactor(1, 1)
        self.event_splitter.setSizes([600, self._compact_event_height])
        splitter.addWidget(left)
        inspector = QFrame()
        inspector.setObjectName('card')
        inspector.setMinimumWidth(270)
        right = QVBoxLayout(inspector)
        right.setContentsMargins(18, 18, 18, 18)
        right.setSpacing(10)
        self.event_title = QLabel('画面与事件')
        self.event_title.setObjectName('sectionTitle')
        self.event_title.setWordWrap(True)
        right.addWidget(self.event_title)
        self.event_location = QLabel('选择监控点查看详情')
        self.event_location.setWordWrap(True)
        self.event_location.setObjectName('muted')
        right.addWidget(self.event_location)
        self.handling_badge = QLabel()
        self.handling_badge.hide()
        right.addWidget(self.handling_badge)
        self.reopen_button = QPushButton('撤回处理')
        self.reopen_button.setObjectName('quiet')
        self.reopen_button.setStyleSheet('QPushButton {background:#fff7e9;color:#925610;border:1px solid #dfbd83;border-radius:12px;padding:10px 14px;font-size:16px;} QPushButton:hover {background:#ffedcd;}')
        self.reopen_button.setToolTip('撤回完成状态并保留原处理记录')
        self.reopen_button.clicked.connect(lambda: self._save_review_action('reopened'))
        self.reopen_button.hide()
        right.addWidget(self.reopen_button)
        self.conclusion_summary = QLabel()
        self.conclusion_summary.setWordWrap(True)
        right.addWidget(self.conclusion_summary)
        self.handling_notice = QLabel()
        self.handling_notice.setWordWrap(True)
        self.handling_notice.hide()
        right.addWidget(self.handling_notice)
        self.priority_detail = QLabel('')
        self.priority_detail.setWordWrap(True)
        right.addWidget(self.priority_detail)
        self.analysis_summary = AnalysisSummary(self)
        right.addWidget(self.analysis_summary)
        self.scene_details_toggle = QPushButton('查看分析依据')
        self.scene_details_toggle.setCheckable(True)
        self.scene_details_toggle.setObjectName('quiet')
        self.scene_details_toggle.toggled.connect(self._show_selected)
        self.analysis_summary.details_toggle.toggled.connect(self.scene_details_toggle.setChecked)
        self.evidence_holder = QWidget()
        evidence_row = QHBoxLayout(self.evidence_holder)
        evidence_row.setContentsMargins(0, 0, 0, 0)
        self.evidence_buttons = []
        for index in range(3):
            button = QToolButton()
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(70, 40))
            button.setFixedSize(78, 65)
            button.setText('无原画面')
            button.setEnabled(False)
            button.clicked.connect(lambda checked=False, i=index: self.show_evidence(i))
            self.evidence_buttons.append(button)
            evidence_row.addWidget(button)
        evidence_row.addStretch()
        right.addWidget(self.evidence_holder)
        self.evidence_holder.hide()
        scene_heading = QLabel('AI画面描述')
        scene_heading.setStyleSheet('font-size:15px;font-weight:600;color:#3867e8;')
        right.addWidget(scene_heading)
        scene_heading.hide()
        self.scene_text = QTextEdit()
        self.scene_text.setReadOnly(True)
        self.scene_text.setPlaceholderText('开始监测后，在这里查看画面描述。')
        self.scene_text.setMinimumHeight(110)
        self.scene_text.setStyleSheet('QTextEdit {background:#f5f7fc;border:0;border-radius:12px;padding:12px;}')
        right.addWidget(self.scene_text, 1)
        self.scene_text.hide()
        self.priority_detail.hide()
        self.conclusion_summary.hide()
        right.addWidget(self.scene_details_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.scene_details_toggle.hide()
        evidence_actions = QHBoxLayout()
        self.review_ai_button = QPushButton('重新分析')
        self.review_ai_button.clicked.connect(self.review_selected)
        self.open_event_button = QPushButton('回放记录')
        self.open_event_button.clicked.connect(self.open_selected_record)
        evidence_actions.addWidget(self.review_ai_button)
        evidence_actions.addWidget(self.open_event_button)
        right.addLayout(evidence_actions)
        self.review_toggle = QPushButton('记录核查结果')
        self.review_toggle.setObjectName('primary')
        self.review_toggle.setCheckable(True)
        right.addWidget(self.review_toggle)
        self.review_form = QWidget()
        review_layout = QVBoxLayout(self.review_form)
        review_layout.setContentsMargins(0, 0, 0, 0)
        review_layout.setSpacing(8)
        self.review_choice = QComboBox()
        fill_conclusions(self.review_choice)
        review_layout.addWidget(self.review_choice)
        self.review_note = QLineEdit()
        self.review_note.setPlaceholderText('记录人工观察或处理说明')
        self.review_note.setMaxLength(1000)
        review_layout.addWidget(self.review_note)
        self.save_review_button = QPushButton('保存核查结果')
        self.save_review_button.setObjectName('primary')
        self.save_review_button.clicked.connect(self.save_review)
        review_layout.addWidget(self.save_review_button)
        self.resolve_button = QPushButton('标记已处理')
        self.resolve_button.setObjectName('primary')
        self.resolve_button.clicked.connect(lambda: self._save_review_action('resolved'))
        self.resolve_button.hide()
        self.review_status = QLabel('')
        self.review_choice.setToolTip('AI意见用于辅助判断，仍需结合原画面和现场情况核查。')
        self.review_status.setWordWrap(True)
        self.review_status.setObjectName('muted')
        review_layout.addWidget(self.review_status)
        right.addWidget(self.review_form)
        right.addWidget(self.resolve_button)
        self.review_form.hide()
        self.review_toggle.toggled.connect(self.review_form.setVisible)
        self.review_toggle.toggled.connect(lambda opened: self.review_toggle.setText('收起核查表单' if opened else '记录核查结果'))
        self.history_button = QPushButton('查看处理记录')
        self.history_button.clicked.connect(self.show_review_history)
        right.addWidget(self.history_button)
        self.continue_button = QPushButton('继续处理待办')
        self.continue_button.clicked.connect(self.show_pending_work)
        self.continue_button.hide()
        right.addWidget(self.continue_button)
        inspector_scroll = QScrollArea()
        self.inspector_scroll = inspector_scroll
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumWidth(330)
        inspector_scroll.setWidget(inspector)
        splitter.addWidget(inspector_scroll)
        splitter.setSizes([760, 410])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        self.message = QLabel('')
        self.message.setToolTip('本地录像按各自时间分析，最多同时4路。监控点位置由你登记。')
        self.message.setWordWrap(True)
        self.message.setObjectName('muted')
        layout.addWidget(self.message)

    def _device(self, identifier):
        return next((d for d in self.devices if d['id'] == identifier), None)

    def _rebuild_cards(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        self.cards = {}
        if not self.devices:
            empty = QFrame()
            empty.setObjectName('card')
            welcome = QVBoxLayout(empty)
            welcome.setSpacing(16)
            welcome.addStretch()
            symbol = QLabel('+')
            symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
            symbol.setFixedSize(60, 60)
            symbol.setStyleSheet('background:#edf2ff;color:#3867e8;border-radius:22px;font-size:32px;font-weight:400;')
            welcome.addWidget(symbol, 0, Qt.AlignmentFlag.AlignCenter)
            note = QLabel('添加第一个监控点')
            note.setStyleSheet('font-size:20px;font-weight:600;')
            note.setAlignment(Qt.AlignmentFlag.AlignCenter)
            welcome.addWidget(note)
            caption = QLabel('集中查看画面，及时跟进异常')
            caption.setObjectName('muted')
            welcome.addWidget(caption, 0, Qt.AlignmentFlag.AlignCenter)
            add = QPushButton('添加监控点')
            add.setObjectName('primary')
            add.clicked.connect(self.add_point)
            welcome.addWidget(add, 0, Qt.AlignmentFlag.AlignCenter)
            demo = QPushButton('先试用两路示例')
            demo.setObjectName('quiet')
            demo.clicked.connect(self.add_demo_points)
            welcome.addWidget(demo, 0, Qt.AlignmentFlag.AlignCenter)
            welcome.addStretch()
            empty.setMinimumHeight(260)
            self.grid.addWidget(empty, 0, 0)
        for i, device in enumerate(self.devices):
            card = MonitorCard(device)
            card.startRequested.connect(self.start_device)
            card.stopRequested.connect(self.stop_device)
            card.selected.connect(self.select_device_event)
            card.modelRequested.connect(self.configure_model)
            self.grid.addWidget(card, i // 2, i % 2)
            self.cards[device['id']] = card
        self.focus_point.blockSignals(True)
        self.focus_point.clear()
        for device in self.devices:
            self.focus_point.addItem(device['name'], device['id'])
        self.focus_point.blockSignals(False)
        if self.devices:
            self._arrange_cards()
        self._render_summary()

    def eventFilter(self, watched, event):
        scroll = getattr(self, 'monitor_scroll', None)
        if scroll is not None and watched is scroll.viewport() and event.type() == QEvent.Type.Resize:
            if not getattr(self, '_arrange_pending', False):
                self._arrange_pending = True
                self._card_layout_timer.start(0)
        return super().eventFilter(watched, event)

    def _arrange_after_resize(self):
        self._arrange_pending = False
        if not self._closing:
            self._arrange_cards()

    def _arrange_cards(self, *args):
        focus = self.view_mode.currentIndex() == 1
        self.focus_point.setVisible(focus and not self.event_mode)
        self.focus_id = self.focus_point.currentData()
        columns = 2 if self.monitor_scroll.viewport().width() >= 640 else 1
        for index, device in enumerate(self.devices):
            card = self.cards.get(device['id'])
            if card is None:
                continue
            self.grid.removeWidget(card)
            card.setVisible(not focus or device['id'] == self.focus_id)
            self.grid.addWidget(card, 0 if focus else index // columns, 0 if focus else index % columns)

    def _model_name(self, device):
        selected = device.get('model_profile', 'inherit')
        profile = self.analysis_preferences().get('model_profile', 'original') if selected == 'inherit' else selected
        return PROFILE_NAMES.get(profile, str(profile)), profile

    def _can_configure_model(self, identifier):
        worker = self.workers.get(identifier)
        if worker and worker.isRunning():
            return False
        if self.scene_worker and self.scene_worker.event_key[0] == identifier:
            return False
        return not any(key[0] == identifier for key in self.scene_queue)

    def _refresh_model_labels(self):
        for device in self.devices:
            card = self.cards.get(device['id'])
            if card is None:
                continue
            worker = self.workers.get(device['id'])
            running = bool(worker and worker.isRunning())
            name, profile = self._model_name(device)
            if running and hasattr(worker, 'options'):
                profile = worker.options.model_profile
                name = PROFILE_NAMES.get(profile, profile)
            prefix = '本次模型：' if running else '下次模型：' if device['id'] in self.outputs else '检测模型：'
            text = prefix + name + ('（全局）' if not running and device.get('model_profile', 'inherit') == 'inherit' else '')
            if card.model_label.text() != text:
                card.model_label.setText(text)
            card.model_label.setToolTip(profile_details(profile)['composition'] if profile in PROFILE_NAMES else name)
            card.model_button.setEnabled(self._can_configure_model(device['id']))
            card.model_button.setToolTip('选择本路烟火检测模型；AI画面理解使用共享本机视觉模型。' if self._can_configure_model(device['id']) else
                                         '停止本路监测并等待本路AI分析完成后，可修改模型；其他路继续运行。')

    def configure_model(self, identifier):
        device = self._device(identifier)
        if not device:
            return
        if not self._can_configure_model(identifier):
            self.message.setText('请先停止本路监测并等待本路AI分析完成，再选择检测模型。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(device['name'] + ' · 检测模型')
        dialog.resize(650, 340)
        layout = QVBoxLayout(dialog)
        selector = PointModelSelector(device, dialog)
        layout.addWidget(selector)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            try:
                updated = {**device, **selector.value()}
                self.devices = self.registry.save([updated if item['id'] == identifier else item for item in self.devices])
                self.cards[identifier].device = dict(updated)
                self._refresh_model_labels()
                self.message.setText(device['name'] + '的模型设置已保存，下次启动本路监测时生效。')
            except (OSError, ValueError) as exc:
                self.message.setText(str(exc))

    def set_event_mode(self, enabled):
        self.event_mode = bool(enabled)
        if enabled:
            self.selected_scene_locked = False
        self.monitor_scroll.setVisible(not enabled)
        self.expand_events_button.setVisible(not enabled)
        self.event_splitter.setHandleWidth(0 if enabled else 8)
        self.view_bar.setVisible(not enabled)
        for button in self.monitor_actions:
            button.setVisible(not enabled)
        self.events_heading.setText('全部事件' if enabled else '事件动态')
        self.event_table.setMaximumHeight(16777215)
        if not enabled:
            self._event_layout_timer.start(0)
        self._render_events()
        self.viewChanged.emit()

    def _restore_events_layout(self):
        if not self._closing:
            self._set_events_expanded(self._events_expanded)

    def _set_events_expanded(self, expanded):
        self._events_expanded = bool(expanded)
        self.expand_events_button.blockSignals(True)
        self.expand_events_button.setChecked(self._events_expanded)
        self.expand_events_button.setText('收起事件区' if self._events_expanded else '展开事件区')
        self.expand_events_button.blockSignals(False)
        if self.event_mode:
            return
        total = sum(self.event_splitter.sizes()) or self.event_splitter.height()
        lower = round(total * .65) if self._events_expanded else self._compact_event_height
        self.event_splitter.setSizes([max(160, total - lower), min(lower, max(1, total - 160))])

    def _sort_header_clicked(self, section):
        current = self.event_sort.currentData()
        mode = ('time_asc' if current == 'time_desc' else 'time_desc') if section == 2 else 'priority' if section == 3 else 'device' if section == 1 else 'time_asc'
        self.event_sort.setCurrentIndex(self.event_sort.findData(mode))

    def _event_clicked(self, item):
        key = item.data(Qt.ItemDataRole.UserRole)
        if key not in self.events:
            return
        changed = self.selected_scene_locked or key != self.selected_key
        self.selected_scene_locked = False
        self.selected_key = key
        if changed:
            self._review_form_key = None
        self._show_selected()
        self._refresh_card_selection()
        self._set_events_expanded(True)
        self._reveal_analysis()

    def _reveal_analysis(self):
        self.inspector_scroll.verticalScrollBar().setValue(0)
        self._analysis_reveal_timer.start(0)

    def _scroll_to_analysis(self):
        if not self._closing:
            self.inspector_scroll.ensureWidgetVisible(self.analysis_summary.description_card, 0, 12)

    def set_devices(self, devices):
        if self.active:
            raise ValueError('请先停止监测与AI任务，再修改设备清单。')
        self.devices = self.registry.save(devices)
        self._rebuild_cards()

    def add_demo_points(self):
        if self.active or self.devices:
            return
        sources = [ASSET_ROOT / 'videos/fireplace_cc0.webm', ASSET_ROOT / 'videos/engineering_no_fire.mp4']
        if not all(path.is_file() for path in sources):
            self.message.setText('演示录像不完整，请通过“添加监控点”选择自己的录像。')
            return
        self.set_devices([{'name': '监控A · 壁炉', 'location': '演示区域A／本地录像', 'kind': 'file', 'source': str(sources[0])},
                          {'name': '监控B · 无火对照', 'location': '演示区域B／生成的几何录像', 'kind': 'file', 'source': str(sources[1])}])
        self.message.setText('两路示例已添加，点击“开始全部”体验。')
        self.message.setToolTip('示例为本地壁炉与生成的几何录像，验证操作流程，不代表独立场景准确率。')

    def add_point(self):
        if self.active:
            self.message.setText('请先停止当前监测，再添加或编辑监控点。')
            return
        dialog = PointDialog(parent=self)
        if dialog.exec():
            try:
                self.set_devices(self.devices + [dialog.value])
            except (OSError, ValueError) as exc:
                self.message.setText(str(exc))

    def manage_points(self):
        if self.active:
            self.message.setText('请先停止当前监测与AI任务，再管理监控点。')
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('管理监控点')
        dialog.resize(720, 380)
        layout = QVBoxLayout(dialog)
        listing = make_table(['设备', '位置', '输入', '检测模型'])
        def refill():
            listing.setRowCount(len(self.devices))
            for row, device in enumerate(self.devices):
                for col, text in enumerate((device['name'], device['location'], '本地录像' if device['kind'] == 'file' else '摄像头', self._model_name(device)[0])):
                    listing.setItem(row, col, QTableWidgetItem(text))
        refill()
        layout.addWidget(listing)
        buttons = QHBoxLayout()
        edit, remove, close = QPushButton('编辑选中'), QPushButton('移除选中'), QPushButton('关闭')
        def selected():
            row = listing.currentRow()
            return self.devices[row] if 0 <= row < len(self.devices) else None
        def edit_current():
            device = selected()
            if not device:
                return
            form = PointDialog(device, dialog)
            if form.exec():
                try:
                    self.set_devices([form.value if d['id'] == device['id'] else d for d in self.devices])
                    refill()
                except (OSError, ValueError) as exc:
                    self.message.setText(str(exc))
        def remove_current():
            device = selected()
            if device and QMessageBox.question(dialog, '移除监控点', f'从清单移除“{device["name"]}”？已保存录像与事件仍保留。') == QMessageBox.StandardButton.Yes:
                try:
                    self.set_devices([d for d in self.devices if d['id'] != device['id']])
                    refill()
                except (OSError, ValueError) as exc:
                    self.message.setText(str(exc))
        edit.clicked.connect(edit_current)
        remove.clicked.connect(remove_current)
        close.clicked.connect(dialog.accept)
        for button in (edit, remove, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        dialog.exec()

    def ai_settings(self):
        dialog = QDialog(self)
        dialog.setWindowTitle('本机AI复核设置')
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        endpoint, model = QLineEdit(self.endpoint), QLineEdit(self.model)
        form.addRow('本机服务', endpoint)
        form.addRow('视觉模型', model)
        layout.addLayout(form)
        note = QLabel('本版本仅连接本机服务。准备完成后运行 start_local_ai.ps1；AI只作场景辅助复核。')
        note.setWordWrap(True)
        layout.addWidget(note)
        start = QPushButton('启动本机AI服务')
        def start_service():
            script = ASSET_ROOT / 'start_local_ai.ps1'
            if script.is_file():
                QProcess.startDetached('powershell.exe', ['-NoProfile', '-WindowStyle', 'Hidden', '-File', str(script)], str(ASSET_ROOT))
                self.ai_status.setText('正在启动本机AI，模型加载需要片刻')
            else:
                self.ai_status.setText('本地AI启动组件尚未安装')
        start.clicked.connect(start_service)
        layout.addWidget(start)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec():
            try:
                self.endpoint = local_endpoint(endpoint.text())
                if not model.text().strip() or len(model.text()) > 120:
                    raise ValueError('请填写本机视觉模型名称。')
                self.model = model.text().strip()
                self.ai_status.setText('本机模型配置已更新')
            except ValueError as exc:
                self.message.setText(str(exc))

    def start_all(self):
        for device in self.devices:
            self.start_device(device['id'])

    def start_device(self, identifier):
        if self._closing or (identifier in self.workers and self.workers[identifier].isRunning()):
            return
        if sum(w.isRunning() for w in self.workers.values()) >= 4:
            self.message.setText('最多同时分析4路，其他设备可在停止后继续。')
            return
        device = self._device(identifier)
        if not device:
            return
        if device['kind'] == 'file' and not Path(device['source']).is_file():
            self.cards[identifier].status.setText('录像不可用')
            self.message.setText(device['name'] + '：录像文件不存在，请在管理中重新选择。')
            return
        if device['kind'] == 'camera' and any(self._device(k) and self._device(k)['kind'] == 'camera'
                and self._device(k)['source'] == device['source'] and w.isRunning() for k, w in self.workers.items()):
            self.message.setText('该摄像头已经由另一个监控点使用。')
            return
        shared = self.analysis_preferences()
        try:
            selected = resolve_device_analysis(device, shared)
        except ValueError as exc:
            self.cards[identifier].status.setText('模型设置需检查')
            self.message.setText(device['name'] + '：' + str(exc))
            return
        options = AnalysisOptions(source=device['source'], source_kind=device['kind'], device='cpu',
            conf=selected['conf'], model_profile=selected['model_profile'],
            association_profile=selected['association_profile'],
            confirmation_profile=selected['confirmation_profile'], alert_sound=bool(shared.get('alert_sound', False)),
            monitoring_profile=shared.get('monitoring_profile', 'full'), recording_mode='events' if device['kind'] == 'camera' else 'full',
            serialize_inference=True,
            monitoring_point={key: device[key] for key in ('id', 'name', 'location')})
        worker = self.worker_class(options, self)
        if hasattr(worker, 'scene_context'):
            worker.scene_context.enabled = self.context_enabled.isChecked()
        worker.point_id = identifier
        self.workers[identifier] = worker
        self.states[identifier] = '正在加载'
        worker.started_run.connect(self._channel_started, Qt.ConnectionType.QueuedConnection)
        worker.outcome.connect(self._channel_done, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._channel_finished, Qt.ConnectionType.QueuedConnection)
        self.cards[identifier].start_button.setEnabled(False)
        self.cards[identifier].stop_button.setEnabled(True)
        self.cards[identifier].status.setText('正在加载')
        self.cards[identifier].video.clear_frame('正在加载检测模型…')
        worker.start()
        self._render_summary()

    @Slot(dict)
    def _channel_started(self, info):
        self._started(self.sender().point_id, info)

    @Slot(dict)
    def _channel_done(self, result):
        self._done(self.sender().point_id, result)

    @Slot()
    def _channel_finished(self):
        worker = self.sender()
        self._worker_finished(worker.point_id, worker)

    def _started(self, identifier, info):
        self.outputs[identifier] = info['output']
        self.packets.pop(identifier, None)
        if info.get('config'):
            self.run_configs[str(Path(info['output']).resolve())] = info['config']
        self._refresh_model_labels()
        if self.selected_scene_locked and self.selected_key and self.selected_key[0] == identifier:
            self.select_device_scene(identifier)

    def _done(self, identifier, result):
        worker = self.workers.get(identifier)
        if worker:
            self._consume(identifier, worker)
        card = self.cards.get(identifier)
        status = result.get('status')
        label = ('检测失败' if not result.get('ok') else '连接中断' if status == 'camera_disconnected' else
                 '已停止 · 已保存' if status == 'stopped_by_user' else '录像结束 · 已保存')
        self.states[identifier] = label
        if card:
            card.status.setText(label)
            card.video.set_context(('本地录像 · 历史画面' if card.device['kind'] == 'file' else '摄像头已停止 · 历史画面'), stale=False)
        if not result.get('ok'):
            self.message.setText((self._device(identifier) or {}).get('name', '') + '：' + result.get('error', '该路分析失败'))
        for entry in self.events.values():
            if entry['device']['id'] == identifier and entry['output'] == self.outputs.get(identifier):
                entry['closed'] = True
                self._save_closed_context(entry)
        scene_key = (identifier, self.outputs.get(identifier), None)
        if scene_key in self.scenes:
            self.scenes[scene_key]['closed'] = True
        self._render_events()
        self.runSaved.emit()

    def _worker_finished(self, identifier, worker):
        if self.workers.get(identifier) is worker:
            self.workers.pop(identifier, None)
        worker.deleteLater()
        card = self.cards.get(identifier)
        if card:
            card.start_button.setEnabled(True)
            card.stop_button.setEnabled(False)
        self._render_summary()

    def stop_device(self, identifier):
        worker = self.workers.get(identifier)
        if worker and worker.isRunning():
            worker.stop()
            self.cards[identifier].status.setText('正在保存')
            self.cards[identifier].stop_button.setEnabled(False)

    def stop_all(self):
        for identifier in list(self.workers):
            self.stop_device(identifier)

    def _consume(self, identifier, worker):
        new_notices = []
        if hasattr(worker, 'take_notifications'):
            for notice in worker.take_notifications():
                new_notices.append(notice)
                if ('sound' in notice.get('channels', []) and self.sound_allowed()
                        and monotonic() - getattr(self, '_last_sound_at', 0.) >= .7):
                    QApplication.beep()
                    self._last_sound_at = monotonic()
        packet, _ = worker.preview()
        card = self.cards.get(identifier)
        device = self._device(identifier)
        if packet and card:
            self.packets[identifier] = packet
            if device['kind'] == 'camera' and packet.get('frame_age_s', 0) > 1.5:
                card.video.set_context('画面延迟 · 当前现场状态未知', stale=True)
            else:
                card.video.set_context(('本地录像 · 视频时间 ' if device['kind'] == 'file' else '实时采集 · 已处理画面 ') + video_time(packet['timestamp_s']))
            card.video.set_bgr(packet['raw_frame'], packet.get('detections'))
            card.status.setText('录像分析中' if device['kind'] == 'file' else '监测中')
            risk = packet.get('risk') or {}
            if (risk.get('fusion') or {}).get('scene_only_hazard'):
                card.priority.setText('AI异常线索 · 请核查')
                card.priority.setStyleSheet('color:#b66a16;font-weight:700;')
            else:
                action = LEVEL_ACTIONS.get(risk.get('level'), '尚无已确认视觉事件')
                card.priority.setText(action)
            scene_key = (identifier, str(worker.output), None)
            frames = worker.scene_evidence() if hasattr(worker, 'scene_evidence') else []
            if frames:
                context = self.scenes.setdefault(scene_key, {'event_id': None, 'output': str(worker.output),
                    'device': dict(device), 'baseline': None, 'closed': False, 'scene_record': None,
                    'scene_status': '等待开播场景分析', 'reference_s': -float('inf'), 'frames': []})
                context.update(latest_frames=list(frames), last_timestamp_s=packet['timestamp_s'], scene_assessment=risk)
                scene_event = self.events.get(scene_key)
                if scene_event and scene_event.get('origin') == 'ai_scene':
                    scene_event.update(latest_frames=list(frames), last_timestamp_s=packet['timestamp_s'], closed=False)
                    if (risk.get('fusion') or {}).get('scene_only_hazard'):
                        priority = {**risk['fusion'], 'score': risk['score'], 'level': risk['level']}
                        priority.setdefault('reason', (scene_event.get('assessment', {}).get('fusion') or {}).get('reason', '综合结果需要人工核查。'))
                        scene_event['assessment'] = {'score': risk['score'], 'level': risk['level'], 'fusion': priority}
                if not context['frames']:
                    context['frames'] = list(frames)
                active_events = bool((packet.get('risk') or {}).get('events'))
                if (self.auto_ai.isChecked() and not active_events and len(frames) >= 2
                        and frames[-1]['timestamp_s'] - frames[0]['timestamp_s'] >= .8
                        and frames[-1]['timestamp_s'] - context['reference_s'] >= 8.):
                    self.queue_review(scene_key)
            if any(self._is_scene_notice(n) for n in new_notices):
                self._index_scene_notice(device, str(worker.output), new_notices,
                    frames=frames, assessment=risk, closed=False)
        for row in worker.monitor_events():
            key = (identifier, row['output'], row['event_id'])
            if key not in self.events:
                baseline = dict(row.get('assessment') or {})
                if not baseline:
                    baseline = {'score': 25., 'level': 'LOW', 'label': row['label'], 'fresh_evidence': True}
                self.events[key] = {**row, 'device': dict(device), 'baseline': baseline, 'review': 'pending',
                                    'scene_status': '等待复核', 'scene_record': None}
                context = self.scenes.get((identifier, row['output'], None))
                if context and context.get('scene_record'):
                    self.events[key]['scene_record'] = context['scene_record']
                if self.auto_ai.isChecked():
                    self.queue_review(key)
            else:
                current = self.events[key]
                if row.get('latest_notice_id') and row.get('latest_notice_id') != current.get('latest_notice_id'):
                    if current.get('latest_notice_id'):
                        current.update(review='pending', review_comment='', conclusion=None)
                    current['latest_notice_id'] = row['latest_notice_id']
                current.update(assessment=row['assessment'], risk_summary=row['risk_summary'],
                               closed=row['closed'], last_timestamp_s=row['last_timestamp_s'], latest_frames=row.get('latest_frames', row['frames']))
        # Bound the in-memory event board; full records remain on disk.
        while len(self.events) > 200:
            removable = next((k for k, e in self.events.items() if e['closed'] and k not in self._queue_keys
                              and (not self.scene_worker or self.scene_worker.event_key != k)), None)
            if removable is None:
                break
            self.events.pop(removable)

    def refresh(self):
        for identifier, worker in list(self.workers.items()):
            self._consume(identifier, worker)
        if self.auto_ai.isChecked() and not self._closing:
            for key, event in self.events.items():
                if not event['closed'] and event.get('scene_record') and event.get('last_timestamp_s', 0) - event['reference_s'] >= 8:
                    self.queue_review(key)
        self._render_events()
        self._start_next_review()

    def queue_review(self, key):
        event = (self.scenes.get(key) if key[2] is None else None) or self.events.get(key) or self.scenes.get(key)
        if not event or event.get('_placeholder') or (not event['frames'] and not event['closed']) or key in self._queue_keys or (self.scene_worker and self.scene_worker.event_key == key):
            return
        if len(self.scene_queue) >= 12:
            event['scene_status'] = '复核队列已满 · 可稍后手动重试'
            return
        self.scene_queue.append(key)
        self._queue_keys.add(key)
        event['scene_status'] = '已排队'

    def _start_next_review(self):
        if self._closing or self.scene_worker or not self.scene_queue or self.external_scene_busy():
            return
        key = self.scene_queue.pop(0)
        self._queue_keys.discard(key)
        event = (self.scenes.get(key) if key[2] is None else None) or self.events.get(key) or self.scenes.get(key)
        if not event:
            self.event_title.setText('画面与事件')
            self.event_location.setText('设备与位置会显示在这里')
            self.priority_detail.clear()
            self.scene_text.clear()
            self._thumb_key = None
            self.evidence_frames = []
            for button in self.evidence_buttons:
                button.setEnabled(False)
                button.setIcon(QIcon())
                button.setText('无原画面')
            return
        if event.get('latest_frames') and (not event['closed'] or event['event_id'] is None):
            event['frames'] = list(event['latest_frames'])
            event['reference_s'] = event['frames'][-1]['timestamp_s']
            event['baseline'] = dict(event.get('assessment') or event['baseline'] or {}) if event['event_id'] is not None else None
            if event['baseline'] and 'baseline_score' in event['baseline']:
                event['baseline'].update(score=event['baseline']['baseline_score'], level=event['baseline']['baseline_level'])
            context = self.scenes.get(key)
            if context is not None and context is not event:
                context['reference_s'] = event['reference_s']
        event['scene_status'] = 'AI正在分析原画面'
        owner = self.workers.get(event['device']['id'])
        store = owner.scene_context if owner and str(owner.output) == event['output'] and hasattr(owner, 'scene_context') else self.external_context_for_run(event['output'])
        worker = SceneReviewWorker(key, event, event['device'], self.endpoint, self.model, self, context_store=store)
        self.scene_worker = worker
        worker.progress.connect(self._scene_progress, Qt.ConnectionType.QueuedConnection)
        worker.outcome.connect(self._scene_done, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._scene_worker_finished, Qt.ConnectionType.QueuedConnection)
        worker.start()
        self.ai_status.setText('AI分析中' + (f' · 等待{len(self.scene_queue)}项' if self.scene_queue else ''))

    @Slot(dict)
    def _scene_progress(self, value):
        event = self.events.get(tuple(value['key'])) or self.scenes.get(tuple(value['key']))
        if event:
            event['scene_preview'] = value
            scene_key = (event['device']['id'], event['output'], None)
            if scene_key in self.scenes:
                self.scenes[scene_key]['scene_preview'] = value
            card = self.cards.get(event['device']['id'])
            if card and self.outputs.get(event['device']['id']) == event['output']:
                card.scene_description.setText('AI · ' + value['description'])
            if tuple(value['key']) == self.selected_key or (self.selected_scene_locked and scene_key == self.selected_key):
                self._show_selected()

    @Slot(dict)
    def _scene_done(self, result):
        event = self.events.get(result['key']) or self.scenes.get(result['key'])
        if not event:
            return
        if result['ok']:
            if event.get('closed') and event.get('event_id') is not None:
                # A review requested after playback is additional evidence, not
                # permission to replace the score shown at the time of the run.
                event.setdefault('historical_priority', self._priority(event))
            event['scene_record'] = result['record']
            event['scene_status'] = '复核完成'
            if event.get('origin') == 'ai_scene' and result['key'] in self.scenes:
                self.scenes[result['key']].update(scene_record=result['record'], scene_status='复核完成')
            if event['event_id'] is not None:
                self._save_closed_context(event)
            else:
                for row in self.events.values():
                    if row['device']['id'] == event['device']['id'] and row['output'] == event['output'] and not row.get('scene_record'):
                        row['scene_record'] = result['record']
            card = self.cards.get(event['device']['id'])
            if card and self.outputs.get(event['device']['id']) == event['output']:
                card.scene_description.setText('AI · ' + result['record']['scene']['description'])
                card.scene_description.setToolTip(result['record']['scene']['description'])
            record = result['record']
            scene_key = (event['device']['id'], event['output'], None)
            view = self.scenes.get(scene_key)
            if view and self._valid_scene_view_record(record):
                previous_record = view.get('scene_record')
                if not previous_record or record['evidence'][-1]['timestamp_s'] >= previous_record['evidence'][-1]['timestamp_s']:
                    view.update(scene_record=record, scene_status='AI画面分析已更新',
                                reference_s=record['evidence'][-1]['timestamp_s'])
                    view.pop('scene_preview', None)
                    retained = self._scene_view_frames(event['output'], record)
                    if retained:
                        view['frames'] = retained
            total = record.get('total_elapsed_s', record['elapsed_s'])
            first = record.get('first_description_total_s')
            text = f'描述{first:.1f}秒／复核{total:.1f}秒' if first is not None else f'上次耗时{total:.1f}秒'
            if record.get('runtime', {}).get('cache_hit'):
                text = f'复用相同画面结果 · {total:.2f}秒'
            self.ai_status.setText('AI分析完成')
            self.ai_status.setToolTip(text)
        else:
            event['scene_status'] = '已取消' if result.get('cancelled') else result.get('error', 'AI复核失败')
            self.ai_status.setText('AI暂不可用 · 基础检测继续')
        self._render_events()

    @Slot()
    def _scene_worker_finished(self):
        self._scene_finished(self.sender())

    def _scene_finished(self, worker):
        if self.scene_worker is worker:
            self.scene_worker = None
        worker.deleteLater()
        self._render_events()
        self._start_next_review()

    def _save_closed_context(self, event):
        """Keep the final displayed baseline/age reproducible after reopening."""
        if event.get('event_id') is None:
            return
        record = event.get('scene_record')
        if not event['closed'] or not record:
            return
        baseline = dict(event.get('assessment') or event['baseline'])
        if 'baseline_score' in baseline:
            baseline.update(score=baseline['baseline_score'], level=baseline['baseline_level'])
        baseline['label'] = event['label']
        reference = record['evidence'][-1]['timestamp_s']
        timestamp = event.get('last_timestamp_s', reference)
        priority = event.get('historical_priority') or (event.get('assessment') or {}).get('fusion') or {
            'policy': 'historical-detection-v1', 'score': baseline['score'], 'level': baseline['level'],
            'baseline_score': baseline['score'], 'baseline_level': baseline['level'], 'context_delta': 0.,
            'reason': '保留已结束记录的检测结果；后续AI描述供回看核查，不追改当时评分。'}
        snapshot = {**record, 'record_kind': 'end_of_run_context', 'event_id': event['event_id'], 'review_scope': 'event',
                    'baseline': baseline, 'priority': priority,
                    'evidence_reference_s': reference, 'applied_timestamp_s': timestamp}
        path = Path(event['output']) / 'scene-reviews.jsonl'
        if record == snapshot:
            return
        try:
            if path.is_symlink():
                raise ValueError('场景记录路径无效。')
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(snapshot, ensure_ascii=False) + '\n')
            event['scene_record'] = snapshot
        except (OSError, ValueError) as exc:
            self.message.setText('本轮结束快照未能保存：' + str(exc))

    def _priority(self, event):
        assessment = event.get('assessment') or event['baseline']
        if assessment.get('fusion'):
            value = dict(assessment['fusion'])
            if event['closed'] and value.get('policy') in ('context-review-v1', 'scene-fusion-v1'):
                value['historical_joint'] = True
            return value
        base = {'score': assessment.get('score', 25.), 'level': assessment.get('level', 'LOW'),
                'label': event['label'], 'fresh_evidence': assessment.get('fresh_evidence', True),
                'features': assessment.get('features') or {}}
        record = event.get('scene_record')
        if not record or not self.context_enabled.isChecked():
            return {**base, 'baseline_score': base['score'], 'baseline_level': base['level'], 'context_delta': 0., 'reason': event['scene_status']}
        if event['closed'] and record and record.get('priority'):
            value = dict(record['priority'])
            if value.get('policy') in ('context-review-v1', 'scene-fusion-v1'):
                value['historical_joint'] = True
            return value
        if event['closed']:
            return {**base, 'baseline_score': base['score'], 'baseline_level': base['level'],
                    'context_delta': 0., 'historical_joint': True,
                    'reason': '保留已保存的当时指数；AI画面描述用于回看，不追改历史评分。'}
        age = max(0., event.get('last_timestamp_s', event['reference_s']) - event['reference_s'])
        return contextual_priority(base, record['scene'], frame_count=record['frame_count'], span_s=record['span_s'], age_s=age)

    @staticmethod
    def _is_scene_notice(notice):
        return notice.get('event_id') is None and (
            notice.get('kind') == 'scene_attention' or
            notice.get('origin') == 'ai_scene' and notice.get('kind') in ('level_upgrade', 'level_downgrade'))

    @staticmethod
    def _notification_for(event, notices):
        return next((row for row in reversed(list(notices)) if row.get('event_id') == event['event_id']
                     and row.get('kind') != 'event_closed'
                     and (event.get('origin') != 'ai_scene' or MonitorCenter._is_scene_notice(row))), None)

    def _index_scene_notice(self, device, output, notices, feedback=None, *, record=None, frames=None,
                            assessment=None, closed=True):
        """Index a real composite scene notification without a fake YOLO event."""
        notices = [row for row in notices if self._is_scene_notice(row)]
        if not notices:
            return
        latest = notices[-1]
        key = (device['id'], str(output), None)
        prior = self.events.get(key, {})
        same_notice = prior.get('latest_notice_id') == latest.get('notification_id')
        human = (feedback or {}).get(latest.get('notification_id'), {})
        if feedback is not None:
            state = event_review_state(None, notices, feedback)
        else:
            state = prior.get('review', 'pending') if same_notice else 'pending'
        context = self.scenes.get(key) or {}
        if record is None:
            record = context.get('scene_record') or prior.get('scene_record')
        if record is None and (Path(output) / 'scene-reviews.jsonl').is_file():
            from core.fusion_dataset import scene_record_key
            records = read_jsonl_tail(Path(output) / 'scene-reviews.jsonl', 500)
            record = next((item for item in reversed(records) if item.get('event_id') is None
                           and (not latest.get('scene_record_key') or scene_record_key(item) == latest['scene_record_key'])), None)
        stamp = latest.get('timestamp_s', latest.get('evidence_reference_s', 0.))
        score = latest.get('score')
        if score is None:
            score = (assessment or {}).get('score', (prior.get('assessment') or {}).get('score', 0.))
        level = latest.get('level', (assessment or {}).get('level', 'MEDIUM'))
        reason = latest.get('message') or latest.get('description') or '视频场景出现异常线索，综合结果需要人工核查。'
        priority = {'policy': 'context-score-v2', 'score': score, 'level': level,
                    'baseline_score': 0., 'baseline_level': 'NORMAL', 'context_delta': score,
                    'reason': reason, 'scene_only_hazard': True}
        if not frames and record:
            import hashlib, re
            frames = []
            for evidence in record.get('evidence', [])[:3]:
                relative = evidence.get('image', '')
                if re.fullmatch(r'scene-evidence/scene-[0-9a-f]{20}\.jpg', relative):
                    image = Path(output) / relative
                    if image.is_file() and not image.is_symlink():
                        data = image.read_bytes()
                        if hashlib.sha256(data).hexdigest() == evidence.get('sha256'):
                            frames.append({'timestamp_s': evidence['timestamp_s'], 'sha256': evidence['sha256'], 'jpeg': data})
        self.events[key] = {**prior, 'event_id': None, 'origin': 'ai_scene', 'label': 'scene',
            'device': dict(device), 'output': str(output), 'confirmed_at_s': stamp,
            'reference_s': latest.get('evidence_reference_s', stamp),
            'last_timestamp_s': (assessment or {}).get('timestamp_s', stamp), 'closed': closed,
            'baseline': {'score': 0., 'level': 'NORMAL'},
            'assessment': {'score': score, 'level': level, 'fusion': priority},
            'review': 'pending' if state == 'unavailable' else state,
            'review_comment': human.get('review_comment', human.get('comment', prior.get('review_comment', '') if same_notice else '')),
            'conclusion': human.get('conclusion', prior.get('conclusion') if same_notice else None),
            'latest_notice_id': latest.get('notification_id'), 'scene_notice': latest,
            'scene_record': record, 'scene_status': '综合异常提示 · 待核查',
            'frames': list(frames or context.get('frames') or prior.get('frames') or [])}

    def _show_evidence_thumbnails(self, event):
        frames = event.get('frames', []) if event else []
        thumb_key = (self.selected_key, tuple(f['sha256'] for f in frames))
        if thumb_key != self._thumb_key:
            self._thumb_key = thumb_key
            self.evidence_frames = list(frames)[:3]
            for index, button in enumerate(self.evidence_buttons):
                if index < len(self.evidence_frames):
                    frame = self.evidence_frames[index]
                    image = QImage.fromData(frame['jpeg'], 'JPG')
                    button.setIcon(QIcon(QPixmap.fromImage(image)))
                    button.setText(f'{frame["timestamp_s"]:.2f}秒')
                    button.setToolTip('查看本次复核使用的未标注原画面')
                    button.setEnabled(True)
                else:
                    button.setIcon(QIcon())
                    button.setText('无原画面')
                    button.setEnabled(False)

    def _scene_attention(self, event):
        """A scene-only cue is visible without inventing a detector event."""
        packet = self.packets.get(event['device']['id']) or {}
        if self.outputs.get(event['device']['id']) == event['output'] and packet.get('risk'):
            return bool((packet['risk'].get('fusion') or {}).get('scene_only_hazard'))
        record = event.get('scene_record')
        if not record or not self.context_enabled.isChecked():
            return False
        evidence = record.get('evidence') or []
        if not evidence or record.get('frame_count', 0) < 2 or record.get('span_s', 0) < .8:
            return False
        age = event.get('last_timestamp_s', evidence[-1]['timestamp_s']) - evidence[-1]['timestamp_s']
        scene = record.get('scene') or {}
        return bool((event.get('closed') or 0 <= age <= 12.) and
                    (scene.get('hazard_cues') or scene.get('spread') == 'observed'))
    def _render_summary(self):
        self._refresh_model_labels()
        count = sum(w.isRunning() for w in self.workers.values())
        visible = self._events_for_view()
        counts = {key: sum(phase(e['review']) == key for e in visible.values()) for key in ('pending', 'action', 'completed')}
        self.summary.setText((f'最近 {len(visible)} 件事件 · {counts["pending"] + counts["action"]}件待办' if self.event_mode else
                            f'{len(self.devices)}个监控点 · {count}路监测中 · {counts["pending"] + counts["action"]}件待办'))
        for index, (title, amount) in enumerate((('全部', len(visible)), ('待核查', counts['pending']),
                                                ('待处理', counts['action']), ('已处理', counts['completed']))):
            self.workflow_tabs.setTabText(index, f'{title} {amount}')
        signature = tuple((key, row['review']) for key, row in self.events.items())
        if signature != getattr(self, '_workload_signature', None):
            self._workload_signature = signature
            self.viewChanged.emit()

    def _events_for_view(self):
        if self.event_mode:
            return self.events
        registered = {device['id'] for device in self.devices}
        return {key: event for key, event in self.events.items() if key[0] in registered
                and (not self.outputs.get(key[0]) or key[1] == self.outputs[key[0]])}

    def _render_events(self, *args):
        self._render_summary()
        selected = self.selected_key
        projected = {key: {**event, 'priority': self._priority(event)} for key, event in self._events_for_view().items()}
        keys = sorted_event_keys(projected, self.event_sort.currentData() or 'priority')
        chronological = enumerate_event_keys(self.events)
        for key in sorted(chronological, key=chronological.get):
            if key not in self._event_numbers:
                self._event_numbers[key] = self._next_event_number
                self._next_event_number += 1
        numbers = self._event_numbers
        if len(numbers) > 2000:
            for key in list(numbers):
                if key not in self.events:
                    numbers.pop(key)
                    if len(numbers) <= 1000:
                        break
        query = self.event_search.text().strip().lower()
        selected_phase = self.workflow_tabs.tabData(self.workflow_tabs.currentIndex())
        keys = [k for k in keys if (not query or query in (self.events[k]['device']['name'] + ' ' + self.events[k]['device']['location']).lower())
                and matches(self.events[k]['review'], selected_phase)
                and (not self.pending_only.isChecked() or self.events[k]['review'] not in REVIEW_COMPLETE)]
        if selected not in keys and selected not in self.scenes and not self.selected_scene_locked:
            selected = self.selected_key = None
        table = self.event_table
        vertical, horizontal = table.verticalScrollBar(), table.horizontalScrollBar()
        scroll_value, horizontal_value = vertical.value(), horizontal.value()
        top_row = table.rowAt(0)
        top_item = table.item(top_row, 0) if top_row >= 0 else None
        top_key = top_item.data(Qt.ItemDataRole.UserRole) if top_item else None
        top_offset = table.rowViewportPosition(top_row) if top_item else 0
        old_keys = [table.item(row, 0).data(Qt.ItemDataRole.UserRole) if table.item(row, 0) else None
                    for row in range(table.rowCount())]
        order_changed = old_keys != keys
        current = table.currentItem()
        current_key = current.data(Qt.ItemDataRole.UserRole) if current else None
        needs_selection = order_changed or current_key != selected or not table.selectedItems()
        self.event_table.blockSignals(True)
        if order_changed:
            self.event_table.clearSelection()
            self.event_table.setCurrentCell(-1, -1)
            self.event_table.setRowCount(len(keys))
        for row, key in enumerate(keys):
            event = self.events[key]
            priority = projected[key]['priority']
            status = presentation(event['review'])
            subject = '场景异常线索' if event.get('origin') == 'ai_scene' else '火焰' if event['label'] == 'fire' else '烟雾'
            values = (f'{numbers[key]:03d}', event['device']['name'] + '\n' + event['device']['location'],
                      video_time(event['confirmed_at_s']) + ' · ' + subject,
                      ('当时：' if status['phase'] == 'completed' else '') + LEVEL_ACTIONS[priority['level']],
                      status['label'] + ('\n' + status['conclusion'] if status['conclusion'] and event['review'] != 'resolved' else ''))
            for col, text in enumerate(values):
                existing = self.event_table.item(row, col)
                if (existing is not None and existing.text() == text
                        and existing.data(Qt.ItemDataRole.UserRole) == key):
                    continue
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(text)
                if col == 4:
                    paint_status_cell(item, event['review'])
                elif col == 3 and status['phase'] == 'completed':
                    from PySide6.QtGui import QColor
                    item.setForeground(QColor('#65778f'))
                self.event_table.setItem(row, col, item)
            if self.event_table.rowHeight(row) != 64:
                self.event_table.setRowHeight(row, 64)
            if key == selected and not self.selected_scene_locked and needs_selection:
                self.event_table.selectRow(row)
        # Re-selecting a current row scrolls Qt's view to that row. Background
        # refresh must instead leave the user's scrollbar/drag in control. When
        # rows move, keep the first visible event anchored where possible.
        if order_changed and top_key in keys and not vertical.isSliderDown():
            anchor_row = keys.index(top_key)
            scroll_value = (vertical.value() + table.rowViewportPosition(anchor_row) - top_offset
                if table.verticalScrollMode() == QTableWidget.ScrollMode.ScrollPerPixel else anchor_row)
        vertical.setValue(scroll_value)
        horizontal.setValue(horizontal_value)
        self.event_table.blockSignals(False)
        if selected is None and keys and not self.selected_scene_locked:
            self.event_table.selectRow(0)
            self._selection_changed()
        self._show_selected()
        self._refresh_card_selection()
        for identifier, card in self.cards.items():
            candidates = [e for e in self.events.values() if e['device']['id'] == identifier
                          and e['output'] == self.outputs.get(identifier)]
            if candidates:
                priority = max((self._priority(e) for e in candidates), key=lambda p: (('LOW', 'MEDIUM', 'HIGH').index(p['level']), p['score']))
                card.priority.setText(LEVEL_ACTIONS[priority['level']])
                card.priority.setToolTip(priority['reason'] + '\n综合核查指数不代表火灾概率。')
                card.priority.setStyleSheet(f'color:{LEVEL_COLORS[priority["level"]]};font-weight:700;')
                unfinished = [e for e in candidates if phase(e['review']) != 'completed']
                if not unfinished and not self.workers.get(identifier):
                    card.priority.setText('当时提示：' + LEVEL_ACTIONS[priority['level']])
                    card.priority.setStyleSheet('color:#65778f;')
                work = 'confirmed_abnormal' if any(phase(e['review']) == 'action' for e in unfinished) else 'pending' if unfinished else 'resolved'
                paint_status(card.work_state, work)
                card.work_state.setText(card.work_state.text() + f' · {len(unfinished) if unfinished else len(candidates)}件')
                card.work_state.show()
            else:
                context = self.scenes.get((identifier, self.outputs.get(identifier), None)) or {}
                if context and self._scene_attention(context):
                    card.priority.setText(('历史AI异常线索' if context.get('closed') else 'AI异常线索') + ' · 请核查')
                    card.priority.setStyleSheet('color:#b66a16;font-weight:700;')
                    card.priority.setToolTip('AI提供了异常线索；烟火检测尚未确认事件，请回看原画面。')
                elif context.get('closed'):
                    card.priority.setText('无确认事件')
                card.work_state.hide()

    def _selection_changed(self):
        row = self.event_table.currentRow()
        item = self.event_table.item(row, 0) if row >= 0 else None
        key = item.data(Qt.ItemDataRole.UserRole) if item else None
        if key is None and self.selected_scene_locked:
            return
        was_scene = self.selected_scene_locked
        if key is not None:
            self.selected_scene_locked = False
        if key != self.selected_key or was_scene:
            self.selected_key = key
            event = self.events.get(key)
            self.review_choice.setCurrentIndex(max(0, self.review_choice.findData(event['review'])) if event else 0)
            self.review_note.setText(event.get('review_comment', '') if event else '')
        self._show_selected()
        self._refresh_card_selection()

    def _selected_item(self):
        if self.selected_scene_locked:
            return self.scenes.get(self.selected_key) or self.events.get(self.selected_key)
        return self.events.get(self.selected_key) or self.scenes.get(self.selected_key)

    def _show_selected(self):
        event = self._selected_item()
        has_event = bool(event and (event.get('event_id') is not None or event.get('origin') == 'ai_scene'))
        self.handling_badge.setVisible(has_event)
        self.history_button.setEnabled(has_event)
        self.resolve_button.setVisible(bool(has_event and phase(event['review']) == 'action'))
        self.reopen_button.setVisible(bool(has_event and phase(event['review']) == 'completed'))
        if has_event:
            self.reopen_button.setText(reopen_label(event['review'], event))
        if has_event:
            paint_status(self.handling_badge, event['review'])
            label = conclusion_label(event['review'], event)
            self.conclusion_summary.setText('核查结论：' + label if label else '')
            form_key = (self.selected_key, event.get('latest_notice_id'))
            if form_key != getattr(self, '_review_form_key', None):
                self._review_form_key = form_key
                choice = event.get('conclusion') if event['review'] == 'resolved' else event['review']
                self.review_choice.setCurrentIndex(max(0, self.review_choice.findData(choice)))
                self.review_note.setText(event.get('review_comment', ''))
                self.handling_notice.hide()
            self.save_review_button.setText('更新核查结果' if event['review'] not in ('pending', 'acknowledged') else '保存核查结果')
        else:
            self.handling_notice.hide()
            self.conclusion_summary.clear()
            self.review_toggle.setChecked(False)
        self.continue_button.setVisible(bool(has_event and phase(event['review']) == 'completed'))
        self._show_evidence_thumbnails(event)
        expanded = self.scene_details_toggle.isChecked()
        self.scene_details_toggle.setEnabled(bool(event))
        self.scene_details_toggle.setText('收起分析依据' if expanded else '查看分析依据')
        self.evidence_holder.setVisible(bool(event and event.get('frames') and expanded))
        self.review_toggle.setEnabled(has_event)
        self.review_form.setVisible(bool(has_event and self.review_toggle.isChecked()))
        for widget in (self.review_ai_button, self.open_event_button, self.save_review_button):
            widget.setEnabled(bool(event))
        if event and event.get('_placeholder'):
            self.review_ai_button.setEnabled(False)
        if not event:
            self.event_title.setText('画面与事件')
            self.event_location.setText('设备与位置会显示在这里')
            self.priority_detail.clear()
            self.scene_text.clear()
            self.analysis_summary.clear_content()
            return
        if event.get('event_id') is None and event.get('origin') != 'ai_scene':
            self.event_title.setText(event['device']['name'] + ' · 视频场景')
            self.event_location.setText(event['device']['location'])
            attention = self._scene_attention(event)
            self.priority_detail.setText(('历史AI异常线索 · 待核查' if event.get('closed') else 'AI异常线索 · 待核查')
                                         if attention else '尚无已确认事件')
            self.priority_detail.setToolTip('AI异常线索供人工核对，尚未形成烟火检测确认事件。' if attention else '')
            self.priority_detail.setStyleSheet('color:#b66a16;font-size:16px;font-weight:600;' if attention else 'color:#78869a;font-size:16px;')
            record = event.get('scene_record')
            preview = event.get('scene_preview')
            self.scene_text.setPlainText(record['scene']['description'] + '\n\n待核对：' + record['scene']['next_check'] if record else
                                        '初步描述 · 完整复核中\n' + preview['description'] if preview else event['scene_status'])
            self.open_event_button.setEnabled(False)
            self.save_review_button.setEnabled(False)
            self.review_ai_button.setEnabled(self._scene_can_review(event))
            self._update_analysis_summary(event)
            return
        priority = self._priority(event)
        sequence = self._event_numbers.get(self.selected_key)
        suffix = f' {sequence:03d}' if sequence is not None else f' #{event["event_id"]}' if event.get('event_id') is not None else ''
        self.event_title.setText(event['device']['name'] + (' · 综合异常提示' if event.get('origin') == 'ai_scene' else ' · 事件') + suffix)
        self.event_location.setText(event['device']['location'] + '\n' + ('本地录像 · 非实时现场' if event['device']['kind'] == 'file' else '摄像头记录')
                                    + ' · ' + video_time(event['confirmed_at_s']))
        self.priority_detail.setText(('历史综合核查指数 ' if priority.get('historical_joint') else '综合核查指数 ')
            + f'{priority["score"]:.1f} · ' + LEVEL_ACTIONS[priority['level']])
        self.priority_detail.setToolTip(priority['reason'] + '\n综合核查指数不代表火灾概率。')
        self.priority_detail.setStyleSheet(f'color:{LEVEL_COLORS[priority["level"]]};font-size:17px;font-weight:600;')
        if phase(event['review']) == 'completed':
            self.priority_detail.setText('当时提示：' + LEVEL_ACTIONS[priority['level']] + f' · {priority["score"]:.1f}')
            self.priority_detail.setStyleSheet('color:#65778f;font-size:16px;')
        record = event.get('scene_record')
        preview = event.get('scene_preview')
        reviewing = self.scene_worker and self.scene_worker.event_key == self.selected_key
        if preview and reviewing:
            text = '正在分析\n\n' + preview['description']
            if expanded:
                text += f'\n\n已等待{preview["elapsed_s"]:.1f}秒；当前为初步描述，完整结果仍在复核。'
        elif record:
            scene = record['scene']
            text = (f'画面描述\n{scene["description"]}\n\n场景：{SCENES[scene["scene"]]}\n\n'
                    + '可见证据\n' + ('\n'.join('• ' + s for s in scene['visible_evidence']) or '证据不足')
                    + '\n\n异常线索\n' + ('\n'.join('• ' + s for s in scene['hazard_cues']) or '未列出明确异常线索，不代表安全')
                    + '\n\n不确定处\n' + ('\n'.join('• ' + s for s in scene['uncertainties']) or '仍需人工核查')
                    + '\n\n下一步核对\n' + scene['next_check']
                    + '\n\n证据时间：' + ' / '.join(f'{f["timestamp_s"]:.2f}s' for f in record['evidence'])
                    + f'\n{record["model"]} · {record["elapsed_s"]:.1f}秒')
            if not expanded:
                text = scene['description'] + '\n\n综合研判依据\n' + priority['reason'] + '\n\n建议核对\n' + scene['next_check']
            else:
                text += ('\n\n综合评分依据\n' + priority['reason']
                    + f'\n检测原始指数：{priority["baseline_score"]:.1f}'
                    + f'\n场景辅助影响：{priority["context_delta"]:+.1f}'
                    + f'\n综合核查指数：{priority["score"]:.1f}')
        else:
            text = (('初步描述 · 未完成完整复核\n' + preview['description'] + '\n\n') if preview else '')
            notice = event.get('scene_notice') or {}
            if event.get('origin') == 'ai_scene':
                text += notice.get('description') or notice.get('scene_description') or notice.get('message') or event['scene_status']
                text += '\n\n综合研判依据\n' + priority['reason']
            else:
                text += event['scene_status']
        if self.scene_text.toPlainText() != text:
            self.scene_text.setPlainText(text)
        self._update_analysis_summary(event, priority)
        self.open_event_button.setEnabled(event['closed'] and (Path(event['output']) / 'summary.json').is_file())

    def _update_analysis_summary(self, event, priority=None):
        if event.get('_placeholder'):
            self.analysis_summary.set_content(event['scene_status'], '等待监测', None,
                [('检测模型', self._model_name(event['device'])[0])], [], status='尚未开始本路监测',
                details=[('监控位置', event['device'].get('location', '未登记')), ('AI模型', self.model)])
            return
        record = event.get('scene_record') or {}
        scene = record.get('scene') or {}
        preview = event.get('scene_preview') or {}
        if priority is None:
            current = self.packets.get(event['device']['id'], {}).get('risk') or {}
            if current and event.get('output') == self.outputs.get(event['device']['id']):
                priority = {**(current.get('fusion') or {}), 'score': current.get('score', 0.),
                            'level': current.get('level', 'NORMAL'),
                            'reason': '当前整路视频的综合研判；点击事件可记录人工核查。'}
            elif event.get('scene_assessment'):
                saved = event['scene_assessment']
                priority = {**(saved.get('fusion') or {}), 'score': saved.get('score', 0.),
                            'level': saved.get('level', 'NORMAL'),
                            'reason': '已保存的整路视频研判，不追改当时结果。'}
            else:
                priority = {'score': 0., 'level': 'NORMAL', 'reason': '尚无确认的烟火事件。'}
        score, level = priority.get('score'), priority.get('level', 'NORMAL')
        reviewing = bool(preview and self.scene_worker and (self.scene_worker.event_key == self.selected_key or
            self.selected_scene_locked and self.scene_worker.event_key[:2] == self.selected_key[:2]))
        description = (preview.get('description') if reviewing else scene.get('description')) or (event.get('scene_notice') or {}).get('description') or '等待完整AI画面分析。'
        has_event = event.get('event_id') is not None or event.get('origin') == 'ai_scene'
        status = presentation(event.get('review', 'pending'))['label'] if has_event else '视频场景 · 持续观察'
        historical = event.get('closed') or priority.get('historical_joint') or (has_event and phase(event.get('review')) == 'completed')
        conclusion = '持续观察' if level == 'NORMAL' else LEVEL_ACTIONS.get(level, '等待分析')
        if reviewing:
            status += ' · AI初步描述，完整分析中'
        assessment = event.get('assessment') or event.get('baseline') or {}
        features = assessment.get('features') or {}
        metrics = []
        score_rows = []
        if score is not None:
            score_rows.append(('辅助评分（规则分）', f'{score:.1f} / 100'))
        if priority.get('baseline_score') is not None:
            score_rows.append(('原始检测评分', f'{priority["baseline_score"]:.1f}'))
        if priority.get('context_delta') is not None:
            score_rows.append(('场景影响', f'{priority["context_delta"]:+.1f}'))
        subject = {'fire': '火焰特征', 'smoke': '烟雾特征', 'scene': '场景异常线索'}.get(event.get('label'), '视频场景')
        metrics.append(('识别线索', subject))
        if features.get('positive_duration_s') is not None:
            metrics.append(('持续时间', f'{features["positive_duration_s"]:.1f} 秒'))
        if features.get('coverage_ratio_smoothed') is not None:
            metrics.append(('画面占比', f'{features["coverage_ratio_smoothed"]:.1%}'))
        if has_event:
            metrics.append(('人工结论', conclusion_label(event.get('review'), event) or '尚未提交'))
        path = str(Path(event['output']).resolve())
        if path not in self.run_configs:
            try:
                self.run_configs[path] = json.loads((Path(path) / 'config.json').read_text(encoding='utf-8'))
            except (OSError, ValueError):
                self.run_configs[path] = {}
        config = self.run_configs[path]
        model_name = config.get('model_profile_name') or PROFILE_NAMES.get(config.get('model_profile'))
        if not model_name:
            model_name = '旧记录未登记' if event.get('closed') else self._model_name(event['device'])[0]
        metrics.append(('检测模型', model_name))
        reasons = [('当时评分依据：' if historical else '') + priority.get('reason', '')]
        if priority.get('context_delta', 0.) > 0:
            reasons.extend(scene.get('hazard_cues', [])[:2])
        details = score_rows + [('监控位置', event['device'].get('location', '未登记')),
                                ('AI模型', record.get('model') or self.model)]
        if event.get('event_id') is not None:
            details.append(('原检测事件编号', f'#{event["event_id"]}'))
        confidence = features.get('confidence')
        valid_confidence = (type(confidence) in (int, float) and math.isfinite(confidence) and 0 <= confidence <= 1
            and features.get('fresh_evidence', assessment.get('fresh_evidence', True)) is True
            and event.get('origin') != 'ai_scene')
        details.extend([('检测匹配分', f'{confidence:.2f} / 1' if valid_confidence else '暂无有效检测值'),
                        ('匹配分含义', '模型对烟火类别的输出分数，不是现场发生火灾的概率。')])
        if record.get('evidence'):
            details.append(('AI证据时间', ' — '.join(video_time(f['timestamp_s']) for f in (record['evidence'][0], record['evidence'][-1]))))
        if record.get('elapsed_s') is not None:
            details.append(('AI分析耗时', f'{record["elapsed_s"]:.1f} 秒'))
        composition = (config.get('scene_selection') or {}).get('composition')
        if composition:
            details.append(('模型组成', composition))
        details.extend([('AI异常线索', '；'.join(scene.get('hazard_cues', [])) or '未记录明确异常线索'),
                        ('可见证据', '；'.join(scene.get('visible_evidence', [])) or '尚未记录'),
                        ('建议核对', scene.get('next_check') or '请结合原画面核查。')])
        self.analysis_summary.set_content(description, conclusion, score, metrics, [s for s in reasons if s],
            status=status, uncertainties=scene.get('uncertainties', [])[:2], details=details,
            level='NORMAL' if historical else level, priority_level=level, historical=bool(historical))

    def select_device_scene(self, identifier):
        device = self._device(identifier)
        if not device:
            return
        output = self.outputs.get(identifier)
        if not output:
            candidates = [key[1] for key in (*self.scenes, *self.events) if key[0] == identifier and key[1]]
            output = max(candidates) if candidates else ''
        key = (identifier, str(output), None)
        for old_key in list(self.scenes):
            if old_key != key and old_key[0] == identifier and self.scenes[old_key].get('_placeholder'):
                self.scenes.pop(old_key)
        worker = self.workers.get(identifier)
        running = bool(worker and worker.isRunning())
        context = self.scenes.setdefault(key, {'event_id': None, 'device': dict(device), 'output': str(output),
            'baseline': None, 'closed': not running, 'scene_record': None, 'frames': [],
            'reference_s': -float('inf'), 'last_timestamp_s': 0.,
            'scene_status': '等待本路AI画面分析' if output else '开始监测后，将自动显示本路AI画面理解。',
            '_placeholder': not bool(output)})
        context['closed'] = not running
        # A camera click opens its latest scene, even when its latest full AI
        # observation came from a confirmed target rather than a general review.
        observations = [context.get('scene_record')]
        observations.extend(event.get('scene_record') for event_key, event in self.events.items()
                            if event_key[0] == identifier and event_key[1] == str(output))
        if output:
            observations.extend(read_jsonl_tail(Path(output) / 'scene-reviews.jsonl', 500))
        current_time = (self.packets.get(identifier) or {}).get('timestamp_s') if running else None
        valid = [record for record in observations if self._valid_scene_view_record(record)
                 and (current_time is None or record['evidence'][-1]['timestamp_s'] <= current_time)]
        if valid:
            latest = max(valid, key=lambda record: record['evidence'][-1]['timestamp_s'])
            context.update(scene_record=latest, reference_s=latest['evidence'][-1]['timestamp_s'],
                           scene_status='已保存的AI画面分析' if not running else 'AI画面分析已更新')
            retained = self._scene_view_frames(output, latest)
            if retained:
                context['frames'] = retained
        if output and 'scene_assessment' not in context:
            try:
                saved = json.loads((Path(output) / 'fusion-latest.json').read_text(encoding='utf-8'))
                if isinstance(saved, dict):
                    context['scene_assessment'] = saved
            except (OSError, ValueError):
                pass
        self.selected_key = key
        self.selected_scene_locked = True
        self._review_form_key = None
        self.event_table.blockSignals(True)
        self.event_table.clearSelection()
        self.event_table.setCurrentCell(-1, -1)
        self.event_table.blockSignals(False)
        self._show_selected()
        self._refresh_card_selection()
        self._reveal_analysis()

    @staticmethod
    def _valid_scene_view_record(record):
        from core.scene_review import validate_scene
        try:
            if not isinstance(record, dict) or not isinstance(record.get('evidence'), list):
                return False
            frames = record['evidence']
            stamps = [item['timestamp_s'] for item in frames]
            if (not frames or type(record.get('frame_count')) is not int or record['frame_count'] != len(frames)
                    or any(type(t) not in (int, float) or not math.isfinite(t) or t < 0 for t in stamps)
                    or stamps != sorted(set(stamps)) or type(record.get('span_s')) not in (int, float)
                    or not math.isclose(record['span_s'], stamps[-1] - stamps[0], abs_tol=.002)):
                return False
            validate_scene(record['scene'], len(frames), record['span_s'])
            return True
        except (KeyError, TypeError, ValueError):
            return False

    @staticmethod
    def _scene_view_frames(output, record):
        import hashlib, re
        retained = []
        root = Path(output).resolve()
        for item in record.get('evidence', [])[:3]:
            relative = item.get('image', '')
            if not isinstance(relative, str) or not re.fullmatch(r'scene-evidence/(?:event-\d+|scene)-[0-9a-f]{20}\.jpg', relative):
                continue
            path = root / relative
            if path.is_file() and not path.is_symlink() and path.stat().st_size <= 8 * 1024 * 1024:
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() == item.get('sha256'):
                    retained.append({**item, 'jpeg': data})
        return retained

    @staticmethod
    def _scene_can_review(event):
        if event.get('_placeholder') or not event.get('output'):
            return False
        if event.get('latest_frames') or event.get('frames'):
            return True
        device = event['device']
        return (event.get('closed') and device.get('kind') == 'file' and
                Path(device.get('source', '')).is_file() and (Path(event['output']) / 'summary.json').is_file())

    def select_device_event(self, identifier):
        # Kept for older buttons and integrations; a card always opens the scene.
        self.select_device_scene(identifier)

    def _refresh_card_selection(self):
        identifier = self.selected_key[0] if self.selected_key else None
        for point_id, card in self.cards.items():
            selected = point_id == identifier
            if card.property('selected') != selected:
                card.setProperty('selected', selected)
                card.style().unpolish(card)
                card.style().polish(card)
                card.update()

    def _toggle_context(self, enabled):
        for worker in self.workers.values():
            if hasattr(worker, 'scene_context'):
                worker.scene_context.enabled = enabled
        self._render_events()

    def show_evidence(self, index):
        if not 0 <= index < len(self.evidence_frames):
            return
        frame = self.evidence_frames[index]
        dialog = QDialog(self)
        dialog.setWindowTitle(f'AI复核原画面 · {frame["timestamp_s"]:.2f}秒')
        layout = QVBoxLayout(dialog)
        image = QLabel()
        image.setPixmap(QPixmap.fromImage(QImage.fromData(frame['jpeg'], 'JPG')))
        layout.addWidget(image)
        layout.addWidget(QLabel('来自该事件的原画面；此窗口不显示其他监控点的当前画面。'))
        dialog.exec()

    def review_selected(self):
        if self.selected_key:
            self.queue_review(self.selected_key)
            self._start_next_review()

    def save_review(self):
        action = self.review_choice.currentData()
        self._save_review_action(action)

    def _save_review_action(self, action):
        event = self.events.get(self.selected_key)
        if not event or not action:
            self.review_status.setText('请先选择人工核查结论。')
            return
        if event['review'] == 'resolved' and action == event.get('conclusion'):
            action = 'resolved'
        if action == 'resolved' and event['review'] not in ('confirmed_abnormal', 'resolved'):
            self.review_status.setText('请先确认异常，再登记处理完成。')
            return
        try:
            notices = read_jsonl_tail(Path(event['output']) / 'alerts.jsonl', 10000)
            notice = self._notification_for(event, notices)
            if not notice:
                raise ValueError('该事件提示尚未保存，请稍后重试。')
            feedback = append_feedback(event['output'], notice, action, '' if action == 'reopened' else self.review_note.text())
            current_action = feedback.get('review_action') or action
            event.update(review=current_action, review_comment=feedback.get('review_comment', ''),
                         conclusion=feedback.get('conclusion'), latest_notice_id=notice['notification_id'])
            self._review_form_key = None
            style = presentation(current_action)
            self.pending_only.setChecked(False)
            self.selected_key = (event['device']['id'], event['output'], event['event_id'])
            self.workflow_tabs.setCurrentIndex({'pending': 1, 'action': 2, 'completed': 3}[style['phase']])
            self.review_status.setText(('已撤回 · ' if action == 'reopened' else '已保存 · ') + style['label'])
            self._render_events()
            self.handling_notice.setText(('已撤回，返回' if action == 'reopened' else '已保存：') + style['label'] + ('。可在“已处理”列表查看和复核。' if style['phase'] == 'completed' else '，继续跟进此事件。'))
            self.handling_notice.setStyleSheet(f'color:{style["color"]};font-size:16px;font-weight:600;')
            self.handling_notice.show()
            self.reviewChanged.emit(event['output'])
        except (OSError, ValueError) as exc:
            self.review_status.setText(str(exc))
            if action == 'reopened' and isinstance(exc, ValueError):
                self.refresh_feedback(event['output'])
            self.handling_notice.setText('未能保存：' + str(exc))
            self.handling_notice.setStyleSheet('color:#b82f48;font-size:16px;font-weight:600;')
            self.handling_notice.show()

    def show_review_history(self):
        event = self.events.get(self.selected_key)
        if event:
            show_handling_history(self, event['output'], event['event_id'])

    def show_pending_work(self):
        self.pending_only.setChecked(False)
        self.workflow_tabs.setCurrentIndex(1 if any(phase(e['review']) == 'pending' for e in self.events.values()) else 2)

    def refresh_feedback(self, run_path):
        path = str(Path(run_path).resolve())
        notices = read_jsonl_tail(Path(path) / 'alerts.jsonl', 10000)
        feedback = {row['notification_id']: row for row in read_jsonl_tail(Path(path) / 'feedback.jsonl', 5000) if 'notification_id' in row}
        for event in self.events.values():
            if str(Path(event['output']).resolve()) != path:
                continue
            state = event_review_state(event['event_id'], notices, feedback)
            notice = self._notification_for(event, notices) or {}
            saved = feedback.get(notice.get('notification_id'), {})
            event.update(review='pending' if state == 'unavailable' else state,
                         review_comment=saved.get('review_comment', saved.get('comment', '')),
                         conclusion=saved.get('conclusion'), latest_notice_id=notice.get('notification_id'))
        self._review_form_key = None
        self._render_events()

    def refresh_scene_records(self, run_path):
        import hashlib, re
        root = Path(run_path).resolve()
        records = {row.get('event_id'): row for row in read_jsonl_tail(root / 'scene-reviews.jsonl', 500)}
        for event in self.events.values():
            if Path(event['output']).resolve() != root:
                continue
            record = records.get(event['event_id']) or records.get(None)
            if not record:
                continue
            frames = []
            for item in record.get('evidence', [])[:3]:
                relative = item.get('image', '')
                if not re.fullmatch(r'scene-evidence/(?:event-\d+|scene)-[0-9a-f]{20}\.jpg', relative):
                    continue
                path = root / relative
                if path.is_file() and not path.is_symlink():
                    data = path.read_bytes()
                    if hashlib.sha256(data).hexdigest() == item.get('sha256'):
                        frames.append({'timestamp_s': item['timestamp_s'], 'sha256': item['sha256'], 'jpeg': data})
            event.update(scene_record=record, scene_status='复核完成')
            if frames:
                event.update(frames=frames, reference_s=frames[-1]['timestamp_s'])
        self._render_events()

    def index_source_events(self, run_path, config, events, notices, feedback, frames=None):
        """Index active single-file analysis in the same run-scoped event board."""
        import hashlib
        path = str(Path(run_path).resolve())
        point = dict(config.get('monitoring_point') or {})
        if not point.get('id'):
            point = {'id': 'source' + hashlib.sha256(path.encode('utf-8')).hexdigest()[:20],
                     'name': Path(config.get('source', '')).name or '录像分析', 'location': '录像分析'}
        device = {'kind': config.get('source_kind', 'file'), 'source': config.get('source', ''), **point}
        for event in events:
            key = (point['id'], path, event['event_id'])
            notice = next((row for row in reversed(list(notices)) if row.get('event_id') == event['event_id'] and row.get('kind') != 'event_closed'), {})
            human = feedback.get(notice.get('notification_id'), {})
            risk = event.get('risk_summary') or {}
            base = event.get('assessment') or {'score': risk.get('peak_score', 25.), 'level': risk.get('peak_level', 'LOW'),
                'label': event['label'], 'fresh_evidence': True, 'features': {}}
            row = self.events.get(key, {})
            latest_frames = list((frames or {}).get(event['event_id'], row.get('latest_frames', [])))
            saved_frames = row.get('frames', []) if row.get('scene_record') else latest_frames
            row.update(event_id=event['event_id'], device=device, output=path, label=event['label'],
                confirmed_at_s=event['confirmed_at_s'], reference_s=event.get('confirmed_at_s', 0.),
                last_timestamp_s=event.get('closed_at_s', event.get('confirmed_at_s', 0.)),
                closed=event.get('state') == 'closed' or 'closed_at_s' in event, baseline=base, assessment=base,
                risk_summary=risk, review=event_review_state(event['event_id'], notices, feedback),
                review_comment=human.get('review_comment', human.get('comment', '')), conclusion=human.get('conclusion'),
                latest_notice_id=notice.get('notification_id'), frames=saved_frames, latest_frames=latest_frames)
            row.setdefault('scene_record', None)
            row.setdefault('scene_status', '录像场景分析')
            self.events[key] = row
        self._index_scene_notice(device, path, notices, feedback, frames=(frames or {}).get(None),
                                 closed=not (Path(path) / '.active').exists())

    def open_selected_record(self):
        event = self.events.get(self.selected_key)
        if event and event['closed'] and (Path(event['output']) / 'summary.json').is_file():
            self.openSavedEvent.emit(event['output'], event['event_id'])

    def shutdown(self):
        self._closing = True
        self._card_layout_timer.stop()
        self._event_layout_timer.stop()
        self._analysis_reveal_timer.stop()
        self.scene_queue.clear()
        self._queue_keys.clear()
        self.stop_all()
        if self.scene_worker and self.scene_worker.isRunning():
            self.scene_worker.requestInterruption()
        if self.active:
            return False
        self.timer.stop()
        return True

    def restore_saved_events(self, listings):
        registered = {d['id']: d for d in self.devices}
        for listing in listings[:100]:
            path = str(listing['output'])
            config = listing.get('config') or {}
            point = dict(config.get('monitoring_point') or {})
            if not point.get('id'):
                import hashlib
                point = {'id': 'source' + hashlib.sha256(path.encode('utf-8')).hexdigest()[:20],
                         'name': Path(config.get('source', '')).name or '录像分析',
                         'location': '录像分析' if config.get('source_kind') != 'camera' else '摄像头记录'}
            known = registered.get(point['id']) or {}
            point = {'kind': config.get('source_kind', 'file'), 'source': config.get('source', ''), **known, **point}
            try:
                record = load_run(path)
                if point['id'] in registered and point['id'] not in self.outputs:
                    self.outputs[point['id']] = path
                feedback = {r['notification_id']: r for r in record['feedback']}
                reviews = {r['event_id']: r for r in read_jsonl_tail(Path(path) / 'scene-reviews.jsonl', 500)}
                general = reviews.get(None)
                scene_key = (point['id'], path, None)
                if general and scene_key not in self.scenes:
                    from core.scene_review import validate_scene
                    validate_scene(general['scene'], general['frame_count'], general['span_s'])
                    self.scenes[scene_key] = {'event_id': None, 'output': path, 'device': dict(point),
                        'baseline': None, 'closed': True, 'scene_record': general, 'frames': [],
                        'reference_s': general['evidence'][-1]['timestamp_s'], 'scene_status': '已加载历史视频场景'}
                    if point['id'] not in self.outputs:
                        self.outputs[point['id']] = path
                        if point['id'] in self.cards:
                            self.cards[point['id']].scene_description.setText('历史AI场景：' + general['scene']['description'])
                for event in record['events'][-200:]:
                    key = (point['id'], path, event['event_id'])
                    if key in self.events:
                        continue
                    risk = event.get('risk_summary') or {}
                    baseline = {'score': risk.get('peak_score', 25.), 'level': risk.get('peak_level', 'LOW'),
                                'label': event['label'], 'fresh_evidence': True, 'features': (risk.get('peak_evidence') or {}).get('features', {})}
                    scene_record = reviews.get(event['event_id'])
                    frames = []
                    if scene_record:
                        from core.scene_review import validate_scene
                        validate_scene(scene_record['scene'], scene_record['frame_count'], scene_record['span_s'])
                        import hashlib, re
                        for evidence in scene_record.get('evidence', [])[:3]:
                            relative = evidence.get('image', '')
                            if re.fullmatch(r'scene-evidence/(?:event-\d+|scene)-[0-9a-f]{20}\.jpg', relative):
                                image = Path(path) / relative
                                if image.is_file() and not image.is_symlink():
                                    data = image.read_bytes()
                                    if hashlib.sha256(data).hexdigest() == evidence['sha256']:
                                        frames.append({'timestamp_s': evidence['timestamp_s'], 'sha256': evidence['sha256'], 'jpeg': data})
                        baseline = scene_record.get('baseline') or baseline
                    device = dict(point)
                    state = event_review_state(event['event_id'], record['notifications'], feedback)
                    final_assessment = next((row for row in (record.get('fusion') or {}).get('events', [])
                                             if row.get('event_id') == event['event_id']), None)
                    notice = next((row for row in reversed(record['notifications']) if row.get('event_id') == event['event_id'] and row.get('kind') != 'event_closed'), {})
                    human = feedback.get(notice.get('notification_id'), {})
                    self.events[key] = {'event_id': event['event_id'], 'device': device, 'output': path,
                        'label': event['label'], 'confirmed_at_s': event['confirmed_at_s'],
                        'reference_s': scene_record.get('evidence_reference_s', event['confirmed_at_s']) if scene_record else event['confirmed_at_s'],
                        'last_timestamp_s': scene_record.get('applied_timestamp_s', event['confirmed_at_s']) if scene_record else event['confirmed_at_s'],
                        'closed': True, 'baseline': baseline, 'assessment': final_assessment or baseline, 'risk_summary': risk,
                        'review': state if state != 'unavailable' else 'pending', 'frames': frames,
                        'review_comment': human.get('review_comment', human.get('comment', '')),
                        'conclusion': human.get('conclusion'), 'latest_notice_id': notice.get('notification_id'),
                        'scene_record': scene_record, 'scene_status': '已加载历史AI复核' if scene_record else '历史事件 · 可打开完整记录'}
                    if len(self.events) >= 200:
                        break
                self._index_scene_notice(point, path, record['notifications'], feedback,
                    record=general, closed=True)
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if len(self.events) >= 200:
                break
        self._render_events()
