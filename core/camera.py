"""Local cameras in a cancellable subprocess; a bounded queue keeps recent frames.

No network URL is accepted here. Android camera tools must expose a Windows
camera device. Capture timestamps describe frames received by this PC, not the
phone sensor's exposure time or network latency.
"""
import multiprocessing as mp
from queue import Empty, Full
import sys
import time


class CameraDisconnected(RuntimeError):
    pass


def validate_camera_index(value):
    if isinstance(value, bool):
        raise ValueError('摄像头编号应为 0–15 的整数。')
    try:
        index = int(value)
    except (TypeError, ValueError):
        raise ValueError('摄像头编号应为 0–15 的整数。') from None
    if str(index) != str(value) or not 0 <= index <= 15:
        raise ValueError('摄像头编号应为 0–15 的整数。')
    return index


def _capture_camera(index, frames, messages, stopping):
    """Own the driver here: even a stuck open/read can be stopped by the parent."""
    import cv2
    capture = None
    try:
        backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == 'win32' else [cv2.CAP_ANY]
        for backend in backends:
            capture = cv2.VideoCapture(index, backend)
            if capture.isOpened():
                break
            capture.release()
        if not capture.isOpened():
            raise RuntimeError('无法打开摄像头，请检查编号、Windows 摄像头权限，以及是否被其他软件占用。')
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        capture.set(cv2.CAP_PROP_FPS, 30)
        sequence = 0
        while not stopping.is_set():
            ok, frame = capture.read()
            if not ok or frame is None:
                raise RuntimeError('摄像头画面已中断，请检查手机连接或 USB 设备，再重新连接。')
            if frame.ndim != 3 or frame.shape[2] != 3 or frame.size > 4096 * 2160 * 3:
                raise RuntimeError('摄像头画面格式或尺寸不受支持。')
            captured_at = time.monotonic()
            if sequence == 0:
                messages.put({'kind': 'ready', 'width': frame.shape[1], 'height': frame.shape[0],
                              'backend': capture.getBackendName()})
            item = (sequence, captured_at, frame)
            try:
                frames.put_nowait(item)
            except Full:
                # The queue holds at most two frames; never accumulate a video
                # backlog while model initialization or inference is slow.
                try:
                    frames.get_nowait()
                except Empty:
                    pass
                try:
                    frames.put_nowait(item)
                except Full:
                    pass
            sequence += 1
    except Exception as exc:
        messages.put({'kind': 'error', 'message': str(exc)})
    finally:
        if capture is not None:
            capture.release()
        frames.cancel_join_thread()


class CameraCapture:
    def __init__(self, index, stop_event=None, *, open_timeout=10., read_timeout=5., _target=None):
        self.index = validate_camera_index(index)
        self.stop_event = stop_event
        self.read_timeout = read_timeout
        self.origin = None
        self.timestamp_s = 0.
        self.sequence = -1
        self.dropped_frames = 0
        self.frame_age_s = 0.
        self.metadata = {}
        self._released = False
        context = mp.get_context('spawn')
        self._frames = context.Queue(maxsize=2)
        self._messages = context.Queue(maxsize=4)
        self._stopping = context.Event()
        self._process = context.Process(target=_target or _capture_camera,
                                        args=(self.index, self._frames, self._messages, self._stopping),
                                        daemon=True)
        try:
            self._process.start()
            deadline = time.monotonic() + open_timeout
            while time.monotonic() < deadline:
                if self.cancelled:
                    return
                try:
                    info = self._messages.get(timeout=.05)
                except Empty:
                    if not self._process.is_alive():
                        raise CameraDisconnected('摄像头采集进程已退出，请重新连接。')
                    continue
                if info['kind'] == 'error':
                    raise CameraDisconnected(info['message'])
                self.metadata = info
                return
            raise CameraDisconnected('连接摄像头超时，请检查设备编号、权限及手机连接。')
        except BaseException:
            self.release()
            raise

    @property
    def cancelled(self):
        return self.stop_event is not None and self.stop_event.is_set()

    def read(self):
        deadline = time.monotonic() + self.read_timeout
        while not self.cancelled:
            if time.monotonic() >= deadline:
                raise CameraDisconnected('摄像头没有提供新的有效画面，请检查连接后重试。')
            try:
                info = self._messages.get_nowait()
            except Empty:
                info = None
            if info and info['kind'] == 'error':
                raise CameraDisconnected(info['message'])
            try:
                item = self._frames.get(timeout=.05)
            except Empty:
                if not self._process.is_alive():
                    raise CameraDisconnected('摄像头采集进程已退出，请重新连接。')
                if time.monotonic() >= deadline:
                    raise CameraDisconnected('超过 5 秒未收到摄像头画面；监控已中断，请检查连接后重试。')
                continue
            # Drain older frames that are already available without blocking.
            while True:
                try:
                    item = self._frames.get_nowait()
                except Empty:
                    break
            sequence, captured_at, frame = item
            if time.monotonic() - captured_at > self.read_timeout:
                continue
            if self.origin is None:
                self.origin = captured_at
            self.dropped_frames += max(0, sequence - self.sequence - 1) if self.sequence >= 0 else 0
            self.sequence = sequence
            self.timestamp_s = round(captured_at - self.origin, 6)
            self.frame_age_s = max(0., time.monotonic() - captured_at)
            return True, frame
        return False, None

    def release(self):
        if self._released:
            return
        self._released = True
        self._stopping.set()
        if self._process.pid is not None:
            self._process.join(timeout=.5)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=2.)
            self._process.close()
        for queue in (self._frames, self._messages):
            queue.cancel_join_thread()
            queue.close()
