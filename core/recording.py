"""Full recording or bounded event clips; timestamps remain in observation time."""
from collections import deque
import json


class VideoRecorder:
    def __init__(self, output, fps, mode='full', before_s=5., after_s=10., max_clip_s=60.):
        if mode not in ('full', 'events', 'none'):
            raise ValueError('录像方式无效。')
        self.output, self.fps, self.mode = output, fps, mode
        self.before_s, self.after_s, self.max_clip_s = before_s, after_s, max_clip_s
        self.buffer = deque()
        self.buffer_bytes = 0
        self.writer = self.times = None
        self.size = None
        self.clips = []
        self.current = None
        self.deadline = -1.
        self.index = 0
        self.last_written = -1.
        self.closed = False

    def _open(self, name, times_name, frame):
        import cv2
        self.size = [frame.shape[1], frame.shape[0]]
        self.writer = cv2.VideoWriter(str(self.output / name), cv2.VideoWriter_fourcc(*'mp4v'), self.fps, tuple(self.size))
        if not self.writer.isOpened():
            self.writer.release()
            self.writer = None
            raise RuntimeError('无法创建录像，请检查磁盘和视频编码器。')
        self.times = (self.output / times_name).open('w', encoding='utf-8')
        self.index, self.last_written = 0, -1.

    def _write(self, frame, timestamp):
        if [frame.shape[1], frame.shape[0]] != self.size:
            raise RuntimeError('画面尺寸改变，已停止录像；请重新连接。')
        if timestamp <= self.last_written:
            return
        self.writer.write(frame)
        self.times.write(json.dumps({'frame_index': self.index, 'timestamp_s': timestamp}) + '\n')
        self.index += 1
        self.last_written = timestamp
        if self.current:
            self.current.update(end_s=timestamp, frames=self.index)

    def _close_clip(self):
        if self.writer:
            self.writer.release()
            self.writer = None
        if self.times:
            self.times.close()
            self.times = None
        if self.current:
            self.clips.append(self.current)
            self.current = None
        if self.mode == 'events':
            (self.output / 'clips.json').write_text(json.dumps({'clips': self.clips,
                'note': 'Only processed frames around confirmation or level upgrades; not continuous raw footage.'},
                ensure_ascii=False, indent=2), encoding='utf-8')

    def write(self, frame, timestamp, triggers=()):
        if self.mode == 'none':
            return
        if self.mode == 'full':
            if self.writer is None:
                self._open('annotated.mp4', 'frame-times.jsonl', frame)
            self._write(frame, timestamp)
            return
        import cv2
        continuation = None
        if self.writer:
            if timestamp > self.deadline:
                self._close_clip()
            elif timestamp - self.current['start_s'] >= self.max_clip_s:
                continuation = list(self.current['event_ids'])
                self._close_clip()
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise RuntimeError('无法缓冲告警前画面。')
        self.buffer.append((timestamp, encoded))
        self.buffer_bytes += encoded.nbytes
        while self.buffer and (timestamp - self.buffer[0][0] > self.before_s or
                               self.buffer_bytes > 32 * 1024 ** 2 or len(self.buffer) > 180):
            _, old = self.buffer.popleft()
            self.buffer_bytes -= old.nbytes
        ids = sorted(set(triggers))
        if ids:
            self.deadline = timestamp + self.after_s
        if ids or continuation:
            if self.writer is None:
                (self.output / 'clips').mkdir(exist_ok=True)
                number = len(self.clips) + 1
                name, times = f'clips/clip-{number}.mp4', f'clips/clip-{number}.jsonl'
                self._open(name, times, frame)
                buffered = [(timestamp, encoded)] if continuation else list(self.buffer)
                self.current = {'path': name, 'timestamps': times, 'start_s': buffered[0][0],
                                'end_s': timestamp, 'event_ids': sorted(set(ids + (continuation or []))), 'frames': 0}
                for prior_time, data in buffered:
                    decoded = cv2.imdecode(data, cv2.IMREAD_COLOR)
                    self._write(decoded, prior_time)
            else:
                self.current['event_ids'] = sorted(set(self.current['event_ids']) | set(ids))
        if self.writer:
            self._write(frame, timestamp)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self._close_clip()
        self.buffer.clear()
        self.buffer_bytes = 0
