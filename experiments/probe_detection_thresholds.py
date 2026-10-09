"""Predeclared developmental-only confidence probe; never reads holdout images."""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.temporal import EventTracker, TemporalConfig
from core.evaluation import evaluate_video
from experiments.benchmark_firesense import aggregate, digest, read, write

THRESHOLDS = (.35, .25, .15)


def main():
    import cv2
    from core.detector import Detector
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    snapshot_path = plan_path.parent / plan['review_snapshot']
    if digest(snapshot_path) != plan['review_sha256'] or digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
        raise ValueError('Frozen inputs changed.')
    rows = {row['filename']: row for row in read(snapshot_path)['videos']}
    entries = [entry for entry in plan['entries'] if entry['split'] == 'development']
    folder = ROOT / 'outputs' / datetime.now().strftime('confidence-probe-%Y%m%d-%H%M%S-%f')
    folder.mkdir()
    proposal = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'scope': 'Development only, no holdout images or labels used for selection.',
                'thresholds_predeclared': THRESHOLDS, 'same_temporal_config': plan['temporal_config'],
                'plan_sha256': digest(plan_path), 'annotation_sha256': plan['review_sha256'],
                'criterion': 'No greater developmental scene misses than single-frame baseline: fire<=2, smoke<=1; false episodes below single-frame baseline: fire<52, smoke<67. Also inspect localization failures; passing counts alone not proof of field suitability.',
                'runner_sha256': digest(Path(__file__)),
                'filter_note': 'Infer once at 0.15; reuse boxes filtered at rounded logged confidence >= threshold. Check reconstructed 0.35 against original baseline.',
                'cases': [entry['filename'] for entry in entries]}
    write(folder / 'proposal.json', proposal)
    (folder / 'review-snapshot.json').write_bytes(snapshot_path.read_bytes())
    detector = Detector(ROOT / 'models/best.pt', confidence=min(THRESHOLDS))
    all_cases = {str(threshold): [] for threshold in THRESHOLDS}
    started = time.perf_counter()
    for number, entry in enumerate(entries, 1):
        source = ROOT / 'videos' / entry['filename']
        if digest(source) != entry['source_sha256']:
            raise ValueError('Source file changed.')
        print(f'BEGIN {number}/30 {entry["filename"]}', flush=True)
        case_folder = folder / f'case-{number:02d}-{source.stem}'
        case_folder.mkdir()
        capture = cv2.VideoCapture(str(source))
        count = 0
        packets = {str(threshold): [] for threshold in THRESHOLDS}
        snapshots = {str(threshold): [] for threshold in THRESHOLDS}
        trackers = {str(threshold): EventTracker(TemporalConfig(**plan['temporal_config'])) for threshold in THRESHOLDS}
        try:
            with (case_folder / 'low-confidence-detections.jsonl').open('w', encoding='utf-8') as log:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    _, detections = detector.predict(frame, annotate=False)
                    raw = {'frame_index': count, 'timestamp_s': round(count / entry['fps'], 6),
                           'frame_size': [frame.shape[1], frame.shape[0]], 'detections': detections}
                    log.write(json.dumps(raw) + '\n')
                    for threshold in THRESHOLDS:
                        key = str(threshold)
                        packet = {**raw, 'detections': [box for box in detections if box['confidence'] >= threshold]}
                        packets[key].append(packet)
                        snapshots[key].append(trackers[key].update(packet['timestamp_s'], packet['detections'], packet['frame_size']))
                    count += 1
                    if count % 1000 == 0:
                        print(f'PROGRESS {number}/30 {count}/{entry["decoded_frames"]}', flush=True)
            if count != entry['decoded_frames']:
                raise ValueError('Incomplete source decoding.')
        finally:
            capture.release()
        for threshold in THRESHOLDS:
            key = str(threshold)
            case = {**entry, 'metrics': evaluate_video(rows[entry['filename']], packets[key], snapshots[key], plan['episode_merge_gap_s'])}
            all_cases[key].append(case)
        write(case_folder / 'comparison.json', {key: all_cases[key][-1] for key in all_cases})
        write(folder / 'progress.json', {'completed': number, 'total': 30, 'last_source': entry['filename']})
        print(f'END {number}/30 frames={count}', flush=True)
    totals = {key: {label: {mode: aggregate(cases, 'development', label, mode) for mode in ('single_frame', 'temporal')}
                    for label in ('fire', 'smoke')} for key, cases in all_cases.items()}
    old = read(ROOT / 'outputs/benchmark-v1-20261006-203720-465883/results.json')
    original = {case['filename']: case['metrics'] for case in old['cases'] if case['split'] == 'development'}
    differs = [case['filename'] for case in all_cases['0.35'] if case['metrics'] != original[case['filename']]]
    result = {'proposal': proposal, 'device': detector.device, 'elapsed_s': round(time.perf_counter() - started, 3),
              'reconstructed_035_metric_differences': differs, 'aggregate': totals, 'cases': all_cases}
    write(folder / 'results.json', result)
    print(json.dumps({'output': str(folder), 'reconstructed_035_metric_differences': differs,
                      'summary': {key: {label: {'events': f'{m["temporal"]["detected_scene_events"]}/{m["temporal"]["ground_truth_scene_events"]}',
                                               'misses': m['temporal']['missed_scene_events'], 'false_episodes': m['temporal']['false_hint_episodes'],
                                               'delay_s': m['temporal']['mean_first_active_hint_delay_s']}
                                        for label, m in values.items()} for key, values in totals.items()}}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
