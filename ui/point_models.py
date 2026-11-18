"""Per-point selection of real registered detector and confirmation profiles."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton,
                              QToolButton, QVBoxLayout, QWidget)

from core.confirmation import CONFIRMATION_NAMES
from core.devices import INHERIT, validate_device_analysis
from core.event_profiles import ASSOCIATION_NAMES
from core.model_profiles import (COMPARISON_PROFILES, PROFILE_NAMES, SCENE_PROFILES,
                                 profile_availability, profile_details)
from ui.model_picker import ModelInfoDialog


class PointModelSelector(QWidget):
    """Editing only; changing a selection never starts inference or downloads."""
    selectionChanged = Signal(dict)

    def __init__(self, device=None, parent=None, *, assets=None):
        super().__init__(parent)
        self._assets = assets
        self._setting = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.explanation = QLabel('此处选择烟火检测模型；AI画面理解共用本机视觉模型。')
        self.explanation.setObjectName('pointModelExplanation')
        self.explanation.setWordWrap(True)
        font = self.explanation.font()
        font.setBold(True)
        self.explanation.setFont(font)
        layout.addWidget(self.explanation)
        form = QFormLayout()
        form.setSpacing(10)
        self.model_profile = QComboBox()
        self.scene_combo = self.model_profile
        self.model_profile.setObjectName('pointModelProfile')
        self.model_profile.addItem('跟随全局设置', INHERIT)
        for profile in (*SCENE_PROFILES, *COMPARISON_PROFILES):
            ready = profile_availability(profile, assets)
            caption = PROFILE_NAMES[profile] + (' · 模型未就绪' if not ready['ready'] else '')
            self.model_profile.addItem(caption, profile)
            item = self.model_profile.model().item(self.model_profile.count() - 1)
            item.setEnabled(ready['ready'])
            item.setToolTip(profile_details(profile)['summary'] + '\n' + ready['message'])
        row = QHBoxLayout()
        row.addWidget(self.model_profile, 1)
        self.details_button = QPushButton('查看模型介绍')
        self.details_button.clicked.connect(self.show_model_info)
        row.addWidget(self.details_button)
        form.addRow('检测场景', row)
        layout.addLayout(form)
        self.summary = QLabel()
        self.summary.setObjectName('pointModelSummary')
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setText('高级设置')
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        layout.addWidget(self.advanced_toggle)
        self.advanced = QWidget()
        advanced_form = QFormLayout(self.advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        advanced_form.setSpacing(10)
        self.association_profile = QComboBox()
        self.association_profile.setObjectName('pointAssociationProfile')
        advanced_form.addRow('事件关联', self.association_profile)
        self.confirmation_profile = QComboBox()
        self.confirmation_profile.setObjectName('pointConfirmationProfile')
        self.confirmation_profile.addItem('跟随全局设置', INHERIT)
        for profile in ('standard', 'fast'):
            self.confirmation_profile.addItem(CONFIRMATION_NAMES[profile], profile)
        advanced_form.addRow('确认速度', self.confirmation_profile)
        layout.addWidget(self.advanced)
        self.advanced.hide()
        self.advanced_toggle.toggled.connect(self._show_advanced)
        self.model_profile.currentIndexChanged.connect(self._model_changed)
        self.association_profile.currentIndexChanged.connect(self._changed)
        self.confirmation_profile.currentIndexChanged.connect(self._changed)
        self.set_value(device or {})

    def _show_advanced(self, expanded):
        self.advanced.setVisible(expanded)
        self.advanced_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)

    def _association_options(self, selected=INHERIT):
        model = self.model_profile.currentData()
        self.association_profile.blockSignals(True)
        self.association_profile.clear()
        self.association_profile.addItem('跟随全局设置' if model == INHERIT else '使用所选模型的默认关联', INHERIT)
        self.association_profile.addItem(ASSOCIATION_NAMES['original'], 'original')
        if model in (INHERIT, 'brightlight'):
            self.association_profile.addItem(ASSOCIATION_NAMES['regions'], 'regions')
        index = self.association_profile.findData(selected)
        self.association_profile.setCurrentIndex(index if index >= 0 else 0)
        self.association_profile.blockSignals(False)

    def _model_changed(self):
        if self._setting:
            return
        self._association_options(self.association_profile.currentData())
        self._changed()

    def _changed(self):
        profile = self.model_profile.currentData()
        self.details_button.setEnabled(profile != INHERIT)
        if profile == INHERIT:
            self.summary.setText('此监控点启动时使用全局检测方案；其他监控点可单独选择。')
            self.summary.setToolTip('')
        else:
            details, ready = profile_details(profile), profile_availability(profile, self._assets)
            self.summary.setText(details['summary'] if ready['ready'] else
                                 '所选模型文件尚未就绪。设置会保留，补齐模型后才能启动本监控点。')
            self.summary.setToolTip(details['composition'] + '\n' + details['validation'])
        if not self._setting:
            self.selectionChanged.emit(self.value())

    def value(self):
        return validate_device_analysis({'model_profile': self.model_profile.currentData(),
                                        'association_profile': self.association_profile.currentData(),
                                        'confirmation_profile': self.confirmation_profile.currentData()})

    def set_value(self, value):
        selected = validate_device_analysis(value)
        self._setting = True
        try:
            self.model_profile.setCurrentIndex(self.model_profile.findData(selected['model_profile']))
            self._association_options(selected['association_profile'])
            self.confirmation_profile.setCurrentIndex(self.confirmation_profile.findData(selected['confirmation_profile']))
            self._changed()
        finally:
            self._setting = False

    def set_model_profile(self, profile):
        selected = self.value()
        selected['model_profile'] = profile
        if selected['association_profile'] == 'regions' and profile not in (INHERIT, 'brightlight'):
            selected['association_profile'] = INHERIT
        self.set_value(selected)

    def set_association_profile(self, profile):
        self.set_value({**self.value(), 'association_profile': profile})

    def set_confirmation_profile(self, profile):
        self.set_value({**self.value(), 'confirmation_profile': profile})

    def show_model_info(self):
        profile = self.model_profile.currentData()
        if profile == INHERIT:
            return
        dialog = ModelInfoDialog(profile, self, assets=self._assets)
        dialog.exec()
        dialog.deleteLater()
