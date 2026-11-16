对应源码与重建说明

libcec-source.tar.gz 是 build-info.json 所标注 libCEC 提交的完整仓库源码归档。
cec-debug-tools 中是本包所用 GUI、后端和打包脚本的实际源码快照；原始 oui.csv 在包顶层。
这些归档不含个人配置、现场抓包、构建工具或源仓库的 .git 数据。

建议的重建步骤
1. 从 https://github.com/blinkjum/cec-debug-tools 克隆工程，包含子模块。
2. 根据 build-info.json 的 tool_base_commit 与 libcec_commit 选择相应 Git 版本。
3. 将 cec-debug-tools 源码快照覆盖到该工具工作区，并将包顶层 oui.csv 复制到工作区。
4. README.md 列出了原生构建所用 CMake、Ninja、LLVM-MinGW 版本和下载方式。
   使用 build-libcec-custom.ps1 重新构建包含回归检查的 libCEC DLL。
5. 按 README.md 建立独立 Python 打包环境，再运行 build-windows.ps1。

只需使用本工具时不需要执行以上步骤，也不需要安装 Python 或编译工具。
libCEC、Python、Tcl/Tk、PyInstaller 与本次原生工具链的许可证在包内 licenses 目录。
