"""Development-only fixed local-fire recheck, before any adaptive scheduling."""
import argparse
import json
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.box_metrics import count_boxes, sum_counts
from core.box_review import training_eligible
from core.detector import Detector
from core.evaluation import evaluate_video
from core.local_fire import LocalFireConfig, corner_crops, map_fire_candidate, merge_preserving_reference
from core.temporal import EventTracker, TemporalConfig
from experiments.benchmark_firesense import aggregate, digest, read, write

BASELINE = ROOT / 'outputs/benchmark-v1-20261006-203720-465883'
CONFIG = LocalFireConfig()
CODE = ('core/local_fire.py', 'core/detector.py', 'core/temporal.py',
        'core/evaluation.py', 'core/box_metrics.py', 'experiments/probe_local_fire.py')


def prepare(stage, proposal_extra=None, extra_code=()):
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    snapshot = plan_path.parent / plan['review_snapshot']
    if digest(snapshot) != plan['review_sha256'] or digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
        raise ValueError('Frozen labels or weights changed.')
    for name, expected in plan['code_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError('Frozen code changed: ' + name)
    folder = ROOT / 'outputs' / datetime.now().strftime('local-fire-' + stage + '-%Y%m%d-%H%M%S-%f')
    folder.mkdir()
    code_names = tuple(dict.fromkeys(CODE + tuple(extra_code)))
    proposal = {
        'stage': stage, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'scope': 'Development only; no holdout images used; no training or app changes.',
        'policy': 'Reference whole-frame output plus four overlapping 60%-width/height crops on every frame; fire additions only; preserve all reference boxes.',
        'config': asdict(CONFIG), 'input_size': 640,
        'crop_cut_edge_policy': 'Discard added fire boxes within 1 original crop pixel of artificial cut edges; natural image edges allowed.',
        'plan_sha256': digest(plan_path), 'weights_sha256': plan['weights_sha256'],
        'annotation_sha256': plan['review_sha256'],
        'box_annotation_sha256': digest(ROOT / 'data/firesense/box-review-pilot-v1/annotations.json'),
        'code_sha256': {name: digest(ROOT / name) for name in code_names},
        'video_adoption_gate': 'At least one recovered missed event; no formerly hit event lost in either class; fire false episodes<=3 and smoke<=1. Passing counts requires correct localization review and resolution of annotation disagreement before adoption.',
        'small_box_diagnostic': 'Small fire truth box area <=1% frame area; report matched truth at IoU>=0.5, not precision of this size subset.',
        'pending_label_review': 'testpos06.822 frame177 fire absent in box review but fire interval [177,201); retain original frozen interval for this provisional comparison; do not modify labels.',
        'prior_art': ['https://arxiv.org/abs/2202.06934'],
        'originality_note': 'Fixed cropped inference is an established technique, not claimed as a new architecture or original method. This run is a feasibility control for a later bounded adaptive policy.',
    }
    proposal.update(proposal_extra or {})
    write(folder / 'proposal.json', proposal)
    shutil.copyfile(snapshot, folder / 'review-snapshot.json')
    shutil.copyfile(ROOT / 'data/firesense/box-review-pilot-v1/annotations.json', folder / 'box-annotations-snapshot.json')
    for name in code_names:
        target = folder / 'code-snapshot' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    write(folder / 'run-manifest.json', {'status': 'running', 'proposal_sha256': digest(folder / 'proposal.json')})
    print('RUN_DIRECTORY ' + str(folder), flush=True)
    return folder, plan, proposal, read(snapshot)


def recheck(detector, frame, reference):
    height, width = frame.shape[:2]
    candidates, crop_predictions = [], []
    for crop in corner_crops((width, height), CONFIG):
        x1, y1, x2, y2 = crop
        _, predictions = detector.predict(frame[y1:y2, x1:x2], annotate=False)
        crop_predictions.append({'crop_xyxy': list(crop), 'detections': predictions})
        for detection in predictions:
            mapped = map_fire_candidate(detection, crop, (width, height), CONFIG)
            if mapped is not None:
                candidates.append(mapped)
    return merge_preserving_reference(reference, candidates, CONFIG), crop_predictions


def small_matches(row, predictions):
    truth = [box for box in row['boxes'] if box['label'] == 'fire' and
             (box['xyxy'][2] - box['xyxy'][0]) * (box['xyxy'][3] - box['xyxy'][1]) /
             (row['width'] * row['height']) <= .01]
    counts = count_boxes(truth, [p for p in predictions if p['label'] == 'fire'])['fire']
    return {'truth': len(truth), 'matched': counts['tp'], 'missed': counts['fn']}


def panel(frame, boxes, title, truth=False):
    import cv2
    import numpy as np
    image = frame.copy()
    for box in boxes:
        coords = box['xyxy'] if truth else box['bbox_xyxy']
        x1, y1, x2, y2 = [round(v) for v in coords]
        color = (0, 140, 255) if box['label'] == 'fire' else (220, 220, 220)
        cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
        cv2.putText(image, box['label'], (x1, max(12, y1 - 3)), cv2.FONT_HERSHEY_SIMPLEX, .4, color, 1)
    canvas = np.zeros((240, 360, 3), dtype=np.uint8)
    scale = min(360 / image.shape[1], 210 / image.shape[0])
    resized = cv2.resize(image, (round(image.shape[1] * scale), round(image.shape[0] * scale)))
    canvas[30:30 + resized.shape[0], :resized.shape[1]] = resized
    cv2.putText(canvas, title, (5, 20), cv2.FONT_HERSHEY_SIMPLEX, .48, (255, 255, 255), 1)
    return canvas


def run_images(folder, plan, detector, candidate_model=None):
    import cv2
    import numpy as np
    rows = read(folder / 'box-annotations-snapshot.json')['images']
    entries = {e['filename']: e for e in plan['entries']}
    cases, panels = [], []
    baseline_cases = {case['filename']: case for case in read(BASELINE / 'results.json')['cases']}
    baseline_packets = {}
    for source in {row['source'] for row in rows}:
        case = baseline_cases[source]
        log = BASELINE / next(p.name for p in BASELINE.glob('case-*') if (p / 'case.json').exists() and read(p / 'case.json')['filename'] == source) / 'detections.jsonl'
        if digest(log) != case['log_sha256']['detections.jsonl']:
            raise ValueError('Baseline log changed.')
        baseline_packets[source] = {p['frame_index']: p for p in (json.loads(line) for line in log.read_text(encoding='utf-8').splitlines())}
    for index, row in enumerate(rows, 1):
        if entries[row['source']]['split'] != 'development' or not training_eligible(row):
            raise ValueError('Only completed, clear development annotations allowed.')
        path = ROOT / 'data/firesense/box-review-pilot-v1' / row['image']
        if digest(path) != row['image_sha256']:
            raise ValueError('Annotated image changed.')
        frame = cv2.imread(str(path))
        if frame is None or [frame.shape[1], frame.shape[0]] != [row['width'], row['height']]:
            raise ValueError('Invalid source image.')
        start = time.perf_counter()
        _, reference = detector.predict(frame, annotate=False)
        reference_s = time.perf_counter() - start
        if reference != baseline_packets[row['source']][row['frame_index']]['detections']:
            raise ValueError('Live reference differs from frozen baseline image: ' + row['image'])
        start = time.perf_counter()
        if candidate_model is None:
            candidate, crops = recheck(detector, frame, reference)
        else:
            _, candidate = candidate_model.predict(frame, annotate=False)
            crops = []
        extra_s = time.perf_counter() - start
        cases.append({'image': row['image'], 'source': row['source'], 'frame_index': row['frame_index'],
                      'reference': reference, 'candidate': candidate, 'crops': crops,
                      'reference_counts': count_boxes(row['boxes'], reference),
                      'candidate_counts': count_boxes(row['boxes'], candidate),
                      'small_reference': small_matches(row, reference), 'small_candidate': small_matches(row, candidate),
                      'reference_prediction_call_s': reference_s, 'extra_prediction_calls_s': extra_s})
        comparison = np.hstack([panel(frame, row['boxes'], f'{index:02d} HUMAN', True),
                                panel(frame, reference, 'REFERENCE'), panel(frame, candidate, 'LOCAL FIRE' if candidate_model is None else 'CANDIDATE')])
        cv2.imwrite(str(folder / f'image-{index:02d}.png'), comparison)
        panels.append(comparison)
        print(f'IMAGE {index}/{len(rows)} {Path(row["image"]).name}', flush=True)
    for start in range(0, len(panels), 9):
        cv2.imwrite(str(folder / f'contact-sheet-{start // 9 + 1:02d}.png'), np.vstack(panels[start:start + 9]))
    totals = {mode: sum_counts([case[mode + '_counts'] for case in cases]) for mode in ('reference', 'candidate')}
    small = {mode: {key: sum(case['small_' + mode][key] for case in cases) for key in ('truth', 'matched', 'missed')}
             for mode in ('reference', 'candidate')}
    worthwhile = any(totals['candidate'][label]['tp'] > totals['reference'][label]['tp']
                    for label in ('fire', 'smoke'))
    if candidate_model is not None:
        worthwhile = worthwhile and all(totals['candidate'][label]['tp'] >= totals['reference'][label]['tp']
                                       for label in ('fire', 'smoke'))
    result = {'scope': '27 selected difficult development frames; localization counts, not representative accuracy or mAP.',
              'aggregate': totals, 'small_fire': small, 'full_video_check_warranted': worthwhile, 'cases': cases,
              'timing': {key: sum(case[key] for case in cases) for key in ('reference_prediction_call_s', 'extra_prediction_calls_s')}}
    write(folder / 'results.json', result)
    lines = ['# ' + ('小火焰局部复检' if candidate_model is None else '候选检测器') + '：图片诊断', '',
             '27张已曝光困难开发图；不是独立测试，不训练、不替换应用默认权重。参考模型原始框逐图与冻结基线一致。', '',
             '| 类别 | 原 TP/FP/FN | 复检 TP/FP/FN |', '|---|---:|---:|']
    for label in ('fire', 'smoke'):
        values = [totals[mode][label] for mode in ('reference', 'candidate')]
        lines.append('| ' + label + ' | ' + ' | '.join('/'.join(str(v[k]) for k in ('tp', 'fp', 'fn')) for v in values) + ' |')
    lines += ['', '小火焰真值框面积≤画面1%，IoU≥0.5的匹配数：' + str(small), '',
              '是否值得完整视频检查：' + str(worthwhile) + '。图片收益不等于事件收益；静态图片不能评价报警连续性。', '',
              '固定切片和现成模型选型均不作为自主网络结构创新；本次不训练、不替换产品配置。']
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {'images': len(cases), 'aggregate': totals, 'small_fire': small, 'full_video_check_warranted': worthwhile}


def run_videos(folder, plan, detector, review, candidate_model=None):
    import cv2
    rows = {row['filename']: row for row in review['videos']}
    entries = [entry for entry in plan['entries'] if entry['split'] == 'development']
    baseline = {c['filename']: c for c in read(BASELINE / 'results.json')['cases'] if c['split'] == 'development'}
    cases = {'reference': [], 'candidate': []}
    log_hashes, changes = {}, []
    for number, entry in enumerate(entries, 1):
        source = ROOT / 'videos' / entry['filename']
        if digest(source) != entry['source_sha256']:
            raise ValueError('Source changed.')
        case_folder = folder / f'case-{number:02d}-{source.stem}'
        case_folder.mkdir()
        trackers = {mode: EventTracker(TemporalConfig(**plan['temporal_config'])) for mode in cases}
        packets, states = {mode: [] for mode in cases}, {mode: [] for mode in cases}
        timing = {'reference_prediction_call_s': 0., 'extra_prediction_calls_s': 0.}
        capture = cv2.VideoCapture(str(source))
        if not capture.isOpened() or abs(capture.get(cv2.CAP_PROP_FPS) - entry['fps']) > 1e-6:
            capture.release()
            raise ValueError('Video decoder or fps mismatch.')
        print(f'BEGIN {number}/{len(entries)} {source.name}', flush=True)
        streams = {name: (case_folder / name).open('w', encoding='utf-8') for name in
                   ('reference-detections.jsonl', 'candidate-detections.jsonl',
                    'reference-temporal.jsonl', 'candidate-temporal.jsonl', 'crop-detections.jsonl')}
        try:
            count = 0
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                before = time.perf_counter()
                _, reference = detector.predict(frame, annotate=False)
                timing['reference_prediction_call_s'] += time.perf_counter() - before
                before = time.perf_counter()
                if candidate_model is None:
                    candidate, crops = recheck(detector, frame, reference)
                else:
                    _, candidate = candidate_model.predict(frame, annotate=False)
                    crops = []
                timing['extra_prediction_calls_s'] += time.perf_counter() - before
                timestamp = round(count / entry['fps'], 6)
                for mode, detections in (('reference', reference), ('candidate', candidate)):
                    packet = {'frame_index': count, 'timestamp_s': timestamp,
                              'frame_size': [frame.shape[1], frame.shape[0]], 'detections': detections}
                    state = trackers[mode].update(timestamp, detections, packet['frame_size'])
                    packets[mode].append(packet)
                    states[mode].append(state)
                    streams[mode + '-detections.jsonl'].write(json.dumps(packet) + '\n')
                    streams[mode + '-temporal.jsonl'].write(json.dumps(state) + '\n')
                streams['crop-detections.jsonl'].write(json.dumps({'frame_index': count, 'timestamp_s': timestamp, 'crops': crops}) + '\n')
                count += 1
                if count % 500 == 0:
                    print(f'PROGRESS {number}/{len(entries)} {count}/{entry["decoded_frames"]}', flush=True)
            if count != entry['decoded_frames']:
                raise ValueError('Incomplete source decoding.')
        finally:
            capture.release()
            for stream in streams.values():
                stream.close()
        metrics = {mode: evaluate_video(rows[entry['filename']], packets[mode], states[mode], plan['episode_merge_gap_s']) for mode in cases}
        if metrics['reference'] != baseline[entry['filename']]['metrics']:
            raise ValueError('Live baseline video metrics changed: ' + entry['filename'])
        for label in ('fire', 'smoke'):
            if metrics['reference'][label]['excluded']:
                continue
            before, after = [metrics[mode][label]['temporal'] for mode in ('reference', 'candidate')]
            old_misses, new_misses = before['missed_intervals_frames'], after['missed_intervals_frames']
            recovered = [item for item in old_misses if item not in new_misses]
            lost = [item for item in new_misses if item not in old_misses]
            if recovered or lost or before['false_hint_episodes'] != after['false_hint_episodes']:
                changes.append({'filename': entry['filename'], 'label': label, 'recovered_intervals': recovered,
                                'lost_intervals': lost, 'false_before': before['false_hint_episodes'],
                                'false_after': after['false_hint_episodes']})
        for mode in cases:
            cases[mode].append({**entry, 'metrics': metrics[mode], 'processed_frames': count})
        hashes = {name: digest(case_folder / name) for name in streams}
        log_hashes[case_folder.name] = hashes
        write(case_folder / 'case.json', {**entry, 'processed_frames': count, 'metrics': metrics, 'timing': timing, 'log_sha256': hashes})
        write(folder / 'progress.json', {'completed': number, 'total': len(entries), 'last_source': entry['filename']})
        print(f'END {number}/{len(entries)} frames={count}', flush=True)
    totals = {method: {label: {mode: aggregate(values, 'development', label, mode) for mode in ('single_frame', 'temporal')}
                       for label in ('fire', 'smoke')} for method, values in cases.items()}
    gates = {'recovered_at_least_one': any(change['recovered_intervals'] for change in changes),
             'no_lost_events': not any(change['lost_intervals'] for change in changes),
             'fire_false_guard': totals['candidate']['fire']['temporal']['false_hint_episodes'] <= 3,
             'smoke_false_guard': totals['candidate']['smoke']['temporal']['false_hint_episodes'] <= 1}
    timing = {key: sum(read(folder / name / 'case.json')['timing'][key] for name in log_hashes)
              for key in ('reference_prediction_call_s', 'extra_prediction_calls_s')}
    frames = sum(case['processed_frames'] for case in cases['reference'])
    timing['reference_prediction_fps'] = frames / timing['reference_prediction_call_s']
    candidate_s = timing['extra_prediction_calls_s']
    if candidate_model is None:
        candidate_s += timing['reference_prediction_call_s']
    timing['candidate_prediction_fps'] = frames / candidate_s
    timing['candidate_cost_note'] = ('Reference plus four crop calls' if candidate_model is None
                                     else 'Candidate-only prediction cost; reference call excluded')
    result = {'aggregate': totals, 'changes': changes, 'numeric_gates': gates,
              'numeric_gate_passed': all(gates.values()), 'adopted': False,
              'decision': 'Pending localization and label review' if all(gates.values()) else 'Rejected by predeclared numeric gates',
              'cases': cases, 'processed_frames': frames, 'timing': timing,
              'timing_note': 'Same-run synchronized prediction-call durations include transfer/postprocess; no drawing/UI/export; not end-to-end software FPS.',
              'log_sha256': log_hashes}
    write(folder / 'results.json', result)
    lines = ['# ' + ('小火焰局部复检' if candidate_model is None else '候选检测器') + '：完整开发视频对照', '',
             '30段开发视频，640输入、0.35置信度、原时序规则。参考模型全帧预测重新执行并与冻结基线逐视频指标核对一致。未运行内部留出。', '',
             '| 类别 | 原检出/事件 | 复检检出/事件 | 原误提示片段 | 复检误提示片段 |', '|---|---:|---:|---:|---:|']
    for label in ('fire', 'smoke'):
        before, after = [totals[mode][label]['temporal'] for mode in ('reference', 'candidate')]
        lines.append(f'| {label} | {before["detected_scene_events"]}/{before["ground_truth_scene_events"]} | {after["detected_scene_events"]}/{after["ground_truth_scene_events"]} | {before["false_hint_episodes"]} | {after["false_hint_episodes"]} |')
    lines += ['', '数值护栏：' + json.dumps(gates, ensure_ascii=False), '',
              '决定：' + result['decision'] + '。未替换产品模型或配置。', '',
              '同次运行预测调用计时：' + json.dumps(timing, ensure_ascii=False), '',
              result['timing_note'], '',
              '场景事件只按类别/时间匹配，不证明框定位正确；testpos06.822起点标注差异仍待人工复核。', '',
              '固定切片和现成模型选型均不作为自主网络结构创新。结果保存全部失败、误提示、检测框和时序日志。']
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return {key: result[key] for key in ('processed_frames', 'numeric_gates', 'numeric_gate_passed', 'decision', 'timing')}


def main():
    import numpy as np
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=('images', 'videos'), required=True)
    args = parser.parse_args()
    folder, plan, proposal, review = prepare(args.stage)
    try:
        detector = Detector(ROOT / 'models/best.pt', confidence=CONFIG.confidence)
        for shape in ((640, 640), (192, 320)):
            detector.predict(np.zeros((*shape, 3), dtype=np.uint8), annotate=False)
        started = time.perf_counter()
        summary = run_images(folder, plan, detector) if args.stage == 'images' else run_videos(folder, plan, detector, review)
        if digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
            raise ValueError('Application weights changed during experiment.')
        write(folder / 'run-manifest.json', {'status': 'completed', 'device': detector.device,
              'proposal_sha256': digest(folder / 'proposal.json'), 'elapsed_s': time.perf_counter() - started,
              'results_sha256': digest(folder / 'results.json'), 'completed_at_utc': datetime.now(timezone.utc).isoformat()})
        print(json.dumps({'output': str(folder), **summary}, ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        write(folder / 'run-manifest.json', {'status': 'failed', 'error': str(exc), 'proposal_sha256': digest(folder / 'proposal.json')})
        raise


if __name__ == '__main__':
    main()
