"""Human-readable selection of registered local detection profiles."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QLabel, QPushButton,
                              QScrollArea, QTabWidget, QToolButton, QVBoxLayout, QWidget)

from core.model_profiles import SCENE_PROFILES, COMPARISON_PROFILES, SCENE_WEIGHTS, profile_availability, profile_details
from ui.product import STYLE

CHOICE_TEXT = {
    'original': ('通用监看', '不确定场景时，从这里开始。'),
    'domestic': ('家庭室内', '家庭摄像头、室内生活区域。'),
    'forest': ('林地远景', '固定摄像头拍摄的山林、山坡。'),
    'uav': ('无人机巡林', '无人机俯拍的林地录像。'),
    'brightlight': ('反光抑制对照', '尝试减少部分灯光、反光误提示。'),
    'modern': ('火焰识别对照', '比较另一套方案对火焰的持续识别表现。'),
}

INTRODUCTION_TEXT = {
    'original': {
        'scene': '常规录像、首次使用，或者暂时不确定该选哪一种方案。',
        'detects': '火焰和烟雾。',
        'strength': '只运行一套模型，电脑负担较小；适合作为其他方案的比较起点。',
        'advice': '灯光、反光和红色物体可能被误认。远处的小火点、被遮挡的火焰也容易漏掉。',
    },
    'domestic': {
        'scene': '家庭室内摄像头拍摄的厨房、客厅等生活区域。',
        'detects': '火焰和烟雾。',
        'strength': '模型的训练用途围绕家庭烟火；一套模型同时查看火焰和烟雾，便于与通用方案对比。',
        'advice': '做饭烟气、香烟烟雾仍需人工区分。现有检查尚未证明它比通用方案更好，建议先用家中录像比较；不宜直接用于判断工厂或仓库场景。',
    },
    'forest': {
        'scene': '固定摄像头拍摄的山林、山坡等室外远景。',
        'detects': '烟雾和可见火焰，模型的训练用途更侧重山林烟雾。',
        'strength': '面向山林远景中的烟雾线索，可作为林地录像的另一种选择。',
        'advice': '云雾、扬尘可能被误认；远处小火点可能漏掉，不能把它当作小火焰专用模型。电脑负担可能更大，建议先比较同一段现场录像。',
    },
    'uav': {
        'scene': '无人机俯拍的林地、巡林录像。',
        'detects': '火焰和烟雾；火焰使用面向航拍的模型，烟雾使用通用方案。',
        'strength': '针对航拍视角选择火焰模型，便于比较俯拍画面中的识别表现。',
        'advice': '远处小火点、树木遮挡和画面晃动仍可能造成漏检。尚未完成独立航拍验证，不能保证比通用方案更好；运行时电脑负担较大。',
    },
    'brightlight': {
        'scene': '灯光闪烁、车灯或阳光反光较多，通用方案频繁提示火焰的录像。',
        'detects': '火焰和烟雾；烟雾识别沿用通用方案。',
        'strength': '在部分开发录像中，亮光引起的火焰误提示较少，适合拿同一段录像进行对照。',
        'advice': '真实火焰可能漏掉或更晚确认，也不能消除全部反光误提示。使用前应同时比较误提示和漏检；运行时电脑负担较大。',
    },
    'modern': {
        'scene': '希望比较另一套方案对火焰的持续识别表现时。',
        'detects': '火焰和烟雾；更换火焰模型，烟雾识别沿用通用方案。',
        'strength': '部分开发录像中的火焰检出更连续，部分误提示也更少。',
        'advice': '仍会出现误检和漏检，并非所有场景更好。远处小火点、遮挡火焰建议用同段录像与通用方案对比，不能当作小火焰专用模型；运行时电脑负担较大。',
    },
}


def choice_label(profile):
    return CHOICE_TEXT[profile][0]


class ModelInfoDialog(QDialog):
    """Read-only introduction: opening it never changes the chosen model."""
    def __init__(self, profile, parent=None, *, assets=None):
        super().__init__(parent)
        self.setWindowTitle(choice_label(profile) + ' · 方案介绍')
        self.setStyleSheet(STYLE)
        self.resize(620, 480)
        self.setMinimumSize(480, 360)
        self.profile = profile
        details = profile_details(profile)
        availability = profile_availability(profile, assets)
        layout = QVBoxLayout(self)
        title = QLabel(choice_label(profile))
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        body = QVBoxLayout(content)
        plain = INTRODUCTION_TEXT[profile]
        lines = [f'适合什么画面\n{plain["scene"]}', f'能检测什么\n{plain["detects"]}',
                 f'主要优点\n{plain["strength"]}', f'容易出错的情况与使用建议\n{plain["advice"]}',
                 '场景名称用于帮助选择，最终提示请结合原画面核查。',
                 availability['message']]
        if availability['missing']:
            lines.append('请按模型准备说明补齐文件后再使用。')
        self.introduction = QLabel('\n\n'.join(lines))
        self.introduction.setWordWrap(True)
        self.introduction.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.addWidget(self.introduction)
        self.details_toggle = QToolButton()
        self.details_toggle.setText('技术信息与验证记录')
        self.details_toggle.setCheckable(True)
        self.details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        body.addWidget(self.details_toggle)
        technical_lines = [details['status'], f'实际模型\n{details["composition"]}',
                 f'适合什么用途\n{details["purpose"]}', f'需要注意\n{details["tradeoff"]}',
                 f'运行开销\n{details["compute"]}', f'验证范围\n{details["validation"]}',
                 '场景名称用于帮助选择；不会自动识别场景，也不保证换模型就提高准确率。',
                 availability['message'], '项目验证记录\n' + details['guide']]
        if availability['missing']:
            technical_lines.append('请按模型准备说明补齐文件；不会自动下载或改用其他方案。\n' + '\n'.join(availability['missing']))
        else:
            technical_lines.append('文件存在不代表版本已校验，可在“设置 → 检查运行环境”核对。')
        if profile in SCENE_WEIGHTS:
            spec = SCENE_WEIGHTS[profile]
            technical_lines.append('公开模型来源\n' + spec['source_url'] + '\n固定版本：' + spec['revision'])
        self.technical_details = QLabel('\n\n'.join(technical_lines))
        self.technical_details.setWordWrap(True)
        self.technical_details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.technical_details.setVisible(False)
        self.details_toggle.toggled.connect(self._show_technical_information)
        body.addWidget(self.technical_details)
        body.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        close = QPushButton('关闭介绍')
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _show_technical_information(self, expanded):
        self.technical_details.setVisible(expanded)
        self.details_toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)


class ModelPickerDialog(QDialog):
    def __init__(self, current_profile, parent=None, *, assets=None):
        super().__init__(parent)
        self.setWindowTitle('按场景选择检测方案')
        self.setStyleSheet(STYLE)
        self.resize(640, 550)
        self.setMinimumSize(520, 450)
        self.selected_profile = None
        self.card_widgets = {}
        self._assets = assets
        self.choice_buttons = {}
        self.availability = {}
        self.info_buttons = {}
        layout = QVBoxLayout(self)
        title = QLabel('按使用场景选择')
        title.setObjectName('pageTitle')
        layout.addWidget(title)
        note = QLabel('选择画面所属的场景，模型说明可点“查看介绍”。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        for profiles, caption in ((SCENE_PROFILES, '按场景选择'), (COMPARISON_PROFILES, '高级对照')):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            cards = QVBoxLayout(content)
            cards.setContentsMargins(2, 6, 6, 2)
            cards.setSpacing(8)
            for profile in profiles:
                self._add_card(cards, profile, current_profile, assets)
            cards.addStretch()
            scroll.setWidget(content)
            self.tabs.addTab(scroll, caption)
        if current_profile in COMPARISON_PROFILES:
            self.tabs.setCurrentIndex(1)
        close = QPushButton('取消')
        close.clicked.connect(self.reject)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)

    def _add_card(self, cards, profile, current_profile, assets):
        availability = profile_availability(profile, assets)
        self.availability[profile] = availability
        card = QFrame()
        card.setObjectName('card')
        self.card_widgets[profile] = card
        card_layout = QHBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        description = QVBoxLayout()
        description.setSpacing(3)
        heading = QHBoxLayout()
        label = QLabel(choice_label(profile))
        label.setObjectName('sectionTitle')
        label.setWordWrap(True)
        heading.addWidget(label)
        if current_profile == profile:
            heading.addWidget(QLabel('当前使用'))
        heading.addStretch()
        description.addLayout(heading)
        text = QLabel(CHOICE_TEXT[profile][1])
        text.setWordWrap(True)
        text.setObjectName('muted')
        description.addWidget(text)
        card_layout.addLayout(description, 1)
        actions = QHBoxLayout()
        if availability['missing']:
            missing = QLabel('模型未就绪')
            missing.setStyleSheet('color:#aa4c17;')
            actions.addWidget(missing)
        info = QPushButton('查看介绍')
        info.clicked.connect(lambda checked=False, value=profile: self.show_introduction(value))
        self.info_buttons[profile] = info
        actions.addWidget(info)
        button = QPushButton(('当前使用' if current_profile == profile else '选择') if availability['ready'] else '暂不可用')
        button.setObjectName('primary')
        button.setEnabled(availability['ready'])
        button.clicked.connect(lambda checked=False, value=profile: self.choose(value))
        actions.addWidget(button)
        card_layout.addLayout(actions)
        self.choice_buttons[profile] = button
        cards.addWidget(card)

    def show_introduction(self, profile):
        dialog = ModelInfoDialog(profile, self, assets=self._assets)
        dialog.exec()
        dialog.deleteLater()

    def choose(self, profile):
        # Recheck in case model files were moved while this dialog was open.
        available = profile_availability(profile, self._assets)
        if not available['ready']:
            self.choice_buttons[profile].setEnabled(False)
            self.choice_buttons[profile].setText('模型文件已不可用')
            return
        self.selected_profile = profile
        self.accept()

