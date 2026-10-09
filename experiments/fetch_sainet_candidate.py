"""Fetch one pinned public research checkpoint; never change models/best.pt."""
import base64
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

ROOT = Path(__file__).resolve().parents[1]
REPO = 'AlterMundi/sai-sainet-latest'
REVISION = '2246d5ea1408a20ad6fa248eb35a3c5db404e32a'
WEIGHT_PATH = 'model/SAINet_v11.1.pt'
MAX_BYTES = 200 * 1024 * 1024


def main():
    session = requests.Session()
    session.headers['User-Agent'] = 'FireGuard-local-research'
    session.mount('https://', HTTPAdapter(max_retries=Retry(total=3, backoff_factor=.5,
                  status_forcelist=(429, 500, 502, 503, 504), allowed_methods=('GET',))))
    api = 'https://api.github.com/repos/' + REPO
    folder = ROOT / 'models/candidates' / ('sainet-' + REVISION[:12])
    folder.mkdir(parents=True, exist_ok=True)
    metadata_path = folder / 'download-metadata.json'
    if metadata_path.exists():
        cached = json.loads(metadata_path.read_text(encoding='utf-8'))
        if cached['revision'] != REVISION:
            raise ValueError('Cached revision changed.')
        info = cached['github_contents']
    else:
        response = session.get(api + '/contents/' + WEIGHT_PATH, params={'ref': REVISION}, timeout=30)
        response.raise_for_status()
        info = response.json()
        metadata_path.write_text(json.dumps({'revision': REVISION, 'github_contents': info}, indent=2), encoding='utf-8')
    if info['size'] > MAX_BYTES:
        raise ValueError('Checkpoint exceeds predeclared 200 MiB budget.')
    pointer_path = folder / 'git-lfs-pointer.txt'
    if pointer_path.exists():
        pointer = pointer_path.read_text(encoding='ascii')
    else:
        response = session.get(api + '/git/blobs/' + info['sha'], timeout=30)
        response.raise_for_status()
        pointer = base64.b64decode(response.json()['content']).decode('ascii')
        pointer_path.write_text(pointer, encoding='ascii')
    match = re.search(r'^oid sha256:([0-9a-f]{64})$', pointer, re.MULTILINE)
    if not pointer.startswith('version https://git-lfs.github.com/spec/v1') or match is None:
        raise ValueError('Expected an authoritative Git LFS SHA256 pointer.')
    expected = match.group(1)
    expected_size = int(re.search(r'^size (\d+)$', pointer, re.MULTILINE).group(1))
    if expected_size != info['size']:
        raise ValueError('GitHub metadata and LFS size differ.')
    for name in ('LICENSE', 'README.md', 'model/train_hyperparams.yaml'):
        if (folder / Path(name).name).exists():
            continue
        response = session.get(api + '/contents/' + name, params={'ref': REVISION}, timeout=30)
        response.raise_for_status()
        document = response.json()
        if document['encoding'] != 'base64':
            raise ValueError('Expected a small source document in GitHub contents API.')
        (folder / Path(name).name).write_bytes(base64.b64decode(document['content']))
    target = folder / Path(WEIGHT_PATH).name
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected or target.stat().st_size != expected_size:
            raise ValueError('Existing candidate differs; do not overwrite.')
    else:
        temporary = target.with_suffix('.pt.part')
        count = temporary.stat().st_size if temporary.exists() else 0
        if count > expected_size:
            raise ValueError('Existing partial file is oversized; preserve it for inspection.')
        # Small independent ranges survive intermittent proxy timeouts; incomplete
        # ranges are never appended. Final publisher hash verifies resumed bytes.
        with temporary.open('ab') as output:
            while count < expected_size:
                end = min(count + 4 * 1024 * 1024, expected_size) - 1
                for attempt in range(3):
                    try:
                        response = session.get(info['download_url'],
                            headers={'Range': f'bytes={count}-{end}'}, timeout=(15, 30))
                        response.raise_for_status()
                        expected_range = f'bytes {count}-{end}/{expected_size}'
                        if response.status_code != 206 or response.headers.get('Content-Range') != expected_range:
                            raise ValueError('Unexpected range response; do not append.')
                        block = response.content
                        if len(block) != end - count + 1:
                            raise ValueError('Incomplete download range.')
                        break
                    except requests.RequestException:
                        if attempt == 2:
                            raise
                output.write(block)
                output.flush()
                count += len(block)
                print(f'DOWNLOAD {count}/{expected_size}', flush=True)
        sha = hashlib.sha256(temporary.read_bytes())
        if count != expected_size or sha.hexdigest() != expected:
            raise ValueError('Checkpoint size or publisher LFS SHA256 mismatch.')
        temporary.replace(target)
    manifest = {'repository': 'https://github.com/' + REPO, 'revision': REVISION,
                'source_path': WEIGHT_PATH, 'download_url': info['download_url'],
                'git_blob_sha': info['sha'], 'sha256': expected, 'bytes': expected_size,
                'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
                'license_declared_by_publisher': 'AGPL-3.0',
                'purpose': 'Local candidate screening only; not adopted or published.',
                'training_overlap_with_firesense': 'Unverified; no external independence claim.',
                'publisher_scope': 'Outdoor early wildfire smoke; benchmark card reports smoke only; fire quality must be tested locally.'}
    (folder / 'source-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'path': str(target), 'sha256': expected, 'bytes': expected_size}, indent=2), flush=True)


if __name__ == '__main__':
    main()
