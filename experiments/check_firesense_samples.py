"""Four predefined integration samples, without tuning or accuracy claims."""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.pipeline import AnalysisOptions, analyze_video

SAMPLES = [
    'fire_videos.1406/pos/posVideo1.868.avi',
    'fire_videos.1406/neg/negsVideo1.858.avi',
    'smoke_videos.1407/pos/testpos01.817.avi',
    'smoke_videos.1407/neg/testneg01.807.avi',
]


def main():
    import cv2
    manifest = json.loads((ROOT / 'videos/firesense-manifest.json').read_text(encoding='utf-8'))
    by_name = {item['filename']: item for item in manifest['videos']}
    results = []
    for filename in SAMPLES:
        registered = by_name[filename]
        row = {'source': filename, 'source_sha256': registered['sha256'],
               'publisher_target': registered['target_class'],
               'publisher_target_present': registered['publisher_target_present']}
        print(f'BEGIN {filename}', flush=True)
        try:
            outcome = analyze_video(AnalysisOptions(source=str(ROOT / 'videos' / filename)), ROOT / 'models/best.pt')
            folder = Path(outcome['output'])
            detected = Counter()
            log_frames = 0
            for line in (folder / 'detections.jsonl').read_text(encoding='utf-8').splitlines():
                packet = json.loads(line)
                detected.update(set(item['label'] for item in packet['detections']))
                log_frames += 1
            events = json.loads((folder / 'events.json').read_text(encoding='utf-8'))
            confirmed = Counter(item['label'] for item in events['events'] if item.get('confirmed_at_s') is not None)
            capture = cv2.VideoCapture(str(folder / 'annotated.mp4'))
            output_frames = 0
            try:
                while capture.read()[0]:
                    output_frames += 1
            finally:
                capture.release()
            count = outcome['summary']['processed_frames']
            row.update(outcome)
            row.update({'frames_with_detection_by_class': dict(detected),
                        'confirmed_events_by_class': dict(confirmed),
                        'decoded_output_frames': output_frames,
                        'checks': {'source_frames_processed': count == registered['decoded_frames'],
                                   'detection_log_complete': log_frames == count,
                                   'output_video_complete': output_frames == count,
                                   'evidence_images_exist': all((folder / item['evidence_image']).is_file()
                                       for item in events['events'] if item.get('evidence_image'))}})
        except Exception as exc:
            row['error'] = str(exc)
        results.append(row)
        report = {'scope': 'Four first-numbered samples predefined before inference. Integration check only; no onset labels or independent accuracy evaluation.',
                  'configuration': 'Unchanged default model, confidence 0.35, temporal and risk settings; auto device; no playback pacing.',
                  'results': results}
        (ROOT / 'outputs/firesense-initial-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(row, ensure_ascii=False), flush=True)
    if any('error' in row or not all(row['checks'].values()) for row in results):
        raise SystemExit('One or more checks failed; inspect firesense-initial-check.json.')


if __name__ == '__main__':
    main()
