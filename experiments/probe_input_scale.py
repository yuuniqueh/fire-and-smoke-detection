"""Development-only input-scale experiment with frozen weights/confirmation rules."""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.detector import Detector
from core.temporal import EventTracker, TemporalConfig
from core.evaluation import evaluate_video
from experiments.benchmark_firesense import aggregate, digest, read, write

INPUT_SIZE = 960


class ScaleProbeDetector(Detector):
    """Only the experimental input size changes; product adapter stays at 640."""
    def predict(self, frame, *, annotate=False):
        result = self.model.predict(frame, device=self.device, conf=self.confidence,
                                    imgsz=INPUT_SIZE, verbose=False)[0]
        detections = []
        for x1, y1, x2, y2, confidence, class_id in result.boxes.data.cpu().tolist():
            detections.append({'class_id': int(class_id), 'label': self.model.names[int(class_id)],
                               'confidence': round(confidence, 6),
                               'bbox_xyxy': [round(v, 2) for v in (x1, y1, x2, y2)]})
        return result.plot() if annotate else None, detections


def main():
    import cv2
    import json
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    snapshot_path = plan_path.parent / plan['review_snapshot']
    if digest(snapshot_path) != plan['review_sha256'] or digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
        raise ValueError('Frozen inputs changed.')
    for name, expected in plan['code_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Frozen implementation changed: {name}')
    rows = {row['filename']: row for row in read(snapshot_path)['videos']}
    entries = [entry for entry in plan['entries'] if entry['split'] == 'development']
    folder = ROOT / 'outputs' / datetime.now().strftime('input-scale-probe-%Y%m%d-%H%M%S-%f')
    folder.mkdir()
    proposal = {'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'Development only; no holdout source images or annotations used.',
                'input_size': INPUT_SIZE, 'reference_input_size': 640, 'confidence': .35,
                'temporal_config': plan['temporal_config'], 'plan_sha256': digest(plan_path),
                'annotation_sha256': plan['review_sha256'], 'weights_sha256': plan['weights_sha256'],
                'runner_sha256': digest(Path(__file__)),
                'criterion': 'No regression of previously detected development events; strictly fewer misses in at least one class; false episodes not greater than original temporal fire=3, smoke=1. Inspect recovered detections for localization. Counts alone do not prove suitability.',
                'rationale': 'Check feature-scale sensitivity of small flames and thin smoke before choosing model training. Larger input cannot restore detail absent from the source.',
                'cases': [entry['filename'] for entry in entries]}
    write(folder / 'proposal.json', proposal)
    detector = ScaleProbeDetector(ROOT / 'models/best.pt', confidence=.35)
    cases = []
    started = time.perf_counter()
    for number, entry in enumerate(entries, 1):
        source = ROOT / 'videos' / entry['filename']
        if digest(source) != entry['source_sha256']:
            raise ValueError('Source changed.')
        print(f'BEGIN {number}/30 {source.name}', flush=True)
        case_folder = folder / f'case-{number:02d}-{source.stem}'
        case_folder.mkdir()
        tracker = EventTracker(TemporalConfig(**plan['temporal_config']))
        packets, snapshots = [], []
        capture = cv2.VideoCapture(str(source))
        inference_s = 0.0
        try:
            with (case_folder / 'detections.jsonl').open('w', encoding='utf-8') as detlog, \
                    (case_folder / 'temporal.jsonl').open('w', encoding='utf-8') as templog:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    before = time.perf_counter()
                    _, detections = detector.predict(frame)
                    inference_s += time.perf_counter() - before
                    packet = {'frame_index': len(packets), 'timestamp_s': round(len(packets) / entry['fps'], 6),
                              'frame_size': [frame.shape[1], frame.shape[0]], 'detections': detections}
                    snapshot = tracker.update(packet['timestamp_s'], detections, packet['frame_size'])
                    detlog.write(json.dumps(packet) + '\n')
                    templog.write(json.dumps(snapshot) + '\n')
                    packets.append(packet)
                    snapshots.append(snapshot)
            if len(packets) != entry['decoded_frames']:
                raise ValueError('Incomplete decoding.')
        finally:
            capture.release()
        case = {**entry, 'metrics': evaluate_video(rows[entry['filename']], packets, snapshots, plan['episode_merge_gap_s']),
                'prediction_call_s': inference_s,
                'log_sha256': {name: digest(case_folder / name) for name in ('detections.jsonl', 'temporal.jsonl')}}
        cases.append(case)
        write(case_folder / 'case.json', case)
        write(folder / 'progress.json', {'completed': number, 'total': len(entries)})
        print(f'END {number}/30 frames={len(packets)}', flush=True)
    totals = {label: {mode: aggregate(cases, 'development', label, mode) for mode in ('single_frame', 'temporal')}
              for label in ('fire', 'smoke')}
    baseline = read(ROOT / 'outputs/benchmark-v1-20261006-203720-465883/results.json')
    previous = {case['filename']: case for case in baseline['cases'] if case['split'] == 'development'}
    changes = []
    for case in cases:
        for label in ('fire', 'smoke'):
            before, after = previous[case['filename']]['metrics'][label], case['metrics'][label]
            if before['excluded']:
                continue
            if before['temporal'] != after['temporal']:
                changes.append({'filename': case['filename'], 'label': label,
                                'previous_missed_intervals': before['temporal']['missed_intervals_frames'],
                                'current_missed_intervals': after['temporal']['missed_intervals_frames'],
                                'previous_false_episodes': before['temporal']['false_hint_episodes'],
                                'current_false_episodes': after['temporal']['false_hint_episodes']})
    write(folder / 'results.json', {'proposal': proposal, 'aggregate': totals, 'cases': cases, 'changes': changes,
                                   'device': detector.device, 'elapsed_s': time.perf_counter() - started,
                                   'timing_note': 'Prediction-call duration includes transfer/postprocessing; baseline timing has different accounting and is not a comparable speed test.'})
    print(json.dumps({'output': str(folder), 'summary': {label: {mode: {'detected': m['detected_scene_events'],
                     'misses': m['missed_scene_events'], 'false_episodes': m['false_hint_episodes']}
                     for mode, m in values.items()} for label, values in totals.items()}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
