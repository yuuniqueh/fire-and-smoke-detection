"""Raw evidence for developmental misses; labels remain untouched."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
from PIL import Image, ImageDraw, ImageFont

CASES = [
    ('case-25-testpos02.818', 'smoke_videos.1407/pos/testpos02.818.avi', [0, 200, 450, 650, 774], 'fire'),
    ('case-26-testpos04.820', 'smoke_videos.1407/pos/testpos04.820.avi', [1100, 1200, 1480, 1700, 1920], 'fire'),
    ('case-27-testpos06.822', 'smoke_videos.1407/pos/testpos06.822.avi', [150, 177, 185, 195, 200], 'fire'),
    ('case-29-testpos08.824', 'smoke_videos.1407/pos/testpos08.824.avi', [123, 160, 210, 260, 304], 'fire'),
    ('case-15-posVideo3.872', 'fire_videos.1406/pos/posVideo3.872.avi', [0, 80, 160, 260, 380], 'smoke'),
]


def main():
    output = ROOT / 'outputs/development-visual-review'
    output.mkdir(exist_ok=True)
    benchmark = ROOT / 'outputs/benchmark-v1-20261006-203720-465883'
    font = ImageFont.truetype('C:/Windows/Fonts/arial.ttf', 18)
    for case, filename, indices, label in CASES:
        packets = [json.loads(line) for line in (benchmark / case / 'detections.jsonl').read_text(encoding='utf-8').splitlines()]
        peak = max(indices + list(range(min(indices), max(indices) + 1)),
                   key=lambda i: max([0] + [box['confidence'] for box in packets[i]['detections'] if box['label'] == label]))
        indices = sorted(set(indices + [peak]))
        sheet = Image.new('RGB', (3 * 500, 65 + ((len(indices) + 2) // 3) * 345), '#101a2b')
        draw = ImageDraw.Draw(sheet)
        draw.text((12, 12), filename + ' | model boxes below, not ground truth', font=font, fill='white')
        capture = cv2.VideoCapture(str(ROOT / 'videos' / filename))
        try:
            for position, index in enumerate(indices):
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, frame = capture.read()
                if not ok:
                    raise ValueError(f'Frame not readable: {filename} {index}')
                for box in packets[index]['detections']:
                    x1, y1, x2, y2 = map(round, box['bbox_xyxy'])
                    color = (0, 160, 255) if box['label'] == 'fire' else (220, 190, 0)
                    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                    cv2.putText(frame, f'{box["label"]} {box["confidence"]:.2f}', (x1, max(18, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, .6, color, 2)
                preview = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
                preview.thumbnail((485, 295))
                x, y = (position % 3) * 500 + 7, 65 + (position // 3) * 345
                sheet.paste(preview, (x, y))
                draw.text((x, y + 302), f'frame {index} | {packets[index]["timestamp_s"]:.3f}s', font=font, fill='white')
        finally:
            capture.release()
        sheet.save(output / f'{case}.jpg')
        print(output / f'{case}.jpg')


if __name__ == '__main__':
    main()
