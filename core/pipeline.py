"""Shared detector pipeline with optional, separately recorded scene fusion."""
import csv
import json
import math
import shutil
import time
from threading import RLock
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from core.runtime import ASSET_ROOT, ROOT

_MONITOR_INFERENCE_LOCK = RLock()


@dataclass
class AnalysisOptions:
    source: str
    device: str = 'auto'
    conf: float = 0.35
    headless: bool = True
    max_frames: object = None
    baseline: bool = False
    temporal_only: bool = False
    temporal_config: str = str(ASSET_ROOT / 'configs' / 'temporal.json')
    risk_config: str = str(ASSET_ROOT / 'configs' / 'risk.json')
    model_profile: str = 'original'
    association_profile: str = 'original'
    confirmation_profile: str = 'standard'
    monitoring_profile: str = 'full'
    alert_sound: bool = False
    source_kind: str = 'file'
    recording_mode: str = 'full'
    storage_max_gb: int = 0
    storage_retention_days: int = 30
    storage_automatic: bool = False
    storage_exclude: tuple = ()
    monitoring_point: object = None
    serialize_inference: bool = False
    scene_context: object = None
    fusion_shadow_model: object = None


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def create_run_directory(root, now=None):
    """Allocate independently even when Windows gives channels the same time."""
    moment = now or datetime.now()
    prefix = moment.strftime('run-%Y%m%d-%H%M%S-')
    for offset in range(1000):
        candidate = Path(root) / (prefix + f'{(moment.microsecond + offset) % 1000000:06d}')
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError('无法分配独立记录目录，请稍后重试。')


def analyze_video(args, weights, *, on_frame=None, on_started=None, stop_event=None, realtime=False, report=print):
    if not 0 < args.conf <= 1 or (args.max_frames is not None and args.max_frames <= 0):
        raise ValueError('检测阈值或帧数限制无效。')
    from core.scene_fusion import SceneFusionEngine
    context = getattr(args, 'scene_context', None)
    fusion_engine = SceneFusionEngine(context, shadow_model_path=getattr(args, 'fusion_shadow_model', None)) if context is not None else None
    from core.event_profiles import ASSOCIATION_NAMES, create_event_tracker, validate_association
    profile = getattr(args, 'model_profile', 'original')
    association = getattr(args, 'association_profile', 'original')
    validate_association(association, profile, args.baseline)
    from core.confirmation import CONFIRMATION_NAMES, FAST_POLICY, validate_confirmation
    confirmation = getattr(args, 'confirmation_profile', 'standard')
    validate_confirmation(confirmation)
    from core.monitoring import MonitoringScheduler
    scheduler = MonitoringScheduler(getattr(args, 'monitoring_profile', 'full'))
    if args.baseline and scheduler.profile != 'full':
        raise ValueError('单帧基线需要完整逐帧检测，请使用完整检测模式。')
    import cv2
    from core.model_profiles import create_detector, PROFILE_NAMES, selection_metadata

    source_kind = getattr(args, 'source_kind', 'file')
    if source_kind not in ('file', 'camera'):
        raise ValueError('输入类型无效。')
    live = source_kind == 'camera'
    from core.camera import CameraCapture, CameraDisconnected
    from core.storage import enforce_quota, StorageLimitError
    if live:
        from core.camera import validate_camera_index
        camera_index = validate_camera_index(args.source)
        source = f'摄像头 {camera_index}'
        capture = CameraCapture(camera_index, stop_event)
    else:
        source = Path(args.source).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f'视频文件不存在：{source}')
        if getattr(args, 'serialize_inference', False) and hasattr(cv2, 'CAP_PROP_N_THREADS'):
            # Avoid one large FFmpeg decoding pool per monitored file.
            capture = cv2.VideoCapture(str(source), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, 1])
        else:
            capture = cv2.VideoCapture(str(source))
        if not capture.isOpened():
            capture.release()
            raise RuntimeError(f'无法读取视频：{source}；请检查文件及编码格式。')
    recorder = None
    tracker = None
    risk_engine = None
    trend_engine = notification_engine = None
    last_trend = None
    notification_log = None
    model_forward_passes = 0
    inference_elapsed_s = 0.0
    fps = 0.0
    shown = False
    frames = detected_frames = 0
    last_timestamp = 0.0
    warnings = []
    evidence_images = {}
    evidence_sources = {}
    started = time.perf_counter()
    status = 'failed'
    error = None
    try:
        output = create_run_directory(ROOT / 'outputs')
        (output / '.active').write_text(str(__import__('os').getpid()), encoding='ascii')
    except OSError:
        capture.release()
        raise
    try:
        if on_started:
            on_started({'output': str(output), 'stage': 'loading'})
        storage_gb = getattr(args, 'storage_max_gb', 0)
        def check_storage():
            if storage_gb:
                enforce_quota(ROOT / 'outputs', max_bytes=storage_gb * 1024 ** 3,
                              retention_days=getattr(args, 'storage_retention_days', 30),
                              automatic=getattr(args, 'storage_automatic', False),
                              exclude=getattr(args, 'storage_exclude', ()))
        check_storage()
        last_storage_check = time.monotonic()
        fps = 30.0 if live else float(capture.get(cv2.CAP_PROP_FPS))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('视频帧率无效，无法可靠计算视频时间。')
        declared_frames = 0 if live else int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        with _MONITOR_INFERENCE_LOCK if getattr(args, 'serialize_inference', False) else nullcontext():
            if getattr(args, 'serialize_inference', False):
                from core.inference_queue import queued_detector
                detector = queued_detector(weights, args.device, args.conf, profile)
            else:
                detector = create_detector(weights, args.device, args.conf, profile)
        from core.recording import VideoRecorder
        recorder = VideoRecorder(output, fps, getattr(args, 'recording_mode', 'full'))
        if not args.baseline:
            from core.temporal import TemporalConfig
            temporal_config = TemporalConfig(**json.loads(Path(args.temporal_config).read_text(encoding='utf-8')))
            tracker = create_event_tracker(temporal_config, association, confirmation)
            if not args.temporal_only:
                from core.risk import RiskConfig, RiskEngine
                risk_config = RiskConfig(**json.loads(Path(args.risk_config).read_text(encoding='utf-8')))
                risk_engine = RiskEngine(risk_config)
                if fusion_engine:
                    fusion_engine.risk_config = risk_config
                from core.trend import TrendEngine
                from core.notifications import NotificationConfig, NotificationEngine
                trend_engine = TrendEngine()
                notification_engine = NotificationEngine(NotificationConfig(sound_enabled=bool(getattr(args, 'alert_sound', False))))
        report(f'模式：{PROFILE_NAMES[profile]}；设备：{detector.device}；输入：{source if live else source.name}；阈值：{args.conf}')
        report('视频窗口中按 Q 或 Esc 停止。' if not args.headless else '无窗口模式。')
        config = {'source': str(source), 'source_kind': source_kind, 'weights': str(weights), 'source_fps': None if live else fps,
                  'declared_frames': declared_frames, 'device': detector.device,
                  'confidence_threshold': args.conf, 'image_size': 640,
                  'model_profile': profile, 'model_profile_name': PROFILE_NAMES[profile],
                  'scene_selection': selection_metadata(profile),
                  'association_profile': association, 'association_profile_name': ASSOCIATION_NAMES[association],
                  'event_region_config': asdict(tracker.region_config) if hasattr(tracker, 'region_config') else None,
                  'frame_size': [capture.metadata.get('width', 0), capture.metadata.get('height', 0)] if live else
                                [int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))],
                  'event_mode': 'single_frame_baseline' if args.baseline else 'temporal',
                  'temporal_config': asdict(temporal_config) if tracker else None,
                  'confirmation_profile': confirmation if tracker else None,
                  'confirmation_profile_name': CONFIRMATION_NAMES[confirmation] if tracker else None,
                  'fast_confirmation_policy': asdict(FAST_POLICY) if tracker and confirmation == 'fast' else None,
                  'risk_config': asdict(risk_engine.config) if risk_engine else None}
        if fusion_engine:
            from core.context_scoring import RULES
            config['scene_fusion'] = {**RULES, 'policy': 'scene-score-v2',
                  'upgrade_hold_s': risk_engine.config.upgrade_hold_s if risk_engine else .5,
                  'downgrade_hold_s': risk_engine.config.downgrade_hold_s if risk_engine else 1.,
                  'note': 'Dynamic untrained review-index rules; raw risk logs remain separate.'}
            config['adaptive_fusion'] = fusion_engine.shadow.metadata()
        config.update(monitoring_profile=scheduler.profile, monitoring_config=asdict(scheduler.config),
                      recording_mode=recorder.mode,
                      storage_policy={'max_gb': storage_gb, 'retention_days': getattr(args, 'storage_retention_days', 30),
                                      'automatic': getattr(args, 'storage_automatic', False)},
                      trend_config=asdict(trend_engine.config) if trend_engine else None,
                      notification_config=asdict(notification_engine.config) if notification_engine else None,
                      measurement_policy='Skipped frames are unobserved; no negative detection or temporal vote is fabricated.')
        if getattr(args, 'monitoring_point', None):
            point = args.monitoring_point
            config['monitoring_point'] = {key: str(point.get(key, ''))[:160] for key in ('id', 'name', 'location')}
        if getattr(args, 'serialize_inference', False):
            config['inference_scheduling'] = 'independent_channels_shared_single_thread_cpu_model'
            config['cpu_backend'] = {'threads': 2, 'mkldnn': False}
        if scheduler.profile == 'adaptive':
            config['image_size'] = None
            config['image_sizes'] = [scheduler.config.idle_image_size, scheduler.config.enhanced_image_size]
            config['validation_note'] = 'Adaptive sampling/resolution is an optional product prototype; prior fixed-640 results do not validate this mode.'
        config['playback_pacing'] = 'live_latest_frame' if live else 'source_fps_when_possible' if realtime else 'unpaced' if args.headless else 'opencv_display_delay'
        if live:
            config.update(camera_index=camera_index, camera_backend=capture.metadata.get('backend'),
                          timestamp_policy='PC monotonic receive time, relative to first analyzed frame; not phone exposure time.',
                          recording_fps=fps, recording_timestamps='frame-times.jsonl',
                          recording_note='Processed frames only; use timestamp map for event replay. Dropped capture frames are unobserved.')
        if recorder.mode != 'full':
            config['recording_timestamps'] = 'per-clip indexes' if recorder.mode == 'events' else None
            config['recording_note'] = ('Only processed frames around confirmation/upgrades, up to 5s before/10s after; '
                                        'memory bounded to 32MiB/180 frames and segments to 60s.'
                                        if recorder.mode == 'events' else 'No video saved; confirmation screenshots and logs remain.')
        if tracker and confirmation == 'fast':
            config['confirmation_validation_note'] = 'Optional faster response. Reused development logs showed additional false fire/smoke episodes; not independently validated.'
        manifest_path = ASSET_ROOT / 'models' / 'manifest.json'
        if hasattr(detector, 'model_manifest'):
            config['model_manifest'] = detector.model_manifest
            if detector.model_manifest.get('kind') == 'scene_checkpoint':
                config['weights'] = detector.model_manifest['branches']['fire']['weights']
        elif manifest_path.is_file():
            config['model_manifest'] = json.loads(manifest_path.read_text(encoding='utf-8'))
        if scheduler.profile == 'adaptive' and 'model_manifest' in config:
            manifest = config['model_manifest']
            config['model_manifest'] = {**manifest,
                'validated_reference_image_size': manifest.get('image_size', 640),
                'image_size': None,
                'runtime_input_policy': 'Adaptive targets 416/960; actual target is recorded per frame. Historical fixed-size validation does not apply.'}
        write_json(output / 'config.json', config)
        if on_started:
            on_started({'output': str(output), 'stage': 'ready', 'config': config})
        playback_started = time.perf_counter()
        with (output / 'detections.jsonl').open('w', encoding='utf-8') as log, \
                (output / 'temporal.jsonl').open('w', encoding='utf-8') as temporal_log, \
                (output / 'risk.jsonl').open('w', encoding='utf-8') as risk_log, \
                (output / 'risk.csv').open('w', encoding='utf-8', newline='') as risk_csv_file, \
                (output / 'trend.jsonl').open('w', encoding='utf-8') as trend_log, \
                (output / 'monitoring.jsonl').open('w', encoding='utf-8') as monitoring_log, \
                (output / 'alerts.jsonl').open('w', encoding='utf-8') as notification_log, \
                ((output / 'fusion.jsonl').open('w', encoding='utf-8') if fusion_engine else nullcontext(None)) as fusion_log:
            risk_csv = csv.writer(risk_csv_file)
            if risk_engine:
                risk_csv.writerow(['timestamp_s', 'status', 'level', 'score', 'event_id', 'fresh_evidence',
                                   'box_union_coverage', 'growth_pp_per_s', 'positive_duration_s'])
            while True:
                if stop_event is not None and stop_event.is_set():
                    status = 'stopped_by_user'
                    break
                if time.monotonic() - last_storage_check >= 10.:
                    check_storage()
                    last_storage_check = time.monotonic()
                ok, frame = capture.read()
                if not ok:
                    if stop_event is not None and stop_event.is_set():
                        status = 'stopped_by_user'
                        break
                    if frames == 0:
                        raise RuntimeError('视频未包含可解码的帧。')
                    if declared_frames > 0 and frames != declared_frames:
                        warnings.append(f'实际解码 {frames} 帧，容器报告 {declared_frames} 帧；'
                                        '容器帧数可能为估算值，请核对素材完整性。')
                    status = 'completed_with_frame_count_warning' if warnings else 'completed'
                    break
                timestamp = capture.timestamp_s if live else round(frames / fps, 6)
                last_timestamp = timestamp
                if live and frames % 60 == 0 and shutil.disk_usage(output).free < 500 * 1024 ** 2:
                    status = 'storage_limit_reached'
                    warnings.append('可用磁盘不足 500 MB，监控已停止并保存记录；请清理磁盘后重新启动。')
                    break
                plan = scheduler.plan(timestamp)
                snapshot = assessment = forecast = None
                notices = []
                passes = 0
                detections = []
                annotated = frame.copy()
                if plan['perform']:
                    inference_started = time.perf_counter()
                    with _MONITOR_INFERENCE_LOCK if getattr(args, 'serialize_inference', False) else nullcontext():
                        annotated, detections = detector.predict(frame, imgsz=plan['image_size'])
                    passes = 1
                    # Rerun this same frame at enhanced resolution BEFORE updating
                    # temporal evidence. A coarse box is a wake-up signal, not a vote.
                    if scheduler.profile == 'adaptive' and detections:
                        scheduler.escalate(timestamp)
                        if plan['image_size'] != scheduler.config.enhanced_image_size:
                            with _MONITOR_INFERENCE_LOCK if getattr(args, 'serialize_inference', False) else nullcontext():
                                annotated, detections = detector.predict(frame, imgsz=scheduler.config.enhanced_image_size)
                            passes += 1
                            plan = {**plan, 'image_size': scheduler.config.enhanced_image_size,
                                    'reason': '发现疑似目标；本帧已加强复检'}
                    inference_elapsed_s += time.perf_counter() - inference_started
                    model_forward_passes += passes * (2 if profile in ('brightlight', 'modern') else 1)
                log.write(json.dumps({'frame_index': frames, 'timestamp_s': timestamp,
                                      'frame_size': [frame.shape[1], frame.shape[0]],
                                      'observed': plan['perform'], 'image_size': plan['image_size'],
                                      'detections': detections}, ensure_ascii=False) + '\n')
                if tracker and plan['perform']:
                    from core.temporal import draw_event_status
                    snapshot = tracker.update(timestamp, detections, (frame.shape[1], frame.shape[0]))
                    snapshot['observed'] = True
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
                            source_path = evidence_dir / f'event-{transition["event_id"]}-original.jpg'
                            source_ok, source_encoded = cv2.imencode('.jpg', frame)
                            if not source_ok:
                                raise RuntimeError('无法生成事件原画面截图。')
                            source_encoded.tofile(str(source_path))
                            evidence_sources[transition['event_id']] = {
                                'image': source_path.relative_to(output).as_posix(),
                                'timestamp_s': timestamp, 'detections': detections}
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
                        if fusion_engine:
                            assessment = fusion_engine.update(assessment)
                            fusion_log.write(json.dumps(assessment, ensure_ascii=False) + '\n')
                        display_frame = annotated
                        annotated = draw_risk_panel(annotated, assessment)
                        for change in assessment['level_changes']:
                            report(f'{timestamp:.2f}s：事件 #{change["event_id"]} 提示等级 {change["from"]} -> {change["to"]}，指数 {change["score"]:.1f}。')
                        forecast = trend_engine.update(assessment)
                        if forecast['events']:
                            last_trend = forecast
                        trend_log.write(json.dumps(forecast, ensure_ascii=False) + '\n')
                        notices = notification_engine.update(assessment, snapshot)
                        for notice in notices:
                            notification_log.write(json.dumps(notice, ensure_ascii=False) + '\n')
                        if notices:
                            notification_log.flush()  # Allow explicit feedback while analysis is running.
                elif tracker:
                    # Idle sampling never runs while a candidate/confirmed track is
                    # active. Empty display state explicitly marks unobserved time.
                    snapshot = {'timestamp_s': timestamp, 'tracks': [], 'transitions': [], 'observed': False}
                    temporal_log.write(json.dumps(snapshot, ensure_ascii=False) + '\n')
                    if risk_engine:
                        assessment = {'timestamp_s': timestamp, 'status': 'not_observed', 'level': 'NORMAL',
                                      'score': 0.0, 'dominant_event_id': None, 'fresh_evidence': False,
                                      'events': [], 'level_changes': [], 'reasons': ['本帧未检测，等待下一次巡检。']}
                        risk_log.write(json.dumps(assessment, ensure_ascii=False) + '\n')
                        risk_csv.writerow([timestamp, 'not_observed', 'NORMAL', '', '', False, '', '', ''])
                        if fusion_engine:
                            assessment = fusion_engine.update(assessment)
                            fusion_log.write(json.dumps(assessment, ensure_ascii=False) + '\n')
                        from core.risk import draw_risk_panel
                        annotated = draw_risk_panel(annotated, assessment)
                monitoring = scheduler.observe(timestamp, plan, detections=bool(detections),
                                               active_tracks=bool(snapshot and snapshot['tracks']), passes=passes)
                monitoring['stats']['model_forward_passes'] = model_forward_passes
                monitoring['inference_elapsed_s'] = round(inference_elapsed_s, 6)
                monitoring_log.write(json.dumps({'frame_index': frames, 'timestamp_s': timestamp, **monitoring}, ensure_ascii=False) + '\n')
                if not plan['perform']:
                    cv2.putText(annotated, 'IDLE - frame not analyzed', (12, 25), cv2.FONT_HERSHEY_SIMPLEX,
                                .55, (80, 210, 240), 1)
                if 'output_frame_size' not in config:
                    height, width = annotated.shape[:2]
                    config['output_frame_size'] = [width, height]
                    write_json(output / 'config.json', config)
                if list(annotated.shape[1::-1]) != config['output_frame_size']:
                    raise RuntimeError('采集画面尺寸发生变化，已停止监控；请重新连接。')
                triggers = [t['event_id'] for t in (snapshot or {}).get('transitions', []) if t['type'] == 'event_confirmed']
                triggers += [n['event_id'] for n in notices if n['kind'] == 'level_upgrade']
                recorder.write(annotated, timestamp, triggers)
                if notices and notification_log:
                    notification_log.flush()
                frames += 1
                detected_frames += bool(detections)
                if on_frame:
                    on_frame({'frame': display_frame if risk_engine and plan['perform'] else frame if risk_engine else annotated,
                              'raw_frame': frame, 'detections': detections,
                              'source_kind': source_kind,
                              'capture_dropped_frames': capture.dropped_frames if live else 0,
                              'frame_age_s': round(max(0., time.monotonic() - capture.origin - timestamp), 3) if live else None,
                              'frame_index': frames - 1, 'timestamp_s': timestamp,
                              'declared_frames': declared_frames, 'processed_frames': frames,
                              'temporal': snapshot, 'risk': assessment,
                              'fusion_peaks': dict(fusion_engine.peaks) if fusion_engine else {},
                              'trend': forecast, 'monitoring': monitoring, 'notifications': notices,
                              'event_counts': tracker.summary() if tracker else {},
                              'risk_peaks': {item['event_id']: {'peak_level': item['peak_level'],
                                             'peak_score': item['peak_score'], 'peak_evidence': item['peak_evidence']}
                                             for item in (risk_engine.event_summaries[-200:] +
                                                          [{'event_id': key, **value} for key, value in risk_engine.states.items()])}
                                            if risk_engine else {},
                              'evidence_images': evidence_images.copy(),
                              'evidence_sources': evidence_sources.copy(),
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
                if realtime and not live:
                    delay = max(0.0, playback_started + frames / fps - time.perf_counter())
                    if stop_event is not None:
                        stop_event.wait(delay)
                    else:
                        time.sleep(delay)
    except StorageLimitError as exc:
        status = 'storage_limit_reached'
        error = str(exc)
        raise
    except CameraDisconnected as exc:
        status = 'camera_disconnected'
        error = str(exc)
        raise
    except KeyboardInterrupt:
        status = 'stopped_by_user'
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        capture.release()
        if recorder is not None:
            recorder.close()
        if shown:
            cv2.destroyAllWindows()
        if tracker:
            reason = 'stream_end' if status.startswith('completed') else 'processing_error' if status == 'failed' else status
            end_timestamp = last_timestamp if live else round(frames / fps, 6) if fps else 0.0
            closing_transitions = tracker.finish(end_timestamp, reason)
            for event in tracker.closed_events:
                event['evidence_image'] = evidence_images.get(event['event_id'])
                event['evidence_source'] = evidence_sources.get(event['event_id'])
            if risk_engine:
                risk_engine.finish(end_timestamp, reason)
                risk_by_id = {item['event_id']: item for item in risk_engine.event_summaries}
                for event in tracker.closed_events:
                    event['risk_summary'] = risk_by_id.get(event['event_id'])
                    if fusion_engine:
                        event['fusion_summary'] = fusion_engine.peaks.get(event['event_id'])
                write_json(output / 'risk-events.json', {'config': asdict(risk_engine.config),
                           'summary': risk_engine.summary(), 'events': risk_engine.event_summaries})
                closing_notices = notification_engine.finish(end_timestamp, reason)
                with (output / 'alerts.jsonl').open('a', encoding='utf-8') as closing_log:
                    for notice in closing_notices:
                        closing_log.write(json.dumps(notice, ensure_ascii=False) + '\n')
                write_json(output / 'trend-latest.json', last_trend)
            write_json(output / 'events.json', {'config': asdict(tracker.config),
                       'association_profile': association,
                       'confirmation_profile': confirmation,
                       'fast_confirmation_policy': asdict(FAST_POLICY) if confirmation == 'fast' else None,
                       'event_region_config': asdict(tracker.region_config) if hasattr(tracker, 'region_config') else None,
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
            'monitoring': {**scheduler.summary(), 'model_forward_passes': model_forward_passes,
                           'inference_elapsed_s': round(inference_elapsed_s, 6)},
            'notifications': notification_engine.summary() if notification_engine else None,
            'source_kind': source_kind,
            'capture_dropped_frames': capture.dropped_frames if live else 0,
            'last_observation_s': last_timestamp,
            'recording': {'mode': recorder.mode, 'event_clips': len(recorder.clips)} if recorder else None,
            'elapsed_s': round(elapsed, 3),
            'end_to_end_fps': round(frames / elapsed, 3) if elapsed else 0,
            'note': 'Contains model initialization, decoding, inference, drawing, writing and optional display delay. Not an accuracy score.',
        }
        if fusion_engine:
            summary['scene_fusion'] = fusion_engine.summary()
            write_json(output / 'fusion-latest.json', fusion_engine.latest)
            write_json(output / 'fusion-events.json', {'summary': fusion_engine.summary(), 'events': list(fusion_engine.peaks.values())})
        write_json(output / 'summary.json', summary)
        (output / '.active').unlink(missing_ok=True)
    report(f'处理 {frames} 帧；状态：{status}；输出目录：{output}')
    for warning in warnings:
        report(f'提示：{warning}')
    return {'output': str(output), 'summary': summary}

