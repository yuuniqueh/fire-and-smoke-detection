"""Human conclusions and work status are separate from detector risk levels."""
CONCLUSIONS = (('正常用火', 'normal_use'), ('识别误报', 'false_positive'),
               ('确认异常', 'confirmed_abnormal'), ('继续核查', 'needs_verification'))
LEGACY_CONCLUSIONS = (('继续核查', 'uncertain'), ('已处理', 'resolved'))
CONCLUSION_NAMES = dict((value, label) for label, value in CONCLUSIONS + LEGACY_CONCLUSIONS)
PHASES = {
    'pending': {'label': '待核查', 'color': '#b82f48', 'background': '#ffedf1'},
    'action': {'label': '待处理', 'color': '#a45b10', 'background': '#fff1dd'},
    'completed': {'label': '已处理', 'color': '#19764b', 'background': '#e7f6ee'},
}


def phase(action):
    if action in ('normal_use', 'false_positive', 'resolved'):
        return 'completed'
    return 'action' if action == 'confirmed_abnormal' else 'pending'


def presentation(action):
    key = phase(action)
    return {'phase': key, **PHASES[key], 'conclusion': CONCLUSION_NAMES.get(action, ''),
            'acknowledged_only': action == 'acknowledged'}


def conclusion_label(action, saved=None):
    value = (saved or {}).get('conclusion') if action == 'resolved' else action
    return CONCLUSION_NAMES.get(value, '') if value != 'resolved' else ''


def matches(action, selected):
    if selected in ('', 'all'):
        return True
    if selected == 'pending':  # Legacy filter means all unfinished work.
        return phase(action) != 'completed'
    if selected in PHASES:
        return phase(action) == selected
    if selected == 'review':
        return phase(action) == 'pending'
    return action == selected


def reopen_action(action, saved=None):
    """Undo completion without asserting a replacement factual conclusion."""
    if phase(action) != 'completed':
        raise ValueError('只有已处理的记录可以撤回。')
    if action == 'resolved' and (saved or {}).get('conclusion') == 'confirmed_abnormal':
        return 'confirmed_abnormal'
    return 'needs_verification'


def reopen_label(action, saved=None):
    return '撤回处理' if action == 'resolved' else '撤回结论'


def history_action_label(record):
    action = record.get('action')
    if action == 'reopened':
        return reopen_label(record.get('reopened_from'))
    if action == 'acknowledged':
        return '标记已查看'
    if action == 'resolved':
        return '标记已处理'
    return CONCLUSION_NAMES.get(action, action or '核查记录')
