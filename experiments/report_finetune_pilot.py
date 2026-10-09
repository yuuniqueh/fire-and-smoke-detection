"""Audit and report the completed pilot, including the criterion ceiling issue."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.benchmark_firesense import digest, read, write

FOLDER = ROOT / 'outputs/finetune-pilot-20261006-223650-088739'


def main():
    result = read(FOLDER / 'results.json')
    proposal = result['proposal']
    baseline = read(ROOT / 'outputs/benchmark-v1-20261006-203720-465883/results.json')
    old = {case['filename']: case for case in baseline['cases'] if case['split'] == 'development'}
    cases = result['cases']
    assert len(cases) == 30 and set(old) == {c['filename'] for c in cases}
    assert digest(ROOT / 'models/best.pt') == proposal['base_weights_sha256']
    assert digest(FOLDER / 'train/weights/best.pt') == result['candidate_sha256']
    assert digest(ROOT / 'datasets/firesense-pilot-v1/dataset-manifest.json') == proposal['dataset_manifest_sha256']
    verified, frames = 0, 0
    for number, case in enumerate(cases, 1):
        path = FOLDER / 'development-videos' / f'case-{number:02d}-{Path(case["filename"]).stem}'
        for name, expected in case['log_sha256'].items():
            assert digest(path / name) == expected
            verified += 1
        frames += case['decoded_frames']
    with (FOLDER / 'train/results.csv').open(encoding='utf-8') as stream:
        epochs = list(csv.DictReader(stream))
    assert len(epochs) == 30
    archive = FOLDER / 'code-snapshot'
    archive.mkdir(exist_ok=True)
    for name in ('experiments/finetune_pilot.py', 'core/box_metrics.py', 'core/detector.py',
                 'core/evaluation.py', 'core/temporal.py'):
        path = archive / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            if name == 'experiments/finetune_pilot.py' and digest(ROOT / name) != proposal['runner_sha256']:
                raise ValueError('Historical runner unavailable; never snapshot a changed runner as the original.')
            path.write_bytes((ROOT / name).read_bytes())
    assert digest(archive / 'experiments/finetune_pilot.py') == proposal['runner_sha256']
    note = ('The original criterion requested fewer misses on other_development, but its baseline already detected '
            'all 6 fire and 4 smoke events. That gain criterion has a ceiling and is not appropriate. '
            'This pilot still fails sensible regression checks: no difficult-event gains and more false episodes. '
            'Do not rewrite the original proposal or present this issue as a successful trial. '
            'Future proposals should seek difficult-source validation gains while using other development as no-regression guards.')
    write(FOLDER / 'integrity-audit.json', {'epochs_completed': len(epochs), 'development_videos': len(cases),
          'decoded_frames': frames, 'verified_log_sha256_count': verified,
          'original_weights_unchanged': True, 'candidate_sha256': result['candidate_sha256'],
          'original_criterion_note': note, 'original_holdout_videos_evaluated': 0})
    lines = ['# 首次小规模微调试验', '',
             '结论：训练与评价流程执行完成，但新权重没有增加开发视频事件检出，误提示显著增加，未替换应用原模型，也未对原内部留出运行此候选。', '',
             '## 设置与数据', '',
             '- 起始权重：第三方 `rabahdev/fire-smoke-yolov8n` 的固定原始 best.pt；不是从零训练或自研模型架构。',
             '- 数据：18 张训练图 / 9 张开发验证图，来源分组；训练组 4 段视频，验证组 2 段。',
             '- 30 轮、640 输入、batch=4、AdamW、初始学习率 0.0001、冻结前 10 层、固定 seed=20261006；禁用 AMP 自动检查、云集成与在线字体下载。',
             '- 本地 RTX 4060 Laptop GPU、torch 2.8.0+cu128、Ultralytics 8.4.173。训练阶段总耗时约 25.75 秒（含初始化/验证等），小数据量耗时不代表完整数据训练成本。',
             '- best.pt 按 9 张开发验证图的 Ultralytics fitness 选择，未按完整视频结果挑选轮次。完整参数与环境在运行目录 proposal.json / train/args.yaml。', '',
             '## 完整开发视频对照', '',
             '两权重均以置信度 0.35、640 输入及原冻结时序配置评价，使用相同人工时间区间和误提示片段聚合规则。新权重完整处理 30 段、28,611 帧；原权重复用已核查基线日志。', '',
             '| 来源角色 | 视频数 | 类别 | 原检出/真值事件 | 新检出/真值事件 | 原误提示片段 | 新误提示片段 |',
             '|---|---:|---|---:|---:|---:|---:|']
    role_names = {'training_groups': '训练来源组', 'epoch_selection_groups': '选择轮次来源组（含同组视频）',
                  'other_development': '其余开发组', 'all_development': '全部开发组'}
    for role, total in result['aggregate'].items():
        for label in ('fire', 'smoke'):
            a, b = (total[method][label]['temporal'] for method in ('original', 'finetuned'))
            lines.append(f'| {role_names[role]} | {total["videos"]} | {label} | '
                         f'{a["detected_scene_events"]}/{a["ground_truth_scene_events"]} | '
                         f'{b["detected_scene_events"]}/{b["ground_truth_scene_events"]} | '
                         f'{a["false_hint_episodes"]} | {b["false_hint_episodes"]} |')
    lines += ['', '训练来源的完整视频包含训练图片，选择轮次组包含验证图片及同源庭院视频，不能作为独立测试。其余 23 段未参与本次训练/选轮，但此前已有开发曝光，也不是独立外部测试。', '',
              '误提示是负标签帧上的活动状态片段，不等于原始通知次数或定位假阳性框数。场景检出只验证类别/时间，不能单独证明框在正确位置。', '',
              '## 框定位对照', '',
              '27 张人工框另行以置信度≥0.35、同类别贪心匹配 IoU≥0.5 计数；不是 mAP。', '',
              '| 图片角色 | 类别 | 原 TP / FP / FN | 新 TP / FP / FN |', '|---|---|---:|---:|']
    for split in ('train', 'val'):
        for label in ('fire', 'smoke'):
            a, b = (result['box_aggregate'][split][method][label] for method in ('original', 'finetuned'))
            lines.append(f'| {split} | {label} | {a["tp"]} / {a["fp"]} / {a["fn"]} | {b["tp"]} / {b["fp"]} / {b["fn"]} |')
    lines += ['', '9 张验证图在本操作点下，两模型都没有达到 IoU≥0.5 的正确框匹配。这不代表所有画面完全没有检测框；检测到局部火焰也可能无法覆盖人工定义的整体目标。需要继续检查目标范围/分组口径与困难场景的定位支持，不能仅靠置信度或片段内出现框宣称准确定位。', '',
              '新模型最终验证 mAP50≈0.138、mAP50-95≈0.055 是 Ultralytics 的另一套低置信度评价，仅来自选轮用的 9 图；本次未按同一 mAP 流程计算原模型对照，不宣称 mAP 提升。', '',
              '## 验收条件中的问题', '',
              '原预设要求其余开发组至少一类漏提示减少，但该组原本已命中全部 6 个 fire 与 4 个 smoke 事件，这一增益条件设得不合理。原方案保留不改写；本次依然因困难事件未增加检出、误提示退步而不能采用。下次应将困难来源验证组作为改善目标，其余开发组作为不丢失既有事件、不增加误提示的回归护栏。', '',
              '当前工作区的试验脚本已为未来运行修正验收条件（criterion_version=2），本次运行目录中的原方案、结果和 code-snapshot 保留旧版本，不重算或改写历史试验结果。', '',
              '## 决定与后续', '',
              '- 不采用该候选，应用原权重 SHA256 保持不变；新权重在运行目录 train/weights/ 下单独保留。',
              '- 先统一相邻帧中火焰整体与分离目标的框口径，整理当前标注与原模型预测的定位差异。',
              '- 扩充多个开发来源的可见小火焰、薄烟、普通正例与明确困难负例。当前仅 18 张训练图、3 张同视频负例不足以覆盖场景变化；这次结果支持“本方案未改善”，不能证明所有微调都无效。',
              '- 下一版重新预先记录目标与设置；保持来源分组，先进行框定位与完整开发视频双重检查，通过后再冻结候选检查原内部留出。', '',
              '## 完整性与来源', '',
              f'已核查 30 个训练轮次、{verified} 份逐帧日志 SHA256、候选 SHA256、原权重未变，代码快照保存在 code-snapshot/。', '',
              '运行目录：`outputs/finetune-pilot-20261006-223650-088739/`。包含原预设方案、实际训练参数、训练 CSV/日志、新旧框对照、全部逐帧检测与时序日志、结果及完整性审计。', '',
              '这是在第三方 YOLOv8n 权重上的本地微调试点，训练素材为用户框标注的 FIRESENSE 开发帧；来源与授权声明沿用原组件/数据记录。未对外发布，未宣称自研网络或取得泛化提升。']
    text = '\n'.join(lines) + '\n'
    (ROOT / 'docs/FINETUNE_PILOT_REPORT.md').write_text(text, encoding='utf-8')
    (FOLDER / 'report.md').write_text(text, encoding='utf-8')
    print(json.dumps({'report': str(ROOT / 'docs/FINETUNE_PILOT_REPORT.md'), 'verified_logs': verified,
                       'frames': frames, 'candidate_adopted': False, 'criterion_ceiling_issue_recorded': True}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
