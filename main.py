"""Local video -> detections -> temporal events -> video and audit logs."""
import argparse
import json
import sys
from pathlib import Path

from core.runtime import ROOT, configure_runtime


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def check_environment(device, weights, profile='original'):
    import cv2
    import numpy as np
    import torch
    import ultralytics
    from core.model_profiles import create_detector

    detector = create_detector(weights, device, profile=profile)
    frame, detections = detector.predict(np.zeros((480, 640, 3), dtype=np.uint8))
    report = {
        'python_version': sys.version.split()[0],
        'python_executable': sys.executable,
        'torch': torch.__version__, 'torchvision': __import__('torchvision').__version__,
        'opencv': cv2.__version__, 'ultralytics': ultralytics.__version__,
        'cuda_available': torch.cuda.is_available(), 'torch_cuda': torch.version.cuda,
        'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        'selected_device': detector.device,
        'model_profile': profile,
        'model_classes': detector.model_manifest['canonical_names'] if hasattr(detector, 'model_manifest') else detector.model.names,
        'blank_image_output_shape': list(frame.shape),
        'blank_image_detections': detections,
        'scope': 'Environment and blank-image inference check; not fire detection accuracy validation.',
    }
    write_json(ROOT / 'outputs' / f'environment-{detector.device}.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


def detect_video(args, weights):
    from core.pipeline import analyze_video
    return analyze_video(args, weights)


def main():
    configure_runtime()
    parser = argparse.ArgumentParser(description='焰鉴：火焰/烟雾视频检测与时序事件确认')
    parser.add_argument('--check', action='store_true', help='验证环境、权重及空白画面推理')
    parser.add_argument('--source', help='本地视频路径')
    parser.add_argument('--device', choices=['auto', 'cpu', '0'], default='auto')
    parser.add_argument('--conf', type=float, default=0.35, help='检测阈值，不是火灾概率')
    parser.add_argument('--model-profile', choices=['original', 'brightlight'], default='original',
                        help='original 为原模型；brightlight 为减少反光误报的候选组合，固定阈值0.35')
    parser.add_argument('--headless', action='store_true', help='保存结果但不显示窗口')
    parser.add_argument('--max-frames', type=int, help='仅处理前 N 帧，用于快速验证')
    parser.add_argument('--baseline', action='store_true', help='关闭时序模块，仅保留单帧检测供对比')
    parser.add_argument('--temporal-config', default=str(ROOT / 'configs' / 'temporal.json'), help='时序参数 JSON 路径')
    parser.add_argument('--temporal-only', action='store_true', help='仅显示时序事件，不计算动态风险指数')
    parser.add_argument('--risk-config', default=str(ROOT / 'configs' / 'risk.json'), help='风险规则参数 JSON 路径')
    args = parser.parse_args()
    if not 0 < args.conf <= 1:
        parser.error('--conf 必须大于 0 且不超过 1')
    if args.max_frames is not None and args.max_frames <= 0:
        parser.error('--max-frames 必须为正整数')
    if args.model_profile == 'brightlight' and abs(args.conf-.35) > 1e-9:
        parser.error('brightlight 候选使用固定 --conf 0.35')
    weights = ROOT / 'models' / 'best.pt'
    if args.check:
        check_environment(args.device, weights, args.model_profile)
    elif args.source:
        detect_video(args, weights)
    else:
        parser.print_help()
        print('\n先运行：.venv-pytorch\\Scripts\\python.exe main.py --check')


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    try:
        main()
    except Exception as exc:
        print(f'运行失败：{exc}', file=sys.stderr)
        sys.exit(1)

