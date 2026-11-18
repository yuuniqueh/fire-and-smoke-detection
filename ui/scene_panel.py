"""Scene descriptions beside a single saved video's detection result."""
import hashlib
from pathlib import Path
import re

from PySide6.QtCore import QTimer, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                              QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from core.records import read_jsonl_tail
from core.scene_review import SCENES
from ui.monitor_worker import SceneReviewWorker
from ui.analysis_cards import AnalysisSummary, ResultCard, TextCard


class SceneDescriptionPanel(QWidget):
    settingsRequested = Signal()
    reviewSaved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = None
        self.target_key = None
        self.target_event = None
        self.target_config = {}
        self.available = False
        self.records, self.errors = {}, {}
        self.previews, self.live_frames = {}, {}
        self.auto_attempted = set()
        self.pending_auto = None
        self.closing = False
        self.can_start = lambda: True
        self.ai_config = lambda: ('http://127.0.0.1:11435', 'qwen3-vl:2b')
        self.context_for_run = lambda run: None
        self.last_requested = {}
        self.latest_assessment = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 2)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.description_card = TextCard('AI画面理解', accent='blue')
        self.mode_hint = QLabel('自动')
        self.mode_hint.setStyleSheet('font-size:16px;color:#3867e8;background:#e0ebff;border-radius:8px;padding:4px 8px;')
        self.mode_hint.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.description_card.header_layout.addWidget(self.mode_hint)
        self.description = self.description_card.body
        self.description.setText('开始分析录像后，AI将同时理解画面，无需等待烟火事件。')
        self.description.setMinimumHeight(62)
        self.status = QLabel('本机分析 · 综合核查')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName('muted')
        self.status.setStyleSheet('color:#526984;font-size:16px;')
        self.description_card.layout().addWidget(self.status)
        layout.addWidget(self.description_card)
        self.fusion_card = ResultCard()
        self.fusion_title = self.fusion_card.title_label
        self.fusion_title.setText('研判建议')
        self.fusion = self.fusion_card.score_label
        self.final_level = self.fusion_card.conclusion_label
        self.final_level.setText('最终等级：等待分析')
        self.fusion_state = self.fusion_card.status_label
        self.fusion_card.hide()
        layout.addWidget(self.fusion_card)
        actions = QHBoxLayout()
        self.generate = QPushButton('生成画面描述')
        self.generate.clicked.connect(self.start_review)
        self.details = QPushButton('分析依据')
        self.details.clicked.connect(self.show_details)
        actions.addWidget(self.generate)
        actions.addWidget(self.details)
        for button in (self.generate, self.details):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setMaximumHeight(52)
        layout.addLayout(actions)
        self.options_toggle = QPushButton('分析选项')
        self.options_toggle.setObjectName('quiet')
        self.options_toggle.setCheckable(True)
        self.options_toggle.setStyleSheet('text-align:left;font-size:16px;padding:3px 0;')
        self.options_toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.options_toggle)
        self.options_box = QWidget()
        options = QHBoxLayout(self.options_box)
        options.setContentsMargins(0, 0, 0, 0)
        self.auto = QCheckBox('自动更新')
        self.auto.setToolTip('开播积累约1秒原画面即分析；按视频时间约8秒更新，有无烟火事件均运行。')
        self.auto.setChecked(True)
        self.auto.toggled.connect(lambda checked: self.mode_hint.setText('自动' if checked else '手动'))
        options.addWidget(self.auto, 1)
        self.settings = QPushButton('AI设置')
        self.settings.setObjectName('quiet')
        self.settings.clicked.connect(lambda: self.settingsRequested.emit())
        options.addWidget(self.settings)
        self.options_box.hide()
        self.options_toggle.toggled.connect(self.options_box.setVisible)
        layout.addWidget(self.options_box)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._render()

    @property
    def is_busy(self):
        return self.worker is not None

    def invalidate_run(self, run):
        path = str(Path(run).resolve())
        self.records = {k: v for k, v in self.records.items() if k[0] != path}
        if self.target_key and self.target_key[0] == path:
            self.target_key = None

    def set_target(self, run, target_event, config, *, running=False):
        key = (str(Path(run).resolve()), target_event['event_id'] if target_event else None) if run else None
        changed = key != self.target_key
        self.target_key = key
        self.target_event = dict(target_event) if target_event else None
        self.target_config = dict(config)
        self.target_running = bool(running)
        frames = self.live_frames.get(key, [])
        live_ready = len(frames) >= 2 and frames[-1]['timestamp_s'] - frames[0]['timestamp_s'] >= .8
        self.available = bool(key and (live_ready or (not running and (Path(key[0]) / 'summary.json').is_file())))
        if changed:
            if self.pending_auto != key:
                self.pending_auto = None
            if key and key not in self.records:
                try:
                    rows = read_jsonl_tail(Path(key[0]) / 'scene-reviews.jsonl', 500)
                except OSError:
                    rows = []
                self.records[key] = next((r for r in reversed(rows) if r.get('event_id') == key[1]), None)
                if self.records[key] is None and key[1] is not None:
                    self.records[key] = next((r for r in reversed(rows) if r.get('event_id') is None), None)
            while len(self.records) > 200:
                old = next(iter(self.records))
                self.records.pop(old)
                self.previews.pop(old, None)
                self.auto_attempted.discard(old)
                self.last_requested.pop(old, None)
        self._render()

    def request_auto(self):
        if (self.auto.isChecked() and self.target_key and not self.records.get(self.target_key)
                and self.target_key not in self.auto_attempted):
            self.pending_auto = self.target_key

    def observe_live(self, run, target_event, frames):
        key = (str(Path(run).resolve()), target_event['event_id'] if target_event else None)
        self.live_frames[key] = list(frames)
        while len(self.live_frames) > 200:
            self.live_frames.pop(next(iter(self.live_frames)))
        if key == self.target_key:
            self.set_target(run, target_event, self.target_config, running=True)
            self.request_auto()
            last = self.last_requested.get(key, -float('inf'))
            if frames and frames[-1]['timestamp_s'] - last >= 8. and self.auto.isChecked():
                self.pending_auto = key

    def show_fusion(self, assessment):
        """Show one recorded final score and priority, never recompute it in Qt."""
        self.latest_assessment = assessment
        info = (assessment or {}).get('fusion') or {}
        self.fusion.setToolTip('')
        if not isinstance(assessment, dict) or 'score' not in assessment or 'level' not in assessment:
            self.fusion.setText('— / 100')
            self.final_level.setText('最终等级：等待分析')
            self.fusion_state.clear()
            self.fusion_card.hide()
            return
        from core.alert_presentation import LEVEL_ACTIONS, LEVEL_NAMES, LEVEL_COLORS
        self.fusion_card.show()
        historical = bool(info.get('policy') in ('scene-fusion-v1', 'context-review-v1') or
                          (self.target_key and not getattr(self, 'target_running', False) and
                           (Path(self.target_key[0]) / 'summary.json').is_file()))
        level = assessment['level']
        scene_state = info.get('scene_status', 'pending')
        delta = info.get('context_delta', 0.)
        if historical:
            state = '显示当时保存的综合结果，未按当前规则重算。'
        elif scene_state == 'expired':
            state = 'AI场景已过期，当前采用烟火检测结果。'
        elif scene_state == 'invalid':
            state = 'AI场景暂不可用，当前采用烟火检测结果。'
        elif scene_state != 'current':
            state = '等待AI场景分析，当前采用烟火检测结果。'
        elif info.get('scene_only_hazard'):
            state = 'AI提示异常线索，请核查原画面。'
        elif delta > 0:
            state = '画面存在异常线索，已参与综合核查。'
        elif delta < 0:
            state = '已结合受控用火场景与烟火变化调整核查优先级。'
        else:
            state = '已结合当前场景与烟火检测，保留当前核查优先级。'
        advice = '持续观察' if level == 'NORMAL' else LEVEL_ACTIONS.get(level, '请查看核查证据')
        self.fusion_card.set_result(advice,
            assessment['score'], state, level='NORMAL' if historical else level,
            priority_level=level, historical=historical)
        self.fusion_title.setText('历史研判建议' if historical else '研判建议')
        dominant = next((row for row in assessment.get('events', [])
            if row.get('event_id') == assessment.get('dominant_event_id')), {})
        reason = (dominant.get('fusion') or {}).get('reason') or info.get('reason', '')
        baseline_score = info.get('baseline_score', assessment['score'])
        baseline_level = info.get('baseline_level', level)
        tooltip = (f'烟火检测原始指数：{baseline_score:.1f} / 100\n'
            + '原始等级：' + LEVEL_NAMES.get(baseline_level, '未记录') + '\n'
            + 'AI场景：' + SCENES.get(info.get('scene'), '等待或暂不明确')
            + ('\n' + reason if reason else ''))
        self.fusion.setToolTip(tooltip)
        self.final_level.setToolTip(tooltip)
        self.fusion_state.setToolTip(tooltip)

    def _tick(self):
        if self.pending_auto and not self.auto.isChecked():
            self.pending_auto = None
        if (not self.closing and self.pending_auto == self.target_key and self.pending_auto
                and self.available and not self.worker and self.can_start()):
            self.pending_auto = None
            self.start_review()

    def start_review(self, *, profile='fast', use_cache=True):
        if self.closing or self.worker or not self.available:
            return
        if not self.can_start():
            self.status.setText('本机AI正在复核其他监控点，请稍后重试。')
            return
        key = self.target_key
        event = self.target_event or {}
        risk = event.get('risk_summary') or {}
        target_assessment = event.get('assessment')
        baseline = dict(target_assessment) if target_assessment else {'score': risk.get('peak_score', 25.), 'level': risk.get('peak_level', 'LOW'),
                    'label': event.get('label', 'fire'), 'fresh_evidence': True,
                    'features': (risk.get('peak_evidence') or {}).get('features', {})}
        if 'baseline_score' in baseline:
            baseline.update(score=baseline['baseline_score'], level=baseline['baseline_level'])
        if self.target_event is None:
            baseline = None
        payload = {'output': key[0], 'event_id': key[1], 'frames': list(self.live_frames.get(key, [])), 'baseline': baseline}
        point = self.target_config.get('monitoring_point') or {}
        device = {'id': point.get('id', 'single-video'),
                  'name': point.get('name', Path(self.target_config.get('source', '')).name or '单路录像'),
                  'location': point.get('location', '未登记实际位置')}
        endpoint, model = self.ai_config()
        self.worker = SceneReviewWorker(key, payload, device, endpoint, model, self, profile=profile, use_cache=use_cache,
                                        context_store=self.context_for_run(key[0]))
        self.worker.progress.connect(self._progress, Qt.ConnectionType.QueuedConnection)
        self.worker.outcome.connect(self._done, Qt.ConnectionType.QueuedConnection)
        self.worker.finished.connect(self._finished, Qt.ConnectionType.QueuedConnection)
        self.errors.pop(key, None)
        self.previews.pop(key, None)
        self.auto_attempted.add(key)
        frames = payload['frames']
        if frames:
            self.last_requested[key] = frames[-1]['timestamp_s']
        self.worker.start()
        self._render()

    @Slot(dict)
    def _progress(self, value):
        self.previews[tuple(value['key'])] = value
        self._render()

    @Slot(dict)
    def _done(self, result):
        key = tuple(result['key'])
        if result['ok']:
            self.records[key] = result['record']
            self.errors.pop(key, None)
            self.reviewSaved.emit(key[0])
        elif not result.get('cancelled'):
            self.errors[key] = result.get('error', 'AI描述生成失败，请重试。')
        self._render()

    @Slot()
    def _finished(self):
        worker, self.worker = self.worker, None
        if worker:
            worker.deleteLater()
        self._render()

    def _render(self):
        record = self.records.get(self.target_key)
        if not record and self.target_key:
            record = self.records.get((self.target_key[0], None))
        preview = self.previews.get(self.target_key)
        current_job = bool(self.worker and self.worker.event_key == self.target_key)
        self.generate.setEnabled(self.available and not self.worker and not self.closing)
        self.generate.setText('正在描述…' if current_job else '重新分析' if record else '生成画面描述')
        self.details.setEnabled(bool(record) and not current_job)
        if current_job and preview:
            self.description.setText(preview['description'])
            self.status.setText('初步描述 · 正在完成复核')
            self.status.setToolTip(f'已等待{preview["elapsed_s"]:.1f}秒。完整结果通过校验后参与综合核查；烟火检测继续运行。')
        elif record:
            self.description.setText(record['scene']['description'])
            duration = record.get('total_elapsed_s', record['elapsed_s'])
            first = record.get('first_description_total_s')
            timing = (f'描述{first:.1f}秒 · 复核{duration:.1f}秒' if first is not None else f'耗时{duration:.1f}秒')
            if record.get('runtime', {}).get('cache_hit'):
                timing = f'已复用相同画面结果 · {duration:.2f}秒'
            scope = f'事件 #{self.target_key[1]}' if record.get('event_id') is not None else '视频场景 · 无需事件触发'
            stamps = record.get('evidence', [])
            evidence_time = f' · 取帧{stamps[0]["timestamp_s"]:.1f}—{stamps[-1]["timestamp_s"]:.1f}秒' if stamps else ''
            state = ('本次未完成 · 显示上次结果' if self.errors.get(self.target_key) else
                     '正在更新 · 显示上次结果' if current_job else scope if record.get('event_id') is None else
                     SCENES.get(record['scene']['scene'], '场景不明确') + ' · 复核完成')
            self.status.setText(state)
            self.status.setToolTip(f'{scope} · {SCENES.get(record["scene"]["scene"], "场景不明确")}\n' + timing + evidence_time
                                   + ('\n' + self.errors[self.target_key] if self.errors.get(self.target_key) else ''))
        elif current_job:
            self.description.setText('正在理解当前画面…')
            self.status.setText('分析中 · 烟火检测继续运行')
            self.status.setToolTip('正在读取本次录像原画面，生成描述；完整结果返回后参与综合核查。')
        elif self.target_key:
            self.description.setText(preview['description'] if preview else
                                     '此事件尚未生成AI画面描述。' if self.available and self.target_event else
                                     '此录像尚未生成AI画面描述。' if self.available else '正在积累原画面，随后开始场景分析。')
            error = self.errors.get(self.target_key)
            self.status.setText(('初步描述尚未完成复核 · ' if preview else '') +
                                (error or '等待画面分析'))
            self.status.setToolTip('AI与烟火检测并行工作，没有烟火事件时也会分析画面。')
        else:
            self.description.setText('开始监看后，这里将显示画面描述。')
            self.status.setText('等待开始')
            self.status.setToolTip('开播后自动理解场景，无需等待烟火事件；原始检测记录保留。')
        self._fit_description()

    def _fit_description(self):
        width = self.description.width()
        if width > 30:
            self.description.setMinimumHeight(max(62, self.description.heightForWidth(width)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_description()

    def show_details(self):
        record = self.records.get(self.target_key)
        if not record and self.target_key:
            record = self.records.get((self.target_key[0], None))
        if not record:
            return
        scene = record['scene']
        dialog = QDialog(self)
        dialog.setWindowTitle('AI画面描述与依据 · ' + (f'事件 #{record["event_id"]}' if record.get('event_id') is not None else '视频场景'))
        dialog.resize(720, 720)
        layout = QVBoxLayout(dialog)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(14)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        summary = AnalysisSummary()
        priority = record.get('priority') or {}
        from core.alert_presentation import LEVEL_ACTIONS
        stamps = record.get('evidence') or []
        frame_range = (f'{stamps[0]["timestamp_s"]:.2f} — {stamps[-1]["timestamp_s"]:.2f} 秒'
                       if stamps else '未记录')
        metrics = [('识别场景', SCENES.get(scene.get('scene'), '场景不明确')),
                   ('原画面范围', frame_range), ('下一步核对', scene.get('next_check', '请人工核对'))]
        details_rows = [('模型', record.get('model', '未记录')),
                        ('本次分析耗时', f'{record.get("total_elapsed_s", record.get("elapsed_s", 0.)):.2f} 秒')]
        if priority.get('baseline_score') is not None:
            details_rows.append(('烟火检测原始指数', f'{priority["baseline_score"]:.1f} / 100'))
        if priority.get('context_delta') is not None:
            details_rows.append(('场景影响', f'{priority["context_delta"]:+.1f}'))
        if priority.get('score') is not None:
            details_rows.insert(0, ('辅助评分（规则分）', f'{priority["score"]:.1f} / 100'))
        reasons = ['异常线索：' + item for item in scene.get('hazard_cues', [])]
        reasons += ['画面证据：' + item for item in scene.get('visible_evidence', [])]
        historical = priority.get('policy') in ('context-review-v1', 'scene-fusion-v1')
        summary.set_content(scene['description'], LEVEL_ACTIONS.get(priority.get('level'), '请核查画面依据'),
            priority.get('score'), metrics, reasons,
            status='取帧时的历史综合结果，保留当时记录。' if historical else
                   '以下为本次取帧的分析依据，最新综合结果请查看主界面。',
            uncertainties=scene.get('uncertainties') or ['画面外情况仍需人工核对。'],
            details=details_rows, level=priority.get('level'), priority_level=priority.get('level'), historical=historical)
        summary.result_card.title_label.setText('取帧时的研判建议')
        content_layout.addWidget(summary)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        images = QHBoxLayout()
        root = Path(self.target_key[0])
        for evidence in record.get('evidence', [])[:3]:
            relative = evidence.get('image', '')
            if not re.fullmatch(r'scene-evidence/(?:event-\d+|scene)-[0-9a-f]{20}\.jpg', relative):
                continue
            path = root / relative
            if not path.is_file() or path.is_symlink():
                continue
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != evidence.get('sha256'):
                continue
            column = QVBoxLayout()
            label = QLabel()
            label.setPixmap(QPixmap.fromImage(QImage.fromData(data, 'JPG')).scaled(190, 115, Qt.AspectRatioMode.KeepAspectRatio))
            column.addWidget(label)
            column.addWidget(QLabel(f'原画面 {evidence["timestamp_s"]:.2f}秒'))
            images.addLayout(column)
        content_layout.addLayout(images)
        detailed = QPushButton('详细复核（更多细节）')
        def request_detail():
            dialog.accept()
            self.start_review(profile='detailed', use_cache=False)
        detailed.clicked.connect(request_detail)
        detailed.setEnabled(self.available and not self.worker)
        layout.addWidget(detailed)
        dialog.exec()

    @staticmethod
    def _detail_text(record):
        scene = record['scene']
        priority = record.get('priority') or {}
        explanation = '\n\n综合核查\n画面描述与烟火检测共同用于核查，主界面展示研判建议与核查优先级，具体规则分保留在详情中。'
        if priority.get('policy') in ('context-review-v1', 'scene-fusion-v1'):
            explanation = ('\n\n历史联合结果（保留当时记录）\n'
                f'当时检测指数：{priority.get("baseline_score", "—")}\n'
                f'当时联合指数：{priority.get("score", "—")}\n' + priority.get('reason', ''))
        elif priority and priority.get('policy') != 'independent-review-v1':
            explanation = ('\n\n取帧时的综合核查依据\n'
                f'烟火检测原始指数：{priority.get("baseline_score", "—")}\n'
                f'综合核查指数：{priority.get("score", "—")}\n' + priority.get('reason', ''))
        return ('画面描述\n' + scene['description'] + '\n\n可见证据\n' + '\n'.join(scene['visible_evidence'])
            + '\n\n异常线索\n' + ('\n'.join(scene['hazard_cues']) or '未列出明确异常线索，不代表安全')
            + '\n\n不确定处\n' + ('\n'.join(scene['uncertainties']) or '仍需人工核对画面外情况')
            + '\n\n下一步核对\n' + scene['next_check'] + explanation + '\n\n模型：' + record['model'])

    def shutdown(self):
        self.closing = True
        self.pending_auto = None
        self.timer.stop()
        if self.worker:
            self.worker.requestInterruption()
            return False
        return True
