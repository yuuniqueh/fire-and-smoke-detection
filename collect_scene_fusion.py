"""Collect unlabelled causal scene/detector observations from local video files."""
import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
import time
import urllib.request

from core.runtime import ROOT, configure_runtime
from core.pipeline import AnalysisOptions, analyze_video
from core.scene_fusion import SceneContextStore
from core.scene_review import analyze_scene, encode_frame, local_endpoint, save_scene_review
from core.fusion_dataset import prepare_dataset


def collect_video(source, *, endpoint, model, device='0', max_seconds=22.):
    import cv2
    source = Path(source).resolve()
    capture = cv2.VideoCapture(str(source))
    fps = capture.get(cv2.CAP_PROP_FPS)
    capture.release()
    if not source.is_file() or fps <= 0:
        raise ValueError('无法打开本地视频：' + str(source))
    store = SceneContextStore()
    options = AnalysisOptions(source=str(source), device=device, scene_context=store,
                              max_frames=max(1, round(max_seconds * fps)), recording_mode='events')
    frames = deque(maxlen=3)
    last_frame, last_request = -float('inf'), -float('inf')
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='scene-collection')
    pending = None
    state = {'run': None, 'reviews': 0, 'errors': []}
    started = time.monotonic()
    last_report = -float('inf')

    def review(captured):
        result = analyze_scene(captured, endpoint=endpoint, model=model, profile='fast', timeout=75.)
        record = save_scene_review(state['run'], None, captured, result,
            device={'id': 'collection-local-video', 'name': source.stem, 'location': '本地录像，实际位置待登记'})
        store.publish(record)
        return record

    def consume_future():
        nonlocal pending
        if pending is not None and pending.done():
            try:
                pending.result()
                state['reviews'] += 1
            except Exception as exc:
                state['errors'].append(str(exc)[:400])
            pending = None

    def on_frame(packet):
        nonlocal last_frame, last_request, pending, last_report
        stamp = packet['timestamp_s']
        consume_future()
        if stamp - last_frame >= .5:
            frames.append(encode_frame(packet['raw_frame'], stamp))
            last_frame = stamp
        if (pending is None and len(frames) >= 2 and frames[-1]['timestamp_s'] - frames[0]['timestamp_s'] >= .8
                and stamp - last_request >= 8.):
            pending = pool.submit(review, list(frames))
            last_request = stamp
        elapsed = time.monotonic() - started
        if elapsed - last_report >= 5.:
            print(json.dumps({'source': source.name, 'video_s': round(stamp, 2),
                              'elapsed_s': round(elapsed, 2), 'complete_reviews': state['reviews']}, ensure_ascii=False), flush=True)
            last_report = elapsed

    try:
        result = analyze_video(options, ROOT / 'models/best.pt', realtime=True,
            on_started=lambda value: state.update(run=value['output']), on_frame=on_frame,
            report=lambda value: None)
    finally:
        # A late result is retained but has no causal availability log and cannot
        # be silently treated as knowledge available earlier in the video.
        pool.shutdown(wait=True)
        consume_future()
    return {'source': str(source), 'run': result['output'], 'scene_reviews': state['reviews'],
            'errors': state['errors'], 'labels_generated': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description='从本地录像采集融合审核素材；不自动生成正常/异常真值')
    parser.add_argument('--videos', nargs='+', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--device', default='0')
    parser.add_argument('--max-seconds', type=float, default=22.)
    parser.add_argument('--endpoint', default='http://127.0.0.1:11435')
    parser.add_argument('--model', default='qwen3-vl:2b')
    args = parser.parse_args(argv)
    import math
    if not math.isfinite(args.max_seconds) or not 2 <= args.max_seconds <= 600:
        parser.error('每段采集时长须在2到600秒之间。')
    destination = Path(args.out).resolve()
    if destination.exists():
        parser.error('输出目录已存在，请使用新目录以保留此前审核记录。')
    configure_runtime()
    endpoint = local_endpoint(args.endpoint)
    try:
        with urllib.request.urlopen(endpoint + '/api/version', timeout=5.) as response:
            status = json.loads(response.read(8192))
            if not status.get('ready'):
                raise RuntimeError('本机场景模型尚未就绪，请先启动本机AI服务。')
        cases = [collect_video(source, endpoint=endpoint, model=args.model, device=args.device,
                               max_seconds=args.max_seconds) for source in args.videos]
        manifest = prepare_dataset([case['run'] for case in cases], destination)
        receipt = {'created_at': datetime.now().astimezone().isoformat(), 'cases': cases,
                   'candidate_samples': len(manifest['samples']), 'labels_generated': 0,
                   'model_trained': False, 'note': '开发素材采集；等待两位独立人工审核，不代表准确率测试。'}
        (destination / 'collection.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, '采集未完成：' + str(exc) + '\n')
    print(json.dumps({'dataset': str(destination), **receipt}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
