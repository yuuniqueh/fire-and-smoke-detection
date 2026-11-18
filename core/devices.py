"""Small local monitoring-point registry; location is entered by the operator."""
import json
import os
import tempfile
import uuid
from pathlib import Path

from core.camera import validate_camera_index
from core.confirmation import CONFIRMATION_NAMES, validate_confirmation
from core.event_profiles import ASSOCIATION_NAMES, validate_association
from core.model_profiles import PROFILE_NAMES, profile_availability, selection_metadata


INHERIT = 'inherit'
ANALYSIS_FIELDS = ('model_profile', 'association_profile', 'confirmation_profile')


def validate_device_analysis(value):
    """Validate persisted choices without requiring model files during registry load."""
    if not isinstance(value, dict):
        raise ValueError('监控点检测设置无效。')
    result = {}
    for key, allowed in (('model_profile', PROFILE_NAMES), ('association_profile', ASSOCIATION_NAMES),
                         ('confirmation_profile', CONFIRMATION_NAMES)):
        selected = value.get(key, INHERIT)
        if not isinstance(selected, str) or selected not in (*allowed, INHERIT):
            raise ValueError('未知监控点检测设置：' + key)
        result[key] = selected
    model, association = result['model_profile'], result['association_profile']
    if model != INHERIT and association != INHERIT:
        validate_association(association, model)
    if result['confirmation_profile'] != INHERIT:
        validate_confirmation(result['confirmation_profile'])
    return result


def resolve_device_analysis(device, shared):
    """Resolve actual launch parameters; never substitute another model silently.

    An explicit model uses its default original association unless separately
    overridden. A fully inherited model/association follows the global pair.
    Confirmation speed always inherits independently when left unspecified.
    Runtime detector creation still performs the registered checksum checks.
    """
    if not isinstance(shared, dict):
        raise ValueError('全局检测设置无效。')
    choice = validate_device_analysis(device)
    inherited = choice['model_profile'] == INHERIT
    model = shared.get('model_profile', 'original') if inherited else choice['model_profile']
    if not isinstance(model, str) or model not in PROFILE_NAMES:
        raise ValueError('全局检测模型无效。')
    association = choice['association_profile']
    if association == INHERIT:
        association = shared.get('association_profile', 'original') if inherited else 'original'
    confirmation = choice['confirmation_profile']
    if confirmation == INHERIT:
        confirmation = shared.get('confirmation_profile', 'standard')
    if not isinstance(association, str) or association not in ASSOCIATION_NAMES:
        raise ValueError('全局事件关联方案无效。')
    if not isinstance(confirmation, str) or confirmation not in CONFIRMATION_NAMES:
        raise ValueError('全局确认速度无效。')
    validate_association(association, model, bool(shared.get('baseline', False)))
    validate_confirmation(confirmation)
    availability = profile_availability(model)
    if not availability['ready']:
        raise ValueError(f'{PROFILE_NAMES[model]}模型尚未就绪：' + '、'.join(availability['missing']))
    confidence = .35 if model != 'original' else shared.get('conf', shared.get('confidence', .35))
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.01 <= confidence <= 1.:
        raise ValueError('检测阈值应在0.01到1之间。')
    return {'model_profile': model, 'association_profile': association, 'confirmation_profile': confirmation,
            'conf': confidence, 'scene_selection': selection_metadata(model), 'availability': availability}


def validate_device(value):
    if not isinstance(value, dict):
        raise ValueError('设备信息无效。')
    result = {}
    identifier = value.get('id') or uuid.uuid4().hex
    if not isinstance(identifier, str) or not identifier.isalnum() or len(identifier) > 64:
        raise ValueError('设备编号无效。')
    result['id'] = identifier
    for key, label, limit in (('name', '设备名称', 80), ('location', '监控位置', 160)):
        text = value.get(key, '')
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > limit or any(ord(c) < 32 for c in text):
            raise ValueError(f'请填写有效的{label}，最多{limit}字。')
        result[key] = text.strip()
    kind = value.get('kind', 'file')
    source = value.get('source', '')
    if kind == 'camera':
        source = str(validate_camera_index(source))
    elif kind == 'file':
        if not isinstance(source, str) or not source.strip() or len(source) > 4096:
            raise ValueError('请选择本地录像。')
        path = Path(source).expanduser()
        if not path.is_absolute():
            raise ValueError('录像路径必须为绝对路径。')
        source = str(path.resolve())
    else:
        raise ValueError('当前监控中心支持本地录像和USB／虚拟摄像头。')
    result.update(kind=kind, source=source)
    result.update(validate_device_analysis(value))
    return result


class DeviceRegistry:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        if not self.path.exists():
            return []
        if self.path.is_symlink() or self.path.stat().st_size > 256 * 1024:
            raise ValueError('设备清单无效，请保留原文件后检查。')
        payload = json.loads(self.path.read_text(encoding='utf-8'))
        if payload.get('version') != 1 or not isinstance(payload.get('devices'), list) or len(payload['devices']) > 32:
            raise ValueError('设备清单格式或数量无效。')
        devices = [validate_device(item) for item in payload['devices']]
        if len({d['id'] for d in devices}) != len(devices):
            raise ValueError('设备编号重复。')
        return devices

    def save(self, devices):
        if len(devices) > 32:
            raise ValueError('最多登记32个监控点，最多同时分析4路。')
        checked = [validate_device(d) for d in devices]
        if len({d['id'] for d in checked}) != len(checked):
            raise ValueError('设备编号重复。')
        if self.path.is_symlink():
            raise ValueError('设备清单路径无效。')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='devices-', suffix='.json', dir=str(self.path.parent))
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump({'version': 1, 'devices': checked}, stream, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return checked
