"""Inspect developer-side misses only; don't design fixes from holdout cases."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    folder = Path(parser.parse_args().directory).resolve()
    if (ROOT / 'outputs').resolve() not in folder.parents:
        raise ValueError('Expected project benchmark folder.')
    result = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    plan = json.loads((folder / 'plan.json').read_text(encoding='utf-8'))
    rows = {row['filename']: row for row in json.loads((folder / 'review-snapshot.json').read_text(encoding='utf-8'))['videos']}
    by_folder = {json.loads((path / 'case.json').read_text(encoding='utf-8'))['filename']: path for path in folder.glob('case-*')}
    failures = []
    for case in result['cases']:
        if case['split'] != 'development':
            continue
        row = rows[case['filename']]
        path = by_folder[case['filename']]
        detections = [json.loads(line) for line in (path / 'detections.jsonl').read_text(encoding='utf-8').splitlines()]
        temporal = [json.loads(line) for line in (path / 'temporal.jsonl').read_text(encoding='utf-8').splitlines()]
        for label, metrics in case['metrics'].items():
            if metrics['excluded']:
                continue
            for start, end in metrics['temporal']['missed_intervals_frames']:
                visible = {frame for interval in row['intervals'] if interval['label'] == label
                           for frame in range(max(start, interval['start_frame']), min(end, interval['end_frame_exclusive']))}
                positive_frames = 0
                ids = set()
                max_margin = max_confidence = max_positive_s = 0.0
                for index in sorted(visible):
                    boxes = [box for box in detections[index]['detections'] if box['label'] == label]
                    positive_frames += bool(boxes)
                    max_confidence = max([max_confidence] + [box['confidence'] for box in boxes])
                    for track in temporal[index]['tracks']:
                        if track['label'] != label:
                            continue
                        ids.add(track['event_id'])
                        max_positive_s = max(max_positive_s, track['positive_s'])
                        if track['present_this_frame']:
                            cfg = plan['temporal_config']
                            margin = min(track['observation_s'] / cfg['min_observation_s'],
                                         track['positive_s'] / cfg['min_positive_s'],
                                         track['presence_ratio'] / cfg['min_presence_ratio'])
                            max_margin = max(max_margin, margin)
                failures.append({'source': case['filename'], 'label': label, 'missed_interval_frames': [start, end],
                                 'visible_frames': len(visible), 'raw_positive_frames': positive_frames,
                                 'raw_detected_time_s': round(positive_frames / row['fps'], 6),
                                 'max_model_confidence': max_confidence, 'associated_candidate_ids': len(ids),
                                 'max_track_positive_s': max_positive_s, 'max_joint_confirmation_margin': round(max_margin, 6),
                                 'classification': 'detector_has_no_visible_frame_support' if positive_frames == 0 else 'raw_support_present_but_no_confirmed_state'})
    output = {'scope': 'Development-side missed events only. Observations from frozen logs, not a causal diagnosis or label correction.',
              'margin_note': 'Joint evidence ratio min(observation/min_observation, positive/min_positive, presence/min_presence) on frames with current evidence; all must reach 1.',
              'cases': failures}
    (folder / 'development-failures.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
