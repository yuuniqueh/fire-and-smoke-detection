# 焰鉴 / FireGuard

2026-11-17 监控选择与事件导航：点击监控画面、卡片文字或“查看详情”，没有烟火事件也能打开本路AI画面理解；等待结果时显示真实等待状态。事件增加稳定序号、时间／优先级／监控点排序，刷新保留当前选择；点击事件自动展开列表，可一键收起或拖动分隔条。485项完整检查中484通过、1跳过，已核对蜡烛无事件记录和厨房事件记录的实际界面及Windows原生启动。[操作与验证](docs/MONITOR_NAVIGATION_20261117.md)。重启源码入口使用，旧便携EXE未重建。

2026-11-17 通俗结果表达：主卡改为“研判建议＋核查优先级”，显示请核查／优先核查／立即核查，具体辅助评分移入展开详情。检测匹配分与规则分分别解释，均不包装为火灾概率；历史等级按原记录显示，不由分数重新划档。后台共同评分与原始记录保持一致。[说明与示例](docs/PLAIN_RESULT_PRESENTATION_20261117.md)。重启源码入口使用。

2026-11-17 监控操作与详情：每路卡片可独立设置烟火检测模型，运行中显示实际方案；已处理可撤回，保留原处理记录并立即更新颜色与状态。分析详情改为综合结论卡、醒目的AI画面理解、关键数据表、逐条依据及折叠模型信息。AI画面理解仍共用本机Qwen。重启源码入口使用，旧便携EXE未重建。[操作与验证](docs/MONITOR_MODELS_REOPEN_READABILITY_20261117.md)。

2026-11-17 当前综合评分：两路模型各自分析，由显式动态规则给出一个综合核查指数与最终提示，取消固定加10分；原始检测和AI意见移入详情。无需小样本训练，过期证据回退，正常用火仅在充分且稳定的单火焰条件下有限降分；场景单独异常也能进入综合评分与待核查。重启源码入口使用，旧便携EXE未重建。[当前规则与操作](docs/COOPERATIVE_SCORE_20261117.md)。下述可训练流程已暂停作为当前产品路线，仅保留开发实验文件。

2026-11-17 可训练融合开发流程：新增带交互项的轻量融合层、双人独立审核窗口、来源分组训练与无交互对照、影子重放及解释记录；当前实际通知仍沿用原规则。已采集蜡烛与厨房录像的10个待审片段，尚未生成真实训练标签或训练有效性结论。双击 `start_fusion_review.cmd` 查看原画面并审核；蜡烛录像当前未被YOLO确认，不能据此宣称正常用火降权成功。[实现、实际发现与操作](docs/ADAPTIVE_FUSION_IMPLEMENTATION_20261117.md)。

2026-11-17 监控与事件处理闭环：默认进入统一“监控台”，单画面／多画面共用监控点和后台任务；事件记录统一提供待核查、待处理、已处理筛选。核查结论精简为正常用火、识别误报、确认异常、继续核查；确认异常后可单独标记已处理。状态使用红／橙／绿色，处理记录可回看，已处理事件可复核和更新；新增告警升级会重新进入待核查。311项回归检查通过，完成实际Qt处理流程与双路录像80帧／路的布局切换验证。[操作与验收](docs/MONITORING_REVIEW_FLOW_20261117.md)。重启源码入口使用，旧便携EXE未重建。

2026-11-17 全局可读性调整：统一原生中文无衬线字体，正文16–18px、辅助信息15px；事件页明确分为列表、现场画面和核查操作，窄窗口切换画面／处理；存储页改为空间总览、保留设置和记录列表，设置、历史、监控中心及分析页同步调整。Windows原生启动确认字体为Microsoft YaHei UI，125%缩放下逻辑字号16px；18张真实Qt截图覆盖主要页面和窄窗口。[本轮改动与验证](docs/UI_READABILITY_20261117.md)。重启源码入口查看，旧便携EXE未重建。

2026-11-17 界面打磨：浅色侧栏、原创矢量图标、圆角视频与卡片、蓝色主操作和统一字体；AI画面描述保留在主界面，耗时／模型／取帧信息移入详情，核查表单按需展开。已核对11张真实Qt界面截图、980×740及1100×800窄窗口布局，301项回归检查通过。重启源码启动入口查看；旧便携EXE未重建。[界面改动与截图](docs/UI_POLISH_20261117.md)。

2026-11-17 多模态融合：开播积累约1秒原画面后，即与YOLO并行分析场景，无烟火事件也能生成描述；按视频时间约8秒更新。场景结果进入最终核查等级、通知和保存记录，界面显示“原始指数＋场景修正→最终等级”。已用无事件录像、厨房录像及双路本机AI联调验证；蜡烛实拍准确性尚未独立验证。重启 `start_desktop.cmd` 或 `start_monitor_center.cmd` 使用，旧便携EXE未重建。[本轮说明与验证](docs/SCENE_FUSION_20261117.md)。以下为先前版本记录。

2026-11-17 AI提速：事件确认后即开始分析，描述逐步显示；厨房录像实测约1.7秒开始显示、6.7秒完成复核，均在录像结束前。重启 `start_desktop.cmd` 使用。完整结果仍须校验，原检测提示保留；[实测与范围](docs/SCENE_SPEED_20261117.md)。

录像分析入口补齐：单路页面右侧已直接显示“AI画面描述”，新检测结束后自动生成，历史事件可手动生成并查看原画面依据。重启 `start_desktop.cmd` 使用，已运行的旧窗口不会自动更新。

2026-11-16 源码桌面版：已增加“监控中心”，支持多路本地录像、监控点名称与位置管理、事件触发的本机Qwen画面描述、场景对核查指数的有限修正、人工处理与可追溯导出。双击 `start_monitor_center.cmd`，首次点击“加载两路演示录像”，再“开始全部”。已用两路完整录像和真实本机模型完成联调，含重开与导出；不代表独立误报率或现场消防效果验证。操作与实际边界见 [本次升级与验收](docs/MONITOR_CENTER_UPGRADE_20261116.md)。旧便携EXE／ZIP尚未重建，下面保留历史版本记录。

2026-10-22 文案与介绍更新：已生成 Windows CPU 便携版 **0.2.0-rc2**。检测框使用“火焰 / 烟雾”，事件提示改为“检测到火焰 / 烟雾”；模型名称和选择卡片移除“试用”。六套模型介绍按适用画面、主要优点和易错情况说明，技术与验证记录可展开查看。新版 ZIP：`outputs/portable-windows-cpu-20261022/FireGuard-Windows-CPU-0.2.0-rc2.zip`，完整解压后运行 FireGuard.exe。264 项自动检查通过、1 项跳过；最终 ZIP 解压后的 EXE 在移除外部 Python / Conda 路径后完成 24 项检查。详见 [本次交付记录](docs/PACKAGE_DELIVERY_20261022.md)。旧包和个人记录保留。以下内容为历史更新。

2026-10-20 打包交付：已生成 Windows CPU 便携版 **0.2.0-rc1**，包含本轮精简界面与六套模型 / 组合方案，无需安装 Python。ZIP 位于 `outputs/portable-windows-cpu-20261020/FireGuard-Windows-CPU-0.2.0-rc1.zip`（851,830,527 字节）。最终 ZIP 的文件与模型哈希核对通过，中文 / 空格目录运行的 EXE 24 项检查通过，并完成隔离 Python / Conda 路径后的检查和普通启动检查。见 [本次打包交付记录](docs/PACKAGE_DELIVERY_20261020.md)。以下“EXE 尚未重建”等表述为打包前开发阶段的历史记录，本段为最新交付状态。

2026-10-20 监测首页精简：启动默认进入“实时监测”，侧栏保留“实时监测 / 事件记录 / 工具 / 设置”，录像分析、历史和存储收进工具。右侧状态卡只显示简短状态及必要操作，说明文字放到“提示说明”，指标默认折叠；无事件时隐藏核查按钮和空时间轴。预览、画面延迟和历史结果仍明确区分。265 项检查中 264 通过、1 跳过，并核对 13 张真实 Qt 截图。见 [简约监测界面验收](docs/CONCISE_MONITORING_UI.md)。重启 `start_desktop.cmd` 查看，现有便携 EXE / ZIP 未重建。

2026-10-20 场景模型：选择页提供“通用监看、家庭室内、林地远景、无人机巡林”，后三项使用各自登记的真实场景权重并标注试用；原反光候选和火焰对照移至“高级对照”。选择页只保留短说明，模型来源、取舍和既有筛查失败记录放在“查看介绍”。场景名称依据发布者训练用途，不代表本项目已验证场景准确率，默认仍是通用模型。见 [场景选择说明与验收](docs/SCENE_MODEL_SELECTION.md)。

选择自动保存、运行中锁定；缺失文件或版本不符会阻止检测，记录 / 报告保存实际方案与分支来源。262 项检查中 261 通过、1 跳过，并完成六套真实 CPU 方案各 40 帧接入核对。双击 `start_desktop.cmd` 启动，已打开的旧窗口需要重启；现有便携 EXE / ZIP 未重建。

提示等级与声音补充：已用黄色“请核查”、橙色“优先核查”、红色“立即核查”替代“关注”等表达，增加卡片色条与感叹号，历史提示使用灰色并注明当时等级。设置页增加声音试听，首页展示声音状态。247 项检查中 246 通过、1 跳过；外部警灯 / 蜂鸣器尚未接入。见 [提示等级与声光方案](docs/ALERT_DESIGN.md)。

2026-10-19 交互改版：源码桌面版已增加侧边导航、浅色工作区、中文检测框、事件时间轴和页内回放；“已知悉”与人工核查分开，支持待跟进意见、5 分钟静音和单独报告导出。双击 `start_desktop.cmd` 启动新版；已打开的旧窗口需要重启，现有便携 EXE/ZIP 未重建。243 项检查中 242 通过、1 跳过，并完成 CPU 206 帧真实模型与 Qt 界面验证。操作与边界见 [改版验收记录](docs/UX_REDESIGN_TEST_REPORT.md)；详细设计见 [交互方案](docs/design/20261019-ux-proposal.md)。下方旧版截图与操作描述作为历史进度保留。

当前应用代码采用 **AGPL-3.0-only**，完整条款见 [LICENSE](LICENSE)，第三方边界见 [NOTICE](NOTICE)。源码取得方式与对应第三方源码说明见 [SOURCE_ACCESS](docs/SOURCE_ACCESS.md)，重建步骤见 [BUILD_FROM_SOURCE](docs/BUILD_FROM_SOURCE.md)。0.1.1-rc1 已完成同机新 CPU 环境源码重建、16 项实际 EXE 检查及许可/源码工程材料核对；当前交付入口见 [材料索引](docs/delivery/DELIVERY_INDEX.md)。既有 0.1.0-test 包保留。下方按日期记录历史进度。

2026-10-17 更新：官方模板结构版技术报告和团队字段已整理；[材料索引](docs/delivery/DELIVERY_INDEX.md)为当前入口。[源码与第三方许可核查](docs/delivery/SOURCE_LICENSE_AUDIT.md)已完成实际包检查并明确交付缺项，旧包缺项后续已整改，当前结论见 [整改记录](docs/delivery/RELEASE_REMEDIATION_011.md)；模型上游训练数据权利未独立认证。下方按日期保留历史进度。

2026-10-15 后续交付材料：[材料索引](docs/delivery/DELIVERY_INDEX.md)已加入 12 页可编辑答辩稿/静态 PDF、逐页讲稿、追问准备及 4 分 20 秒字幕功能演示。视频来自实际源码软件自动化操作和 CPU 推理，使用合法工程素材，无配音；不是手机/现场验收。官方模板对齐、真实设备细节与公开交付许可仍待完成，既有软件 ZIP 不改动。

2026-10-15 参赛材料初稿：[材料索引](docs/delivery/DELIVERY_INDEX.md)汇总使用说明、技术方案/核心算法、4 分 20 秒演示脚本、名称/简介和缺项表。已生成 4 页使用说明与 6 页技术方案审阅 PDF，位于 `output/pdf`。官方模板对齐、视频录制、答辩幻灯片及公开交付许可仍待完成；本轮不修改软件包、训练或推送 GitHub。

面向 AI+软件创新参赛项目的火焰与烟雾视频分析原型。当前已实现本地视频检测、时序事件确认、可解释风险提示和桌面操作闭环；Windows CPU 便携测试包已构建并完成同机工程验收，跨电脑、真实手机及正式交付仍待验收。

2026-10-13 便携版：`outputs/portable-windows-cpu-20261013/FireGuard-Windows-CPU-test.zip`，约 324 MiB，解压约 807 MiB。完整解压后双击 `FireGuard.exe`，无需安装 Python；不要单独移动 EXE。三种模型已随包提供，初始默认原模型，使用 CPU 推理。实际冻结 EXE 在中文/空格目录的 15 项工程检查通过，中文截图已核对；232 项自动测试中 231 通过、1 跳过。未打包个人设置、原始视频、标注或历史记录。见 [便携使用说明](docs/PORTABLE_GUIDE.md) 与 [本轮验收记录](docs/PORTABLE_TEST_REPORT.md)。这是本地测试版，真实设备、长时间运行和公开交付许可材料仍待完成；训练与 GitHub 同步继续暂停。

2026-10-12 存储管理：摄像头默认仅事件片段，新增记录占用扫描、清理预览与确认、重要保护、当前查看排除、期限/容量管理和超额停止。默认 30 天/5 GiB，自动清理关闭，不立即删除历史。详见 [存储指南](docs/STORAGE_GUIDE.md) 和 [工程验收](docs/STORAGE_TEST_REPORT.md)。

2026-10-12 Windows 产品开发：新增 USB/虚拟摄像头入口、安卓手机接入说明、无模型预览、实时监控、断线保存及按采集时间回放。204 项自动检查与 7 项真实模型/Qt 联调检查通过；后者用现有合法视频替代相机，真实手机/驱动和正式安装包仍待验收。见 [摄像头说明](docs/LIVE_CAMERA_GUIDE.md) 和 [验收记录](docs/LIVE_CAMERA_TEST_REPORT.md)。训练与 GitHub 同步继续暂停。

2026-10-11 当前重点为帧检测：已接入“YOLO26 火焰 + 原烟雾（试用）”。固定 30 段旧开发视频中，原始 fire 误检帧 857→248，正标签帧检出 5713→6151；这不是独立泛化验证，也不是框 mAP。192 项自动检查通过，并完成 536 帧真实组合推理核对。本机已选用新方案，标准确认和完整检测保留；出厂默认及命令行默认仍保留原模型以便复现。新权重不包含在迁移包内。对照、退步案例、准备方式和视觉指数限制见 [现成模型试用记录](docs/MODERN_FIRE_MODEL_TRIAL.md)。

2026-10-11 新增产品框架：检测区域覆盖趋势与短期外推、分级提示中心及人工反馈、可选节能巡检与疑似事件加强检测。界面设置集中到设置页；默认保持完整逐帧检测。操作与边界见 [软件框架说明](docs/PRODUCT_FRAMEWORK.md)。预测不是物理火势预测，检测次数不是实测电量，新增模式尚需独立效果验证。

框架阶段 154 项自动检查和 9 项真实模型/Qt 联调检查通过，输入、保留的误唤醒案例与界面截图见 [功能验收记录](docs/PRODUCT_FRAMEWORK_TEST_REPORT.md)。

事件使用流程已补齐：前后视频回放、逐帧核查、提示与事件跳转、处理状态筛选、可读复核报告。当前 167 项自动检查、14 项已有视频/Qt 工作流检查通过；没有重新推理或训练。见 [操作说明](docs/EVENT_REVIEW_GUIDE.md) 和 [验收记录](docs/EVENT_REVIEW_TEST_REPORT.md)。

设置与交付准备：新增设置自动保存/恢复默认、后台环境诊断、可收起的错误提示及显式 CPU 重试。当前 180 项自动检查通过，本地源码迁移包已完成同机新路径检查；另一台电脑的全新环境安装仍需实际验收。见 [设置与迁移指南](docs/SETTINGS_AND_TRANSFER_GUIDE.md)、[验收记录](docs/SETTINGS_DELIVERY_TEST_REPORT.md)。

## 桌面版启动

双击 `start_desktop.cmd`，点击“壁炉示例”或“打开视频”，再点击“开始分析”。可停止、查看曲线与事件、查询历史并导出 ZIP。详情见 `docs/DESKTOP_GUIDE.md`，阶段验证见 `docs/DESKTOP_TEST_REPORT.md`。

桌面版与下方命令行共用处理逻辑；分析后台运行，停止或关闭窗口会等待当前帧处理与记录保存。无需重新安装 PyTorch。

界面“检测方案”可选择“原模型”、“减少反光误报（候选）”或“YOLO26 火焰 + 原烟雾（试用）”。后两种固定阈值 0.35，均保留原烟雾分支。亮光候选在开发视频中减少误提示，但部分真实火焰确认更迟或中断。首次使用亮光候选需先运行 `experiments/screen_brightlight_candidate.py --stage fetch`；新 YOLO26 方案的准备见上述试用记录。启动时不自动下载。

选择亮光候选检测后，“事件关联”可切换为“稳定区域（候选）”：让同一区域内轮流出现的局部火焰框接续证据，保留原确认时间条件。开发集壁炉案例首次确认 42.08→28.96 秒，fire 事件检出仍为 9/13、误提示仍为 0；未解决所有延迟或漏检。这是既有候选的历史验证，新 YOLO26 方案使用原关联，详情见 `docs/EVENT_REGION_VALIDATION_REPORT.md`。

确认速度可在“分析配置”选择“快速响应”：稳定目标可在约 0.5 秒检测窗口后确认，断续目标继续观察。该可选档位在旧开发回放中增加了误报，默认仍为标准；案例与限制见 [确认速度报告](docs/CONFIRMATION_SPEED_REPORT.md)。

## 当前环境

项目虚拟环境位于 `.venv-pytorch`，无需激活即可使用。它通过 `--system-site-packages` 复用已有 `D:\anaconda\envs\pytorch` 环境的 Python 3.9.25、PyTorch 2.8.0+cu128 和 torchvision 0.23.0+cu128；检测新增依赖位于项目目录，未修改原有环境。GPU 为 RTX 4060 Laptop。

当前 `.venv-pytorch` 仍依赖上述基础环境路径，不能直接复制给其他电脑。源码迁移时使用 `setup_desktop.ps1` 在项目 `.venv` 重建，可复用另一台电脑已安装的匹配 PyTorch，也可显式安装独立 CPU/CUDA 环境。直接试用软件可使用上述便携包；它已完成同机搬迁验收，独立新电脑验收仍待实际进行。

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

测试稳定区域关联同时加 `--association-profile regions`；恢复原关联用 `--association-profile original`。稳定区域候选只允许与 brightlight、开启时序的模式组合。关联方案及区域参数随记录和 ZIP 导出保存；风险覆盖仍由实际检测框计算。

节能监测试用模式加 `--monitoring-profile adaptive`：低频巡检、定期高分辨率复查，疑似目标触发逐帧加强；恢复完整检测用 `--monitoring-profile full`。未检测帧记录 `observed=false`，不能当作无火无烟的检测结果。声音设置用于桌面客户端，命令行只保存提示记录。

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
- `trend.jsonl` / `trend-latest.json`：检测框覆盖短期外推与最后观察，保留不足/暂缺/不稳定状态。
- `alerts.jsonl` / `feedback.jsonl`：分级提示与用户主动提交的确认/误提示反馈；不自动修改标注或模型。
- `monitoring.jsonl`：每帧检测与否、输入尺寸、监测状态和累计检测次数。

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
