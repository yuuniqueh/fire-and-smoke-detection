"""Check benchmark drawing bypass against the existing real-model reference."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()


def main():
    import cv2
    from core.detector import Detector
    cached = json.loads((ROOT / 'outputs/firesense-initial-check.json').read_text(encoding='utf-8'))['results'][0]
    reference = [json.loads(line) for line in (Path(cached['output']) / 'detections.jsonl').read_text(encoding='utf-8').splitlines()][:30]
    detector = Detector(ROOT / 'models/best.pt', confidence=.35)
    capture = cv2.VideoCapture(str(ROOT / 'videos' / cached['source']))
    try:
        for packet in reference:
            ok, frame = capture.read()
            assert ok
            annotated, detections = detector.predict(frame, annotate=False)
            assert annotated is None and detections == packet['detections']
        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = capture.read()
        annotated, detections = detector.predict(frame)
        assert ok and annotated is not None and detections == reference[0]['detections']
    finally:
        capture.release()
    result = {'unannotated_frames_match_reference': 30, 'default_annotated_call_preserved': True,
              'device': detector.device, 'source': cached['source']}
    (ROOT / 'outputs/unannotated-inference-verification.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
