CEC Debug Tools — Windows x64 便携版

适用：Windows 10/11 x64。不需要安装 Python、libCEC 软件或 C++ 开发工具。

使用方式
1. 先将 ZIP 完整解压到可写目录，例如“文档\CEC-Debug-Tools”。不要在 ZIP 内运行。
2. 双击 CEC-Debug-Tools.exe。
3. 接上 Pulse-Eight USB-CEC Adapter，确认设备管理器中出现 COM 端口。
4. 点击“列出适配器”，选择对应 COM 口，然后“连接设备”。

硬件驱动说明
便携版包含应用运行库，但不自动安装或改动 USB 驱动。新电脑若没有出现 COM 端口，
仍需安装 Pulse-Eight 官方 USB-CEC 驱动；安装驱动可能需要管理员权限。
请通过组织允许的软件渠道安装驱动，不需要关闭安全软件。

目录说明
CEC-Debug-Tools.exe：主程序。
cec.dll：本次自编译的 x64 libCEC，包含原始帧/Poll ACK、地址占用及线程死锁修复。
oui.csv：IEEE OUI 厂商表，可用新表替换，或通过工具菜单选择其他表。
_internal：Python/Tcl/Tk 等运行库，必须保留，不要只复制 EXE。
build-info.json：源码版本、原始 DLL 名称和校验信息。
SHA256SUMS.txt：包内文件 SHA-256。
licenses：第三方许可证。
source：本包对应的工具源码及 libCEC 源码归档。

参数配置保存在当前用户的 %APPDATA%\PulseEight\CECClientGUI\settings.json。
快捷命令分组和搜索预设也保存在 settings.json；书签及注释保存在同目录 bookmarks.json。
启动默认进入“报文监视”；主页面为报文监视、发包与模拟、设备与拓扑、测试与报告。
“报文监视”中可展开快捷命令、组合查找、书签注释、Opcode 关注、日志工具；再次点击当前按钮收起。
手动发包、角色自动应答和逻辑地址占用位于“发包与模拟”；快捷命令与手动发包共用地址/Opcode/参数。
“设备与拓扑”显示连接配置与总线设备；“连接设置”集中保留 DLL、身份、链路、EEPROM、监听及日志参数。
“工具”菜单打开原生日志、API控制台、统计、编译信息、接口帮助、Bootloader和OUI表；诊断窗口关闭时隐藏并保留日志。
“测试与报告”集中 CTS、报告和统一脚本编辑器；兼容 API 命令、wait/@wait、expect、expect-opcode、assert。
AUTO 遇到场景检查或 wait 时采用80ms节奏；仅有@wait或.cmd扩展名时采用旧命令间隔。
无格式标记的纯命令 .cec/.txt 默认按场景节奏；旧文件可选择 LEGACY 并设置原来的毫秒间隔。保存不改写脚本语法。
Opcode 关注支持12项、16种预设颜色及系统颜色选择器自选颜色；仅为Opcode单元格着色。
相同Opcode按编号最小的关注项生效；配置自动保存，原有四项关注自动迁移。
双击消息查看完整详情；支持长参数换行、滚动、选择复制及复制全部详情，抓包可继续。
右键“载入 Raw 注入”填入源/目标LA、Opcode、参数及完整报文，等待手动发送。
“日志工具”提供长度检查、上/下一个异常、仅非法数据块、离线打开及批量提取。
红色 ! 表示长度或格式异常；未知规则不判错，长度检查不代替CTS或改变ACK状态。
离线支持本工具CSV/TSV/JSON、RX/TX报文日志和逐行十六进制文本；原文件只读。
离线查看时实时抓包继续记录，点击“返回实时”恢复；离线注释另存offline-bookmarks目录。
Ctrl/Shift多选、Ctrl+A选择表中各行；可导出选中行、全部匹配或会话全部，不受2000行显示上限限制。
可勾选CSV/TSV/JSON字段，或提取完整报文/Opcode名称；格式化、写入在后台完成。
F5–F12发送已分配的快捷命令；Ctrl+F打开查找，F3/Shift+F3前后定位；F2/Shift+F2跳转标记。
快捷键仅在监视页生效；双击快捷命令发送，右键报文添加书签和注释。
参数掩码支持 ??（任意字节）和 A?（任意低半字节）；查询覆盖本会话全部记录。
查找条件自动应用，默认保留完整列表与上下文；上一/下一命中仅跳转，工具栏显示条件摘要及命中位置。
全文搜索位于“上一命中 / 下一命中”左侧，可搜索报文、解释、注释和标签，并与组合查找条件叠加。
条件条的 × 可单独移除条件；“清除查找”清空查找条件。错误条件可直接移除，无条件时收起条件区。
选中消息时显示命中原因，包含匹配字段和内容片段；完整原因可在报文详情中查看。
“列布局”或右键表头可选择精简/完整布局、勾选显示列，或自定义列顺序与宽度。
列布局自动保存至 settings.json 的 workbench.capture_layout，重启后恢复；隐藏列不删除原始字段。
手动列宽保持固定；切换布局预设或“重置列宽”恢复自动适配，重置列宽保留显示列和顺序。
勾选“仅显示匹配”才筛选列表；无条件或条件有误时禁用导航。右键“查找相同 Opcode”可快速定位同类消息。
定位后保持浏览位置，新报文继续记录；“返回最新”或手动滚到底部恢复跟随。
清空抓包会开始新会话，历史标注保留在“全部会话”；CSV/JSON导出包含标注字段。
界面异常写入程序所在目录的 outputs\cec-gui-errors.log。
本包不包含打包电脑的 COM 配置、个人设置或抓包记录。
解压和移动后会从 EXE 所在目录查找 cec.dll 和 oui.csv，不依赖工作目录。

离线自检（不占用串口、不发送总线报文）
CEC-Debug-Tools.exe --self-test-report 自检报告.json
自检验证界面/抓包组件、OUI 表和原生 DLL 加载；不代表真实硬件 CTS 认证通过。

第三方软件与源码
libCEC 许可证见 licenses/libcec-LICENSE.md；对应原生源码见 source/libcec-source.tar.gz。
GUI 源码及构建脚本在 source/cec-debug-tools 中。上游与本项目地址：
https://github.com/blinkjum/cec-debug-tools
https://github.com/blinkjum/libcec
https://github.com/Pulse-Eight/libcec
Python、Tcl/Tk、PyInstaller 的许可证亦随包提供。EXE 未做商业代码签名。
