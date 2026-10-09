"""A single-video desktop workflow with explicit running and archived states."""
from pathlib import Path

from PySide6.QtCore import QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFrame,
                              QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                              QMainWindow, QMessageBox, QProgressBar, QPushButton,
                              QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                              QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from core.pipeline import AnalysisOptions
from core.model_profiles import PROFILE_NAMES, CANDIDATE_PATH
from core.records import list_runs, load_run
from core.runtime import ROOT
from ui.widgets import RiskCurve, VideoView
from ui.worker import AnalysisWorker, ExportWorker, HistoryWorker

LEVEL_NAMES = {'NORMAL': '无确认事件', 'LOW': '低 · LOW', 'MEDIUM': '中 · MEDIUM', 'HIGH': '高 · HIGH'}
LEVEL_COLORS = {'NORMAL': '#9bafca', 'LOW': '#55d8c3', 'MEDIUM': '#efba59', 'HIGH': '#ff7888'}
CLASS_NAMES = {'fire': '火焰', 'smoke': '烟雾'}
COMPONENT_NAMES = {'class_base': '确认类别', 'box_coverage': '检测框覆盖', 'positive_duration': '持续观察',
                   'positive_growth': '覆盖增长', 'model_confidence': '模型置信度'}
STATUS_NAMES = {'completed': '分析完成', 'completed_with_frame_count_warning': '分析完成 · 帧数有差异',
                'stopped_by_user': '已停止 · 当前结果已保存', 'frame_limit_reached': '已达到帧数限制', 'failed': '分析失败'}
CLOSE_NAMES = {'stream_end': '视频结束', 'stopped_by_user': '手动停止', 'detection_timeout': '检测超时',
               'processing_error': '处理异常', 'frame_limit_reached': '帧数限制'}

STYLE = '''
QMainWindow, QWidget { background:#0c1320; color:#e5ecf8; }
QWidget { font-size:13px; }
QLabel#brand { font-size:26px; font-weight:700; }
QLabel#muted { color:#98aac6; }
QFrame#card { background:#101a2b; border:1px solid #26344c; border-radius:10px; }
QFrame#card QLabel { background:transparent; }
QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit { background:#172238; border:1px solid #2c3c57; border-radius:5px; padding:6px; }
QPushButton { background:#202e47; border:1px solid #31425f; border-radius:6px; padding:8px 14px; }
QPushButton:hover { background:#304461; }
QPushButton:disabled { color:#66768f; background:#141f32; border-color:#24324a; }
QPushButton#primary { background:#37baa6; color:#081f22; font-weight:700; border:0; }
QPushButton#primary:disabled { background:#204b4a; color:#758f90; }
QPushButton#stop { color:#ff9da9; }
QPushButton#stop:disabled { color:#66768f; }
QTableWidget { background:#101a2b; alternate-background-color:#142037; gridline-color:#25344d; border:0; }
QTableWidget::item { padding:5px; }
QTableWidget::item:selected { background:#254658; }
QHeaderView::section { background:#1b2941; color:#aebed5; padding:6px; border:0; }
QTabWidget::pane { border:1px solid #26344c; }
QTabBar::tab { background:#172238; color:#9fb0ca; padding:8px 18px; }
QTabBar::tab:selected { background:#263b52; color:#55d8c3; }
QProgressBar { border:1px solid #273650; border-radius:4px; background:#172238; height:8px; }
QProgressBar::chunk { background:#37baa6; }
QScrollArea { border:0; background:transparent; }
'''


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    widget.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    return widget


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('焰鉴 · 视频火情分析与预警')
        self.setMinimumSize(980, 740)
        self.resize(1280, 920)
        self.setStyleSheet(STYLE)
        self.task = None
        self.current_run = None
        self.events = {}
        self.history = []
        self.closing = False
        self.last_error = None
        self._build()
        self.timer = QTimer(self)
        self.timer.setInterval(50)
        self.timer.timeout.connect(self._refresh_preview)
        self.timer.start()
        self.refresh_history()
        self._set_controls()

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 15, 20, 12)
        layout.setSpacing(10)
        heading = QHBoxLayout()
        brand = QLabel('焰鉴  /  FireGuard')
        brand.setObjectName('brand')
        heading.addWidget(brand)
        heading.addStretch()
        self.state = QLabel('就绪 · 本地视频分析')
        self.state.setObjectName('muted')
        heading.addWidget(self.state)
        layout.addLayout(heading)
        subtitle = QLabel('检测 → 时序确认 → 风险提示 → 事件证据    |    辅助分析原型')
        subtitle.setObjectName('muted')
        layout.addWidget(subtitle)
        toolbar = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setReadOnly(True)
        self.source.setPlaceholderText('请选择本地视频')
        self.source.setMinimumWidth(200)
        self.open_button = QPushButton('打开视频')
        self.open_button.clicked.connect(self.choose_video)
        self.demo_button = QPushButton('壁炉示例')
        self.demo_button.clicked.connect(lambda: self.set_source(ROOT / 'videos' / 'fireplace_cc0.webm'))
        self.start_button = QPushButton('开始分析')
        self.start_button.setObjectName('primary')
        self.start_button.clicked.connect(self.start_analysis)
        self.stop_button = QPushButton('停止')
        self.stop_button.setObjectName('stop')
        self.stop_button.clicked.connect(self.stop_analysis)
        for widget in (self.source, self.open_button, self.demo_button, self.start_button, self.stop_button):
            toolbar.addWidget(widget, 1 if widget is self.source else 0)
        layout.addLayout(toolbar)
        options = QHBoxLayout()
        self.device = QComboBox()
        for label, value in [('自动选择设备', 'auto'), ('仅 CPU', 'cpu'), ('GPU 0', '0')]:
            self.device.addItem(label, value)
        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(0.01, 1.0)
        self.confidence.setSingleStep(0.05)
        self.confidence.setDecimals(2)
        self.confidence.setValue(0.35)
        self.realtime = QCheckBox('按原视频速度显示（性能允许时）')
        self.realtime.setChecked(True)
        options.addWidget(QLabel('推理设备'))
        options.addWidget(self.device)
        options.addSpacing(15)
        options.addWidget(QLabel('检测阈值'))
        options.addWidget(self.confidence)
        options.addSpacing(15)
        options.addWidget(self.realtime)
        options.addStretch()
        layout.addLayout(options)
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
        layout.addLayout(model_options)
        self.model_profile.currentIndexChanged.connect(self._profile_changed)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 5, 0)
        self.video = VideoView()
        left_layout.addWidget(self.video, 1)
        self.progress_text = QLabel('视频时间 —    |    已处理 0 帧')
        self.progress_text.setObjectName('muted')
        left_layout.addWidget(self.progress_text)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 100)
        left_layout.addWidget(self.progress)
        curve_title = QLabel('风险指数曲线 · 视频时间 · 最近 120 秒（显示采样）')
        curve_title.setObjectName('muted')
        left_layout.addWidget(curve_title)
        self.curve = RiskCurve()
        left_layout.addWidget(self.curve)
        body.addWidget(left)

        card = QFrame()
        card.setObjectName('card')
        card.setMinimumWidth(295)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 12, 18, 12)
        self.level = QLabel('尚未分析')
        self.level.setStyleSheet('font-size:26px;font-weight:700;color:#9bafca;')
        self.score = QLabel('指数 — / 100')
        self.score.setStyleSheet('font-size:22px;')
        self.evidence = QLabel('等待输入')
        self.evidence.setWordWrap(True)
        self.evidence.setObjectName('muted')
        card_layout.addWidget(QLabel('视觉风险提示'))
        for widget in (self.level, self.score, self.evidence):
            card_layout.addWidget(widget)
        grid = QGridLayout()
        self.feature_labels = {}
        for index, (key, title) in enumerate([('coverage', '框并集覆盖'), ('duration', '正检测时间'),
                                             ('trend', '覆盖趋势'), ('confidence', '模型置信度')]):
            grid.addWidget(QLabel(title), index, 0)
            value = QLabel('—')
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            grid.addWidget(value, index, 1)
            self.feature_labels[key] = value
        card_layout.addLayout(grid)
        self.explanation = QLabel('确认持续视觉事件后显示评分依据。')
        self.explanation.setWordWrap(True)
        card_layout.addWidget(self.explanation)
        self.contributions = QLabel('')
        self.contributions.setWordWrap(True)
        self.contributions.setObjectName('muted')
        card_layout.addWidget(self.contributions)
        card_layout.addStretch()
        note = QLabel('指数来自启发式规则，不是火灾概率。\n“无确认事件”不代表现场安全。')
        note.setObjectName('muted')
        note.setWordWrap(True)
        card_layout.addWidget(note)
        scroll = QScrollArea()
        scroll.setMinimumWidth(315)
        scroll.setWidgetResizable(True)
        scroll.setWidget(card)
        body.addWidget(scroll)
        body.setSizes([840, 340])
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 1)
        splitter.addWidget(body)

        self.tabs = QTabWidget()
        event_page = QWidget()
        event_layout = QHBoxLayout(event_page)
        self.event_table = table(['事件', '类别', '确认时间', '结束 / 状态', '最高等级', '最高指数'])
        self.event_table.itemSelectionChanged.connect(self.select_event)
        event_layout.addWidget(self.event_table, 3)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText('选择事件查看时间边界、评分依据和证据截图。')
        event_layout.addWidget(self.detail, 2)
        self.tabs.addTab(event_page, '事件记录（最近 200 条）')
        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_actions = QHBoxLayout()
        self.refresh_button = QPushButton('刷新记录')
        self.refresh_button.clicked.connect(self.refresh_history)
        self.load_button = QPushButton('查看选中记录')
        self.load_button.clicked.connect(self.open_history)
        self.history_filter = QLineEdit()
        self.history_filter.setPlaceholderText('按视频名或运行时间筛选')
        self.history_filter.textChanged.connect(self.filter_history)
        for widget in (self.history_filter, self.refresh_button, self.load_button):
            history_actions.addWidget(widget)
        history_layout.addLayout(history_actions)
        self.history_table = table(['运行时间', '视频', '状态', '帧数', '确认事件', '最高等级'])
        self.history_table.doubleClicked.connect(lambda index: self.open_history())
        history_layout.addWidget(self.history_table)
        self.tabs.addTab(history_page, '历史分析')
        splitter.addWidget(self.tabs)
        splitter.setSizes([590, 210])
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)
        footer = QHBoxLayout()
        self.output_label = QLabel('结果自动保存在 outputs 中')
        self.output_label.setObjectName('muted')
        self.output_label.setWordWrap(True)
        footer.addWidget(self.output_label, 1)
        self.folder_button = QPushButton('结果目录')
        self.folder_button.clicked.connect(self.open_output)
        self.export_button = QPushButton('导出记录 ZIP')
        self.export_button.clicked.connect(self.choose_export)
        footer.addWidget(self.folder_button)
        footer.addWidget(self.export_button)
        layout.addLayout(footer)

    @property
    def busy(self):
        return self.task is not None

    def _set_controls(self):
        for widget in (self.open_button, self.demo_button, self.device, self.confidence,
                       self.model_profile, self.realtime, self.refresh_button, self.load_button, self.history_filter):
            widget.setEnabled(not self.busy)
        self.confidence.setEnabled(not self.busy and self.model_profile.currentData() == 'original')
        self.start_button.setEnabled(not self.busy and bool(self.source.property('source_path')))
        self.stop_button.setEnabled(isinstance(self.task, AnalysisWorker) and not self.task.stop_event.is_set())
        self.export_button.setEnabled(not self.busy and self.current_run is not None
                                      and (self.current_run / 'summary.json').is_file())
        self.folder_button.setEnabled(self.current_run is not None)

    def _profile_changed(self):
        if self.model_profile.currentData() == 'brightlight':
            self.confidence.setValue(.35)
            self.model_note.setText('候选组合：减少部分反光误报，但部分火焰可能确认更迟或中断；固定阈值 0.35。')
        else:
            self.model_note.setText('原模型：保留现有检测与时序设置。')
        self._set_controls()

    def _notify_error(self, message):
        self.last_error = message
        self.state.setText('操作失败')
        QMessageBox.warning(self, '焰鉴', message)

    def set_source(self, path):
        if self.busy:
            return
        path = Path(path).resolve()
        self.source.setProperty('source_path', str(path))
        self.source.setText(path.name)
        self.source.setToolTip(str(path))
        self._set_controls()

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
        if self.busy:
            return
        source = self.source.property('source_path')
        if not source:
            return
        if not Path(source).is_file():
            self._notify_error('视频文件不存在，请重新选择。')
            return
        if not (ROOT / 'models' / 'best.pt').is_file():
            self._notify_error('缺少 models/best.pt，请先按说明准备模型权重。')
            return
        if self.model_profile.currentData() == 'brightlight' and not CANDIDATE_PATH.is_file():
            self._notify_error('缺少所选候选模型，请先按项目说明准备亮光检测权重。')
            return
        self.last_error = None
        self.current_run = None
        self.events = {}
        self.event_table.setRowCount(0)
        self.detail.clear()
        self.video.clear_frame('正在加载模型…\n可以停止分析，界面仍可操作。')
        self.curve.set_points([])
        self.progress.setValue(0)
        self.progress_text.setText('正在初始化 · 已处理 0 帧')
        self.level.setText('等待分析')
        self.level.setStyleSheet('font-size:26px;font-weight:700;color:#9bafca;')
        self.score.setText('指数 — / 100')
        self.evidence.setText('正在加载模型')
        self._show_features(None)
        self.state.setText('正在加载模型…')
        self.output_label.setText('正在创建本次结果目录')
        self.tabs.setCurrentIndex(0)
        options = AnalysisOptions(source=source, device=self.device.currentData(), conf=self.confidence.value(),
                                  model_profile=self.model_profile.currentData())
        task = AnalysisWorker(options, self.realtime.isChecked(), self)
        task.started_run.connect(self._run_started)
        self._attach_task(task, self._analysis_done)

    def _run_started(self, info):
        self.current_run = Path(info['output'])
        self.output_label.setText(f'本次结果：{self.current_run.name}')
        if info['stage'] == 'ready':
            config = info['config']
            self.curve.thresholds = (config['risk_config']['medium_enter'], config['risk_config']['high_enter'])
            if not self.task.stop_event.is_set():
                self.state.setText(f'正在分析 · {config.get("model_profile_name", "原模型")} · 设备 {config["device"]}')
        self._set_controls()

    def stop_analysis(self):
        if isinstance(self.task, AnalysisWorker):
            self.task.stop()
            self.state.setText('正在停止并保存记录…')
            self._set_controls()

    def _refresh_preview(self):
        if not isinstance(self.task, AnalysisWorker):
            return
        packet, curve = self.task.preview()
        if packet is None:
            return
        self.video.set_bgr(packet['frame'])
        self.curve.set_points(curve)
        count, total = packet['processed_frames'], packet['declared_frames']
        speed = count / packet['elapsed_s'] if packet['elapsed_s'] else 0
        self.progress_text.setText(f'视频时间 {packet["timestamp_s"]:.2f}s   |   已处理 {count} 帧'
                                  f'   |   播放处理 {speed:.1f} 帧/s   |   已确认 {packet["event_counts"].get("confirmed_events", 0)} 个事件')
        self.progress.setValue(min(100, round(count / total * 100)) if total > 0 else 0)
        self._show_assessment(packet['risk'])
        for item in packet['closed_events']:
            self.events[item['event_id']] = {**self.events.get(item['event_id'], {}), **item, 'state': 'closed'}
        for track in packet['temporal']['tracks']:
            if track['state'] != 'confirmed':
                continue
            event = {**self.events.get(track['event_id'], {}), **track}
            event['evidence_image'] = packet['evidence_images'].get(track['event_id'])
            risk = next((r for r in packet['risk']['events'] if r['event_id'] == track['event_id']), None)
            if risk:
                event['assessment'] = risk
            self.events[track['event_id']] = event
        for event_id, peak in packet['risk_peaks'].items():
            if event_id in self.events:
                self.events[event_id]['risk_summary'] = peak
        self.events = dict(sorted(self.events.items())[-200:])
        self._fill_events()

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
            explanation = f'已确认持续{CLASS_NAMES.get(event.get("label"), "视觉")}事件，根据覆盖、时间、趋势和置信度评分。'
        self.explanation.setText(explanation)
        self.contributions.setText('评分贡献\n' + '\n'.join(f'{COMPONENT_NAMES[key]}   +{value:.1f}'
                                                           for key, value in event['score_components'].items()))

    def _show_assessment(self, assessment):
        if not assessment:
            return
        pending = assessment['status'] == 'awaiting_confirmation'
        self.level.setText('待时序确认' if pending else LEVEL_NAMES[assessment['level']])
        self.level.setStyleSheet(f'font-size:26px;font-weight:700;color:{"#efba59" if pending else LEVEL_COLORS[assessment["level"]]};')
        self.score.setText(f'指数 {assessment["score"]:.1f} / 100' if assessment['events'] else '指数 — / 100')
        event = next((r for r in assessment['events'] if r['event_id'] == assessment['dominant_event_id']), None)
        self.evidence.setText(f'事件 #{event["event_id"]} · {CLASS_NAMES[event["label"]]} · '
                              + ('当前有检测证据' if event['fresh_evidence'] else '检测暂缺') if event else
                              '正在验证检测的持续性' if pending else '当前没有已确认视觉事件')
        self._show_features(event)

    def _fill_events(self):
        selected = self.event_table.currentRow()
        selected_id = self.event_table.item(selected, 0).data(Qt.ItemDataRole.UserRole) if selected >= 0 else None
        self.event_table.blockSignals(True)
        values = list(reversed(list(self.events.values())))
        self.event_table.setRowCount(len(values))
        selected_row = None
        for row, event in enumerate(values):
            risk = event.get('risk_summary') or {}
            cells = [f'#{event["event_id"]}', CLASS_NAMES.get(event['label'], event['label']),
                     f'{event["confirmed_at_s"]:.2f}s',
                     f'{event["closed_at_s"]:.2f}s' if 'closed_at_s' in event else '持续 / 检测暂缺' if not event.get('present_this_frame', True) else '持续中',
                     risk.get('peak_level', '—'), f'{risk["peak_score"]:.1f}' if 'peak_score' in risk else '—']
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, event['event_id'])
                self.event_table.setItem(row, column, item)
            if event['event_id'] == selected_id:
                selected_row = row
        if selected_row is not None:
            self.event_table.selectRow(selected_row)
        self.event_table.blockSignals(False)

    def select_event(self):
        row = self.event_table.currentRow()
        if row < 0:
            return
        item = self.event_table.item(row, 0)
        event = self.events.get(item.data(Qt.ItemDataRole.UserRole))
        if not event:
            return
        risk = event.get('risk_summary') or {}
        lines = [f'事件 #{event["event_id"]} · {CLASS_NAMES.get(event["label"], event["label"])}',
                 f'首次检测：{event["started_at_s"]:.2f}s', f'确认时间：{event["confirmed_at_s"]:.2f}s',
                 f'最高已生效等级：{risk.get("peak_level", "—")}',
                 f'最高指数：{risk.get("peak_score", "—")}',
                 f'结束原因：{CLOSE_NAMES.get(event.get("close_reason"), "事件仍在观察")}',
                 '结束时间是软件边界，不代表实际火情结束。']
        peak = risk.get('peak_evidence')
        if peak:
            lines.append(f'最高指数时刻：{peak["timestamp_s"]:.2f}s')
            lines.extend(f'{COMPONENT_NAMES[key]}：+{value:.1f}' for key, value in peak['components'].items())
            if not self.busy:
                self.level.setText('事件最高等级 ' + risk['peak_level'])
                self.level.setStyleSheet(f'font-size:23px;font-weight:700;color:{LEVEL_COLORS[risk["peak_level"]]};')
                self.score.setText(f'事件最高指数 {risk["peak_score"]:.1f}')
                self._show_features({'features': peak['features'], 'label': event['label'],
                                     'score_components': peak['components'], 'fresh_evidence': True})
                self.evidence.setText(f'历史峰值 @ {peak["timestamp_s"]:.2f}s；画面为确认截图')
        image = event.get('evidence_image')
        if image:
            lines.append(f'确认截图：{image}')
            if not self.busy and self.current_run:
                candidate = (self.current_run / image).resolve()
                if self.current_run.resolve() in candidate.parents:
                    self.video.set_file(candidate)
        self.detail.setPlainText('\n'.join(lines))

    def _analysis_done(self, result):
        self._refresh_preview()
        if result.get('output'):
            self.current_run = Path(result['output'])
        if not result['ok']:
            suffix = '\n已生成的记录保留在结果目录。' if self.current_run else ''
            self._notify_error(f'分析失败：{result["error"]}{suffix}')
        if self.current_run and (self.current_run / 'summary.json').is_file():
            try:
                record = load_run(self.current_run)
                self._apply_record(record, historical=False)
            except (ValueError, OSError) as exc:
                self._notify_error(f'结果读取失败：{exc}')

    def _apply_record(self, record, historical):
        self.current_run = Path(record['output'])
        self.events = {e['event_id']: e for e in record['events'][-200:]}
        self._fill_events()
        summary = record['summary']
        prefix = '历史记录 · ' if historical else ''
        self.state.setText(prefix + STATUS_NAMES.get(summary['status'], summary['status']))
        self.progress_text.setText(f'已处理 {summary["processed_frames"]} 帧   |   确认事件 '
                                  f'{(summary.get("temporal") or {}).get("confirmed_events", 0)} 个   |   '
                                  f'端到端 {summary.get("end_to_end_fps", 0):.1f} 帧/s（含初始化与等待）')
        self.progress.setValue(100 if summary['status'].startswith('completed') else self.progress.value())
        warnings = summary.get('warnings', [])
        self.output_label.setText(f'结果：{self.current_run.name}' + (' · 帧数差异见 summary.json' if warnings else ''))
        self.output_label.setToolTip('\n'.join(warnings) if warnings else str(self.current_run))
        if historical:
            source = record['config'].get('source')
            if source:
                self.source.setProperty('source_path', source)
                self.source.setText(Path(source).name)
                self.source.setToolTip(source)
            self.video.clear_frame('历史记录已载入\n选择事件查看确认截图，或打开结果目录查看视频。')
            risk = summary.get('risk')
            self.level.setText('最高等级 ' + risk['peak_level'] if risk else '该记录未启用风险提示')
            self.level.setStyleSheet('font-size:23px;font-weight:700;color:#9bafca;')
            self.score.setText(f'最高指数 {risk["peak_score"]:.1f}' if risk else '指数 —')
            self.evidence.setText('历史汇总 · 不是当前实时状态')
            self._show_features(None)
            self.explanation.setText('选择事件查看历史峰值依据。')
            self.tabs.setCurrentIndex(0)

    def _task_finished(self):
        task = self.task
        self.task = None
        if task:
            task.deleteLater()
        self.refresh_history()
        self._set_controls()
        if self.closing:
            QTimer.singleShot(0, self.close)

    def refresh_history(self):
        if self.busy:
            return
        self.history = list_runs(ROOT / 'outputs')
        self.history_table.setRowCount(len(self.history))
        for row, record in enumerate(self.history):
            summary, config = record['summary'], record['config']
            risk = summary.get('risk') or {}
            name = Path(record['output']).name
            cells = [name.replace('run-', ''), Path(config.get('source', '')).name,
                     STATUS_NAMES.get(summary['status'], summary['status']), str(summary['processed_frames']),
                     str((summary.get('temporal') or {}).get('confirmed_events', 0)), risk.get('peak_level', '—')]
            for column, value in enumerate(cells):
                self.history_table.setItem(row, column, QTableWidgetItem(value))
        self.filter_history(self.history_filter.text())

    def filter_history(self, text):
        query = text.strip().lower()
        for row in range(self.history_table.rowCount()):
            searchable = ' '.join(self.history_table.item(row, col).text() for col in (0, 1)).lower()
            self.history_table.setRowHidden(row, query not in searchable)

    def open_history(self):
        if self.busy:
            return
        row = self.history_table.currentRow()
        if row < 0:
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
        if self.busy:
            self.closing = True
            self.stop_analysis()
            if not isinstance(self.task, AnalysisWorker):
                self.state.setText('正在完成记录操作，随后关闭…')
            event.ignore()
        else:
            self.timer.stop()
            event.accept()
