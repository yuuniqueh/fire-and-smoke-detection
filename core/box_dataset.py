"""Convert reviewed original-pixel boxes; never turn draft/uncertain labels into negatives."""
from core.box_review import training_eligible

CLASS_IDS = {'smoke': 0, 'fire': 1}


def assign_grouped_splits(rows, entries, validation_sources):
    """Keep every existing conservative source constraint in one pilot fold."""
    validation_groups = {entries[source]['constraint_group'] for source in validation_sources}
    result = []
    for row in rows:
        if not training_eligible(row):
            raise ValueError('存在未完成或不确定标注，不能纳入这版数据。')
        entry = entries[row['source']]
        if entry['split'] != 'development' or row['source_sha256'] != entry['source_sha256']:
            raise ValueError('只能导出来源身份一致的开发组图片。')
        result.append('val' if entry['constraint_group'] in validation_groups else 'train')
    if set(result) != {'train', 'val'}:
        raise ValueError('试点必须同时有训练和开发验证图片。')
    return result


def to_yolo_text(row):
    if not training_eligible(row):
        raise ValueError('仅可转换完成且两类均明确的人工标注。')
    lines = []
    for box in row['boxes']:
        x1, y1, x2, y2 = box['xyxy']
        values = ((x1 + x2) / (2 * row['width']), (y1 + y2) / (2 * row['height']),
                  (x2 - x1) / row['width'], (y2 - y1) / row['height'])
        lines.append(str(CLASS_IDS[box['label']]) + ' ' + ' '.join(f'{v:.8f}' for v in values))
    return '\n'.join(lines) + ('\n' if lines else '')
