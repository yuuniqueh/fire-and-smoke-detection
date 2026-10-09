"""Run the frozen internal baseline; write full logs and resume only verified completed cases."""
import argparse
import hashlib
import json
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.evaluation import evaluate_video
from core.temporal import EventTracker, TemporalConfig


def digest(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            sha.update(block)
    return sha.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def aggregate(cases, split, label, mode):
    metrics = [case['metrics'][label][mode] for case in cases
               if case['split'] == split and not case['metrics'][label]['excluded']]
    totals = {key: sum(row[key] for row in metrics) for key in
              ('ground_truth_scene_events', 'detected_scene_events', 'missed_scene_events', 'false_hint_episodes',
               'negative_duration_s', 'ground_truth_positive_frames', 'active_hint_frames',
               'true_positive_hint_frames', 'false_positive_hint_frames', 'false_negative_hint_frames')}
    delays = [delay for row in metrics for delay in row['first_active_hint_delays_s']]
    totals.update({'videos_included': len(metrics),
                   'scene_event_recall': totals['detected_scene_events'] / totals['ground_truth_scene_events'] if totals['ground_truth_scene_events'] else None,
                   'false_hint_episodes_per_negative_minute': totals['false_hint_episodes'] * 60 / totals['negative_duration_s'] if totals['negative_duration_s'] else None,
                   'mean_first_active_hint_delay_s': sum(delays) / len(delays) if delays else None,
                   'delay_denominator_detected_events': len(delays)})
    return totals


def generate_report(folder, manifest, cases, plan):
    totals = {split: {label: {mode: aggregate(cases, split, label, mode) for mode in ('single_frame', 'temporal')}
                      for label in ('fire', 'smoke')} for split in ('development', 'internal_holdout')}
    write(folder / 'results.json', {'run_manifest': manifest, 'aggregate': totals, 'cases': cases})
    lines = ['# FIRESENSE 固定内部划分的基线对照', '',
             '保守约束分组：开发 30 段，内部留出 14 段，解码异常 5 段排除。分组与设置在未曝光视频推理前冻结；标签由用户标注。', '',
             '视觉相似约束不是原始拍摄来源证明，训练重叠也未排除；本报告不能称为完全独立外部测试或真实危险程度准确率。', '',
             '| 分组 | 类别 | 方法 | 纳入视频 | 场景检出/总数 | 漏提示 | 负标签误提示片段 | 每负分钟误提示片段 | 已检出事件平均延迟（秒） |',
             '|---|---|---|---:|---:|---:|---:|---:|---:|']
    fmt = lambda value: '—' if value is None else f'{value:.3f}'
    for split in totals:
        for label in totals[split]:
            for mode, values in totals[split][label].items():
                lines.append(f'| {split} | {label} | {mode} | {values["videos_included"]} | '
                             f'{values["detected_scene_events"]}/{values["ground_truth_scene_events"]} | '
                             f'{values["missed_scene_events"]} | {values["false_hint_episodes"]} | '
                             f'{fmt(values["false_hint_episodes_per_negative_minute"])} | {fmt(values["mean_first_active_hint_delay_s"])} |')
    lines += ['', '误提示片段按负标签帧上的活动状态聚合，所有方法使用同一个 ≤1 秒间隔合并规则；不是原始通知数。时序状态包含短暂缺证据时的保持。延迟只平均已检出事件，须与漏提示数量一起读。', '',
              '场景事件按类别/时间匹配，没有框真值，不能证明框落在正确目标上。烟雾不确定的一段仅排除 smoke 指标。风险指数未作为真假标签。', '',
              '下次修改依据开发组失败案例；内部留出组用于检查冻结版本，不针对留出逐片调参。多次复用后需另外保留新素材检验。', '',
              '## 开发组优先复核项', '', '| 文件 / 类别 | 单帧漏提示 → 时序漏提示 | 时序误提示片段 |', '|---|---:|---:|']
    failures = []
    for case in cases:
        if case['split'] != 'development':
            continue
        for label, values in case['metrics'].items():
            if not values['excluded'] and (values['temporal']['missed_scene_events'] or values['temporal']['false_hint_episodes']):
                failures.append((values['temporal']['missed_scene_events'] * 100 + values['temporal']['false_hint_episodes'], case, label))
    for _, case, label in sorted(failures, key=lambda value: (-value[0], value[1]['filename'], value[2]))[:10]:
        values = case['metrics'][label]
        lines.append(f'| {Path(case["filename"]).name} / {label} | {values["single_frame"]["missed_scene_events"]} → '
                     f'{values["temporal"]["missed_scene_events"]} | {values["temporal"]["false_hint_episodes"]} |')
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return totals


def main():
    import cv2
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', help='Resume this benchmark directory under outputs; completed cases are verified.')
    args = parser.parse_args()
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    assert plan['frozen']
    plan_sha = digest(plan_path)
    snapshot_path = plan_path.parent / plan['review_snapshot']
    if digest(snapshot_path) != plan['review_sha256'] or digest(ROOT / 'models/best.pt') != plan['weights_sha256']:
        raise ValueError('Frozen annotation or weights hash mismatch.')
    for name, expected in plan['code_sha256'].items():
        if digest(ROOT / name) != expected:
            raise ValueError(f'Frozen evaluation implementation changed: {name}')
    rows = {row['filename']: row for row in read(snapshot_path)['videos']}
    folder = Path(args.output).resolve() if args.output else ROOT / 'outputs' / datetime.now().strftime('benchmark-v1-%Y%m%d-%H%M%S-%f')
    if (ROOT / 'outputs').resolve() not in folder.parents:
        raise ValueError('Output must stay inside project outputs directory.')
    folder.mkdir(exist_ok=True)
    runner_sha = digest(Path(__file__))
    if (folder / 'run-manifest.json').exists():
        manifest = read(folder / 'run-manifest.json')
        if manifest['plan_sha256'] != plan_sha or manifest['runner_sha256'] != runner_sha:
            raise ValueError('Resume plan/runner changed; use a new output directory.')
    else:
        manifest = {'started_at_utc': datetime.now(timezone.utc).isoformat(), 'plan_sha256': plan_sha,
                    'runner_sha256': runner_sha, 'status': 'running', 'scope': plan['scope'],
                    'log_only': True, 'note': 'No annotated MP4; every source frame analysed once. Same detections for both methods.'}
        shutil.copyfile(plan_path, folder / 'plan.json')
        shutil.copyfile(snapshot_path, folder / 'review-snapshot.json')
        write(folder / 'run-manifest.json', manifest)
    cached = {row['source']: row for row in read(ROOT / 'outputs/firesense-initial-check.json')['results']}
    entries = sorted((entry for entry in plan['entries'] if entry['split'] != 'excluded'),
                     key=lambda entry: (entry['split'] != 'development', entry['filename']))
    detector = None
    cases = []
    started = time.perf_counter()
    try:
        for number, entry in enumerate(entries, 1):
            filename = entry['filename']
            source = (ROOT / 'videos' / filename).resolve()
            if (ROOT / 'videos').resolve() not in source.parents or digest(source) != entry['source_sha256']:
                raise ValueError(f'Source identity mismatch: {filename}')
            case_folder = folder / f'case-{number:02d}-{source.stem}'
            case_folder.mkdir(exist_ok=True)
            saved = case_folder / 'case.json'
            if saved.exists():
                case = read(saved)
                if case['status'] != 'completed' or case['source_sha256'] != entry['source_sha256'] or any(digest(case_folder / name) != expected for name, expected in case['log_sha256'].items()):
                    raise ValueError('Completed case metadata or logs altered.')
                cases.append(case)
                print(f'RESUME {number}/44 {filename}', flush=True)
                continue
            print(f'BEGIN {number}/44 {entry["split"]} {filename}', flush=True)
            tracker = EventTracker(TemporalConfig(**plan['temporal_config']))
            capture = None
            frame_count = 0
            previous = cached.get(filename) if entry['development_seen'] else None
            case_started = time.perf_counter()
            try:
                if previous:
                    original = Path(previous['output'])
                    config = read(original / 'config.json')
                    if config['temporal_config'] != plan['temporal_config'] or config['confidence_threshold'] != plan['confidence'] or config['model_manifest']['sha256'] != plan['weights_sha256']:
                        raise ValueError('Cached input/settings differ from the frozen plan.')
                    packets = [json.loads(line) for line in (original / 'detections.jsonl').read_text(encoding='utf-8').splitlines()]
                else:
                    if detector is None:
                        from core.detector import Detector
                        detector = Detector(ROOT / 'models/best.pt', confidence=plan['confidence'])
                        manifest['device'] = detector.device
                    capture = cv2.VideoCapture(str(source))
                    if not capture.isOpened() or abs(capture.get(cv2.CAP_PROP_FPS) - entry['fps']) > 1e-6:
                        raise ValueError('Video decoder or frame rate mismatch.')
                    packets = None
                with (case_folder / 'detections.jsonl').open('w', encoding='utf-8') as detected_log, \
                        (case_folder / 'temporal.jsonl').open('w', encoding='utf-8') as temporal_log:
                    while True:
                        if packets is not None:
                            if frame_count >= len(packets):
                                break
                            packet = packets[frame_count]
                        else:
                            ok, frame = capture.read()
                            if not ok:
                                break
                            _, detections = detector.predict(frame, annotate=False)
                            packet = {'frame_index': frame_count, 'timestamp_s': round(frame_count / entry['fps'], 6),
                                      'frame_size': [frame.shape[1], frame.shape[0]], 'detections': detections}
                        snapshot = tracker.update(packet['timestamp_s'], packet['detections'], packet['frame_size'])
                        detected_log.write(json.dumps(packet, ensure_ascii=False) + '\n')
                        temporal_log.write(json.dumps(snapshot, ensure_ascii=False) + '\n')
                        frame_count += 1
                        if frame_count % 1000 == 0:
                            print(f'PROGRESS {number}/44 {frame_count}/{entry["decoded_frames"]}', flush=True)
                if frame_count != entry['decoded_frames']:
                    raise ValueError(f'Incomplete source decoding: {frame_count}/{entry["decoded_frames"]}')
                tracker.finish(round(frame_count / entry['fps'], 6))
                detection_packets = [json.loads(line) for line in (case_folder / 'detections.jsonl').read_text(encoding='utf-8').splitlines()]
                temporal_packets = [json.loads(line) for line in (case_folder / 'temporal.jsonl').read_text(encoding='utf-8').splitlines()]
                case = {**entry, 'status': 'completed', 'processed_frames': frame_count,
                        'cached_detections_reused': bool(previous), 'elapsed_s': round(time.perf_counter() - case_started, 3),
                        'log_sha256': {name: digest(case_folder / name) for name in ('detections.jsonl', 'temporal.jsonl')},
                        'metrics': evaluate_video(rows[filename], detection_packets, temporal_packets, plan['episode_merge_gap_s']),
                        'temporal_summary': tracker.summary()}
                write(case_folder / 'events.json', {'summary': tracker.summary(), 'events': tracker.closed_events})
                write(saved, case)
                cases.append(case)
                write(folder / 'progress.json', {'completed': len(cases), 'total': len(entries), 'last_source': filename})
                print(f'END {number}/44 frames={frame_count} elapsed={case["elapsed_s"]}', flush=True)
            finally:
                if capture is not None:
                    capture.release()
        manifest.update({'status': 'completed', 'completed_at_utc': datetime.now(timezone.utc).isoformat(),
                         'total_frames': sum(case['processed_frames'] for case in cases),
                         'cached_cases': sum(case['cached_detections_reused'] for case in cases),
                         'elapsed_this_invocation_s': round(time.perf_counter() - started, 3)})
        totals = generate_report(folder, manifest, cases, plan)
        write(folder / 'run-manifest.json', manifest)
        print(json.dumps({'output': str(folder), 'aggregate': totals, 'total_frames': manifest['total_frames']}, ensure_ascii=False, indent=2), flush=True)
    except BaseException as exc:
        manifest.update({'status': 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed', 'error': str(exc)})
        write(folder / 'run-manifest.json', manifest)
        print(f'RESUME_DIRECTORY {folder}', flush=True)
        raise


if __name__ == '__main__':
    main()
