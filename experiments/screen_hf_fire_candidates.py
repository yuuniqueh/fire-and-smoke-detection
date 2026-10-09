"""Bounded development comparison; no training, holdout inference or app changes."""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.box_metrics import count_boxes, sum_counts
from core.box_review import training_eligible
from core.detector import Detector
from core.evaluation import evaluate_video
from core.temporal import EventTracker, TemporalConfig
from experiments.benchmark_firesense import aggregate, digest, read, write
from experiments.fetch_hf_fire_candidates import CANDIDATES, candidate_folder
from experiments.probe_local_fire import BASELINE, prepare, small_matches, panel

THRESHOLDS = (.15, .25, .35, .50)
REFERENCE_LOW = ROOT / 'outputs/confidence-probe-20261006-205455-969393'


def filtered(packet, threshold):
    return {**packet, 'detections': [box for box in packet['detections'] if box['confidence'] >= threshold]}


def load_packets(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def replay(row, packets, plan, state_log=None):
    results = {}
    for threshold in THRESHOLDS:
        key = str(threshold)
        tracker = EventTracker(TemporalConfig(**plan['temporal_config']))
        selected, states = [], []
        for packet in packets:
            item = filtered(packet, threshold)
            selected.append(item)
            states.append(tracker.update(item['timestamp_s'], item['detections'], item['frame_size']))
        results[key] = evaluate_video(row, selected, states, plan['episode_merge_gap_s'])
        if state_log is not None and threshold == .35:
            with state_log.open('w', encoding='utf-8') as output:
                for state in states:
                    output.write(json.dumps(state) + '\n')
    return results


def gates(reference_cases, candidate_cases):
    original = {case['filename']: case['metrics'] for case in reference_cases}
    changes = []
    for case in candidate_cases:
        for label in ('fire', 'smoke'):
            before, after = original[case['filename']][label], case['metrics'][label]
            if before['excluded']:
                continue
            old = before['temporal']['missed_intervals_frames']
            new = after['temporal']['missed_intervals_frames']
            changes.append({'filename': case['filename'], 'label': label,
                            'recovered': [item for item in old if item not in new],
                            'lost': [item for item in new if item not in old]})
    checks = {
        'recovers_fire_event': any(item['recovered'] for item in changes if item['label'] == 'fire'),
        'no_lost_events': not any(item['lost'] for item in changes),
        'fire_false_guard': aggregate(candidate_cases, 'development', 'fire', 'temporal')['false_hint_episodes'] <= 3,
        'smoke_false_guard': aggregate(candidate_cases, 'development', 'smoke', 'temporal')['false_hint_episodes'] <= 1,
    }
    return {'checks': checks, 'passed': all(checks.values()),
            'changes': [item for item in changes if item['recovered'] or item['lost']]}


def image_stage(folder, plan, reference, candidate):
    import cv2
    import numpy as np
    rows = read(folder / 'box-annotations-snapshot.json')['images']
    entries = {entry['filename']: entry for entry in plan['entries']}
    original = {}
    baseline = {case['filename']: case for case in read(BASELINE / 'results.json')['cases']}
    for case_dir in BASELINE.glob('case-*'):
        source = read(case_dir / 'case.json')['filename']
        if source in {row['source'] for row in rows}:
            path = case_dir / 'detections.jsonl'
            if digest(path) != baseline[source]['log_sha256']['detections.jsonl']:
                raise ValueError('Baseline image log changed.')
            original[source] = {packet['frame_index']: packet for packet in load_packets(path)}
    cases, panels = [], []
    for number, row in enumerate(rows, 1):
        if not training_eligible(row) or entries[row['source']]['split'] != 'development':
            raise ValueError('Only completed development box annotations allowed.')
        path = ROOT / 'data/firesense/box-review-pilot-v1' / row['image']
        if digest(path) != row['image_sha256']:
            raise ValueError('Annotated image changed.')
        frame = cv2.imread(str(path))
        if frame is None or [frame.shape[1], frame.shape[0]] != [row['width'], row['height']]:
            raise ValueError('Invalid image.')
        predictions, timing = {}, {}
        for name, detector in (('reference', reference), ('candidate', candidate)):
            start = time.perf_counter()
            predictions[name] = detector.predict(frame, annotate=False)[1]
            timing[name] = time.perf_counter() - start
        if filtered({'detections': predictions['reference']}, .35)['detections'] != original[row['source']][row['frame_index']]['detections']:
            raise ValueError('Reference image does not reproduce baseline at 0.35.')
        counts, small = {}, {}
        for name, detections in predictions.items():
            counts[name], small[name] = {}, {}
            for threshold in THRESHOLDS:
                selected = filtered({'detections': detections}, threshold)['detections']
                counts[name][str(threshold)] = count_boxes(row['boxes'], selected)
                small[name][str(threshold)] = small_matches(row, selected)
        cases.append({'image': row['image'], 'source': row['source'], 'frame_index': row['frame_index'],
                      'predictions': predictions, 'counts': counts, 'small_fire': small, 'prediction_call_s': timing})
        columns = [panel(frame, row['boxes'], 'Human annotation', truth=True)]
        columns += [panel(frame, filtered({'detections': predictions[name]}, .35)['detections'], name + ' conf=0.35') for name in ('reference', 'candidate')]
        triptych = np.hstack(columns)
        cv2.imwrite(str(folder / f'image-{number:02d}.png'), triptych)
        panels.append(triptych)
    for start in range(0, len(panels), 9):
        cv2.imwrite(str(folder / f'contact-sheet-{start // 9 + 1:02d}.png'), np.vstack(panels[start:start + 9]))
    totals = {name: {str(t): sum_counts([case['counts'][name][str(t)] for case in cases]) for t in THRESHOLDS} for name in ('reference', 'candidate')}
    small = {name: {str(t): {key: sum(case['small_fire'][name][str(t)][key] for case in cases) for key in ('truth', 'matched', 'missed')} for t in THRESHOLDS} for name in totals}
    # These deliberately difficult images are a diagnostic, not a video selection metric.
    warranted = any(totals['candidate'][str(t)][label]['tp'] > totals['reference']['0.35'][label]['tp']
                    for t in THRESHOLDS for label in ('fire', 'smoke'))
    result = {'aggregate': totals, 'small_fire': small, 'cases': cases, 'full_video_check_warranted': warranted,
              'note': 'Image gate requires any class TP gain at any predeclared threshold, then full videos judge tradeoffs; no claim of representativeness or adoption.'}
    write(folder / 'results.json', result)
    return {key: result[key] for key in ('aggregate', 'small_fire', 'full_video_check_warranted')}


def video_stage(folder, plan, candidate, review):
    import cv2
    entries = [entry for entry in plan['entries'] if entry['split'] == 'development']
    rows = {row['filename']: row for row in review['videos']}
    frozen = {case['filename']: case for case in read(BASELINE / 'results.json')['cases']}
    cached_proposal = read(REFERENCE_LOW / 'proposal.json')
    if (cached_proposal['plan_sha256'] != digest(ROOT / 'data/firesense/evaluation-v1/plan.json') or
            cached_proposal['annotation_sha256'] != plan['review_sha256'] or
            cached_proposal['same_temporal_config'] != plan['temporal_config']):
        raise ValueError('Reference cache used different evaluation inputs.')
    cached = {}
    for path in REFERENCE_LOW.glob('case-*/comparison.json'):
        source = read(path)['0.35']['filename']
        cached[source] = path.parent / 'low-confidence-detections.jsonl'
    cases = {name: {str(t): [] for t in THRESHOLDS} for name in ('reference', 'candidate')}
    inference_s, total_frames, log_hashes = 0., 0, {}
    started = time.perf_counter()
    for number, entry in enumerate(entries, 1):
        source = ROOT / 'videos' / entry['filename']
        if digest(source) != entry['source_sha256']:
            raise ValueError('Source changed.')
        print(f'BEGIN {number}/{len(entries)} {entry["filename"]}', flush=True)
        case_dir = folder / f'case-{number:02d}-{source.stem}'
        case_dir.mkdir()
        reference_packets = load_packets(cached[entry['filename']])
        reference_metrics = replay(rows[entry['filename']], reference_packets, plan)
        if reference_metrics['0.35'] != frozen[entry['filename']]['metrics']:
            raise ValueError('Cached reference fails frozen per-video metric reproduction.')
        cached_metrics = read(cached[entry['filename']].parent / 'comparison.json')
        for key in ('0.15', '0.25', '0.35'):
            if reference_metrics[key] != cached_metrics[key]['metrics']:
                raise ValueError('Reference cache does not reproduce prior threshold metrics.')
        cap = cv2.VideoCapture(str(source))
        packets, call_s = [], 0.
        log = case_dir / 'low-confidence-detections.jsonl'
        try:
            with log.open('w', encoding='utf-8') as output:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    index = len(packets)
                    ref = reference_packets[index]
                    if ref['frame_size'] != [frame.shape[1], frame.shape[0]]:
                        raise ValueError('Reference frame dimensions differ.')
                    start = time.perf_counter()
                    detections = candidate.predict(frame, annotate=False)[1]
                    call_s += time.perf_counter() - start
                    packet = {'frame_index': index, 'timestamp_s': round(index / entry['fps'], 6),
                              'frame_size': [frame.shape[1], frame.shape[0]], 'detections': detections}
                    packets.append(packet)
                    output.write(json.dumps(packet) + '\n')
                    if len(packets) % 1000 == 0:
                        print(f'PROGRESS {number}/{len(entries)} {len(packets)}/{entry["decoded_frames"]}', flush=True)
        finally:
            cap.release()
        if len(packets) != entry['decoded_frames']:
            raise ValueError('Incomplete decoding.')
        candidate_metrics = replay(rows[entry['filename']], packets, plan, case_dir / 'temporal-035.jsonl')
        for key in cases['reference']:
            for name, metrics in (('reference', reference_metrics), ('candidate', candidate_metrics)):
                cases[name][key].append({**entry, 'metrics': metrics[key]})
        hashes = {'candidate_raw': digest(log), 'candidate_temporal_035': digest(case_dir / 'temporal-035.jsonl'),
                  'reference_raw': digest(cached[entry['filename']])}
        log_hashes[case_dir.name] = hashes
        write(case_dir / 'case.json', {**entry, 'reference_cache_path': str(cached[entry['filename']].relative_to(ROOT)),
              'metrics': {'reference': reference_metrics, 'candidate': candidate_metrics},
              'prediction_call_s': call_s, 'log_sha256': hashes})
        inference_s += call_s
        total_frames += len(packets)
        write(folder / 'progress.json', {'completed': number, 'total': len(entries), 'frames': total_frames})
        print(f'END {number}/{len(entries)} frames={len(packets)}', flush=True)
    totals = {name: {key: {label: {mode: aggregate(values, 'development', label, mode) for mode in ('single_frame', 'temporal')} for label in ('fire', 'smoke')} for key, values in group.items()} for name, group in cases.items()}
    checks = {key: gates(cases['reference']['0.35'], values) for key, values in cases['candidate'].items()}
    result = {'aggregate': totals, 'gates': checks, 'cases': cases, 'processed_frames': total_frames,
              'log_sha256': log_hashes, 'candidate_prediction_call_s': inference_s,
              'candidate_prediction_fps': total_frames / inference_s, 'elapsed_s': time.perf_counter() - started,
              'adopted': False, 'timing_note': 'Warm GPU prediction calls include transfers and postprocess, exclude UI/drawing/export and replay; no same-run reference timing. Not desktop end-to-end FPS.',
              'decision': 'Numeric gate passed; localization and label review required' if any(item['passed'] for item in checks.values()) else 'No predeclared global threshold passes adoption gates'}
    write(folder / 'results.json', result)
    return {key: result[key] for key in ('processed_frames', 'aggregate', 'gates', 'candidate_prediction_fps', 'decision')}


def main():
    import numpy as np
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', choices=tuple(CANDIDATES), required=True)
    parser.add_argument('--stage', choices=('images', 'videos'), required=True)
    parser.add_argument('--image-run')
    args = parser.parse_args()
    source_path = candidate_folder(args.candidate) / 'source-manifest.json'
    source = read(source_path)
    weights = candidate_folder(args.candidate) / source['filename']
    if digest(weights) != CANDIDATES[args.candidate]['sha256'] or source['sha256'] != CANDIDATES[args.candidate]['sha256']:
        raise ValueError('Candidate changed.')
    if args.stage == 'videos':
        previous = Path(args.image_run or '').resolve()
        if (ROOT / 'outputs').resolve() not in previous.parents:
            raise ValueError('Supply completed project image diagnostic.')
        manifest = read(previous / 'run-manifest.json')
        if (manifest['status'] != 'completed' or digest(previous / 'results.json') != manifest['results_sha256'] or
                read(previous / 'proposal.json')['candidate_weights_sha256'] != source['sha256'] or
                not read(previous / 'results.json')['full_video_check_warranted']):
            raise ValueError('Image diagnostic incomplete or does not warrant videos.')
    extra = {'policy': 'Candidate whole-frame model; original 640 input and temporal settings. Infer once at 0.15, filter rounded logged confidence at fixed global thresholds. No crops, per-video tuning, training or app changes.',
             'thresholds_predeclared': THRESHOLDS, 'config': {'minimum_confidence': .15},
             'crop_cut_edge_policy': None, 'prior_art': [],
             'candidate_source': source, 'candidate_weights_sha256': source['sha256'],
             'candidate_weights_path': str(weights.relative_to(ROOT)),
             'candidate_source_manifest_sha256': digest(source_path), 'image_run': args.image_run,
             'image_screen_gate': 'Any class TP gain at any declared threshold vs reference 0.35 warrants full videos. Images alone do not select or reject a product operating point.',
             'video_adoption_gate': 'Recover >=1 original missed fire event, lose no original confirmed event in either class; false episodes fire<=3, smoke<=1. Original per-video interval truth frozen. Passing needs localization and pending boundary review.',
             'originality_note': 'Third-party model selection is not an original architecture.',
             'reference_cache': str(REFERENCE_LOW.relative_to(ROOT)),
             'reference_check': 'Verify reconstructed reference 0.35 exactly matches frozen per-image boxes or per-video metrics; save currently consumed cache hashes.',
             'confidence_note': 'Filtered low-threshold NMS outputs are replay operating points; not separate inference or confidence calibration. All declared points reported.'}
    folder, plan, proposal, review = prepare(args.candidate + '-' + args.stage, extra,
                                            ('experiments/screen_hf_fire_candidates.py', 'experiments/fetch_hf_fire_candidates.py'))
    try:
        candidate = Detector(weights, confidence=.15)
        candidate.predict(np.zeros((640, 640, 3), dtype=np.uint8), annotate=False)
        if args.stage == 'images':
            reference = Detector(ROOT / 'models/best.pt', confidence=.15)
            reference.predict(np.zeros((640, 640, 3), dtype=np.uint8), annotate=False)
            summary = image_stage(folder, plan, reference, candidate)
        else:
            summary = video_stage(folder, plan, candidate, review)
        if digest(ROOT / 'models/best.pt') != plan['weights_sha256'] or digest(weights) != source['sha256']:
            raise ValueError('Weights changed during experiment.')
        write(folder / 'run-manifest.json', {'status': 'completed', 'device': candidate.device,
              'candidate_names': candidate.model.names, 'candidate_parameters': sum(p.numel() for p in candidate.model.model.parameters()),
              'proposal_sha256': digest(folder / 'proposal.json'), 'results_sha256': digest(folder / 'results.json'),
              'completed_at_utc': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'output': str(folder), **summary}, ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'error': str(exc), 'proposal_sha256': digest(folder / 'proposal.json')})
        raise


if __name__ == '__main__':
    main()
