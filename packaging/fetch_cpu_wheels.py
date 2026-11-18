"""Fetch fixed official wheels with bounded requests, resume and SHA256 checks."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / '.cache/package-wheels'


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.links.extend(value for key, value in attrs if key == 'href')


def fetch(project, version, size):
    filename = f'{project}-{version}-cp39-cp39-win_amd64.whl'
    index = f'https://download.pytorch.org/whl/cpu/{project}/'
    with urllib.request.urlopen(index, timeout=30) as response:
        parser = Links()
        parser.feed(response.read().decode('utf-8'))
    links = [link for link in parser.links if urllib.parse.unquote(urllib.parse.urlsplit(link).path).endswith('/' + filename)]
    if len(links) != 1:
        raise ValueError('Expected exactly one official wheel: ' + filename)
    link = urllib.parse.urlsplit(urllib.parse.urljoin(index, links[0]))
    expected = urllib.parse.parse_qs(link.fragment).get('sha256', [''])[0]
    if not re.fullmatch('[0-9a-f]{64}', expected):
        raise ValueError('Missing official SHA256')
    url = urllib.parse.urlunsplit(('https', 'download.pytorch.org', link.path, '', ''))
    DEST.mkdir(parents=True, exist_ok=True)
    target = DEST / filename
    partial = DEST / (filename + '.part')
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() == expected:
            return
        raise ValueError('Existing wheel hash mismatch: ' + filename)
    start = partial.stat().st_size if partial.exists() else 0
    if start > size:
        raise ValueError('Partial file too large')
    def chunk(bounds):
        left, right = bounds
        request = urllib.request.Request(url, headers={'Range': f'bytes={left}-{right}'})
        with urllib.request.urlopen(request, timeout=40) as response:
            if response.status != 206 or response.headers.get('Content-Range') != f'bytes {left}-{right}/{size}':
                raise ValueError('Server did not honor the requested byte range')
            data = response.read()
        if len(data) != right - left + 1:
            raise ValueError('Truncated range response')
        return data
    bounds = [(left, min(size - 1, left + 4 * 1024 * 1024 - 1)) for left in range(start, size, 4 * 1024 * 1024)]
    with ThreadPoolExecutor(max_workers=4) as pool, partial.open('ab') as stream:
        for data in pool.map(chunk, bounds):
            stream.write(data)
            stream.flush()
            print(filename, stream.tell(), '/', size, flush=True)
    digest = hashlib.sha256()
    with partial.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError('Final wheel SHA256 mismatch')
    partial.rename(target)
    print('Verified', filename, expected, flush=True)


if __name__ == '__main__':
    fetch('torch', '2.8.0+cpu', 619275028)
    fetch('torchvision', '0.23.0+cpu', 1600872)
