"""Versioned desktop preferences, without input paths or user identity."""
import json
import math
import os
from pathlib import Path
import tempfile

from core.model_profiles import PROFILE_NAMES

DEFAULTS = {'device': 'auto', 'confidence': .35, 'realtime': True,
            'model_profile': 'original', 'association_profile': 'original',
            'monitoring_profile': 'full', 'confirmation_profile': 'standard', 'alert_sound': False,
            'recording_mode': 'events', 'storage_max_gb': 5, 'storage_retention_days': 30, 'storage_automatic': False,
            'window_width': 1280, 'window_height': 920}
CHOICES = {'device': ('auto', 'cpu', '0'), 'model_profile': tuple(PROFILE_NAMES),
           'association_profile': ('original', 'regions'), 'monitoring_profile': ('full', 'adaptive'),
           'confirmation_profile': ('standard', 'fast'), 'recording_mode': ('events', 'full', 'none')}


def validate_preferences(values):
    if not isinstance(values, dict):
        raise ValueError('设置内容应为对象。')
    result = dict(DEFAULTS)
    for key in DEFAULTS:
        if key not in values:
            continue
        value = values[key]
        if key in CHOICES and value not in CHOICES[key]:
            raise ValueError(f'设置选项无效：{key}')
        if key in ('realtime', 'alert_sound', 'storage_automatic') and not isinstance(value, bool):
            raise ValueError(f'设置应为布尔值：{key}')
        if key in ('storage_max_gb', 'storage_retention_days'):
            upper = 1000 if key == 'storage_max_gb' else 3650
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
                raise ValueError(f'存储设置超出有效范围：{key}')
        if key == 'confidence' and (isinstance(value, bool) or not isinstance(value, (int, float))
                                    or not math.isfinite(value) or not .01 <= value <= 1.):
            raise ValueError('检测阈值应在 0.01 到 1 之间。')
        if key.startswith('window_'):
            minimum = 980 if key == 'window_width' else 740
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 7680:
                raise ValueError(f'窗口大小无效：{key}')
        result[key] = value
    if result['model_profile'] != 'brightlight':
        result['association_profile'] = 'original'
    if result['model_profile'] != 'original':
        result['confidence'] = .35
    return result


class PreferencesStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return dict(DEFAULTS), '使用默认设置；修改后会自动保存。'
        try:
            if self.path.is_symlink():
                raise ValueError('设置文件不能是符号链接。')
            if self.path.stat().st_size > 64 * 1024:
                raise ValueError('设置文件过大。')
            payload = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(payload, dict) or payload.get('schema_version') != 1:
                raise ValueError('设置文件版本不支持。')
            return validate_preferences(payload.get('settings')), '已恢复上次保存的设置。'
        except (ValueError, OSError) as exc:
            return dict(DEFAULTS), f'设置未能恢复，已使用默认值；原文件保留。原因：{exc}'

    def save(self, values):
        values = validate_preferences(values)
        if self.path.is_symlink():
            raise ValueError('设置文件不能是符号链接。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='desktop-settings-', suffix='.tmp', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'schema_version': 1, 'settings': values}, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return values
