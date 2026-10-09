# 焰鉴 / FireGuard

面向 AI+软件创新参赛项目的火焰与烟雾视频分析原型。当前已实现本地视频检测、时序事件确认、可解释风险提示和基础桌面操作闭环；真实场景验证、安装包与正式交付仍待完成。

## 桌面版启动

双击 `start_desktop.cmd`，点击“壁炉示例”或“打开视频”，再点击“开始分析”。可停止、查看曲线与事件、查询历史并导出 ZIP。详情见 `docs/DESKTOP_GUIDE.md`，阶段验证见 `docs/DESKTOP_TEST_REPORT.md`。

桌面版与下方命令行共用处理逻辑；分析后台运行，停止或关闭窗口会等待当前帧处理与记录保存。无需重新安装 PyTorch。

界面“检测方案”可选择“原模型”或“减少反光误报（候选）”。候选采用新模型识别火焰、原模型识别烟雾，固定阈值 0.35；它在开发视频中减少误提示，但部分真实火焰确认更迟或中断，目前默认仍为原模型。首次使用候选需先运行 `experiments/screen_brightlight_candidate.py --stage fetch` 下载固定版本权重；启动时不自动下载。

## 当前环境

项目虚拟环境位于 `.venv-pytorch`，无需激活即可使用。它通过 `--system-site-packages` 复用已有 `D:\anaconda\envs\pytorch` 环境的 Python 3.9.25、PyTorch 2.8.0+cu128 和 torchvision 0.23.0+cu128；检测新增依赖位于项目目录，未修改原有环境。GPU 为 RTX 4060 Laptop。

目前仍依赖上述基础环境路径，不能直接复制 `.venv-pytorch` 给其他电脑。后续交付需在新环境重建或打包软件上验证。Python 版本升级另做兼容性验证。

## 验证模型与环境

在 D:\AI_competition 打开 PowerShell：

```powershell
.\.venv-pytorch\Scripts\python.exe main.py --check
```

输出 Python、PyTorch、显卡、模型类别，以及空白画面的推理结果。该检查不代表真实火焰识别效果。

## 检测本地视频

双击 `start_demo.cmd` 启动完整的当前演示：左侧画面与时序事件，右侧等级、指数、覆盖、趋势及解释。双击 `start_temporal.cmd` 看仅时序对照，`start_baseline.cmd` 看单帧对照。双击 `check_environment.cmd` 查看环境信息。退出检测窗口后按任意键关闭命令窗口。

已准备一段 CC0 壁炉火焰视频，可直接运行：

```powershell
.\.venv-pytorch\Scripts\python.exe main.py --source '.\videos\fireplace_cc0.webm'
```

窗口中按 Q 或 Esc 停止。仅保存结果而不打开窗口：

```powershell
.\.venv-pytorch\Scripts\python.exe main.py --source '.\videos\fireplace_cc0.webm' --headless
```

CPU 运行加 `--device cpu`；快速检查可以加 `--max-frames 30`；阈值例如 `--conf 0.35`。置信度属于检测模型的输出，不是火灾发生概率。

命令行选择候选加 `--model-profile brightlight`（固定 `--conf 0.35`），切回原模型用 `--model-profile original`。每次结果的 `config.json` 记录所选方案、两个模型的来源、版本、SHA256 和各分支阈值；不删除或覆盖原权重。

默认启用时序确认。加 `--baseline` 仅保留单帧检测；时序参数位于 configs/temporal.json，也可以用 `--temporal-config` 指定另一个配置文件。默认至少观察 1 秒、窗口内正检测时间至少 0.8 秒且比例不低于 70%，短时漏检容忍 0.6 秒。

confirmed 表示持续视觉检测事件，presence 表示时间一致性比例，两者均不是物理火灾真假判定或火灾概率。当前提示为画面状态与控制台记录。

已确认事件默认计算动态视觉指数，显示 LOW/MEDIUM/HIGH。框并集覆盖、观察时间、增长及模型置信度各自的贡献会保存；缺失证据时保留上一次评估并标记暂缺。权重与阈值是初始启发式规则，指数不代表火灾概率，NORMAL 仅表示没有已确认视觉事件。详见 docs/RISK_DESIGN.md。

风险配置位于 configs/risk.json，用 `--risk-config` 可指定其他配置；加 `--temporal-only` 可关闭风险评分而保留时序事件。

每次运行写入独立的 `outputs/run-*` 目录：

- `annotated.mp4`：带检测框的结果视频。
- `detections.jsonl`：逐帧视频时间、类别、置信度与坐标。
- `temporal.jsonl`：逐帧候选/确认状态、时间一致性比例和转换；单帧基线模式下为空。
- `events.json`：确认事件的时间、依据、提示与关闭原因；单帧基线模式下不生成。
- `risk.jsonl` / `risk.csv`：逐帧风险指数、特征与解释；关闭风险时为空。
- `risk-events.json`：事件最高指数、最高已生效等级、变化及证据；关闭风险时不生成。
- `config.json`：输入路径、参数与模型来源。
- `summary.json`：处理帧数、结束状态与端到端速度。
- `evidence/event-N.jpg`：事件确认时刻截图；最高指数可能发生在其他时刻。

视频时间按帧序号与输入 FPS 计算，目前以恒定帧率视频为首版输入。端到端速度包含初始化、解码、绘制、保存及可选的窗口等待，不等同于纯模型推理速度。

某些 WebM 容器报告的帧数是估算值。若与实际解码帧数不同，summary 会记录 `completed_with_frame_count_warning` 和差异提示，需核对素材完整性，不将帧数差异直接当作程序崩溃。

## 重建环境

在当前电脑重建同样的复用环境：

```powershell
& 'D:\anaconda\envs\pytorch\python.exe' -m venv --system-site-packages .venv-pytorch
$env:PIP_CACHE_DIR = "$PWD\.cache\pip"
.\.venv-pytorch\Scripts\python.exe -m pip install -r requirements.txt
.\.venv-pytorch\Scripts\python.exe download_model.py
```

GPU 路线要求兼容的 NVIDIA 显卡和驱动。在其他电脑上，先建立独立 Python 环境并按 PyTorch 官方说明安装兼容的 torch/torchvision，再安装检测依赖。当前测试版本为 torch 2.8.0、torchvision 0.23.0，GPU 二进制为 cu128；对应官方索引 `https://download.pytorch.org/whl/cu128`，CPU 索引 `https://download.pytorch.org/whl/cpu`。

`download_model.py` 会优先复用已有 manifest 中的 revision 并核对已有 SHA256，初次下载才使用当前上游版本。精确复现时保留 `models/manifest.json`。跨机器重建的完整验证尚未完成。

## 规划和来源

- `docs/GIT_WORKFLOW.md`：本地 Git 与 GitHub 管理流程、代码同步和本地素材备份范围。

- `docs/BRIGHTLIGHT_MODEL_REPORT.md`：亮光/红色设备误报候选试验，包含收益、烟雾失败及覆盖/延迟代价；默认模型未替换。

- `PROJECT_PLAN.md`：按成果推进的计划与比赛提交检查表。
- `docs/REQUIREMENTS.md`：首版范围与验收条件。
- `docs/THIRD_PARTY.md`：组件授权、模型与素材来源记录。
- `docs/TEMPORAL_DESIGN.md`：时序规则、参数、解释与限制。
- `docs/TEMPORAL_TEST_REPORT.md`：行为检查、日志回放和真实视频集成结果。
- `docs/RISK_DESIGN.md`：特征口径、评分公式、等级稳定规则及限制。
- `docs/RISK_TEST_REPORT.md`：特征与风险测试、实测回放、规则曲线及复现命令。
- `docs/DESKTOP_GUIDE.md`：桌面操作、历史查询和记录导出。
- `docs/DESKTOP_TEST_REPORT.md`：窗口集成、清理、导出与命令行回归证据。
- `docs/FIRESENSE_DATA.md`：49 段真实场景素材的来源、校验、解码异常、四段接入结果与误提示案例。
- `docs/DATA_REVIEW_GUIDE.md`：原视频逐帧标注、同源分组、数据划分与评价口径草案。双击 `start_data_review.cmd` 打开独立标注工具；无需模型推理或新依赖。
- `docs/ANNOTATION_AUDIT.md`：用户提交 44 段标注后的身份/区间检查、单帧正例复核与待分组事项。
- `docs/DEVELOPMENT_COMPARISON.md`：四段已曝光开发素材的单帧/时序活动提示比较，记录减少误提示与增加延迟的实际取舍。
- `docs/FROZEN_EVALUATION_PROTOCOL.md`：30 开发 / 14 内部留出 / 5 排除的固定保守分组、参数和指标口径。
- `docs/FIRESENSE_BENCHMARK_REPORT.md`：44 段、36,461 帧的首轮结果；时序减少误提示但增加漏提示，包含完整失败记录与后续复核方向。
- `docs/DETECTION_DIAGNOSIS.md`：开发组原图/框复核、检测阈值/时序/输入尺寸受控试验结果与下一步。三类试验均未达到采用条件，保持原配置；27 张开发原图框标注试点在 `data/firesense/box-review-pilot-v1/`。
- `docs/BOX_REVIEW_GUIDE.md`：27 张困难场景的火焰/烟雾目标框标注操作。双击 `start_box_review.cmd`，可放大、拖框、删除、判别无/有/不确定、保存草稿及恢复；独立保存，不改原视频时间段标注。
- `docs/BOX_ANNOTATION_AUDIT.md`：首批框标注提交检查与修改后的导出结果。27 张已完成，YOLO 试点包在 `datasets/firesense-pilot-v1/`，按来源分 18 训练 / 9 开发验证；真实数据加载、标签坐标与哈希检查通过，尚未训练。
- `docs/FINETUNE_PILOT_REPORT.md`：首次 30 轮小规模微调与 30 段完整开发视频对照。事件检出未增加、误提示增加，候选未采用；记录数据角色、框定位计数、原预设验收的指标上限问题及完整日志。
- `docs/EXISTING_MODEL_SCREEN_REPORT.md`：2026-10-09 两份现成模型对照。家庭候选未通过图片检查；无人机候选补回一个 fire 事件但 smoke 误提示增加，未整体替换；包含四阈值结果、来源与下一轮组合验证方向。
- `docs/CLASS_ROUTING_VALIDATION_REPORT.md`：候选仅供 fire、原模型供 smoke 的固定组合，30 段实际运行与 14 段内部留出均补回一个 fire 事件，未增加时序误提示；预测计算时间约 2.63 倍，小火焰和烟雾仍有漏报，默认模型尚未更换。

FIRESENSE 已解压至 `videos/fire_videos.1406/` 与 `videos/smoke_videos.1407/`。桌面版“打开视频”可直接选择其中 `pos`/`neg` 下的 AVI；各目录标签只针对该包类别。完整登记在 `videos/firesense-manifest.json`，四段初次接入结果在 `outputs/firesense-initial-check.json`。这一轮发现岩壁误提示，尚未完成独立效果评估；五段帧数异常暂不进入正式统计。

应用默认模型是第三方训练成果；本项目另有一次未采用的小规模微调试点，保留完整参数、来源与失败结果。使用与交付前需履行实际许可证条件；比赛效果与创新性以真实独立测试为依据。
