# Git 与 GitHub 管理

当前项目已建立本地 Git 仓库并保存初始代码版本，主分支为 `main`。用户指定远程为 [yuuniqueh/fire-and-smoke-detection](https://github.com/yuuniqueh/fire-and-smoke-detection)，已在本地设置为 `origin`。

2026-10-09 检查时远程为空、可见性为公开，账号有写入权限。首次上传被自动审批阻止，等待用户确认公开代码、文档、模型来源清单及人工标注 JSON 的范围，或先将仓库改为私有；目前尚未上传。

Git 保存代码版本，GitHub 托管这些版本。建议继续在本机编辑和运行，验证通过后提交、推送；GitHub 网页适合查看差异或修改少量文档，完整软件仍需在配置好的电脑上测试。

## 纳入与排除

代码、测试、配置、说明文档、模型来源清单及标注 JSON 纳入管理。`.gitignore` 排除了虚拟环境、缓存、模型权重、原始视频、数据集、标注截图、输出日志、压缩包、比赛 PDF 以及环境凭据文件。

比赛 PDF、模型、素材、标注截图及实验输出仍保存在本地，需要单独备份。GitHub 代码仓库不能替代完整实验档案；换电脑时除了克隆代码，还需按来源清单恢复合法素材和固定版本权重、重建 Python 环境。不要复制 `.venv-pytorch` 当作可移植运行环境。

## 常用流程

开始另一台电脑上的工作前先同步仓库，完成一项改动后在本机验证、查看差异，再提交并推送。避免两台电脑在未同步的情况下同时修改同一批文件。

```powershell
git status
git diff
git add .
git commit -m "描述这次修改解决的问题"
git push
```

本地远程地址已经设置，无需重复添加。用户确认上述公开范围，或改为私有并重新核查后，再执行首次推送 `git push -u origin main`。不要强制推送覆盖远程修改；后续用 `git status -sb` 和 `git log -1 origin/main` 核对同步状态。

后续修改模型或时序机制应使用独立分支，保留原始基线、模型 SHA256、实验配置和结果摘要。大型逐帧结果不进代码仓库，通过报告记录本地实验路径和校验值。被 Git 忽略的数据不会自动同步到其他电脑。

`.gitattributes` 保留文件原始字节，不自动转换换行符，避免克隆后使冻结源码的 SHA256 失效。修改冻结算法或标签时需要建立新版本评价计划，不重写历史基线的校验值。

## 参考

- [Git 与 GitHub 的关系](https://docs.github.com/en/get-started/using-git/about-git)
- [提交身份与隐私邮箱](https://docs.github.com/en/account-and-profile/reference/email-addresses-reference)

GitHub 的私有可见性不替代模型、代码和素材的授权要求；组件记录继续以 `THIRD_PARTY.md` 为准。
