# 第三方来源与授权记录

登记日期：2026-10-06。具体安装版本以 requirements-lock.txt 为准。

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

桌面界面使用 QtCore、QtGui、QtWidgets；曲线为本项目的 QPainter 控件。四个 PySide/shiboken 安装包的许可文件路径清单已记录在 docs/sources/*-license-files.txt，但该清单不是完整分发许可包。打包时仍需核查实际 Qt DLL、插件和第三方声明、许可证文本与适用源码义务。当前未打包或对外发布。界面使用操作系统已有字体，未复制字体文件用于分发。

## 素材记录

已登记初始视频 `videos/fireplace_cc0.webm`：ABOedit2015-Video-Fire.webm，作者 BrillLyle，2015-11-04 拍摄，Wikimedia Commons 页面声明 CC0 1.0。来源：https://commons.wikimedia.org/wiki/File:ABOedit2015-Video-Fire.webm 。许可：https://creativecommons.org/publicdomain/zero/1.0/ 。原始页面已保存于 docs/sources/fireplace-source.html，SHA1 已与来源页面核对一致。该素材是壁炉火焰，不作为失控火灾或现场部署证明。

每个文件记录来源 URL/自行拍摄说明、作者、许可证/同意范围、允许的展示及分发用途、获取时间、SHA256、内容说明及标注方式。详细文件信息见 videos/manifest.json。

自行生成的空白/几何图形工程验证视频不含真实火情，不列为真实场景实验数据。

已登记 FIRESENSE 火焰/烟雾两包，共 49 段视频。发布者：Nikos Grammalidis、Kosmas Dimitropoulos、Enis Cetin（2017），DOI 10.5281/zenodo.836749；公开元数据声明 CC BY 4.0。来源、许可、使用与修改说明、压缩包 MD5、逐文件 SHA256、解码差异和标注限制见 docs/FIRESENSE_DATA.md 与 videos/firesense-manifest.json；发布者元数据快照保存在 docs/sources/firesense-zenodo-record.json。部分画面有第三方水印、既有检测轮廓或人物，拟公开展示片段仍需逐段核查来源与适用权利。当前仅用于本地开发检查，未对外发布。

## 2026-10-09：独立保存的候选权重

以下均为第三方已训练模型，仅在开发实验中使用，未替换应用 `models/best.pt`，不称为本项目自主研发网络。

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

