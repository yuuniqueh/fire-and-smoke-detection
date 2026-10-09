"""Limited developmental replay: separate spatial association and confirmation time."""
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.temporal import EventTracker, TemporalConfig
from core.evaluation import evaluate_video
from experiments.benchmark_firesense import aggregate, digest, read, write

INPUT = ROOT / 'outputs/confidence-probe-20261006-205455-969393'
PROFILES = {
    'reference': {},
    'association_only': {'match_iou': .05},
    'confirmation_only': {'min_observation_s': .5, 'min_positive_s': .3, 'min_presence_ratio': .5},
    'association_and_confirmation': {'match_iou': .05, 'min_observation_s': .5,
                                     'min_positive_s': .3, 'min_presence_ratio': .5},
}


def main():
    import json
    original = read(INPUT / 'results.json')
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    if digest(plan_path) != original['proposal']['plan_sha256']:
        raise ValueError('Plan changed.')
    snapshot_path = INPUT / 'review-snapshot.json'
    if digest(snapshot_path) != plan['review_sha256']:
        raise ValueError('Annotation changed.')
    for name, expected in plan['code_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Frozen implementation changed: {name}')
    rows = {row['filename']: row for row in read(snapshot_path)['videos']}
    entries = [entry for entry in plan['entries'] if entry['split'] == 'development']
    folder = ROOT / 'outputs' / datetime.now().strftime('temporal-probe-%Y%m%d-%H%M%S-%f')
    folder.mkdir()
    configs = {key: {**plan['temporal_config'], **changes} for key, changes in PROFILES.items()}
    proposal = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'scope': 'Development only',
                'confidence': .35, 'profiles_predeclared': configs, 'plan_sha256': digest(plan_path),
                'runner_sha256': digest(Path(__file__)),
                'criterion': original['proposal']['criterion'],
                'rationale': 'Separate association fragmentation from strict confirmation. Positive time remains >=0.3 seconds; no single-frame confirmation.',
                'input': str(INPUT.relative_to(ROOT)), 'input_log_sha256': {}}
    write(folder / 'proposal.json', proposal)
    cases = {key: [] for key in PROFILES}
    for number, entry in enumerate(entries, 1):
        log = INPUT / f'case-{number:02d}-{Path(entry["filename"]).stem}' / 'low-confidence-detections.jsonl'
        proposal['input_log_sha256'][str(log.relative_to(INPUT))] = digest(log)
        packets = []
        with log.open(encoding='utf-8') as stream:
            for line in stream:
                packet = json.loads(line)
                packet['detections'] = [box for box in packet['detections'] if box['confidence'] >= .35]
                packets.append(packet)
        for key, config in configs.items():
            tracker = EventTracker(TemporalConfig(**config))
            snapshots = [tracker.update(p['timestamp_s'], p['detections'], p['frame_size']) for p in packets]
            metric = evaluate_video(rows[entry['filename']], packets, snapshots, plan['episode_merge_gap_s'])
            cases[key].append({**entry, 'metrics': metric})
        if cases['reference'][-1]['metrics'] != original['cases']['0.35'][number - 1]['metrics']:
            raise ValueError('Reference replay differs.')
        print(f'{number}/30 {entry["filename"]}', flush=True)
    write(folder / 'proposal.json', proposal)
    totals = {key: {label: aggregate(values, 'development', label, 'temporal')
                    for label in ('fire', 'smoke')} for key, values in cases.items()}
    write(folder / 'results.json', {'proposal': proposal, 'aggregate': totals, 'cases': cases,
                                    'reference_replay_exact': True})
    print(json.dumps({'output': str(folder), 'summary': {key: {label: {'misses': m['missed_scene_events'],
          'false_episodes': m['false_hint_episodes'], 'detected': m['detected_scene_events']}
          for label, m in values.items()} for key, values in totals.items()}}, indent=2), flush=True)


if __name__ == '__main__':
    main()
