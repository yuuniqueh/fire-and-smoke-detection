"""A single-video desktop workflow with explicit running and archived states."""
from pathlib import Path
from time import monotonic
from datetime import datetime
from shiboken6 import isValid

from PySide6.QtCore import QTimer, Qt, QUrl, QSize
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                              QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                              QMainWindow, QMessageBox, QProgressBar, QPushButton, QSpinBox,
                              QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                              QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from core.pipeline import AnalysisOptions
from core.model_profiles import (PROFILE_NAMES, SCENE_PROFILES, COMPARISON_PROFILES, CANDIDATE_PATH, MODERN_PATH, profile_details,
                                 profile_availability)
from core.event_profiles import ASSOCIATION_NAMES
from core.confirmation import CONFIRMATION_NAMES
from core.records import append_feedback, event_review_state, list_runs, load_run, render_review_report, REVIEW_COMPLETE
from core.replay import review_media, review_window
from core.runtime import ASSET_ROOT, ROOT
from core.preferences import DEFAULTS, PreferencesStore
from ui.widgets import CoverageTrend, EventTimeline, RiskCurve, VideoView
from ui.product import LEVEL_NAMES, LEVEL_COLORS, STYLE, video_time, ui_font, record_label
from ui.model_picker import choice_label, ModelInfoDialog
from core.alert_presentation import LEVEL_ACTIONS, LEVEL_BACKGROUNDS, LEVEL_BORDERS, notice_title
from ui.worker import AnalysisWorker, CameraPreviewWorker, DiagnosticsWorker, ExportWorker, HistoryWorker, StorageWorker
from ui.icons import icon
from core.review_workflow import phase, matches, presentation, conclusion_label, reopen_label
from ui.review_controls import paint_status, paint_status_cell, StatusDelegate, show_handling_history

CLASS_NAMES = {'fire': '火焰', 'smoke': '烟雾', 'scene': '场景异常线索'}
COMPONENT_NAMES = {'class_base': '确认类别', 'box_coverage': '检测框覆盖', 'positive_duration': '持续观察',
                   'positive_growth': '覆盖增长', 'model_confidence': '检测匹配分', 'scene_context': 'AI场景修正'}
STATUS_NAMES = {'completed': '分析完成', 'completed_with_frame_count_warning': '分析完成 · 视频信息需核对',
                'stopped_by_user': '已停止 · 当前结果已保存', 'frame_limit_reached': '已达到帧数限制', 'failed': '分析失败',
                'camera_disconnected': '摄像头已断开 · 请重新连接', 'storage_limit_reached': '存储上限或磁盘不足 · 已停止'}
CLOSE_NAMES = {'stream_end': '视频结束', 'stopped_by_user': '手动停止', 'detection_timeout': '检测超时',
               'processing_error': '处理异常', 'frame_limit_reached': '帧数限制',
               'camera_disconnected': '采集中断', 'storage_limit_reached': '磁盘空间不足'}
TREND_NAMES = {'ready': '可进行短期外推', 'warming_up': '正在积累观测',
               'temporarily_missing': '检测暂缺 · 暂停外推', 'unstable': '观测不稳定 · 暂停外推'}
MONITOR_NAMES = {'full': '逐帧分析', 'idle': '低负载巡检', 'enhanced': '异常增强分析'}
FEEDBACK_NAMES = {'acknowledged': '已知悉 · 仍待核查', 'false_positive': '人工标为疑似误报',
                  'needs_verification': '需进一步核实 · 待跟进', 'uncertain': '无法判断 · 待跟进',
                  'normal_use': '人工判为正常用火', 'confirmed_abnormal': '人工确认异常 · 待处理', 'resolved': '人工登记已处理',
                  'reopened': '已撤回 · 重新跟进'}
REVIEW_NAMES = {'pending': '待核查', 'unavailable': '待核查 · 无提示详情', **FEEDBACK_NAMES}

def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    widget.verticalHeader().setDefaultSectionSize(64)
    widget.setShowGrid(False)
    return widget


class MainWindow(QMainWindow):
    def __init__(self, settings_path=None):
        super().__init__()
        QApplication.setFont(ui_font())
        self.setFont(ui_font())
        self.setWindowTitle('焰鉴 · 火情监测')
        self.setMinimumSize(980, 740)
        self.resize(1440, 900)
        self.setStyleSheet(STYLE)
        self.task = None
        self.current_run = None
        self.run_config = {}
        self.replays = []
        self.events = {}
        self.history = []
        self.closing = False
        self.last_error = None
        self.notifications = {}
        self.feedback = {}
        self.active_notification = None
        self.latest_trend = None
        self.trend_historical = False
        self.last_sound_at = -float('inf')
        self.muted_until = 0
        self._last_frame_clock = ''
        self._timeline_time = 0
        self._historical = False
        self.inline_replay = None
        self._last_live_frame_at = None
        self.storage_plan = None
        self.storage_records = []
        self._loading_preferences = True
        self._preferences_dirty = False
        self.preferences = None if settings_path is False else PreferencesStore(settings_path or ROOT / '.cache' / 'desktop-settings.json')
        self._build()
        self.preferences_timer = QTimer(self)
        self.preferences_timer.setSingleShot(True)
        self.preferences_timer.setInterval(500)
        self.preferences_timer.timeout.connect(self._save_preferences)
        values, message = self.preferences.load() if self.preferences else (dict(DEFAULTS), '本次为验证模式，不保存设置。')
        self._preferences_bad = message.startswith('设置未能恢复')
        self._apply_preferences(values)
        self.settings_status.setText(message)
        self._loading_preferences = False
        for combo in (self.device, self.model_profile, self.association_profile, self.monitoring_profile, self.confirmation_profile):
            combo.currentIndexChanged.connect(self._settings_changed)
        self.confidence.valueChanged.connect(self._settings_changed)
        self.realtime.toggled.connect(self._settings_changed)
        self.alert_sound.toggled.connect(self._settings_changed)
        self.recording_mode.currentIndexChanged.connect(self._storage_settings_changed)
        self.storage_max_gb.valueChanged.connect(self._storage_settings_changed)
        self.storage_days.valueChanged.connect(self._storage_settings_changed)
        self.storage_automatic.toggled.connect(self._storage_settings_changed)
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._refresh_preview)
        self.timer.start()
        self.refresh_history()
        self._set_controls()
        self.input_kind.setCurrentIndex(self.input_kind.findData('camera'))
        self.navigate('center')

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(190)
        self.sidebar_layout = QVBoxLayout(sidebar)
        self.sidebar_layout.setContentsMargins(14, 26, 14, 18)
        self.sidebar_layout.setSpacing(6)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(6, 0, 0, 0)
        brand_symbol = QLabel()
        brand_symbol.setPixmap(icon('brand', size=40).pixmap(40, 40))
        brand_row.addWidget(brand_symbol)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand = QLabel('焰鉴')
        brand.setObjectName('brand')
        brand_text.addWidget(brand)
        brand_caption = QLabel('FireGuard')
        brand_caption.setObjectName('brandCaption')
        brand_text.addWidget(brand_caption)
        brand_row.addLayout(brand_text)
        brand_row.addStretch()
        self.sidebar_layout.addLayout(brand_row)
        self.sidebar_layout.addSpacing(28)
        shell.addWidget(sidebar)
        workspace = QWidget()
        shell.addWidget(workspace, 1)
        layout = QVBoxLayout(workspace)
        layout.setContentsMargins(24, 22, 24, 16)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        self.page_title = QLabel('视频分析')
        self.page_title.setObjectName('pageTitle')
        heading.addWidget(self.page_title)
        heading.addStretch()
        self.state = QLabel('待连接')
        self.state.setWordWrap(True)
        self.state.setObjectName('statePill')
        self.state.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading.addWidget(self.state)
        self.pending_summary = QLabel('待核查 0 条')
        self.pending_summary.setObjectName('pendingPill')
        heading.addWidget(self.pending_summary)
        layout.addLayout(heading)
        self.page_subtitle = QLabel('选择已有录像，查看火焰和烟雾的检测结果')
        self.page_subtitle.setObjectName('muted')
        self.page_subtitle.setWordWrap(True)
        layout.addWidget(self.page_subtitle)
        self.setup_card = QFrame()
        self.setup_card.setObjectName('setupCard')
        setup_layout = QVBoxLayout(self.setup_card)
        setup_layout.setContentsMargins(16, 14, 16, 14)
        setup_layout.setSpacing(10)
        toolbar = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setReadOnly(True)
        self.source.setPlaceholderText('请选择本地视频')
        self.source.setMinimumWidth(100)
        self.open_button = QPushButton('选择视频')
        self.open_button.setIcon(icon('folder', '#64748b', 18))
        self.open_button.clicked.connect(self.choose_video)
        self.demo_button = QPushButton('试用示例')
        self.demo_button.setObjectName('quiet')
        self.demo_button.clicked.connect(lambda: self.set_source(ASSET_ROOT / 'videos' / 'fireplace_cc0.webm'))
        self.start_button = QPushButton('开始分析')
        self.start_button.setObjectName('primary')
        self.start_button.setIcon(icon('play', '#ffffff', 17))
        self.start_button.clicked.connect(self.start_analysis)
        self.stop_button = QPushButton('停止')
        self.stop_button.setObjectName('stop')
        self.stop_button.clicked.connect(self.stop_analysis)
        for widget in (self.source, self.open_button, self.demo_button, self.start_button, self.stop_button):
            toolbar.addWidget(widget, 1 if widget is self.source else 0)
        setup_layout.addLayout(toolbar)
        self.input_kind = QComboBox(root)
        self.input_kind.addItem('本地视频', 'file')
        self.input_kind.addItem('摄像头 / 安卓手机', 'camera')
        self.camera_index = QSpinBox()
        self.camera_index.setRange(0, 15)
        self.camera_index.setPrefix('摄像头编号 ')
        self.camera_index.setToolTip('0 通常为默认摄像头；手机虚拟摄像头可能为 1、2 等。先连接预览核对画面。')
        self.preview_button = QPushButton('连接预览')
        self.preview_button.clicked.connect(self.start_camera_preview)
        self.camera_help = QPushButton('连接帮助')
        self.camera_help.clicked.connect(self.show_camera_help)
        self.input_status = QLabel('本地视频按原视频时间分析')
        self.input_status.setObjectName('muted')
        self.input_status.setWordWrap(True)
        self.input_kind.hide()
        self.camera_index.setParent(root)
        self.camera_index.hide()
        self.camera_device = QComboBox()
        for index in range(16):
            self.camera_device.addItem(f'摄像头 {index}', index)
        self.camera_device.setToolTip('先连接预览核对画面；手机虚拟摄像头可选择其他设备编号。')
        self.camera_device.currentIndexChanged.connect(self.camera_index.setValue)
        self.camera_index.valueChanged.connect(self.camera_device.setCurrentIndex)
        toolbar.insertWidget(0, self.camera_device, 1)
        toolbar.insertWidget(1, self.preview_button)
        toolbar.addWidget(self.camera_help)
        scene_row = QHBoxLayout()
        self.scene_caption = QLabel('检测场景')
        scene_row.addWidget(self.scene_caption)
        self.scene_combo = QComboBox()
        self.scene_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.scene_combo.setMinimumContentsLength(12)
        for profile in SCENE_PROFILES:
            self.scene_combo.addItem(choice_label(profile), profile)
        self.scene_combo.insertSeparator(self.scene_combo.count())
        for profile in COMPARISON_PROFILES:
            self.scene_combo.addItem('高级对照 · ' + choice_label(profile), profile)
        scene_row.addWidget(self.scene_combo, 1)
        self.scene_info_button = QPushButton('模型介绍')
        self.scene_info_button.setText('方案说明')
        self.scene_info_button.setObjectName('quiet')
        self.scene_info_button.clicked.connect(self.show_current_model_info)
        scene_row.addWidget(self.scene_info_button)
        self.compare_models_button = QPushButton('更多模型')
        self.compare_models_button.setText('选择方案')
        self.compare_models_button.clicked.connect(self.open_model_picker)
        scene_row.addWidget(self.compare_models_button)
        self.model_file_status = QLabel('')
        self.model_file_status.setWordWrap(True)
        scene_row.addWidget(self.model_file_status)
        setup_layout.addLayout(scene_row)
        self.scene_note = QLabel('')
        self.scene_note.setObjectName('muted')
        self.scene_note.setWordWrap(True)
        setup_layout.addWidget(self.scene_note)
        self.input_kind.currentIndexChanged.connect(self._input_changed)
        self.alert_banner = QLabel('开始检测后，检测事件会显示在这里')
        self.alert_banner.setObjectName('alertBanner')
        self.alert_banner.setWordWrap(True)
        self.alert_banner.hide()
        self.error_panel = QWidget()
        error_layout = QHBoxLayout(self.error_panel)
        error_layout.setContentsMargins(0, 0, 0, 0)
        self.error_message = QLabel()
        self.error_message.setWordWrap(True)
        self.error_message.setStyleSheet('color:#b52e40;')
        self.cpu_retry = QPushButton('使用兼容模式重试')
        self.cpu_retry.setToolTip('切换为 CPU 分析，速度可能较慢。')
        self.cpu_retry.clicked.connect(self.retry_with_cpu)
        self.dismiss_error = QPushButton('收起')
        self.dismiss_error.clicked.connect(self.error_panel.hide)
        error_layout.addWidget(self.error_message, 1)
        error_layout.addWidget(self.cpu_retry)
        error_layout.addWidget(self.dismiss_error)
        layout.addWidget(self.error_panel)
        self.error_panel.hide()
        settings_page = QWidget()
        settings_layout = QVBoxLayout(settings_page)
        settings_layout.setContentsMargins(0, 0, 0, 0)
        settings_layout.setSpacing(16)
        common_card = QFrame()
        common_card.setObjectName('panel')
        common_container = QVBoxLayout(common_card)
        common_container.setContentsMargins(22, 22, 22, 22)
        common_container.setSpacing(18)
        settings_title = QLabel('播放与提醒')
        settings_title.setStyleSheet('font-size:20px;font-weight:700;')
        common_container.addWidget(settings_title)
        common_layout = QVBoxLayout()
        common_layout.setSpacing(18)
        common_container.addLayout(common_layout)
        settings_layout.addWidget(common_card)
        self.advanced_toggle = QPushButton('高级设置')
        self.advanced_toggle.setObjectName('quiet')
        self.advanced_toggle.setCheckable(True)
        settings_layout.addWidget(self.advanced_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.advanced_settings = QFrame()
        self.advanced_settings.setObjectName('panel')
        advanced_layout = QVBoxLayout(self.advanced_settings)
        advanced_layout.setContentsMargins(18, 18, 18, 18)
        advanced_layout.setSpacing(16)
        self.advanced_settings.hide()
        self.advanced_toggle.toggled.connect(self.advanced_settings.setVisible)
        self.advanced_toggle.toggled.connect(lambda opened: self.advanced_toggle.setText('收起高级分析选项' if opened else '展开高级分析选项'))
        settings_layout.addWidget(self.advanced_settings)
        options = QHBoxLayout()
        self.device = QComboBox()
        for label, value in [('自动选择设备', 'auto'), ('仅 CPU', 'cpu'), ('GPU 0', '0')]:
            self.device.addItem(label, value)
        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(0.01, 1.0)
        self.confidence.setSingleStep(0.05)
        self.confidence.setDecimals(2)
        self.confidence.setValue(0.35)
        self.confidence.setToolTip('检测匹配分阈值，取值0—1；表示模型对烟火目标的匹配程度，不是火灾发生概率。')
        self.realtime = QCheckBox('按原视频速度播放')
        self.realtime.setToolTip('处理性能允许时，按原视频速度显示。')
        self.realtime.setChecked(True)
        options.addWidget(QLabel('推理设备'))
        options.addWidget(self.device)
        options.addSpacing(15)
        options.addWidget(QLabel('检测阈值'))
        options.addWidget(self.confidence)
        options.addSpacing(15)
        common_layout.addWidget(self.realtime)
        options.addStretch()
        advanced_layout.addLayout(options)
        model_options = QHBoxLayout()
        model_options.addWidget(QLabel('检测方案'))
        self.model_profile = QComboBox()
        for value, label in PROFILE_NAMES.items():
            self.model_profile.addItem(label, value)
        self.model_profile.setMinimumWidth(190)
        model_options.addWidget(self.model_profile)
        self.model_note = QLabel('原模型：保留现有检测与时序设置。')
        self.model_note.setObjectName('muted')
        self.model_note.setWordWrap(True)
        model_options.addWidget(self.model_note, 1)
        advanced_layout.addLayout(model_options)
        self.model_profile.currentIndexChanged.connect(self._profile_changed)
        self.scene_combo.currentIndexChanged.connect(self._scene_changed)

        association_options = QHBoxLayout()
        association_options.addWidget(QLabel('事件关联'))
        self.association_profile = QComboBox()
        for value, label in ASSOCIATION_NAMES.items():
            self.association_profile.addItem(label, value)
        self.association_profile.setMinimumWidth(190)
        association_options.addWidget(self.association_profile)
        association_note = QLabel('稳定区域候选：合并同一处火焰的局部检测；用于候选检测组合，确认时间条件不变。')
        association_note.setObjectName('muted')
        association_note.setWordWrap(True)
        association_options.addWidget(association_note, 1)
        advanced_layout.addLayout(association_options)
        confirmation_options = QHBoxLayout()
        confirmation_options.addWidget(QLabel('确认速度'))
        self.confirmation_profile = QComboBox()
        for value in ('standard', 'fast'):
            self.confirmation_profile.addItem(CONFIRMATION_NAMES[value], value)
        confirmation_options.addWidget(self.confirmation_profile)
        confirmation_note = QLabel('快速档：同一区域近 0.5 秒检测持续率至少 90%，至少 4 次观测且采样间隔不超过 0.2 秒。'
                                  '断续检测仍走标准通道；旧开发视频回放发现误报增加，请按场景选择。')
        confirmation_note.setWordWrap(True)
        confirmation_note.setObjectName('muted')
        confirmation_options.addWidget(confirmation_note, 1)
        advanced_layout.addLayout(confirmation_options)
        monitoring_options = QHBoxLayout()
        monitoring_options.addWidget(QLabel('监测方式'))
        self.monitoring_profile = QComboBox()
        self.monitoring_profile.addItem('逐帧分析 · 基准方式', 'full')
        self.monitoring_profile.addItem('低负载巡检 · 异常时增强', 'adaptive')
        monitoring_options.addWidget(self.monitoring_profile)
        self.alert_sound = QCheckBox('新告警时播放提示音')
        self.alert_sound.setToolTip('出现橙色或红色的新提示时播放声音。')
        common_layout.addWidget(self.alert_sound)
        self.test_sound_button = QPushButton('试听提示音')
        self.test_sound_button.clicked.connect(self.test_alert_sound)
        common_layout.addWidget(self.test_sound_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.sound_test_result = QLabel('')
        self.sound_test_result.setObjectName('muted')
        self.sound_test_result.setWordWrap(True)
        self.sound_test_result.hide()
        common_layout.addWidget(self.sound_test_result)
        monitoring_options.addStretch()
        advanced_layout.addLayout(monitoring_options)
        monitoring_note = QLabel('低负载方式在巡检时减少模型调用；发现候选区域后提高输入尺寸并持续分析，'
                                 '恢复平稳后回到巡检。该方式可能错过短暂事件，节省的是推理负载，尚未测量实际功耗。')
        monitoring_note.setObjectName('muted')
        monitoring_note.setWordWrap(True)
        advanced_layout.addWidget(monitoring_note)
        sound_note = QLabel('提示方式：界面横幅、提示日志、可选本机提示音。人工知悉与疑似误报反馈会写入本次记录，'
                            '不会自动修改检测结果或训练模型。')
        sound_note.setWordWrap(True)
        sound_note.setObjectName('muted')
        sound_note.hide()
        preference_actions = QHBoxLayout()
        maintenance_card = QFrame()
        maintenance_card.setObjectName('panel')
        maintenance = QVBoxLayout(maintenance_card)
        maintenance.setContentsMargins(22, 20, 22, 20)
        maintenance.setSpacing(16)
        maintenance_title = QLabel('应用设置')
        maintenance_title.setObjectName('sectionTitle')
        maintenance.addWidget(maintenance_title)
        self.save_settings = QPushButton('保存当前设置')
        self.save_settings.clicked.connect(lambda: self._save_preferences(explicit=True))
        self.reset_settings = QPushButton('恢复默认设置')
        self.reset_settings.clicked.connect(self.reset_preferences)
        self.diagnose_button = QPushButton('检查运行环境')
        self.diagnose_button.clicked.connect(self.start_diagnostics)
        self.license_button = QPushButton('许可与源码')
        self.license_button.clicked.connect(self.show_licenses)
        for button in (self.save_settings, self.reset_settings, self.diagnose_button, self.license_button):
            preference_actions.addWidget(button)
        preference_actions.addStretch()
        maintenance.addLayout(preference_actions)
        self.settings_status = QLabel('设置会自动保存')
        self.settings_status.setWordWrap(True)
        self.settings_status.setObjectName('muted')
        maintenance.addWidget(self.settings_status)
        settings_layout.addWidget(maintenance_card)
        self.diagnostics_result = QTextEdit()
        self.diagnostics_result.setReadOnly(True)
        self.diagnostics_result.setPlaceholderText('检查依赖、推理设备、模型文件、配置与目录权限。检查不会下载或运行模型。')
        self.diagnostics_result.setMinimumHeight(170)
        self.diagnostics_result.hide()
        settings_layout.addWidget(self.diagnostics_result)
        settings_layout.addStretch()
        self.tabs = QTabWidget()
        self.tabs.tabBar().hide()
        monitoring_page = QWidget()
        self.monitoring_page = monitoring_page
        monitoring_layout = QVBoxLayout(monitoring_page)
        monitoring_layout.setContentsMargins(0, 0, 0, 0)
        monitoring_layout.setSpacing(12)
        monitoring_layout.addWidget(self.setup_card)
        monitoring_layout.addWidget(self.alert_banner)
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        video_heading = QHBoxLayout()
        title = QLabel('当前画面')
        title.setObjectName('sectionTitle')
        video_heading.addWidget(title, 1)
        self.show_boxes = QCheckBox('显示检测框')
        self.show_boxes.setChecked(True)
        video_heading.addWidget(self.show_boxes)
        left_layout.addLayout(video_heading)
        self.video = VideoView()
        left_layout.addWidget(self.video, 1)
        self.progress_text = QLabel('等待开始')
        self.progress_text.setObjectName('muted')
        left_layout.addWidget(self.progress_text)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        left_layout.addWidget(self.progress)
        self.monitoring_status = QLabel('逐帧分析 · 尚未开始')
        self.monitoring_status.setObjectName('muted')
        self.monitoring_status.setWordWrap(True)
        self.curve = RiskCurve()
        self.timeline_title = QLabel('最近 5 分钟')
        self.timeline_title.setObjectName('sectionTitle')
        left_layout.addWidget(self.timeline_title)
        self.timeline = EventTimeline()
        self.timeline.eventSelected.connect(self.focus_event)
        left_layout.addWidget(self.timeline)
        body.addWidget(left)

        card = QFrame()
        self.alert_card = card
        card.setObjectName('alertCard')
        card.setMinimumWidth(235)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 16)
        card_layout.setSpacing(9)
        self.level = QLabel('尚未分析')
        self.level.setWordWrap(True)
        self.level.setStyleSheet('font-size:23px;font-weight:700;color:#52647a;')
        self.score = QLabel('辅助评分 —（0—100）')
        self.score.setStyleSheet('font-size:17px;')
        self.score.setToolTip('算法辅助评分，不是火灾概率。处理建议依据当前保存的提示等级，包含持续确认和防抖过程。')
        self.evidence = QLabel('等待输入')
        self.evidence.setWordWrap(True)
        self.evidence.setObjectName('muted')
        self.alert_context = QLabel('检测状态')
        card_layout.addWidget(self.alert_context)
        alert_heading = QHBoxLayout()
        self.alert_symbol = QLabel('—')
        self.alert_symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.alert_symbol.setFixedSize(30, 30)
        self.alert_badge = QLabel('尚未检测')
        self.alert_badge.setWordWrap(True)
        alert_heading.addWidget(self.alert_symbol)
        alert_heading.addWidget(self.alert_badge, 1)
        card_layout.addLayout(alert_heading)
        for widget in (self.level, self.evidence):
            card_layout.addWidget(widget)
        self.next_step = QLabel('先选择画面来源，再开始检测。')
        self.next_step.setWordWrap(True)
        card_layout.addWidget(self.next_step)
        from ui.scene_panel import SceneDescriptionPanel
        self.scene_description = SceneDescriptionPanel(self)
        self.scene_description.settingsRequested.connect(lambda: self.monitor_center.ai_settings())
        card_layout.addWidget(self.scene_description)
        self.current_event_button = QPushButton('核查事件')
        self.current_event_button.setObjectName('primary')
        self.current_event_button.clicked.connect(self.open_current_event)
        card_layout.addWidget(self.current_event_button)
        self.current_acknowledge = QPushButton('已查看')
        self.current_acknowledge.setToolTip('记录你已查看这条提示；事件仍需核查。')
        self.current_acknowledge.clicked.connect(self.acknowledge_current)
        card_layout.addWidget(self.current_acknowledge)
        self.mute_button = QPushButton('暂时静音 5 分钟')
        self.mute_button.setObjectName('quiet')
        self.mute_button.clicked.connect(self.toggle_mute)
        self.sound_status = QPushButton('提示音：关闭')
        self.sound_status.setObjectName('quiet')
        self.sound_status.setToolTip('点击开启或关闭提示音；橙色、红色新提示时响铃。')
        self.sound_status.clicked.connect(lambda: self.alert_sound.setChecked(not self.alert_sound.isChecked()))
        self.alert_sound.toggled.connect(self._update_mute)
        sound_row = QHBoxLayout()
        sound_row.addWidget(self.sound_status)
        sound_row.addWidget(self.mute_button)
        card_layout.addLayout(sound_row)
        self.analysis_toggle = QPushButton('检测详情')
        self.analysis_toggle.setObjectName('quiet')
        self.analysis_toggle.setCheckable(True)
        card_layout.addWidget(self.analysis_toggle)
        self.analysis_details = QWidget()
        detail_layout = QVBoxLayout(self.analysis_details)
        detail_layout.setContentsMargins(0, 8, 0, 0)
        detail_layout.addWidget(self.score)
        grid = QGridLayout()
        self.feature_labels = {}
        for index, (key, title) in enumerate([('coverage', '检测框占比'), ('duration', '累计检测时长'),
                                             ('trend', '框占比变化'), ('confidence', '检测匹配分')]):
            grid.addWidget(QLabel(title), index, 0)
            value = QLabel('—')
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(value, index, 1)
            self.feature_labels[key] = value
        detail_layout.addLayout(grid)
        self.explanation = QLabel('确认持续视觉事件后显示评分依据。')
        self.explanation.setWordWrap(True)
        detail_layout.addWidget(self.explanation)
        self.contributions = QLabel('')
        self.contributions.setWordWrap(True)
        self.contributions.setObjectName('muted')
        detail_layout.addWidget(self.contributions)
        detail_layout.addWidget(self.monitoring_status)
        detail_layout.addWidget(self.input_status)
        self.analysis_details.hide()
        self.analysis_toggle.toggled.connect(self.analysis_details.setVisible)
        self.analysis_toggle.toggled.connect(lambda expanded: self.analysis_toggle.setText('收起详情' if expanded else '检测详情'))
        card_layout.addWidget(self.analysis_details)
        card_layout.addStretch()
        self.alert_help = QPushButton('提示说明')
        self.alert_help.setObjectName('quiet')
        self.alert_help.hide()
        self.alert_help.clicked.connect(self.show_monitoring_help)
        self.analysis_toggle.toggled.connect(self.alert_help.setVisible)
        card_layout.addWidget(self.alert_help)
        scroll = QScrollArea()
        scroll.setMinimumWidth(250)
        scroll.setWidgetResizable(True)
        right_column = QWidget()
        right_layout = QVBoxLayout(right_column)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(card, 0, Qt.AlignmentFlag.AlignTop)
        right_layout.addStretch()
        scroll.setWidget(right_column)
        body.addWidget(scroll)
        body.setSizes([840, 340])
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 1)
        monitoring_layout.addWidget(body, 1)
        self.tabs.addTab(monitoring_page, '画面分析')
        self._build_trend_page()
        self._build_notification_page()
        from ui.event_page import build_event_page
        build_event_page(self, table)
        history_page = QFrame()
        history_page.setObjectName('panel')
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(18, 18, 18, 18)
        history_layout.setSpacing(16)
        history_actions = QHBoxLayout()
        self.refresh_button = QPushButton('刷新记录')
        self.refresh_button.clicked.connect(self.refresh_history)
        self.load_button = QPushButton('查看选中记录')
        self.load_button.clicked.connect(self.open_history)
        self.history_filter = QLineEdit()
        self.history_filter.setPlaceholderText('按视频名或运行时间筛选')
        self.history_filter.textChanged.connect(self.filter_history)
        self.history_status = QComboBox()
        for label, value in [('全部运行状态', ''), ('分析完成', 'completed'), ('手动停止', 'stopped_by_user'),
                             ('分析失败', 'failed'), ('摄像头断开', 'camera_disconnected'),
                             ('磁盘空间不足', 'storage_limit_reached')]:
            self.history_status.addItem(label, value)
        self.history_status.currentIndexChanged.connect(lambda: self.filter_history(self.history_filter.text()))
        for widget in (self.history_filter, self.refresh_button, self.load_button):
            history_actions.addWidget(widget)
        history_actions.insertWidget(1, self.history_status)
        history_layout.addLayout(history_actions)
        self.history_table = table(['运行时间', '视频', '状态', '帧数', '确认事件', '当时最高提示'])
        self.history_table.doubleClicked.connect(lambda index: self.open_history())
        history_layout.addWidget(self.history_table)
        self.tabs.addTab(history_page, '历史记录')
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(settings_page)
        self.tabs.addTab(settings_scroll, '设置')
        layout.addWidget(self.tabs, 1)
        footer = QHBoxLayout()
        self.output_label = QLabel('分析记录自动保存在本机')
        self.output_label.setToolTip(str(ROOT / 'outputs'))
        self.output_label.setObjectName('muted')
        self.output_label.setWordWrap(True)
        footer.addWidget(self.output_label, 1)
        self.folder_button = QPushButton('结果目录')
        self.folder_button.setObjectName('quiet')
        self.folder_button.clicked.connect(self.open_output)
        self.report_button = QPushButton('导出分析报告')
        self.report_button.setObjectName('quiet')
        self.report_button.clicked.connect(self.export_review_report)
        footer.addWidget(self.report_button)
        self.export_button = QPushButton('导出完整记录')
        self.export_button.setText('导出记录')
        self.export_button.clicked.connect(self.choose_export)
        footer.addWidget(self.folder_button)
        footer.addWidget(self.export_button)
        layout.addLayout(footer)
        self._build_storage_page()
        from ui.monitor_center import MonitorCenter
        self.monitor_center = MonitorCenter(self)
        self.monitor_center.external_scene_busy = lambda: self.scene_description.is_busy
        self.monitor_center.external_context_for_run = lambda path: self.task.scene_context if isinstance(self.task, AnalysisWorker) and self.task.output and Path(self.task.output).resolve() == Path(path).resolve() else None
        self.monitor_center.analysis_preferences = lambda: {'model_profile': self.model_profile.currentData(),
            'association_profile': self.association_profile.currentData(), 'confirmation_profile': self.confirmation_profile.currentData(),
            'monitoring_profile': self.monitoring_profile.currentData(), 'conf': self.confidence.value(), 'alert_sound': self.alert_sound.isChecked()}
        self.monitor_center.sound_allowed = lambda: self.alert_sound.isChecked() and monotonic() >= self.muted_until
        self.monitor_center.soundToggleRequested.connect(lambda: self.alert_sound.setChecked(not self.alert_sound.isChecked()))
        self.monitor_center.muteRequested.connect(self.toggle_mute)
        self.scene_description.can_start = lambda: not self.monitor_center.scene_worker and not self.monitor_center.scene_queue
        self.scene_description.ai_config = lambda: (self.monitor_center.endpoint, self.monitor_center.model)
        self.monitor_center.openSavedEvent.connect(self._open_monitor_record)
        self.monitor_center.runSaved.connect(self.refresh_history)
        self.monitor_center.reviewChanged.connect(self._review_changed)
        self.scene_description.reviewSaved.connect(self.monitor_center.refresh_scene_records)
        self.monitor_center.viewChanged.connect(self._sync_navigation)
        self.monitor_center.viewChanged.connect(self._update_event_summary)
        self.tabs.addTab(self.monitor_center, '监控中心')
        self._build_navigation()
        self._paint_alert()

    def _build_navigation(self):
        self.nav_buttons = {}
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        def add_button(key, label, target, secondary=False):
            button = QPushButton(label)
            button.setObjectName('toolNav' if secondary else 'nav')
            button.setIcon(icon(key))
            button.setIconSize(QSize(19, 19))
            button.setCheckable(True)
            self.nav_group.addButton(button)
            button.clicked.connect(lambda checked=False, value=('event_board' if key == 'events' else key): self.navigate(value))
            target.addWidget(button)
            self.nav_buttons[key] = button
        add_button('center', '监控台', self.sidebar_layout)
        add_button('camera', '单路监测', self.sidebar_layout)
        self.nav_buttons['camera'].hide()  # Compatibility route; live views share the monitoring desk.
        add_button('events', '事件记录', self.sidebar_layout)
        self.tools_toggle = QPushButton('更多功能')
        self.tools_toggle.setIcon(icon('more'))
        self.tools_toggle.setObjectName('navGroup')
        self.tools_toggle.setCheckable(True)
        self.tools_toggle.setToolTip('录像分析、历史记录和存储管理')
        self.sidebar_layout.addWidget(self.tools_toggle)
        self.tools_panel = QWidget()
        tools_layout = QVBoxLayout(self.tools_panel)
        tools_layout.setContentsMargins(8, 0, 0, 0)
        tools_layout.setSpacing(2)
        for key, label in [('file', '录像分析'), ('history', '历史记录'), ('storage', '录像与存储')]:
            add_button(key, label, tools_layout, True)
        self.tools_panel.hide()
        self.tools_toggle.toggled.connect(self.tools_panel.setVisible)
        self.tools_toggle.setToolTip('录像分析、历史记录与存储管理')
        self.sidebar_layout.addWidget(self.tools_panel)
        self.sidebar_layout.addStretch()
        add_button('settings', '设置', self.sidebar_layout)
        self.sidebar_layout.addSpacing(16)
        footer_note = QLabel('本地运行')
        footer_note.setObjectName('muted')
        footer_note.setContentsMargins(14, 0, 0, 0)
        footer_note.setToolTip('画面在本机分析；软件用于辅助核查。')
        self.sidebar_layout.addWidget(footer_note)
        self.tabs.currentChanged.connect(self._sync_navigation)
        self._sync_navigation()

    def navigate(self, key):
        if key in ('center', 'camera', 'event_board'):
            if key == 'camera' and not self.busy:
                self.input_kind.setCurrentIndex(self.input_kind.findData('camera'))
            self.monitor_center.set_event_mode(key == 'event_board')
            self.tabs.setCurrentIndex(7)
            self._sync_navigation()
            return
        if key in ('camera', 'file'):
            if self.busy and key != self.input_kind.currentData():
                self._sync_navigation()
                return
            self.input_kind.setCurrentIndex(self.input_kind.findData(key))
            self.tabs.setCurrentIndex(0)
        else:
            self.tabs.setCurrentIndex({'events': 3, 'history': 4, 'settings': 5, 'storage': 6, 'center': 7}[key])
        self._sync_navigation()

    def _sync_navigation(self, *args):
        if not hasattr(self, 'nav_buttons'):
            return
        index = self.tabs.currentIndex()
        live = self.input_kind.currentData() == 'camera'
        key = {0: 'camera' if live else 'file', 1: 'events', 2: 'events', 3: 'events',
               4: 'history', 5: 'settings', 6: 'storage', 7: 'center'}.get(index, 'file')
        if index == 7:
            key = 'events' if self.monitor_center.event_mode else 'center'
        self.nav_buttons[key].setChecked(True)
        for nav_key, button in self.nav_buttons.items():
            button.setIcon(icon(nav_key, '#3867e8' if nav_key == key else '#8290a6'))
        self.tools_toggle.setChecked(key in ('file', 'history', 'storage'))
        title, subtitle = {
            0: ('实时监测' if live else '录像分析', '查看当前画面，及时核查检测事件' if live else '选择已有录像，查看火焰和烟雾的检测结果'),
            1: ('详细分析依据', '检测框占比表示画面中的框大小，不代表实际燃烧面积'),
            2: ('事件提示历史', '等级变化与提示按事件保留；已知悉不等于核查完成'),
            3: ('事件记录', '查看事件前后片段，记录人工核查结果'),
            4: ('历史记录', '查找已保存的分析，回看证据与人工记录'),
            5: ('设置', '提示与播放设置可直接调整，技术参数位于高级选项'),
            6: ('录像与存储', '设置录像方式，保护重要记录，预览后清理'),
            7: ('事件记录' if self.monitor_center.event_mode else '监控台', '统一查看各路画面和事件处理进度')
        }[index]
        point = self.run_config.get('monitoring_point') or {}
        if index == 3 and point:
            subtitle = f'{point.get("name", "监控点")} · {point.get("location", "位置未登记")} · {subtitle}'
        self.page_title.setText(title)
        self.page_subtitle.setText(subtitle)
        self.page_subtitle.hide()
        self.page_title.setToolTip(subtitle)
        self.state.setVisible(index not in (5, 6, 7))
        pending = sum(event_review_state(e, self.notifications.values(), self.feedback) not in REVIEW_COMPLETE for e in self.events)
        self.pending_summary.setVisible(index not in (5, 6, 7) and pending > 0)
        for widget in (self.output_label, self.report_button, self.folder_button, self.export_button):
            widget.setVisible(index in (0, 1, 2, 3, 4) and self.current_run is not None)

    def focus_event(self, event_id):
        self._reset_event_filters()
        self.tabs.setCurrentWidget(self.event_page)
        for row in range(self.event_table.rowCount()):
            if self.event_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == event_id:
                self.event_table.selectRow(row)
                self.event_table.scrollToItem(self.event_table.item(row, 0))
                return

    def open_event_analysis(self):
        event = self._selected_event()
        if not event:
            return
        index = self.trend_selector.findData(event['event_id'])
        self.trend_selector.setCurrentIndex(index)
        self.tabs.setCurrentIndex(1)
        if index < 0:
            self.trend_context.setText(f'事件 #{event["event_id"]} 没有可用的框占比趋势记录')
            self.trend_reason.setText('当前记录只保留可用的趋势快照；不会使用其他事件的数据代替。')

    def _latest_notice(self, event_id):
        return next((n for n in reversed(list(self.notifications.values()))
                     if n.get('event_id') == event_id and n.get('kind') != 'event_closed'), None)

    def _current_notice(self):
        notice = self.notifications.get(self.active_notification)
        if notice and notice.get('kind') != 'event_closed':
            return notice
        candidates = [self._latest_notice(e) for e in self.events]
        candidates = [n for n in candidates if n]
        return max(candidates, key=lambda n: ({'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}.get(n.get('level'), 0), n.get('timestamp_s', 0)), default=None)

    def open_current_event(self):
        notice = self._current_notice()
        if notice:
            self.focus_event(notice['event_id'])
        else:
            self.tabs.setCurrentWidget(self.event_page)

    def acknowledge_current(self):
        notice = self._current_notice()
        if notice:
            self._record_feedback('acknowledged', notice=notice, comment='')

    def acknowledge_selected_event(self):
        event = self._selected_event()
        if event:
            self._record_feedback('acknowledged', notice=self._latest_notice(event['event_id']), comment='')

    def toggle_mute(self):
        self.muted_until = 0 if self.muted_until > monotonic() else monotonic() + 300
        self._update_mute()

    def test_alert_sound(self):
        self.sound_test_result.show()
        QApplication.beep()
        self.sound_test_result.setText('已发送试听请求。请确认能听到声音，并检查系统音量、静音状态和输出设备。')

    def show_monitoring_help(self):
        QMessageBox.information(self, '提示说明',
            '黄色：请核查\n橙色：优先核查\n红色：立即核查\n\n'
            '颜色表示核查优先程度。提示依据摄像头画面，仍需结合现场核实。\n\n'
            '“标为已查看”只记录已阅读，事件仍保留待核查。\n'
            '检测详情中的辅助评分不是火灾概率。\n\n'
            '开启提示音后，橙色、红色新提示会响铃；历史记录不响铃。')

    def _paint_alert(self, level='NORMAL', historical=False, badge=None):
        level = level if level in LEVEL_NAMES else 'NORMAL'
        visual = 'NORMAL' if historical else level
        color, background, border = LEVEL_COLORS[visual], LEVEL_BACKGROUNDS[visual], LEVEL_BORDERS[visual]
        self.alert_card.setStyleSheet(f'QFrame#alertCard {{ background:#ffffff; border:1px solid {"#e5ebf4" if visual == "NORMAL" else border}; border-radius:18px; }}')
        self.alert_symbol.setText('档' if historical else '!' if visual != 'NORMAL' else '—')
        self.alert_symbol.setStyleSheet(f'background:{background}; color:{color}; border-radius:15px; font-size:18px; font-weight:600;')
        text = badge or ('当时提示：' + LEVEL_NAMES[level] if historical and level != 'NORMAL' else
                         '历史结果' if historical else LEVEL_NAMES[level] if level != 'NORMAL' else '正在检测')
        self.alert_badge.setText(text)
        self.alert_badge.setStyleSheet(f'color:{color}; font-size:15px; font-weight:600;')
        self.alert_symbol.setVisible(badge != '尚未检测')
        self.alert_badge.setVisible(badge != '尚未检测')
        if not historical and level != 'NORMAL' and not badge:
            self.alert_badge.setText(LEVEL_NAMES[level].split(' · ')[0])
        self.alert_context.setText('历史记录 · 非实时' if historical else '检测状态')
        self.alert_card.setAccessibleName(('历史记录，' if historical else '') + text)
        self.level.setStyleSheet(f'font-size:25px; font-weight:700; color:{color};')
        if historical:
            self.next_step.setText('历史结果，请回看核查。')
            self.next_step.hide()
            self.evidence.show()

    def _update_mute(self):
        remaining = max(0, int(self.muted_until - monotonic()))
        self.mute_button.setText(f'恢复 {remaining // 60:02d}:{remaining % 60:02d}' if remaining else '静音 5 分钟')
        self.mute_button.setToolTip('静音 5 分钟；检测和视觉提示继续运行。再次点击可提前恢复。')
        self.sound_status.setText('提示音：静音中' if remaining and self.alert_sound.isChecked() else
                                  '提示音：开启' if self.alert_sound.isChecked() else '提示音：关闭')
        self.mute_button.setVisible(self.alert_sound.isChecked())
        if hasattr(self, 'monitor_center'):
            self.monitor_center.sound_control.setText(self.sound_status.text())
            self.monitor_center.mute_control.setText(self.mute_button.text())
            self.monitor_center.mute_control.setVisible(self.alert_sound.isChecked())

    def _update_event_summary(self):
        states = {(row['output'], row['event_id']): row['review'] for row in self.monitor_center.events.values()} if hasattr(self, 'monitor_center') else {}
        for identifier in self.events:
            states[(str(self.current_run), identifier)] = event_review_state(identifier, self.notifications.values(), self.feedback)
        pending = sum(phase(action) != 'completed' for action in states.values())
        self.pending_summary.setText(f'待办 {pending} 条')
        self.pending_summary.setVisible(pending > 0 and self.tabs.currentIndex() not in (5, 6, 7))
        self.nav_buttons['events'].setText(f'事件记录   {pending}' if pending else '事件记录')
        available = bool(self._current_notice() and self.current_run)
        self.current_event_button.setEnabled(bool(self.events))
        self.current_event_button.setVisible(bool(self.events))
        self.current_acknowledge.setEnabled(available and (not self.busy or isinstance(self.task, AnalysisWorker)))
        self.current_acknowledge.setVisible(available)
        self.timeline_title.setVisible(bool(self.events))
        self.timeline.setVisible(bool(self.events))
        self.analysis_toggle.setVisible(bool(self.busy or self._historical or self.events))
        self.alert_banner.setVisible(bool(self._current_notice() and not self._historical and isinstance(self.task, AnalysisWorker)))
        self.timeline.set_events(list(self.events.values()), self._timeline_time)

    def _show_event_review(self, event):
        scene_event = event
        if isinstance(self.task, AnalysisWorker) and not self._historical:
            current = self.scene_description.latest_assessment or {}
            scene_event = self.events.get(current.get('dominant_event_id'))
        self.scene_description.set_target(self.current_run, scene_event, self.run_config, running=self.busy)
        notice = self._latest_notice(event['event_id']) if event else None
        available = bool(notice and self.current_run and (not self.busy or isinstance(self.task, AnalysisWorker)))
        for widget in (self.review_choice, self.review_comment, self.review_save, self.review_ack):
            widget.setEnabled(available)
        key = (str(self.current_run), event.get('event_id'), notice.get('notification_id') if notice else None) if event else None
        if key != getattr(self, '_review_draft_key', None):
            self.close_inline_replay()
            self._review_draft_key = key
            feedback = self.feedback.get(notice['notification_id'], {}) if notice else {}
            action = feedback.get('review_action') or feedback.get('action')
            if action == 'resolved':
                action = feedback.get('conclusion')
            self.review_choice.setCurrentIndex(max(0, self.review_choice.findData(action)))
            self.review_comment.setPlainText(feedback.get('review_comment') or feedback.get('comment', ''))
            self.review_note_toggle.setChecked(bool(self.review_comment.toPlainText()))
            self.review_video.clear_frame('暂无可用截图\n可在保存后尝试回看片段')
        if not event:
            self.event_subject.setText('现场画面')
            self.review_priority.clear()
            self.event_scene.hide()
            self.review_resolve.hide()
            self.review_reopen.hide()
            self.completed_events_button.hide()
            self.review_conclusion.clear()
            self.review_state.setText('尚未选择事件')
            self.review_basis.setText('选择事件后，查看证据并记录你的判断。')
            self.review_context.setText('等待选择')
            self.review_video.clear_frame('选择左侧事件\n查看检测依据')
            return
        state = event_review_state(event['event_id'], self.notifications.values(), self.feedback)
        self.review_state.setText(REVIEW_NAMES.get(state, state))
        paint_status(self.review_state, state)
        self.review_resolve.setVisible(phase(state) == 'action')
        self.review_resolve.setEnabled(bool(available and phase(state) == 'action'))
        self.review_reopen.setVisible(phase(state) == 'completed')
        self.review_reopen.setEnabled(bool(available and phase(state) == 'completed'))
        self.review_reopen.setText(reopen_label(state, self.feedback.get((notice or {}).get('notification_id'))))
        self.completed_events_button.setVisible(phase(state) == 'completed')
        self.review_ack.setVisible(phase(state) == 'pending' and state != 'acknowledged')
        style = presentation(state)
        label = conclusion_label(state, self.feedback.get((notice or {}).get('notification_id')))
        self.review_conclusion.setText('核查结论：' + label if label else '')
        self.event_subject.setText(CLASS_NAMES.get(event['label'], '异常') + f'事件  #{event["event_id"]}')
        feature = (notice or {}).get('features') or {}
        basis = f'检测到{CLASS_NAMES.get(event.get("label"), "异常")} · 视频 / 监看经过 {video_time(event.get("confirmed_at_s"))}'
        if feature.get('positive_duration_s') is not None:
            basis += f'\n累计检测到特征 {feature["positive_duration_s"]:.1f} 秒'
        if feature.get('coverage_ratio_smoothed') is not None:
            basis += f'\n检测框占画面约 {feature["coverage_ratio_smoothed"]:.1%}'
        self.review_basis.setText(basis + '\n现场情况需人工核实。')
        priority = (event.get('fusion_summary') or event.get('risk_summary') or {}).get('peak_level') or (notice or {}).get('level')
        if priority in LEVEL_NAMES:
            self.review_basis.setText(basis + '\n当时提示：' + LEVEL_NAMES[priority] + '\n现场情况需人工核实。')
        self.review_priority.setText(LEVEL_ACTIONS.get(priority, '待核查') + '  ·  ' + video_time(event.get('confirmed_at_s')))
        self.review_priority.setStyleSheet(f'font-size:20px;font-weight:600;color:{LEVEL_COLORS.get(priority, "#435c7b")};padding:10px 0;')
        if phase(state) == 'completed':
            self.review_priority.setText('当时提示：' + LEVEL_ACTIONS.get(priority, '待核查') + ' · ' + video_time(event.get('confirmed_at_s')))
            self.review_priority.setStyleSheet('font-size:16px;color:#65778f;padding:8px 0;')
        self.review_priority.setToolTip(self.review_basis.text())
        self.review_context.setText('事件截图' if isinstance(self.task, AnalysisWorker) else '历史截图')
        self.review_context.setToolTip('本记录方案：' + self.run_config.get('model_profile_name', self.run_config.get('model_profile', '未记录')))
        scene = self._event_scene_description(event['event_id'])
        self.event_scene.setText('AI画面描述\n' + scene)
        self.event_scene.setVisible(bool(scene))
        if self._show_event_image(self.review_video, event):
            self.review_video.set_context('事件截图')

    def _event_scene_description(self, event_id):
        if not self.current_run:
            return ''
        from core.records import read_jsonl_tail
        path = self.current_run / 'scene-reviews.jsonl'
        try:
            signature = (str(path), path.stat().st_mtime_ns) if path.is_file() else (str(path), None)
            if signature != getattr(self, '_event_scene_signature', None):
                self._event_scene_signature = signature
                self._event_scene_records = {row.get('event_id'): row for row in read_jsonl_tail(path, 500)} if signature[1] else {}
            rows = getattr(self, '_event_scene_records', {})
            record = rows.get(event_id) or rows.get(None) or {}
            return (record.get('scene') or {}).get('description', '')
        except (OSError, ValueError, TypeError):
            return ''

    def _adapt_event_layout(self):
        if not hasattr(self, 'event_detail_split'):
            return
        compact = self.width() < 1280
        self.event_detail_tabs.setVisible(compact)
        self.evidence_scroll.setVisible(not compact or self.event_detail_tabs.currentIndex() == 0)
        self.review_scroll.setVisible(not compact or self.event_detail_tabs.currentIndex() == 1)
        if getattr(self, '_compact_event_layout', None) != compact:
            self._compact_event_layout = compact
            self.event_detail_split.setSizes([560, 330])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adapt_event_layout()

    def _show_event_image(self, view, event):
        if not self.current_run:
            return False
        source = event.get('evidence_source') or {}
        for image, detections in [(source.get('image'), source.get('detections')), (event.get('evidence_image'), None)]:
            if not image:
                continue
            candidate = (self.current_run / image).resolve()
            if self.current_run.resolve() in candidate.parents and candidate.is_file():
                view.set_file(candidate, detections)
                return True
        return False

    def save_event_review(self):
        event = self._selected_event()
        action = self.review_choice.currentData()
        if not event or not action:
            self.review_save_status.setText('请先选择核查结果；不确定时可以选择“无法判断”。')
            return
        comment = self.review_comment.toPlainText()
        if len(comment) > 1000:
            self.review_save_status.setText('备注超过 1000 字，请缩短后保存。')
            return
        current = self.feedback.get((self._latest_notice(event['event_id']) or {}).get('notification_id'), {})
        if current.get('review_action') == 'resolved' and action == current.get('conclusion'):
            action = 'resolved'
        if self._record_feedback(action, notice=self._latest_notice(event['event_id']), comment=comment):
            self._keep_handled_event_visible(event['event_id'], action)
            self.review_save_status.setText('核查结果已保存。' + ('该事件仍保留待跟进。' if action not in REVIEW_COMPLETE else '人工意见已记录，原检测证据保留。'))
            style = presentation(action)
            self.review_save_status.setStyleSheet(f'color:{style["color"]};font-weight:600;')
            if style['phase'] == 'completed':
                self.review_save_status.setText('已处理。可在“已处理”列表查看和复核。')

    def complete_event_review(self):
        event = self._selected_event()
        if not event or event_review_state(event['event_id'], self.notifications.values(), self.feedback) != 'confirmed_abnormal':
            return
        if self._record_feedback('resolved', notice=self._latest_notice(event['event_id']), comment=self.review_comment.toPlainText()):
            self._keep_handled_event_visible(event['event_id'], 'resolved')
            self.review_save_status.setText('已处理。可查看处理记录，或进入“已处理”列表复核。')
            self.review_save_status.setStyleSheet('color:#19764b;font-weight:600;')

    def show_event_review_history(self):
        event = self._selected_event()
        if event and self.current_run:
            show_handling_history(self, self.current_run, event['event_id'])

    def reopen_event_review(self):
        event = self._selected_event()
        if not event:
            return
        notice = self._latest_notice(event['event_id'])
        state = event_review_state(event['event_id'], self.notifications.values(), self.feedback)
        if phase(state) != 'completed':
            self.review_save_status.setText('该记录尚未完成，无需撤回。')
            return
        self._review_draft_key = None
        if self._record_feedback('reopened', notice=notice, comment=''):
            saved = self.feedback.get(notice['notification_id'], {})
            current = saved.get('review_action', 'needs_verification')
            self._keep_handled_event_visible(event['event_id'], current)
            style = presentation(current)
            self.review_save_status.setText('已撤回，返回“' + style['label'] + '”。原处理记录仍可查看。')
            self.review_save_status.setStyleSheet(f'color:{style["color"]};font-weight:600;')

    def show_completed_events(self):
        event = self._selected_event()
        run = str(self.current_run)
        self.navigate('event_board')
        self.monitor_center.workflow_tabs.setCurrentIndex(3)
        if event:
            for row in range(self.monitor_center.event_table.rowCount()):
                key = self.monitor_center.event_table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                if key[1] == run and key[2] == event['event_id']:
                    self.monitor_center.event_table.selectRow(row)
                    break

    def _keep_handled_event_visible(self, identifier, action):
        self.event_handling.setCurrentIndex(self.event_handling.findData({'pending': 'review', 'action': 'action', 'completed': 'completed'}[phase(action)]))
        for row in range(self.event_table.rowCount()):
            if self.event_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == identifier:
                self.event_table.selectRow(row)
                break

    def _review_changed(self, run_path):
        self.monitor_center.refresh_feedback(run_path)
        if self.current_run and Path(run_path).resolve() == self.current_run.resolve():
            from core.records import read_jsonl_tail
            self.feedback = {row['notification_id']: row for row in read_jsonl_tail(self.current_run / 'feedback.jsonl', 5000) if 'notification_id' in row}
            self._fill_notifications()
            self._fill_events()
        self._update_event_summary()

    def close_inline_replay(self):
        dialog, self.inline_replay = self.inline_replay, None
        if dialog:
            dialog.hide()
            dialog.close()
        self.replay_host.hide()
        self.review_video.show()

    def export_review_report(self):
        if self.busy or not self.current_run:
            return
        path, _ = QFileDialog.getSaveFileName(self, '导出分析报告', '焰鉴-事件复核报告.md', 'Markdown 报告 (*.md)')
        if not path:
            return
        try:
            target = Path(path).resolve()
            if target == self.current_run.resolve() or self.current_run.resolve() in target.parents:
                raise ValueError('请将报告保存到当前记录目录之外。')
            target.write_text(render_review_report(self.current_run), encoding='utf-8')
            self.output_label.setText(f'报告已导出：{target.name}')
        except (OSError, ValueError) as exc:
            self._notify_error(f'报告未能导出：{exc}')

    def _build_storage_page(self):
        from ui.storage_page import build_storage_page
        build_storage_page(self, table)

    def show_licenses(self):
        QMessageBox.information(self, '焰鉴 · 许可与源码',
            '焰鉴应用代码 © 2026 卡卡队，采用 AGPL-3.0-only。\n'
            '允许依该许可修改与再分发，不提供保证。第三方库、模型和数据保留各自许可及版权。\n\n'
            '应用源码：软件目录中的 application-source.zip。\n'
            '第三方源码：corresponding-sources 文件夹。\n'
            '许可全文：LICENSE、licenses 和 third-party-notices。\n'
            '取得方式和重建说明：SOURCE_ACCESS.md、BUILD_FROM_SOURCE.md。\n\n'
            '本软件使用 Qt、Ultralytics 与 OpenCV/FFmpeg 等组件。'
            '它是视觉辅助核查工具，非认证消防报警设备。')

    def _storage_settings_changed(self, *args):
        self.storage_plan = None
        self.storage_cleanup.setEnabled(False)
        self.storage_status.setText('设置已更改，请重新扫描以更新清理预览。')
        self._settings_changed()

    def _storage_exclusions(self):
        paths = [str(self.current_run)] if self.current_run else []
        outputs = ROOT / 'outputs'
        for dialog in self.replays:
            for media in dialog.media:
                for parent in Path(media['path']).parents:
                    if parent.parent == outputs:
                        paths.append(str(parent))
                        break
        return tuple(set(paths))

    def scan_storage(self):
        if self.busy:
            return
        policy = {'retention_days': self.storage_days.value(), 'max_bytes': self.storage_max_gb.value() * 1024 ** 3}
        self.storage_status.setText('正在后台扫描记录…')
        self._attach_task(StorageWorker(ROOT / 'outputs', policy, exclude=self._storage_exclusions(), parent=self), self._storage_done)

    def _storage_done(self, result):
        from ui.storage_page import storage_size
        if not result['ok']:
            self.storage_plan = None
            self.storage_status.setText(f'存储操作失败：{result["error"]}')
            return
        plan = self.storage_plan = result['plan']
        self.storage_records = sorted(plan['records'], key=lambda r: r['name'], reverse=True)
        candidates = {r['name']: r['cleanup_reason'] for r in plan['candidates']}
        self.storage_table.setRowCount(len(self.storage_records))
        for row, record in enumerate(self.storage_records):
            for column, value in enumerate((record_label(record['name']), storage_size(record['bytes']),
                                            record['reason'] or '可清理', candidates.get(record['name'], '保留'))):
                item = QTableWidgetItem(value)
                item.setToolTip(record['name'] if column == 0 else value)
                self.storage_table.setItem(row, column, item)
        text = (f'{len(plan["records"])} 条记录 · 共 {plan["total_bytes"] / 1024 ** 2:.1f} MiB；'
                f'计划清理 {len(plan["candidates"])} 条，释放约 {plan["reclaim_bytes"] / 1024 ** 2:.1f} MiB。')
        if plan['over_limit']:
            text += ' 受保护或使用中记录仍超过上限，需要调整容量或记录保护。'
        cleanup = result.get('cleanup')
        if cleanup:
            text = f'已清理 {len(cleanup["removed"])} 条，跳过 {len(cleanup["skipped"])} 条。' + text
        self.storage_metrics['used'].setText(storage_size(plan['total_bytes']))
        self.storage_metrics['reclaim'].setText(storage_size(plan['reclaim_bytes']))
        self.storage_metrics['count'].setText(str(len(plan['records'])))
        summary = f'扫描完成 · {len(plan["records"])} 条记录'
        if plan['over_limit']:
            summary += ' · 当前空间超过上限'
        if cleanup:
            summary = f'已清理 {len(cleanup["removed"])} 条记录'
        self.storage_status.setText(summary)
        self.storage_status.setToolTip(text)

    def clean_storage(self):
        if self.busy or not self.storage_plan or not self.storage_plan['candidates']:
            return
        plan = self.storage_plan
        answer = QMessageBox.question(self, '确认清理历史记录',
            f'将永久删除预览中 {len(plan["candidates"])} 条记录，约 {plan["reclaim_bytes"] / 1024 ** 2:.1f} MiB。\n'
            '包含这些记录的录像、截图、日志和人工反馈。源视频、模型及导出包不受影响。\n'
            '删除前会再次检查保护、使用状态和文件变化。是否清理？',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        policy = {'retention_days': plan['retention_days'], 'max_bytes': plan['max_bytes']}
        self._attach_task(StorageWorker(ROOT / 'outputs', policy, exclude=self._storage_exclusions(),
                                       approved=plan, parent=self), self._storage_done)

    def _storage_selection_changed(self):
        row = self.storage_table.currentRow()
        selected = self.storage_records[row] if 0 <= row < len(self.storage_records) else None
        self.storage_protect.setEnabled(not self.busy and selected is not None and selected['reason'] != '链接或非目录')
        self.storage_protect.setText('解除重要保护' if selected and selected['protected'] else '标记为重要记录')

    def toggle_storage_protection(self):
        if self.busy:
            return
        row = self.storage_table.currentRow()
        if not 0 <= row < len(self.storage_records):
            return
        from core.storage import protect_record
        record = self.storage_records[row]
        try:
            protect_record(ROOT / 'outputs', record['name'], not record['protected'])
        except (OSError, ValueError) as exc:
            self.storage_status.setText(f'修改保护失败：{exc}')
            return
        self.scan_storage()

    def release_current_record(self):
        if self.busy:
            return
        if self.replays:
            self.storage_status.setText('请先关闭事件回放窗口，再结束当前查看。')
            return
        self.current_run = None
        self.run_config = {}
        self.events = {}
        self.notifications = {}
        self.feedback = {}
        self.active_notification = None
        self._fill_events()
        self._fill_notifications()
        self.curve.set_points([])
        self._show_trend(None)
        self.video.clear_frame('当前查看已结束\n可在历史分析重新载入记录。')
        self.level.setText('等待分析')
        self.score.setText('辅助评分 —（0—100）')
        self.evidence.setText('当前未查看监控记录')
        self._show_features(None)
        self.state.setText('就绪 · 未查看历史记录')
        self.output_label.setText(f'结果保存在：{ROOT / "outputs"}；当前未载入记录')
        self.alert_banner.setText('当前没有实时监控或载入的提示记录')
        self.scan_storage()

    def _build_trend_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 16, 18, 16)
        back = QPushButton('返回事件核查')
        back.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        title = QLabel('画面中的检测框占比变化')
        title.setStyleSheet('font-size:21px;font-weight:700;')
        layout.addWidget(title)
        row = QHBoxLayout()
        row.addWidget(QLabel('观察事件'))
        self.trend_selector = QComboBox()
        self.trend_selector.setMinimumWidth(240)
        self.trend_selector.currentIndexChanged.connect(self._select_trend)
        row.addWidget(self.trend_selector)
        row.addStretch()
        self.trend_status = QLabel('等待连续观测')
        self.trend_status.setObjectName('muted')
        row.addWidget(self.trend_status)
        layout.addLayout(row)
        self.trend_context = QLabel('尚无趋势结果')
        self.trend_context.setObjectName('muted')
        layout.addWidget(self.trend_context)
        metric_card = QFrame()
        metric_card.setObjectName('panel')
        metrics = QGridLayout(metric_card)
        metrics.setContentsMargins(20, 18, 20, 18)
        self.trend_metrics = {}
        self.trend_extra_labels = []
        for column, (key, label) in enumerate((('current', '当前框覆盖'), ('growth', '变化速度'),
                                                ('forecast', '短期外推覆盖'), ('range', '经验残差范围'))):
            caption = QLabel(label)
            if key in ('forecast', 'range'):
                self.trend_extra_labels.append(caption)
            caption.setObjectName('muted')
            value = QLabel('—')
            value.setStyleSheet('font-size:22px;font-weight:600;')
            metrics.addWidget(caption, 0, column)
            metrics.addWidget(value, 1, column)
            self.trend_metrics[key] = value
        layout.addWidget(metric_card)
        self.trend_projection = QCheckBox('显示短期框占比外推（高级）')
        self.trend_projection.setToolTip('虚线外推检测框大小，阴影为经验残差范围；不是物理火势预测或统计置信区间。')
        self.trend_projection.toggled.connect(self._select_trend)
        layout.addWidget(self.trend_projection)
        self.trend_curve = CoverageTrend()
        layout.addWidget(self.trend_curve, 1)
        self.trend_reason = QLabel('确认同一事件并积累观测后，才显示可用的趋势。')
        self.trend_reason.setWordWrap(True)
        layout.addWidget(self.trend_reason)
        note = QLabel('这里预测检测框占画面的比例，不能解释为真实燃烧面积、物理蔓延速度或起火概率。'
                      '虚线为短期线性外推；阴影表示观测残差形成的经验范围，尚未校准为统计置信区间。')
        note.setWordWrap(True)
        note.setObjectName('muted')
        self.trend_projection.setToolTip(note.text())
        note.hide()
        score_title = QLabel('视觉提示指数 · 不是火灾概率')
        score_title.setObjectName('muted')
        layout.addWidget(score_title)
        layout.addWidget(self.curve)
        self.tabs.addTab(page, '趋势观察')

    def _build_notification_page(self):
        page = QFrame()
        page.setObjectName('panel')
        self.notification_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        back = QPushButton('返回事件核查')
        back.clicked.connect(lambda: self.tabs.setCurrentIndex(3))
        layout.addWidget(back, 0, Qt.AlignmentFlag.AlignLeft)
        description = QLabel('以下为事件的提示历史；同一事件的多次变化不会生成多条事件待办。')
        description.setWordWrap(True)
        description.setObjectName('muted')
        self.notification_page.setToolTip(description.text())
        description.hide()
        self.notification_table = table(['观测时间', '事件 / 类别', '视觉等级', '提示', '人工处理'])
        self.notification_table.itemSelectionChanged.connect(self.select_notification)
        layout.addWidget(self.notification_table, 3)
        self.notification_detail = QTextEdit()
        self.notification_detail.setReadOnly(True)
        self.notification_detail.setPlaceholderText('选择提示查看依据。历史提示不会重播声音。')
        self.notification_detail.setMaximumHeight(140)
        layout.addWidget(self.notification_detail, 1)
        review_actions = QHBoxLayout()
        self.notice_event = QPushButton('定位到事件')
        self.notice_event.clicked.connect(self.open_notification_event)
        self.notice_replay = QPushButton('回看提示前后')
        self.notice_replay.clicked.connect(self.replay_notification)
        review_actions.addWidget(self.notice_event)
        review_actions.addWidget(self.notice_replay)
        review_actions.addStretch()
        layout.addLayout(review_actions)
        actions = QHBoxLayout()
        self.feedback_comment = QLineEdit()
        self.feedback_comment.setPlaceholderText('可选：填写人工处理说明，最多 1000 字')
        self.feedback_comment.setMaxLength(1000)
        actions.addWidget(self.feedback_comment, 1)
        self.acknowledge_button = QPushButton('已知悉')
        self.acknowledge_button.clicked.connect(lambda: self._record_feedback('acknowledged'))
        self.false_positive_button = QPushButton('标为疑似误报')
        self.false_positive_button.clicked.connect(lambda: self._record_feedback('false_positive'))
        actions.addWidget(self.acknowledge_button)
        actions.addWidget(self.false_positive_button)
        layout.addLayout(actions)
        self.feedback_status = QLabel('人工标记仅作为反馈保存，不能替代原视频复核与真值标注。')
        self.feedback_status.setObjectName('muted')
        self.feedback_status.setWordWrap(True)
        layout.addWidget(self.feedback_status)
        self.tabs.addTab(page, '提示中心（最近 500 条）')

    def _show_monitoring(self, monitoring):
        if not monitoring:
            self.monitoring_status.setText('该记录未提供监测调度统计')
            return
        stats = monitoring.get('stats') or monitoring
        state = MONITOR_NAMES.get(monitoring.get('state'), '监测统计')
        size = monitoring.get('image_size')
        counts = f'解码 {stats.get("decoded_frames", 0)} · 分析 {stats.get("analyzed_frames", 0)} · 跳过 {stats.get("skipped_frames", 0)} 帧'
        forwards = stats.get('model_forward_passes')
        if forwards is not None:
            counts += f' · 模型前向 {forwards} 次'
        self.monitoring_status.setText(state + (f' · 输入 {size}' if size else '') + '   |   ' + counts)
        self.monitoring_status.setToolTip(monitoring.get('reason', '记录的是调用次数，不能直接视为电能节省。'))

    def _show_trend(self, trend, historical=False):
        previous = self.trend_selector.currentData()
        self.latest_trend = trend
        self.trend_historical = historical
        events = (trend or {}).get('events') or []
        self.trend_selector.blockSignals(True)
        self.trend_selector.clear()
        for event in events:
            self.trend_selector.addItem(f'#{event["event_id"]} · {CLASS_NAMES.get(event.get("label"), "视觉事件")}', event['event_id'])
        chosen = self.trend_selector.findData(previous)
        if chosen < 0:
            chosen = self.trend_selector.findData((trend or {}).get('dominant_event_id'))
        if chosen >= 0:
            self.trend_selector.setCurrentIndex(chosen)
        self.trend_selector.blockSignals(False)
        self._select_trend()

    def _select_trend(self):
        trend = self.latest_trend or {}
        selected = self.trend_selector.currentData()
        event = next((e for e in trend.get('events', []) if e.get('event_id') == selected), None)
        displayed = dict(event) if event else None
        if displayed and not self.trend_projection.isChecked():
            for field in ('projection', 'upper_coverage_ratio', 'lower_coverage_ratio'):
                displayed.pop(field, None)
        self.trend_curve.set_event(displayed)
        for field in ('forecast', 'range'):
            self.trend_metrics[field].setVisible(self.trend_projection.isChecked())
        for caption in self.trend_extra_labels:
            caption.setVisible(self.trend_projection.isChecked())
        timestamp = trend.get('timestamp_s')
        context = '历史最后观察' if self.trend_historical else '当前视频观测'
        self.trend_context.setText(f'{context} @ {timestamp:.2f}s' if timestamp is not None else '尚无趋势结果')
        if not event:
            self.trend_status.setText('等待连续观测' if trend else '尚无趋势记录')
            self.trend_reason.setText(trend.get('note', '确认同一事件并积累观测后，才显示可用的趋势。'))
            for value in self.trend_metrics.values():
                value.setText('—')
            return
        status = event.get('status', 'warming_up')
        self.trend_status.setText(TREND_NAMES.get(status, status))
        self.trend_status.setStyleSheet(f'color:{"#55d8c3" if status == "ready" else "#efba59"};')
        coverage = event.get('coverage_ratio')
        growth = event.get('growth_pp_per_s')
        predicted = event.get('predicted_coverage_ratio')
        lower, upper = event.get('lower_coverage_ratio'), event.get('upper_coverage_ratio')
        self.trend_metrics['current'].setText(f'{coverage:.2%}' if coverage is not None else '—')
        self.trend_metrics['growth'].setText(f'{growth:+.2f} pp/s' if growth is not None else '—')
        self.trend_metrics['forecast'].setText(f'{predicted:.2%} / {event.get("horizon_s", 3):g}s' if predicted is not None else '暂停')
        self.trend_metrics['range'].setText(f'{lower:.2%}–{upper:.2%}' if lower is not None and upper is not None else '—')
        self.trend_reason.setText(event.get('reason', ''))

    def _receive_notifications(self, notices, historical=False):
        fresh = []
        for notice in notices:
            identifier = notice.get('notification_id')
            if identifier is None:
                continue
            if identifier not in self.notifications:
                fresh.append(notice)
            self.notifications[identifier] = notice
        while len(self.notifications) > 500:
            del self.notifications[next(iter(self.notifications))]
        self._fill_notifications()
        if fresh and not historical:
            latest = {}
            for notice in fresh:
                latest[notice.get('event_id')] = notice
            active = [n for n in latest.values() if n.get('kind') != 'event_closed']
            notice = max(active, key=lambda n: {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}.get(n.get('level'), 0)) if active else fresh[-1]
            self.active_notification = notice['notification_id']
            level = notice.get('level', 'NORMAL')
            self.alert_banner.setText(f'{notice_title(notice)}  ·  观测 {notice.get("timestamp_s", 0):.2f}s  ·  {notice.get("message", "")}')
            color = '#98aac6' if notice.get('kind') == 'event_closed' else LEVEL_COLORS.get(level, '#9bafca')
            background = LEVEL_BACKGROUNDS.get(level, '#f4f7fa') if notice.get('kind') != 'event_closed' else '#f4f7fa'
            border = LEVEL_BORDERS.get(level, '#cfd9e6') if notice.get('kind') != 'event_closed' else '#cfd9e6'
            self.alert_banner.setStyleSheet(f'color:{color}; background:{background}; border:1px solid {border}; border-left:5px solid {border}; border-radius:8px; padding:12px;')
            audible = [n for n in latest.values() if n.get('level') in ('MEDIUM', 'HIGH')
                       and 'sound' in (n.get('channels') or []) and n.get('kind') != 'event_closed']
            urgent_upgrade = any(n.get('kind') == 'level_upgrade' and n.get('level') == 'HIGH' for n in audible)
            if audible and (urgent_upgrade or monotonic() - self.last_sound_at >= 3):
                if monotonic() >= self.muted_until:
                    QApplication.beep()
                    self.last_sound_at = monotonic()

    def _fill_notifications(self):
        selected = self._selected_notification()
        selected_id = selected.get('notification_id') if selected else None
        self.notification_table.blockSignals(True)
        notices = list(reversed(list(self.notifications.values())))
        self.notification_table.setRowCount(len(notices))
        selected_row = None
        for row, notice in enumerate(notices):
            identifier = notice['notification_id']
            feedback = self.feedback.get(identifier) or {}
            cells = [f'{notice.get("timestamp_s", 0):.2f}s',
                     f'#{notice.get("event_id", "—")} / {CLASS_NAMES.get(notice.get("label"), "视觉事件")}',
                     LEVEL_NAMES.get(notice.get('level'), notice.get('level', '—')),
                     notice_title(notice),
                     '无需处理' if notice.get('kind') == 'event_closed' else FEEDBACK_NAMES.get(feedback.get('action'), '待处理')]
            for column, value in enumerate(cells):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, identifier)
                self.notification_table.setItem(row, column, item)
            if identifier == selected_id:
                selected_row = row
        if selected_row is not None:
            self.notification_table.selectRow(selected_row)
        self.notification_table.blockSignals(False)
        self.select_notification()

    def _selected_notification(self):
        row = self.notification_table.currentRow()
        item = self.notification_table.item(row, 0) if row >= 0 else None
        return self.notifications.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def select_notification(self):
        notice = self._selected_notification()
        enabled = bool(notice and self.current_run and notice.get('kind') != 'event_closed'
                       and (not self.busy or isinstance(self.task, AnalysisWorker)))
        self.acknowledge_button.setEnabled(enabled)
        self.false_positive_button.setEnabled(enabled)
        self.feedback_comment.setEnabled(enabled)
        self.notice_event.setEnabled(bool(notice and notice.get('event_id') in self.events))
        self.notice_replay.setEnabled(bool(notice and self.current_run and not self.busy))
        self.notice_replay.setToolTip('分析结束或停止并保存后可回放；回放不会重新运行模型。')
        if not notice:
            self.notification_detail.clear()
            return
        feedback = self.feedback.get(notice['notification_id']) or {}
        channels = {'panel': '界面', 'log': '日志', 'sound': '本机提示音'}
        scope = f'事件 #{notice["event_id"]}' if notice.get('event_id') is not None else '综合异常提示'
        lines = [notice_title(notice), notice.get('message', ''),
                 f'观测时间：{notice.get("timestamp_s", 0):.2f}s · {scope}',
                 '提示方式：' + '、'.join(channels.get(c, c) for c in notice.get('channels', [])),
                 '人工处理：' + ('无需处理（结束记录）' if notice.get('kind') == 'event_closed' else
                             FEEDBACK_NAMES.get(feedback.get('action'), '待处理'))]
        if notice.get('score') is not None:
            lines.append(f'该提示时刻的综合指数：{notice["score"]:.1f} / 100')
        feature = notice.get('features') or {}
        if feature.get('coverage_ratio_smoothed') is not None:
            lines.append(f'框并集覆盖：{feature["coverage_ratio_smoothed"]:.2%}；'
                         f'正检测时间：{feature.get("positive_duration_s", 0):.2f}s')
        if notice.get('score_components'):
            lines.append('评分依据：' + ' · '.join(f'{COMPONENT_NAMES.get(key, key)} +{value:.1f}'
                                               for key, value in notice['score_components'].items()))
        if feedback.get('comment'):
            lines.append('处理说明：' + feedback['comment'])
        self.notification_detail.setPlainText('\n'.join(lines))

    def _record_feedback(self, action, notice=None, comment=None):
        notice = notice or self._selected_notification()
        if not notice or not self.current_run:
            return
        try:
            record = append_feedback(self.current_run, notice, action, self.feedback_comment.text() if comment is None else comment)
        except (ValueError, OSError) as exc:
            self._notify_error(f'无法保存人工反馈：{exc}')
            return
        self.feedback[notice['notification_id']] = record
        self.feedback_comment.clear()
        scope = f'事件 #{notice["event_id"]}' if notice.get('event_id') is not None else '综合异常提示'
        self.feedback_status.setText(f'已保存：{FEEDBACK_NAMES[action]} · {scope}。原检测记录保留。')
        if self.active_notification == notice['notification_id']:
            self.active_notification = None
            self.alert_banner.setText('已知悉 · 仍需核查。后续新的等级变化会继续提示。' if action == 'acknowledged'
                                      else '核查意见已记录 · 新的等级变化会继续提示。')
            self.alert_banner.setStyleSheet('color:#52647a;')
        self._fill_notifications()
        self._fill_events()
        if hasattr(self, 'monitor_center'):
            self.monitor_center.index_source_events(self.current_run, self.run_config, self.events.values(), self.notifications.values(), self.feedback)
            self.monitor_center.refresh_feedback(self.current_run)
        return True

    @property
    def busy(self):
        return self.task is not None

    def _apply_preferences(self, values):
        for key, combo in (('device', self.device), ('model_profile', self.model_profile),
                           ('association_profile', self.association_profile), ('monitoring_profile', self.monitoring_profile),
                           ('confirmation_profile', self.confirmation_profile)):
            combo.setCurrentIndex(combo.findData(values[key]))
        self.confidence.setValue(values['confidence'])
        self.realtime.setChecked(values['realtime'])
        self.alert_sound.setChecked(values['alert_sound'])
        self.recording_mode.setCurrentIndex(self.recording_mode.findData(values['recording_mode']))
        self.storage_max_gb.setValue(values['storage_max_gb'])
        self.storage_days.setValue(values['storage_retention_days'])
        self.storage_automatic.setChecked(values['storage_automatic'])
        self.resize(values['window_width'], values['window_height'])

    def _current_preferences(self):
        return {'device': self.device.currentData(), 'confidence': self.confidence.value(),
                'realtime': self.realtime.isChecked(), 'model_profile': self.model_profile.currentData(),
                'association_profile': self.association_profile.currentData(),
                'confirmation_profile': self.confirmation_profile.currentData(),
                'monitoring_profile': self.monitoring_profile.currentData(), 'alert_sound': self.alert_sound.isChecked(),
                'recording_mode': self.recording_mode.currentData(), 'storage_max_gb': self.storage_max_gb.value(),
                'storage_retention_days': self.storage_days.value(), 'storage_automatic': self.storage_automatic.isChecked(),
                'window_width': self.width(), 'window_height': self.height()}

    def _settings_changed(self, *args):
        if self._loading_preferences or self.preferences is None:
            return
        self._preferences_dirty = True
        self.settings_status.setText('设置已修改，正在自动保存…')
        self.preferences_timer.start()

    def _save_preferences(self, explicit=False):
        if self.preferences is None:
            return
        if not explicit and not self._preferences_dirty:
            return
        self.preferences_timer.stop()
        try:
            self.preferences.save(self._current_preferences())
            self._preferences_dirty = False
            self._preferences_bad = False
            self.settings_status.setText('设置已保存')
            self.settings_status.setToolTip('下次启动时自动恢复设置。视频路径和人工反馈单独管理。')
            self.settings_status.setToolTip(str(self.preferences.path))
        except (ValueError, OSError) as exc:
            self.settings_status.setText(f'设置保存失败；当前选项仍可使用。原因：{exc}')

    def reset_preferences(self):
        if self.busy:
            return
        self._loading_preferences = True
        self._apply_preferences(dict(DEFAULTS))
        self._loading_preferences = False
        self._preferences_dirty = True
        self._save_preferences(explicit=True)
        self._set_controls()

    def _scene_changed(self):
        if self.busy:
            self._sync_scene_selection()
            return
        index = self.model_profile.findData(self.scene_combo.currentData())
        if index >= 0:
            self.model_profile.setCurrentIndex(index)
        self._sync_scene_selection()

    def _sync_scene_selection(self):
        profile = self.model_profile.currentData()
        if profile not in PROFILE_NAMES:
            return
        self.scene_combo.blockSignals(True)
        self.scene_combo.setCurrentIndex(self.scene_combo.findData(profile))
        self.scene_combo.blockSignals(False)
        details = profile_details(profile)
        availability = profile_availability(profile, ASSET_ROOT)
        self.model_file_status.setText('文件已找到' if availability['ready'] else '模型未就绪')
        self.model_file_status.setVisible(not availability['ready'])
        self.model_file_status.setStyleSheet('color:#287158;' if availability['ready'] else 'color:#a94d13;')
        self.model_file_status.setToolTip('\n'.join(availability['missing']) if availability['missing'] else
                                          '文件存在不等于版本已验证；可在设置中检查运行环境。候选方案启动时也会校验版本。')
        self.scene_note.setText('检测中已锁定，结束并保存后可更换方案。' if self.busy else '')
        self.scene_note.hide()
        self.scene_note.setToolTip(f'{details["summary"]}\n实际模型：{details["composition"]}\n{details["compute"]}\n{details["validation"]}')
        self.scene_caption.setText('本次场景' if isinstance(self.task, AnalysisWorker) else '检测场景')
        self.scene_combo.setToolTip(self.scene_note.toolTip() + ('\n检测中暂不可更换。' if self.busy else ''))
        self.scene_combo.setEnabled(not self.busy)
        self.compare_models_button.setEnabled(not self.busy)
        return availability

    def show_current_model_info(self):
        dialog = ModelInfoDialog(self.model_profile.currentData(), self, assets=ASSET_ROOT)
        dialog.exec()
        dialog.deleteLater()

    def open_model_picker(self):
        if self.busy:
            return
        from ui.model_picker import ModelPickerDialog
        dialog = ModelPickerDialog(self.model_profile.currentData(), self, assets=ASSET_ROOT)
        if dialog.exec() == ModelPickerDialog.DialogCode.Accepted and dialog.selected_profile:
            self.model_profile.setCurrentIndex(self.model_profile.findData(dialog.selected_profile))
            self._sync_scene_selection()
        dialog.deleteLater()

    def start_diagnostics(self):
        if self.busy:
            return
        self.diagnostics_result.show()
        self.diagnostics_result.setPlainText('正在检查运行环境…')
        task = DiagnosticsWorker(self.device.currentData(), self.model_profile.currentData(), self)
        task.progress.connect(self.diagnostics_result.setPlainText)
        self._attach_task(task, self._diagnostics_done)

    def _diagnostics_done(self, result):
        if result['ok']:
            from core.diagnostics import diagnostics_text
            self.diagnostics_result.setPlainText(diagnostics_text(result['report']))
            self.state.setText('环境检查完成' if result['report']['ready'] else '环境检查发现待处理项')
        else:
            self.diagnostics_result.setPlainText('环境检查失败：' + result['error'])
            self.state.setText('环境检查失败')

    def retry_with_cpu(self):
        if self.busy:
            return
        self.device.setCurrentIndex(self.device.findData('cpu'))
        self.error_panel.hide()
        self.start_analysis()

    def _set_controls(self):
        for widget in (self.open_button, self.demo_button, self.device, self.confidence,
                       self.model_profile, self.realtime, self.monitoring_profile, self.confirmation_profile, self.alert_sound,
                       self.refresh_button, self.load_button, self.history_filter, self.history_status):
            widget.setEnabled(not self.busy)
        for button in (self.save_settings, self.reset_settings, self.diagnose_button, self.cpu_retry):
            button.setEnabled(not self.busy)
        demo_available = (ASSET_ROOT / 'videos' / 'fireplace_cc0.webm').is_file()
        live = self.input_kind.currentData() == 'camera'
        self.input_kind.setEnabled(not self.busy)
        self.camera_index.hide()
        self.camera_device.setVisible(live)
        self.source.setVisible(not live)
        self.camera_device.setEnabled(not self.busy)
        self.camera_index.setEnabled(not self.busy)
        self.preview_button.setVisible(live)
        self.preview_button.setEnabled(not self.busy)
        self.camera_help.setVisible(live)
        self.open_button.setEnabled(not self.busy and not live)
        self.open_button.setVisible(not live)
        self.demo_button.setVisible(not live)
        self.demo_button.setEnabled(not self.busy and demo_available and not live)
        self.realtime.setEnabled(not self.busy and not live)
        self.sound_status.setEnabled(not self.busy)
        self.start_button.setText('开始监测' if live else '开始分析')
        self.stop_button.setText('停止预览' if isinstance(self.task, CameraPreviewWorker) else '结束监测' if live else '停止并保存')
        self.stop_button.setVisible(isinstance(self.task, (AnalysisWorker, CameraPreviewWorker)))
        self.demo_button.setToolTip('使用本地示例素材' if demo_available else '当前目录未包含示例素材，请点击“打开视频”选择合法素材。')
        self.confidence.setEnabled(not self.busy and self.model_profile.currentData() == 'original')
        self.association_profile.setEnabled(not self.busy and self.model_profile.currentData() == 'brightlight')
        self.start_button.setEnabled((not self.busy or isinstance(self.task, CameraPreviewWorker)) and (live or bool(self.source.property('source_path'))))
        availability = self._sync_scene_selection()
        if availability and not availability['ready']:
            self.start_button.setEnabled(False)
            self.start_button.setToolTip('请先补齐所选方案的模型文件；不会自动改用其他模型。')
        else:
            self.start_button.setToolTip('使用当前所选检测方案开始新记录。')
        self.stop_button.setEnabled(isinstance(self.task, (AnalysisWorker, CameraPreviewWorker)) and not self.task.stop_event.is_set())
        self.export_button.setEnabled(not self.busy and self.current_run is not None
                                      and (self.current_run / 'summary.json').is_file())
        self.folder_button.setEnabled(self.current_run is not None)
        self.report_button.setEnabled(self.export_button.isEnabled())
        for widget in (self.output_label, self.folder_button, self.report_button, self.export_button):
            widget.setVisible(self.current_run is not None)
        self.progress_text.setVisible(isinstance(self.task, AnalysisWorker) or self._historical)
        self.progress.setVisible(not live and isinstance(self.task, AnalysisWorker))
        self.select_notification()
        self.select_event()
        for widget in (self.recording_mode, self.storage_max_gb, self.storage_days, self.storage_automatic, self.storage_scan):
            widget.setEnabled(not self.busy)
        self.storage_cleanup.setEnabled(not self.busy and bool(self.storage_plan and self.storage_plan['candidates']))
        self.storage_release.setEnabled(not self.busy and self.current_run is not None)
        self._storage_selection_changed()
        if hasattr(self, 'nav_buttons'):
            for key in ('camera', 'file'):
                self.nav_buttons[key].setEnabled(not self.busy or self.input_kind.currentData() == key)
            self._sync_navigation()
            self._update_event_summary()
            self._update_mute()

    def _profile_changed(self):
        if self.model_profile.currentData() == 'brightlight':
            self.confidence.setValue(.35)
            self.model_note.setText('候选组合：减少部分反光误报，但部分火焰可能确认更迟或中断；固定阈值 0.35。')
        elif self.model_profile.currentData() == 'modern':
            self.confidence.setValue(.35)
            self.association_profile.setCurrentIndex(self.association_profile.findData('original'))
            self.model_note.setText('YOLO26-S 检测火焰，保留原烟雾检测；阈值 0.35。开发素材有改善，仍需复核。框范围变化会影响视觉指数与趋势，不能据此断言火势加重。')
        elif self.model_profile.currentData() in ('domestic', 'forest', 'uav'):
            self.confidence.setValue(.35)
            self.association_profile.setCurrentIndex(self.association_profile.findData('original'))
            details = profile_details(self.model_profile.currentData())
            self.model_note.setText(details['summary'] + '\n' + details['compute'] + '\n' + details['validation'])
        else:
            self.model_note.setText('原模型：保留现有检测与时序设置。')
            self.association_profile.setCurrentIndex(self.association_profile.findData('original'))
        self._set_controls()

    def _notify_error(self, message):
        self.last_error = message
        self.state.setText('操作失败')
        self.error_message.setText(message)
        gpu_error = any(word in message.lower() for word in ('gpu', 'cuda', 'cudnn', '显存'))
        self.cpu_retry.setVisible(gpu_error and (self.input_kind.currentData() == 'camera' or bool(self.source.property('source_path'))))
        self.error_panel.show()

    def set_source(self, path):
        if self.busy:
            return
        path = Path(path).resolve()
        self.source.setProperty('source_path', str(path))
        self._historical = False
        self.close_inline_replay()
        self.source.setText(path.name)
        self.source.setToolTip(str(path))
        self.input_kind.setCurrentIndex(self.input_kind.findData('file'))
        self._input_changed()
        self._set_controls()

    def _input_changed(self):
        live = self.input_kind.currentData() == 'camera'
        self.source.setText('摄像头' if live else
                            Path(self.source.property('source_path')).name if self.source.property('source_path') else '')
        self.source.setToolTip('输入来自 Windows 摄像头设备；不会连接外网服务。' if live else
                               self.source.property('source_path') or '')
        self.input_status.setText('先预览核对画面，再点击“开始监看”。预览期间不检测、不录像。' if live else '本地视频按原视频时间分析')
        if not self.busy and not self._historical:
            self.state.setText('摄像头未连接' if live else '待分析' if self.source.property('source_path') else '未选择录像')
            self.video.clear_frame('连接摄像头，开始监测' if live else '点击“开始分析”' if self.source.property('source_path') else '选择录像，开始分析')
            self.video.set_context('尚未检测')
            self._paint_alert(badge='尚未检测')
            self.level.setText('等待连接' if live else '等待分析')
            self.evidence.hide()
            self.next_step.hide()
            self.analysis_toggle.setChecked(False)
        self._set_controls()

    def show_camera_help(self):
        QMessageBox.information(self, '安卓手机摄像头接入',
            '本版通过 Windows 摄像头设备接入手机画面。\n\n'
            '1. 在安卓手机与电脑安装配套摄像头工具（例如 DroidCam），按工具说明通过 USB 或同一 Wi-Fi 连接。\n'
            '2. 保持工具连接，确认 Windows 能使用该虚拟摄像头。\n'
            '3. 选择摄像头编号，点击“连接预览”；画面不对时停止后换一个编号。\n'
            '4. 确认画面后点击“开始监测”，软件会结束预览并开始检测。\n\n'
            '也可直接使用 USB 或笔记本内置摄像头。请允许 Windows 桌面应用访问摄像头。\n'
            '实时记录包含已处理的画面，不是完整原始录像。断线后停止本次监控，重新连接会创建新记录。')

    def start_camera_preview(self):
        if self.busy or self.input_kind.currentData() != 'camera':
            return
        self.error_panel.hide()
        self.close_inline_replay()
        self._historical = False
        self._paint_alert(badge='仅预览 · 尚未检测')
        self.next_step.setText('画面无误后，点击“开始监测”。')
        self.next_step.show()
        self.current_run = None
        self.run_config = {}
        self.events = {}
        self.notifications = {}
        self.feedback = {}
        self.active_notification = None
        self._timeline_time = 0
        self._fill_notifications()
        self._fill_events()
        self.last_error = None
        self._last_live_frame_at = monotonic()
        self._last_frame_clock = ''
        self.video.set_context('仅预览 · 尚未检测')
        self.level.setText('仅预览')
        self.score.setText('辅助评分 —（0—100）')
        self.evidence.setText('尚未启用检测')
        self.evidence.show()
        self._show_features(None)
        self.alert_banner.setText('仅预览摄像头画面 · 未启用检测和告警')
        self.curve.set_points([])
        self._show_trend(None)
        self.video.clear_frame('正在连接摄像头…')
        self.state.setText('正在连接摄像头…')
        self.input_status.setText('预览模式 · 未进行火焰／烟雾检测')
        self._attach_task(CameraPreviewWorker(self.camera_index.value(), self), self._preview_done)

    def _preview_done(self, result):
        self.evidence.setText('检测未运行')
        self.evidence.show()
        self.next_step.show()
        if not result['ok']:
            self._paint_alert(badge='连接异常')
            self.level.setText('连接已中断')
            self.next_step.setText('检查连接后重试。')
            self.video.clear_frame('摄像头连接失败或中断\n请检查连接后重试。')
            self.input_status.setText('画面中断 · 当前没有实时监控')
            self._notify_error(result['error'])
        else:
            self._paint_alert(badge='尚未检测')
            self.level.setText('预览已停止')
            self.next_step.setText('点击“开始监测”启用检测。')
            self.state.setText('预览已停止')
            self.input_status.setText('预览已停止；点击“开始监控”进行检测并保存记录。')

    def choose_video(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择本地视频', str(ROOT / 'videos'),
                                             '视频 (*.mp4 *.webm *.avi *.mkv *.mov *.m4v);;所有文件 (*)')
        if path:
            self.set_source(path)

    def _attach_task(self, task, receiver):
        self.task = task
        task.outcome.connect(receiver)
        task.finished.connect(self._task_finished)
        self._set_controls()
        task.start()

    def start_analysis(self):
        if isinstance(self.task, CameraPreviewWorker):
            self._start_after_preview = True
            self.stop_analysis()
            self.start_button.setEnabled(False)
            return
        if self.busy:
            return
        live = self.input_kind.currentData() == 'camera'
        source = str(self.camera_index.value()) if live else self.source.property('source_path')
        if not source:
            return
        if not live and not Path(source).is_file():
            self._notify_error('视频文件不存在，请重新选择。')
            return
        if self.model_profile.currentData() not in ('domestic', 'forest') and not (ASSET_ROOT / 'models' / 'best.pt').is_file():
            self._notify_error('缺少 models/best.pt，请先按说明准备模型权重。')
            return
        if self.model_profile.currentData() == 'brightlight' and not CANDIDATE_PATH.is_file():
            self._notify_error('缺少所选候选模型，请先按项目说明准备亮光检测权重。')
            return
        if self.model_profile.currentData() == 'modern' and not MODERN_PATH.is_file():
            self._notify_error('缺少 YOLO26 火焰权重，请按 MODERN_FIRE_MODEL_TRIAL.md 准备固定版本。迁移包未包含该试用权重。')
            return
        availability = profile_availability(self.model_profile.currentData(), ASSET_ROOT)
        if not availability['ready']:
            self._notify_error('所选方案的模型文件缺失，请查看方案介绍并补齐文件：\n' + '\n'.join(availability['missing']))
            return
        self.last_error = None
        self.error_panel.hide()
        self.close_inline_replay()
        self._historical = False
        self._timeline_time = 0
        storage_exclude = self._storage_exclusions()
        self.current_run = None
        self.run_config = {}
        self.events = {}
        self.notifications = {}
        self.feedback = {}
        self.active_notification = None
        self.notification_table.setRowCount(0)
        self.notification_detail.clear()
        self.feedback_comment.clear()
        self.feedback_status.setText('人工标记仅作为反馈保存，不能替代原视频复核与真值标注。')
        self.alert_banner.setText('正在准备本次分析 · 尚无视觉事件提示')
        self.alert_banner.setStyleSheet('color:#98aac6;')
        self._show_trend(None)
        self.monitoring_status.setText('正在初始化监测方式…')
        self.event_table.setRowCount(0)
        self._reset_event_filters()
        self.detail.clear()
        self.video.clear_frame('正在加载模型…\n可以停止分析，界面仍可操作。')
        self._paint_alert(badge='准备检测 · 尚未开始')
        self.video.set_context('准备检测 · 尚未开始')
        self.curve.set_points([])
        self.progress.setValue(0)
        self.progress.setRange(0, 0 if live else 100)
        self._last_live_frame_at = None
        self.progress_text.setText('正在初始化 · 已处理 0 帧')
        self.level.setText('等待分析')
        self.level.setStyleSheet('font-size:26px;font-weight:700;color:#9bafca;')
        self.score.setText('辅助评分 —（0—100）')
        self.evidence.setText('正在加载模型')
        self.evidence.show()
        self.next_step.hide()
        self._show_features(None)
        self.state.setText('正在加载模型…')
        self.output_label.setText('正在创建本次结果目录')
        self.tabs.setCurrentIndex(0)
        options = AnalysisOptions(source=source, source_kind='camera' if live else 'file', device=self.device.currentData(), conf=self.confidence.value(),
                                  recording_mode=self.recording_mode.currentData() if live else 'full',
                                  storage_max_gb=self.storage_max_gb.value(), storage_retention_days=self.storage_days.value(),
                                  storage_automatic=self.storage_automatic.isChecked(), storage_exclude=storage_exclude,
                                  model_profile=self.model_profile.currentData(),
                                  association_profile=self.association_profile.currentData(),
                                  confirmation_profile=self.confirmation_profile.currentData(),
                                  monitoring_profile=self.monitoring_profile.currentData(),
                                  alert_sound=self.alert_sound.isChecked())
        task = AnalysisWorker(options, self.realtime.isChecked(), self)
        task.scene_evidence_enabled = True
        self.scene_description.context_for_run = lambda run, owner=task: owner.scene_context if str(Path(run).resolve()) == str(Path(owner.output).resolve()) else None
        self.scene_description.latest_assessment = None
        task.started_run.connect(self._run_started)
        self._attach_task(task, self._analysis_done)

    def _run_started(self, info):
        self.current_run = Path(info['output'])
        self.output_label.setText('记录自动保存')
        self.output_label.setToolTip(str(self.current_run))
        if info['stage'] == 'ready':
            config = info['config']
            self.run_config = config
            if config.get('source_kind') == 'camera':
                self.source.setText(f'实时摄像头 · 编号 {config.get("camera_index", 0)}')
                self.input_status.setText('实时监控 · 录像方式见存储管理 · 断线会停止并保存记录')
            self.curve.thresholds = (config['risk_config']['medium_enter'], config['risk_config']['high_enter'])
            if not self.task.stop_event.is_set():
                self.state.setText('正在启动检测…')
                self.state.setToolTip(f'检测方案：{config.get("model_profile_name", "原模型")} · 设备 {config["device"]}')
        self._set_controls()

    def stop_analysis(self):
        if isinstance(self.task, (AnalysisWorker, CameraPreviewWorker)):
            self.task.stop()
            self.state.setText('正在停止预览…' if isinstance(self.task, CameraPreviewWorker) else '正在停止并保存记录…')
            self._set_controls()

    def _refresh_preview(self):
        self._update_mute()
        if isinstance(self.task, CameraPreviewWorker):
            packet = self.task.preview()
            if packet:
                self._last_live_frame_at = monotonic()
                self.video.set_bgr(packet['frame'])
                self._last_frame_clock = datetime.now().strftime('%H:%M:%S')
                delayed = (packet.get('frame_age_s') or 0) > 1.5
                self.video.set_context('预览画面延迟 · 尚未检测' if delayed else '仅预览 · 尚未检测', stale=delayed)
                if not self.task.stop_event.is_set():
                    self.state.setText('摄像头已连接 · 仅预览')
                self.input_status.setText(f'仅预览 · 观测 {packet["timestamp_s"]:.1f}s · 未进行检测')
            elif self._last_live_frame_at is not None and monotonic() - self._last_live_frame_at > 1.5 and not self.task.stop_event.is_set():
                self.input_status.setText('等待新画面 · 当前预览可能已过时')
                self.video.set_context(f'预览未更新 · 上次收到 {self._last_frame_clock or "—"}', stale=True)
                self.state.setText('预览画面未更新 · 尚未进行检测，请检查摄像头连接')
            return
        if not isinstance(self.task, AnalysisWorker):
            return
        notices = self.task.take_notifications()
        if notices:
            self._receive_notifications(notices)
        packet, curve = self.task.preview()
        if packet is None:
            if (self.run_config.get('source_kind') == 'camera' and self._last_live_frame_at is not None
                    and monotonic() - self._last_live_frame_at > 1.5 and not self.task.stop_event.is_set()):
                self.input_status.setText('等待新画面 · 当前显示可能已过时，请检查连接或处理速度')
                self._show_stale_frame()
            return
        self._last_live_frame_at = monotonic()
        self._last_frame_clock = datetime.now().strftime('%H:%M:%S')
        raw = packet.get('raw_frame')
        self.video.set_bgr(raw if raw is not None else packet['frame'], packet.get('detections') if self.show_boxes.isChecked() else None)
        self.curve.set_points(curve)
        count, total = packet['processed_frames'], packet['declared_frames']
        speed = count / packet['elapsed_s'] if packet['elapsed_s'] else 0
        live = packet.get('source_kind') == 'camera'
        self._timeline_time = packet['timestamp_s']
        total_s = total / self.run_config['source_fps'] if total > 0 and self.run_config.get('source_fps') else None
        self.progress_text.setText((f'监看经过 {video_time(packet["timestamp_s"])}' if live else
                                   f'已分析到 {video_time(packet["timestamp_s"])}' + (f' / 视频总长 {video_time(total_s)}' if total_s else ''))
                                  + f'   ·   已检测到 {packet["event_counts"].get("confirmed_events", 0)} 个事件')
        self.progress_text.setToolTip(f'已处理 {count} 帧 · 平均处理速度 {speed:.1f} 帧/秒')
        self.progress.setValue(min(100, round(count / total * 100)) if total > 0 else 0)
        if live:
            recording_name = {'events': '事件片段', 'full': '完整处理录像', 'none': '不录像'}.get(self.run_config.get('recording_mode'), '处理录像')
            self.input_status.setText(f'摄像头 {self.camera_index.value()} · 保存方式：{recording_name}')
            self.input_status.setToolTip(f'采集到处理 {packet.get("frame_age_s", 0):.2f}s · 已跳过 {packet.get("capture_dropped_frames", 0)} 帧')
            self.state.setText(f'监测中 · 更新于 {self._last_frame_clock}')
            self.video.set_context('当前摄像头画面')
        else:
            self.state.setText('录像分析中 · 非实时')
            self.video.set_context('视频分析 · 非实时画面')
        self._show_assessment(packet.get('risk'))
        self.scene_description.show_fusion(packet.get('risk'))
        self._show_monitoring(packet.get('monitoring'))
        self._show_trend(packet.get('trend'))
        for item in packet.get('closed_events') or []:
            self.events[item['event_id']] = {**self.events.get(item['event_id'], {}), **item, 'state': 'closed'}
        for track in (packet.get('temporal') or {}).get('tracks', []):
            if track['state'] != 'confirmed':
                continue
            event = {**self.events.get(track['event_id'], {}), **track}
            event['evidence_image'] = packet.get('evidence_images', {}).get(track['event_id'])
            event['evidence_source'] = packet.get('evidence_sources', {}).get(track['event_id'])
            risk = next((r for r in (packet.get('risk') or {}).get('events', []) if r['event_id'] == track['event_id']), None)
            if risk:
                event['assessment'] = risk
            self.events[track['event_id']] = event
        for event_id, peak in packet.get('risk_peaks', {}).items():
            if event_id in self.events:
                self.events[event_id]['risk_summary'] = peak
        for event_id, peak in packet.get('fusion_peaks', {}).items():
            if event_id in self.events:
                self.events[event_id]['fusion_summary'] = peak
        self.events = dict(sorted(self.events.items())[-200:])
        if self.current_run:
            self.monitor_center.index_source_events(self.current_run, self.run_config, self.events.values(), self.notifications.values(), self.feedback,
                {None: self.task.scene_evidence(),
                 **{identifier: self.task.scene_evidence(identifier) for identifier in self.events}})
        self._fill_events()
        scene_event = self.events.get((packet.get('risk') or {}).get('dominant_event_id'))
        if self.current_run:
            frames = self.task.scene_evidence()
            if frames:
                self.scene_description.observe_live(self.current_run, scene_event, frames)
        if live and (packet.get('frame_age_s') or 0) > 1.5:
            self._show_stale_frame()

    def _show_stale_frame(self):
        self._paint_alert(badge='画面延迟 · 当前状态未知')
        self.video.set_context(f'画面延迟 / 未更新 · 上次收到 {self._last_frame_clock or "—"}', stale=True)
        self.state.setText('画面未更新 · 请检查连接')
        self.level.setText('画面可能已过时')
        self.level.setStyleSheet('font-size:22px;font-weight:700;color:#a35913;')
        self.score.setText('辅助评分 —（0—100）')
        self.evidence.setText('当前现场状态未知')
        self.evidence.show()
        self.next_step.setText('请检查摄像头连接。')
        self.next_step.show()
        self._show_features(None)

    def _show_features(self, event):
        if not event:
            for label in self.feature_labels.values():
                label.setText('—')
            self.explanation.setText('确认持续视觉事件后显示评分依据。')
            self.contributions.clear()
            return
        features = event['features']
        coverage = features.get('coverage_ratio_smoothed')
        growth = features.get('growth_pp_per_s')
        self.feature_labels['coverage'].setText(f'{coverage:.1%}' if coverage is not None else '—')
        self.feature_labels['duration'].setText(f'{features["positive_duration_s"]:.2f}s')
        self.feature_labels['confidence'].setText(f'{features["confidence"]:.2f}' if features.get('confidence') is not None else '暂缺')
        self.feature_labels['trend'].setText(f'{growth:+.2f} 百分点/s' if growth is not None else '等待趋势窗口')
        if not event.get('fresh_evidence', True):
            explanation = '本帧检测暂缺，保留上一次等级与指数；不据此判断火情变小。'
        elif event.get('pending_target'):
            explanation = f'指标已触及 {event["pending_target"]} 条件，正在等待持续证据。'
        else:
            explanation = f'已确认持续{CLASS_NAMES.get(event.get("label"), "视觉")}事件，根据覆盖、时间、趋势和检测匹配分评分。'
        self.explanation.setText(explanation)
        self.contributions.setText('评分贡献\n' + '\n'.join(f'{COMPONENT_NAMES[key]}   {value:+.1f}'
                                                           for key, value in event['score_components'].items()))

    def _show_assessment(self, assessment):
        self._paint_alert((assessment or {}).get('level'))
        self.evidence.show()
        self.next_step.hide()
        if not assessment:
            self.level.setText('本帧未分析')
            self.level.setStyleSheet('font-size:26px;font-weight:700;color:#9bafca;')
            self.score.setText('辅助评分 —（0—100）')
            self.evidence.setText('等待下一次有效观测')
            self._show_features(None)
            return
        if assessment.get('status') == 'not_observed':
            self.level.setText('巡检间隔')
            self.level.setStyleSheet('font-size:26px;font-weight:700;color:#9bafca;')
            self.score.setText('辅助评分 —（0—100）')
            self.evidence.setText('本帧未检测 · 等待下一次巡检')
            self._show_features(None)
            self.explanation.setText('跳过模型调用的画面不代表无火烟，不能据此判定现场安全。')
            return
        pending = assessment['status'] == 'awaiting_confirmation'
        self.level.setText('正在确认' if pending else '未见持续异常' if assessment['level'] == 'NORMAL' else LEVEL_ACTIONS[assessment['level']])
        self.level.setStyleSheet(f'font-size:26px;font-weight:700;color:{"#efba59" if pending else LEVEL_COLORS[assessment["level"]]};')
        scene_attention = bool((assessment.get('fusion') or {}).get('scene_only_hazard'))
        self.score.setText(f'辅助评分 {assessment["score"]:.1f}（0—100）' if assessment['events'] or scene_attention else '辅助评分 —（0—100）')
        event = next((r for r in assessment['events'] if r['event_id'] == assessment['dominant_event_id']), None)
        self.evidence.setText('已检测到目标，正在确认持续情况' if pending else '')
        self.evidence.setVisible(bool(event or pending))
        if event:
            duration = event.get('features', {}).get('positive_duration_s')
            self.evidence.setText(f'检测到{CLASS_NAMES[event["label"]]}' +
                                  (' · 本帧检测暂缺' if not event['fresh_evidence'] else
                                   f' · 持续 {duration:.1f} 秒' if duration is not None else ''))
        self._show_features(event)
        if (assessment.get('fusion') or {}).get('scene_only_hazard'):
            self.level.setText(LEVEL_ACTIONS[assessment['level']] + ' · 场景异常线索')
            self.evidence.setText('YOLO尚无确认事件，请核对AI描述与原画面')
            self.evidence.show()
            self.explanation.setText('综合指数来自当前可用的场景异常线索；尚无烟火检测确认，不代表已确认火灾。')

    def _fill_events(self):
        selected = self.event_table.currentRow()
        selected_id = self.event_table.item(selected, 0).data(Qt.ItemDataRole.UserRole) if selected >= 0 else None
        self.event_table.blockSignals(True)
        values = list(reversed(list(self.events.values())))
        self.event_table.setRowCount(len(values))
        selected_row = None
        for row, event in enumerate(values):
            risk = event.get('fusion_summary') or event.get('risk_summary') or {}
            handling = event_review_state(event['event_id'], self.notifications.values(), self.feedback)
            style = presentation(handling)
            state_label = style['label'] + ('\n' + style['conclusion'] if style['conclusion'] and handling != 'resolved' else '')
            cells = [f'#{event["event_id"]}', CLASS_NAMES.get(event['label'], event['label']) + '\n' + LEVEL_ACTIONS.get(risk.get('peak_level'), '待观察'),
                     video_time(event['confirmed_at_s']),
                     f'{event["closed_at_s"]:.2f}s' if 'closed_at_s' in event else '持续 / 检测暂缺' if not event.get('present_this_frame', True) else '持续中',
                     LEVEL_NAMES.get(risk.get('peak_level'), '—'), f'{risk["peak_score"]:.1f}' if 'peak_score' in risk else '—',
                     state_label]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, event['event_id'])
                item.setToolTip(text)
                if column == 6:
                    paint_status_cell(item, handling)
                if column in (1, 4) and risk.get('peak_level') in LEVEL_COLORS:
                    item.setBackground(QColor(LEVEL_BACKGROUNDS[risk['peak_level']]))
                    item.setForeground(QColor(LEVEL_COLORS[risk['peak_level']]))
                    item.setToolTip('当时提示：' + LEVEL_NAMES[risk['peak_level']])
                self.event_table.setItem(row, column, item)
            self.event_table.setRowHeight(row, 88)
            if event['event_id'] == selected_id:
                selected_row = row
        if selected_row is not None:
            self.event_table.selectRow(selected_row)
        elif values and selected_id is None:
            self.event_table.selectRow(0)
        self.event_table.blockSignals(False)
        self._update_event_summary()
        self.filter_events()

    def _reset_event_filters(self):
        self.event_query.clear()
        for combo in (self.event_class, self.event_level, self.event_handling):
            combo.setCurrentIndex(0)

    def filter_events(self, *args):
        query = self.event_query.text().strip().lower()
        shown = 0
        for row in range(self.event_table.rowCount()):
            item = self.event_table.item(row, 0)
            if item is None:
                continue
            event = self.events[item.data(Qt.ItemDataRole.UserRole)]
            risk = event.get('fusion_summary') or event.get('risk_summary') or {}
            state = event_review_state(event['event_id'], self.notifications.values(), self.feedback)
            searchable = ' '.join(self.event_table.item(row, col).text() for col in range(self.event_table.columnCount())).lower()
            visible = (query in searchable
                       and (not self.event_class.currentData() or event['label'] == self.event_class.currentData())
                       and (not self.event_level.currentData() or risk.get('peak_level') == self.event_level.currentData())
                       and (matches(state, self.event_handling.currentData())
                            or (self.event_handling.currentData() == 'acknowledged' and any(
                                f.get('event_id') == event['event_id'] and (f.get('acknowledged') or f.get('action') == 'acknowledged') for f in self.feedback.values()))
                            ))
            self.event_table.setRowHidden(row, not visible)
            shown += int(visible)
        self.event_count.setText(f'当前显示 {shown} / {self.event_table.rowCount()} 条事件（最多载入 200 条；完整记录可导出）')
        self.event_list_count.setText(f'{shown} 条')
        self.event_list_count.setToolTip(self.event_count.text())
        self.select_event()

    def _selected_event(self):
        row = self.event_table.currentRow()
        if row < 0 or self.event_table.isRowHidden(row):
            return None
        item = self.event_table.item(row, 0)
        return self.events.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def open_notification_event(self):
        notice = self._selected_notification()
        if not notice or notice.get('event_id') not in self.events:
            return
        self._reset_event_filters()
        for row in range(self.event_table.rowCount()):
            if self.event_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == notice['event_id']:
                self.event_table.selectRow(row)
                self.tabs.setCurrentWidget(self.event_page)
                break

    def open_event_notification(self):
        event = self._selected_event()
        if not event:
            return
        latest = next((n for n in reversed(list(self.notifications.values()))
                       if n.get('event_id') == event['event_id'] and n.get('kind') != 'event_closed'), None)
        if latest:
            for row in range(self.notification_table.rowCount()):
                if self.notification_table.item(row, 0).data(Qt.ItemDataRole.UserRole) == latest['notification_id']:
                    self.notification_table.selectRow(row)
                    self.tabs.setCurrentWidget(self.notification_page)
                    break

    def replay_event(self):
        event = self._selected_event()
        if event:
            self._open_replay(event)

    def replay_notification(self):
        notice = self._selected_notification()
        if notice:
            event = self.events.get(notice['event_id']) or {'event_id': notice['event_id'], 'confirmed_at_s': notice['timestamp_s']}
            self._open_replay(event, notice['timestamp_s'])

    def _open_replay(self, event, focus_s=None):
        if self.busy or not self.current_run:
            return
        if event['event_id'] not in self.events:
            self._notify_error('对应事件不在当前显示范围内，请导出完整记录查看。')
            return
        self.focus_event(event['event_id'])
        media = review_media(self.current_run, self.run_config, event.get('confirmed_at_s') if focus_s is None else focus_s)
        if not media:
            self._notify_error('没有可回放的原视频、完整录像或事件片段；本次可能选择了“不录像”。仍可查看事件截图和日志。')
            return
        from ui.replay import ReplayDialog
        self.close_inline_replay()
        dialog = ReplayDialog(media, review_window(event, focus_s), event['event_id'], self.replay_host, embedded=True)
        dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.setMinimumSize(220, 340)
        self.inline_replay = dialog
        self.replay_host_layout.addWidget(dialog)
        self.review_video.hide()
        self.replay_host.show()
        self.tabs.setCurrentWidget(self.event_page)
        self.replays.append(dialog)
        dialog.destroyed.connect(lambda obj=None, ref=dialog: self._replay_closed(ref))
        dialog.show()

    def _replay_closed(self, dialog):
        if dialog in self.replays:
            self.replays.remove(dialog)
        if self.inline_replay is dialog:
            self.inline_replay = None
            if isValid(self.replay_host):
                self.replay_host.hide()
            if isValid(self.review_video):
                self.review_video.show()
        if self.closing and not self.busy and not self.replays and isValid(self):
            QTimer.singleShot(0, self.close)

    def select_event(self):
        event = self._selected_event()
        self._show_event_review(event)
        self.event_analysis_button.setEnabled(bool(event))
        self.event_replay.setEnabled(bool(event and self.current_run and not self.busy))
        self.event_replay.setToolTip('分析结束或停止并保存后可回放。')
        self.event_notice.setEnabled(bool(event and any(n.get('event_id') == event['event_id']
                                        and n.get('kind') != 'event_closed' for n in self.notifications.values())))
        self.event_image.setEnabled(bool(event and event.get('evidence_image') and not self.busy))
        if not event:
            self.detail.clear()
            return
        raw_risk = event.get('risk_summary') or {}
        risk = event.get('fusion_summary') or raw_risk
        lines = [f'事件 #{event["event_id"]} · {CLASS_NAMES.get(event["label"], event["label"])}',
                 f'首次检测：{event["started_at_s"]:.2f}s', f'确认时间：{event["confirmed_at_s"]:.2f}s',
                 f'核查优先程度：{LEVEL_NAMES.get(risk.get("peak_level"), "—")}',
                 f'辅助评分峰值：{risk.get("peak_score", "—")}',
                 '最新提示处理：' + REVIEW_NAMES[event_review_state(event['event_id'], self.notifications.values(), self.feedback)],
                 f'结束原因：{CLOSE_NAMES.get(event.get("close_reason"), "事件仍在观察")}',
                 '结束时间是软件边界，不代表实际火情结束。']
        confirmation = event.get('confirmation_evidence') or {}
        route = '稳定检测快速通道' if confirmation.get('confirmation_route') == 'stable_fast' else '标准观察通道'
        lines.append(f'确认依据：{route}；观察 {confirmation.get("observation_s", 0):.2f}s，'
                     f'检测持续率 {confirmation.get("presence_ratio", 0):.0%}')
        peak = raw_risk.get('peak_evidence')
        if peak:
            lines.append(f'原始检测评分峰值时刻：{peak["timestamp_s"]:.2f}s')
            lines.extend(f'{COMPONENT_NAMES[key]}：+{value:.1f}' for key, value in peak['components'].items())
        if not self.busy and risk:
            stored_level = risk.get('peak_level', 'NORMAL')
            self._paint_alert(stored_level, historical=True)
            self.level.setText('当时建议：' + LEVEL_ACTIONS.get(stored_level, '请查看记录'))
            self.score.setText(f'事件辅助评分峰值 {risk.get("peak_score", "—")}（0—100）')
            if peak:
                self._show_features({'features': peak['features'], 'label': event['label'],
                                     'score_components': peak['components'], 'fresh_evidence': True})
                if event.get('fusion_summary'):
                    self.explanation.setText('原始检测依据；上方处理建议采用已保存的综合研判等级。')
            else:
                self._show_features(None)
            self.evidence.setText('历史事件依据 · 请回看原画面')
        image = event.get('evidence_image')
        if image:
            lines.append(f'确认截图：{image}')
            if not self.busy and self._show_event_image(self.video, event):
                self.video.set_context('事件证据截图 · 非实时画面')
        self.detail.setPlainText('\n'.join(lines))

    def _analysis_done(self, result):
        self._refresh_preview()
        self.progress.setRange(0, 100)
        if self.input_kind.currentData() == 'camera':
            self.input_status.setText('本次实时监控已结束 · 当前画面为最后一帧，不是实时画面')
        if result.get('output'):
            self.current_run = Path(result['output'])
        if not result['ok']:
            suffix = '\n已生成的记录保留在结果目录。' if self.current_run else ''
            self._notify_error(f'分析失败：{result["error"]}{suffix}')
        if self.current_run and (self.current_run / 'summary.json').is_file():
            try:
                record = load_run(self.current_run)
                self._apply_record(record, historical=False)
                if result.get('ok'):
                    self.scene_description.request_auto()
            except (ValueError, OSError) as exc:
                self._notify_error(f'结果读取失败：{exc}')

    def _apply_record(self, record, historical):
        self.close_inline_replay()
        self._historical = True
        self.current_run = Path(record['output'])
        self.scene_description.invalidate_run(self.current_run)
        self.run_config = record.get('config') or {}
        self.event_table.setRowCount(0)
        self.events = {e['event_id']: e for e in record['events'][-200:]}
        self._reset_event_filters()
        self.notifications = {}
        self.feedback = {f['notification_id']: f for f in record.get('feedback', []) if 'notification_id' in f}
        self.active_notification = None
        self._receive_notifications(record.get('notifications', []), historical=True)
        self._fill_events()
        self._show_trend(record.get('trend'), historical=True)
        self.alert_banner.setText('记录已保存，可在“事件记录”中回看。')
        self.alert_banner.setStyleSheet('color:#935b0d;')
        summary = record['summary']
        self._paint_alert((summary.get('scene_fusion') or summary.get('risk') or {}).get('peak_level'), historical=True)
        self.level.setText('本次检测已结束')
        self._show_monitoring(summary.get('monitoring'))
        prefix = '历史记录 · ' if historical else '检测已结束 · '
        self.state.setText(prefix + STATUS_NAMES.get(summary['status'], summary['status']))
        self.video.set_context('已保存记录 · 非实时画面')
        self.progress_text.setText(f'本次检测已结束 · 检测到 {(summary.get("temporal") or {}).get("confirmed_events", 0)} 个事件')
        self.progress_text.setToolTip(f'已处理 {summary["processed_frames"]} 帧 · 平均 {summary.get("end_to_end_fps", 0):.1f} 帧/秒')
        self.progress.setValue(100 if summary['status'].startswith('completed') else self.progress.value())
        warnings = summary.get('warnings', [])
        self.output_label.setText('记录已保存' + (' · 视频信息需核对' if warnings else ''))
        self.output_label.setToolTip('\n'.join(warnings) if warnings else str(self.current_run))
        if historical:
            source = record['config'].get('source')
            if source and record['config'].get('source_kind') == 'camera':
                self.camera_index.setValue(record['config'].get('camera_index', 0))
                self.input_kind.setCurrentIndex(self.input_kind.findData('camera'))
                self._input_changed()
            elif source:
                self.input_kind.setCurrentIndex(self.input_kind.findData('file'))
                self.source.setProperty('source_path', source)
                self.source.setText(Path(source).name)
                self.source.setToolTip(source)
            self.video.clear_frame('历史记录已载入\n在事件记录中选中事件，查看截图或回看前后视频。')
            risk = summary.get('scene_fusion') or summary.get('risk')
            self.level.setText('历史检测结果' if risk else '历史记录')
            self.level.setStyleSheet('font-size:23px;font-weight:700;color:#9bafca;')
            self.score.setText(f'辅助评分峰值 {risk["peak_score"]:.1f}（0—100）' if risk else '辅助评分 —')
            self.evidence.setText('历史汇总 · 不是当前实时状态')
            self._show_features(None)
            self.explanation.setText('选择事件查看历史峰值依据。')
            self.tabs.setCurrentIndex(3 if self.events else 0)
        self._update_event_summary()
        self.scene_description.set_target(self.current_run, self._selected_event(), self.run_config, running=False)
        self.scene_description.show_fusion(record.get('fusion'))

    def _task_finished(self):
        task = self.task
        self.task = None
        if task:
            task.deleteLater()
        self.refresh_history()
        self._set_controls()
        if self.input_kind.currentData() == 'camera' and isinstance(task, (AnalysisWorker, CameraPreviewWorker)):
            self.alert_banner.setText('监测已结束，记录已保存。')
            self.video.set_context('预览已停止 · 尚未检测' if isinstance(task, CameraPreviewWorker) else '监测已结束 · 非实时画面')
        if getattr(self, '_start_after_preview', False):
            self._start_after_preview = False
            if not self.closing:
                QTimer.singleShot(0, self.start_analysis)
        if self.closing:
            QTimer.singleShot(0, self.close)

    def refresh_history(self):
        if self.busy:
            return
        self.history = list_runs(ROOT / 'outputs')
        if hasattr(self, 'monitor_center'):
            self.monitor_center.restore_saved_events(self.history)
        self.history_table.setRowCount(len(self.history))
        for row, record in enumerate(self.history):
            summary, config = record['summary'], record['config']
            risk = summary.get('scene_fusion') or summary.get('risk') or {}
            name = Path(record['output']).name
            cells = [record_label(name), Path(config.get('source', '')).name,
                     STATUS_NAMES.get(summary['status'], summary['status']), str(summary['processed_frames']),
                     str((summary.get('temporal') or {}).get('confirmed_events', 0)), LEVEL_ACTIONS.get(risk.get('peak_level'), '未记录')]
            for column, value in enumerate(cells):
                self.history_table.setItem(row, column, QTableWidgetItem(value))
        self.filter_history(self.history_filter.text())

    def filter_history(self, text):
        query = text.strip().lower()
        for row in range(self.history_table.rowCount()):
            searchable = ' '.join(self.history_table.item(row, col).text() for col in (0, 1)).lower()
            status = self.history[row]['summary']['status']
            selected_status = self.history_status.currentData()
            matches_status = not selected_status or (status.startswith('completed') if selected_status == 'completed' else status == selected_status)
            self.history_table.setRowHidden(row, query not in searchable or not matches_status)

    def open_history(self):
        if self.busy:
            return
        row = self.history_table.currentRow()
        if row < 0 or self.history_table.isRowHidden(row):
            self._notify_error('请先选中一条历史分析记录。')
            return
        self.state.setText('正在读取历史记录…')
        self._attach_task(HistoryWorker(self.history[row]['output'], self), self._history_done)

    def _history_done(self, result):
        if not result['ok']:
            self._notify_error(f'无法读取记录：{result["error"]}')
            return
        self._apply_record(result['record'], historical=True)
        config = result['record']['config'].get('risk_config') or {}
        self.curve.set_points(result['curve'], (config.get('medium_enter', 45), config.get('high_enter', 70)))

    def open_output(self):
        if self.current_run:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_run)))

    def choose_export(self):
        if self.busy or self.current_run is None:
            return
        dialog = QFileDialog(self, '导出本次分析记录', str(ROOT / 'outputs'))
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setNameFilter('ZIP 文件 (*.zip)')
        dialog.setDefaultSuffix('zip')
        dialog.selectFile(self.current_run.name + '.zip')
        if dialog.exec():
            self.start_export(dialog.selectedFiles()[0])

    def start_export(self, path):
        if self.busy or self.current_run is None:
            return
        self.state.setText('正在导出记录…')
        self._attach_task(ExportWorker(self.current_run, path, self), self._export_done)

    def _export_done(self, result):
        if result['ok']:
            self.state.setText('导出完成')
            self.output_label.setText(f'已导出：{Path(result["path"]).name}')
        else:
            self._notify_error(f'导出失败：{result["error"]}')

    def closeEvent(self, event):
        scene_ready = self.scene_description.shutdown()
        center_ready = not hasattr(self, 'monitor_center') or self.monitor_center.shutdown()
        if not scene_ready or not center_ready:
            self.closing = True
            self.stop_analysis()
            self.state.setText('正在结束AI与监测任务并保存记录，随后关闭…')
            QTimer.singleShot(200, self.close)
            event.ignore()
            return
        if self.replays:
            self.closing = True
            for dialog in list(self.replays):
                dialog.close()
        if self.busy:
            self.closing = True
            self.stop_analysis()
            if not isinstance(self.task, (AnalysisWorker, CameraPreviewWorker)):
                self.state.setText('正在完成记录操作，随后关闭…')
            event.ignore()
        elif self.replays:
            event.ignore()
        else:
            self.timer.stop()
            if self.preferences and not self._preferences_bad:
                self._preferences_dirty = True
            self._save_preferences()
            event.accept()

    def _open_monitor_record(self, path, event_id):
        if self.busy:
            self._notify_error('请先停止单路分析或等待当前记录操作完成，再打开这条记录。')
            return
        def done(result):
            self._history_done(result)
            if result.get('ok'):
                if event_id is None:
                    self.navigate('file')
                    self.scene_description.set_target(self.current_run, None, self.run_config, running=False)
                else:
                    self.focus_event(event_id)
        self._attach_task(HistoryWorker(path, self), done)
