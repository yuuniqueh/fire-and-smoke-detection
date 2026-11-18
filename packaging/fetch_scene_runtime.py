"""Fetch the pinned scene-checkpoint runtime wheel and its source from PyPI."""
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.4.0'


def main():
    cache = ROOT / '.cache/package-wheels'
    cache.mkdir(parents=True, exist_ok=True)
    url = f'https://pypi.org/pypi/dill/{VERSION}/json'
    with urllib.request.urlopen(url, timeout=30) as response:
        metadata = json.load(response)
    assert metadata['info']['version'] == VERSION
    (cache / f'dill-{VERSION}-pypi.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    records = []
    for kind in ('bdist_wheel', 'sdist'):
        matches = [item for item in metadata['urls'] if item['packagetype'] == kind]
        assert len(matches) == 1
        item = matches[0]
        source = urllib.parse.urlsplit(item['url'])
        assert source.scheme == 'https' and source.hostname == 'files.pythonhosted.org'
        assert Path(item['filename']).name == item['filename']
        folder = cache if kind == 'bdist_wheel' else ROOT / '.cache/release-compliance/sources/python'
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / item['filename']
        if not target.exists():
            with urllib.request.urlopen(item['url'], timeout=30) as response:
                data = response.read(20000000)
            assert len(data) == item['size'] and hashlib.sha256(data).hexdigest() == item['digests']['sha256']
            target.write_bytes(data)
        assert target.stat().st_size == item['size']
        assert hashlib.sha256(target.read_bytes()).hexdigest() == item['digests']['sha256']
        if kind == 'bdist_wheel':
            (cache / f'dill-{VERSION}-install.txt').write_text(
                f'dill=={VERSION} --hash=sha256:' + item['digests']['sha256'] + '\n', encoding='utf-8')
        else:
            row = {'component': 'dill', 'version': VERSION, 'url': item['url'],
                   'file': 'python/' + item['filename'], 'sha256': item['digests']['sha256'],
                   'verification': 'PyPI pinned version metadata SHA256', 'ok': True, 'bytes': item['size']}
            manifest_path = ROOT / '.cache/release-compliance/source-manifest.json'
            manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else []
            existing = [entry for entry in manifest if entry['component'] == 'dill']
            if existing:
                assert all(entry['version'] == VERSION and entry['sha256'] == row['sha256'] for entry in existing)
            else:
                manifest.append(row)
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        records.append({'filename': item['filename'], 'sha256': item['digests']['sha256'], 'bytes': item['size']})
    print(json.dumps(records, indent=2))


if __name__ == '__main__':
    main()
