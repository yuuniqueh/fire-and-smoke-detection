"""User-facing review priorities, shared by the UI, notices and reports.

These describe software review priority, never an official fire severity grade.
"""

LEVEL_ACTIONS = {'NORMAL': '暂未发现持续异常', 'LOW': '请核查', 'MEDIUM': '优先核查', 'HIGH': '立即核查'}
LEVEL_NAMES = {'NORMAL': '暂未发现持续异常', 'LOW': '黄色提示 · 请核查',
               'MEDIUM': '橙色提示 · 优先核查', 'HIGH': '红色提示 · 立即核查'}
LEVEL_COLORS = {'NORMAL': '#52647a', 'LOW': '#806000', 'MEDIUM': '#a54d08', 'HIGH': '#b12736'}
LEVEL_BACKGROUNDS = {'NORMAL': '#f4f7fa', 'LOW': '#fff8dc', 'MEDIUM': '#fff0df', 'HIGH': '#ffedf0'}
LEVEL_BORDERS = {'NORMAL': '#cfd9e6', 'LOW': '#c69712', 'MEDIUM': '#dd7c1e', 'HIGH': '#d83c4b'}


def notice_title(notice):
    """Translate older saved notices without rewriting their original records."""
    if notice.get('origin') == 'ai_scene' or notice.get('label') == 'scene':
        return notice.get('title') or '综合研判 · 请核查场景异常线索'
    subject = {'fire': '火焰', 'smoke': '烟雾'}.get(notice.get('label'), '异常')
    kind = notice.get('kind')
    if kind == 'event_closed':
        return f'本次{subject}持续检测已结束'
    if kind in ('event_confirmed', 'level_upgrade', 'level_downgrade'):
        return f'检测到{subject} · {LEVEL_NAMES.get(notice.get("level"), "请查看证据")}'
    return notice.get('title', '请查看事件证据')
