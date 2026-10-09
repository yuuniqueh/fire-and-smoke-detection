"""Verify exported hashes/coordinates and actual installed YOLO data loading."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.runtime import configure_runtime
configure_runtime()
from core.box_dataset import CLASS_IDS
from experiments.benchmark_firesense import digest, read, write


def main():
    from ultralytics.data.dataset import YOLODataset
    from ultralytics.utils import YAML
    package = ROOT / 'datasets/firesense-pilot-v1'
    manifest = read(package / 'dataset-manifest.json')
    assert digest(package / 'metadata/annotations.json') == manifest['annotation_sha256']
    assert digest(package / 'metadata/image-manifest.json') == manifest['image_manifest_sha256']
    assert digest(package / 'metadata/internal-evaluation-plan.json') == manifest['internal_evaluation_plan_sha256']
    config = YAML.load(package / 'dataset.yaml')
    assert config['names'] == {0: 'smoke', 1: 'fire'}
    max_roundtrip_error = 0.0
    empty = 0
    expected_files = set()
    rows = {Path(row['dataset_image']).stem: row for row in manifest['images']}
    for row in rows.values():
        image, label = package / row['dataset_image'], package / row['dataset_label']
        assert digest(image) == row['image_sha256'] and digest(label) == row['label_sha256']
        expected_files.update((image.resolve(), label.resolve()))
        lines = label.read_text(encoding='utf-8').splitlines()
        assert len(lines) == len(row['boxes'])
        if not lines:
            assert row['completed'] and all(v == 'absent' for v in row['class_status'].values())
            empty += 1
        for line, box in zip(lines, row['boxes']):
            cls, cx, cy, w, h = [float(v) for v in line.split()]
            assert cls == CLASS_IDS[box['label']] and 0 <= cx <= 1 and 0 <= cy <= 1 and 0 < w <= 1 and 0 < h <= 1
            recovered = [(cx - w / 2) * row['width'], (cy - h / 2) * row['height'],
                         (cx + w / 2) * row['width'], (cy + h / 2) * row['height']]
            error = max(abs(a - b) for a, b in zip(recovered, box['xyxy']))
            max_roundtrip_error = max(max_roundtrip_error, error)
            assert error < .001
    actual_files = {p.resolve() for d in ('images', 'labels') for p in (package / d).rglob('*') if p.suffix in ('.png', '.txt')}
    assert actual_files == expected_files
    loaded = {}
    for split in ('train', 'val'):
        dataset = YOLODataset(img_path=str(package / config[split]), data={'names': config['names'], 'nc': 2, 'channels': 3},
                              imgsz=640, augment=False, cache=False, batch_size=1, prefix=f'Pilot {split}: ')
        assert len(dataset) == manifest['summary'][split]['images']
        count = 0
        for label in dataset.labels:
            row = rows[Path(label['im_file']).stem]
            assert row['dataset_split'] == split and len(label['cls']) == len(row['boxes'])
            count += len(label['cls'])
        expected_count = sum(manifest['summary'][split]['boxes'].values())
        assert count == expected_count
        sample = dataset[0]
        assert sample['img'].shape[0] == 3 and sample['img'].shape[-2:] == (640, 640)
        loaded[split] = {'images': len(dataset), 'boxes': count, 'first_sample_tensor_shape': list(sample['img'].shape)}
    train_groups = {r['constraint_group'] for r in rows.values() if r['dataset_split'] == 'train'}
    val_groups = {r['constraint_group'] for r in rows.values() if r['dataset_split'] == 'val'}
    assert not train_groups & val_groups and all(r['split'] == 'development' for r in rows.values())
    report = {'package': str(package), 'annotation_sha256': manifest['annotation_sha256'],
              'image_label_pairs_verified': len(rows), 'empty_negative_labels': empty,
              'max_coordinate_roundtrip_error_pixels': max_roundtrip_error,
              'ultralytics_dataset_loader': loaded, 'group_overlap': [], 'training_started': False}
    write(ROOT / 'outputs/box-dataset-verification.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
