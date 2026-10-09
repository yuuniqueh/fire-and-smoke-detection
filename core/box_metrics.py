"""Fixed-confidence localization counts, greedy same-class IoU>=threshold matching."""
from core.temporal import iou


def count_boxes(truth, detections, threshold=.5):
    pairs = []
    for ti, target in enumerate(truth):
        for pi, prediction in enumerate(detections):
            if target['label'] == prediction['label']:
                overlap = iou(target['xyxy'], prediction['bbox_xyxy'])
                if overlap >= threshold:
                    pairs.append((overlap, ti, pi))
    matched_truth, matched_predictions = set(), set()
    for _, ti, pi in sorted(pairs, reverse=True):
        if ti not in matched_truth and pi not in matched_predictions:
            matched_truth.add(ti)
            matched_predictions.add(pi)
    counts = {}
    for label in ('fire', 'smoke'):
        tp = sum(truth[i]['label'] == label for i in matched_truth)
        fp = sum(p['label'] == label for i, p in enumerate(detections) if i not in matched_predictions)
        fn = sum(t['label'] == label for i, t in enumerate(truth) if i not in matched_truth)
        counts[label] = {'tp': tp, 'fp': fp, 'fn': fn}
    return counts


def sum_counts(rows):
    result = {}
    for label in ('fire', 'smoke'):
        tp, fp, fn = (sum(row[label][key] for row in rows) for key in ('tp', 'fp', 'fn'))
        result[label] = {'tp': tp, 'fp': fp, 'fn': fn,
                         'precision': tp / (tp + fp) if tp + fp else None,
                         'recall': tp / (tp + fn) if tp + fn else None}
    return result
