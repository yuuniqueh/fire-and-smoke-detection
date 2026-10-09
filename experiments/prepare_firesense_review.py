"""Create an unlabelled human-review workspace without overwriting annotations."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.data_review import initialize_review, read_review, save_review


def main():
    manifest = json.loads((ROOT / 'videos/firesense-manifest.json').read_text(encoding='utf-8'))
    runs = json.loads((ROOT / 'outputs/firesense-initial-check.json').read_text(encoding='utf-8'))
    seen = {row['source'] for row in runs['results']}
    path = ROOT / 'data/firesense/review.json'
    if path.exists():
        document, _ = read_review(path)
        if {r['filename']: r['source_sha256'] for r in document['videos']} != {r['filename']: r['sha256'] for r in manifest['videos']}:
            raise ValueError('现有审查清单与素材登记不一致；保留现有标注，人工核查。')
        print('Existing annotations preserved.', flush=True)
    else:
        document = initialize_review(manifest, seen)
        save_review(path, document)
    print(json.dumps({'review_file': str(path), 'videos': len(document['videos']),
                      'quarantined': sum(r['quality'].startswith('quarantined') for r in document['videos']),
                      'development_seen': sum(r['development_seen'] for r in document['videos']),
                      'full_video_reviewed': sum(r['full_video_reviewed'] for r in document['videos']),
                      'split_frozen': document['split_frozen']}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
