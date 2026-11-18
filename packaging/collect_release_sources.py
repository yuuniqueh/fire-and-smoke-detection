"""Fetch pinned upstream source archives, never execute their contents."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / '.cache/release-compliance'
SOURCES = OUT / 'sources'
FFMPEG_REV = 'd63d7c154c57242bf2283be61166be2bd30ec47e'
LOCK = threading.Lock()


def read_url(url, limit=4000000):
    request = urllib.request.Request(url, headers={'User-Agent': 'FireGuard-corresponding-source'})
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.read(limit)


def hashed(path, algorithm='sha256', git_blob=False):
    h = hashlib.new(algorithm)
    if git_blob:
        h.update(b'blob ' + str(path.stat().st_size).encode() + b'\0')
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()


def download(item):
    item = dict(item)
    target = SOURCES / item['file']
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if target.exists():
            assert not item.get('sha256') or hashed(target) == item['sha256'], target.name
            assert not item.get('git_blob_sha1') or hashed(target, 'sha1', True) == item['git_blob_sha1']
        else:
            error = None
            for url in item.get('mirrors', []) + [item['url']]:
                partial = target.with_name(target.name + '.partial')
                try:
                    request = urllib.request.Request(url, headers={'User-Agent': 'FireGuard-corresponding-source'})
                    with urllib.request.urlopen(request, timeout=35) as response, partial.open('wb') as stream:
                        while True:
                            chunk = response.read(1048576)
                            if not chunk:
                                break
                            stream.write(chunk)
                    if item.get('sha256'):
                        assert hashed(partial) == item['sha256'], 'SHA256 mismatch'
                    if item.get('git_blob_sha1'):
                        assert hashed(partial, 'sha1', True) == item['git_blob_sha1'], 'Git blob mismatch'
                    partial.replace(target)
                    error = None
                    break
                except Exception as exc:
                    error = exc
            if error:
                raise error
        item.update({'ok': True, 'bytes': target.stat().st_size, 'sha256': hashed(target)})
        item.pop('mirrors', None)
    except Exception as exc:
        item.update({'ok': False, 'error': type(exc).__name__ + ': ' + str(exc)[:200]})
    with LOCK:
        print(item['component'], 'OK' if item['ok'] else item['error'], flush=True)
    return item


def qt_archive(component, url):
    data = read_url(url + '.meta4')
    tree = ET.fromstring(data)
    ns = {'m': 'urn:ietf:params:xml:ns:metalink'}
    file = tree.find('m:file', ns)
    expected = file.find('m:hash[@type="sha-256"]', ns).text
    mirrors = [n.text for n in file.findall('m:url', ns) if n.text.startswith('https://')]
    preferred = [u for u in mirrors if any(host in u for host in ('jaist.ac.jp', 'nic.funet.fi', 'fau.de', 'accum.se'))]
    return {'component': component, 'version': '6.9.3', 'url': url,
            'mirrors': preferred[:2], 'file': component + '/' + file.get('name'),
            'sha256': expected, 'verification': 'Qt official metalink SHA256'}


def main():
    SOURCES.mkdir(parents=True, exist_ok=True)
    plan_path = OUT / 'source-plan.json'
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
    else:
        plan = [qt_archive('qtbase', 'https://download.qt.io/archive/qt/6.9/6.9.3/submodules/qtbase-everywhere-src-6.9.3.tar.xz'),
                qt_archive('pyside-setup', 'https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.9.3-src/pyside-setup-everywhere-src-6.9.3.tar.xz')]
        ref = urllib.parse.quote('ffmpeg/4.x_20241226_src', safe='')
        try:
            info = json.loads(read_url('https://api.github.com/repos/opencv/opencv_3rdparty/git/trees/' + ref + '?recursive=1'))
            assert not info.get('truncated')
            revision = info['sha']
            for entry in info['tree']:
                if entry['type'] == 'blob' and entry['path'].startswith('sources/'):
                    plan.append({'component': 'opencv-ffmpeg', 'version': revision,
                                 'url': 'https://raw.githubusercontent.com/opencv/opencv_3rdparty/' + revision + '/' + entry['path'],
                                 'file': 'opencv-ffmpeg/' + entry['path'].removeprefix('sources/'),
                                 'git_blob_sha1': entry['sha'], 'verification': 'Pinned OpenCV source branch Git blob SHA1'})
        except Exception:
            # The release-specific source branch is published by the same
            # build_update.sh that produced the hash-matched bundled DLL.
            branch = 'ffmpeg/4.x_20241226_src'
            for path in ('opencv_ffmpeg.tar.xz', 'opencv/opencv-videoio-ffmpeg.tar.xz',
                         'build/ffmpeg/ffmpeg-src-n4.4.5.tar.xz',
                         'build/libvpx/libvpx-src-v1.15.0.tar.xz',
                         'build/openh264/openh264-api-headers-v1.8.0.tar.xz',
                         'build/aom/aom-src-v3.11.0.tar.xz'):
                plan.append({'component':'opencv-ffmpeg','version':branch,
                    'url':'https://raw.githubusercontent.com/opencv/opencv_3rdparty/'+branch+'/sources/'+path,
                    'file':'opencv-ffmpeg/'+path,
                    'verification':'Official release-specific source branch; local SHA256 recorded after retrieval; API unavailable.'})
        inventory = json.loads((ROOT / 'outputs/portable-windows-cpu-20261013/dist/FireGuard/third-party-notices/dependency-inventory.json').read_text(encoding='utf-8'))
        failures = []
        def pypi(entry):
            name, version = entry['name'], entry['version'].split('+')[0]
            if name.lower().startswith(('pyside', 'shiboken')):
                return None
            try:
                metadata = json.loads(read_url('https://pypi.org/pypi/' + name + '/' + version + '/json'))
                sdist = next((v for v in metadata['urls'] if v['packagetype'] == 'sdist'), None)
                if not sdist:
                    failures.append({'component': name, 'version': version, 'reason': 'No upstream PyPI sdist; separately licensed binary/source reference retained.'})
                    return None
                return {'component': name, 'version': version, 'url': sdist['url'],
                        'file': 'python/' + sdist['filename'], 'sha256': sdist['digests']['sha256'],
                        'verification': 'PyPI version metadata SHA256'}
            except Exception as exc:
                failures.append({'component': name, 'version': version, 'reason': str(exc)[:200]})
                return None
        with concurrent.futures.ThreadPoolExecutor(4) as pool:
            plan += [v for v in pool.map(pypi, inventory) if v]
        (OUT / 'upstream-sdist-gaps.json').write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding='utf-8')
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Source archives planned:', len(plan), flush=True)
    results = []
    previous = json.loads((OUT/'source-manifest.json').read_text(encoding='utf-8')) if (OUT/'source-manifest.json').exists() else []
    with concurrent.futures.ThreadPoolExecutor(4) as pool:
        for item in pool.map(download, plan):
            results.append(item)
            extras = [v for v in previous if v['component'] not in {p['component'] for p in plan}]
            (OUT/'source-manifest.json').write_text(json.dumps(results + extras, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'ok': sum(v['ok'] for v in results), 'failed': [v['component'] for v in results if not v['ok']],
                      'bytes': sum(v.get('bytes', 0) for v in results)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
