# CEC Debug Tools

这是基于 Pulse-Eight USB-CEC Adapter 和 libCEC 的 Windows CEC 调试、
报文监视、角色模拟及测试自动化工具。后端通过 `ctypes` 直接调用
`cec.dll` C API，不启动或解析 `cec-client.exe` 子进程。

本仓库通过 `libcec-upstream` 子模块引用带有原始总线帧回调扩展的
libCEC 分支。发送、接收、Poll ACK 状态、统计、拓扑和角色模拟共用
同一个原生连接。

## 功能

- 选择 `cec.dll`、COM 端口、角色、HDMI/Base 地址、OSD、Vendor ID 和监视模式
- 原生 API 命令面板：包含 `tx`、`txn`、`scan`、`mon`、`pow`、`gas`、`gsam`、`ea`、`da`、`play`、`bl` 等
- 适配器枚举、libCEC 编译信息、统计和 Bootloader 工具入口
- CEC 自定义场景执行、命令间隔、`wait`/`expect`/`assert` 检查
- 保存 libCEC 原生日志和报文输出
- 命令执行记录、输出滚动、参数保存
- 角色模拟器：TV / Audio System / Playback / Recorder / Tuner
- 内置 Power、OSD、Vendor、CEC Version、Physical Address、Audio Status、System Audio Mode 等常用回复
- 可配置自定义规则：`入站 opcode|目标|响应字节`，支持 `{power}`、`{volume}`、`{vendor}`、`{physical}`、`{osd}` 等变量
- 设备模拟与 Raw 注入：角色模拟和任意 Source LA、Destination LA、Opcode、参数、完整报文注入集中管理
- Raw CEC Opcode 帮助：可下拉选择常用 Opcode，实时显示消息作用、通信方向和参数格式；仍支持手动输入未收录或厂商自定义 Opcode
- 全局枚举下拉框：统一显示“raw 值 — 解释”，涵盖日志级别、连接模式、角色、电源状态、抓包方向、CTS 执行方式和 CDF 决策；程序内部仍保存纯 raw 值
- CEC 报文监视：结构化显示 TX/RX、Source/Destination、Opcode、参数和 ACK/NACK/ERROR；支持 Polling、过滤、多色 Opcode 关注、增量刷新、智能尾部跟随、右键复制及 CSV/JSON 导出
- 报文工作台：可折叠的快捷命令、组合查找及书签注释；快捷发送共用当前 libCEC 连接
- 原生统计：读取 `tx_ack`、`tx_nack`、`tx_error`、`rx_total`、`rx_error`
- 连接与拓扑：连接页负责会话，拓扑预检作为连接页内的子页面
- 高级工具：保留完整 libCEC 命令控制台、脚本工具、统计、Bootloader 和 API 信息，不干扰普通测试流程
- CTS 测试执行器：内置 102 个基于 CTS/CDF 明细的完整测试项目，可按章节/执行方式筛选和勾选；项目统一归类为自动、人工、外部 CTS、条件执行四类；自动项目通过 libCEC API 发包、监听回调并按期望条件判定；人工/外部/条件项目显示完整流程和判定条件，并可人工录入结果；支持 HTML/JSON 报告

## 运行

### Windows 便携版

便携包位于本地 `dist/CEC-Debug-Tools-Windows-x64-*.zip`。完整解压后运行
`CEC-Debug-Tools.exe`，无需安装 Python；必须保留 `_internal` 运行库目录，以及
EXE 旁的自编译 `cec.dll` 和 `oui.csv`。首次使用适配器仍需安装相应 USB 驱动。
包内附离线自检结果、SHA-256、第三方许可证和对应源码，不含个人设置或现场抓包。

可重建方式（Windows x64 Python）：

```powershell
python -m venv work/package-venv
work/package-venv/Scripts/python.exe -m pip install -r packaging/requirements-build.txt
powershell -File build-windows.ps1
```

构建脚本选择工具目录下最新的自编译 libCEC DLL，保留其哈希与原生提交号；
在中文/空格路径及不含 Python 的 PATH 下运行 EXE 离线自检，通过后才生成分发 ZIP。
自检不会打开 COM 端口，也不会向 CEC 总线发送消息，不能代替真实硬件验证。

### 源码运行

克隆时请同时拉取 libCEC 子模块：

```powershell
git clone --recurse-submodules https://github.com/blinkjum/cec-debug-tools.git
cd cec-debug-tools
```

双击 `run-cec-client-gui.cmd`，或执行：

```powershell
python .\cec-client-gui.py
```

程序优先查找仓库内构建安装目录：

```text
libcec-custom-install\bin\cec.dll
```

如果本地尚未构建自定义 libCEC，则回退查找：

```text
C:\Program Files\Pulse-Eight\USB-CEC Adapter\cec.dll
```

如果路径不同，可以在界面中浏览选择。

## 报文监视工作台

进入“CEC 报文监视”，点击“快捷命令 / 组合查找 / 书签注释 / Opcode 关注”展开底部面板，再次点击当前按钮可收起。

- **快捷命令**：内置电源、音量、OSD、厂商ID、物理地址、CEC版本、SAD、ARC及遥控等13条命令。可修改源/目标LA、Opcode和参数，实时预览完整报文；支持自定义分组、保存/删除及F5–F12快捷键。音量等44按键命令默认在100ms后发送45释放。快捷发送在后台直接调用现有libCEC API；快捷键仅在监视页生效。
- **组合查找**：源/目标LA、Opcode、ACK状态、参数掩码和顶部文字按AND组合；LA/Opcode可逗号多选，`POLL`匹配轮询。参数为前缀匹配，`??`代表任意字节，`A?`代表任意低半字节。可选择“筛选列表”或“仅定位”，保存常用条件，用F3/Shift+F3前后定位；查找覆盖本次会话全部记录，可跳到2000条显示窗口之前的消息。
- **书签注释**：右键消息添加/取消书签，编辑多行注释及标签；F2/Shift+F2跳转本会话标记。书签按会话UUID和消息编号保存，并包含原始消息快照；清空列表会开始新会话，历史标注仍能从“全部会话”查看。CSV/JSON导出增加会话、书签、注释和标签字段。
- 快捷命令及搜索预设保存在原有`settings.json`中的`workbench`字段；书签自动保存到同目录`bookmarks.json`（Windows通常为`%APPDATA%/PulseEight/CECClientGUI/`）。写入经过后台队列并原子替换，退出时完成保存。
- **Opcode 关注**：在底部面板配置12个关注项，Opcode下拉框同时显示raw值与解释，也可输入未定义的十六进制值。每项可独立选择16种预设颜色，或用“自选”打开系统颜色选择器；文字颜色按背景明暗自动调整。仅命中的Opcode单元格着色，源地址行底色继续保留；同一Opcode重复配置时编号最小的项生效，选中行仍保持蓝色高亮。“清空关注”清除Opcode，“恢复默认颜色”保留Opcode并还原颜色。配置自动保存至`settings.json`，原有四项自动迁移，隐藏面板后继续生效。
- “返回最新”恢复显示刷新并跟随最新数据；关注颜色调整只重绘现有行，不重建列表，保留选中消息和滚动位置。

## 使用建议

1. 先填入适配器端口，例如 `COM5`；如果设备管理器显示了其他 COM 号，按实际端口修改。
2. 如果要模拟 TV，角色选择 `x`；普通控制器可按需要选择 `p`、`r`、`t`、`a`。
3. 点击窗口顶部的“连接设备”；普通测试进入“CEC CTS 测试”，底层命令和脚本进入“高级工具”。
4. `bl`、`q`、`am` 等可能改变适配器或设备状态的命令会弹出确认框。

## 角色模拟

打开“设备模拟与 Raw”页面下的“角色模拟”子页面：

1. 选择角色：`x`=TV，`a`=Audio System，`p`=Playback，`r`=Recorder，`t`=Tuner。
2. 配置逻辑地址、物理地址、OSD、Vendor ID、电源、音量和 System Audio Mode。
3. 点击“启动角色模拟”。程序以 libCEC monitor-only 模式接收 `commandReceived` 回调，并将匹配的响应通过 `libcec_transmit` 发回总线，不会再叠加 libCEC 自身的角色自动回复。

自定义规则示例：

```text
8F|origin|90:{power}
46|origin|47:{osd}
A0|origin|00:01
```

规则目标可以是 `origin`、`broadcast` 或十六进制逻辑地址。自定义规则优先于内置规则。角色模拟应在没有同逻辑地址真实设备的测试链路中使用。

## 自定义场景示例

在“CEC CTS 测试”页面下打开“自定义场景”：

```text
# libCEC API 命令直接发送
la 0
tx 05:70:10:00
wait 500
expect-opcode 72 timeout=1500
assert rx-count>=1
```

场景收到匹配的 RX 报文后继续执行；期望报文超时或断言失败时，场景标记为失败。测试过程中的所有帧会进入“报文与证据”页面。

## Raw CEC 注入

如果目标是手工模拟任意 LA 并发送任意 CEC 报文，应使用“设备模拟与 Raw”页面下的“Raw CEC 注入”，而不是角色模拟。例如：

```text
Source LA:      0
Destination LA: F
Opcode:         82
Parameters:     10:00
完整报文:       0F:82:10:00
```

点击“应用 LA”调用 `libcec_set_logical_address(0)`；点击“发送 tx”调用 `libcec_transmit`。`txn` 使用零等待传输超时。完整报文的第一个字节必须由 Source/Destination LA 组成：`(Source << 4) | Destination`。

## 逻辑地址占用/碰撞实验

“设备模拟与 Raw → 逻辑地址占用/碰撞”使用自编译 libCEC 的原生扩展，
直接设置 Pulse-Eight USB 适配器的硬件 ACK Mask，不经过 LA 分配轮询。
一个适配器可以同时占用多个 LA，例如 Audio System 的 `5`、Playback 的
`4/8/B`、Recorder 的 `1/2/9` 或 Tuner 的 `3/6/7/A`。`F` 不允许占用。
仅 ACK 占用不自动发送 Report Physical Address 或其他角色回复。

1. 勾选候选地址。可勾选“连接时启用地址占用”再使用顶部“连接设备”，
   或在已有连接中点击“开始占用 / 更新占用”。工具会停止 GUI 角色回复，
   切换至监听路由后设置硬件 ACK。DUT 须已开启 CEC、取得有效物理地址，
   并处于该 case 要求的 HDMI 拓扑中。
2. 重启 DUT，或人工/夹具触发其重新分配地址。观察自 Poll（例如 `55`）
   的 ACK、备用地址轮询、`Report Physical Address` 及 DUT 的实际状态。
3. 点击“释放全部”，重新触发 DUT 分配，观察 NACK/取得地址和恢复行为。
4. 需要同 LA 的重复宣告时，可在 Raw 页面注入 `5F:84:20:00:05`。
   占用期间 Raw 自动应用 LA 会跳过，发送 Header 中的 Source 保持不变。
   仅 Header 的 Raw 帧（例如 `55`）表示 Poll。
5. 用“导出实验 JSON”保存操作/观测记录和 Poll ACK/NACK 计数；完整报文仍在
   “CEC 报文监视”中，使用其 CSV/JSON 导出。实验页保留最近 500 条记录。

底部始终显示占用状态。占用期间不能关闭监听路由或注册普通角色；
“读取占用状态”返回适配器最后接受的配置，不等于线上的 ACK 测量。
实际 ACK/NACK 来自 `rawFrame` 的 `cec_command`，不解析文本日志。
释放后继续被动监听；正常断开会清除测试 Mask，下次启动默认不自动占用。
旧 DLL 或其他适配器会显示不支持，不会假报占用成功。

本模式可制造“候选地址已被占用”和“已运行 DUT 遭遇重复 LA”条件。
通过/失败应按 DUT 的 CEC 版本、角色、CDF 和具体 case 判定；单凭无响应不能
判定通过。HPD/重启仍需设备端或夹具触发，硬件 ACK 不提供位级仲裁/同时发送控制。
同 LA 的设备在总线 Header 中无法区分，需结合实验步骤及 DUT 日志归因。

原生扩展（仅本项目分支）为 `libcec_set_test_ack_mask(connection, uint16_t mask)`
和 `libcec_get_test_ack_mask(connection)`；Mask 为 0 时释放。接口要求全部客户端
处于 monitor-only 状态，并对适配器支持、广播位和 USB 命令返回结果进行检查。
编译后需重新启动 GUI 加载新 DLL；测试可运行
`python -m unittest discover -s tests -v`。

当前 x64 DLL 可通过 `powershell -File build-libcec-custom.ps1` 重建。脚本使用
工程 `work/build-tools` 内的便携 CMake 3.31.6、Ninja 1.11.1.4 和 LLVM-MinGW
20261006 UCRT x64；先构建到独立目录，通过原生 Mask 和线程回归检查后，安装到
`libcec-custom-install/bin/cec-occupancy-<hash>.dll`。
GUI 启动时优先加载最新构建，旧 DLL 保留用于回退；已运行的 GUI 需重启。
这些工具是本机构建依赖，不会提交入库。CMake/Ninja 可用
`python -m pip install --target work/build-tools cmake==3.31.6 ninja==1.11.1.4` 获取，
LLVM-MinGW 使用[官方发布包](https://github.com/mstorsjo/llvm-mingw/releases/tag/20261006)，
解压到同一 `work/build-tools` 目录。

所绑定的 libCEC 子模块包含异步线程重复启动死锁修复：在启动线程前预留运行状态，
避免 Sony 连续厂商命令触发持锁等待。原生线程回归覆盖重复/并发启动、停止、自然结束后
重启及立即销毁；构建脚本在回归失败或超时后不会安装新 DLL。旧版本 DLL 保留用于回退。

## 持续抓包与界面刷新

报文表格默认采用紧凑列宽，窗口缩小时适当压缩长文本列；低于最小可读宽度时仍可横向滚动。
可拖动表头分隔线调整列宽，双击分隔线按当前内容调整，“重置列宽”恢复默认。
长报文、Opcode、参数及解释按列宽换行，原始值和导出内容保持不变；“说明”可展开 ACK/时长规则。
双击消息打开可缩放的详情窗口，展示完整报文、Opcode/参数解释、ACK状态、时序、书签与注释；内容可换行、滚动、选择复制，也可一键复制报文或全部详情。详情保持打开时的消息快照，窗口不会阻断抓包。
需要复用报文时，右键选择“载入 Raw 注入”，会同步填入源/目标LA、Opcode、参数与完整报文并切换到Raw页；该操作等待手动发送。载入Polling会清空Opcode和参数，避免残留上一条消息的字段。

“CEC 报文监视”仍从原生 `rawFrame` 回调接收结构化报文，不解析或去重文本日志。
列表最多展示 2000 行，满表后淘汰旧行（保留选中行），内存记录与 CSV/JSON 导出
不受这项显示上限影响。表格只绘制可见区域，保留参数换行、Source 底色及 Opcode
单元格多色高亮，避免满表后移动数万个绘图对象。

事件轮询按批次/时间预算处理；单条事件或地址占用实验界面异常不会中断后续轮询。
“暂停刷新”只暂停展示，取消后立即补齐列表；浏览历史时不跳到底部，滚到底部后
继续跟随新报文。下方状态显示累计帧数、待处理事件数、距最近处理帧的时间，以及
“跟随最新 / 浏览历史 / 显示已暂停”，便于区分界面停留与实际无新数据。

界面异常通过后台写入工具目录下 `outputs/cec-gui-errors.log`，包含时间与异常堆栈。
该日志用于定位刷新异常，不能作为 CEC 总线流量来源。回归检查：
`python -m unittest discover -s tests -v`，包含原生回调入口的持续收包、满表淘汰、
地址占用 Poll 统计、选中保持、暂停恢复及界面异常注入；测试不向总线发送报文。

## CTS 测试执行器

> 注意：内置目录是项目级测试自动化数据，不是 HDMI Forum 发布的
> 规范原文，也不能替代授权认证实验室、CEC-CTT 或正式认证结论。

“CTS 测试执行器”页将 CTS 项目分为四类：

- `AUTO`：当前可以通过 libCEC API 发出消息、监听 RX 回调并自动判定，例如 Give Physical Address、Get CEC Version、Vendor ID、OSD Name、Audio Status 等消息语义。
- `MANUAL`：需要真实设备动作、待机/上电、按键、音频听感或人工确认；GUI 会展示前置条件、流程、通过条件和失败条件，可用“人工通过/人工失败”录入。
- `EXTERNAL CTS`：需要 CEC-CTT、Quantum Data/Simplay 分析仪、示波器、ACK/NACK/仲裁/位级故障注入等设备；这些项目不会被伪装成自动通过。
- `CONDITIONAL`：测试项目本身保留在完整 CTS 目录中，不再因为当前 CDF 自动跳过。测试人员需要对照当前产品 CDF，手动决定是否勾选；如果不适用，应在报告中保留 CDF 字段、版本和排除理由。

执行前确认 DUT LA 和测试端 LA（默认 5/0），并确保测试端不与真实设备占用同一逻辑地址。测试人员先按 CDF 勾选适用项目，再执行选中项目。自动项会使用当前 COM 端口和同一个 libCEC 原生连接；如果尚未建立会话，CTS 执行器会临时打开 monitor-only 连接，完成后自动关闭。报告默认导出 HTML，也可选择 JSON。报告会保留执行方式、CDF 决策和能力边界，不能替代 HDMI-ATC/CEC-CTT 的最终认证结论。
