"""Bounded, development-only test of a third-party fire/other/smoke model."""
import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from experiments.benchmark_firesense import digest, read, write
from experiments.probe_local_fire import prepare
from experiments.screen_hf_fire_candidates import THRESHOLDS, image_stage, video_stage

REPO = 'Nocluee100/Fire-and-Smoke-Detection-yolov8-v1'
REVISION = '0da45ff9f6d2c52563972f6b5ce2312a7cd02726'
BLOB = '5a3d415bd02de068ee1739a106d132a3ca43ed10'
SIZE = 6257194
MODEL_DIR = ROOT / 'models/candidates' / ('brightlight-' + REVISION[:12])
CODE = ('experiments/screen_brightlight_candidate.py',
        'experiments/screen_hf_fire_candidates.py', 'experiments/fetch_hf_fire_candidates.py',
        'experiments/benchmark_firesense.py')


def fetch():
    import requests
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers['User-Agent'] = 'FireGuard-bounded-model-comparison'
    tree = session.get(f'https://api.github.com/repos/{REPO}/git/trees/{REVISION}',
                       params={'recursive': '1'}, timeout=(10, 30))
    tree.raise_for_status()
    metadata = tree.json()
    item = next(x for x in metadata['tree'] if x['path'] == 'weights/best.pt')
    if metadata['sha'] != REVISION or item['sha'] != BLOB or item['size'] != SIZE:
        raise ValueError('Pinned GitHub metadata changed.')
    write(MODEL_DIR / 'publisher-tree.json', metadata)
    target = MODEL_DIR / 'best.pt'
    if not target.exists():
        response = session.get(f'https://raw.githubusercontent.com/{REPO}/{REVISION}/weights/best.pt',
                               timeout=(10, 45))
        response.raise_for_status()
        payload = response.content
        actual_blob = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
        if len(payload) != SIZE or actual_blob != BLOB:
            raise ValueError('Downloaded weight does not match pinned Git object.')
        partial = target.with_suffix('.pt.partial')
        partial.write_bytes(payload)
        partial.replace(target)
    payload = target.read_bytes()
    if len(payload) != SIZE or hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest() != BLOB:
        raise ValueError('Local weight does not match pinned Git object.')
    for filename in ('LICENSE', 'README.md', 'dataset.txt'):
        response = session.get(f'https://raw.githubusercontent.com/{REPO}/{REVISION}/{filename}', timeout=(10, 30))
        response.raise_for_status()
        (MODEL_DIR / filename).write_bytes(response.content)
    manifest = {'repo_id': REPO, 'revision': REVISION, 'filename': 'best.pt',
                'size_bytes': SIZE, 'git_blob_sha1': BLOB, 'sha256': digest(target),
                'source_url': f'https://github.com/{REPO}',
                'license_declared_by_publisher': 'MIT for weights and scripts; dataset.txt declares CC BY 4.0',
                'license_note': 'Upstream obligations and training source/version need review before distribution. README and dataset.txt link different datasets.',
                'downloaded_at_utc': datetime.now(timezone.utc).isoformat(),
                'document_sha256': {n: digest(MODEL_DIR / n) for n in ('LICENSE', 'README.md', 'dataset.txt')}}
    write(MODEL_DIR / 'source-manifest.json', manifest)
    print(json.dumps({'downloaded': str(target), 'sha256': manifest['sha256']}, ensure_ascii=False), flush=True)
    return manifest


def canonical_targets(detections):
    """Keep fire/smoke even if an other box overlaps; never route other to risk."""
    output = []
    for box in detections:
        if box['label'] not in ('fire', 'smoke', 'other'):
            raise ValueError('Unexpected candidate label: ' + box['label'])
        if box['label'] != 'other':
            output.append({**box, 'class_id': {'smoke': 0, 'fire': 1}[box['label']],
                           'bbox_xyxy': list(box['bbox_xyxy'])})
    return output


class ThreeClassCandidate:
    def __init__(self, weights):
        import torch
        from ultralytics import YOLO
        self.device = '0' if torch.cuda.is_available() else 'cpu'
        self.model = YOLO(str(weights))
        if set(self.model.names.values()) != {'fire', 'smoke', 'other'}:
            raise ValueError('Expected exactly fire, smoke, other: ' + str(self.model.names))
        self.context = None
        self.stream = None
        self.calls = 0

    def predict(self, frame, *, annotate=False):
        result = self.model.predict(frame, device=self.device, conf=.15, imgsz=640, verbose=False)[0]
        all_boxes = []
        for x1, y1, x2, y2, confidence, class_id in result.boxes.data.cpu().tolist():
            all_boxes.append({'class_id': int(class_id), 'label': self.model.names[int(class_id)],
                              'confidence': round(confidence, 6),
                              'bbox_xyxy': [round(v, 2) for v in (x1, y1, x2, y2)]})
        if self.stream is not None:
            context = next(self.context)
            self.stream.write(json.dumps({**context, 'frame_size': [frame.shape[1], frame.shape[0]],
                                          'detections': all_boxes}) + '\n')
            self.calls += 1
        return None, canonical_targets(all_boxes)


def false_reduction_gates(result):
    reference = result['cases']['reference']['0.35']
    before = {c['filename']: c['metrics'] for c in reference}
    output = {}
    for threshold in THRESHOLDS:
        key = str(threshold)
        changes = []
        for case in result['cases']['candidate'][key]:
            for label in ('fire', 'smoke'):
                old, new = before[case['filename']][label], case['metrics'][label]
                if old['excluded']:
                    continue
                old_missed, new_missed = old['temporal']['missed_intervals_frames'], new['temporal']['missed_intervals_frames']
                lost = [x for x in new_missed if x not in old_missed]
                recovered = [x for x in old_missed if x not in new_missed]
                if lost or recovered:
                    changes.append({'filename': case['filename'], 'label': label, 'lost': lost, 'recovered': recovered})
        totals = result['aggregate']['candidate'][key]
        checks = {'strictly_fewer_confirmed_fire_false_episodes': totals['fire']['temporal']['false_hint_episodes'] < 3,
                  'no_more_confirmed_smoke_false_episodes': totals['smoke']['temporal']['false_hint_episodes'] <= 1,
                  'no_previously_detected_event_lost': not any(x['lost'] for x in changes)}
        output[key] = {'checks': checks, 'passed': all(checks.values()), 'changes': changes}
    return output


def main():
    import numpy as np
    from core.detector import Detector
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=('fetch', 'images', 'videos'), required=True)
    args = parser.parse_args()
    if args.stage == 'fetch':
        fetch()
        return
    source = read(MODEL_DIR / 'source-manifest.json')
    weights = MODEL_DIR / 'best.pt'
    if source['revision'] != REVISION or digest(weights) != source['sha256']:
        raise ValueError('Candidate source or weight changed.')
    extra = {'scope': 'Development only: 27 reviewed images or all 30 development videos. No holdout, training, or app changes.',
             'policy': 'Whole-frame three-class model; preserve other predictions for review but send only fire/smoke with canonical IDs to unchanged temporal logic. No other-overlap veto.',
             'config': {'confidence': .15}, 'crop_cut_edge_policy': None, 'prior_art': [],
             'originality_note': 'Third-party model comparison, not an original architecture.',
             'candidate_source': source, 'candidate_weights_sha256': source['sha256'],
             'primary_operating_point': .35, 'diagnostic_thresholds': THRESHOLDS,
             'image_screen_gate': 'Image TP gain is not required: this task targets persistent false alarms; run all 30 development videos regardless.',
             'video_adoption_gate': 'Confirmed fire false episodes strictly below baseline 3; smoke false episodes <=1; no previously detected event lost in either class. Any numeric pass remains development-only and requires localization/new-source checks.',
             'red_object_scope': 'Existing development scenes include red equipment/obstructions, but there is no standalone red-object negative benchmark or causal color attribution.',
             'confidence_note': 'Four points filter the same conf=0.15 post-NMS log; not four independent runs or calibrated probabilities.'}
    folder, plan, proposal, review = prepare('brightlight-' + args.stage, extra, CODE)
    shutil.copyfile(MODEL_DIR / 'source-manifest.json', folder / 'source-manifest.json')
    try:
        candidate = ThreeClassCandidate(weights)
        for _ in range(3):
            candidate.predict(np.zeros((640, 640, 3), dtype=np.uint8))
        if args.stage == 'images':
            rows = read(folder / 'box-annotations-snapshot.json')['images']
            fps = {e['filename']: e['fps'] for e in plan['entries']}
            contexts = ({'source': r['source'], 'image': r['image'], 'frame_index': r['frame_index'],
                         'timestamp_s': round(r['frame_index'] / fps[r['source']], 6)} for r in rows)
            expected_calls = len(rows)
        else:
            entries = [e for e in plan['entries'] if e['split'] == 'development']
            contexts = ({'source': e['filename'], 'frame_index': i, 'timestamp_s': round(i/e['fps'], 6)}
                        for e in entries for i in range(e['decoded_frames']))
            expected_calls = sum(e['decoded_frames'] for e in entries)
        candidate.context = iter(contexts)
        raw_path = folder / 'all-three-class-detections.jsonl'
        with raw_path.open('w', encoding='utf-8') as stream:
            candidate.stream = stream
            if args.stage == 'images':
                reference = Detector(ROOT / 'models/best.pt', confidence=.15)
                for _ in range(3):
                    reference.predict(np.zeros((640, 640, 3), dtype=np.uint8), annotate=False)
                image_stage(folder, plan, reference, candidate)
            else:
                video_stage(folder, plan, candidate, review)
        candidate.stream = None
        if candidate.calls != expected_calls or next(candidate.context, None) is not None:
            raise ValueError('Prediction/context count mismatch.')
        result = read(folder / 'results.json')
        if args.stage == 'videos':
            result['legacy_recovery_gates'] = result.pop('gates')
            result['false_reduction_gates'] = false_reduction_gates(result)
            result['primary_operating_point'] = .35
            result['decision'] = 'Primary false-reduction gate passed; independent/source-localization review still required' if result['false_reduction_gates']['0.35']['passed'] else 'Primary false-reduction gate failed; do not replace default'
        result['all_three_class_log_sha256'] = digest(raw_path)
        result['adopted'] = False
        write(folder / 'results.json', result)
        if digest(ROOT / 'models/best.pt') != plan['weights_sha256'] or digest(weights) != source['sha256']:
            raise ValueError('Weights changed during experiment.')
        for name, expected in proposal['code_sha256'].items():
            if digest(ROOT / name) != expected:
                raise ValueError('Experiment code changed during execution: ' + name)
        write(folder / 'run-manifest.json', {'status': 'completed', 'device': candidate.device,
              'candidate_names': candidate.model.names, 'candidate_parameters': sum(p.numel() for p in candidate.model.model.parameters()),
              'proposal_sha256': digest(folder / 'proposal.json'), 'results_sha256': digest(folder / 'results.json'),
              'all_three_class_log_sha256': digest(raw_path), 'prediction_calls': candidate.calls,
              'completed_at_utc': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'output': str(folder), 'aggregate': result['aggregate'],
                          'false_reduction_gates': result.get('false_reduction_gates'),
                          'decision': result.get('decision')}, ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'error': str(exc), 'proposal_sha256': digest(folder / 'proposal.json')})
        raise


if __name__ == '__main__':
    main()
