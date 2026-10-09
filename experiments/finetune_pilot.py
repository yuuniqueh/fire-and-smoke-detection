"""One predeclared local fine-tune plus fixed-confidence developmental comparison."""
import contextlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['CUBLAS_WORKSPACE_CONFIG'] = ':4096:8'
from core.runtime import configure_runtime
configure_runtime()
from core.box_metrics import count_boxes, sum_counts
from core.temporal import EventTracker, TemporalConfig
from core.evaluation import evaluate_video
from experiments.benchmark_firesense import aggregate, digest, read, write

PARAMETERS = dict(epochs=30, batch=4, imgsz=640, device=0, workers=0,
                  optimizer='AdamW', lr0=.0001, lrf=.1, cos_lr=True, momentum=.9,
                  weight_decay=.0005, warmup_epochs=1., warmup_bias_lr=.0001,
                  freeze=10, patience=0, seed=20261006, deterministic=True,
                  amp=False, mosaic=0., mixup=0., copy_paste=0., close_mosaic=0,
                  hsv_h=.01, hsv_s=.1, hsv_v=.1, translate=.05, scale=.2,
                  fliplr=.5, flipud=0., plots=False, val=True, cache=False,
                  save=True, verbose=False)


def local_only_training_setup():
    from ultralytics.utils import callbacks
    from ultralytics.data import utils as data_utils
    # No cloud integrations and no downloading font/AMP test models.
    callbacks.add_integration_callbacks = lambda instance: None
    font = Path('C:/Windows/Fonts/arial.ttf')
    if not font.is_file():
        raise ValueError('Local system Arial font unavailable.')
    data_utils.check_font = lambda *args, **kwargs: font


def cohort_totals(cases):
    return {label: {mode: aggregate(cases, 'development', label, mode) for mode in ('single_frame', 'temporal')}
            for label in ('fire', 'smoke')}


def compare(folder, weights, proposal, package, plan):
    import cv2
    from core.detector import Detector
    detector = Detector(weights, confidence=.35)
    baseline_detector = Detector(ROOT / 'models/best.pt', confidence=.35)
    boxed = []
    for row in package['images']:
        source = ROOT / 'datasets/firesense-pilot-v1' / row['dataset_image']
        frame = cv2.imread(str(source))
        methods = {}
        for method, adapter in [('original', baseline_detector), ('finetuned', detector)]:
            _, detections = adapter.predict(frame, annotate=False)
            methods[method] = {'detections': detections, 'counts': count_boxes(row['boxes'], detections)}
        boxed.append({'image': row['dataset_image'], 'split': row['dataset_split'], **methods})
    box_totals = {split: {method: sum_counts([row[method]['counts'] for row in boxed if row['split'] == split])
                         for method in ('original', 'finetuned')} for split in ('train', 'val')}
    write(folder / 'box-comparison.json', {'protocol': 'confidence>=0.35; image640; same-class greedy IoU>=0.5; counts not mAP',
                                          'aggregate': box_totals, 'images': boxed})
    del baseline_detector
    snapshot = read(ROOT / 'data/firesense/evaluation-v1' / plan['review_snapshot'])
    rows = {row['filename']: row for row in snapshot['videos']}
    entries = [entry for entry in plan['entries'] if entry['split'] == 'development']
    original = read(ROOT / 'outputs/benchmark-v1-20261006-203720-465883/results.json')
    original_cases = {case['filename']: case for case in original['cases'] if case['split'] == 'development'}
    cases = []
    video_folder = folder / 'development-videos'
    video_folder.mkdir()
    for number, entry in enumerate(entries, 1):
        source = ROOT / 'videos' / entry['filename']
        if digest(source) != entry['source_sha256']:
            raise ValueError('Source changed.')
        print(f'VIDEO BEGIN {number}/30 {source.name}', flush=True)
        case_folder = video_folder / f'case-{number:02d}-{source.stem}'
        case_folder.mkdir()
        capture = cv2.VideoCapture(str(source))
        tracker = EventTracker(TemporalConfig(**plan['temporal_config']))
        packets, snapshots = [], []
        try:
            with (case_folder / 'detections.jsonl').open('w', encoding='utf-8') as detlog, \
                    (case_folder / 'temporal.jsonl').open('w', encoding='utf-8') as templog:
                while True:
                    ok, frame = capture.read()
                    if not ok:
                        break
                    _, detections = detector.predict(frame, annotate=False)
                    packet = {'frame_index': len(packets), 'timestamp_s': round(len(packets) / entry['fps'], 6),
                              'frame_size': [frame.shape[1], frame.shape[0]], 'detections': detections}
                    temporal = tracker.update(packet['timestamp_s'], detections, packet['frame_size'])
                    packets.append(packet)
                    snapshots.append(temporal)
                    detlog.write(json.dumps(packet) + '\n')
                    templog.write(json.dumps(temporal) + '\n')
            if len(packets) != entry['decoded_frames']:
                raise ValueError('Incomplete video decode.')
        finally:
            capture.release()
        role = proposal['video_roles'][entry['filename']]
        case = {**entry, 'trial_role': role,
                'metrics': evaluate_video(rows[entry['filename']], packets, snapshots, plan['episode_merge_gap_s']),
                'log_sha256': {name: digest(case_folder / name) for name in ('detections.jsonl', 'temporal.jsonl')}}
        cases.append(case)
        write(case_folder / 'case.json', case)
        print(f'VIDEO END {number}/30 frames={len(packets)}', flush=True)
    totals = {}
    regressions = []
    for role in ('training_groups', 'epoch_selection_groups', 'other_development', 'all_development'):
        current = [c for c in cases if role == 'all_development' or c['trial_role'] == role]
        before = [original_cases[c['filename']] for c in current]
        totals[role] = {'videos': len(current), 'original': cohort_totals(before), 'finetuned': cohort_totals(current)}
    for case in cases:
        if case['trial_role'] != 'other_development':
            continue
        for label in ('fire', 'smoke'):
            before = original_cases[case['filename']]['metrics'][label]
            after = case['metrics'][label]
            if not before['excluded'] and set(map(tuple, after['temporal']['missed_intervals_frames'])) - set(map(tuple, before['temporal']['missed_intervals_frames'])):
                regressions.append({'filename': case['filename'], 'label': label})
    other = totals['other_development']
    target = totals['epoch_selection_groups']
    gain = any(target['finetuned'][label]['temporal']['missed_scene_events'] < target['original'][label]['temporal']['missed_scene_events'] for label in ('fire', 'smoke'))
    gate = gain and not regressions and all(
        other['finetuned'][label]['temporal']['false_hint_episodes'] <= other['original'][label]['temporal']['false_hint_episodes']
        and other['finetuned'][label]['temporal']['missed_scene_events'] <= other['original'][label]['temporal']['missed_scene_events']
        and target['finetuned'][label]['temporal']['missed_scene_events'] <= target['original'][label]['temporal']['missed_scene_events']
        and target['finetuned'][label]['temporal']['false_hint_episodes'] <= target['original'][label]['temporal']['false_hint_episodes']
        and totals['all_development']['finetuned'][label]['temporal']['false_hint_episodes'] <= totals['all_development']['original'][label]['temporal']['false_hint_episodes']
        for label in ('fire', 'smoke'))
    result = {'proposal': proposal, 'candidate_sha256': digest(weights), 'aggregate': totals,
              'previously_detected_event_regressions_on_other_development': regressions,
              'development_gate_passed': gate, 'box_aggregate': box_totals, 'cases': cases,
              'application_model_replaced': False,
              'note': 'Epoch selection uses 9 val images. All cohorts remain development; even other_development is previously inspected and cannot be called independent external validation.'}
    write(folder / 'results.json', result)
    print(json.dumps({'output': str(folder), 'gate_passed': gate,
                     'summary': {role: {method: {label: {'detected': values[label]['temporal']['detected_scene_events'],
                                                           'events': values[label]['temporal']['ground_truth_scene_events'],
                                                           'false_episodes': values[label]['temporal']['false_hint_episodes']}
                                                for label in ('fire', 'smoke')} for method, values in total.items() if method != 'videos'}
                                 for role, total in totals.items()}}, indent=2), flush=True)


def main():
    import torch
    import ultralytics
    from ultralytics import YOLO
    from ultralytics.utils import LOGGER
    if not torch.cuda.is_available():
        raise ValueError('This GPU trial requires CUDA.')
    dataset = ROOT / 'datasets/firesense-pilot-v1'
    package = read(dataset / 'dataset-manifest.json')
    for row in package['images']:
        if digest(dataset / row['dataset_image']) != row['image_sha256'] or digest(dataset / row['dataset_label']) != row['label_sha256']:
            raise ValueError('Frozen dataset changed.')
    if digest(dataset / 'metadata/annotations.json') != package['annotation_sha256']:
        raise ValueError('Frozen annotations changed.')
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    if digest(plan_path) != package['internal_evaluation_plan_sha256'] or digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
        raise ValueError('Base weights or plan changed.')
    for name, expected in plan['code_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Frozen analysis code changed.')
    train_groups = {row['constraint_group'] for row in package['images'] if row['dataset_split'] == 'train'}
    val_groups = {row['constraint_group'] for row in package['images'] if row['dataset_split'] == 'val'}
    video_roles = {entry['filename']: ('training_groups' if entry['constraint_group'] in train_groups else
                                     'epoch_selection_groups' if entry['constraint_group'] in val_groups else 'other_development')
                   for entry in plan['entries'] if entry['split'] == 'development'}
    folder = ROOT / 'outputs' / datetime.now().strftime('finetune-pilot-%Y%m%d-%H%M%S-%f')
    folder.mkdir()
    proposal = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'parameters': PARAMETERS,
                'base_weights_sha256': plan['weights_sha256'], 'dataset_manifest_sha256': digest(dataset / 'dataset-manifest.json'),
                'dataset_yaml_sha256': digest(dataset / 'dataset.yaml'), 'annotation_sha256': package['annotation_sha256'],
                'plan_sha256': digest(plan_path), 'runner_sha256': digest(Path(__file__)),
                'environment': {'python': sys.version, 'torch': torch.__version__, 'ultralytics': ultralytics.__version__,
                                'gpu': torch.cuda.get_device_name(0)}, 'video_roles': video_roles,
                'checkpoint_selection': 'best.pt selected by Ultralytics fitness on the 9 development val images, not by 30 video results.',
                'criterion_version': 2,
                'criterion': 'On epoch_selection developmental source groups: at least one class has fewer missed events, neither class has more misses or false episodes. On other_development groups: no new misses or false episodes and no previously detected event is lost. Total developmental false episodes must not increase in either class. Passing only permits further localization review/frozen internal holdout check, never automatic product replacement.',
                'fixed_video_config': {'confidence': .35, 'image_size': 640, 'temporal_config': plan['temporal_config']},
                'local_execution': {'amp': False, 'cloud_integrations': False, 'font': 'existing OS Arial; no font download or distribution'},
                'limitations': '18 training / 9 val frames; 6 sources; very small selected hard-case pilot; epochs selected using val; development previously inspected.'}
    write(folder / 'proposal.json', proposal)
    (folder / 'dataset-manifest-snapshot.json').write_bytes((dataset / 'dataset-manifest.json').read_bytes())
    local_only_training_setup()
    model = YOLO(str(ROOT / 'models/best.pt'))
    stream = sys.stdout
    def epoch_progress(trainer):
        stream.write(f'TRAIN EPOCH {trainer.epoch + 1}/{PARAMETERS["epochs"]}\n')
        stream.flush()
    model.add_callback('on_train_epoch_end', epoch_progress)
    print(f'TRAIN BEGIN output={folder}', flush=True)
    started = time.perf_counter()
    with (folder / 'training.log').open('w', encoding='utf-8') as log:
        handlers = [(handler, handler.stream) for handler in LOGGER.handlers if hasattr(handler, 'setStream')]
        try:
            for handler, _ in handlers:
                handler.setStream(log)
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                model.train(data=str(dataset / 'dataset.yaml'), project=str(folder), name='train', **PARAMETERS)
        finally:
            for handler, original_stream in handlers:
                handler.setStream(original_stream)
    candidate = folder / 'train/weights/best.pt'
    if not candidate.is_file() or digest(ROOT / 'models/best.pt') != proposal['base_weights_sha256']:
        raise ValueError('Candidate unavailable or original weights modified.')
    write(folder / 'training-completion.json', {'elapsed_s': time.perf_counter() - started,
          'candidate_sha256': digest(candidate), 'original_weights_unchanged': True,
          'selected_train_metrics': model.trainer.metrics})
    print('TRAIN COMPLETE; begin fixed developmental comparison', flush=True)
    del model
    compare(folder, candidate, proposal, package, plan)


if __name__ == '__main__':
    main()
