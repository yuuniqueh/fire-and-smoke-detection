"""Replay measurements and separately report explicitly scripted rule cases."""
import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.runtime import ROOT, configure_runtime
from core.temporal import EventTracker, TemporalConfig
from core.risk import RiskConfig, RiskEngine


def replay(rows, temporal_config, risk_config, end_timestamp):
    tracker, engine = EventTracker(temporal_config), RiskEngine(risk_config)
    results = []
    for row in rows:
        state = tracker.update(row['timestamp_s'], row['detections'], row['frame_size'])
        results.append(engine.update(state, row['detections'], row['frame_size']))
    engine.finish(end_timestamp)
    tracker.finish(end_timestamp)
    return {'summary': engine.summary(), 'event_risk_summaries': engine.event_summaries,
            'confirmed_events': tracker.confirmed_count,
            'assessed_frame_levels': dict(Counter(r['level'] for r in results if r['status'] == 'assessed'))}, results


def plot_curve(rows, config, destination, title):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    times = [r['timestamp_s'] for r in rows]
    scores, coverage, levels = [], [], []
    rank = {'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}
    for row in rows:
        event = next((e for e in row['events'] if e['event_id'] == row['dominant_event_id']), None)
        scores.append(row['score'] if event else np.nan)
        coverage.append(100 * (event['features']['coverage_ratio_smoothed'] or 0) if event else np.nan)
        levels.append(rank[row['level']] if event else np.nan)
    fig, axes = plt.subplots(3, 1, figsize=(10, 7), sharex=True, gridspec_kw={'height_ratios': [2, 1.3, 1]})
    axes[0].plot(times, scores, color='#d95a39', linewidth=1.8, label='Heuristic visual index')
    axes[0].axhline(config.medium_enter, color='#c69832', linestyle='--', label='Medium entry')
    axes[0].axhline(config.high_enter, color='#b52f32', linestyle='--', label='High entry')
    axes[0].set_ylim(0, 100)
    axes[0].set_ylabel('Index (not probability)')
    axes[0].legend(loc='upper right', fontsize=8)
    axes[1].plot(times, coverage, color='#38799c', linewidth=1.6)
    axes[1].set_ylabel('Box-union coverage (%)')
    axes[2].step(times, levels, where='post', color='#267c64', linewidth=1.8)
    axes[2].set_yticks([1, 2, 3], ['LOW', 'MEDIUM', 'HIGH'])
    axes[2].set_ylim(.75, 3.25)
    axes[2].set_ylabel('Held level')
    axes[2].set_xlabel('Video / sequence time (s)')
    for axis in axes:
        axis.grid(alpha=.2)
        axis.spines[['top', 'right']].set_visible(False)
    fig.suptitle(title, fontsize=13)
    fig.text(.5, .015, 'Unconfirmed intervals omitted. Level changes use hysteresis and hold times; no calibrated hazard labels.', ha='center', fontsize=8)
    fig.tight_layout(rect=(0, .04, 1, .96))
    fig.savefig(destination, dpi=160)
    plt.close(fig)


def scripted_rows(case):
    rows = []
    for i in range(151):
        t = i/10
        coverage = .025
        present = True
        if case == 'expanding_box':
            coverage = min(.6, .01 + .04 * t)
        elif case == 'brief_false_positive':
            present = t < .2
        elif case == 'dropout':
            present = not 5 <= t < 5.4
        elif case == 'empty':
            present = False
        elif case == 'shrinking_box':
            coverage = max(.025, .6 - .04 * t)
        side = coverage ** .5 * 640
        box = [320 - side/2, 320 - side/2, 320 + side/2, 320 + side/2]
        rows.append({'timestamp_s': t, 'frame_size': [640, 640],
                     'detections': [{'label': 'fire', 'confidence': .9, 'bbox_xyxy': box}] if present else []})
    return rows


def main():
    configure_runtime()
    parser = argparse.ArgumentParser()
    parser.add_argument('--run', action='append', default=[])
    parser.add_argument('--temporal-config', default=str(ROOT/'configs'/'temporal.json'))
    parser.add_argument('--risk-config', default=str(ROOT/'configs'/'risk.json'))
    parser.add_argument('--output', default=str(ROOT/'outputs'/'risk-evaluation.json'))
    args = parser.parse_args()
    temporal_config = TemporalConfig(**json.loads(Path(args.temporal_config).read_text(encoding='utf-8')))
    risk_config = RiskConfig(**json.loads(Path(args.risk_config).read_text(encoding='utf-8')))
    scripted, measured = [], []
    for case in ['brief_false_positive', 'stable_box', 'expanding_box', 'shrinking_box', 'dropout', 'empty']:
        report, rows = replay(scripted_rows(case), temporal_config, risk_config, 15.1)
        scripted.append({'case': case, 'evidence_type': 'Scripted detection boxes only; NOT measured model detections or physical risk labels.', **report})
        if case == 'expanding_box':
            plot_curve(rows, risk_config, ROOT/'outputs'/'scripted-expansion-risk-curve.png', 'SCRIPTED BOX EXPANSION - rule behavior only')
        print('SCRIPTED', case, report['summary'])
    for name in args.run:
        run = Path(name).resolve()
        settings = json.loads((run/'config.json').read_text(encoding='utf-8'))
        detections = [json.loads(line) for line in (run/'detections.jsonl').read_text(encoding='utf-8').splitlines()]
        size = settings.get('frame_size')
        if not size:
            import cv2
            cap = cv2.VideoCapture(settings['source'])
            size = [int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))]
            cap.release()
        for row in detections:
            row.setdefault('frame_size', size)
        end = round(detections[-1]['timestamp_s'] + 1/settings['source_fps'], 6) if detections else 0.0
        report, rows = replay(detections, temporal_config, risk_config, end)
        measured.append({'source_run': str(run), 'source_video': settings['source'],
                         'evidence_type': 'Measured model-detection replay, WITHOUT independently annotated hazard levels.', **report})
        curve = run/'risk-curve.png'
        plot_curve(rows, risk_config, curve, f'Measured detection replay: {Path(settings["source"]).name}')
        (run/'risk-replay.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        print('MEASURED',Path(settings['source']).name,report['summary'])
    output = {'temporal_config':asdict(temporal_config),'risk_config':asdict(risk_config),
              'scripted_rule_checks':scripted,'measured_replays':measured,
              'limitations':'Index and grades are configurable heuristic outputs, not calibrated probability, independently verified hazard levels, or measured accuracy gains.'}
    target=Path(args.output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(target)


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    main()
