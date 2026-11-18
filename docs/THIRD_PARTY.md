# 第三方来源与授权记录

登记日期：2026-10-06。具体安装版本以 requirements-lock.txt 为准。

2026-11-16 可选本机场景复核：接入第三方 [Qwen3-VL-2B-Instruct固定模型卡](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct/blob/89644892e4d85e24eaac8bacfd4f463576704203/README.md)，模型卡声明Apache-2.0。语言层在运行时进行NF4量化，视觉部分FP16；本团队未训练此模型。版本、权重哈希、依赖与本机路径边界见 [升级验收](MONITOR_CENTER_UPGRADE_20261116.md)。当前通过独立本机环境运行，未纳入旧便携EXE／ZIP；既有应用许可与其他组件许可不变。

当前交付候选版 0.1.1-rc1 的应用代码采用 AGPL-3.0-only，第三方保留原许可。源码与完整条款的当前入口为 [SOURCE_ACCESS.md](SOURCE_ACCESS.md) 和 [BUILD_FROM_SOURCE.md](BUILD_FROM_SOURCE.md)。Qt/PySide、OpenCV/FFmpeg 及依赖的固定版本源码、许可和校验记录已整理到对应源码档案；新构建只保留 Widgets 界面需要的 Qt 模块。本文件下方的“尚未打包”“授权待补”等带日期段落描述旧版本核查过程，不替代当前包内清单；第三方训练素材权利仍以发布者声明及实际证据为依据，不宣称独立认证。

2026-10-17 交付核查补记：实际 CPU 包的版本以包内 third-party-notices/dependency-inventory.json 和 packaging/requirements-cpu.txt 为准，不能套用开发 GPU 清单。详见 [源码与第三方许可核查](delivery/SOURCE_LICENSE_AUDIT.md)。已核对 3266 个清单文件和 105 文件应用源码快照；Qt 开源许可文本、额外 GPLv3 Virtual Keyboard 模块、对应源码/重建信息及应用授权路线仍需处理。单独备齐五份许可参考文本，没有修改旧包、为项目新授予许可证或对外发布。下文带日期的说明保留历史状态。

| 组件 | 上游来源 | 授权与处理 |
|---|---|---|
| Ultralytics | https://github.com/ultralytics/ultralytics / https://www.ultralytics.com/license | AGPL-3.0 或另行授权；分发时核对源码、许可证及声明义务 |
| fire-smoke-yolov8n 权重 | https://huggingface.co/rabahdev/fire-smoke-yolov8n | 仓库声明 AGPL-3.0；下载后保存 revision、SHA256 和文件大小 |
| PyTorch / torchvision | https://github.com/pytorch/pytorch / https://github.com/pytorch/vision | 核对安装包许可证及捆绑第三方声明；保存实际许可文件清单 |
| OpenCV Python | https://github.com/opencv/opencv-python | 核对 wrapper、OpenCV 及二进制捆绑组件的不同许可文件 |
| Hugging Face Hub | https://github.com/huggingface/huggingface_hub | 核对安装包许可与依赖声明 |
| PySide6 / shiboken6 6.9.3 | https://doc.qt.io/qtforpython-6/licenses.html / https://code.qt.io/cgit/pyside/pyside-setup.git/ | 安装包元数据声明 LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only；Qt 运行库和模块需分别核对，保留版本、声明及对应源码获取信息 |
| 其他间接依赖 | 安装环境内各 dist-info | 汇总 LICENSE/COPYING/NOTICE 等文件，交付前复核 |

## 权重使用声明

应用默认检测模型为第三方在 D-Fire 数据集上微调的现成权重。2026-10-06 本项目另做了一次基于该权重与用户 FIRESENSE 框标注的本地 30 轮微调试点，候选表现未改善，单独保留在 `outputs/finetune-pilot-20261006-223650-088739/train/weights/`，未替换应用默认模型。该试点不是从零训练或自主研发网络架构，训练参数、标签来源、权重 SHA256 和失败结果见 `docs/FINETUNE_PILOT_REPORT.md`。模型卡指标属于发布者报告，不是本项目测试结果。上游模型卡中的训练数据说明不能自动替代数据集和演示素材的授权核查。

当前仅建立本地开发基线。公开发布、可执行文件分发前必须核对实际许可证及适用义务；此记录不是法律授权，也不构成项目已自动开源。若选择该 AGPL 路线，应按适用条件准备完整对应源码及声明；若交付目标无法满足，调整授权或替换组件。

2026-10-12 开始制作本地 Windows CPU 便携验收包：明确选择原模型、亮光候选和 YOLO26-S 试用分支，附应用源码快照、固定模型来源、实际构建依赖清单及可找到的许可证文本。该本地验收产物不是公开发布；本文早期“尚未打包”“不加入迁移包”的记录描述当时状态。公开分发审核仍未完成，收集声明不代表已履行完整对应源码、Qt/第三方组件等适用义务。上游权重仍为第三方成果，不能因被封装进软件而称为自主训练或自主研发网络。

桌面界面使用 QtCore、QtGui、QtWidgets；曲线为本项目的 QPainter 控件。四个 PySide/shiboken 安装包的许可文件路径清单已记录在 docs/sources/*-license-files.txt，但该清单不是完整分发许可包。打包时仍需核查实际 Qt DLL、插件和第三方声明、许可证文本与适用源码义务。当前未打包或对外发布。界面使用操作系统已有字体，未复制字体文件用于分发。

## 素材记录

已登记初始视频 `videos/fireplace_cc0.webm`：ABOedit2015-Video-Fire.webm，作者 BrillLyle，2015-11-04 拍摄，Wikimedia Commons 页面声明 CC0 1.0。来源：https://commons.wikimedia.org/wiki/File:ABOedit2015-Video-Fire.webm 。许可：https://creativecommons.org/publicdomain/zero/1.0/ 。原始页面已保存于 docs/sources/fireplace-source.html，SHA1 已与来源页面核对一致。该素材是壁炉火焰，不作为失控火灾或现场部署证明。

每个文件记录来源 URL/自行拍摄说明、作者、许可证/同意范围、允许的展示及分发用途、获取时间、SHA256、内容说明及标注方式。详细文件信息见 videos/manifest.json。

自行生成的空白/几何图形工程验证视频不含真实火情，不列为真实场景实验数据。

已登记 FIRESENSE 火焰/烟雾两包，共 49 段视频。发布者：Nikos Grammalidis、Kosmas Dimitropoulos、Enis Cetin（2017），DOI 10.5281/zenodo.836749；公开元数据声明 CC BY 4.0。来源、许可、使用与修改说明、压缩包 MD5、逐文件 SHA256、解码差异和标注限制见 docs/FIRESENSE_DATA.md 与 videos/firesense-manifest.json；发布者元数据快照保存在 docs/sources/firesense-zenodo-record.json。部分画面有第三方水印、既有检测轮廓或人物，拟公开展示片段仍需逐段核查来源与适用权利。当前仅用于本地开发检查，未对外发布。

## 2026-10-09：独立保存的候选权重

以下均为第三方已训练模型，仅在开发实验中使用，未替换应用 `models/best.pt`，不称为本项目自主研发网络。

2026-10-20 补充：下列家庭、SAINet 和无人机权重现已作为手动“场景试用”接入源码版选择页，默认权重未替换。此变更不推翻旧筛查失败结果，也不构成准确率采用结论；无人机方案只使用其火焰分支，烟雾使用基准。新构建资源清单纳入固定权重、来源和上游声明，原有便携包未重建。详见 [场景模型说明](SCENE_MODEL_SELECTION.md)。

| 候选 / 上游 | 固定版本 | 本地目录 | 声明授权及来源限制 |
|---|---|---|---|
| [mfranzon/fire-smoke-yolov8](https://huggingface.co/mfranzon/fire-smoke-yolov8) | `f1c6426b069c1849cbf13b1ef5d2a260289286db` | `models/candidates/domestic-f1c6426b069c/` | 模型卡与元数据声明 AGPL-3.0；仓库无独立 LICENSE。发布者称 D-Fire + Domestic Fire and Smoke 微调，训练素材权利与本项目数据重叠未独立审核。 |
| [xiazh0219/yolo26m-UAV-fire-smoke-detection](https://huggingface.co/xiazh0219/yolo26m-UAV-fire-smoke-detection) | `57cf971a788fff62b8f3e5b1c3632e1214b8255c` | `models/candidates/uav-57cf971a788f/` | 模型卡与元数据声明 AGPL-3.0；仓库无独立 LICENSE。发布者称 FASDD + 自制无人机数据微调，来源权利与训练重叠未独立审核。 |
| [AlterMundi SAINet（前次筛查补记）](https://github.com/AlterMundi/sai-sainet-latest) | `2246d5ea1408a20ad6fa248eb35a3c5db404e32a` | `models/candidates/sainet-2246d5ea1408/` | 已保存发布者 AGPL-3.0 LICENSE、README、训练参数和 Git LFS 指针。前次图片诊断未通过，不继续视频评价。 |

每个目录保留 `source-manifest.json`，包括版本、真实文件名、字节数与 SHA256。两份 Hugging Face 候选另保存固定版本的完整公开元数据和模型卡，权重 SHA256 与发布者 LFS 对象一致；没有运行发布者附带的预测脚本。当前只核验了来源声明与文件完整性，不能据此宣称分发授权及素材权利已全部解决。

家庭候选加载需要额外安装 `dill==0.4.0`；仅补充到项目虚拟环境。可选依赖记录在 `requirements-candidates.txt`，安装包声明 BSD-3-Clause，许可文本副本和路径清单在 `docs/sources/dill-0.4.0-LICENSE` 与 `docs/sources/dill-license-files.txt`。默认模型无需该额外依赖。实验版本记录在 `outputs/candidate-environment-20261009.json`。

本次比较范围、完整失败结果和采用决定见 `docs/EXISTING_MODEL_SCREEN_REPORT.md`。

## 2026-10-09：亮光第三类候选

用户指定的 [Nocluee100/Fire-and-Smoke-Detection-yolov8-v1](https://github.com/Nocluee100/Fire-and-Smoke-Detection-yolov8-v1)，固定 Git 版本 `0da45ff9f6d2c52563972f6b5ce2312a7cd02726`。`weights/best.pt` 大小 6,257,194 字节，下载字节已与 Git blob `5a3d415bd02de068ee1739a106d132a3ca43ed10` 一致核验；SHA256 为 `8e23e1110a47dad1fb81b26984de1ca88e751cec0c070894f1a7bd4211c2d0b0`。

本地目录 `models/candidates/brightlight-0da45ff9f6d2/` 保存权重、Git 树元数据、来源 manifest、README、LICENSE 和 dataset.txt。LICENSE 声明权重与 Python 脚本为 MIT，数据由第三方提供，dataset.txt 声明 CC BY 4.0。README 链接 `catargiuconstantin/firesmokedataset`，dataset.txt 链接 `catargiuconstantin2/firesmokenewdataset`，实际训练数据版本及划分需进一步确认；发布者的 MIT 声明不替代上游 Ultralytics 和素材权利核查。

仅运行本项目隔离的实验脚本，没有执行仓库附带程序，没有新增依赖或对外分发。实际类别 fire/other/smoke；完整结果及 fire 分支探索性回放见 `docs/BRIGHTLIGHT_MODEL_REPORT.md`。默认模型未替换，不将第三方权重或模型选型表述为自主研发神经网络。

## 2026-10-11：两份新的 YOLO26 对比权重

另见下方2026-10-12的RT-DETR研究对照登记；没有因此替换任何产品模型。

| 发布者模型 | 固定版本 | 文件 / SHA256 | 发布者声明 |
|---|---|---|---|
| [SalahALHaismawi/yolov26-fire-detection](https://huggingface.co/SalahALHaismawi/yolov26-fire-detection) | `5397b43994c39c2ed4ef81a6996c6334e2b7a5fa` | `best.pt`；`52684ddffd4797f1bc5ff1bd2b72af3ec34eabf0c1646c37f518bb766b27b79f`；20,301,317 字节 | YOLO26-S，fire/other/smoke，8,939 张 Roboflow 图；模型 MIT、数据 CC BY 4.0。 |
| [odiug77/wildfire-smoke-fire](https://huggingface.co/odiug77/wildfire-smoke-fire) | `9c81734ac75d14ba57c7834904891874074e451f` | `wildfire-smoke-fire.pt`；`6b7089f0abb37821cc17bd72d9c595591f03d9f230fe49acec0166049c9f213e`；44,028,569 字节 | YOLO26-M，smoke/fire，户外野火数据；模型 Apache-2.0。 |

权重下载后校验与固定版本的发布者 LFS 对象一致；本地保存公开元数据、README 与 `source-manifest.json`。实际加载为 YOLO26-S、9,950,186 参数及 YOLO26-M、21,775,972 参数，类别与对应声明一致。只运行本项目的推理代码，没有执行发布者脚本或安装新的依赖。

这些授权和训练信息是发布者声明，未独立审核训练成员、原始素材权利或与本项目数据的重叠。MIT/Apache 声明不能单独证明免除上游 Ultralytics 的许可条件；当前用于本机开发对比，不加入源码迁移包，也不声称已完成公开分发授权核查。

## 2026-10-12：RT-DETR-L 研究对照（未采用）

发布者 Ayash Hossain Chowdhury，[源码与记录](https://github.com/ayashhossain555/fire-detector-reliability)、[固定Zenodo存档22776642](https://zenodo.org/records/22776642)。仅下载 `checkpoints_rtdetrl_dfire_s3407.zip`，83,589,481字节，MD5 `09eb64913b1110c247ad9ff74b7c8d34`；其中 best.pt 为66,226,045字节，SHA256 `46fd2e0b07ab9ce5d1869581b08206e8cc70d4f311a38fc69f2dcb2377c7f2d9`，与作者训练记录一致。保存在 `models/candidates/rtdetrl-dfire-s3407/`，含来源manifest与存档目录清单。

作者存档声明训练权重为AGPL-3.0-or-later；研究脚本、评测导出、日志等另有各自条款，不能以脚本MIT代替权重许可。训练数据D-Fire及与本项目素材的重叠未独立核清。当前只在本机调用既有Ultralytics接口做一次开发筛查，没有执行作者脚本、安装依赖或对外分发；候选未采用，不加入迁移包或软件交付。报告见 `RTDETR_REFERENCE_SCREEN_REPORT.md`。

