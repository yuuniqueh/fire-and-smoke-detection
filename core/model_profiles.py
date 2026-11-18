"""Runtime detector selection; the frozen original detector stays unchanged."""
import hashlib
import math
from pathlib import Path

from core.runtime import ASSET_ROOT as ROOT

ORIGINAL_SHA256 = 'b91633799ceb052c814b4f8b77a37efc9a40f002d528df97d74463585fa4f28f'
CANDIDATE_SHA256 = '8e23e1110a47dad1fb81b26984de1ca88e751cec0c070894f1a7bd4211c2d0b0'
CANDIDATE_PATH = ROOT / 'models/candidates/brightlight-0da45ff9f6d2/best.pt'
MODERN_SHA256 = '52684ddffd4797f1bc5ff1bd2b72af3ec34eabf0c1646c37f518bb766b27b79f'
MODERN_PATH = ROOT / 'models/candidates/general26s-5397b43994c3/best.pt'
PROFILE_NAMES = {'original': '通用监看', 'brightlight': '反光抑制对照',
                 'modern': '火焰识别对照',
                 'domestic': '家庭室内', 'forest': '林地远景',
                 'uav': '无人机巡林'}
SCENE_PROFILES = ('original', 'domestic', 'forest', 'uav')
COMPARISON_PROFILES = ('brightlight', 'modern')

# Pinned, already downloaded public checkpoints. No network access at runtime.
SCENE_WEIGHTS = {
    'domestic': {
        'path': 'models/candidates/domestic-f1c6426b069c/fire_smoke_yolov8.pt',
        'sha256': 'ac0a10257b2bc1f20c9d957f8adeeb61dd6140322fc19d0b4a116cb491776d16',
        'repo_id': 'mfranzon/fire-smoke-yolov8',
        'revision': 'f1c6426b069c1849cbf13b1ef5d2a260289286db',
        'source_url': 'https://huggingface.co/mfranzon/fire-smoke-yolov8'},
    'forest': {
        'path': 'models/candidates/sainet-2246d5ea1408/SAINet_v11.1.pt',
        'sha256': '7c261a170554e3f7ffc549c4561bb1201e67c6b41418c0618535a82b0827ed50',
        'repo_id': 'AlterMundi/sai-sainet-latest',
        'revision': '2246d5ea1408a20ad6fa248eb35a3c5db404e32a',
        'source_url': 'https://github.com/AlterMundi/sai-sainet-latest'},
    'uav': {
        'path': 'models/candidates/uav-57cf971a788f/weights/best.pt',
        'sha256': 'a06febf257bff40cf5f3814e77f5120e2a80ab91d3ff3a84370caf6fdf832740',
        'repo_id': 'xiazh0219/yolo26m-UAV-fire-smoke-detection',
        'revision': '57cf971a788fff62b8f3e5b1c3632e1214b8255c',
        'source_url': 'https://huggingface.co/xiazh0219/yolo26m-UAV-fire-smoke-detection'},
}
SCENE_MODEL_ASSETS = (
    'models/candidates/domestic-f1c6426b069c/fire_smoke_yolov8.pt',
    'models/candidates/domestic-f1c6426b069c/source-manifest.json',
    'models/candidates/domestic-f1c6426b069c/README.md',
    'models/candidates/domestic-f1c6426b069c/publisher-metadata.json',
    'models/candidates/sainet-2246d5ea1408/SAINet_v11.1.pt',
    'models/candidates/sainet-2246d5ea1408/source-manifest.json',
    'models/candidates/sainet-2246d5ea1408/README.md',
    'models/candidates/sainet-2246d5ea1408/LICENSE',
    'models/candidates/sainet-2246d5ea1408/train_hyperparams.yaml',
    'models/candidates/uav-57cf971a788f/weights/best.pt',
    'models/candidates/uav-57cf971a788f/source-manifest.json',
    'models/candidates/uav-57cf971a788f/README.md',
    'models/candidates/uav-57cf971a788f/publisher-metadata.json',
)

# Scene labels describe selection intent, not independently certified domains.
PROFILE_DETAILS = {
    'original': {
        'scene': '常规监看 / 不确定场景', 'status': '通用方案',
        'summary': '不确定如何选择时，从基准方案开始；火焰和烟雾使用同一模型。',
        'purpose': '常规录像检查、首次使用，或与其他方案对照。',
        'composition': 'YOLOv8n 基准火焰 / 烟雾模型',
        'tradeoff': '单模型；部分反光、灯光或红色物体仍可能误提示，小目标也可能漏检。',
        'validation': '已有项目基线检查；尚未证明对所有室内、室外场景都适用。',
        'compute': '单模型推理；实际速度取决于设备与画面。',
        'guide': 'docs/MODEL_REPLACEMENT_DECISION.md',
        'files': ('models/best.pt',)},
    'brightlight': {
        'scene': '灯光 / 反光干扰较多', 'status': '对照方案',
        'summary': '可尝试减少部分亮光误提示，但真实火焰可能更晚确认或漏检；请先用现场录像对照。',
        'purpose': '灯光、反光干扰较多，愿意比较误提示与漏检取舍的场景。',
        'composition': '亮光候选检测火焰 + 基准模型检测烟雾',
        'tradeoff': '开发素材上部分误提示减少，但部分真实火焰确认延迟增加、持续检出下降；反光问题并未解决。',
        'validation': '开发组检查，不是独立场景验证；不能保证强光场景更准确。',
        'compute': '每次运行两个模型，计算开销增加；固定阈值 0.35。',
        'guide': 'docs/MODEL_REPLACEMENT_DECISION.md',
        'files': ('models/best.pt', 'models/candidates/brightlight-0da45ff9f6d2/best.pt')},
    'modern': {
        'scene': '火焰识别对照', 'status': '对照方案',
        'summary': '换用另一套火焰检测器，烟雾沿用基准；部分开发画面有改善，仍存在误检和漏检。',
        'purpose': '想用另一套火焰检测器，对相同录像比较识别表现。',
        'composition': 'YOLO26-S 检测火焰 + 基准模型检测烟雾',
        'tradeoff': '旧开发素材的部分火焰检测有改善，但并非所有场景更好；检测框变化也会影响视觉指数。',
        'validation': '旧开发组检查，困难图定位门槛未通过；不代表独立泛化或场景准确率结论。',
        'compute': '每次运行两个模型，计算开销增加；固定阈值 0.35。',
        'guide': 'docs/MODERN_FIRE_MODEL_TRIAL.md',
        'files': ('models/best.pt', 'models/candidates/general26s-5397b43994c3/best.pt')},
}

PROFILE_DETAILS.update({
    'domestic': {
        'scene': '家庭室内', 'status': '场景方案',
        'summary': '用于家庭室内录像对照；发布者面向烹饪、电气和香烟相关烟火训练。',
        'purpose': '家庭室内摄像头录像；不能据此扩展为仓库、车间或工业专用。',
        'composition': '家庭烟火 YOLOv8n（mfranzon），同时检测火焰和烟雾',
        'tradeoff': '旧困难图中火焰和烟雾正确匹配数均少于基准；未通过整体替换筛查。烹饪烟气与危险烟雾仍需人工判断。',
        'validation': '仅作为手动试用。此前 27 张困难图筛查未通过；尚无独立家庭现场录像验证，不承诺优于通用模型。',
        'compute': '单模型，输入 640，固定阈值 0.35；实际速度取决于设备。',
        'guide': 'docs/EXISTING_MODEL_SCREEN_REPORT.md',
        'files': (SCENE_WEIGHTS['domestic']['path'],)},
    'forest': {
        'scene': '林地远景', 'status': '场景方案',
        'summary': '用于山林、山坡等固定摄像头远景；发布者主要面向早期山火烟雾。',
        'purpose': '室外山林固定机位、山坡远景中的烟雾与可见火焰录像对照。',
        'composition': 'SAINet v11.1 / YOLO26m，同时检测烟雾和火焰',
        'tradeoff': '发布者公开评测主要针对烟雾；不代表火焰同等可靠。云雾、扬尘仍可能误提示；旧困难图筛查未通过。',
        'validation': '按发布者的室外远景训练用途提供试用；未完成本项目独立林地录像验证，旧筛查失败记录保留。',
        'compute': '单个较大模型，输入 640，固定阈值 0.35；可能比通用模型更慢，阈值尚未按现场校准。',
        'guide': 'docs/SCENE_MODEL_SELECTION.md',
        'files': (SCENE_WEIGHTS['forest']['path'],)},
    'uav': {
        'scene': '无人机巡林', 'status': '场景方案',
        'summary': '用于无人机俯拍林地录像；采用航拍候选的火焰分支，烟雾沿用基准。',
        'purpose': '无人机巡林、俯拍林地录像中的可见火焰对照；区别于固定机位林地远景。',
        'composition': '无人机 YOLO26m 检测火焰（0.50）+ 基准 YOLOv8n 检测烟雾（0.35）',
        'tradeoff': '未直接采用无人机模型的烟雾分支，因为旧视频对照中误提示较多。当前使用整帧 640 推理，未接入 4K 切片，小目标仍可能漏检。',
        'validation': '该组合已做旧开发录像及一次内部留出检查；不等于独立航拍验证，烟雾分支也不是航拍专用。',
        'compute': '每次运行两个模型；火焰阈值 0.50、烟雾阈值 0.35，实际速度取决于设备。',
        'guide': 'docs/CLASS_ROUTING_VALIDATION_REPORT.md',
        'files': ('models/best.pt', SCENE_WEIGHTS['uav']['path'])},
})


def profile_details(profile):
    if profile not in PROFILE_DETAILS:
        raise ValueError('未知检测模式：' + str(profile))
    return {**PROFILE_DETAILS[profile], 'profile': profile, 'name': PROFILE_NAMES[profile]}


def profile_availability(profile, root=None):
    """Cheap file presence check. Hash validation remains in diagnostics/runtime."""
    root = Path(root) if root is not None else ROOT
    details = profile_details(profile)
    missing = []
    for relative in details['files']:
        path = root / relative
        try:
            present = path.is_file() and path.stat().st_size > 0
        except OSError:
            present = False
        if not present:
            missing.append(relative)
    return {'profile': profile, 'ready': not missing, 'missing': missing,
            'message': '模型文件已找到' if not missing else '缺少模型文件，暂不可开始检测'}


def selection_metadata(profile):
    details = profile_details(profile)
    return {'schema_version': 1, 'profile': profile, 'scene': details['scene'], 'name': details['name'],
            'status': details['status'], 'composition': details['composition'],
            'tradeoff': details['tradeoff'], 'validation': details['validation'],
            'source_url': SCENE_WEIGHTS.get(profile, {}).get('source_url'),
            'selection': 'configured_profile; no automatic scene classification'}


def profile_weight_specs(profile):
    """Relative path + checksum pairs used by diagnostics and delivery."""
    profile_details(profile)
    if profile in ('domestic', 'forest'):
        spec = SCENE_WEIGHTS[profile]
        return [(spec['path'], spec['sha256'])]
    weights = [('models/best.pt', ORIGINAL_SHA256)]
    if profile == 'brightlight':
        weights.append(('models/candidates/brightlight-0da45ff9f6d2/best.pt', CANDIDATE_SHA256))
    elif profile == 'modern':
        weights.append(('models/candidates/general26s-5397b43994c3/best.pt', MODERN_SHA256))
    elif profile == 'uav':
        weights.append((SCENE_WEIGHTS['uav']['path'], SCENE_WEIGHTS['uav']['sha256']))
    return weights


def verify_weight(path, expected):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'所选检测模式缺少模型文件：{path}')
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f'模型文件与已验证版本不一致：{path.name}')


def route_detections(reference, candidate, fire_threshold=.35):
    """Class routing, not score averaging; other never overrides a real fire box."""
    routed = []
    for boxes, label in ((candidate, 'fire'), (reference, 'smoke')):
        for box in boxes:
            if box['label'] == label and box['confidence'] >= (fire_threshold if label == 'fire' else .35):
                routed.append({**box, 'class_id': {'smoke': 0, 'fire': 1}[label],
                               'bbox_xyxy': list(box['bbox_xyxy'])})
    return routed


class BrightlightFusionDetector:
    profile = 'brightlight'
    candidate_path = CANDIDATE_PATH
    candidate_sha256 = CANDIDATE_SHA256
    candidate_repo = 'Nocluee100/Fire-and-Smoke-Detection-yolov8-v1'
    candidate_revision = '0da45ff9f6d2c52563972f6b5ce2312a7cd02726'
    candidate_names = {'fire', 'smoke', 'other'}
    fire_threshold = .35
    validation_note = 'Development candidate: fewer confirmed fire false hints, lower fire coverage and longer delay in some videos. Not independently validated.'

    def __init__(self, weights, device='auto', confidence=.35):
        if not math.isfinite(confidence) or abs(confidence-.35) > 1e-9:
            raise ValueError(f'{PROFILE_NAMES[self.profile]}使用固定阈值 0.35。')
        verify_weight(weights, ORIGINAL_SHA256)
        verify_weight(self.candidate_path, self.candidate_sha256)
        from core.detector import Detector
        from ultralytics import YOLO
        self.reference = Detector(Path(weights), device, .35)
        self.device = self.reference.device
        self.candidate = YOLO(str(self.candidate_path))
        if set(self.candidate.names.values()) != self.candidate_names:
            raise ValueError('候选模型的类别与已验证版本不一致。')
        self.model_manifest = {
            'kind': 'class_routing', 'profile': self.profile, 'name': PROFILE_NAMES[self.profile],
            'image_size': 640, 'canonical_names': {'0': 'smoke', '1': 'fire'},
            'branches': {
                'fire': {'repo_id': self.candidate_repo,
                         'revision': self.candidate_revision,
                         'weights': str(self.candidate_path), 'sha256': self.candidate_sha256, 'confidence': self.fire_threshold},
                'smoke': {'repo_id': 'rabahdev/fire-smoke-yolov8n',
                          'revision': '13017fe8af477c25f5298d168e2dfede4b000753',
                          'weights': str(weights), 'sha256': ORIGINAL_SHA256, 'confidence': .35}},
            'other_policy': 'Candidate other boxes are excluded; no overlap veto or risk contribution.',
            'validation_note': self.validation_note}

    def predict(self, frame, *, annotate=True, imgsz=640):
        _, reference = self.reference.predict(frame, annotate=False, imgsz=imgsz)
        result = self.candidate.predict(frame, device=self.device, conf=self.fire_threshold, imgsz=imgsz, verbose=False)[0]
        boxes = []
        for x1, y1, x2, y2, confidence, class_id in result.boxes.data.cpu().tolist():
            boxes.append({'class_id': int(class_id), 'label': self.candidate.names[int(class_id)],
                          'confidence': round(confidence, 6),
                          'bbox_xyxy': [round(v, 2) for v in (x1, y1, x2, y2)]})
        detections = route_detections(reference, boxes, self.fire_threshold)
        if not annotate:
            return None, detections
        import cv2
        annotated = frame.copy()
        for box in detections:
            x1, y1, x2, y2 = map(round, box['bbox_xyxy'])
            color = (0, 140, 255) if box['label'] == 'fire' else (230, 230, 230)
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            cv2.putText(annotated, f'{box["label"]} {box["confidence"]:.2f}',
                        (x1, max(14, y1-4)), cv2.FONT_HERSHEY_SIMPLEX, .45, color, 1)
        return annotated, detections


class ModernFireFusionDetector(BrightlightFusionDetector):
    profile = 'modern'
    candidate_path = MODERN_PATH
    candidate_sha256 = MODERN_SHA256
    candidate_repo = 'SalahALHaismawi/yolov26-fire-detection'
    candidate_revision = '5397b43994c39c2ed4ef81a6996c6334e2b7a5fa'
    validation_note = ('Reused 30 development videos: fire false-positive frames 857->248, '
                       'true-positive frames 5713->6151; original smoke branch preserved. '
                       'Not independently validated; some scenes regress and localization remains limited.')


class UAVFireFusionDetector(BrightlightFusionDetector):
    profile = 'uav'
    candidate_path = ROOT / SCENE_WEIGHTS['uav']['path']
    candidate_sha256 = SCENE_WEIGHTS['uav']['sha256']
    candidate_repo = SCENE_WEIGHTS['uav']['repo_id']
    candidate_revision = SCENE_WEIGHTS['uav']['revision']
    candidate_names = {'fire', 'smoke'}
    fire_threshold = .50
    validation_note = ('UAV publisher scope; fire-only routing at 0.50 with original smoke at 0.35. '
                       'Prior development and internal holdout checks, no independent aerial validation; '
                       'full-frame inference, no sliced inference.')


class SceneDetector:
    """Use the actual scene checkpoint for both classes, with canonical IDs."""
    def __init__(self, profile, device='auto', confidence=.35):
        if not math.isfinite(confidence) or abs(confidence-.35) > 1e-9:
            raise ValueError(f'{PROFILE_NAMES[profile]}使用固定阈值 0.35。')
        spec = SCENE_WEIGHTS[profile]
        path = ROOT / spec['path']
        verify_weight(path, spec['sha256'])
        from core.detector import Detector
        self.detector = Detector(path, device, confidence)
        self.device = self.detector.device
        branch = {key: spec[key] for key in ('repo_id', 'revision', 'sha256', 'source_url')}
        branch.update(weights=str(path), confidence=confidence)
        self.model_manifest = {
            'kind': 'scene_checkpoint', 'profile': profile, 'name': PROFILE_NAMES[profile],
            'image_size': 640, 'canonical_names': {'0': 'smoke', '1': 'fire'},
            'branches': {label: dict(branch) for label in ('fire', 'smoke')},
            'validation_note': profile_details(profile)['validation']}

    def predict(self, frame, *, annotate=True, imgsz=640):
        image, boxes = self.detector.predict(frame, annotate=annotate, imgsz=imgsz)
        return image, [{**box, 'class_id': {'smoke': 0, 'fire': 1}[box['label']]} for box in boxes]


def create_detector(weights, device='auto', confidence=.35, profile='original'):
    if profile in ('domestic', 'forest'):
        return SceneDetector(profile, device, confidence)
    if profile == 'uav':
        return UAVFireFusionDetector(weights, device, confidence)
    if profile == 'modern':
        return ModernFireFusionDetector(weights, device, confidence)
    if profile == 'brightlight':
        return BrightlightFusionDetector(weights, device, confidence)
    if profile != 'original':
        raise ValueError('未知检测模式：' + str(profile))
    from core.detector import Detector
    return Detector(Path(weights), device, confidence)
