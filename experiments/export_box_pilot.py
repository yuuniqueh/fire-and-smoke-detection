"""Freeze human labels and export a small group-separated YOLO pilot package."""
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.box_review import digest_bytes, validate_document
from core.box_dataset import CLASS_IDS, assign_grouped_splits, to_yolo_text
from experiments.benchmark_firesense import digest, read, write

VALIDATION_SOURCES = ('fire_videos.1406/pos/posVideo3.872.avi',
                      'smoke_videos.1407/pos/testpos06.822.avi')


def main():
    import cv2
    pilot = ROOT / 'data/firesense/box-review-pilot-v1'
    manifest_raw = (pilot / 'manifest.json').read_bytes()
    annotation_raw = (pilot / 'annotations.json').read_bytes()
    manifest, annotations = json.loads(manifest_raw), json.loads(annotation_raw)
    validate_document(annotations, manifest, digest_bytes(manifest_raw))
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    entries = {entry['filename']: entry for entry in plan['entries']}
    rows = annotations['images']
    splits = assign_grouped_splits(rows, entries, VALIDATION_SOURCES)
    # This version records the human's correction acknowledgement. It is not inferred from predictions.
    corrected = {
        'basis': 'User reported corrections of image 6/10 and asked to generate data; all 27 completion flags now true.',
        'images': [rows[i]['image'] for i in (5, 9)],
        'annotation_sha256': digest_bytes(annotation_raw),
    }
    output = ROOT / 'datasets/firesense-pilot-v1'
    output.parent.mkdir(exist_ok=True)
    output.mkdir(exist_ok=False)  # Never overwrite an earlier frozen export.
    for split in ('train', 'val'):
        (output / 'images' / split).mkdir(parents=True)
        (output / 'labels' / split).mkdir(parents=True)
    (output / 'metadata').mkdir()
    (output / 'metadata/annotations.json').write_bytes(annotation_raw)
    (output / 'metadata/image-manifest.json').write_bytes(manifest_raw)
    (output / 'metadata/internal-evaluation-plan.json').write_bytes(plan_path.read_bytes())
    sources_verified = set()
    exported = []
    used_stems = set()
    for row, split in zip(rows, splits):
        entry = entries[row['source']]
        if row['source'] not in sources_verified:
            if digest(ROOT / 'videos' / row['source']) != row['source_sha256']:
                raise ValueError('Original video hash changed.')
            sources_verified.add(row['source'])
        source = (pilot / row['image']).resolve()
        if pilot.resolve() not in source.parents:
            raise ValueError('Unexpected image path.')
        raw = source.read_bytes()
        if digest_bytes(raw) != row['image_sha256']:
            raise ValueError('Original image hash changed.')
        image = cv2.imread(str(source))
        if image is None or image.shape[:2] != (row['height'], row['width']):
            raise ValueError('Invalid image dimensions.')
        stem = source.stem
        if stem in used_stems:
            raise ValueError('Duplicate image basename.')
        used_stems.add(stem)
        image_file = output / 'images' / split / source.name
        label_file = output / 'labels' / split / (stem + '.txt')
        image_file.write_bytes(raw)
        label_file.write_text(to_yolo_text(row), encoding='utf-8')
        exported.append({**row, 'dataset_split': split, 'constraint_group': entry['constraint_group'],
                         'dataset_image': image_file.relative_to(output).as_posix(),
                         'dataset_label': label_file.relative_to(output).as_posix(),
                         'label_sha256': digest(label_file)})
    group_sets = {split: {r['constraint_group'] for r in exported if r['dataset_split'] == split} for split in ('train', 'val')}
    hash_sets = {split: {r['image_sha256'] for r in exported if r['dataset_split'] == split} for split in ('train', 'val')}
    if group_sets['train'] & group_sets['val'] or hash_sets['train'] & hash_sets['val']:
        raise ValueError('Group or identical-image overlap.')
    yaml = '# Small developmental pilot; not an independent test dataset.\ntrain: images/train\nval: images/val\nnames:\n  0: smoke\n  1: fire\n'
    (output / 'dataset.yaml').write_text(yaml, encoding='utf-8')
    source_text = '''FIRESENSE fire/smoke video dataset
Nikos Grammalidis, Kosmas Dimitropoulos, Enis Cetin (2017).
Source: https://zenodo.org/records/836749
DOI: 10.5281/zenodo.836749
Publisher-declared license: CC BY 4.0; https://creativecommons.org/licenses/by/4.0/
Modifications: extracted original video frames; human fire/smoke rectangle labels; normalized YOLO coordinates; development-source grouping.
Human reviewer: R1. Full source video/frame/hash mapping is in dataset-manifest.json.
Local development export. Individual third-party marks/subjects still require review for public demonstrations; export is not public publication.
'''
    (output / 'ATTRIBUTION.txt').write_text(source_text, encoding='utf-8')
    summary = {split: {'images': sum(r['dataset_split'] == split for r in exported),
                      'sources': len({r['source'] for r in exported if r['dataset_split'] == split}),
                      'boxes': dict(Counter(box['label'] for r in exported if r['dataset_split'] == split for box in r['boxes'])),
                      'empty_negative_images': sum(r['dataset_split'] == split and not r['boxes'] for r in exported)}
               for split in ('train', 'val')}
    record = {'created_at_utc': datetime.now(timezone.utc).isoformat(), 'version': 'firesense-box-pilot-v1',
              'annotation_sha256': digest_bytes(annotation_raw), 'image_manifest_sha256': digest_bytes(manifest_raw),
              'internal_evaluation_plan_sha256': digest(plan_path), 'exporter_sha256': digest(Path(__file__)),
              'class_ids': CLASS_IDS, 'scope': 'Local developmental pilot only; 27 selected hard-case frames from 6 videos.',
              'split_rationale': 'Keep conservative source groups together; retain rock-wall negatives in training and reserve woodland thin smoke + courtyard bucket groups for developmental validation. Not a randomly sampled performance dataset.',
              'validation_sources': VALIDATION_SOURCES, 'user_correction_record': corrected,
              'group_overlap': [], 'identical_image_overlap': [], 'summary': summary, 'images': exported}
    write(output / 'dataset-manifest.json', record)
    readme = '''# FIRESENSE 目标检测试点数据 v1

27 张原始 PNG 及对应 YOLO txt：18 张训练图（4 段视频），9 张开发验证图（2 段视频）。类别 smoke=0、fire=1，与原模型一致。标签格式为 class_id x_center y_center width height，四个坐标均按原图尺寸归一化。

`dataset.yaml` 可由 Ultralytics 读取；路径相对该文件所在目录，不需要手动改绝对路径。`images/train`、`labels/train` 和 `images/val`、`labels/val` 一一对应。明确人工核查的 3 张无目标图在训练组，标签为空 txt。

`metadata/` 保留这次人工标注、原图清单与原内部评价划分快照；`dataset-manifest.json` 记录每图来源视频、帧号、时间、SHA256、分组与框；`ATTRIBUTION.txt` 记录来源/授权声明及处理说明。

同一来源视频和保守约束组不会跨训练/验证。现有 14 段内部留出未进入此数据包。继续扩充时，木堆火焰和庭院桶及其同组视频继续留在开发验证侧，不能随机分散相邻帧。

本批仅供验证数据加载、训练流程与小规模改进试验：场景少、由困难案例定向选取，验证组没有整图无目标样本。9 张上的指标不能当作独立泛化结果或比赛最终准确率。正式效果评估仍需更多来源、负例及完整视频的漏提示/误提示/延迟检查。

此步骤只生成数据，没有启动训练或替换应用模型。不要改写冻结版本；新修改重新生成下一版。
'''
    (output / 'README.md').write_text(readme, encoding='utf-8')
    print(json.dumps({'output': str(output), 'summary': summary}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
