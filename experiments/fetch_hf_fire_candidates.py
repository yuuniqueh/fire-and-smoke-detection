"""Download two explicitly selected public checkpoints at immutable revisions."""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from experiments.benchmark_firesense import digest, read, write

CANDIDATES = {
    'domestic': {
        'repo_id': 'mfranzon/fire-smoke-yolov8',
        'revision': 'f1c6426b069c1849cbf13b1ef5d2a260289286db',
        'filename': 'fire_smoke_yolov8.pt', 'size_bytes': 6262051,
        'sha256': 'ac0a10257b2bc1f20c9d957f8adeeb61dd6140322fc19d0b4a116cb491776d16',
        'training_provenance': 'Publisher describes YOLOv8n, D-Fire + Domestic Fire and Smoke. Not independently audited.',
    },
    'uav': {
        'repo_id': 'xiazh0219/yolo26m-UAV-fire-smoke-detection',
        'revision': '57cf971a788fff62b8f3e5b1c3632e1214b8255c',
        'filename': 'weights/best.pt', 'size_bytes': 44020050,
        'sha256': 'a06febf257bff40cf5f3814e77f5120e2a80ab91d3ff3a84370caf6fdf832740',
        'training_provenance': 'Publisher describes YOLO26m, FASDD + own UAV forest data. Not independently audited.',
    },
}


def candidate_folder(key):
    return ROOT / 'models/candidates' / (key + '-' + CANDIDATES[key]['revision'][:12])


def fetch(key):
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    spec = CANDIDATES[key]
    folder = candidate_folder(key)
    folder.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.mount('https://', HTTPAdapter(max_retries=Retry(
        total=2, backoff_factor=.5, status_forcelist=[429, 500, 502, 503, 504])))
    response = session.get('https://huggingface.co/api/models/' + spec['repo_id'] +
                           '/revision/' + spec['revision'], params={'blobs': 'true'}, timeout=(10, 20))
    response.raise_for_status()
    metadata = response.json()
    file = next(item for item in metadata['siblings'] if item['rfilename'] == spec['filename'])
    if (metadata['sha'] != spec['revision'] or file['lfs']['sha256'] != spec['sha256'] or
            file['size'] != spec['size_bytes'] or metadata.get('cardData', {}).get('license') != 'agpl-3.0'):
        raise ValueError('Pinned publisher metadata differs from inspected values.')
    write(folder / 'publisher-metadata.json', metadata)
    base = 'https://huggingface.co/' + spec['repo_id'] + '/resolve/' + spec['revision'] + '/'
    for name in ('README.md', 'args.yaml'):
        if any(item['rfilename'] == name for item in metadata['siblings']):
            doc = session.get(base + name, timeout=(10, 20))
            doc.raise_for_status()
            (folder / name).write_bytes(doc.content)
    target = folder / spec['filename']
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or digest(target) != spec['sha256']:
        partial = target.with_suffix(target.suffix + '.partial')
        # Resume only complete validated range chunks; final SHA256 verifies the whole file.
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > spec['size_bytes']:
            raise ValueError('Partial file larger than publisher file.')
        with partial.open('ab') as output:
            while offset < spec['size_bytes']:
                end = min(offset + 4 * 1024 * 1024, spec['size_bytes']) - 1
                chunk = session.get(base + spec['filename'], headers={'Range': f'bytes={offset}-{end}'}, timeout=(10, 45))
                chunk.raise_for_status()
                expected = f'bytes {offset}-{end}/{spec["size_bytes"]}'
                if chunk.status_code == 200 and offset == 0 and len(chunk.content) == spec['size_bytes']:
                    output.write(chunk.content)
                    offset = spec['size_bytes']
                elif chunk.status_code == 206 and chunk.headers.get('Content-Range') == expected and len(chunk.content) == end - offset + 1:
                    output.write(chunk.content)
                    offset = end + 1
                else:
                    raise ValueError('Unexpected range response: ' + str(chunk.headers.get('Content-Range')))
                output.flush()
                print(f'DOWNLOADED {key} {offset}/{spec["size_bytes"]}', flush=True)
        if partial.stat().st_size != spec['size_bytes'] or digest(partial) != spec['sha256']:
            raise ValueError('Downloaded weight checksum mismatch.')
        partial.replace(target)
    manifest = {**spec, 'source_url': 'https://huggingface.co/' + spec['repo_id'],
                'license_declared_by_repository': 'agpl-3.0',
                'license_note': 'Publisher declaration saved in model card and metadata; no separate LICENSE file in repository. Distribution obligations and training data rights require separate review.',
                'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
                'local_filename': spec['filename'], 'verified_sha256': digest(target),
                'metadata_sha256': digest(folder / 'publisher-metadata.json'),
                'model_card_sha256': digest(folder / 'README.md')}
    write(folder / 'source-manifest.json', manifest)
    print('VERIFIED ' + key + ' ' + str(target), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--candidate', choices=tuple(CANDIDATES), required=True)
    fetch(parser.parse_args().candidate)
