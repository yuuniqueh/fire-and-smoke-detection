"""Prepare, audit, fit and replay development-only scene-fusion candidates."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from core.adaptive_fusion import FEATURE_VERSION, FusionModel, build_features, fit_model
from core.fusion_dataset import (adjudicated_samples, annotation_summary, load_dataset,
                                 prepare_dataset)
from core.scene_review import contextual_priority


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def _new_directory(path):
    folder = Path(path).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=False)
    return folder


def _fingerprint(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _group_overrides(path):
    if not path:
        return None
    with Path(path).open('rb') as stream:
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError('来源分组文件过大。')
    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                                          for k, v in value.items()):
        raise ValueError('来源分组须为JSON对象：原视频路径对应人工场地组。')
    return value


def _feature_rows(dataset_path):
    approved, exclusions = adjudicated_samples(dataset_path)
    rows = []
    for sample in approved:
        features = build_features(sample['baseline'], sample.get('scene_record'),
            timestamp_s=sample['timestamp_s'], active_event_count=sample.get('active_event_count', 1))
        if not features['eligible']:
            exclusions.append({'sample_id': sample['sample_id'], 'reason': features['reason']})
            continue
        rows.append({'sample': sample, 'features': features['values'], 'label': sample['label'],
                     'source_group': sample['source_group']})
    return rows, exclusions


def _has_valid_split(rows):
    labels = {}
    for row in rows:
        labels.setdefault(row['source_group'], set()).add(row['label'])
    normal = sum(value == {0} for value in labels.values())
    abnormal = sum(value == {1} for value in labels.values())
    mixed = sum(value == {0, 1} for value in labels.values())
    # A minimal validation set is either one mixed group or two pure groups.
    # Removing any further groups cannot restore a missing training class.
    return ((mixed >= 1 and len(labels) >= 3 and
             (mixed >= 2 or normal >= 1 and abnormal >= 1)) or
            (normal >= 1 and abnormal >= 1 and len(labels) >= 4 and
             (mixed >= 1 or normal >= 2 and abnormal >= 2)))


def dataset_status(dataset_path):
    manifest = load_dataset(dataset_path)
    states = annotation_summary(dataset_path)
    rows, exclusions = _feature_rows(dataset_path)
    groups = {}
    for sample in manifest['samples']:
        group = sample['source_group']
        groups.setdefault(group, {'source_group': group, 'candidates': 0, 'eligible_labels': 0,
                                  'class_counts': {'normal': 0, 'abnormal': 0}})['candidates'] += 1
    for row in rows:
        group = groups[row['source_group']]
        group['eligible_labels'] += 1
        group['class_counts']['abnormal' if row['label'] else 'normal'] += 1
    return {'candidate_samples': len(manifest['samples']),
            'review_status': dict(Counter(state['status'] for state in states.values())),
            'eligible_labels': len(rows), 'source_groups': list(groups.values()),
            'exclusions': exclusions, 'feature_version': FEATURE_VERSION,
            'ready_for_training': _has_valid_split(rows),
            'note': '双人一致标签、精确AI可用时间及独立来源划分均必要；入口就绪不代表数据充分。'}


def _metrics(rows):
    counts = {'tp': 0, 'fp': 0, 'tn': 0, 'fn': 0}
    for label, positive in rows:
        counts['tp' if label and positive else 'fn' if label else 'fp' if positive else 'tn'] += 1
    positive = counts['tp'] + counts['fn']
    negative = counts['tn'] + counts['fp']
    return {'samples': len(rows), **counts,
            'abnormal_recall': counts['tp'] / positive if positive else None,
            'normal_false_positive_fraction': counts['fp'] / negative if negative else None,
            'scope': 'reviewed clips; not event/hour metrics or independent field performance'}


def evaluate_rows(rows, model):
    comparisons = {'detector_index': [], 'fixed_fusion': [], 'adaptive_shadow': []}
    cases = []
    for row in rows:
        sample = row['sample']
        raw, record = sample['baseline'], sample['scene_record']
        age = sample['timestamp_s'] - record['evidence'][-1]['timestamp_s']
        old = contextual_priority(raw, record['scene'], frame_count=record['frame_count'],
                                  span_s=record['span_s'], age_s=age)
        fixed_score = old['score']
        if old['context_delta'] < 0 and sample.get('active_event_count', 1) != 1:
            fixed_score = raw['score']
        prediction = model.predict(raw, record, timestamp_s=sample['timestamp_s'],
                                   active_event_count=sample.get('active_event_count', 1))
        scores = {'detector_index': raw['score'], 'fixed_fusion': fixed_score,
                  'adaptive_shadow': prediction['score']}
        for name, score in scores.items():
            comparisons[name].append((row['label'], score >= 45.))
        cases.append({'sample_id': sample['sample_id'], 'source_group': row['source_group'],
                      'label': row['label'], 'scores': scores, 'candidate': prediction})
    return {'threshold': 45., 'threshold_status': 'existing-index threshold; candidate requires validation',
            'metrics': {name: _metrics(items) for name, items in comparisons.items()}, 'cases': cases}


def train_dataset(dataset_path, destination, *, validation_groups, l2=1., iterations=800):
    rows, exclusions = _feature_rows(dataset_path)
    groups = {row['source_group'] for row in rows}
    held = set(validation_groups)
    if not held or held - groups:
        raise ValueError('请明确指定已有合格标签的验证来源组；用status查看完整编号。')
    train = [row for row in rows if row['source_group'] not in held]
    validation = [row for row in rows if row['source_group'] in held]
    if len({row['label'] for row in validation}) != 2:
        raise ValueError('验证来源组需要同时含正常与异常的独立审核片段。')
    if len({row['source_group'] for row in train}) < 2 or len({row['label'] for row in train}) != 2:
        raise ValueError('训练部分至少需要两个来源组、正常与异常两类双人一致标签；验证来源不能参与训练。')
    fitting = [{'features': row['features'], 'label': row['label'], 'source_group': row['source_group']}
               for row in train]
    models = {name: fit_model(fitting, l2=l2, iterations=iterations, include_interactions=interactions)
              for name, interactions in (('interactions', True), ('no_interactions', False))}
    report = {'status': 'development_only', 'online_enabled': False, 'calibrated': False,
              'created_at': datetime.now(timezone.utc).isoformat(), 'feature_version': FEATURE_VERSION,
              'dataset_path': str(Path(dataset_path).resolve()),
              'reviewed_features_sha256': _fingerprint(fitting),
              'labelled_sample_snapshot_sha256': _fingerprint([
                  {'sample': row['sample'], 'features': row['features'], 'label': row['label'],
                   'source_group': row['source_group']} for row in rows]),
              'training_groups': sorted(groups - held), 'validation_groups': sorted(held),
              'training_samples': len(train), 'validation_samples': len(validation),
              'exclusions': exclusions,
              'validation': {name: evaluate_rows(validation, model) for name, model in models.items()},
              'limitations': ['来源隔离的开发验证；尚无最终独立测试、概率校准或现场效果结论。',
                             '候选修正幅度与45分阈值仍待验证；不会自动切换实际通知。',
                             '按片段统计，不能据此宣称每小时误报率或首次报警延迟。']}
    folder = _new_directory(destination)
    _write(folder / 'model.json', models['interactions'].to_dict())
    _write(folder / 'model-no-interactions.json', models['no_interactions'].to_dict())
    _write(folder / 'report.json', report)
    (folder / 'README.md').write_text(
        '# 场景融合开发候选\n\n本目录模型仅用于影子计算，不改变实际通知。\n\n'
        f'训练片段：{len(train)}；验证片段：{len(validation)}。原视频来源分组互不重叠。\n\n'
        'model.json含交互项；model-no-interactions.json用于对照。report.json保留逐条依据与局限。\n'
        '小样本、未校准结果不能作为火灾概率或现场效果保证。\n', encoding='utf-8')
    return {'output': str(folder), 'status': report['status'],
            'training_samples': len(train), 'validation_samples': len(validation), 'online_enabled': False}


def replay_dataset(dataset_path, model_path, destination):
    model = FusionModel.load(model_path)
    manifest = load_dataset(dataset_path)
    output = []
    training_groups = set(model.training['source_groups'])
    for sample in manifest['samples']:
        prediction = model.predict(sample['baseline'], sample.get('scene_record'),
            timestamp_s=sample['timestamp_s'], active_event_count=sample.get('active_event_count', 1))
        output.append({'sample_id': sample['sample_id'], 'source_group': sample['source_group'],
                       'source_seen_in_training': sample['source_group'] in training_groups,
                       'availability': sample.get('availability'), 'candidate': prediction})
    folder = _new_directory(destination)
    _write(folder / 'replay.json', {'mode': 'shadow', 'affects_notifications': False,
        'model_id': model.model_id, 'calibrated': False, 'samples': output,
        'note': '历史可用时间为estimated的片段仅作功能诊断，不纳入因果性能评价。'})
    return {'output': str(folder), 'replayed': len(output), 'model_id': model.model_id,
            'affects_notifications': False}


def parser():
    root = argparse.ArgumentParser(description='场景融合：准备审核片段、检查标签、开发训练与影子重放')
    commands = root.add_subparsers(dest='command', required=True)
    prepare = commands.add_parser('prepare', help='从明确指定的记录准备审核候选，不自动生成标签')
    prepare.add_argument('--runs', nargs='+', required=True)
    prepare.add_argument('--out', required=True)
    prepare.add_argument('--max-samples', type=int, default=200)
    prepare.add_argument('--source-groups', help='可选JSON文件，将同场地不同视频合并为同一来源组')
    status = commands.add_parser('status', help='查看审核进度及可训练来源组')
    status.add_argument('dataset')
    train = commands.add_parser('train', help='按人工指定来源组进行开发训练，不修改线上通知')
    train.add_argument('dataset')
    train.add_argument('--out', required=True)
    train.add_argument('--validation-groups', nargs='+', required=True)
    train.add_argument('--l2', type=float, default=1.)
    train.add_argument('--iterations', type=int, default=800)
    replay = commands.add_parser('replay', help='离线查看候选调整及解释')
    replay.add_argument('dataset')
    replay.add_argument('--model', required=True)
    replay.add_argument('--out', required=True)
    return root


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    try:
        if args.command == 'prepare':
            prepare_dataset(args.runs, args.out, max_samples=args.max_samples,
                            source_groups=_group_overrides(args.source_groups))
            result = dataset_status(args.out)
            result['dataset'] = str(Path(args.out).resolve())
        elif args.command == 'status':
            result = dataset_status(args.dataset)
        elif args.command == 'train':
            result = train_dataset(args.dataset, args.out, validation_groups=args.validation_groups,
                                   l2=args.l2, iterations=args.iterations)
        else:
            result = replay_dataset(args.dataset, args.model, args.out)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        cli.exit(2, '未执行：' + str(exc) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
