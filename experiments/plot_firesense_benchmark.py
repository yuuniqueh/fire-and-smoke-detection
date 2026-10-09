"""Standalone chart of the frozen internal benchmark; no new inference."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory')
    args = parser.parse_args()
    folder = Path(args.directory).resolve()
    if (ROOT / 'outputs').resolve() not in folder.parents:
        raise ValueError('Expected a benchmark folder inside outputs.')
    result = json.loads((folder / 'results.json').read_text(encoding='utf-8'))
    if result['run_manifest']['status'] != 'completed':
        raise ValueError('Benchmark not complete.')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.font_manager import FontProperties
    font = FontProperties(fname='C:/Windows/Fonts/msyh.ttc')
    plt.rcParams['axes.unicode_minus'] = False
    fig, axes = plt.subplots(2, 3, figsize=(13, 7), constrained_layout=False)
    colors = {'single_frame': '#2878b5', 'temporal': '#d68b26'}
    labels = {'single_frame': '单帧', 'temporal': '时序'}
    metric_names = [('scene_event_recall', '场景事件检出率（%）', 100),
                    ('false_hint_episodes_per_negative_minute', '每负分钟误提示片段', 1),
                    ('mean_first_active_hint_delay_s', '已检出事件平均延迟（秒）', 1)]
    for row_index, split in enumerate(('development', 'internal_holdout')):
        for column, (metric, title, multiplier) in enumerate(metric_names):
            ax = axes[row_index, column]
            for offset, mode in ((-.18, 'single_frame'), (.18, 'temporal')):
                for label_index, label in enumerate(('fire', 'smoke')):
                    values = result['aggregate'][split][label][mode]
                    value = values[metric]
                    if value is None:
                        ax.text(label_index + offset, .05, 'N/A', ha='center', fontsize=9)
                        continue
                    value *= multiplier
                    ax.bar(label_index + offset, value, .32, color=colors[mode], label=labels[mode] if label_index == 0 else None)
                    text = f'{values["detected_scene_events"]}/{values["ground_truth_scene_events"]}' if column == 0 else f'{value:.2f}'
                    ax.annotate(text, (label_index + offset, value), xytext=(0, 4), textcoords='offset points', ha='center', fontsize=9)
            ax.set_xticks([0, 1], ['fire', 'smoke'])
            ax.set_title(('开发组' if split == 'development' else '内部留出组') + ' · ' + title, fontproperties=font, fontsize=11)
            ax.spines[['top', 'right']].set_visible(False)
            ax.grid(axis='y', alpha=.18)
            ax.set_axisbelow(True)
            ax.margins(y=.2)
            if column == 0:
                ax.set_ylim(0, 118)
                ax.legend(prop=font, loc='upper right', frameon=False)
    fig.suptitle('焰鉴：FIRESENSE 固定内部划分的初步对照', fontproperties=font, fontsize=17, y=.975)
    fig.text(.03, .035, '同一检测输出、参数未调；≤1秒间隔合并。延迟只含已检出事件，须结合检出率读。\n原始来源与训练重叠未完全核查；不能解释为完全独立测试或真实风险准确率。', fontproperties=font, fontsize=10)
    fig.subplots_adjust(top=.89, bottom=.17, left=.055, right=.98, hspace=.4, wspace=.28)
    fig.savefig(folder / 'comparison.png', dpi=170, facecolor='white')
    plt.close(fig)
    print(folder / 'comparison.png')


if __name__ == '__main__':
    main()
