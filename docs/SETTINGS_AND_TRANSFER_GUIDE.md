# 设置保存、错误恢复与换电脑

2026-10-11。本阶段完善使用与迁移，不训练模型、不改变识别和确认规则。

## 设置保存

在“分析配置”修改设备、检测方案、关联方案、阈值、播放速度模式、监测方式或本机提示音，约半秒后自动保存。下次启动恢复这些选项及关闭时的窗口大小；也可以点击“保存当前设置”或“恢复默认设置”。

文件为项目 `.cache/desktop-settings.json`，不包含输入视频路径、人员信息、历史筛选或人工反馈，不进入 Git 和迁移包。损坏或不支持的设置文件会使用默认值并说明原因，在用户修改/明确保存前保留原文件。保存失败时当前界面选项仍可使用。

“恢复默认设置”恢复原模型、原关联、逐帧检测、自动设备、阈值 0.35、原速显示和关闭声音；它不改 `configs/` 算法配置、模型、原视频、标注或已有结果。

## 出错后怎么办

错误显示在主窗口中，可收起，不阻断其余界面操作。GPU/CUDA 相关的分析失败提供“切换 CPU 后重试”，由用户点击后开启新的分析；旧结果保留。文件缺失要重新选择或恢复文件，不自动下载。

分析配置页“检查运行环境”在后台检查依赖导入、GPU、当前方案权重哈希、算法配置和输出/缓存目录可写性。不反序列化权重、不推理，也不会安装依赖。CLI `check_desktop.py` 做相同检查并保存 `outputs/environment-check.json`；该本机诊断含路径，应保留本地。

## 迁移包是什么

`build_transfer.py` 按白名单创建本地源码迁移 ZIP，包含运行代码、配置、依赖清单、启动与重建脚本及说明。`--include-models` 可加入已登记哈希一致的原/候选权重及来源说明。

不包含 Python 环境、私人视频、训练数据/标注、历史结果、个人设置、缓存、PDF、Git 或凭据。没有打包操作系统字体和已安装库。它是给自己另一台电脑使用的源码迁移包，不是免安装 EXE，也不是已审核的公开发布包。第三方授权说明见 `THIRD_PARTY.md`；模型和依赖的正式公开分发要求仍需单独核对。

在当前项目创建包含本地权重的包：

```powershell
.\.venv-pytorch\Scripts\python.exe -X utf8 build_transfer.py --include-models
```

默认保存 `outputs/FireGuard-transfer.zip`。ZIP 中 `transfer-manifest.json` 为每个文件记录相对路径、大小和 SHA256，不含原电脑路径。测试视频需另行选择合法素材；没有把下载的素材默认带走。

## 新电脑启动

1. 解压到普通可写目录，例如 `D:\FireGuard`。不要直接在 ZIP 内运行。
2. 准备 64 位 Python 3.9–3.11；当前开发环境为 3.9.25。新 Python 版本的全新安装尚需实际验证。
3. 在项目目录运行下面一种环境重建方式。脚本只创建/使用项目 `.venv`，保留原环境；中断后可修复原因再运行，不自动删除目录。

已有 PyTorch **2.8.0 / torchvision 0.23.0** 时，优先复用其 Python：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_desktop.ps1 -PythonPath '你的已有环境\python.exe' -TorchMode reuse -PlanOnly
powershell -ExecutionPolicy Bypass -File .\setup_desktop.ps1 -PythonPath '你的已有环境\python.exe' -TorchMode reuse
```

`-PlanOnly` 检查选定 Python 并显示命令计划，不创建环境、不安装。已有 PyTorch 版本不匹配时会说明原因，不替换原环境中的 torch。

没有对应 PyTorch 时，可显式建立独立 CPU 环境，安装需要联网：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_desktop.ps1 -PythonPath python -TorchMode cpu
```

有适配 NVIDIA 驱动时可以把 `cpu` 改为 `cu128`，下载量更大。固定 torch/torchvision 版本及 CPU/CUDA 安装源依据 [PyTorch 官方历史版本说明](https://pytorch.org/get-started/previous-versions/#v280)；脚本没有升级原环境或自动安装显卡驱动。

4. 双击 `check_environment.cmd`，处理报告中的问题。没有打包权重时，先复制已登记的 `models/best.pt`；候选模型是可选项。
5. 双击 `start_desktop.cmd`。启动脚本优先项目 `.venv`，没有时兼容当前 `.venv-pytorch`。

不要复制旧虚拟环境；旧环境可能依赖原电脑的基础 Python 路径。软件本机设置和历史也不随此包迁移；需要旧分析记录时另行备份对应运行目录。

当前只在原电脑的新解压路径检查迁移包结构与界面启动，不能替代另一台电脑的真实安装和运行验收。
