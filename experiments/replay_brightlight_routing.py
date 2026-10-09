"""Exploratory development replay after observing the whole-model smoke failures."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.benchmark_firesense import aggregate, digest, read, write
from experiments.probe_local_fire import BASELINE, prepare
from experiments.probe_class_routing import indices, replay_one, verified_run
from experiments.screen_hf_fire_candidates import filtered, gates, load_packets


def route(reference, candidate):
    if (reference['frame_index'] != candidate['frame_index'] or
            abs(reference['timestamp_s'] - candidate['timestamp_s']) > 1e-5 or
            reference['frame_size'] != candidate['frame_size']):
        raise ValueError('Cannot combine different frames.')
    boxes = []
    for packet, label in ((candidate, 'fire'), (reference, 'smoke')):
        for box in packet['detections']:
            if box['label'] == label and box['confidence'] >= .35:
                boxes.append({**box, 'class_id': {'smoke': 0, 'fire': 1}[label],
                              'bbox_xyxy': list(box['bbox_xyxy'])})
    return {**reference, 'detections': boxes}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate-run', required=True)
    args = parser.parse_args()
    candidate_folder = Path(args.candidate_run).resolve()
    if (ROOT / 'outputs').resolve() not in candidate_folder.parents:
        raise ValueError('Candidate run must be inside outputs.')
    prior, prior_proposal = verified_run(candidate_folder)
    folder, plan, proposal, review = prepare('brightlight-route-replay', {
        'scope': 'Exploratory development log replay only, proposed after full candidate smoke failures; no independent test, live dual-model inference, holdout, training or app changes.',
        'policy': 'Fixed candidate fire >=0.35 plus original smoke >=0.35. No other veto. Unchanged tracker.',
        'config': {'fire_confidence': .35, 'smoke_confidence': .35},
        'crop_cut_edge_policy': None, 'prior_art': [],
        'originality_note': 'Third-party model routing is an engineering combination, not a novel network.',
        'candidate_run': str(candidate_folder.relative_to(ROOT)),
        'candidate_results_sha256': digest(candidate_folder / 'results.json'),
        'candidate_weights_sha256': prior_proposal['candidate_weights_sha256'],
        'video_adoption_gate': 'Recover >=1 old missed fire, lose no old event, strictly reduce confirmed fire false episodes below3 and preserve original smoke metrics. Development only; continuity/latency tradeoffs reported separately.'
    }, ('experiments/replay_brightlight_routing.py', 'experiments/screen_brightlight_candidate.py',
        'experiments/probe_class_routing.py', 'experiments/screen_hf_fire_candidates.py',
        'experiments/fetch_hf_fire_candidates.py', 'experiments/benchmark_firesense.py'))
    try:
        if (prior_proposal['plan_sha256'] != proposal['plan_sha256'] or
                prior_proposal['annotation_sha256'] != plan['review_sha256']):
            raise ValueError('Candidate uses different evaluation inputs.')
        rows = {r['filename']: r for r in review['videos']}
        original_index, candidate_index = indices(BASELINE), indices(candidate_folder)
        original_cases = [c for c in read(BASELINE / 'results.json')['cases'] if c['split'] == 'development']
        expected = {c['filename']: c for c in prior['cases']['candidate']['0.35']}
        reference = {c['filename']: c for c in original_cases}
        cases = []
        for number, entry in enumerate((e for e in plan['entries'] if e['split'] == 'development'), 1):
            old_dir, new_dir = original_index[entry['filename']], candidate_index[entry['filename']]
            old_path, new_path = old_dir / 'detections.jsonl', new_dir / 'low-confidence-detections.jsonl'
            if (digest(old_path) != reference[entry['filename']]['log_sha256']['detections.jsonl'] or
                    digest(new_path) != read(new_dir / 'case.json')['log_sha256']['candidate_raw'] or
                    digest(ROOT / 'videos' / entry['filename']) != entry['source_sha256']):
                raise ValueError('Input log/video changed.')
            old, new = load_packets(old_path), load_packets(new_path)
            if len(old) != entry['decoded_frames'] or len(new) != entry['decoded_frames']:
                raise ValueError('Incomplete input logs.')
            routed = [route(a, b) for a, b in zip(old, new)]
            case_folder = folder / f'case-{number:02d}-{Path(entry["filename"]).stem}'
            case_folder.mkdir()
            detection_path = case_folder / 'detections.jsonl'
            with detection_path.open('w', encoding='utf-8') as stream:
                for packet in routed:
                    stream.write(json.dumps(packet) + '\n')
            metrics = replay_one(rows[entry['filename']], routed, plan, case_folder / 'temporal.jsonl')
            if (metrics['fire'] != expected[entry['filename']]['metrics']['fire'] or
                    metrics['smoke'] != reference[entry['filename']]['metrics']['smoke']):
                raise ValueError('Branch replay failed to preserve expected metrics.')
            case = {**entry, 'metrics': metrics, 'input_log_sha256': {'reference': digest(old_path), 'candidate': digest(new_path)},
                    'log_sha256': {name: digest(case_folder / name) for name in ('detections.jsonl', 'temporal.jsonl')}}
            write(case_folder / 'case.json', case)
            cases.append(case)
        checks = gates(original_cases, cases)
        checks['checks']['strict_fire_false_reduction'] = aggregate(cases, 'development', 'fire', 'temporal')['false_hint_episodes'] < 3
        checks['passed'] = all(checks['checks'].values())
        result = {'stage': 'exploratory_replay', 'cases': cases, 'processed_frames': sum(c['decoded_frames'] for c in cases),
                  'aggregate': {label: {mode: aggregate(cases, 'development', label, mode) for mode in ('single_frame', 'temporal')} for label in ('fire', 'smoke')},
                  'gates': checks, 'adopted': False,
                  'decision': 'Retain as development candidate; new-source and live integration needed' if checks['passed'] else 'Replay gate failed; do not adopt',
                  'limitation': 'Chosen after seeing candidate smoke failures. Replay verifies branch compatibility, not independent generalization or live two-model speed. Event hit alone does not guarantee continuous/correct target localization.'}
        write(folder / 'results.json', result)
        if digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
            raise ValueError('Default weight changed.')
        for name, expected_sha in proposal['code_sha256'].items():
            if digest(ROOT / name) != expected_sha:
                raise ValueError('Code changed during replay.')
        write(folder / 'run-manifest.json', {'status': 'completed', 'proposal_sha256': digest(folder / 'proposal.json'),
              'results_sha256': digest(folder / 'results.json')})
        print(json.dumps({'output': str(folder), 'aggregate': result['aggregate'], 'gates': checks}, ensure_ascii=False, indent=2))
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'error': str(exc)})
        raise


if __name__ == '__main__':
    main()
