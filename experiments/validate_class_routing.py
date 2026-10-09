"""One frozen internal holdout check; no threshold selection on holdout results."""
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
from core.detector import Detector
from core.evaluation import evaluate_video
from core.temporal import EventTracker, TemporalConfig
from experiments.benchmark_firesense import aggregate, digest, read, write
from experiments.fetch_hf_fire_candidates import candidate_folder, CANDIDATES
from experiments.probe_class_routing import (route_packet, verified_run, indices,
                                           FIRE_THRESHOLD, SMOKE_THRESHOLD)
from experiments.probe_local_fire import BASELINE, prepare, panel
from experiments.screen_hf_fire_candidates import load_packets


def main():
    import cv2
    import numpy as np
    parser = argparse.ArgumentParser()
    parser.add_argument('--development-run', required=True)
    args = parser.parse_args()
    development = Path(args.development_run).resolve()
    if (ROOT / 'outputs').resolve() not in development.parents:
        raise ValueError('Development run must be inside project outputs.')
    dev_result, dev_proposal = verified_run(development)
    if (dev_result['stage'] != 'live' or not dev_result['gates']['passed'] or
            not dev_result['live_packets_identical_to_replay'] or
            dev_proposal['config'] != {'fire_threshold': FIRE_THRESHOLD, 'smoke_threshold': SMOKE_THRESHOLD}):
        raise ValueError('Require completed live development check at the frozen configuration.')
    review_note = ROOT / 'data/firesense/boundary-review-20261009.json'
    sensitivity_path = ROOT / 'outputs/local-fire-class-route-replay-20261009-195102-390832/boundary-sensitivity.json'
    note, sensitivity = read(review_note), read(sensitivity_path)
    if note['user_statement'] != '176大概':
        raise ValueError('Review note changed; reassess before validation.')
    for mode in ('reference', 'routed'):
        for key in ('detected_scene_events', 'missed_scene_events', 'false_hint_episodes'):
            if sensitivity['checks']['176'][mode]['temporal'][key] != sensitivity['checks']['177'][mode]['temporal'][key]:
                raise ValueError('Development conclusion sensitive to unresolved label boundary.')
    source_path = candidate_folder('uav') / 'source-manifest.json'
    source = read(source_path)
    weight = candidate_folder('uav') / source['filename']
    if digest(weight) != CANDIDATES['uav']['sha256']:
        raise ValueError('Candidate weight changed.')
    extra = {'scope': 'One frozen check on original 14 internal holdout videos only; earlier baseline exposure and unverified source/training overlap mean not independent external testing.',
             'policy': 'Candidate fire>=.50, original smoke>=.35, same 640 input and original temporal config; no further tuning on holdout.',
             'config': {'fire_threshold': FIRE_THRESHOLD, 'smoke_threshold': SMOKE_THRESHOLD},
             'candidate_source': source, 'candidate_weights_sha256': source['sha256'],
             'development_run': str(development.relative_to(ROOT)),
             'development_results_sha256': digest(development / 'results.json'),
             'human_boundary_review_sha256': digest(review_note), 'boundary_sensitivity_sha256': digest(sensitivity_path),
             'pending_label_review': 'User reports approximately frame176 in development testpos06; original frozen labels retained; 176/177 sensitivity does not change event conclusions. Not exact revised ground truth.',
             'prior_art': [], 'crop_cut_edge_policy': None,
             'video_adoption_gate': 'Internal check: lose no original held event in either class and increase no held false-episode count in either class. Any failure is reported without retuning.',
             'originality_note': 'Fixed routing of two existing detectors is an engineering option, not an original neural architecture.'}
    folder, plan, proposal, review = prepare('class-route-holdout', extra,
        ('experiments/validate_class_routing.py', 'experiments/probe_class_routing.py',
         'experiments/screen_hf_fire_candidates.py', 'experiments/fetch_hf_fire_candidates.py'))
    frozen = {'frozen_at_utc': datetime.now(timezone.utc).isoformat(), 'config': extra['config'],
              'reference_weights_sha256': plan['weights_sha256'], 'candidate_weights_sha256': source['sha256'],
              'input_size': 640, 'temporal_config': plan['temporal_config'],
              'plan_sha256': proposal['plan_sha256'], 'annotation_sha256': plan['review_sha256'],
              'development_results_sha256': extra['development_results_sha256'],
              'validation_gate': extra['video_adoption_gate'], 'no_retuning_on_this_holdout': True}
    write(folder / 'frozen-candidate.json', frozen)
    try:
        reference, candidate = Detector(ROOT / 'models/best.pt', confidence=.35), Detector(weight, confidence=.50)
        for model in (reference, candidate):
            for _ in range(3):
                model.predict(np.zeros((640, 640, 3), dtype=np.uint8), annotate=False)
        entries = [entry for entry in plan['entries'] if entry['split'] == 'internal_holdout']
        if len(entries) != 14:
            raise ValueError('Unexpected validation cohort.')
        rows = {row['filename']: row for row in review['videos']}
        baseline_index = indices(BASELINE)
        expected = {case['filename']: case for case in read(BASELINE / 'results.json')['cases']}
        cases = {'reference': [], 'routed': []}
        changes, hashes = [], {}
        started = time.perf_counter()
        for number, entry in enumerate(entries, 1):
            path = ROOT / 'videos' / entry['filename']
            if digest(path) != entry['source_sha256']:
                raise ValueError('Validation source changed.')
            baseline_log = baseline_index[entry['filename']] / 'detections.jsonl'
            if digest(baseline_log) != expected[entry['filename']]['log_sha256']['detections.jsonl']:
                raise ValueError('Validation baseline log changed.')
            old_packets = load_packets(baseline_log)
            case_dir = folder / f'case-{number:02d}-{path.stem}'
            case_dir.mkdir()
            packets, states = {name: [] for name in cases}, {name: [] for name in cases}
            trackers = {name: EventTracker(TemporalConfig(**plan['temporal_config'])) for name in cases}
            streams = {name: (case_dir / name).open('w', encoding='utf-8') for name in
                       ('reference-detections.jsonl', 'routed-detections.jsonl', 'routed-temporal.jsonl')}
            capture = cv2.VideoCapture(str(path))
            count = 0
            print(f'BEGIN {number}/14 {entry["filename"]}', flush=True)
            try:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    header = {'frame_index': count, 'timestamp_s': round(count / entry['fps'], 6),
                              'frame_size': [frame.shape[1], frame.shape[0]]}
                    old = {**header, 'detections': reference.predict(frame, annotate=False)[1]}
                    new = {**header, 'detections': candidate.predict(frame, annotate=False)[1]}
                    if count >= len(old_packets) or old != old_packets[count]:
                        raise ValueError('Fresh reference validation output differs from frozen baseline.')
                    routed = route_packet(old, new)
                    for name, packet in (('reference', old), ('routed', routed)):
                        state = trackers[name].update(packet['timestamp_s'], packet['detections'], packet['frame_size'])
                        packets[name].append(packet)
                        states[name].append(state)
                        streams[name + '-detections.jsonl'].write(json.dumps(packet) + '\n')
                        if name == 'routed':
                            streams['routed-temporal.jsonl'].write(json.dumps(state) + '\n')
                    count += 1
                    if count % 1000 == 0:
                        print(f'PROGRESS {number}/14 {count}/{entry["decoded_frames"]}', flush=True)
            finally:
                capture.release()
                for stream in streams.values():
                    stream.close()
            if count != entry['decoded_frames']:
                raise ValueError('Incomplete validation decoding.')
            metrics = {name: evaluate_video(rows[entry['filename']], packets[name], states[name], plan['episode_merge_gap_s']) for name in cases}
            if metrics['reference'] != expected[entry['filename']]['metrics']:
                raise ValueError('Fresh validation reference metrics changed.')
            for label in ('fire', 'smoke'):
                if metrics['reference'][label]['excluded']:
                    continue
                before, after = metrics['reference'][label]['temporal'], metrics['routed'][label]['temporal']
                old_misses, new_misses = before['missed_intervals_frames'], after['missed_intervals_frames']
                recovered = [item for item in old_misses if item not in new_misses]
                lost = [item for item in new_misses if item not in old_misses]
                if recovered or lost or before['false_hint_episodes'] != after['false_hint_episodes']:
                    changes.append({'filename': entry['filename'], 'label': label, 'recovered': recovered,
                                    'lost': lost, 'false_before': before['false_hint_episodes'], 'false_after': after['false_hint_episodes']})
                    sample = (recovered or lost or after['false_hint_intervals_frames'])[0]
                    indices_to_review = [sample[0], (sample[0] + sample[1] - 1)//2, sample[1]-1]
                    cap = cv2.VideoCapture(str(path))
                    panels = []
                    for index in indices_to_review:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
                        ok, frame = cap.read()
                        if ok:
                            panels.append(np.hstack([panel(frame, packets['reference'][index]['detections'], f'Original frame {index}'),
                                                     panel(frame, packets['routed'][index]['detections'], f'Routed frame {index}')]))
                    cap.release()
                    if panels:
                        cv2.imwrite(str(case_dir / ('qa-' + label + '.png')), np.vstack(panels))
            for name in cases:
                cases[name].append({**entry, 'metrics': metrics[name]})
            log_hashes = {name: digest(case_dir / name) for name in streams}
            log_hashes['baseline_input'] = digest(baseline_log)
            hashes[case_dir.name] = log_hashes
            write(case_dir / 'case.json', {**entry, 'metrics': metrics, 'log_sha256': log_hashes,
                  'baseline_input_path': str(baseline_log.relative_to(ROOT))})
            write(folder / 'progress.json', {'completed': number, 'total': 14})
            print(f'END {number}/14', flush=True)
        totals = {name: {label: {mode: aggregate(values, 'internal_holdout', label, mode) for mode in ('single_frame', 'temporal')} for label in ('fire', 'smoke')} for name, values in cases.items()}
        checks = {'no_lost_original_events': not any(item['lost'] for item in changes),
                  **{label + '_false_not_increased': totals['routed'][label]['temporal']['false_hint_episodes'] <= totals['reference'][label]['temporal']['false_hint_episodes'] for label in ('fire', 'smoke')}}
        result = {'aggregate': totals, 'cases': cases, 'changes': changes, 'checks': checks,
                  'numeric_check_passed': all(checks.values()), 'processed_frames': sum(entry['decoded_frames'] for entry in entries),
                  'elapsed_s': time.perf_counter() - started, 'adopted': False,
                  'frozen_candidate_sha256': digest(folder / 'frozen-candidate.json'), 'log_sha256': hashes,
                  'decision': 'Internal check passed; not external generalization or deployment proof' if all(checks.values()) else 'Rejected by frozen internal check; no retuning on holdout'}
        write(folder / 'results.json', result)
        if digest(ROOT / 'models/best.pt') != plan['weights_sha256'] or digest(weight) != source['sha256']:
            raise ValueError('Weights changed during validation.')
        if any(digest(ROOT / path) != sha for path, sha in proposal['code_sha256'].items()):
            raise ValueError('Code changed during validation.')
        write(folder / 'run-manifest.json', {'status': 'completed', 'proposal_sha256': digest(folder / 'proposal.json'),
              'results_sha256': digest(folder / 'results.json'), 'completed_at_utc': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'output': str(folder), 'aggregate': totals, 'checks': checks, 'changes': changes,
                          'decision': result['decision']}, ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'proposal_sha256': digest(folder / 'proposal.json'), 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
