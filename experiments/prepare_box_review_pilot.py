"""Export raw development frames for a small localization/box-labeling pilot."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from experiments.benchmark_firesense import digest, read, write

SAMPLES = {
    'fire_videos.1406/pos/posVideo3.872.avi': [40, 100, 160, 250, 330],
    'smoke_videos.1407/pos/testpos02.818.avi': [0, 100, 200, 330, 450],
    'smoke_videos.1407/pos/testpos04.820.avi': [1100, 1200, 1480, 1837, 1920],
    'smoke_videos.1407/pos/testpos06.822.avi': [177, 185, 195, 200],
    'smoke_videos.1407/pos/testpos08.824.avi': [123, 135, 160, 210, 260],
    'fire_videos.1406/neg/negsVideo1.858.avi': [1900, 1927, 2000],
}


def main():
    import cv2
    plan_path = ROOT / 'data/firesense/evaluation-v1/plan.json'
    plan = read(plan_path)
    entries = {entry['filename']: entry for entry in plan['entries']}
    output = ROOT / 'data/firesense/box-review-pilot-v1'
    output.mkdir(exist_ok=False)
    images = output / 'images'
    images.mkdir()
    records = []
    for name, indices in SAMPLES.items():
        entry = entries[name]
        if entry['split'] != 'development':
            raise ValueError('Only development sources may enter this pilot.')
        source = ROOT / 'videos' / name
        if digest(source) != entry['source_sha256']:
            raise ValueError('Source changed.')
        capture = cv2.VideoCapture(str(source))
        wanted = set(indices)
        found = set()
        try:
            for index in range(entry['decoded_frames']):
                ok, frame = capture.read()
                if not ok:
                    raise ValueError('Incomplete decode.')
                if index not in wanted:
                    continue
                filename = f'{source.stem}-f{index:05d}.png'
                image_path = images / filename
                if not cv2.imwrite(str(image_path), frame):
                    raise ValueError('Image write failed.')
                if cv2.imread(str(image_path)).shape != frame.shape:
                    raise ValueError('Image validation failed.')
                records.append({'image': 'images/' + filename, 'image_sha256': digest(image_path),
                                'width': frame.shape[1], 'height': frame.shape[0],
                                'source': name, 'source_sha256': entry['source_sha256'],
                                'frame_index': index, 'timestamp_s': index / entry['fps'],
                                'split': 'development', 'status': 'pending',
                                'reviewer_id': None, 'boxes_xyxy': None,
                                'class_status': {'fire': 'unknown', 'smoke': 'unknown'}})
                found.add(index)
        finally:
            capture.release()
        if found != wanted:
            raise ValueError('Missing requested frames.')
    write(output / 'manifest.json', {'purpose': 'Localization review / box-labeling pilot, not a ready training set.',
                                     'plan_sha256': digest(plan_path), 'source_license': 'CC-BY-4.0 (FIRESENSE publisher declaration)',
                                     'images': records})
    guide = '''# 开发困难场景框标注试点

共 27 张原始 PNG，来自 6 段开发组视频；没有叠加预测框，没有自动生成训练标签。

本批用于核查模型有没有框住实际火焰/烟雾，并试做检测框标注。它不是足够训练或评价模型的数据集。相邻帧属于同一来源，不能随机拆成训练与测试。

1. 分别判断火焰和烟雾，只标画面直接可见的目标；用原视频和时间段标注辅助确认。
2. 绘制实际可见火焰/烟雾的矩形框；不要把红色设备、岩壁、光源直接当作火焰。
3. 模糊、遮挡、喷射光等无法可靠分类时标为 uncertain，暂不把该帧作为有监督训练样本。
4. 确实没有两类目标时，完成人工核查后才能创建空标签；当前 manifest 的 null 是“待标”，不是负例。
5. 记录审查人代号及框坐标。正式训练前还需统一标注工具/格式、检查坐标并扩充正例与困难负例。

已有时间段标注继续用于事件评价。这里补充的是目标位置标签。内部留出视频不在此批，原视频和时间段标签未改动。

来源：FIRESENSE，Zenodo DOI 10.5281/zenodo.836749，Nikos Grammalidis、Kosmas Dimitropoulos、Enis Cetin，2017，发布者声明 CC BY 4.0。仅本地开发审查；公开演示仍需检查各片第三方水印及其他素材权益。
'''
    (output / 'README.md').write_text(guide, encoding='utf-8')
    print(f'Prepared {len(records)} raw frames: {output}', flush=True)


if __name__ == '__main__':
    main()
