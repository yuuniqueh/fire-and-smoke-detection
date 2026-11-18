"""Scene/class visible-state evaluation. Not box mAP or physical-risk evaluation."""
import math
from core.data_review import validate_video


def episodes(flags, fps, gap_s=1.0):
    """Return [start, end) runs, joining gaps <= gap_s without modifying truth flags."""
    if not math.isfinite(fps) or fps <= 0 or not math.isfinite(gap_s) or gap_s < 0:
        raise ValueError('Invalid time basis or episode gap.')
    result = []
    for frame, present in enumerate(flags):
        if present:
            if result and (frame - result[-1][1]) / fps <= gap_s + 1e-9:
                result[-1][1] = frame + 1
            else:
                result.append([frame, frame + 1])
    return result


def score_states(truth, prediction, fps, gap_s=1.0):
    if len(truth) != len(prediction):
        raise ValueError('Truth/prediction lengths differ.')
    ground_events = episodes(truth, fps, gap_s)
    false_flags = [bool(pred) and not actual for actual, pred in zip(truth, prediction)]
    delays = []
    misses = []
    for start, end in ground_events:
        matched = next((i for i in range(start, end) if truth[i] and prediction[i]), None)
        if matched is None:
            misses.append([start, end])
        else:
            delays.append((matched - start) / fps)
    positive = sum(bool(value) for value in truth)
    negative = len(truth) - positive
    true_positive = sum(bool(a) and bool(b) for a, b in zip(truth, prediction))
    predicted = sum(bool(value) for value in prediction)
    false_events = episodes(false_flags, fps, gap_s)
    return {'ground_truth_scene_events': len(ground_events), 'detected_scene_events': len(delays),
            'missed_scene_events': len(misses), 'missed_intervals_frames': misses,
            'first_active_hint_delays_s': [round(delay, 6) for delay in delays],
            'mean_first_active_hint_delay_s': round(sum(delays) / len(delays), 6) if delays else None,
            'negative_duration_s': round(negative / fps, 6),
            'false_hint_episodes': len(false_events), 'false_hint_intervals_frames': false_events,
            'false_hint_episodes_per_negative_minute': round(len(false_events) * 60 * fps / negative, 6) if negative else None,
            'ground_truth_positive_frames': positive, 'active_hint_frames': predicted,
            'true_positive_hint_frames': true_positive, 'false_positive_hint_frames': sum(false_flags),
            'false_negative_hint_frames': positive - true_positive,
            'visible_state_precision': round(true_positive / predicted, 6) if predicted else None,
            'visible_state_recall': round(true_positive / positive, 6) if positive else None,
            'active_hint_episodes': len(episodes(prediction, fps, gap_s))}


def evaluate_video(row, detection_packets, temporal_packets, gap_s=1.0):
    validate_video(row)
    if not row['full_video_reviewed'] or row['quality'].startswith('quarantined'):
        raise ValueError('Source not eligible for this annotation-based development comparison.')
    count, fps = row['decoded_frames'], row['fps']
    if len(detection_packets) != count or len(temporal_packets) != count:
        raise ValueError('Inference logs do not cover every source frame.')
    flags = {mode: {label: [] for label in ('fire', 'smoke')} for mode in ('single_frame', 'temporal')}
    emitted = {'fire': 0, 'smoke': 0}
    for index, (detected, temporal) in enumerate(zip(detection_packets, temporal_packets)):
        if detected.get('observed', True) is False or temporal.get('observed', True) is False:
            raise ValueError('Sparse monitoring contains unobserved frames; this every-frame evaluator cannot treat them as negatives.')
        if detected['frame_index'] != index or abs(detected['timestamp_s'] - index / fps) > 1e-5 or abs(temporal['timestamp_s'] - index / fps) > 1e-5:
            raise ValueError('Log frame order or timestamps inconsistent with annotations.')
        for label in ('fire', 'smoke'):
            flags['single_frame'][label].append(any(item['label'] == label for item in detected['detections']))
            flags['temporal'][label].append(any(track['label'] == label and track['state'] == 'confirmed' for track in temporal['tracks']))
        for transition in temporal['transitions']:
            if transition['type'] == 'event_confirmed' and transition['alert_emitted']:
                emitted[transition['label']] += 1
    result = {}
    for label in ('fire', 'smoke'):
        if row['class_status'][label] in ('unknown', 'uncertain'):
            result[label] = {'excluded': True, 'reason': row['class_status'][label]}
            continue
        truth = [False] * count
        for item in row['intervals']:
            if item['label'] == label:
                truth[item['start_frame']:item['end_frame_exclusive']] = [True] * (item['end_frame_exclusive'] - item['start_frame'])
        result[label] = {'excluded': False, **{mode: score_states(truth, flags[mode][label], fps, gap_s)
                                              for mode in ('single_frame', 'temporal')},
                         'temporal_raw_emitted_confirmations': emitted[label]}
    return result
