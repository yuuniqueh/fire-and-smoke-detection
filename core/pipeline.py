"""Shared video pipeline for CLI and desktop; UI never changes scoring."""
import csv
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.runtime import ROOT


@dataclass
class AnalysisOptions:
    source: str
    device: str = 'auto'
    conf: float = 0.35
    headless: bool = True
    max_frames: object = None
    baseline: bool = False
    temporal_only: bool = False
    temporal_config: str = str(ROOT / 'configs' / 'temporal.json')
    risk_config: str = str(ROOT / 'configs' / 'risk.json')
    model_profile: str = 'original'


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def analyze_video(args, weights, *, on_frame=None, on_started=None, stop_event=None, realtime=False, report=print):
    if not 0 < args.conf <= 1 or (args.max_frames is not None and args.max_frames <= 0):
        raise ValueError('检测阈值或帧数限制无效。')
    import cv2
    from core.model_profiles import create_detector, PROFILE_NAMES

    source = Path(args.source).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f'视频文件不存在：{source}')
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        capture.release()
        raise RuntimeError(f'无法读取视频：{source}；请检查文件及编码格式。')
    writer = None
    tracker = None
    risk_engine = None
    fps = 0.0
    shown = False
    frames = detected_frames = 0
    warnings = []
    evidence_images = {}
    started = time.perf_counter()
    status = 'failed'
    error = None
    output = ROOT / 'outputs' / datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')
    output.mkdir()
    try:
        if on_started:
            on_started({'output': str(output), 'stage': 'loading'})
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('视频帧率无效，无法可靠计算视频时间。')
        declared_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        profile = getattr(args, 'model_profile', 'original')
        detector = create_detector(weights, args.device, args.conf, profile)
        if not args.baseline:
            from dataclasses import asdict
            from core.temporal import EventTracker, TemporalConfig
            temporal_config = TemporalConfig(**json.loads(Path(args.temporal_config).read_text(encoding='utf-8')))
            tracker = EventTracker(temporal_config)
            if not args.temporal_only:
                from core.risk import RiskConfig, RiskEngine
                risk_config = RiskConfig(**json.loads(Path(args.risk_config).read_text(encoding='utf-8')))
                risk_engine = RiskEngine(risk_config)
        report(f'模式：{PROFILE_NAMES[profile]}；设备：{detector.device}；视频：{source.name}；阈值：{args.conf}')
        report('视频窗口中按 Q 或 Esc 停止。' if not args.headless else '无窗口模式。')
        config = {'source': str(source), 'weights': str(weights), 'source_fps': fps,
                  'declared_frames': declared_frames, 'device': detector.device,
                  'confidence_threshold': args.conf, 'image_size': 640,
                  'model_profile': profile, 'model_profile_name': PROFILE_NAMES[profile],
                  'frame_size': [int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))],
                  'event_mode': 'single_frame_baseline' if args.baseline else 'temporal',
                  'temporal_config': asdict(temporal_config) if tracker else None,
                  'risk_config': asdict(risk_engine.config) if risk_engine else None}
        config['playback_pacing'] = 'source_fps_when_possible' if realtime else 'unpaced' if args.headless else 'opencv_display_delay'
        manifest_path = ROOT / 'models' / 'manifest.json'
        if hasattr(detector, 'model_manifest'):
            config['model_manifest'] = detector.model_manifest
        elif manifest_path.is_file():
            config['model_manifest'] = json.loads(manifest_path.read_text(encoding='utf-8'))
        write_json(output / 'config.json', config)
        if on_started:
            on_started({'output': str(output), 'stage': 'ready', 'config': config})
        playback_started = time.perf_counter()
        with (output / 'detections.jsonl').open('w', encoding='utf-8') as log, \
                (output / 'temporal.jsonl').open('w', encoding='utf-8') as temporal_log, \
                (output / 'risk.jsonl').open('w', encoding='utf-8') as risk_log, \
                (output / 'risk.csv').open('w', encoding='utf-8', newline='') as risk_csv_file:
            risk_csv = csv.writer(risk_csv_file)
            if risk_engine:
                risk_csv.writerow(['timestamp_s', 'status', 'level', 'score', 'event_id', 'fresh_evidence',
                                   'box_union_coverage', 'growth_pp_per_s', 'positive_duration_s'])
            while True:
                if stop_event is not None and stop_event.is_set():
                    status = 'stopped_by_user'
                    break
                ok, frame = capture.read()
                if not ok:
                    if frames == 0:
                        raise RuntimeError('视频未包含可解码的帧。')
                    if declared_frames > 0 and frames != declared_frames:
                        warnings.append(f'实际解码 {frames} 帧，容器报告 {declared_frames} 帧；'
                                        '容器帧数可能为估算值，请核对素材完整性。')
                    status = 'completed_with_frame_count_warning' if warnings else 'completed'
                    break
                annotated, detections = detector.predict(frame)
                snapshot = assessment = None
                timestamp = round(frames / fps, 6)
                log.write(json.dumps({'frame_index': frames, 'timestamp_s': timestamp,
                                      'frame_size': [frame.shape[1], frame.shape[0]],
                                      'detections': detections}, ensure_ascii=False) + '\n')
                if tracker:
                    from core.temporal import draw_event_status
                    snapshot = tracker.update(timestamp, detections, (frame.shape[1], frame.shape[0]))
                    temporal_log.write(json.dumps(snapshot, ensure_ascii=False) + '\n')
                    annotated = draw_event_status(annotated, snapshot)
                    for transition in snapshot['transitions']:
                        if transition['type'] == 'event_confirmed':
                            evidence_dir = output / 'evidence'
                            evidence_dir.mkdir(exist_ok=True)
                            image_path = evidence_dir / f'event-{transition["event_id"]}.jpg'
                            # imencode + tofile supports Chinese Windows paths.
                            encoded_ok, encoded = cv2.imencode('.jpg', annotated)
                            if not encoded_ok:
                                raise RuntimeError('无法生成事件证据截图。')
                            encoded.tofile(str(image_path))
                            evidence_images[transition['event_id']] = str(image_path.relative_to(output)).replace('\\', '/')
                            action = '预警提示' if transition['alert_emitted'] else '冷却期内记录事件'
                            report(f'{timestamp:.2f}s：{transition["label"]} 事件 #{transition["event_id"]} 已确认，{action}。')
                    if risk_engine:
                        from core.risk import draw_risk_panel
                        assessment = risk_engine.update(snapshot, detections, (frame.shape[1], frame.shape[0]))
                        risk_log.write(json.dumps(assessment, ensure_ascii=False) + '\n')
                        dominant = next((e for e in assessment['events'] if e['event_id'] == assessment['dominant_event_id']), None)
                        feature = dominant['features'] if dominant else {}
                        risk_csv.writerow([timestamp, assessment['status'], assessment['level'], assessment['score'],
                                           assessment['dominant_event_id'], assessment['fresh_evidence'],
                                           feature.get('coverage_ratio_smoothed'), feature.get('growth_pp_per_s'),
                                           feature.get('positive_duration_s')])
                        display_frame = annotated
                        annotated = draw_risk_panel(annotated, assessment)
                        for change in assessment['level_changes']:
                            report(f'{timestamp:.2f}s：事件 #{change["event_id"]} 提示等级 {change["from"]} -> {change["to"]}，指数 {change["score"]:.1f}。')
                if writer is None:
                    height, width = annotated.shape[:2]
                    config['output_frame_size'] = [width, height]
                    write_json(output / 'config.json', config)
                    writer = cv2.VideoWriter(str(output / 'annotated.mp4'),
                                             cv2.VideoWriter_fourcc(*'mp4v'), fps, (width, height))
                    if not writer.isOpened():
                        raise RuntimeError('无法创建输出视频，请检查磁盘和视频编码器。')
                writer.write(annotated)
                frames += 1
                detected_frames += bool(detections)
                if on_frame:
                    on_frame({'frame': display_frame if risk_engine else annotated,
                              'frame_index': frames - 1, 'timestamp_s': timestamp,
                              'declared_frames': declared_frames, 'processed_frames': frames,
                              'temporal': snapshot, 'risk': assessment,
                              'event_counts': tracker.summary() if tracker else {},
                              'risk_peaks': {item['event_id']: {'peak_level': item['peak_level'],
                                             'peak_score': item['peak_score'], 'peak_evidence': item['peak_evidence']}
                                             for item in (risk_engine.event_summaries[-200:] +
                                                          [{'event_id': key, **value} for key, value in risk_engine.states.items()])}
                                            if risk_engine else {},
                              'evidence_images': evidence_images.copy(),
                              'closed_events': tracker.closed_events[-200:] if tracker else [],
                              'elapsed_s': time.perf_counter() - playback_started})
                if not args.headless:
                    cv2.imshow('FireGuard - Video Analysis (Q to stop)', annotated)
                    shown = True
                    key = cv2.waitKey(max(1, round(1000 / fps))) & 0xff
                    if key in (ord('q'), 27) or cv2.getWindowProperty(
                            'FireGuard - Video Analysis (Q to stop)', cv2.WND_PROP_VISIBLE) < 1:
                        status = 'stopped_by_user'
                        break
                if args.max_frames and frames >= args.max_frames:
                    status = 'frame_limit_reached'
                    break
                if realtime:
                    delay = max(0.0, playback_started + frames / fps - time.perf_counter())
                    if stop_event is not None:
                        stop_event.wait(delay)
                    else:
                        time.sleep(delay)
    except KeyboardInterrupt:
        status = 'stopped_by_user'
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        capture.release()
        if writer is not None:
            writer.release()
        if shown:
            cv2.destroyAllWindows()
        if tracker:
            reason = 'stream_end' if status.startswith('completed') else 'processing_error' if status == 'failed' else status
            end_timestamp = round(frames / fps, 6) if fps else 0.0
            closing_transitions = tracker.finish(end_timestamp, reason)
            for event in tracker.closed_events:
                event['evidence_image'] = evidence_images.get(event['event_id'])
            if risk_engine:
                risk_engine.finish(end_timestamp, reason)
                risk_by_id = {item['event_id']: item for item in risk_engine.event_summaries}
                for event in tracker.closed_events:
                    event['risk_summary'] = risk_by_id.get(event['event_id'])
                write_json(output / 'risk-events.json', {'config': asdict(risk_engine.config),
                           'summary': risk_engine.summary(), 'events': risk_engine.event_summaries})
            write_json(output / 'events.json', {'config': asdict(tracker.config),
                       'summary': tracker.summary(), 'events': tracker.closed_events,
                       'closing_transitions': closing_transitions,
                       'note': 'Confirmed means persistent visual detection, not a verified physical fire.'})
        elapsed = time.perf_counter() - started
        summary = {
            'status': status, 'processed_frames': frames,
            'error': error,
            'warnings': warnings,
            'frames_with_detections': detected_frames,
            'temporal': tracker.summary() if tracker else None,
            'risk': risk_engine.summary() if risk_engine else None,
            'elapsed_s': round(elapsed, 3),
            'end_to_end_fps': round(frames / elapsed, 3) if elapsed else 0,
            'note': 'Contains model initialization, decoding, inference, drawing, writing and optional display delay. Not an accuracy score.',
        }
        write_json(output / 'summary.json', summary)
    report(f'处理 {frames} 帧；状态：{status}；输出目录：{output}')
    for warning in warnings:
        report(f'提示：{warning}')
    return {'output': str(output), 'summary': summary}

