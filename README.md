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
- CEC 报文监视：结构化显示 TX/RX、Source/Destination、Opcode、参数和 ACK/NACK/ERROR；支持 Polling、过滤、四色 Opcode 关注、增量刷新、智能尾部跟随、右键复制及 CSV/JSON 导出
- 原生统计：读取 `tx_ack`、`tx_nack`、`tx_error`、`rx_total`、`rx_error`
- 连接与拓扑：连接页负责会话，拓扑预检作为连接页内的子页面
- 高级工具：保留完整 libCEC 命令控制台、脚本工具、统计、Bootloader 和 API 信息，不干扰普通测试流程
- CTS 测试执行器：内置 102 个基于 CTS/CDF 明细的完整测试项目，可按章节/执行方式筛选和勾选；项目统一归类为自动、人工、外部 CTS、条件执行四类；自动项目通过 libCEC API 发包、监听回调并按期望条件判定；人工/外部/条件项目显示完整流程和判定条件，并可人工录入结果；支持 HTML/JSON 报告

## 运行

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

## CTS 测试执行器

> 注意：内置目录是项目级测试自动化数据，不是 HDMI Forum 发布的
> 规范原文，也不能替代授权认证实验室、CEC-CTT 或正式认证结论。

“CTS 测试执行器”页将 CTS 项目分为四类：

- `AUTO`：当前可以通过 libCEC API 发出消息、监听 RX 回调并自动判定，例如 Give Physical Address、Get CEC Version、Vendor ID、OSD Name、Audio Status 等消息语义。
- `MANUAL`：需要真实设备动作、待机/上电、按键、音频听感或人工确认；GUI 会展示前置条件、流程、通过条件和失败条件，可用“人工通过/人工失败”录入。
- `EXTERNAL CTS`：需要 CEC-CTT、Quantum Data/Simplay 分析仪、示波器、ACK/NACK/仲裁/位级故障注入等设备；这些项目不会被伪装成自动通过。
- `CONDITIONAL`：测试项目本身保留在完整 CTS 目录中，不再因为当前 CDF 自动跳过。测试人员需要对照当前产品 CDF，手动决定是否勾选；如果不适用，应在报告中保留 CDF 字段、版本和排除理由。

执行前确认 DUT LA 和测试端 LA（默认 5/0），并确保测试端不与真实设备占用同一逻辑地址。测试人员先按 CDF 勾选适用项目，再执行选中项目。自动项会使用当前 COM 端口和同一个 libCEC 原生连接；如果尚未建立会话，CTS 执行器会临时打开 monitor-only 连接，完成后自动关闭。报告默认导出 HTML，也可选择 JSON。报告会保留执行方式、CDF 决策和能力边界，不能替代 HDMI-ATC/CEC-CTT 的最终认证结论。
