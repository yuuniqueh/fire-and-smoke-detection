"""Response policies, separate from the historical temporal baseline."""
from dataclasses import dataclass


CONFIRMATION_NAMES = {'fast': '快速响应 · 稳定约 0.5 秒', 'standard': '标准观察 · 至少 1 秒'}


@dataclass(frozen=True)
class FastConfirmationPolicy:
    window_s: float = .5
    min_positive_s: float = .45
    min_presence_ratio: float = .9
    min_samples: int = 4
    max_sample_gap_s: float = .2


def validate_confirmation(profile):
    if profile not in CONFIRMATION_NAMES:
        raise ValueError(f'未知确认速度：{profile}')


FAST_POLICY = FastConfirmationPolicy()
