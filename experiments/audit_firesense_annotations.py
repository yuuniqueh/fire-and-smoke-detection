"""Audit user annotations without editing labels or exposing unseen model results."""
import hashlib
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.data_review import validate_video


def file_hash(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def main():
    path = ROOT / 'data/firesense/review.json'
    raw = path.read_bytes()
    review_sha = hashlib.sha256(raw).hexdigest()
    review = json.loads(raw.decode('utf-8'))
    manifest = json.loads((ROOT / 'videos/firesense-manifest.json').read_text(encoding='utf-8'))
    registered = {row['filename']: row for row in manifest['videos']}
    seen_runs = json.loads((ROOT / 'outputs/firesense-initial-check.json').read_text(encoding='utf-8'))
    seen = {row['source'] for row in seen_runs['results']}
    folder = ROOT / 'outputs' / datetime.now().strftime('annotation-audit-%Y%m%d-%H%M%S-%f')
    folder.mkdir()
    (folder / 'review-snapshot.json').write_bytes(raw)
    errors, followups, uncertain, quarantined = [], [], [], []
    rows = review['videos']
    names = [row['filename'] for row in rows]
    if len(names) != len(set(names)) or set(names) != set(registered):
        errors.append({'type': 'identity', 'detail': 'Missing, extra, or duplicate registered filenames.'})
    canonical = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    if review['source_manifest_sha256'] != canonical:
        errors.append({'type': 'manifest_revision', 'detail': 'Review refers to a different source manifest.'})
    for row in rows:
        filename = row['filename']
        source = registered.get(filename)
        if source is None:
            continue
        try:
            validate_video(row)
        except (ValueError, KeyError, TypeError) as exc:
            errors.append({'filename': filename, 'type': 'annotation_schema', 'detail': str(exc)})
        source_path = (ROOT / 'videos' / filename).resolve()
        if (ROOT / 'videos').resolve() not in source_path.parents:
            errors.append({'filename': filename, 'type': 'source_path', 'detail': 'Outside videos directory.'})
            continue
        actual_hash = file_hash(source_path) if source_path.is_file() else None
        if actual_hash != source['sha256'] or row['source_sha256'] != source['sha256']:
            errors.append({'filename': filename, 'type': 'source_identity', 'detail': 'Source hash differs or missing file.'})
        if row['fps'] != source['fps'] or row['decoded_frames'] != source['decoded_frames']:
            errors.append({'filename': filename, 'type': 'timing_metadata', 'detail': 'FPS or decoded frame count changed.'})
        if filename in seen and (not row['development_seen'] or row['split'] != 'development_seen'):
            errors.append({'filename': filename, 'type': 'development_exposure', 'detail': 'Known development exposure lost.'})
        if not source['frame_count_matches']:
            quarantined.append(filename)
            if not row['quality'].startswith('quarantined'):
                errors.append({'filename': filename, 'type': 'quality_state', 'detail': 'Decode warning not quarantined.'})
            continue
        if not row['full_video_reviewed']:
            followups.append({'filename': filename, 'type': 'incomplete_review'})
        for label in ('fire', 'smoke'):
            if row['class_status'][label] == 'uncertain':
                uncertain.append({'filename': filename, 'label': label, 'notes': row['notes']})
            intervals = [item for item in row['intervals'] if item['label'] == label]
            duration = sum(item['end_frame_exclusive'] - item['start_frame'] for item in intervals)
            if row['class_status'][label] == 'present' and duration == 1 and row['decoded_frames'] / row['fps'] >= 2:
                followups.append({'filename': filename, 'label': label, 'type': 'single_frame_positive',
                                 'intervals': intervals, 'video_frames': row['decoded_frames'],
                                 'detail': 'Legal one-frame interval; check whether the default end boundary was left unchanged. Do not auto-correct.'})
    usable = [row for row in rows if row['filename'] not in quarantined]
    report = {'audited_at_utc': datetime.now(timezone.utc).isoformat(), 'review_sha256': review_sha,
              'snapshot': str(folder / 'review-snapshot.json'), 'source_videos_hashed': len(registered),
              'summary': {'total': len(rows), 'full_video_reviewed': sum(r['full_video_reviewed'] for r in rows),
                          'quarantined': len(quarantined), 'nonquarantined': len(usable),
                          'development_seen': sum(r['development_seen'] for r in rows),
                          'verified_source_groups': sum(r['group_verified'] for r in usable),
                          'class_status_nonquarantined': {label: dict(Counter(r['class_status'][label] for r in usable))
                                                          for label in ('fire', 'smoke')}},
              'structural_errors': errors, 'annotation_followups': followups,
              'uncertain_classes_excluded_from_definite_metrics': uncertain, 'quarantined_files': quarantined,
              'split_frozen': review['split_frozen'],
              'readiness': 'needs_followup' if errors or followups or any(not r['group_verified'] for r in usable) else 'ready_for_split_design',
              'scope': 'File identity and annotation consistency only; not a verification of visual ground truth, dataset independence or accuracy.',
              'review_changed_during_audit': hashlib.sha256(path.read_bytes()).hexdigest() != review_sha}
    (folder / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': str(folder / 'audit.json'), 'summary': report['summary'],
                      'structural_errors': errors, 'annotation_followups': followups,
                      'readiness': report['readiness'], 'review_changed_during_audit': report['review_changed_during_audit']}, ensure_ascii=False, indent=2), flush=True)
    if errors or report['review_changed_during_audit']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
