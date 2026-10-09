# Git 与 GitHub 管理

当前项目已建立本地 Git 仓库并保存初始代码版本，主分支为 `main`。用户指定远程为 [yuuniqueh/fire-and-smoke-detection](https://github.com/yuuniqueh/fire-and-smoke-detection)，已在本地设置为 `origin`。

2026-10-09 检查时远程为空、可见性为公开，账号有写入权限。用户已确认上传代码等项目文件并要求保护隐私。本次公开范围为代码、测试、配置、说明、公开来源清单及仅使用匿名代号的标注 JSON；上传前检查当前文件和提交历史，不发布凭据、真实联系方式或个人用户目录。Git 提交身份使用 GitHub 隐私邮箱。

首次公开推送已于 2026-10-09 完成，`main` 跟踪 `origin/main`。上传前检查了 263 个历史文本文件版本，未发现匹配的个人用户目录、真实邮箱、手机号、证件号、访问令牌或私钥；标注人均为空值或匿名代号 `R1`。本地检查记录位于 `outputs/github-privacy-check-20261009.json`，不随代码上传。此检查不替代后续提交前的人工核对。

Git 保存代码版本，GitHub 托管这些版本。建议继续在本机编辑和运行，验证通过后提交、推送；GitHub 网页适合查看差异或修改少量文档，完整软件仍需在配置好的电脑上测试。

## 纳入与排除

代码、测试、配置、说明文档、模型来源清单及标注 JSON 纳入管理。`.gitignore` 排除了虚拟环境、缓存、模型权重、原始视频、数据集、标注截图、输出日志、压缩包、比赛 PDF、环境凭据、私钥和证书容器。`.gitignore` 不会清理已经提交的文件，公开前还必须检查提交历史。

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

本地远程地址已经设置，无需重复添加。首次推送为 `git push -u origin main`。不要强制推送覆盖远程修改；后续用 `git status -sb` 和 `git log -1 origin/main` 核对同步状态。后续提交前也应检查自由备注和新增文档，避免写入真实姓名、联系方式、账户凭据等个人信息。

后续修改模型或时序机制应使用独立分支，保留原始基线、模型 SHA256、实验配置和结果摘要。大型逐帧结果不进代码仓库，通过报告记录本地实验路径和校验值。被 Git 忽略的数据不会自动同步到其他电脑。

`.gitattributes` 保留文件原始字节，不自动转换换行符，避免克隆后使冻结源码的 SHA256 失效。修改冻结算法或标签时需要建立新版本评价计划，不重写历史基线的校验值。

## 参考

- [Git 与 GitHub 的关系](https://docs.github.com/en/get-started/using-git/about-git)
- [提交身份与隐私邮箱](https://docs.github.com/en/account-and-profile/reference/email-addresses-reference)

GitHub 的私有可见性不替代模型、代码和素材的授权要求；组件记录继续以 `THIRD_PARTY.md` 为准。
