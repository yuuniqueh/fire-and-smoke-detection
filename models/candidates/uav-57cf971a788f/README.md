---
language:
  - en
  - zh
tags:
  - ultralytics
  - yolo
  - yolo26m
  - object-detection
  - fire-detection
  - smoke-detection
  - uav
  - drone
  - forestry
  - remote-sensing
  - sliced-inference
  - computer-vision
license: agpl-3.0
pipeline_tag: object-detection
library_name: ultralytics
model-index:
  - name: yolo26m-fasdd-fire-smoke-detection
    results:
      - task:
          type: object-detection
          name: Object Detection
        dataset:
          name: FASDD
          type: FASDD
        metrics:
          - type: mAP50
            value: 0.843
            name: mAP@0.5
          - type: mAP50-95
            value: 0.594
            name: mAP@0.5:0.95
          - type: precision
            value: 0.811
            name: Precision
          - type: recall
            value: 0.829
            name: Recall
---

# 🔥 YOLOv26m — UAV Forest Patrol Fire & Smoke Detection

> 无人机林地巡检烟火检测模型 · 基于 YOLOv26m 在 FASDD 数据集上微调

![PR Curve](BoxPR_curve.png)

## 📌 简介 / Overview

本模型用于**无人机航拍林地场景下的烟火目标检测**，可同时识别两类目标：

- 🔥 **fire** — 火焰
- 💨 **smoke** — 烟雾

基于 COCO 预训练的 YOLOv26m，在 **FASDD**（Forest Aerial Smoke/Fire Detection）数据集及 **自制无人机森林火灾数据集** 上微调得到。适用于林地巡检、森林防火、火情早期预警等无人机遥感场景。

## 📊 性能指标 / Metrics

| 指标 Metric | 数值 Value |
|:---|:---|
| mAP@0.5 | **0.843** |
| mAP@0.5:0.95 | **0.594** |
| Precision | 0.811 |
| Recall | 0.829 |
| 输入尺寸 Input Size | 640 × 640 |
| 参数 Params | YOLOv26m（约 40M） |

> 指标读取自发布的 `weights/best.pt` 内置验证结果，使用 SGD + cosine LR 训练 200 epochs。

![Results](results.png)

## 🛠 训练配置 / Training Config

| 配置项 | 值 |
|:---|:---|
| 基础模型 Base | YOLOv26m（COCO 预训练） |
| 数据 Data | FASDD（fire / smoke 两类） |
| 优化器 Optimizer | SGD |
| 学习率策略 LR Schedule | cosine (lr0=0.005, lrf=0.001) |
| Epochs | 200（patience=30） |
| Batch Size | 16 |
| Image Size | 640 |
| 增强 Augmentation | degrees=30, flipud=0.5, fliplr=0.5, scale=0.5, hsv 等 |
| 其他 | label_smoothing=0.1, multi_scale=0.3, mosaic=0 |

## 🚀 快速使用 / Quickstart

### 方式一：直接使用（Ultralytics）

```python
from ultralytics import YOLO

model = YOLO("weights/best.pt")

# 图片检测
results = model.predict("aerial_image.jpg", conf=0.25, imgsz=640)

# 视频检测（无人机巡检视频）
results = model.predict("drone_video.mp4", conf=0.25, imgsz=640)

# 验证指标
metrics = model.val()
```

### 方式二：从 Hugging Face 下载加载

```python
from huggingface_hub import hf_hub_download
from ultralytics import YOLO

model_path = hf_hub_download(
    "xiazh0219/yolo26m-UAV-fire-smoke-detection",
    "weights/best.pt",
)
model = YOLO(model_path)
```

### 方式三：4K 无人机视频切片检测（推荐用于小目标）

先下载本仓库，然后安装依赖：

```bash
python -m pip install -r requirements.txt
```

使用与训练一致的 640×640 输入，对原始视频做 20% 重叠切片：

```bash
python predict_video_sliced.py \
  --source /path/to/drone_video.mp4 \
  --model weights/best.pt \
  --slice-size 640 \
  --overlap 0.20 \
  --device 0
```

未通过 `--model` 指定权重且本地不存在 `weights/best.pt` 时，脚本会自动从本 Hugging Face 仓库下载权重。脚本会将检测框映射回原始视频坐标，并执行按类别 NMS 去重。默认额外执行一次整帧检测，以兼顾跨越多个切片的大范围烟雾。输出仅为保持源分辨率和 FPS 的标注视频；切片检测计算量明显高于普通整帧推理。

### 命令行推理

```bash
yolo predict model=weights/best.pt source=aerial_image.jpg imgsz=640 conf=0.25
```

## 📁 文件结构 / File Structure

```
yolo26m-fasdd-fire-smoke-detection/
├── README.md                # 模型说明（本文件）
├── predict_video_sliced.py  # 640×640 重叠切片视频推理
├── requirements.txt         # 推理依赖
├── args.yaml                # 完整训练参数
├── results.png              # 训练曲线
├── BoxPR_curve.png          # P-R 曲线
├── confusion_matrix.png     # 混淆矩阵
├── val_batch0_pred.jpg      # 验证集预测示例
└── weights/
    └── best.pt              # 最优权重（44 MB）
```

## ⚠️ 使用注意事项 / Notes

- 输入尺寸请保持 **640×640**，与训练一致以获得最佳效果。
- 对 4K 视频中的远距离小目标，推荐使用 `predict_video_sliced.py`，而不是将整帧直接缩小到 640。
- 模型在无人机**俯拍林地**场景上训练，应用于航拍/低空影像效果最佳。
- 检测置信度阈值建议 `conf=0.25`，可视场景密度调整（0.15–0.35）。
- NMS IoU 默认 0.7；`max_det=300`。

## 📜 许可证 / License

- 模型权重与代码基于 [AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0.html) 开源（Ultralytics YOLO 系列协议）。
- 若用于商用，请评估 AGPL 约束或联系获得商业授权。

## 📚 引用 / Citation

如果你使用了本模型，欢迎引用：

```bibtex
@misc{outsideguy2026yolo26mfasdd,
  title={YOLOv26m UAV Forest Patrol Fire and Smoke Detection},
  author={OutsideGuy},
  year={2026},
  howpublished={\url{https://huggingface.co/xiazh0219/yolo26m-UAV-fire-smoke-detection}},
}
```

---
<div align="center">

**如果这个模型有帮助，别忘了 ⭐ Star / ❤️ Follow**

</div>
