"""A thin, replaceable adapter around the third-party fire/smoke model."""
from pathlib import Path

import torch
from ultralytics import YOLO


class Detector:
    def __init__(self, weights: Path, device='auto', confidence=0.35):
        if not weights.is_file():
            raise FileNotFoundError('未找到模型权重，请先运行 download_model.py。')
        self.device = ('0' if torch.cuda.is_available() else 'cpu') if device == 'auto' else device
        if self.device == '0' and not torch.cuda.is_available():
            raise RuntimeError('GPU 当前不可用；可以使用 --device cpu。')
        self.confidence = confidence
        self.model = YOLO(str(weights))
        if set(self.model.names.values()) != {'fire', 'smoke'}:
            raise ValueError(f'权重类别不符合预期：{self.model.names}')

    def predict(self, frame, *, annotate=True, imgsz=640):
        result = self.model.predict(frame, device=self.device, conf=self.confidence,
                                    imgsz=imgsz, verbose=False)[0]
        detections = []
        for row in result.boxes.data.cpu().tolist():
            x1, y1, x2, y2, confidence, class_id = row
            detections.append({
                'class_id': int(class_id),
                'label': self.model.names[int(class_id)],
                'confidence': round(confidence, 6),
                'bbox_xyxy': [round(v, 2) for v in (x1, y1, x2, y2)],
            })
        return result.plot() if annotate else None, detections

