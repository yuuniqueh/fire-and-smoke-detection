"""Post-run localization, false-example, and matched-event latency review."""
import json
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.evaluation import episodes
from experiments.benchmark_firesense import read, write
from experiments.probe_local_fire import BASELINE
from experiments.probe_class_routing import indices, verified_run
from experiments.screen_hf_fire_candidates import load_packets

NEW = ROOT / 'outputs/local-fire-brightlight-videos-20261009-210518-006667'
COMBO = ROOT / 'outputs/local-fire-class-route-live-20261009-195540-326777'


def draw(frame, boxes, title):
    import cv2
    import numpy as np
    canvas = frame.copy()
    for box in boxes:
        x1, y1, x2, y2 = map(round, box['bbox_xyxy'])
        color = {'fire': (0, 140, 255), 'smoke': (230, 230, 230), 'other': (40, 230, 40)}[box['label']]
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        cv2.putText(canvas, box['label'] + ' ' + str(round(box['confidence'], 2)),
                    (x1, max(14, y1-4)), cv2.FONT_HERSHEY_SIMPLEX, .4, color, 1)
    h, w = canvas.shape[:2]
    scale = min(400/w, 270/h)
    out = np.zeros((300, 400, 3), dtype=np.uint8)
    small = cv2.resize(canvas, (round(w*scale), round(h*scale)))
    out[30:30+small.shape[0], :small.shape[1]] = small
    cv2.putText(out, title, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, .45, (255, 255, 255), 1)
    return out


def frame_at(source, index):
    import cv2
    cap = cv2.VideoCapture(str(ROOT / 'videos' / source))
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, index)
        ok, frame = cap.read()
        if not ok:
            raise ValueError('QA decode failed: ' + source)
        return frame
    finally:
        cap.release()


def event_delays(row, metrics):
    truth = [False] * row['decoded_frames']
    for interval in row['intervals']:
        if interval['label'] == 'fire':
            a, b = interval['start_frame'], interval['end_frame_exclusive']
            truth[a:b] = [True] * (b-a)
    ground = episodes(truth, row['fps'])
    present = [e for e in ground if e not in metrics['missed_intervals_frames']]
    assert len(present) == len(metrics['first_active_hint_delays_s'])
    return {tuple(e): d for e, d in zip(present, metrics['first_active_hint_delays_s'])}


def main():
    import cv2
    import numpy as np
    result, _ = verified_run(NEW)
    verified_run(COMBO)
    rows = {r['filename']: r for r in read(NEW / 'review-snapshot.json')['videos']}
    original = {c['filename']: c for c in result['cases']['reference']['0.35']}
    shared = []
    for case in result['cases']['candidate']['0.35']:
        source = case['filename']
        a = event_delays(rows[source], original[source]['metrics']['fire']['temporal'])
        b = event_delays(rows[source], case['metrics']['fire']['temporal'])
        for event in a.keys() & b.keys():
            shared.append({'source': source, 'interval_frames': list(event),
                           'original_delay_s': a[event], 'candidate_delay_s': b[event]})
    summary = {'shared_fire_events': shared,
               'shared_event_mean_delay_s': {mode: sum(c[mode+'_delay_s'] for c in shared)/len(shared) for mode in ('original', 'candidate')},
               'fire_positive_frame_coverage': {mode: result['aggregate'][mode]['0.35']['fire']['temporal']['true_positive_hint_frames']/7317 for mode in ('reference', 'candidate')},
               'scope': 'Post-run development diagnostics; no threshold or label changes.'}
    raw = load_packets(NEW / 'all-three-class-detections.jsonl')
    raw_index = {(p['source'], p['frame_index']): p for p in raw}
    assert len(raw_index) == 28611
    old_idx, combo_idx = indices(BASELINE), indices(COMBO)
    qa = NEW / 'qa'
    qa.mkdir(exist_ok=True)
    focus = [('headlights-12', 'fire_videos.1406/neg/negsVideo2.859.avi', 12),
             ('headlights-69', 'fire_videos.1406/neg/negsVideo2.859.avi', 69),
             ('reflection-629', 'fire_videos.1406/neg/negsVideo9.866.avi', 629),
             ('recovered-fire-154', 'smoke_videos.1407/pos/testpos08.824.avi', 154),
             ('recovered-fire-180', 'smoke_videos.1407/pos/testpos08.824.avi', 180),
             ('recovered-fire-270', 'smoke_videos.1407/pos/testpos08.824.avi', 270),
             ('red-device-0', 'smoke_videos.1407/pos/testpos02.818.avi', 0),
             ('red-device-330', 'smoke_videos.1407/pos/testpos02.818.avi', 330)]
    qa_rows = []
    for name, source, index in focus:
        frame = frame_at(source, index)
        a = load_packets(old_idx[source] / 'detections.jsonl')[index]
        b = load_packets(combo_idx[source] / 'detections.jsonl')[index]
        c = raw_index[(source, index)]
        assert a['frame_index'] == b['frame_index'] == c['frame_index'] == index
        selected = [x for x in c['detections'] if x['confidence'] >= .35]
        panel = np.hstack([draw(frame, a['detections'], 'Original .35 / f' + str(index)),
                           draw(frame, b['detections'], 'Previous UAV fire combination'),
                           draw(frame, selected, 'New candidate .35 (green=other)')])
        cv2.imwrite(str(qa / (name + '.png')), panel)
        qa_rows.append({'name': name, 'source': source, 'frame_index': index,
                        'reference': a['detections'], 'previous_combination': b['detections'],
                        'candidate_all_035': selected})
    summary['qa'] = qa_rows
    write(NEW / 'diagnostics.json', summary)
    false_samples = []
    for case in result['cases']['candidate']['0.35']:
        for label in ('fire', 'smoke'):
            m = case['metrics'][label]
            if m['excluded']:
                continue
            for interval in m['temporal']['false_hint_intervals_frames']:
                source, index = case['filename'], interval[0]
                frame = frame_at(source, index)
                selected = [x for x in raw_index[(source, index)]['detections'] if x['confidence'] >= .35]
                filename = f'new-false-{len(false_samples)+1:02d}.png'
                cv2.imwrite(str(qa / filename), draw(frame, selected, f'{Path(source).stem} f{index}'))
                false_samples.append({'source': source, 'label': label, 'interval_frames': interval,
                                      'frame_index': index, 'current_detections': selected, 'image': filename})
    write(NEW / 'false-sample-review.json', false_samples)
    panels = [cv2.imread(str(qa / s['image'])) for s in false_samples]
    if panels:
        while len(panels) % 3:
            panels.append(np.zeros_like(panels[0]))
        cv2.imwrite(str(qa / 'new-false-contact-sheet.png'), np.vstack([np.hstack(panels[i:i+3]) for i in range(0, len(panels), 3)]))
    print(json.dumps({k: summary[k] for k in ('shared_event_mean_delay_s', 'fire_positive_frame_coverage')}, indent=2))
    print('QA_DIRECTORY ' + str(qa))


if __name__ == '__main__':
    main()
