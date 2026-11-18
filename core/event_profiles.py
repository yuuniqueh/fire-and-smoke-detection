"""Explicit association selection; the original tracker remains the default."""
from core.temporal import EventTracker

ASSOCIATION_NAMES = {'original': '原关联', 'regions': '稳定区域（候选）'}


def validate_association(profile, model_profile='original', baseline=False):
    if profile not in ASSOCIATION_NAMES:
        raise ValueError(f'未知事件关联方案：{profile}')
    if profile == 'regions' and (model_profile != 'brightlight' or baseline):
        raise ValueError('稳定区域候选仅用于 brightlight 检测组合，且需要开启时序模块。')


def create_event_tracker(config, profile='original', confirmation_profile='standard'):
    if profile == 'original':
        return EventTracker(config, confirmation_profile)
    if profile == 'regions':
        from core.event_regions import StableRegionTracker
        return StableRegionTracker(config, confirmation_profile=confirmation_profile)
    raise ValueError(f'未知事件关联方案：{profile}')
