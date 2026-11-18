"""Presentation vocabulary and theme for the desktop product."""

from core.alert_presentation import LEVEL_NAMES, LEVEL_COLORS


def video_time(value):
    value = max(0, int(value or 0))
    hours, remainder = divmod(value, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f'{hours:02d}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes:02d}:{seconds:02d}'


from ui.theme import STYLE, ui_font


def record_label(name):
    """A readable timestamp while the underlying run identity stays intact."""
    from datetime import datetime
    try:
        stamp = datetime.strptime(name[4:19], '%Y%m%d-%H%M%S')
        return stamp.strftime('%Y-%m-%d %H:%M')
    except (ValueError, TypeError):
        return name
