"""Register locally downloaded FIRESENSE, verify extraction, decode, and make review sheets."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
import zipfile
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()

import cv2
from PIL import Image, ImageDraw, ImageFont

PACKAGES = {
    'fire_videos.1406': {'target': 'fire', 'md5': '7cfe8286cc4b45193149186bf67ea00d', 'expected': {'pos': 11, 'neg': 16}},
    'smoke_videos.1407': {'target': 'smoke', 'md5': '5ee6bc867c4a9332b4e75f308c26fc23', 'expected': {'pos': 13, 'neg': 9}},
}


def digest_file(path):
    sha, md5, crc = hashlib.sha256(), hashlib.md5(), 0
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            sha.update(block)
            md5.update(block)
            crc = zlib.crc32(block, crc)
    return sha.hexdigest(), md5.hexdigest(), crc & 0xffffffff


def contact_sheet(rows, frames, path, title):
    columns, cell_width, cell_height = 3, 370, 180
    row_count = math.ceil(len(rows) / columns)
    image = Image.new('RGB', (columns * cell_width, 55 + row_count * cell_height), '#101a2b')
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 15)
    title_font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 20)
    draw.text((14, 12), title + ' | publisher labels, review pending', fill='white', font=title_font)
    for index, row in enumerate(rows):
        x, y = (index % columns) * cell_width, 55 + (index // columns) * cell_height
        for sample_index, frame in enumerate(frames[row['filename']]):
            if frame is None:
                continue
            thumbnail = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            thumbnail.thumbnail((176, 120))
            image.paste(thumbnail, (x + 5 + sample_index * 182 + (176 - thumbnail.width) // 2, y + 5))
        draw.text((x + 8, y + 129), Path(row['filename']).name, fill='#cbd9ed', font=font)
        draw.text((x + 8, y + 151), f'{row["decoded_duration_s"]:.2f}s | {row["decoded_frames"]} frames | 1/3 and 2/3', fill='#8fa5c7', font=font)
    image.save(path)


def main():
    output = ROOT / 'outputs' / 'firesense-review'
    output.mkdir(exist_ok=True)
    registered, archives, sample_frames = [], [], {}
    for name, definition in PACKAGES.items():
        archive_path = ROOT / 'videos' / (name + '.zip')
        extracted = ROOT / 'videos' / name
        if not archive_path.is_file() or not extracted.is_dir():
            raise FileNotFoundError(f'请先准备压缩包及对应解压目录：{name}')
        sha, md5, _ = digest_file(archive_path)
        if md5 != definition['md5']:
            raise ValueError(f'{archive_path.name} 的 MD5 与发布者不一致')
        archives.append({'filename': archive_path.name, 'size_bytes': archive_path.stat().st_size,
                         'md5': md5, 'md5_matches_publisher': True, 'sha256': sha})
        with zipfile.ZipFile(archive_path) as bundle:
            members = [info for info in bundle.infolist() if not info.is_dir()]
            allowed_names = set()
            counts = Counter()
            for info in members:
                relative = Path(info.filename)
                if relative.is_absolute() or '..' in relative.parts:
                    raise ValueError('压缩包包含无效路径')
                path = (extracted / relative).resolve()
                if extracted.resolve() not in path.parents or not path.is_file():
                    raise ValueError(f'缺少对应解压文件：{info.filename}')
                file_sha, _, crc = digest_file(path)
                if crc != info.CRC or path.stat().st_size != info.file_size:
                    raise ValueError(f'解压文件与压缩包不一致：{path.name}')
                allowed_names.add(path)
                if path.suffix.lower() != '.avi':
                    continue
                subset = relative.parts[0]
                if subset not in ('pos', 'neg'):
                    raise ValueError(f'未知数据目录：{subset}')
                counts[subset] += 1
                capture = cv2.VideoCapture(str(path))
                try:
                    if not capture.isOpened():
                        raise ValueError(f'无法读取 {path.name}')
                    fps = capture.get(cv2.CAP_PROP_FPS)
                    declared = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                    if not math.isfinite(fps) or fps <= 0:
                        raise ValueError(f'{path.name} 帧率无效')
                    sample_indices = {max(0, declared // 3): 0, max(0, 2 * declared // 3): 1}
                    samples = [None, None]
                    decoded = 0
                    size = None
                    while True:
                        ok, frame = capture.read()
                        if not ok:
                            break
                        size = [frame.shape[1], frame.shape[0]]
                        if decoded in sample_indices:
                            samples[sample_indices[decoded]] = frame.copy()
                        decoded += 1
                    if not decoded:
                        raise ValueError(f'{path.name} 无可解码帧')
                finally:
                    capture.release()
                filename = path.relative_to(ROOT / 'videos').as_posix()
                registered.append({'filename': filename, 'package': name, 'target_class': definition['target'],
                                   'publisher_subset': subset, 'publisher_target_present': subset == 'pos',
                                   'other_class_annotation': 'unknown', 'sha256': file_sha,
                                   'size_bytes': path.stat().st_size, 'archive_crc_matches': True,
                                   'fps': fps, 'frame_size': size, 'declared_frames': declared,
                                   'decoded_frames': decoded, 'frame_count_matches': decoded == declared,
                                   'decoded_duration_s': round(decoded / fps, 6),
                                   'manual_review': 'pending_full_video_review',
                                   'onset_offset_annotations': None,
                                   'overlap_with_model_training_data': 'not_verified',
                                   'split': 'unassigned'})
                sample_frames[filename] = samples
            if dict(counts) != definition['expected']:
                raise ValueError(f'{name} 正负例数量不符合发布说明：{dict(counts)}')
            extras = [str(p) for p in extracted.rglob('*') if p.is_file() and p.resolve() not in allowed_names]
            if extras:
                raise ValueError(f'解压目录有包外文件：{extras}')
        print(f'{name}: {sum(counts.values())} videos verified and fully decoded', flush=True)
    duplicates = []
    by_hash = {}
    for row in registered:
        by_hash.setdefault(row['sha256'], []).append(row['filename'])
    duplicates = [paths for paths in by_hash.values() if len(paths) > 1]
    manifest = {'dataset': 'FIRESENSE', 'doi': '10.5281/zenodo.836749',
                'source_url': 'https://zenodo.org/records/836749',
                'metadata_url': 'https://zenodo.org/api/records/836749',
                'authors': ['Nikos Grammalidis', 'Kosmas Dimitropoulos', 'Enis Cetin'],
                'license_declared_by_publisher': 'CC-BY-4.0',
                'license_url': 'https://creativecommons.org/licenses/by/4.0/',
                'registered_at_utc': datetime.now(timezone.utc).isoformat(),
                'archives': archives, 'video_count': len(registered), 'exact_duplicate_groups': duplicates,
                'decoded_frames': sum(r['decoded_frames'] for r in registered),
                'decoded_duration_s': round(sum(r['decoded_duration_s'] for r in registered), 6),
                'scope': 'Publisher video-level target labels only; other class, event timing, true risk and training overlap unverified.',
                'videos': registered}
    (ROOT / 'videos' / 'firesense-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, definition in PACKAGES.items():
        for subset in ('pos', 'neg'):
            rows = sorted((r for r in registered if r['package'] == name and r['publisher_subset'] == subset), key=lambda r: r['filename'])
            contact_sheet(rows, sample_frames, output / f'{definition["target"]}-{subset}.jpg', f'FIRESENSE {definition["target"]} {subset}')
    print(json.dumps({'videos': manifest['video_count'], 'frames': manifest['decoded_frames'],
                      'minutes': round(manifest['decoded_duration_s'] / 60, 2),
                      'frame_count_warnings': [r['filename'] for r in registered if not r['frame_count_matches']],
                      'exact_duplicate_groups': duplicates}, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
