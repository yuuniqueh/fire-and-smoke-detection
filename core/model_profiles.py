"""Runtime detector selection; the frozen original detector stays unchanged."""
import hashlib
import math
from pathlib import Path

from core.runtime import ROOT

ORIGINAL_SHA256 = 'b91633799ceb052c814b4f8b77a37efc9a40f002d528df97d74463585fa4f28f'
CANDIDATE_SHA256 = '8e23e1110a47dad1fb81b26984de1ca88e751cec0c070894f1a7bd4211c2d0b0'
CANDIDATE_PATH = ROOT / 'models/candidates/brightlight-0da45ff9f6d2/best.pt'
PROFILE_NAMES = {'original': '原模型', 'brightlight': '减少反光误报（候选）'}


def verify_weight(path, expected):
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f'所选检测模式缺少模型文件：{path}')
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != expected:
        raise ValueError(f'模型文件与已验证版本不一致：{path.name}')


def route_detections(reference, candidate):
    """Class routing, not score averaging; other never overrides a real fire box."""
    routed = []
    for boxes, label in ((candidate, 'fire'), (reference, 'smoke')):
        for box in boxes:
            if box['label'] == label and box['confidence'] >= .35:
                routed.append({**box, 'class_id': {'smoke': 0, 'fire': 1}[label],
                               'bbox_xyxy': list(box['bbox_xyxy'])})
    return routed


class BrightlightFusionDetector:
    def __init__(self, weights, device='auto', confidence=.35):
        if not math.isfinite(confidence) or abs(confidence-.35) > 1e-9:
            raise ValueError('减少反光误报候选模式使用已验证的固定阈值 0.35。')
        verify_weight(weights, ORIGINAL_SHA256)
        verify_weight(CANDIDATE_PATH, CANDIDATE_SHA256)
        from core.detector import Detector
        from ultralytics import YOLO
        self.reference = Detector(Path(weights), device, .35)
        self.device = self.reference.device
        self.candidate = YOLO(str(CANDIDATE_PATH))
        if set(self.candidate.names.values()) != {'fire', 'smoke', 'other'}:
            raise ValueError('候选模型的类别与已验证版本不一致。')
        self.model_manifest = {
            'kind': 'class_routing', 'profile': 'brightlight', 'name': PROFILE_NAMES['brightlight'],
            'image_size': 640, 'canonical_names': {'0': 'smoke', '1': 'fire'},
            'branches': {
                'fire': {'repo_id': 'Nocluee100/Fire-and-Smoke-Detection-yolov8-v1',
                         'revision': '0da45ff9f6d2c52563972f6b5ce2312a7cd02726',
                         'weights': str(CANDIDATE_PATH), 'sha256': CANDIDATE_SHA256, 'confidence': .35},
                'smoke': {'repo_id': 'rabahdev/fire-smoke-yolov8n',
                          'revision': '13017fe8af477c25f5298d168e2dfede4b000753',
                          'weights': str(weights), 'sha256': ORIGINAL_SHA256, 'confidence': .35}},
            'other_policy': 'Candidate other boxes are excluded; no overlap veto or risk contribution.',
            'validation_note': 'Development candidate: fewer confirmed fire false hints, lower fire coverage and longer delay in some videos. Not independently validated.'}

    def predict(self, frame, *, annotate=True):
        _, reference = self.reference.predict(frame, annotate=False)
        result = self.candidate.predict(frame, device=self.device, conf=.35, imgsz=640, verbose=False)[0]
        boxes = []
        for x1, y1, x2, y2, confidence, class_id in result.boxes.data.cpu().tolist():
            boxes.append({'class_id': int(class_id), 'label': self.candidate.names[int(class_id)],
                          'confidence': round(confidence, 6),
                          'bbox_xyxy': [round(v, 2) for v in (x1, y1, x2, y2)]})
        detections = route_detections(reference, boxes)
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


def create_detector(weights, device='auto', confidence=.35, profile='original'):
    if profile == 'brightlight':
        return BrightlightFusionDetector(weights, device, confidence)
    if profile != 'original':
        raise ValueError('未知检测模式：' + str(profile))
    from core.detector import Detector
    return Detector(Path(weights), device, confidence)
