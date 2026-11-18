"""Fetch two pinned public YOLO26 fire checkpoints, without remote code."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
SPECS = {
    'general26s': {
        'repo_id': 'SalahALHaismawi/yolov26-fire-detection',
        'revision': '5397b43994c39c2ed4ef81a6996c6334e2b7a5fa',
        'filename': 'best.pt', 'size_bytes': 20301317,
        'sha256': '52684ddffd4797f1bc5ff1bd2b72af3ec34eabf0c1646c37f518bb766b27b79f',
        'publisher_license': 'mit',
        'publisher_architecture': 'YOLO26-S',
        'publisher_training': '8,939 Roboflow images, fire/smoke/other; dataset declared CC BY 4.0. Not independently audited.',
    },
    'wildfire26m': {
        'repo_id': 'odiug77/wildfire-smoke-fire',
        'revision': '9c81734ac75d14ba57c7834904891874074e451f',
        'filename': 'wildfire-smoke-fire.pt', 'size_bytes': 44028569,
        'sha256': '6b7089f0abb37821cc17bd72d9c595591f03d9f230fe49acec0166049c9f213e',
        'publisher_license': 'apache-2.0',
        'publisher_architecture': 'YOLO26-M',
        'publisher_training': 'Outdoor wildfire RGB camera images, linked odiug77/wildfire dataset; exact training membership not audited.',
    },
}


def folder_for(key):
    return ROOT / 'models/candidates' / (key + '-' + SPECS[key]['revision'][:12])


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch(key):
    import requests
    spec = SPECS[key]
    folder = folder_for(key)
    folder.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    metadata_url = 'https://huggingface.co/api/models/' + spec['repo_id'] + '/revision/' + spec['revision']
    response = session.get(metadata_url, params={'blobs': 'true'}, timeout=(10, 20))
    response.raise_for_status()
    metadata = response.json()
    remote = next(f for f in metadata['siblings'] if f['rfilename'] == spec['filename'])
    assert metadata['sha'] == spec['revision']
    assert remote['lfs']['sha256'] == spec['sha256'] and remote['size'] == spec['size_bytes']
    assert metadata.get('cardData', {}).get('license') == spec['publisher_license']
    (folder / 'publisher-metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    base = 'https://huggingface.co/' + spec['repo_id'] + '/resolve/' + spec['revision'] + '/'
    card = session.get(base + 'README.md', timeout=(10, 20))
    card.raise_for_status()
    (folder / 'README.md').write_bytes(card.content)
    target = folder / spec['filename']
    if target.exists():
        if sha256(target) != spec['sha256']:
            raise ValueError('Existing candidate checksum differs; refusing to overwrite.')
    else:
        partial = target.with_suffix('.pt.partial')
        offset = partial.stat().st_size if partial.exists() else 0
        if offset > spec['size_bytes']:
            raise ValueError('Partial file exceeds expected size.')
        with partial.open('ab') as stream:
            while offset < spec['size_bytes']:
                end = min(offset + 4 * 1024 * 1024, spec['size_bytes']) - 1
                response = session.get(base + spec['filename'], headers={'Range': f'bytes={offset}-{end}'}, timeout=(10, 45))
                response.raise_for_status()
                if response.status_code == 200 and offset == 0 and len(response.content) == spec['size_bytes']:
                    stream.write(response.content)
                    offset = spec['size_bytes']
                elif (response.status_code == 206 and
                      response.headers.get('Content-Range') == f'bytes {offset}-{end}/{spec["size_bytes"]}' and
                      len(response.content) == end - offset + 1):
                    stream.write(response.content)
                    offset = end + 1
                else:
                    raise ValueError('Unexpected download range or size.')
                stream.flush()
                print(f'{key}: {offset}/{spec["size_bytes"]} bytes', flush=True)
        if sha256(partial) != spec['sha256']:
            raise ValueError('Downloaded candidate checksum differs.')
        partial.replace(target)
    manifest = {**spec, 'source_url': 'https://huggingface.co/' + spec['repo_id'],
                'verified_sha256': sha256(target), 'model_card_sha256': sha256(folder / 'README.md'),
                'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
                'usage_scope': 'Local development comparison only; not bundled with the software.',
                'license_note': 'Publisher MIT/Apache declaration does not establish an exemption from upstream Ultralytics AGPL/enterprise conditions; provenance and distribution rights remain unverified.'}
    (folder / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print('VERIFIED ' + str(target), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', choices=tuple(SPECS), required=True)
    fetch(parser.parse_args().candidate)
