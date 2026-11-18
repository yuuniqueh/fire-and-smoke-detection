"""Locate saved review media and derive a bounded event review window."""
import math
import json
from array import array
from bisect import bisect_left, bisect_right
from pathlib import Path


def review_window(event, focus_s=None, before_s=5.0, after_s=10.0):
    """Focus on the selected notice or confirmation, not the whole long event."""
    focus = event.get('confirmed_at_s') if focus_s is None else focus_s
    values = (focus, before_s, after_s)
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) or v < 0 for v in values) or after_s <= 0:
        raise ValueError('回放时间无效。')
    return {'focus_s': float(focus), 'start_s': max(0.0, focus - before_s),
            'end_s': focus + after_s}


def review_media(run_path, config, focus_s=None):
    """Prefer original footage. Never use an unrelated currently selected input."""
    root = Path(run_path).resolve()
    media = []
    source = config.get('source')
    if source and config.get('source_kind', 'file') != 'camera':
        source_path = Path(source).expanduser()
        # Relative paths from moved records cannot be reliably resolved.
        if source_path.is_absolute() and source_path.is_file():
            media.append({'kind': 'original', 'label': '原视频 · 人工复核', 'path': str(source_path)})
    annotated = root / 'annotated.mp4'
    if annotated.is_file() and not annotated.is_symlink():
        media.append({'kind': 'annotated', 'label': '检测结果视频 · 含模型标记', 'path': str(annotated)})
        if config.get('source_kind') == 'camera':
            media[-1].update(timestamps=str(root / 'frame-times.jsonl'),
                             label='实时监控记录 · 已处理帧 · 含模型标记')
    clips_index = root / 'clips.json'
    if clips_index.is_file() and not clips_index.is_symlink():
        clips = json.loads(clips_index.read_text(encoding='utf-8')).get('clips', [])
        if focus_s is not None:
            clips = sorted(clips, key=lambda c: max(c['start_s'] - focus_s, focus_s - c['end_s'], 0))
        for clip in clips:
            path, times = root / clip['path'], root / clip['timestamps']
            if (path.parent != root / 'clips' or times.parent != root / 'clips'
                    or path.suffix != '.mp4' or times.suffix != '.jsonl'
                    or path.resolve() != path or times.resolve() != times
                    or not path.is_file() or not times.is_file()):
                continue
            media.append({'kind': 'annotated', 'label': f'事件片段 · {clip["start_s"]:.1f}–{clip["end_s"]:.1f}s',
                          'path': str(path), 'timestamps': str(times)})
    return media


class ReplayTimeline:
    """Map saved frames to observation time; live recordings are not constant-rate observations."""
    def __init__(self, fps, count, timestamps=None):
        self.fps, self.count = fps, count
        self.times = None
        if timestamps is not None:
            self.times = array('d')
            path = Path(timestamps)
            if not path.is_file() or path.is_symlink():
                raise ValueError('实时监控时间索引缺失，不能按事件时间可靠回放。')
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    if len(self.times) >= count:
                        raise ValueError('实时监控时间索引与视频帧数不一致。')
                    row = json.loads(line)
                    timestamp = row['timestamp_s']
                    if (row['frame_index'] != len(self.times) or isinstance(timestamp, bool)
                            or not isinstance(timestamp, (float, int)) or not math.isfinite(timestamp)
                            or timestamp < 0 or (self.times and timestamp <= self.times[-1])):
                        raise ValueError('实时监控时间索引无效。')
                    self.times.append(timestamp)
            if len(self.times) != count:
                raise ValueError('实时监控时间索引与视频帧数不一致。')

    def time(self, index):
        return self.times[index] if self.times is not None else index / self.fps

    def locate(self, window):
        if self.times is None:
            start = min(self.count - 1, max(0, math.floor(window['start_s'] * self.fps)))
            end = min(self.count - 1, max(start, math.ceil(window['end_s'] * self.fps) - 1))
            requested = round(window['focus_s'] * self.fps)
            focus = min(end, max(start, requested))
            clamped = focus != requested
        else:
            start = min(self.count - 1, max(0, bisect_right(self.times, window['start_s']) - 1))
            end = min(self.count - 1, max(start, bisect_left(self.times, window['end_s'])))
            right = min(end, max(start, bisect_left(self.times, window['focus_s'])))
            focus = min((right, max(start, right - 1)), key=lambda i: abs(self.time(i) - window['focus_s']))
            clamped = not self.time(start) <= window['focus_s'] <= self.time(end)
        return start, end, focus, clamped
