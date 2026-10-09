"""Score four previously exposed videos from identical cached detector outputs."""
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.evaluation import evaluate_video


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def main():
    raw = (ROOT / 'data/firesense/review.json').read_bytes()
    review = json.loads(raw.decode('utf-8'))
    rows = {row['filename']: row for row in review['videos']}
    cached = json.loads((ROOT / 'outputs/firesense-initial-check.json').read_text(encoding='utf-8'))
    folder = ROOT / 'outputs' / datetime.now().strftime('development-comparison-%Y%m%d-%H%M%S-%f')
    folder.mkdir()
    (folder / 'review-snapshot.json').write_bytes(raw)
    protocol = {'version': 'development-visible-state-v1', 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'scope': 'Previously exposed four development videos only; not held-out or external independent performance.',
                'annotation_sha256': hashlib.sha256(raw).hexdigest(),
                'evaluation_code_sha256': hashlib.sha256((ROOT / 'core/evaluation.py').read_bytes()).hexdigest(),
                'episode_merge_gap_s': 1.0,
                'baseline': 'Any raw detector box of class at confidence 0.35 on a frame.',
                'temporal': 'Any confirmed track of class on a frame, including held state during short missing evidence.',
                'matching': 'Any active hint on an actually annotated visible frame within a merged ground-truth scene episode.',
                'false_hints': 'Active hints on frames labelled not visible; merge with the same 1-second episode policy.',
                'delay': 'First active hint on visible frame minus merged annotated event start; preexisting state can yield zero.',
                'uncertain': 'Exclude that video/class; never interpret as negative.',
                'limitation': 'Class presence/time matching, without box truth; does not prove localization or physical hazard.',
                'sources': [result['source'] for result in cached['results']]}
    (folder / 'protocol.json').write_text(json.dumps(protocol, ensure_ascii=False, indent=2), encoding='utf-8')
    results = []
    for previous in cached['results']:
        row = rows[previous['source']]
        assert row['development_seen'] and row['source_sha256'] == previous['source_sha256']
        run = Path(previous['output'])
        config = json.loads((run / 'config.json').read_text(encoding='utf-8'))
        assert config['confidence_threshold'] == .35 and config['source_fps'] == row['fps']
        result = {'source': row['filename'], 'cached_run': str(run), 'source_sha256': row['source_sha256'],
                  'config': config, 'log_sha256': {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                                                 for name in ('detections.jsonl', 'temporal.jsonl')},
                  'metrics': evaluate_video(row, load_jsonl(run / 'detections.jsonl'), load_jsonl(run / 'temporal.jsonl'))}
        results.append(result)
    (folder / 'results.json').write_text(json.dumps({'protocol': protocol, 'results': results}, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# 四段开发素材的初步对照', '', '只复用既有检测日志；没有向尚未曝光素材运行模型，也没有调整参数。', '',
             '| 素材 / 类别 | 单帧 → 时序：检出场景事件 | 单帧 → 时序：负帧误提示片段 | 单帧 → 时序：首次有效提示延迟（秒） |',
             '|---|---|---|---|']
    for result in results:
        for label, metrics in result['metrics'].items():
            if metrics['excluded']:
                continue
            single, temporal = metrics['single_frame'], metrics['temporal']
            delay = lambda value: '—' if value is None else str(round(value, 3))
            lines.append(f'| {Path(result["source"]).name} / {label} | '
                         f'{single["detected_scene_events"]}/{single["ground_truth_scene_events"]} → {temporal["detected_scene_events"]}/{temporal["ground_truth_scene_events"]} | '
                         f'{single["false_hint_episodes"]} → {temporal["false_hint_episodes"]} | '
                         f'{delay(single["mean_first_active_hint_delay_s"])} → {delay(temporal["mean_first_active_hint_delay_s"])} |')
    lines += ['', '误提示片段按负标签帧上的活动提示聚合，统一合并 ≤1 秒的间隔；不是原始弹出提示次数。时序状态包含暂缺证据时的保持，避免只统计确认瞬间而漏掉持续误提示。', '',
              '首次提示延迟按帧号/FPS计算，起点为人工可见区间，并非实际点火时刻。0/0 表示该片段该类别没有正事件，不是漏报率。', '',
              '只有四段开发素材，不能将结果视为最终准确率、全场景提升或风险判定正确性。模型训练重叠与同源关系尚未排除；完整指标、配置、日志哈希及标注快照见同目录 JSON。']
    (folder / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('\n'.join(lines), flush=True)
    print(f'OUTPUT {folder}', flush=True)


if __name__ == '__main__':
    main()
