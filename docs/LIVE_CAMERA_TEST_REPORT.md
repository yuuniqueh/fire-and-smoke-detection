# Windows 摄像头接入工程验收

日期：2026-10-12。

## 修改范围

增加 Windows 摄像头类型、编号、连接说明和无模型预览；实时监控复用检测/时序/风险/趋势/提示/反馈模块。本地文件仍逐帧处理并按帧号/FPS 计时。

独立采集进程、有限队列、可中止连接/读取；实时使用电脑接收帧的相对单调时间。保存已处理帧及回放索引，导出包含索引；增加断线/磁盘不足结束原因。

没有训练、搜索模型、调整确认条件、改变本机保存的模型选择或提交 GitHub。

## 自动检查

新增 `tests/test_camera_monitoring.py` 的 12 项检查通过：非法编号、真实 Windows spawn 子进程的有限队列替换旧帧、打开失败、驱动读取卡住后的超时和终止、主动取消、重复释放、采集时间确认、断线保存、主动停止、磁盘保护、录像/索引/导出相符、无效索引拒绝、输入切换及 Qt 回放定位。

完整自动检查：204 项通过，包括已有 192 项回归检查。

```powershell
$env:TEMP=(Resolve-Path -LiteralPath outputs/workflow-test-temp).Path
$env:TMP=$env:TEMP
.\.venv-pytorch\Scripts\python.exe -X utf8 -m unittest discover -s tests -q
```

## 真实模型与 Qt 联调

`experiments/verify_live_desktop.py` 使用已有 CC0 壁炉视频作为有节奏的摄像头替代输入，不是实际摄像头。现有原模型运行实时流程，不覆盖用户设置。

7 项联调检查通过：无模型预览、停止释放、真实模型确认/告警/停止保存、停止后不显示为实时、按采集时间回放、记录导出、界面持续响应。

证据：`outputs/live-desktop-verification.json`、`outputs/live-desktop-preview.png`、`outputs/live-desktop-stopped.png`、`outputs/live-desktop-record-verification.zip`。

首次运行 `outputs/run-20261012-191437-665499`：29 帧、1 个确认事件、手动停止，最后观测 14.172 秒；界面最大心跳间隔 0.125 秒。

这些次数和耗时只验证接线与响应，不评价真实摄像头帧率、手机延迟或识别质量。替代输入与推理同线程，真实子进程有限缓冲另由自动测试验证；尚未完成真实驱动到界面的整链路测试。

## 尚未完成

真实 USB/Android 权限与驱动、同一 Wi-Fi/USB 连接、长时间运行、录像轮转、手机到界面的实际延迟、CPU 新电脑、正式安装包和分发许可证清单验收。当前只能称为摄像头功能开发版。
