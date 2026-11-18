# 从源码构建焰鉴

环境：Windows x64、Python 3.9；本版 CPU 构建清单为 packaging/requirements-cpu.txt。第三方源码及许可随 corresponding-sources 交付，便于取得和修改。运行库可从官方发行包安装；重建本应用不需要从零编译 PyTorch 或 Qt。

1. 将 application-source.zip 解压到一个新的可写目录，进入 FireGuard 子目录。不要覆盖有个人记录的旧软件目录。
2. 运行 `python -m venv .venv` 创建独立环境。安装时先用官方 CPU 索引安装 torch==2.8.0+cpu、torchvision==0.23.0+cpu，再安装 packaging/requirements-cpu.txt。构建所用轮子文件和 SHA-256 记录在配套重建证据中；需要离线安装时使用该轮子目录和 `--no-index --find-links`。
3. 把随软件分发的 _internal/models 中六份登记权重按原相对路径复制到源码 models。保留源码中的来源 manifest、LICENSE 和 README。build_windows.py 在构建前会验证权重 SHA-256；不要下载任意同名 best.pt 替代。家庭场景权重还需要固定依赖 dill==0.4.0，已列入 CPU 清单。
4. 确认源码中的 LICENSE、NOTICE、licenses、configs、docs 与 packaging 文件完整。对应第三方源码可放到源码目录的 .cache/release-compliance/sources，并保留 source-manifest.json；也可使用构建参数指定既有对应源码目录。
5. 执行 `.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests`。测试需要已有视频/数据的部分应按实际条件执行，不预填通过。
6. 执行 `.venv\Scripts\python.exe -X utf8 build_windows.py --output 新的构建目录`。使用既有第三方源码目录时增加 `--corresponding-sources 对应源码根目录`（此根目录内需有 source-manifest.json 和 sources 子目录）。已有目录不会被覆盖。输出目录包括 dist/FireGuard 和软件 ZIP。
7. 完整解压新的 ZIP 到另外一个目录，运行“检查软件.cmd”，并核对模型推理、界面、保存、回放和导出。测试数据与实际使用数据隔离保存。

源码中的 requirements-lock.txt 保留旧 GPU 开发环境版本供历史追溯；它不是本 CPU 包的安装依据。使用 packaging/requirements-cpu.txt。不要复制开发机整个虚拟环境或个人 .cache。

Qt 仅保留本界面需要的 Core、Gui、Widgets 和必要平台/图像插件，排除未使用的虚拟键盘、PDF 和 QML/Quick。修改这项策略后须重新检查相关功能，不应直接从旧包随意删 DLL。FFmpeg 与 Qt 等外部库的版本、官方构建脚本及源档案见对应源码清单。
