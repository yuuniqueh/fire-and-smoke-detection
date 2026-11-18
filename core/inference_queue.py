"""One owning inference thread, shared model cache and bounded channel requests."""
from concurrent.futures import Future
from pathlib import Path
from queue import Queue
from threading import Lock, Thread


class _InferenceQueue:
    def __init__(self):
        self.jobs = Queue(maxsize=8)
        self.models = {}
        self.thread = Thread(target=self._run, name='FireGuard-shared-inference', daemon=True)
        self.thread.start()

    def call(self, operation, *args):
        future = Future()
        self.jobs.put((future, operation, args), timeout=30)
        return future.result(timeout=180)

    def _run(self):
        import torch
        # Keep a single CPU kernel owner and a small native thread pool on the
        # 16-GiB desktop. Per-channel capture and event state remain independent.
        torch.set_num_threads(2)
        torch.backends.mkldnn.enabled = False
        from core.model_profiles import create_detector
        while True:
            future, operation, args = self.jobs.get()
            try:
                if operation == 'load':
                    key, weights, device, confidence, profile = args
                    if key not in self.models:
                        self.models[key] = create_detector(weights, device, confidence, profile)
                    detector = self.models[key]
                    result = {'device': detector.device}
                    if hasattr(detector, 'model_manifest'):
                        result['model_manifest'] = detector.model_manifest
                else:
                    key, frame, kwargs = args
                    result = self.models[key].predict(frame, **kwargs)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)
            finally:
                self.jobs.task_done()


_LOCK = Lock()
_QUEUE = None


def queued_detector(weights, device, confidence, profile):
    global _QUEUE
    with _LOCK:
        if _QUEUE is None:
            _QUEUE = _InferenceQueue()
    path = Path(weights).resolve()
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size, str(device), confidence, profile)
    metadata = _QUEUE.call('load', key, path, device, confidence, profile)

    class DetectorProxy:
        def __init__(self):
            self.device = metadata['device']
            if 'model_manifest' in metadata:
                self.model_manifest = metadata['model_manifest']

        def predict(self, frame, **kwargs):
            return _QUEUE.call('predict', key, frame, kwargs)

    return DetectorProxy()
