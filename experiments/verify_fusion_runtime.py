"""Verify the production fusion adapter against the frozen development replay."""
import json
import sys
import time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.model_profiles import create_detector
from core.evaluation import evaluate_video
from core.temporal import EventTracker, TemporalConfig
from experiments.benchmark_firesense import aggregate, digest, read, write
from experiments.probe_class_routing import indices, verified_run
from experiments.probe_local_fire import prepare
from experiments.screen_hf_fire_candidates import load_packets

REPLAY = ROOT / 'outputs/local-fire-brightlight-route-replay-20261009-211138-531239'


def main():
    import cv2
    import numpy as np
    previous, old_proposal = verified_run(REPLAY)
    folder, plan, proposal, review = prepare('brightlight-runtime', {
        'scope': 'Actual production fusion adapter on all 30 development videos; no holdout, training or default change.',
        'policy': 'Production fire candidate .35 + original smoke .35; compare every packet and per-video metric with verified replay.',
        'config': {'fire_threshold': .35, 'smoke_threshold': .35}, 'prior_art': [], 'crop_cut_edge_policy': None,
        'candidate_weights_sha256': old_proposal['candidate_weights_sha256'],
        'originality_note': 'Runtime integration of third-party class routing, not an original model.',
        'video_adoption_gate': 'Exact replay reproduction is required for optional mode; default replacement additionally requires acceptable coverage/delay and new-source validation.'
    }, ('experiments/verify_fusion_runtime.py', 'core/model_profiles.py', 'core/pipeline.py',
        'ui/window.py', 'ui/worker.py', 'main.py', 'experiments/probe_class_routing.py',
        'experiments/screen_hf_fire_candidates.py', 'experiments/fetch_hf_fire_candidates.py',
        'experiments/benchmark_firesense.py'))
    try:
        detector = create_detector(ROOT / 'models/best.pt', profile='brightlight')
        for _ in range(3):
            detector.predict(np.zeros((640, 640, 3), dtype=np.uint8), annotate=False)
        write(folder / 'model-manifest.json', detector.model_manifest)
        expected = {c['filename']: c for c in previous['cases']}
        old_index = indices(REPLAY)
        rows = {r['filename']: r for r in review['videos']}
        cases = []
        total_call_s = 0.
        total_s = time.perf_counter()
        entries = [e for e in plan['entries'] if e['split'] == 'development']
        for number, entry in enumerate(entries, 1):
            source = ROOT / 'videos' / entry['filename']
            if digest(source) != entry['source_sha256']:
                raise ValueError('Source video changed.')
            old_log = old_index[entry['filename']] / 'detections.jsonl'
            if digest(old_log) != expected[entry['filename']]['log_sha256']['detections.jsonl']:
                raise ValueError('Expected replay changed.')
            old_packets = load_packets(old_log)
            tracker = EventTracker(TemporalConfig(**plan['temporal_config']))
            case_dir = folder / f'case-{number:02d}-{source.stem}'
            case_dir.mkdir()
            cap = cv2.VideoCapture(str(source))
            if not cap.isOpened() or abs(cap.get(cv2.CAP_PROP_FPS)-entry['fps']) > 1e-6:
                raise ValueError('Video decoder/fps mismatch.')
            packets, states = [], []
            call_s = 0.
            print(f'BEGIN {number}/30 {source.name}', flush=True)
            try:
                with (case_dir / 'detections.jsonl').open('w', encoding='utf-8') as log, (case_dir / 'temporal.jsonl').open('w', encoding='utf-8') as state_log:
                    while True:
                        ok, frame = cap.read()
                        if not ok:
                            break
                        index = len(packets)
                        start = time.perf_counter()
                        _, detections = detector.predict(frame, annotate=False)
                        call_s += time.perf_counter()-start
                        packet = {'frame_index': index, 'timestamp_s': round(index/entry['fps'], 6),
                                  'frame_size': [frame.shape[1], frame.shape[0]], 'detections': detections}
                        if index >= len(old_packets) or packet != old_packets[index]:
                            write(case_dir / 'mismatch.json', {'actual': packet, 'expected': old_packets[index] if index < len(old_packets) else None})
                            raise ValueError('Production fusion differs from replay: ' + entry['filename'] + f' frame{index}')
                        state = tracker.update(packet['timestamp_s'], detections, packet['frame_size'])
                        packets.append(packet)
                        states.append(state)
                        log.write(json.dumps(packet)+'\n')
                        state_log.write(json.dumps(state)+'\n')
            finally:
                cap.release()
            if len(packets) != entry['decoded_frames']:
                raise ValueError('Incomplete video.')
            metrics = evaluate_video(rows[entry['filename']], packets, states, plan['episode_merge_gap_s'])
            if metrics != expected[entry['filename']]['metrics']:
                raise ValueError('Production metrics differ from replay.')
            case = {**entry, 'metrics': metrics, 'prediction_call_s': call_s,
                    'log_sha256': {n: digest(case_dir/n) for n in ('detections.jsonl', 'temporal.jsonl')}}
            write(case_dir / 'case.json', case)
            cases.append(case)
            total_call_s += call_s
            write(folder / 'progress.json', {'completed': number, 'total': 30})
            print(f'END {number}/30 frames={len(packets)}', flush=True)
        result = {'aggregate': {l: {m: aggregate(cases, 'development', l, m) for m in ('single_frame', 'temporal')} for l in ('fire', 'smoke')},
                  'cases': cases, 'all_packets_identical_to_replay': True, 'all_metrics_identical_to_replay': True,
                  'processed_frames': sum(c['decoded_frames'] for c in cases), 'prediction_call_s': total_call_s,
                  'prediction_fps': sum(c['decoded_frames'] for c in cases)/total_call_s,
                  'elapsed_s': time.perf_counter()-total_s, 'default_replaced': False,
                  'timing_note': 'Warm sequential two-model prediction adapter calls; includes transfer/postprocess; excludes decode/drawing/UI/export and tracker.'}
        write(folder / 'results.json', result)
        for name, expected_sha in proposal['code_sha256'].items():
            if digest(ROOT/name) != expected_sha:
                raise ValueError('Runtime code changed during verification.')
        write(folder / 'run-manifest.json', {'status': 'completed', 'proposal_sha256': digest(folder/'proposal.json'),
              'results_sha256': digest(folder/'results.json')})
        print(json.dumps({'output': str(folder), 'prediction_fps': result['prediction_fps'], 'exact_replay_reproduction': True}, ensure_ascii=False))
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
