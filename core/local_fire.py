"""Geometry for an experimental small-fire recheck; not enabled in the app."""
import math
from dataclasses import dataclass

from core.temporal import iou


@dataclass(frozen=True)
class LocalFireConfig:
    crop_fraction: float = .60
    confidence: float = .35
    max_frame_area_ratio: float = .05
    duplicate_iou: float = .50
    interior_edge_margin_px: float = 1.0

    def __post_init__(self):
        if not .5 <= self.crop_fraction < 1:
            raise ValueError('Crop fraction must cover the frame with four corner crops.')
        if not 0 < self.confidence <= 1 or not 0 < self.max_frame_area_ratio <= 1:
            raise ValueError('Invalid confidence or area limit.')
        if not 0 < self.duplicate_iou <= 1 or self.interior_edge_margin_px < 0:
            raise ValueError('Invalid duplicate or crop edge limit.')


def corner_crops(frame_size, config):
    width, height = frame_size
    if width <= 0 or height <= 0:
        raise ValueError('Frame dimensions must be positive.')
    cw = min(width, math.ceil(width * config.crop_fraction))
    ch = min(height, math.ceil(height * config.crop_fraction))
    return list(dict.fromkeys((x, y, x + cw, y + ch)
                             for y in (0, height - ch) for x in (0, width - cw)))


def map_fire_candidate(detection, crop, frame_size, config):
    """Map crop pixels to full-frame pixels; reject tiny fragments at cut edges."""
    if detection['label'] != 'fire':
        return None
    score = detection['confidence']
    if not math.isfinite(score) or score < config.confidence:
        return None
    x0, y0, xend, yend = crop
    width, height = frame_size
    x1, y1, x2, y2 = detection['bbox_xyxy']
    if not all(math.isfinite(v) for v in (x1, y1, x2, y2)):
        return None
    cw, ch = xend - x0, yend - y0
    x1, y1, x2, y2 = max(0., x1), max(0., y1), min(float(cw), x2), min(float(ch), y2)
    if x2 <= x1 or y2 <= y1:
        return None
    margin = config.interior_edge_margin_px
    if ((x0 > 0 and x1 <= margin) or (y0 > 0 and y1 <= margin)
            or (xend < width and x2 >= cw - margin)
            or (yend < height and y2 >= ch - margin)):
        return None
    if (x2 - x1) * (y2 - y1) / (width * height) > config.max_frame_area_ratio:
        return None
    return {**detection, 'bbox_xyxy': [round(v, 2) for v in (x1 + x0, y1 + y0, x2 + x0, y2 + y0)]}


def merge_preserving_reference(reference, candidates, config):
    """Never replace baseline boxes; suppress duplicate extra fire boxes only."""
    merged = list(reference)
    for candidate in sorted(candidates, key=lambda d: d['confidence'], reverse=True):
        if candidate['label'] != 'fire':
            continue
        if not any(existing['label'] == 'fire' and
                   iou(existing['bbox_xyxy'], candidate['bbox_xyxy']) >= config.duplicate_iou
                   for existing in merged):
            merged.append(candidate)
    return merged
