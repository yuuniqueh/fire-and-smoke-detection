"""Find raw-image similarity constraints; feature matches are not source provenance."""
import json
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import cv2
import numpy as np


def main():
    manifest = json.loads((ROOT / 'videos/firesense-manifest.json').read_text(encoding='utf-8'))
    rows = [row for row in manifest['videos'] if row['frame_count_matches']]
    features = {}
    orb = cv2.ORB_create(nfeatures=700)
    for row in rows:
        cap = cv2.VideoCapture(str(ROOT / 'videos' / row['filename']))
        samples = []
        try:
            for fraction in (1 / 3, 2 / 3):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(row['decoded_frames'] * fraction))
                ok, frame = cap.read()
                if not ok:
                    raise ValueError(f'Cannot read sample: {row["filename"]}')
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                scale = min(640 / gray.shape[1], 480 / gray.shape[0], 1)
                gray = cv2.resize(gray, None, fx=scale, fy=scale)
                mask = np.full(gray.shape, 255, dtype=np.uint8)
                mask[:max(20, gray.shape[0] // 10)] = 0  # avoid header overlays as the main match
                points, descriptors = orb.detectAndCompute(gray, mask)
                samples.append((points, descriptors, gray.shape))
        finally:
            cap.release()
        features[row['filename']] = samples
    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = []
    for left, right in combinations(features, 2):
        best = {'inliers': 0, 'area_fraction_min': 0.0}
        for p1, d1, size1 in features[left]:
            for p2, d2, size2 in features[right]:
                if d1 is None or d2 is None or len(d1) < 4 or len(d2) < 4:
                    continue
                good = [pair[0] for pair in matcher.knnMatch(d1, d2, k=2) if len(pair) == 2 and pair[0].distance < .75 * pair[1].distance]
                if len(good) < 6:
                    continue
                a = np.float32([p1[item.queryIdx].pt for item in good])
                b = np.float32([p2[item.trainIdx].pt for item in good])
                _, mask = cv2.findHomography(a, b, cv2.RANSAC, 4.0)
                if mask is None:
                    continue
                selected = mask.ravel().astype(bool)
                count = int(selected.sum())
                if count < 4:
                    continue
                area = min(float(np.prod(np.ptp(points[selected], axis=0))) / (size[0] * size[1])
                           for points, size in ((a, size1), (b, size2)))
                if count > best['inliers']:
                    best = {'inliers': count, 'area_fraction_min': round(area, 4)}
        if best['inliers'] >= 18 and best['area_fraction_min'] >= .06:
            pairs.append({'files': [left, right], **best, 'status': 'visual_similarity_not_provenance'})
    pairs.sort(key=lambda row: row['inliers'], reverse=True)
    result = {'method': 'Two raw sampled frames; ORB ratio matches + RANSAC; >=18 inliers and >=6% image area in both images.',
              'purpose': 'Conservative keep-together constraints only, not proof of original capture identity or training independence.',
              'model_inference_performed': False, 'videos_examined': len(rows), 'pairs': pairs}
    output = ROOT / 'outputs/firesense-similarity.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
