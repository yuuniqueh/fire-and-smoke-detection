# 按场景选择真实模型

更新：2026-10-22。选择页按具体使用环境提供方案，各方案连接原有固定权重。名称与卡片移除“试用”标记，介绍改为适用画面、主要优点和易错情况；技术信息与验证记录默认折叠。此次只调整显示文案与介绍，不改变模型、阈值或验证结论。

## 界面与使用

在“实时监测 / 工具 → 录像分析 → 更多模型”中，默认页为“按场景选择”，显示场景名称、短说明、“查看介绍”和“选择”。四个选项在默认 640×550 窗口内完整显示。介绍先说明适用画面、主要优点和容易出错的情况，技术组成、来源与验证记录可展开查看；打开介绍不更换模型。

| 场景 | 实际模型 | 用途依据与限制 |
| --- | --- | --- |
| 通用监看 | 现有 YOLOv8n 火焰 / 烟雾 | 保留默认，场景不明确时作为对照起点；仍有误检和漏检 |
| 家庭室内 | mfranzon 家庭烟火 YOLOv8n，火焰 / 烟雾均使用该权重 | 发布者面向家庭早期烟火，涉及烹饪、电气、香烟；旧困难图筛查失败，不能宣称适用于全部室内场所 |
| 林地远景 | SAINet v11.1 / YOLO26m，火焰 / 烟雾均使用该权重 | 发布者面向室外固定机位远距离山火烟雾；公开评测主要针对烟雾，旧困难图筛查失败 |
| 无人机巡林 | 无人机林地 YOLO26m 的 fire 分支 0.50 + 原 smoke 分支 0.35 | 采用已有类别组合检查配置；未直接启用旧视频中烟雾误提示较多的无人机 smoke 分支；没有实现 4K 切片推理 |

“高级对照”页保留“反光抑制对照”和“火焰识别对照”。首页下拉框同样将高级对照放在分隔线后，各模型名称不再附加“试用”。选择记忆、运行中锁定、查看介绍和取消行为保留。

暂不提供“仓库专用”“工厂专用”等选项：本轮没有找到足以据此提供专用选项的可核验权重。已查看的 [FireGuuard 室内 CCTV 项目](https://github.com/ParsaVictor/FireGuuard_Fire_detection_smoke) 有训练流程，未提供本次能够固定获取的训练后权重，因此没有接入。

## 来源、版本与准备

| 场景 | 公开来源 | 固定版本 |
| --- | --- | --- |
| 家庭室内 | [mfranzon/fire-smoke-yolov8](https://huggingface.co/mfranzon/fire-smoke-yolov8) | `f1c6426b069c1849cbf13b1ef5d2a260289286db` |
| 林地远景 | [AlterMundi/sai-sainet-latest](https://github.com/AlterMundi/sai-sainet-latest) | `2246d5ea1408a20ad6fa248eb35a3c5db404e32a` |
| 无人机巡林 | [xiazh0219/yolo26m-UAV-fire-smoke-detection](https://huggingface.co/xiazh0219/yolo26m-UAV-fire-smoke-detection) | `57cf971a788fff62b8f3e5b1c3632e1214b8255c` |

这些文件此前已下载到本地，本轮重新读取来源记录、核对 SHA256 并接入。实际路径、完整 SHA256 和依赖关系统一登记在 `core/model_profiles.py` 的 `SCENE_WEIGHTS`、`profile_weight_specs()` 和 `PROFILE_DETAILS`；原下载清单、模型卡和发布者许可声明保留在对应目录。均为第三方成果，发布者声明 AGPL-3.0；训练素材来源权利和来源重叠未独立审计，详情见 [第三方记录](THIRD_PARTY.md)。

本地需要保留：

- `models/candidates/domestic-f1c6426b069c/fire_smoke_yolov8.pt`
- `models/candidates/sainet-2246d5ea1408/SAINet_v11.1.pt`
- `models/candidates/uav-57cf971a788f/weights/best.pt`
- 通用、无人机组合和原高级对照仍需要原来的 `models/best.pt`；家庭、林地单模型不依赖它。

文件缺失或为空时禁止选择 / 开始，运行时复核 SHA256，不自动下载、替换、回退。复制权重时应保留相邻来源和声明文件。源码仓库提供原固定版本获取脚本 `experiments/fetch_hf_fire_candidates.py --candidate domestic|uav` 与 `experiments/fetch_sainet_candidate.py`；应用内没有下载按钮。

新的 Windows 构建资源清单含全部六份权重以及相应来源材料；本地源码迁移勾选“含模型”时纳入已存在的场景文件并校验哈希。此轮没有实际重建、替换或发布现有 EXE / ZIP。

## 结果记录

每次运行的 `config.json` 保存 `model_profile`、`scene_selection` 与实际 `model_manifest`。场景选择包含来源网址、用途、组成、取舍和验证范围；分支清单包含实际文件、固定 revision、SHA256 和各自阈值。家庭 / 林地单模型的顶层 `weights` 也指向实际场景文件。报告读取该次运行的配置，切换后续模型不会改写旧记录。

家庭模型的类别编号与基准相反；新适配层按类别名称转成统一 smoke=0、fire=1。无人机组合仅路由候选 fire≥0.50 和基准 smoke≥0.35，不把候选 smoke 混入结果。

## 效果边界与已有负面证据

这些是依据发布者训练用途提供的**手动场景试用**，不是已通过本项目独立场景验证的推荐配置。默认仍是原通用模型，不能因为名称匹配就推断准确率更高。

- 家庭模型：此前 27 张选取的困难开发图在 0.35 下，fire 正确框 2 对基准 3，smoke 正确框 3 对基准 10；未通过筛查。详见 [原始报告](EXISTING_MODEL_SCREEN_REPORT.md)。不能将筛查失败改写成“室内模型已验证更好”。
- SAINet：同一困难图诊断的 fire 正确框 0 对基准 3，smoke 8 对基准 10；未进入完整视频评测。原始记录 `outputs/local-fire-sainet-images-20261009-165335-320247/results.json` 保留。这批素材不是代表性的林地现场验证，因此既不能据此证明全场景无效，也不能忽略已有失败。
- 无人机火焰 + 基准烟雾：已有 30 段开发录像及一次 14 段内部留出组合检查；不是独立航拍现场验证。小目标、断检和延迟仍存在。详见 [组合报告](CLASS_ROUTING_VALIDATION_REPORT.md)。

本轮没有训练、改变时序门槛或重新声明准确率提升。要把场景试用升级为正式推荐，需要相应场景的独立正负录像、定位复核、事件检出、误提示时长和端到端延迟。

## 工程验收

- 完整单元检查 262 项，261 通过、1 项按原规则跳过，无失败。新增验证真实权重来源、类别编号转换、无人机分支阈值、版本不符时阻止加载、场景 / 高级页分组、选择持久化、参数进入工作线程与运行前文件复检。日志 `outputs/typical-scenes-20261020/all-tests.txt`。
- 六套登记方案全部使用本地 CPU 实际运行，每套处理既有 CC0 壁炉视频前 40 帧，共 240 帧，核对选择、推理、保存、报告与控件解锁。完整记录 `outputs/typical-scenes-20261020/verification.json`。这是接入检查，壁炉不是独立家庭 / 林地 / 航拍准确率测试。
- 调整单模型顶层权重记录后，额外检查三个新增场景的真实 CPU 运行各 40 帧；最终记录和截图在 `outputs/typical-scenes-final-20261020/`。
- Qt 真实控件截图核对默认选择页四个场景、单独介绍、高级对照，以及 980×740 / 1440×900 首页。`scene-picker.png` 为最终选择页；offscreen 环境仅从本机载入微软雅黑验证中文显示，没有分发字体。
- 复现：`python experiments/verify_scene_selection.py --infer --output outputs/your-check`；可加 `--profiles domestic forest uav` 仅运行指定方案。

此前三方案选择及精简界面的历史记录分别保留在 `outputs/scene-selection-20261020/` 与 `outputs/compact-model-picker-20261020/`，不覆盖旧实验。
