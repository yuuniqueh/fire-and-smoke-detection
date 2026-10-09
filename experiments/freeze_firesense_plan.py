"""Freeze a conservative internal split before inspecting unseen predictions."""
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.data_review import validate_video


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def design_groups(rows, constraints):
    parents = {row['filename']: row['filename'] for row in rows}
    def find(name):
        while parents[name] != name:
            name = parents[name]
        return name
    for files in constraints:
        if any(name not in parents for name in files):
            raise ValueError('Unknown group member.')
        for name in files[1:]:
            parents[find(name)] = find(files[0])
    groups = {}
    for row in rows:
        groups.setdefault(find(row['filename']), []).append(row['filename'])
    return [sorted(names) for names in sorted(groups.values(), key=lambda names: min(names))]


def propose_split(rows, groups, seed=20261006, target=14):
    by_name = {row['filename']: row for row in rows}
    usable = {row['filename'] for row in rows if not row['quality'].startswith('quarantined')}
    candidates = [[name for name in names if name in usable] for names in groups
                  if not any(by_name[name]['development_seen'] for name in names)]
    candidates = [names for names in candidates if names]
    rng = random.Random(seed)
    best = None
    for _ in range(5000):
        shuffled = candidates.copy()
        rng.shuffle(shuffled)
        selected = []
        for names in shuffled:
            if len(selected) >= target:
                break
            selected.extend(names)
        counts = {(label, state): sum(by_name[name]['class_status'][label] == state for name in selected)
                  for label in ('fire', 'smoke') for state in ('present', 'absent')}
        if any(counts[label, 'present'] < 3 or counts[label, 'absent'] < 6 for label in ('fire', 'smoke')):
            continue
        cost = 10 * abs(len(selected) - target) + sum(3 * abs(counts[label, 'present'] - 5) + abs(counts[label, 'absent'] - 9)
                                                    for label in ('fire', 'smoke'))
        candidate = (cost, tuple(sorted(selected)))
        if best is None or candidate < best:
            best = candidate
    if best is None:
        raise ValueError('Unable to form a balanced conservative split.')
    heldout = set(best[1])
    return {row['filename']: 'excluded' if row['filename'] not in usable else 'internal_holdout' if row['filename'] in heldout else 'development'
            for row in rows}


def main():
    folder = ROOT / 'data/firesense/evaluation-v1'
    plan_path = folder / 'plan.json'
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        print('Existing frozen plan preserved:', plan_path)
        print(json.dumps(plan['split_counts'], ensure_ascii=False, indent=2))
        return
    review_path = ROOT / 'data/firesense/review.json'
    raw = review_path.read_bytes()
    review = json.loads(raw.decode('utf-8'))
    rows = review['videos']
    for row in rows:
        validate_video(row)
        if not row['quality'].startswith('quarantined') and not row['full_video_reviewed']:
            raise ValueError('Incomplete nonquarantined human review.')
    candidates = json.loads((ROOT / 'data/firesense/group-candidates.json').read_text(encoding='utf-8'))
    similarity = json.loads((ROOT / 'outputs/firesense-similarity.json').read_text(encoding='utf-8'))
    constraints = [item['files'] for item in candidates['groups']] + [item['files'] for item in similarity['pairs']]
    groups = design_groups(rows, constraints)
    split = propose_split(rows, groups)
    grouped = {name: f'constraint-group-{i+1:02d}' for i, names in enumerate(groups) for name in names}
    entries = [{key: row[key] for key in ('filename', 'source_sha256', 'fps', 'decoded_frames', 'development_seen', 'class_status')}
               | {'split': split[row['filename']], 'constraint_group': grouped[row['filename']]} for row in rows]
    # Python 3.9 supports dictionary union.
    plan = {'version': 'firesense-internal-baseline-v1', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
            'frozen': True, 'scope': 'Exploratory internal grouped split; original provenance and training overlap unverified.',
            'grouping': 'Keep every visual/source candidate together; unlinked clips are singleton constraints, not proven independent captures.',
            'group_evidence': {'manual_candidates': candidates, 'feature_similarity': similarity},
            'seed': 20261006, 'selection': '5000 deterministic group proposals; target 14 clips, approximately 5 positive/9 negative for each class; no predictions used.',
            'review_sha256': hashlib.sha256(raw).hexdigest(),
            'review_snapshot': 'review-snapshot.json', 'model_manifest': json.loads((ROOT / 'models/manifest.json').read_text(encoding='utf-8')),
            'weights_sha256': sha(ROOT / 'models/best.pt'), 'confidence': .35, 'image_size': 640,
            'temporal_config': json.loads((ROOT / 'configs/temporal.json').read_text(encoding='utf-8')),
            'episode_merge_gap_s': 1.0,
            'code_sha256': {name: sha(ROOT / name) for name in ('core/detector.py', 'core/temporal.py', 'core/evaluation.py')},
            'split_counts': {split_name: sum(item['split'] == split_name for item in entries)
                             for split_name in ('development', 'internal_holdout', 'excluded')},
            'split_class_counts': {split_name: {label: {state: sum(item['split'] == split_name and item['class_status'][label] == state for item in entries)
                                                       for state in ('present', 'absent', 'uncertain')}
                                                 for label in ('fire', 'smoke')}
                                   for split_name in ('development', 'internal_holdout')},
            'groups': [{'id': f'constraint-group-{i+1:02d}', 'files': names} for i, names in enumerate(groups)],
            'entries': entries}
    folder.mkdir(parents=True)
    (folder / 'review-snapshot.json').write_bytes(raw)
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'plan': str(plan_path), 'split_counts': plan['split_counts'], 'class_counts': plan['split_class_counts']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
