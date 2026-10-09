"""Replay identical detection outputs; synthetic cases are rule checks only."""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.runtime import ROOT, configure_runtime
from core.temporal import EventTracker, TemporalConfig


def compare(rows, config, end_timestamp, close_reason='stream_end'):
    tracker = EventTracker(config)
    baseline_episodes = Counter()
    previous_labels = set()
    snapshots = []
    detected_frames = Counter()
    for row in rows:
        labels = {item['label'] for item in row['detections']}
        for label in labels:
            detected_frames[label] += 1
        for label in labels - previous_labels:
            baseline_episodes[label] += 1
        previous_labels = labels
        snapshots.append(tracker.update(row['timestamp_s'], row['detections'], row['frame_size']))
    tracker.finish(end_timestamp, close_reason)
    return {
        'frame_count': len(rows), 'single_frame_trigger_frames': dict(detected_frames),
        'single_frame_detection_episodes': dict(baseline_episodes),
        'temporal_summary': tracker.summary(), 'events': tracker.closed_events,
        'baseline_definition': 'Per class: a nonempty frame triggers immediately; consecutive positive frames form one episode. No cooldown. Counts are not an accuracy metric.',
    }, snapshots


def scripted_cases(config):
    results = []
    box = [100, 100, 220, 240]
    descriptions = [
        ('brief_false_positive', 'Scripted false-positive flash lasting 0.2 s.', lambda i: i < 2),
        ('sustained_target', 'Scripted sustained target lasting 3 s.', lambda i: True),
        ('target_with_dropout', 'Scripted target with 0.4 s missing observations.', lambda i: not 12 <= i <= 15),
        ('sustained_false_positive', 'Scripted persistent distractor; temporal consistency cannot distinguish it.', lambda i: True),
        ('empty_input', 'Scripted empty detection sequence.', lambda i: False),
    ]
    for name, description, presence in descriptions:
        rows = [{'timestamp_s': i/10, 'frame_size': [640, 480],
                 'detections': [{'label': 'fire', 'confidence': 0.9, 'bbox_xyxy': box}] if presence(i) else []}
                for i in range(30)]
        report, _ = compare(rows, config, 3.0)
        results.append({'case': name, 'description': description,
                        'evidence_type': 'Synthetic scripted detections, NOT model or real-world performance data.', **report})
    return results


def main():
    configure_runtime()
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='append', default=[], help='Existing run directory containing detections.jsonl/config.json')
    parser.add_argument('--config', default=str(ROOT/'configs'/'temporal.json'))
    parser.add_argument('--output', default=str(ROOT/'outputs'/'temporal-comparison.json'))
    args = parser.parse_args()
    config = TemporalConfig(**json.loads(Path(args.config).read_text(encoding='utf-8')))
    replay_results = []
    for path in args.run:
        run = Path(path).resolve()
        run_config = json.loads((run/'config.json').read_text(encoding='utf-8'))
        rows = [json.loads(line) for line in (run/'detections.jsonl').read_text(encoding='utf-8').splitlines()]
        size = run_config.get('frame_size')
        if not size:
            import cv2
            cap = cv2.VideoCapture(run_config['source'])
            size = [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]
            cap.release()
        for row in rows:
            row.setdefault('frame_size', size)
        end = rows[-1]['timestamp_s'] + 1/run_config['source_fps'] if rows else 0.0
        report, snapshots = compare(rows, config, round(end, 6))
        replay_results.append({'source_run': str(run), 'source_video': run_config['source'],
                               'evidence_type': 'Replay of measured detections; no ground-truth accuracy labels.', **report})
        (run/'temporal-replay.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in snapshots),encoding='utf-8')
    report = {'temporal_config': json.loads(Path(args.config).read_text(encoding='utf-8')),
              'scripted_rule_checks': scripted_cases(config), 'measured_detection_replays': replay_results,
              'limitations': 'Scripted cases verify behavior only. Measured replays have no independent event ground truth; do not infer false-alarm reduction or accuracy gains.'}
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for item in report['scripted_rule_checks']:
        print(item['case'], 'single-frame episodes:',item['single_frame_detection_episodes'], 'temporal:',item['temporal_summary'])
    for item in replay_results:
        print(Path(item['source_video']).name, 'single-frame episodes:',item['single_frame_detection_episodes'], 'temporal:',item['temporal_summary'])
    print(target)


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    main()
