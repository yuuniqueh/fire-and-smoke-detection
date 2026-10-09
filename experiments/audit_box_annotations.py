"""Snapshot and inspect human box labels; never substitute model boxes for truth."""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.box_review import digest_bytes, training_eligible, validate_document
from experiments.benchmark_firesense import digest, read, write


def main():
    import cv2
    import numpy as np
    pilot = ROOT / 'data/firesense/box-review-pilot-v1'
    manifest_raw = (pilot / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_raw)
    annotation_raw = (pilot / 'annotations.json').read_bytes()
    doc = json.loads(annotation_raw)
    revision = digest_bytes(annotation_raw)
    validate_document(doc, manifest, digest_bytes(manifest_raw))
    plan = read(ROOT / 'data/firesense/evaluation-v1/plan.json')
    entries = {entry['filename']: entry for entry in plan['entries']}
    output = ROOT / 'outputs' / datetime.now().strftime('box-annotation-audit-%Y%m%d-%H%M%S-%f')
    output.mkdir()
    (output / 'annotations-snapshot.json').write_bytes(annotation_raw)
    (output / 'manifest-snapshot.json').write_bytes(manifest_raw)
    checked_sources = set()
    panels = []
    small = []
    for number, row in enumerate(doc['images'], 1):
        entry = entries[row['source']]
        if entry['split'] != 'development' or entry['source_sha256'] != row['source_sha256']:
            raise ValueError('Unexpected source identity or split.')
        if row['source'] not in checked_sources:
            if digest(ROOT / 'videos' / row['source']) != row['source_sha256']:
                raise ValueError('Video source changed.')
            checked_sources.add(row['source'])
        image_path = pilot / row['image']
        if digest(image_path) != row['image_sha256']:
            raise ValueError('Raw image changed.')
        image = cv2.imread(str(image_path))
        if image is None or image.shape[:2] != (row['height'], row['width']):
            raise ValueError('Image dimensions differ.')
        # Draw only USER boxes. Separate panels keep reviewed/draft and class states visible.
        rendered = image.copy()
        for index, box in enumerate(row['boxes'], 1):
            x1, y1, x2, y2 = box['xyxy']
            if min(x2 - x1, y2 - y1) < 3:
                small.append({'image': row['image'], 'box_index': index, 'xyxy': box['xyxy']})
            color = (30, 100, 255) if box['label'] == 'fire' else (255, 200, 30)
            cv2.rectangle(rendered, (round(x1), round(y1)), (round(x2), round(y2)), color, 1)
            cv2.putText(rendered, f'{index}:{box["label"]}', (round(x1), max(12, round(y1) - 3)),
                        cv2.FONT_HERSHEY_SIMPLEX, .35, color, 1)
        panel = np.full((358, 520, 3), (22, 22, 22), dtype=np.uint8)
        scale = min(510 / row['width'], 286 / row['height'])
        size = (round(row['width'] * scale), round(row['height'] * scale))
        shown = cv2.resize(rendered, size, interpolation=cv2.INTER_NEAREST)
        left, top = (520 - size[0]) // 2, 66 + (286 - size[1]) // 2
        panel[top:top + size[1], left:left + size[0]] = shown
        cv2.putText(panel, f'{number:02d} {Path(row["image"]).name}', (8, 20), cv2.FONT_HERSHEY_SIMPLEX, .47, (240, 240, 240), 1)
        state = 'completed' if row['completed'] else 'DRAFT'
        cv2.putText(panel, f'HUMAN {state}; fire={row["class_status"]["fire"]}; smoke={row["class_status"]["smoke"]}',
                    (8, 43), cv2.FONT_HERSHEY_SIMPLEX, .42, (180, 215, 230), 1)
        panels.append(panel)
    for start in range(0, len(panels), 9):
        sheet = np.vstack([np.hstack(panels[start + j:start + j + 3]) for j in (0, 3, 6)])
        if not cv2.imwrite(str(output / f'human-boxes-{start // 9 + 1}.png'), sheet):
            raise ValueError('Contact sheet could not be saved.')
    report = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'annotation_sha256': revision,
              'manifest_sha256': digest_bytes(manifest_raw), 'images_total': len(doc['images']),
              'completed': sum(row['completed'] for row in doc['images']),
              'schema_training_eligible': sum(training_eligible(row) for row in doc['images']),
              'reviewers': dict(Counter(row['reviewer_id'] for row in doc['images'])),
              'boxes': dict(Counter(box['label'] for row in doc['images'] for box in row['boxes'])),
              'source_videos_verified': len(checked_sources), 'image_hashes_verified': len(doc['images']),
              'completed_empty_negative_images': sum(training_eligible(row) and not row['boxes'] for row in doc['images']),
              'draft_images': [row['image'] for row in doc['images'] if not row['completed']],
              'uncertain_images': [row['image'] for row in doc['images'] if 'uncertain' in row['class_status'].values()],
              'tiny_boxes_under_3_pixels': small,
              'note': 'Schema eligibility is not semantic correctness. Inspect contact sheets and visible targets before training; no model predictions generated here.'}
    write(output / 'audit.json', report)
    print(json.dumps({'output': str(output), **report}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
