"""Video-time compute scheduling; skipped frames are unobserved, never negatives."""
from dataclasses import asdict, dataclass
import math


MONITORING_NAMES = {'full': '完整检测', 'adaptive': '节能监测（试用）'}


@dataclass(frozen=True)
class MonitoringConfig:
    idle_hz: float = 2.0
    idle_image_size: int = 416
    enhanced_image_size: int = 960
    audit_interval_s: float = 5.0
    enhanced_hold_s: float = 8.0
    quiet_release_s: float = 3.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f'{name} must be a positive finite number')
        for name in ('idle_image_size', 'enhanced_image_size'):
            value = getattr(self, name)
            if not isinstance(value, int) or value % 32 or not 320 <= value <= 1280:
                raise ValueError(f'{name} must be a multiple of 32 in [320, 1280]')
        if self.idle_image_size > self.enhanced_image_size:
            raise ValueError('idle resolution must not exceed enhanced resolution')


class MonitoringScheduler:
    def __init__(self, profile='full', config=None):
        if profile not in MONITORING_NAMES:
            raise ValueError('未知监测模式：' + str(profile))
        self.profile = profile
        self.config = config or MonitoringConfig()
        self.state = 'full' if profile == 'full' else 'idle'
        self.last_timestamp = None
        self.last_probe = None
        self.last_audit = None
        self.enhanced_since = None
        self.last_activity = None
        self.stats = dict(decoded_frames=0, analyzed_frames=0, skipped_frames=0,
                          inference_passes=0, enhanced_frames=0)

    def plan(self, timestamp):
        if not math.isfinite(timestamp) or timestamp < 0 or (self.last_timestamp is not None and timestamp <= self.last_timestamp):
            raise ValueError('monitoring timestamps must increase strictly')
        self.last_timestamp = timestamp
        self.stats['decoded_frames'] += 1
        if self.state == 'full':
            return {'perform': True, 'image_size': 640, 'reason': '完整模式逐帧检测'}
        if self.state == 'enhanced':
            return {'perform': True, 'image_size': self.config.enhanced_image_size, 'reason': '疑似事件加强观察'}
        # The first observation is a high-resolution audit. Weak candidates do not
        # need to pass the low-resolution branch to ever be seen at high resolution.
        audit = self.last_audit is None or timestamp - self.last_audit + 1e-9 >= self.config.audit_interval_s
        probe = self.last_probe is None or timestamp - self.last_probe + 1e-9 >= 1 / self.config.idle_hz
        if audit or probe:
            self.last_probe = timestamp
            if audit:
                self.last_audit = timestamp
            return {'perform': True, 'image_size': self.config.enhanced_image_size if audit else self.config.idle_image_size,
                    'reason': '定期高分辨率巡检' if audit else '低频巡检'}
        return {'perform': False, 'image_size': None, 'reason': '等待下一次巡检；本帧未检测'}

    def escalate(self, timestamp):
        if self.profile != 'adaptive':
            return False
        entering = self.state != 'enhanced'
        if entering:
            self.enhanced_since = timestamp
        self.state = 'enhanced'
        self.last_activity = timestamp
        return entering

    def observe(self, timestamp, plan, *, detections=False, active_tracks=False, passes=0):
        if timestamp != self.last_timestamp:
            raise ValueError('observation must belong to the current planned frame')
        if plan['perform']:
            self.stats['analyzed_frames'] += 1
            self.stats['inference_passes'] += passes
            if self.state == 'enhanced':
                self.stats['enhanced_frames'] += 1
            if detections or active_tracks:
                self.escalate(timestamp)
            elif self.state == 'enhanced' and (
                    timestamp - self.enhanced_since + 1e-9 >= self.config.enhanced_hold_s and
                    timestamp - self.last_activity + 1e-9 >= self.config.quiet_release_s):
                self.state = 'idle'
                self.last_probe = timestamp
                self.last_audit = timestamp  # This frame was already a high-res observation.
        else:
            self.stats['skipped_frames'] += 1
        return {'profile': self.profile, 'state': self.state, 'inference_performed': plan['perform'],
                'image_size': plan['image_size'], 'reason': plan['reason'], 'stats': self.stats.copy()}

    def summary(self):
        return {'profile': self.profile, 'config': asdict(self.config), **self.stats,
                'analyzed_frame_ratio': self.stats['analyzed_frames'] / self.stats['decoded_frames'] if self.stats['decoded_frames'] else None,
                'note': 'Counts describe compute scheduling, not measured energy savings. Sparse sampling can miss short or weak events.'}
