# 焰鉴 0.3.0-rc1 交付使用说明

目标：Windows 10/11 64位。两个组件都已自带需要的运行环境，无需安装Python；不要复制开发机环境或个人缓存。

1. 完整解压主程序ZIP，进入FireGuard目录。
2. 使用AI画面理解时，把本地AI组件ZIP解压到同一个FireGuard目录。最终应为FireGuard.exe与ai-component目录并列。
3. 双击“启动完整版.cmd”。首次加载AI模型需要片刻，准备后进入监控台。只使用烟火检测时，可直接双击FireGuard.exe。
4. 点击“添加监控点”，选择本地录像，填写名称与位置。可添加多路录像，分别设置检测模型，点击“开始全部”。
5. 点击卡片查看整路AI画面理解；点击事件行查看该事件的研判、证据及处理操作。列表支持排序、展开、拖动和状态筛选。人工已处理可撤回，历史记录保留。

主程序烟火检测使用CPU以适应更多Windows电脑。本地AI组件使用Qwen3-VL-2B-Instruct，按当前NVIDIA 8GB显存环境验证，显卡驱动需支持所带CUDA 12.8运行库；不同电脑的速度和兼容性仍须实测。未提供显卡驱动安装程序。AI只连接127.0.0.1，模型文件随组件提供，不要求把画面上传到外部服务。

记录保存在FireGuard/data/outputs，设置和缓存位于FireGuard/data/.cache。AI日志位于FireGuard/data/outputs/local-ai。请保留完整_internal、portable.flag和ai-component目录，更新前备份data；新包解压到新目录，避免覆盖旧记录。

应用采用AGPL-3.0-only；模型与第三方库保留各自许可。主程序带application-source.zip、third-party-notices、licenses和corresponding-sources。本地AI组件附运行服务源码、模型来源信息及依赖许可。软件为视觉辅助核查工具，尚非认证消防报警设备。
