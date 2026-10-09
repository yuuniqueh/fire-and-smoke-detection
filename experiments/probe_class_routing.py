"""Fixed fire/smoke routing feasibility study; experiment only, no app changes."""
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
from experiments.screen_hf_fire_candidates import gates, load_packets, filtered

CANDIDATE_RUN = ROOT / 'outputs/local-fire-uav-videos-20261009-192903-334651'
IMAGE_RUN = ROOT / 'outputs/local-fire-uav-images-20261009-192738-192895'
FIRE_THRESHOLD = .50
SMOKE_THRESHOLD = .35
CANONICAL_IDS = {'smoke': 0, 'fire': 1}


def route_packet(reference, candidate):
    if (reference['frame_index'] != candidate['frame_index'] or
            abs(reference['timestamp_s'] - candidate['timestamp_s']) > 1e-5 or
            reference['frame_size'] != candidate['frame_size']):
        raise ValueError('Cannot combine predictions from different frames.')
    detections = []
    for packet, label, threshold in ((candidate, 'fire', FIRE_THRESHOLD),
                                      (reference, 'smoke', SMOKE_THRESHOLD)):
        for box in packet['detections']:
            if box['label'] == label and box['confidence'] >= threshold:
                detections.append({**box, 'class_id': CANONICAL_IDS[label],
                                   'bbox_xyxy': list(box['bbox_xyxy'])})
    return {**reference, 'detections': detections}


def replay_one(row, packets, plan, log_path=None):
    tracker = EventTracker(TemporalConfig(**plan['temporal_config']))
    states = [tracker.update(p['timestamp_s'], p['detections'], p['frame_size']) for p in packets]
    if log_path:
        with log_path.open('w', encoding='utf-8') as output:
            for state in states:
                output.write(json.dumps(state) + '\n')
    return evaluate_video(row, packets, states, plan['episode_merge_gap_s'])


def verified_run(folder):
    manifest = read(folder / 'run-manifest.json')
    if (manifest['status'] != 'completed' or digest(folder / 'results.json') != manifest['results_sha256'] or
            digest(folder / 'proposal.json') != manifest['proposal_sha256']):
        raise ValueError('Prior completed experiment failed integrity check: ' + str(folder))
    return read(folder / 'results.json'), read(folder / 'proposal.json')


def indices(folder):
    return {read(path / 'case.json')['filename']: path for path in folder.glob('case-*')}


def image_diagnostic(folder, plan):
    import cv2
    import numpy as np
    old, proposal = verified_run(IMAGE_RUN)
    if proposal['box_annotation_sha256'] != digest(folder / 'box-annotations-snapshot.json'):
        raise ValueError('Image annotations differ from previous comparison.')
    cached = {case['image']: case for case in old['cases']}
    entries = {entry['filename']: entry for entry in plan['entries']}
    cases = []
    for number, row in enumerate(read(folder / 'box-annotations-snapshot.json')['images'], 1):
        if not training_eligible(row) or entries[row['source']]['split'] != 'development':
            raise ValueError('Only completed development images allowed.')
        path = ROOT / 'data/firesense/box-review-pilot-v1' / row['image']
        if digest(path) != row['image_sha256']:
            raise ValueError('Annotated image changed.')
        reference, candidate = [cached[row['image']]['predictions'][name] for name in ('reference', 'candidate')]
        header = {'frame_index': row['frame_index'], 'timestamp_s': row['timestamp_s'],
                  'frame_size': [row['width'], row['height']]}
        routed = route_packet({**header, 'detections': reference}, {**header, 'detections': candidate})['detections']
        original = filtered({'detections': reference}, .35)['detections']
        cases.append({'image': row['image'], 'source': row['source'], 'frame_index': row['frame_index'],
                      'reference': original, 'routed': routed,
                      'reference_counts': count_boxes(row['boxes'], original), 'routed_counts': count_boxes(row['boxes'], routed),
                      'small_reference': small_matches(row, original), 'small_routed': small_matches(row, routed)})
        frame = cv2.imread(str(path))
        cv2.imwrite(str(folder / f'image-{number:02d}.png'), np.hstack([
            panel(frame, row['boxes'], 'Human annotation', truth=True),
            panel(frame, original, 'Original conf=0.35'), panel(frame, routed, 'Fire .50 + smoke .35')]))
    result = {'aggregate': {name: sum_counts([case[name + '_counts'] for case in cases]) for name in ('reference', 'routed')},
              'small_fire': {name: {key: sum(case['small_' + name][key] for case in cases) for key in ('truth', 'matched', 'missed')} for name in ('reference', 'routed')},
              'cases': cases, 'note': 'Cached development diagnostic, not new independent images; IoU>=0.5.'}
    write(folder / 'image-results.json', result)
    return {key: result[key] for key in ('aggregate', 'small_fire')}


def run(folder, plan, review, stage, detectors=None):
    import cv2
    cached_result, cached_proposal = verified_run(CANDIDATE_RUN)
    if (cached_proposal['annotation_sha256'] != plan['review_sha256'] or
            cached_proposal['weights_sha256'] != plan['weights_sha256'] or
            cached_proposal['candidate_weights_sha256'] != CANDIDATES['uav']['sha256']):
        raise ValueError('Previous comparison inputs differ.')
    entries = [entry for entry in plan['entries'] if entry['split'] == 'development']
    rows = {row['filename']: row for row in review['videos']}
    original_index, candidate_index = indices(BASELINE), indices(CANDIDATE_RUN)
    expected = {case['filename']: case for case in cached_result['cases']['candidate']['0.5']}
    reference_cases = [case for case in read(BASELINE / 'results.json')['cases'] if case['split'] == 'development']
    original_metrics = {case['filename']: case['metrics'] for case in reference_cases}
    cases, hashes, timing = [], {}, {'reference_s': 0., 'candidate_s': 0., 'route_s': 0., 'tracker_s': 0.}
    started = time.perf_counter()
    for number, entry in enumerate(entries, 1):
        source = ROOT / 'videos' / entry['filename']
        if digest(source) != entry['source_sha256']:
            raise ValueError('Source changed.')
        original_dir, candidate_dir = original_index[entry['filename']], candidate_index[entry['filename']]
        reference_path, candidate_path = original_dir / 'detections.jsonl', candidate_dir / 'low-confidence-detections.jsonl'
        if (digest(reference_path) != read(original_dir / 'case.json')['log_sha256']['detections.jsonl'] or
                digest(candidate_path) != read(candidate_dir / 'case.json')['log_sha256']['candidate_raw']):
            raise ValueError('Input detection log changed.')
        reference_packets, candidate_packets = load_packets(reference_path), load_packets(candidate_path)
        if len(reference_packets) != entry['decoded_frames'] or len(candidate_packets) != entry['decoded_frames']:
            raise ValueError('Incomplete prior logs.')
        case_dir = folder / f'case-{number:02d}-{source.stem}'
        case_dir.mkdir()
        print(f'BEGIN {number}/{len(entries)} {entry["filename"]}', flush=True)
        packets, states = [], []
        tracker = EventTracker(TemporalConfig(**plan['temporal_config']))
        capture = cv2.VideoCapture(str(source)) if stage == 'live' else None
        before_case = time.perf_counter()
        try:
            with (case_dir / 'detections.jsonl').open('w', encoding='utf-8') as log, (case_dir / 'temporal.jsonl').open('w', encoding='utf-8') as state_log:
                for frame_index, (reference_packet, candidate_packet) in enumerate(zip(reference_packets, candidate_packets)):
                    cached_packet = route_packet(reference_packet, candidate_packet)
                    if stage == 'live':
                        ok, frame = capture.read()
                        if not ok or [frame.shape[1], frame.shape[0]] != reference_packet['frame_size']:
                            raise ValueError('Live decoding differs from cache.')
                        start = time.perf_counter()
                        reference = detectors[0].predict(frame, annotate=False)[1]
                        timing['reference_s'] += time.perf_counter() - start
                        start = time.perf_counter()
                        candidate = detectors[1].predict(frame, annotate=False)[1]
                        timing['candidate_s'] += time.perf_counter() - start
                        start = time.perf_counter()
                        packet = route_packet({**reference_packet, 'detections': reference}, {**candidate_packet, 'detections': candidate})
                        timing['route_s'] += time.perf_counter() - start
                        if packet != cached_packet:
                            raise ValueError(f'Live routed prediction differs from replay: {entry["filename"]} frame {frame_index}')
                    else:
                        packet = cached_packet
                    start = time.perf_counter()
                    state = tracker.update(packet['timestamp_s'], packet['detections'], packet['frame_size'])
                    timing['tracker_s'] += time.perf_counter() - start
                    packets.append(packet)
                    states.append(state)
                    log.write(json.dumps(packet) + '\n')
                    state_log.write(json.dumps(state) + '\n')
                    if stage == 'live' and (frame_index + 1) % 1000 == 0:
                        print(f'PROGRESS {number}/{len(entries)} {frame_index+1}/{entry["decoded_frames"]}', flush=True)
                if capture is not None and capture.read()[0]:
                    raise ValueError('Live stream has extra frames.')
        finally:
            if capture is not None:
                capture.release()
        metrics = evaluate_video(rows[entry['filename']], packets, states, plan['episode_merge_gap_s'])
        if replay_one(rows[entry['filename']], reference_packets, plan) != original_metrics[entry['filename']]:
            raise ValueError('Reference metrics do not reproduce frozen baseline.')
        if metrics['smoke'] != original_metrics[entry['filename']]['smoke'] or metrics['fire'] != expected[entry['filename']]['metrics']['fire']:
            raise ValueError('Class routing changed within-class event behavior.')
        case_hashes = {name: digest(case_dir / name) for name in ('detections.jsonl', 'temporal.jsonl')}
        case_hashes.update({'reference_input': digest(reference_path), 'candidate_input': digest(candidate_path)})
        hashes[case_dir.name] = case_hashes
        cases.append({**entry, 'metrics': metrics})
        write(case_dir / 'case.json', {**entry, 'metrics': metrics, 'log_sha256': case_hashes,
              'reference_input_path': str(reference_path.relative_to(ROOT)), 'candidate_input_path': str(candidate_path.relative_to(ROOT)),
              'elapsed_s': time.perf_counter() - before_case})
        write(folder / 'progress.json', {'completed': number, 'total': len(entries)})
        print(f'END {number}/{len(entries)}', flush=True)
    totals = {name: {label: {mode: aggregate(values, 'development', label, mode) for mode in ('single_frame', 'temporal')} for label in ('fire', 'smoke')} for name, values in (('reference', reference_cases), ('routed', cases))}
    frames = sum(case['decoded_frames'] for case in cases)
    timing['scope'] = 'Warm sequential GPU calls and routing; tracker separately. Decode, writing, reference replay and UI excluded from prediction FPS.'
    if stage == 'live':
        prediction_s = timing['reference_s'] + timing['candidate_s'] + timing['route_s']
        timing['reference_prediction_fps'] = frames / timing['reference_s']
        timing['routed_prediction_fps'] = frames / prediction_s
        timing['prediction_cost_ratio'] = prediction_s / timing['reference_s']
    result = {'stage': stage, 'aggregate': totals, 'gates': gates(reference_cases, cases),
              'processed_frames': frames, 'cases': cases, 'log_sha256': hashes,
              'timing': timing, 'elapsed_s': time.perf_counter() - started,
              'per_video_branch_metrics_identical': True, 'live_packets_identical_to_replay': stage == 'live',
              'adopted': False, 'decision': 'Development feasibility only; pending human annotation boundary review and frozen validation.',
              'selection_note': 'Routing chosen after inspecting previous development results; this replay is a consistency check, not new generalization evidence.'}
    if stage == 'replay':
        result['image_diagnostic'] = image_diagnostic(folder, plan)
    write(folder / 'results.json', result)
    return {key: result[key] for key in ('stage', 'processed_frames', 'aggregate', 'gates', 'timing', 'decision')}


def main():
    import numpy as np
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=('replay', 'live'), required=True)
    parser.add_argument('--replay-run')
    args = parser.parse_args()
    if args.stage == 'live':
        previous = Path(args.replay_run or '').resolve()
        if (ROOT / 'outputs').resolve() not in previous.parents:
            raise ValueError('Supply completed project replay run.')
        previous_result, previous_proposal = verified_run(previous)
        if not previous_result['gates']['passed'] or previous_proposal['config'] != {'fire_threshold': FIRE_THRESHOLD, 'smoke_threshold': SMOKE_THRESHOLD}:
            raise ValueError('Replay must pass unchanged fixed configuration first.')
    source_path = candidate_folder('uav') / 'source-manifest.json'
    source = read(source_path)
    weight = candidate_folder('uav') / source['filename']
    if digest(weight) != CANDIDATES['uav']['sha256']:
        raise ValueError('Candidate weight changed.')
    extra = {'policy': 'Only candidate fire>=0.50 plus only original smoke>=0.35; normalize IDs smoke=0/fire=1; original temporal config; no new NMS or cross-class suppression.',
             'config': {'fire_threshold': FIRE_THRESHOLD, 'smoke_threshold': SMOKE_THRESHOLD},
             'scope': 'Same 30 development videos, no holdout inference or label changes. Routing proposed after prior development results; not independent validation.',
             'stage': args.stage, 'candidate_weights_sha256': source['sha256'], 'candidate_source': source,
             'candidate_source_manifest_sha256': digest(source_path), 'candidate_run': str(CANDIDATE_RUN.relative_to(ROOT)),
             'candidate_results_sha256': digest(CANDIDATE_RUN / 'results.json'),
             'image_results_sha256': digest(IMAGE_RUN / 'results.json'), 'replay_run': args.replay_run,
             'prior_art': [], 'crop_cut_edge_policy': None,
             'originality_note': 'Fixed class-specific routing of existing models is an engineering control; no original neural architecture claim.',
             'live_check': 'Direct original .35 and candidate .50 predictions; all routed packets must exactly reproduce low-threshold cached routing. Fail and report discrepancies rather than silently retune.',
             'video_adoption_gate': 'Recover >=1 fire event; lose no former event; fire false episodes<=3, smoke<=1; correctness and label review required before adoption.'}
    folder, plan, proposal, review = prepare('class-route-' + args.stage, extra,
        ('experiments/probe_class_routing.py', 'experiments/screen_hf_fire_candidates.py', 'experiments/fetch_hf_fire_candidates.py'))
    try:
        detectors = None
        if args.stage == 'live':
            detectors = (Detector(ROOT / 'models/best.pt', confidence=.35), Detector(weight, confidence=.50))
            for detector in detectors:
                for _ in range(3):
                    detector.predict(np.zeros((640, 640, 3), dtype=np.uint8), annotate=False)
        summary = run(folder, plan, review, args.stage, detectors)
        if digest(ROOT / 'models/best.pt') != plan['weights_sha256'] or digest(weight) != source['sha256']:
            raise ValueError('Weights changed.')
        if any(digest(ROOT / path) != sha for path, sha in proposal['code_sha256'].items()):
            raise ValueError('Experiment code changed during run.')
        write(folder / 'run-manifest.json', {'status': 'completed', 'proposal_sha256': digest(folder / 'proposal.json'),
              'results_sha256': digest(folder / 'results.json'), 'completed_at_utc': datetime.now(timezone.utc).isoformat(),
              'device': detectors[0].device if detectors else 'cached logs; no inference'})
        print(json.dumps({'output': str(folder), **summary}, ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'proposal_sha256': digest(folder / 'proposal.json'), 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
