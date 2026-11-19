#!/usr/bin/env python3
"""A Windows GUI front-end for the Pulse-Eight libCEC C API.

The GUI talks to ``cec.dll`` directly.  ``cec-client.exe`` is not used as a
child process, so raw frames, callbacks, statistics, topology queries and
role sessions all share one native libCEC connection.
"""

from __future__ import annotations

import json
import html
import os
import queue
import re
import threading
import time
import csv
import bisect
import traceback
import sys
from uuid import uuid4
from datetime import datetime
from pathlib import Path
from tkinter import END, BOTH, LEFT, RIGHT, TOP, X, Y, W, E, N, S
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
import tkinter.font as tkfont

from libcec_backend import BackendResult, LibCecBackend, LibCecError, OPCODE_NAMES
from vendor_oui import OuiLookup
from cts_testcases import CASE_BY_ID, CASES, AUTO, CONDITIONAL, EXTERNAL, MANUAL, CtsTestCase
from runtime_paths import application_directory
from cec_workbench_ui import CaptureWorkbenchMixin, LA_CHOICES
from cec_workbench import HIGHLIGHT_PALETTE, HIGHLIGHT_RULE_COUNT
from capture_files_ui import CaptureFilesMixin
from cec_capture_tools import write_export
from capture_ai_export import write_ai_package
from capture_layout import CAPTURE_COLUMNS, CAPTURE_HEADINGS, MINIMUM_WIDTHS, PREFERRED_WIDTHS
from capture_layout_ui import CaptureLayoutMixin
from workspace_ui import WorkspaceUIMixin
from cec_scripts import ScriptPlan, compile_script


APP_NAME = "CEC Client GUI"
BUILT_CEC = next((path for path in LibCecBackend.bundled_dlls() if path.is_file()),
                 application_directory() / "libcec-custom-install" / "bin" / "cec.dll")
DEFAULT_CEC = BUILT_CEC if BUILT_CEC.is_file() else Path(r"C:\Program Files\Pulse-Eight\USB-CEC Adapter\cec.dll")


COMMANDS = [
    ("tx", "bytes", "发送原始 CEC 帧并等待 ACK"),
    ("txn", "bytes", "发送原始 CEC 帧但不等待 ACK"),
    ("on", "address", "打开指定设备"),
    ("standby", "address", "让指定设备进入待机"),
    ("la", "logical address", "修改适配器逻辑地址"),
    ("p", "device port", "修改适配器使用的 HDMI 端口"),
    ("pa", "physical address", "修改适配器物理地址"),
    ("as", "", "将适配器设为 Active Source"),
    ("is", "", "将适配器标记为非 Active Source"),
    ("ea", "", "启用 System Audio Mode"),
    ("da", "", "禁用 System Audio Mode"),
    ("gas", "", "查询 Audio Status"),
    ("gsam", "", "查询 System Audio Mode 状态"),
    ("osd", "addr string", "向指定设备发送 OSD 字符串"),
    ("ver", "addr", "查询 CEC 版本"),
    ("ven", "addr", "查询 Vendor ID"),
    ("lang", "addr", "查询菜单语言"),
    ("pow", "addr", "查询电源状态"),
    ("name", "addr", "查询 OSD 名称"),
    ("poll", "addr", "轮询指定逻辑地址"),
    ("lad", "", "列出总线上的 Active Device"),
    ("ad", "addr", "检查指定设备是否为 Active Source"),
    ("at", "type", "检查指定设备类型是否 Active"),
    ("sp", "physical address", "设置 Stream Path"),
    ("spl", "logical address", "设置指定逻辑地址为 Active"),
    ("volup", "", "向 Audio System 发送音量加"),
    ("voldown", "", "向 Audio System 发送音量减"),
    ("mute", "", "切换 Audio System 静音"),
    ("play", "addr mode", "向指定设备发送 Play 命令"),
    ("self", "", "列出 libCEC 当前控制的地址"),
    ("scan", "", "扫描 CEC 总线并显示设备信息"),
    ("mon", "1|0", "开启或关闭 CEC 总线监视"),
    ("am", "1|0", "关闭/开启 Autonomous Mode；不带参数时查询"),
    ("log", "1-31", "修改运行时日志级别"),
    ("ping", "", "Ping CEC 适配器"),
    ("bl", "", "让适配器进入 Bootloader；谨慎使用"),
    ("r", "", "重新连接适配器"),
    ("h", "", "显示 libCEC API 命令帮助"),
    ("q", "", "关闭当前 libCEC 会话"),
]


# Common CEC messages used by the Raw CEC and role-simulation workflows.
# The free-form opcode entry remains available for vendor-specific and
# newly-defined messages; this catalogue only supplies human-readable help.
OPCODE_GUIDE: dict[int, tuple[str, str, str, str]] = {
    0x00: ("Feature Abort", "原始 Opcode + Abort Reason，各 1 字节", "目标设备 -> 原发起方", "表示目标不支持请求，或请求参数无效。"),
    0x04: ("Image View On", "无", "Source -> TV", "请求电视退出待机并显示图像。"),
    0x09: ("Record On", "录制源/录制参数", "Source -> Recorder", "请求录像设备开始录制；参数格式取决于录制源类型。"),
    0x0A: ("Record Status", "录制状态", "Recorder -> Source", "返回录像请求的接受、拒绝或当前录制状态。"),
    0x0B: ("Record Off", "无", "Source -> Recorder", "请求录像设备停止录制。"),
    0x0D: ("Text View On", "无", "Source -> TV", "请求电视退出待机并显示文本视图。"),
    0x32: ("Set Menu Language", "3 字节 ASCII 语言码，例如 65:6E:67", "通常为广播", "设置菜单语言；通常由电视广播语言状态。"),
    0x36: ("Standby", "无", "Source -> 目标/广播", "请求设备进入待机。发送前请确认目标，可能影响真实设备。"),
    0x41: ("Play", "播放模式 1 字节", "Source -> Playback", "控制播放设备开始播放、快进、倒放或跳转等。"),
    0x42: ("Deck Control", "Deck Control Mode 1 字节", "Source -> Recorder/Playback", "控制磁带/录像机类设备的 deck 状态。"),
    0x43: ("Timer Programming", "定时器信息", "Source -> Recorder", "设置或修改录像定时器；参数是结构化定时器字段。"),
    0x44: ("User Control Pressed", "按键码 1 字节", "Source -> 目标", "模拟遥控器按键按下，例如音量、方向键或电源键。"),
    0x45: ("User Control Released", "无", "Source -> 目标", "表示上一条 User Control Pressed 的按键释放。"),
    0x46: ("Give OSD Name", "无", "Source -> 目标", "请求设备返回 OSD 名称，通常响应 0x47。"),
    0x47: ("Set OSD Name", "OSD 名称文本，最多 14 字节", "目标 -> 请求方/设备状态", "返回或设置设备在 CEC 网络中的显示名称。"),
    0x64: ("Set OSD String", "显示方式 1 字节 + 文本", "Source -> TV", "请求电视显示临时 OSD 字符串。"),
    0x67: ("Set Timer Program Title", "标题文本", "Source -> Recorder", "设置当前定时录像的节目标题。"),
    0x70: ("System Audio Mode Request", "物理地址 2 字节，例如 10:00", "Source -> Audio System", "请求 Audio System 切换到指定音频路径并启用系统音频模式。"),
    0x71: ("Give Audio Status", "无", "Source -> Audio System", "请求 Audio System 返回音量和静音状态，通常响应 0x7A。"),
    0x72: ("Set System Audio Mode", "模式 1 字节：00=Off，01=On", "Source -> Audio System", "设置 System Audio Mode 开关。"),
    0x7A: ("Report Audio Status", "音频状态 1 字节：音量低 7 位，bit7=静音", "Audio System -> Source", "报告当前音量和静音状态。"),
    0x7D: ("Give System Audio Mode Status", "无", "Source -> Audio System", "请求 Audio System 返回系统音频模式，通常响应 0x7E。"),
    0x7E: ("System Audio Mode Status", "模式 1 字节：00=Off，01=On", "Audio System -> Source", "报告 System Audio Mode 当前状态。"),
    0x80: ("Routing Change", "旧物理地址 2 字节 + 新物理地址 2 字节", "设备 -> 广播", "通知 HDMI 路由从旧路径切换到新路径。"),
    0x81: ("Routing Information", "当前物理地址 2 字节", "设备 -> 广播", "广播当前活动源的 HDMI 物理路径。"),
    0x82: ("Active Source", "物理地址 2 字节", "设备 -> 广播", "声明本设备为当前活动源；常见参数如 10:00。"),
    0x83: ("Give Physical Address", "无", "Source -> 目标/广播", "请求设备返回物理地址和设备类型，通常响应 0x84。"),
    0x84: ("Report Physical Address", "物理地址 2 字节 + 设备类型 1 字节", "设备 -> 广播", "报告 HDMI 物理地址及角色类型。"),
    0x85: ("Request Active Source", "无", "Source -> 广播", "请求当前活动源重新广播 0x82。"),
    0x86: ("Set Stream Path", "物理地址 2 字节", "Source -> 广播", "请求 HDMI 网络切换到指定物理路径。"),
    0x87: ("Device Vendor ID", "Vendor ID 3 字节", "设备 -> 广播", "报告设备厂商 ID；常见 Sony 为 08:00:46。"),
    0x89: ("Vendor Remote Button Down", "厂商自定义按键数据", "Source -> 目标", "发送厂商自定义遥控按键按下事件。"),
    0x8A: ("Vendor Remote Button Up", "厂商自定义按键数据", "Source -> 目标", "发送厂商自定义遥控按键释放事件。"),
    0x8C: ("Give Device Vendor ID", "无", "Source -> 目标/广播", "请求设备返回 Vendor ID，通常响应 0x87。"),
    0x8D: ("Menu Request", "菜单请求类型 1 字节", "Source -> 目标", "请求设备显示、隐藏或查询菜单状态。"),
    0x8E: ("Menu Status", "菜单状态 1 字节", "目标 -> Source", "报告菜单当前打开、关闭或查询状态。"),
    0x8F: ("Give Device Power Status", "无", "Source -> 目标", "请求设备返回电源状态，通常响应 0x90。"),
    0x90: ("Report Power Status", "状态 1 字节：00=On，01=Standby，02/03=过渡", "目标 -> Source", "报告设备当前电源状态。"),
    0x91: ("Get Menu Language", "无", "Source -> TV", "请求电视广播当前菜单语言，通常响应 0x32。"),
    0x92: ("Clear Analog Timer", "模拟定时器标识", "Source -> Recorder", "清除模拟广播定时器。"),
    0x93: ("Set Analog Timer", "模拟定时器信息", "Source -> Recorder", "设置模拟广播定时器。"),
    0x94: ("Timer Status", "定时器状态", "Recorder -> Source", "返回录像定时器编程结果。"),
    0x95: ("Timer Cleared Status", "定时器清除状态", "Recorder -> Source", "返回定时器清除操作结果。"),
    0x9D: ("Inactive Source", "物理地址 2 字节", "Source -> TV", "声明当前源不再活动，并给出其物理地址。"),
    0x9E: ("CEC Version", "版本 1 字节", "设备 -> Source", "报告 CEC 版本，例如 05 表示 CEC 1.4。"),
    0x9F: ("Get CEC Version", "无", "Source -> 目标", "请求设备返回 CEC 版本，通常响应 0x9E。"),
    0xA0: ("Vendor Command With ID", "Vendor ID 3 字节 + 厂商数据", "Source -> 目标/广播", "发送带厂商 ID 的厂商自定义命令。"),
    0xA1: ("Clear External Timer", "外部定时器标识", "Source -> Recorder", "清除外部输入源定时器。"),
    0xA2: ("Set External Timer", "外部定时器信息", "Source -> Recorder", "设置外部输入源定时器。"),
    0xA3: ("Report Short Audio Descriptors", "一个或多个 3 字节 SAD", "Audio System -> Source", "报告音频系统支持的短音频描述符。"),
    0xA4: ("Request Short Audio Descriptors", "音频格式码列表", "Source -> Audio System", "请求 Audio System 返回指定音频格式能力。"),
    0xC0: ("Initiate ARC", "无", "Audio System -> TV", "请求启动 ARC 音频回传链路。"),
    0xC1: ("Report ARC Initiated", "无", "TV -> Audio System", "确认 ARC 已启动。"),
    0xC2: ("Report ARC Terminated", "无", "TV -> Audio System", "报告 ARC 已终止。"),
    0xC3: ("Request ARC Initiation", "无", "TV -> Audio System", "请求 Audio System 启动 ARC。"),
    0xC4: ("Request ARC Termination", "无", "TV -> Audio System", "请求 Audio System 终止 ARC。"),
    0xC5: ("Terminate ARC", "无", "Audio System -> TV", "终止 ARC 音频回传链路。"),
    0xF8: ("CDC Message", "CDC 厂商/扩展数据", "设备间", "CEC 通信块中的厂商或扩展协议消息；参数依赖 CDC 类型。"),
}

OPCODE_CATALOG_OPTIONS = [(f"{opcode:02X}", guide[0]) for opcode, guide in sorted(OPCODE_GUIDE.items())]


# CEC User Control Code values carried as the single operand of
# User Control Pressed (0x44).  Keep this table in the GUI instead of using a
# small hand-written subset so that a captured frame is decoded consistently
# with libCEC's cec_user_control_code enum.  The raw value is still displayed
# by the decoder; these labels are only an interpretation of that byte.
USER_CONTROL_CODE_NAMES: dict[int, str] = {
    0x00: "Select（选择）",
    0x01: "Up（上）",
    0x02: "Down（下）",
    0x03: "Left（左）",
    0x04: "Right（右）",
    0x05: "Right-Up（右上）",
    0x06: "Right-Down（右下）",
    0x07: "Left-Up（左上）",
    0x08: "Left-Down（左下）",
    0x09: "Root Menu（根菜单）",
    0x0A: "Setup Menu（设置菜单）",
    0x0B: "Contents Menu（内容菜单）",
    0x0C: "Favorite Menu（收藏夹菜单）",
    0x0D: "Exit（退出）",
    0x10: "Top Menu（顶层菜单）",
    0x11: "DVD Menu（DVD 菜单）",
    0x1D: "Number Entry Mode（数字输入模式）",
    0x1E: "Number 11（数字 11）",
    0x1F: "Number 12（数字 12）",
    **{0x20 + number: f"Number {number}（数字 {number}）" for number in range(10)},
    0x2A: "Dot（点号）",
    0x2B: "Enter（确认/回车）",
    0x2C: "Clear（清除）",
    0x2F: "Next Favorite（下一个收藏）",
    0x30: "Channel Up（频道加）",
    0x31: "Channel Down（频道减）",
    0x32: "Previous Channel（上一个频道）",
    0x33: "Sound Select（声音选择）",
    0x34: "Input Select（输入选择）",
    0x35: "Display Information（显示信息）",
    0x36: "Help（帮助）",
    0x37: "Page Up（上一页）",
    0x38: "Page Down（下一页）",
    0x40: "Power（电源）",
    0x41: "Volume Up（音量加）",
    0x42: "Volume Down（音量减）",
    0x43: "Mute（静音）",
    0x44: "Play（播放）",
    0x45: "Stop（停止）",
    0x46: "Pause（暂停）",
    0x47: "Record（录制）",
    0x48: "Rewind（倒回）",
    0x49: "Fast Forward（快进）",
    0x4A: "Eject（弹出）",
    0x4B: "Forward（前进）",
    0x4C: "Backward（后退）",
    0x4D: "Stop Record（停止录制）",
    0x4E: "Pause Record（暂停录制）",
    0x50: "Angle（角度）",
    0x51: "Sub-picture（子画面）",
    0x52: "Video on Demand（视频点播）",
    0x53: "Electronic Program Guide（电子节目指南）",
    0x54: "Timer Programming（定时器编程）",
    0x55: "Initial Configuration（初始配置）",
    0x56: "Select Broadcast Type（选择广播类型）",
    0x57: "Select Sound Presentation（选择声音呈现方式）",
    0x60: "Play Function（播放功能）",
    0x61: "Pause Play Function（暂停播放功能）",
    0x62: "Record Function（录制功能）",
    0x63: "Pause Record Function（暂停录制功能）",
    0x64: "Stop Function（停止功能）",
    0x65: "Mute Function（静音功能）",
    0x66: "Restore Volume Function（恢复音量功能）",
    0x67: "Tune Function（调谐功能）",
    0x68: "Select Media Function（选择媒体功能）",
    0x69: "Select AV Input Function（选择 AV 输入功能）",
    0x6A: "Select Audio Input Function（选择音频输入功能）",
    0x6B: "Power Toggle Function（电源切换功能）",
    0x6C: "Power Off Function（关机功能）",
    0x6D: "Power On Function（开机功能）",
    0x71: "F1 Blue（F1 蓝色）",
    0x72: "F2 Red（F2 红色）",
    0x73: "F3 Green（F3 绿色）",
    0x74: "F4 Yellow（F4 黄色）",
    0x75: "F5",
    0x76: "Data（数据）",
    0x91: "Return（返回，Samsung 扩展）",
    0x96: "Channels List（频道列表，Samsung 扩展）",
}

# Values marked reserved by libCEC's cec_user_control_code definition.  They
# are different from an implementation that simply has not recognized a
# value, so report them separately in the monitor.
USER_CONTROL_CODE_RESERVED = frozenset(
    {
        *range(0x0E, 0x10),
        *range(0x12, 0x1D),
        *range(0x2D, 0x2F),
        *range(0x39, 0x40),
        0x4F,
        *range(0x58, 0x60),
        *range(0x6E, 0x71),
        *range(0x77, 0x100),
    }
) - USER_CONTROL_CODE_NAMES.keys()


def _log_level_options() -> list[tuple[str, str]]:
    bit_names = ((1, "Error"), (2, "Warning"), (4, "Notice"), (8, "Traffic"), (16, "Debug"))
    options: list[tuple[str, str]] = []
    for level in range(32):
        if level == 0:
            description = "关闭日志"
        elif level == 31:
            description = "全部日志级别"
        else:
            description = "+".join(name for bit, name in bit_names if level & bit)
        options.append((str(level), f"{description}（0x{level:02X} 位掩码）"))
    return options


LOG_LEVEL_OPTIONS = _log_level_options()
BOOL_MODE_OPTIONS = [
    ("default", "使用 libCEC 默认值"),
    ("0", "禁用"),
    ("1", "启用"),
]
ROLE_OPTIONS = [
    ("x", "TV"),
    ("a", "Audio System"),
    ("p", "Playback"),
    ("r", "Recorder"),
    ("t", "Tuner"),
]
POWER_OPTIONS = [
    ("0", "On"),
    ("1", "Standby"),
    ("2", "Transition → On"),
    ("3", "Transition → Standby"),
]
CAPTURE_DIRECTION_OPTIONS = [
    ("ALL", "全部报文"),
    ("TX", "发送方向"),
    ("RX", "接收方向"),
]
CTS_MODE_OPTIONS = [
    ("全部", "全部执行方式"),
    (AUTO, "可由 libCEC 自动执行"),
    (MANUAL, "需要测试人员人工操作"),
    (EXTERNAL, "需要外部 CTS 设备"),
    (CONDITIONAL, "根据 CDF 条件决定"),
]
CDF_DECISION_OPTIONS = [
    ("—", "当前项目无 CDF 决策"),
    ("待决定", "等待测试人员依据 CDF 决定"),
    ("适用", "按当前 CDF 适用"),
    ("不适用", "按当前 CDF 不适用"),
]


class RawValueCombobox(ttk.Combobox):
    """A native ttk combobox that keeps the raw value separate from its label.

    The dropdown is deliberately owned by ``ttk::combobox``.  Earlier
    versions used a detached ``Toplevel``/``Listbox`` popup so that the
    explanations could be wider, but that popup could remain behind or out
    of sync when the main window moved.  Native Tk popdowns follow the parent
    window and close on focus/click changes as expected on Windows.

    ``raw_variable`` continues to contain only the protocol value (for
    example ``A3`` or ``RX``), while the visible combobox shows
    ``raw — explanation``.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        raw_variable: tk.StringVar,
        options: list[tuple[str, str]] | tuple[tuple[str, str], ...],
        **kwargs: object,
    ) -> None:
        self.raw_variable = raw_variable
        self._raw_options = tuple((str(raw), str(description)) for raw, description in options)
        self._raw_to_display = {
            raw: description if not raw else f"{raw} — {description}"
            for raw, description in self._raw_options
        }
        self._display_to_raw = {
            display: raw for raw, display in self._raw_to_display.items()
        }
        self._display_variable = tk.StringVar(master=master)
        entry_width = int(kwargs.pop("width", 10))
        display_width_limit = int(kwargs.pop("display_width_limit", 72))
        display_width = max(
            (len(display) for display in self._display_to_raw),
            default=entry_width,
        )
        # Native popdowns use the combobox width.  Make it wide enough for
        # the catalogue labels so the raw value and explanation are visible
        # without reintroducing a detached, manually-positioned popup.
        native_width = max(entry_width, min(display_width_limit, display_width + 1))
        kwargs["textvariable"] = self._display_variable
        kwargs["values"] = tuple(self._display_to_raw)
        kwargs["width"] = native_width
        super().__init__(master, **kwargs)

        self._raw_trace_id = self.raw_variable.trace_add("write", self._sync_display_from_raw)
        self.bind("<<ComboboxSelected>>", self._sync_raw_from_display, add="+")
        self.bind("<KeyRelease>", self._sync_raw_from_display, add="+")
        self.bind("<FocusOut>", self._sync_raw_from_display, add="+")
        self._sync_display_from_raw()

    def get(self) -> str:
        return self.raw_variable.get()

    def set(self, value: str) -> None:
        self.raw_variable.set(str(value))
        self._sync_display_from_raw()

    def _sync_display_from_raw(self, *_args) -> None:
        raw = self.raw_variable.get()
        display = self._raw_to_display.get(raw, raw)
        if self._display_variable.get() != display:
            self._display_variable.set(display)

    def _sync_raw_from_display(self, _event=None) -> None:
        display = self._display_variable.get()
        raw = self._display_to_raw.get(display, display)
        if self.raw_variable.get() != raw:
            self.raw_variable.set(raw)

    def destroy(self) -> None:
        try:
            self.raw_variable.trace_remove("write", self._raw_trace_id)
        except (tk.TclError, AttributeError):
            pass
        super().destroy()


class CaptureTable(ttk.Frame):
    """A small Treeview-compatible table with cell-level background colors.

    ttk.Treeview tags apply to complete items, not individual cells.  The
    capture page needs Source-based row colors and Opcode-only alert colors,
    so this widget draws the header and body on synchronized canvases while
    exposing the small subset of the Treeview API used by the GUI.
    """

    def __init__(self, master: tk.Misc, *, columns: tuple[str, ...]) -> None:
        super().__init__(master)
        self.columns = tuple(columns)
        self.display_columns = self.columns
        self.auto_fit_columns = True
        self._headings: dict[str, str] = {column: column for column in self.columns}
        self._widths: dict[str, int] = {column: 100 for column in self.columns}
        self._items: dict[str, dict[str, object]] = {}
        self._row_heights: dict[str, int] = {}
        self._order: list[str] = []
        self._row_offsets: list[int] = [0]
        self._render_job: str | None = None
        self._layout_job: str | None = None
        self._preferred_widths: dict[str, int] = {}
        self._minimum_widths: dict[str, int] = {}
        self._column_drag: tuple[str, float, int] | None = None
        self._wrap_cache: dict[tuple[str, int], str] = {}
        self._tags: dict[str, dict[str, object]] = {}
        self._selection: tuple[str, ...] = ()
        self._focus_item: str | None = None
        self._next_item = 1
        self._batch_depth = 0
        self._header_height = 26
        self._base_row_height = 24
        self._wrap_columns = {"frame", "theoretical_duration_ms", "callback_interval_ms", "estimated_gap_ms",
                              "opcode", "opcode_explanation", "params", "params_explanation", "status"}
        self._font = tkfont.nametofont("TkDefaultFont")
        self._header_font = self._font.copy()
        self._header_font.configure(weight="bold")
        self._yscrollcommand = None
        self._xscrollcommand = None

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.header_canvas = tk.Canvas(self, height=self._header_height, highlightthickness=0, background="#F2F2F2")
        self.body_canvas = tk.Canvas(self, highlightthickness=0, background="white")
        self.header_canvas.grid(row=0, column=0, sticky="ew")
        self.body_canvas.grid(row=1, column=0, sticky="nsew")
        self.body_canvas.configure(yscrollcommand=self._on_yscroll)
        self.body_canvas.bind("<Button-1>", self._on_left_click)
        self.body_canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.body_canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)
        self.body_canvas.bind("<Button-4>", lambda _event: self._scroll_rows(-1))
        self.body_canvas.bind("<Button-5>", lambda _event: self._scroll_rows(1))
        self.header_canvas.bind("<Shift-MouseWheel>", self._on_shift_mousewheel)
        self.header_canvas.bind("<Motion>", self._on_header_motion)
        self.header_canvas.bind("<ButtonPress-1>", self._on_header_press)
        self.header_canvas.bind("<B1-Motion>", self._on_header_drag)
        self.header_canvas.bind("<ButtonRelease-1>", self._on_header_release)
        self.header_canvas.bind("<Double-1>", self._on_header_autofit)
        self.body_canvas.bind("<Configure>", self._on_body_configure)

    def configure(self, cnf=None, **kwargs):  # type: ignore[no-untyped-def]
        yscrollcommand = kwargs.pop("yscrollcommand", None)
        xscrollcommand = kwargs.pop("xscrollcommand", None)
        if yscrollcommand is not None:
            self._yscrollcommand = yscrollcommand
            self.body_canvas.configure(yscrollcommand=self._on_yscroll)
        if xscrollcommand is not None:
            self._xscrollcommand = xscrollcommand
            self.body_canvas.configure( xscrollcommand=self._on_xscroll )
        return super().configure(cnf, **kwargs)

    config = configure

    def _on_yscroll(self, first: str, last: str) -> None:
        if self._yscrollcommand is not None:
            self._yscrollcommand(first, last)
        self._request_render()

    def _on_xscroll(self, first: str, last: str) -> None:
        self.header_canvas.xview_moveto(first)
        if self._xscrollcommand is not None:
            self._xscrollcommand(first, last)

    def xview(self, *args: object) -> tuple[float, float] | None:
        result = self.body_canvas.xview(*args)
        first = self.body_canvas.xview()[0]
        self.header_canvas.xview_moveto(first)
        return result

    def yview(self, *args: object) -> tuple[float, float] | None:
        result = self.body_canvas.yview(*args)
        if args:
            self._request_render()
        return result

    def is_at_bottom(self) -> bool:
        _first, last = self.body_canvas.yview()
        return last >= 0.999

    def set_column_layout(self, preferred: dict[str, int], minimum: dict[str, int], *, auto_fit: bool = True) -> None:
        """Fit only on window resize/user request, never on incoming traffic."""
        self.auto_fit_columns = auto_fit
        self._minimum_widths = {
            column: max(minimum[column], self._header_font.measure(self._headings[column]) + 12)
            for column in self.columns
        }
        self._preferred_widths = {
            column: max(preferred[column], self._minimum_widths[column]) for column in self.columns
        }
        self._apply_column_widths(self._preferred_widths)
        self.fit_columns()

    def set_display_columns(self, columns) -> None:
        columns = tuple(dict.fromkeys(c for c in columns if c in self.columns))
        if not columns:
            raise ValueError("至少显示一列")
        if columns == self.display_columns:
            return
        follow = self.is_at_bottom()
        top = max(0, self.body_canvas.canvasy(0))
        anchor = self.identify_row(0)
        within = top - self._row_offsets[self._order.index(anchor)] if anchor else 0
        self.display_columns = columns
        for item in self._order:
            self._row_heights[item] = self._measure_row_height(item)
        self._redraw()
        self.fit_columns()
        self.xview("moveto", 0)
        if follow:
            self.see_end()
        elif anchor:
            offset = self._row_offsets[self._order.index(anchor)] + min(within, self._row_heights[anchor]-1)
            self.yview("moveto", offset / max(1, self._row_offsets[-1]))

    def _on_body_configure(self, _event=None) -> None:
        self._request_render()
        if self._preferred_widths:
            if self._layout_job is not None:
                self.after_cancel(self._layout_job)
            self._layout_job = self.after(120, self.fit_columns)

    def fit_columns(self, available: int | None = None) -> None:
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        if not self._preferred_widths or (not self.auto_fit_columns and available is None):
            return
        if available is None:
            available = self.body_canvas.winfo_width()
        if available <= 1:
            return
        preferred = self._preferred_widths
        minimum = self._minimum_widths
        columns = self.display_columns
        minimum_total = sum(minimum[c] for c in columns)
        budget = max(minimum_total, min(available, sum(preferred[c] for c in columns)))
        extra = budget - minimum_total
        flexibility = sum(preferred[c] - minimum[c] for c in columns)
        widths = {c: minimum[c] + (extra * (preferred[c] - minimum[c]) // flexibility if flexibility else 0)
                  for c in columns}
        remaining = budget - sum(widths.values())
        for column in columns:
            if remaining and widths[column] < preferred[column]:
                widths[column] += 1
                remaining -= 1
        self._apply_column_widths(widths)

    def _apply_column_widths(self, widths: dict[str, int]) -> None:
        if all(self._widths[c] == width for c, width in widths.items()):
            return
        follow_tail = self.is_at_bottom()
        top = max(0.0, self.body_canvas.canvasy(0))
        anchor = min(len(self._order) - 1, bisect.bisect_right(self._row_offsets, top) - 1)
        within_row = top - self._row_offsets[anchor] if anchor >= 0 else 0
        remeasure = any(self._widths[c] != widths[c] for c in self._wrap_columns if c in widths)
        self._widths.update(widths)
        if remeasure:
            for item_id in self._order:
                self._row_heights[item_id] = self._measure_row_height(item_id)
        self._redraw()
        if follow_tail:
            self.see_end()
        elif anchor >= 0:
            position = self._row_offsets[anchor] + min(within_row, self._row_heights[self._order[anchor]] - 1)
            self.yview("moveto", position / max(1, self._row_offsets[-1]))
        if sum(self._widths[c] for c in self.display_columns) <= self.body_canvas.winfo_width():
            self.xview("moveto", 0)

    def _header_edge(self, x: int) -> str | None:
        position = self.header_canvas.canvasx(x)
        offset = 0
        for column in self.display_columns:
            offset += self._widths[column]
            if abs(position - offset) <= 5:
                return column
        return None

    def _on_header_motion(self, event: tk.Event) -> None:
        self.header_canvas.configure(cursor="sb_h_double_arrow" if self._header_edge(event.x) else "")

    def _on_header_press(self, event: tk.Event) -> None:
        column = self._header_edge(event.x)
        self._column_drag = ((column, self.header_canvas.canvasx(event.x), self._widths[column])
                             if column else None)

    def _on_header_drag(self, event: tk.Event) -> None:
        if self._column_drag:
            self.header_canvas.delete("resize_guide")
            x = self.header_canvas.canvasx(event.x)
            self.header_canvas.create_line(x, 0, x, self._header_height, fill="#1976D2", width=2,
                                           tags=("resize_guide",))

    def _on_header_release(self, event: tk.Event) -> None:
        self.header_canvas.delete("resize_guide")
        drag, self._column_drag = self._column_drag, None
        if drag:
            column, start, original = drag
            widths = dict(self._widths)
            widths[column] = max(self._minimum_widths.get(column, 40),
                                 int(original + self.header_canvas.canvasx(event.x) - start))
            self._preferred_widths = dict(widths)
            self.auto_fit_columns = False
            self._apply_column_widths(widths)
            self._notify_column_resize()

    def _on_header_autofit(self, event: tk.Event) -> None:
        column = self._header_edge(event.x)
        self._column_drag = None
        if not column:
            return
        index = self.columns.index(column)
        width = max((self._font.measure(str(self._items[item]["values"][index])) + 12
                     for item in self._order), default=40)
        widths = dict(self._widths)
        widths[column] = max(self._minimum_widths.get(column, 40), min(480, width))
        self._preferred_widths = dict(widths)
        self.auto_fit_columns = False
        self._apply_column_widths(widths)
        self._notify_column_resize()

    def _notify_column_resize(self) -> None:
        callback = getattr(self, "on_column_resize", None)
        if callback:
            callback()

    def see_end(self) -> None:
        self.body_canvas.yview_moveto(1.0)
        self._request_render()

    def see(self, item: str, *, center: bool = False) -> None:
        if item not in self._items:
            return
        index = self._order.index(item)
        top, bottom = self._row_offsets[index:index + 2]
        view_top = self.body_canvas.canvasy(0)
        height = max(1, self.body_canvas.winfo_height())
        if center or top < view_top or bottom > view_top + height:
            target = top - max(0, (height - (bottom - top)) // 2) if center else top - height // 3
            self.body_canvas.yview_moveto(max(0, target) / max(1, self._row_offsets[-1]))
        self._request_render()

    def heading(self, column: str, option: str | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        if "text" in kwargs:
            self._headings[column] = str(kwargs["text"])
            self._redraw()
        if option == "text":
            return self._headings.get(column, column)
        return {"text": self._headings.get(column, column)}

    def column(self, column: str, option: str | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        if "width" in kwargs:
            self._widths[column] = max(30, int(kwargs["width"]))
            if column in self._wrap_columns:
                for item_id in self._order:
                    self._row_heights[item_id] = self._measure_row_height(item_id)
            self._redraw()
        if option == "width":
            return self._widths.get(column, 100)
        return {"width": self._widths.get(column, 100)}

    def tag_configure(self, tag: str, **kwargs: object) -> None:
        self._tags.setdefault(tag, {}).update(kwargs)
        self._redraw()

    def insert(self, _parent: str, _index: object, *, values: tuple[object, ...], tags: tuple[str, ...] = ()) -> str:
        item_id = f"capture{self._next_item}"
        self._next_item += 1
        self._items[item_id] = {"values": tuple(values), "tags": tuple(tags)}
        self._row_heights[item_id] = self._measure_row_height(item_id)
        self._order.append(item_id)
        self._row_offsets.append(self._row_offsets[-1] + self._row_heights[item_id])
        if self._batch_depth == 0:
            self._update_scrollregion()
            self._request_render()
        return item_id

    def delete(self, *items: str) -> None:
        existing = set(items).intersection(self._items)
        if not existing:
            return
        for item_id in existing:
            del self._items[item_id]
            self._row_heights.pop(item_id, None)
        self._order = [item_id for item_id in self._order if item_id not in existing]
        self._rebuild_offsets()
        self._selection = tuple(item for item in self._selection if item in self._items)
        if self._focus_item not in self._items:
            self._focus_item = None
        if self._batch_depth == 0:
            self._update_scrollregion()
            self._request_render()

    def get_children(self, _item: str = "") -> tuple[str, ...]:
        return tuple(self._order)

    def item(self, item: str, option: str | None = None, **kwargs: object):  # type: ignore[no-untyped-def]
        data = self._items.get(item, {})
        if kwargs and item in self._items:
            data.update(kwargs)
            if "values" in kwargs:
                self._row_heights[item] = self._measure_row_height(item)
                self._rebuild_offsets()
                self._update_scrollregion()
            self._request_render()
        if option == "values":
            return data.get("values", ())
        if option == "tags":
            return data.get("tags", ())
        return data

    def selection(self) -> tuple[str, ...]:
        return self._selection

    def selection_set(self, item: str | tuple[str, ...]) -> None:
        selected = (item,) if isinstance(item, str) else tuple(item)
        previous = self._selection
        self._selection = tuple(value for value in selected if value in self._items)
        self._request_render()
        if self._selection != previous:
            self.body_canvas.event_generate("<<TreeviewSelect>>", when="tail")

    def focus(self, item: str | None = None):  # type: ignore[no-untyped-def]
        if item is not None:
            self._focus_item = item
            return
        return self._focus_item or ""

    def identify_row(self, y: int) -> str:
        position = self.body_canvas.canvasy(y)
        if position < 0 or position >= self._row_offsets[-1]:
            return ""
        return self._order[bisect.bisect_right(self._row_offsets, position) - 1]

    def identify_column(self, x: int) -> str:
        position = self.body_canvas.canvasx(x)
        offset = 0
        for column in self.display_columns:
            offset += self._widths[column]
            if position < offset:
                return f"#{self.columns.index(column) + 1}"
        return ""

    def begin_update(self) -> None:
        self._batch_depth += 1

    def end_update(self) -> None:
        self._batch_depth = max(0, self._batch_depth - 1)
        if self._batch_depth == 0:
            self._redraw()

    def bind(self, sequence=None, func=None, add=None):  # type: ignore[no-untyped-def]
        return self.body_canvas.bind(sequence, func, add=add)

    def __getitem__(self, key: str):  # type: ignore[no-untyped-def]
        if key == "columns":
            return self.columns
        return super().__getitem__(key)

    def _on_left_click(self, event: tk.Event) -> None:
        item = self.identify_row(event.y)
        if item:
            anchor = getattr(self, "_selection_anchor", None)
            if event.state & 0x0001 and anchor in self._items:
                first, last = sorted((self._order.index(anchor), self._order.index(item)))
                self.selection_set(tuple(self._order[first:last + 1]))
            elif event.state & 0x0004:
                selected = set(self._selection)
                selected.remove(item) if item in selected else selected.add(item)
                self.selection_set(tuple(value for value in self._order if value in selected))
                self._selection_anchor = item
            else:
                self.selection_set(item)
                self._selection_anchor = item
            self.focus(item)

    def _scroll_rows(self, units: int) -> str:
        self.body_canvas.yview_scroll(units, "units")
        callback = getattr(self, "on_user_scroll", None)
        if callback:
            callback()
        return "break"

    def _on_mousewheel(self, event: tk.Event) -> str:
        delta = int(getattr(event, "delta", 0))
        if delta:
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
            self._scroll_rows(units)
        return "break"

    def _on_shift_mousewheel(self, event: tk.Event) -> str:
        delta = int(getattr(event, "delta", 0))
        if delta:
            units = -max(1, abs(delta) // 120) if delta > 0 else max(1, abs(delta) // 120)
            self.xview("scroll", units, "units")
        return "break"

    def _redraw(self) -> None:
        if not hasattr(self, "body_canvas") or self._batch_depth:
            return
        total_width = sum(self._widths[column] for column in self.display_columns)
        self.header_canvas.delete("all")
        self.header_canvas.configure(scrollregion=(0, 0, total_width, self._header_height))
        self._rebuild_offsets()
        self._update_scrollregion()

        offset = 0
        for column in self.display_columns:
            width = self._widths[column]
            self.header_canvas.create_rectangle(offset, 0, offset + width, self._header_height, fill="#F2F2F2", outline="#D5D5D5")
            self.header_canvas.create_text(offset + 5, self._header_height / 2, text=self._headings[column], anchor="w", font=self._header_font, fill="#202020")
            offset += width

        self._request_render()

        first = self.body_canvas.xview()[0]
        self.header_canvas.xview_moveto(first)

    def _rebuild_offsets(self) -> None:
        offsets = [0]
        for item_id in self._order:
            offsets.append(offsets[-1] + self._row_heights.get(item_id, self._base_row_height))
        self._row_offsets = offsets

    def _request_render(self) -> None:
        if self._batch_depth or self._render_job is not None:
            return
        self._render_job = self.after_idle(self._render_body)

    def _render_body(self) -> None:
        self._render_job = None
        if self._batch_depth:
            return
        self.body_canvas.delete("capture_row")
        top = max(0.0, self.body_canvas.canvasy(0))
        bottom = top + max(1, self.body_canvas.winfo_height())
        # Keep a small overscan margin. Stored/history rows never become tens
        # of thousands of canvas objects, and trimming no longer moves them all.
        start = max(0, bisect.bisect_right(self._row_offsets, top) - 3)
        end = min(len(self._order), bisect.bisect_left(self._row_offsets, bottom) + 2)
        for index in range(start, end):
            self._draw_body_row(self._order[index], self._row_offsets[index])

    def destroy(self) -> None:
        if self._layout_job is not None:
            self.after_cancel(self._layout_job)
            self._layout_job = None
        if self._render_job is not None:
            self.after_cancel(self._render_job)
            self._render_job = None
        super().destroy()

    def _update_scrollregion(self) -> None:
        total_width = sum(self._widths[column] for column in self.display_columns)
        total_height = self._row_offsets[-1]
        self.body_canvas.configure(scrollregion=(0, 0, total_width, total_height))

    def _wrap_text(self, value: object, width: int) -> str:
        """Wrap text by rendered width, including CJK text without spaces."""
        text = str(value)
        if not text or width <= 1:
            return text
        key = (text, width)
        if key in self._wrap_cache:
            return self._wrap_cache[key]
        lines: list[str] = []
        paragraphs = text.splitlines() or [""]
        for paragraph in paragraphs:
            current = ""
            for character in paragraph:
                candidate = current + character
                if current and self._font.measure(candidate) > width:
                    lines.append(current)
                    current = character
                else:
                    current = candidate
            lines.append(current)
        wrapped = "\n".join(lines)
        if len(self._wrap_cache) >= 4096:
            self._wrap_cache.pop(next(iter(self._wrap_cache)))
        self._wrap_cache[key] = wrapped
        return wrapped

    def _measure_row_height(self, item_id: str) -> int:
        data = self._items.get(item_id, {})
        values = tuple(data.get("values", ()))
        max_lines = 1
        for column in self.display_columns:
            value_index = self.columns.index(column)
            if column not in self._wrap_columns or value_index >= len(values):
                continue
            wrapped = self._wrap_text(values[value_index], max(20, self._widths[column] - 10))
            max_lines = max(max_lines, wrapped.count("\n") + 1)
        line_height = int(self._font.metrics("linespace"))
        return max(self._base_row_height, max_lines * line_height + 8)

    def _draw_body_row(self, item_id: str, y0: int) -> None:
        data = self._items[item_id]
        values = tuple(data.get("values", ()))
        tags = tuple(data.get("tags", ()))
        selected = item_id in self._selection
        row_background = "#1976D2" if selected else "white"
        row_foreground = "white" if selected else "#263238"
        source_tag = next((tag for tag in tags if tag.startswith("capture_source_")), "")
        if not selected:
            row_background = str(self._tags.get(source_tag, {}).get("background", "white"))
            row_foreground = str(self._tags.get(source_tag, {}).get("foreground", "#263238"))
        highlight_tag = next(
            (tag for tag in tags if tag.startswith("capture_opcode_highlight_")),
            "",
        )
        highlighted = bool(highlight_tag) and not selected
        row_height = self._row_heights.get(item_id, self._base_row_height)
        y1 = y0 + row_height
        offset = 0
        canvas_tags = ("capture_row", f"capture_item_{item_id}")
        for column in self.display_columns:
            value_index = self.columns.index(column)
            width = self._widths[column]
            cell_background = row_background
            cell_foreground = row_foreground
            cell_outline = "#D9D9D9"
            if highlighted and column == "opcode":
                highlight_style = self._tags.get(highlight_tag, {})
                cell_background = str(highlight_style.get("background", "#FFE68A"))
                cell_foreground = str(highlight_style.get("foreground", "#202020"))
                cell_outline = str(highlight_style.get("outline", "#666666"))
            if not selected and column == "mark" and "capture_length_error" in tags:
                cell_background, cell_foreground, cell_outline = "#F8B4B4", "#8B0000", "#CC5555"
            self.body_canvas.create_rectangle(
                offset, y0, offset + width, y1,
                fill=cell_background, outline=cell_outline, tags=canvas_tags,
            )
            value = str(values[value_index]) if value_index < len(values) else ""
            if column in self._wrap_columns:
                value = self._wrap_text(value, max(20, width - 10))
                text_anchor = "nw"
                text_y = y0 + 4
            else:
                text_anchor = "w"
                text_y = y0 + row_height / 2
            self.body_canvas.create_text(
                offset + 5, text_y,
                text=value, anchor=text_anchor, font=self._font, fill=cell_foreground,
                justify="left", tags=canvas_tags,
            )
            offset += width


class AsyncLogWriter:
    """Serialize and batch log-file writes outside Tk's main thread."""

    def __init__(self, error_callback=None) -> None:  # type: ignore[no-untyped-def]
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._error_callback = error_callback
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="cec-log-writer", daemon=False)
        self._thread.start()

    def write(self, path_text: str, text: str) -> None:
        path = path_text.strip().strip('"')
        if path and text and not self._closed:
            self._queue.put(("write", (path, text)))

    def replace_json(self, path: Path | str, payload: object) -> None:
        """Queue an atomic JSON replacement, ordered with all other file writes."""
        if not self._closed:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
            self._queue.put(("replace", (str(path), text)))

    def flush(self, *, wait: bool = False, timeout: float = 2.0) -> bool:
        if self._closed:
            return True
        completed = threading.Event()
        self._queue.put(("flush", completed))
        return completed.wait(timeout) if wait else True

    def capture_export(self, path, records, kind, fields, callback) -> None:
        if not self._closed:
            self._queue.put(("capture_export", (path, records, kind, fields, callback)))
        else:
            callback("文件写入队列已关闭")

    def ai_package(self, path, records, context, operations, callback) -> None:
        if not self._closed:
            self._queue.put(("ai_package", (path, records, context, operations, callback)))
        else:
            callback("文件写入队列已关闭")

    def close(self, timeout: float = 3.0) -> None:
        if self._closed:
            return
        self._closed = True
        completed = threading.Event()
        self._queue.put(("close", completed))
        completed.wait(timeout)
        self._thread.join(timeout=max(0.0, timeout))

    def _report_error(self, path: str, exc: OSError) -> None:
        if self._error_callback is not None:
            self._error_callback(f"写入日志失败：{path}：{exc}")

    def _run(self) -> None:
        handles: dict[str, object] = {}
        failed_paths: set[str] = set()
        closing = False
        close_event: threading.Event | None = None
        while not closing:
            command, payload = self._queue.get()
            batch = [(command, payload)]
            for _ in range(499):
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break

            flush_events: list[threading.Event] = []
            for command, payload in batch:
                if command == "write":
                    path, text = payload  # type: ignore[misc]
                    try:
                        handle = handles.get(path)
                        if handle is None:
                            handle = Path(path).open("a", encoding="utf-8", buffering=64 * 1024)
                            handles[path] = handle
                        handle.write(text)  # type: ignore[attr-defined]
                        failed_paths.discard(path)
                    except OSError as exc:
                        if path not in failed_paths:
                            failed_paths.add(path)
                            self._report_error(path, exc)
                elif command == "replace":
                    path, text = payload  # type: ignore[misc]
                    target = Path(path)
                    temporary = target.with_name(target.name + f".{uuid4().hex}.tmp")
                    try:
                        old_handle = handles.pop(path, None)
                        if old_handle is not None:
                            old_handle.close()
                        target.parent.mkdir(parents=True, exist_ok=True)
                        temporary.write_text(text, encoding="utf-8")
                        os.replace(temporary, target)
                        failed_paths.discard(path)
                    except OSError as exc:
                        self._report_error(path, exc)
                    finally:
                        try:
                            temporary.unlink(missing_ok=True)
                        except OSError:
                            pass
                elif command == "capture_export":
                    path, records, kind, fields, callback = payload
                    try:
                        write_export(path, records, kind, fields)
                        callback(None)
                    except Exception as exc:
                        callback(str(exc))
                elif command == "ai_package":
                    path, records, context, operations, callback = payload
                    try:
                        write_ai_package(path, records, context, operations)
                        callback(None)
                    except Exception as exc:
                        callback(str(exc))
                elif command == "flush":
                    flush_events.append(payload)  # type: ignore[arg-type]
                elif command == "close":
                    closing = True
                    close_event = payload  # type: ignore[assignment]

            for path, handle in list(handles.items()):
                try:
                    handle.flush()  # type: ignore[attr-defined]
                except OSError as exc:
                    if path not in failed_paths:
                        failed_paths.add(path)
                        self._report_error(path, exc)
            for event in flush_events:
                event.set()

        for path, handle in list(handles.items()):
            try:
                handle.flush()  # type: ignore[attr-defined]
                handle.close()  # type: ignore[attr-defined]
            except OSError as exc:
                if path not in failed_paths:
                    self._report_error(path, exc)
        if close_event is not None:
            close_event.set()


class CECClientGUI(WorkspaceUIMixin, CaptureLayoutMixin, CaptureFilesMixin, CaptureWorkbenchMixin, tk.Tk):
    def __init__(self, *, read_settings: bool = True, start_hidden: bool = False) -> None:
        super().__init__()
        if start_hidden:
            self.withdraw()
        self._closing = False
        self._poll_after_id: str | None = None
        self.gui_error_count = 0
        self.gui_error_path = application_directory() / "outputs" / "cec-gui-errors.log"
        self.capture_last_frame_at: float | None = None
        self._capture_display_dirty = False
        self._capture_display_retry_at = 0.0
        self.title(APP_NAME)
        self.geometry("1280x820")
        self.minsize(980, 650)

        self.backend = LibCecBackend()
        self.output_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.log_writer = AsyncLogWriter(
            lambda message: self.output_queue.put(("log_writer_error", message))
        )
        self.oneshot_busy = False
        self.connection_profile = ""
        self.connection_kwargs: dict[str, object] = {}
        self.connection_role_source: int | None = None
        self.simulation_active = False
        self.simulation_frame_count = 0
        self.occupancy_busy = False
        self.occupancy_observed_mask = 0
        self.occupancy_poll_counts: dict[int, list[int]] = {}
        self.history: list[str] = []
        self.settings_path = self._settings_path()
        self.settings = self._load_settings() if read_settings else {}
        self.oui_lookup = OuiLookup()

        self._create_variables()
        self._wb_init(read_settings)
        self.oui_lookup.load_default(self.settings.get("oui_path"))
        self.oui_path_var.set(str(self.oui_lookup.path) if self.oui_lookup.path else "未加载 OUI 厂商表；可从“工具 → 选择 OUI 厂商表”加载")
        self._refresh_vendor_id_help()
        self._create_widgets()
        self._load_settings_into_widgets()
        self._bind_events()
        self._poll_after_id = self.after(80, self._poll_output)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    @staticmethod
    def _settings_path() -> Path:
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "PulseEight" / "CECClientGUI" / "settings.json"

    def _load_settings(self) -> dict:
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self) -> None:
        data = {
            "cec_path": self.cec_path_var.get(),
            "port": self.port_var.get(),
            "types": self.types_var.get(),
            "hdmi_port": self.hdmi_port_var.get(),
            "base": self.base_var.get(),
            "osd_name": self.osd_name_var.get(),
            "vendor_id": self.vendor_id_var.get(),
            "oui_path": self.oui_path_var.get(),
            "capture_highlight_opcodes": [var.get() for var in self.capture_highlight_vars],
            "capture_highlight_rules": self._wb_highlight_preferences(),
            "log_level": self.log_level_var.get(),
            "autowake": self.autowake_var.get(),
            "automode": self.automode_var.get(),
            "monitor_only": self.monitor_only_var.get(),
            "read_rom": self.read_rom_var.get(),
            "log_file": self.log_file_var.get(),
            "short_log_file": self.short_log_file_var.get(),
            "script_delay": self.script_delay_var.get(),
            "script_dialect": self.script_dialect_var.get(),
            "sim_role": self.sim_role_var.get(),
            "sim_connect": self.sim_connect_var.get(),
            "occupancy_addresses": [la for la, var in enumerate(self.occupancy_la_vars) if var.get()],
            "sim_source": self.sim_source_var.get(),
            "sim_physical": self.sim_physical_var.get(),
            "sim_osd": self.sim_osd_var.get(),
            "sim_vendor": self.sim_vendor_var.get(),
            "sim_version": self.sim_version_var.get(),
            "sim_power": self.sim_power_var.get(),
            "sim_language": self.sim_language_var.get(),
            "sim_volume": self.sim_volume_var.get(),
            "sim_mute": self.sim_mute_var.get(),
            "sim_audio_mode": self.sim_audio_mode_var.get(),
            "sim_auto_reply": self.sim_auto_reply_var.get(),
            "raw_source": self.raw_source_var.get(),
            "raw_destination": self.raw_destination_var.get(),
            "raw_opcode": self.raw_opcode_var.get(),
            "raw_params": self.raw_params_var.get(),
            "raw_frame": self.raw_frame_var.get(),
            "raw_count": self.raw_count_var.get(),
            "raw_interval": self.raw_interval_var.get(),
            "raw_auto_la": self.raw_auto_la_var.get(),
            "raw_no_ack": self.raw_no_ack_var.get(),
            "raw_monitor": self.raw_monitor_var.get(),
            "geometry": self.geometry(),
            "workbench": self._wb_preferences(),
        }
        self.settings = data
        self.log_writer.replace_json(self.settings_path, data)

    def _create_variables(self) -> None:
        self.cec_path_var = tk.StringVar(value=str(DEFAULT_CEC))
        self.port_var = tk.StringVar(value="COM5")
        self.types_var = tk.StringVar(value="x")
        self.hdmi_port_var = tk.StringVar()
        self.base_var = tk.StringVar()
        self.osd_name_var = tk.StringVar(value="CEC Tester")
        self.vendor_id_var = tk.StringVar()
        self.vendor_id_help_var = tk.StringVar(value="Vendor ID 查表：未加载 OUI 表")
        self.sim_vendor_help_var = tk.StringVar(value="Vendor ID 查表：未加载 OUI 表")
        self.oui_path_var = tk.StringVar()
        self.log_level_var = tk.StringVar(value="31")
        self.autowake_var = tk.StringVar(value="default")
        self.automode_var = tk.StringVar(value="default")
        self.monitor_only_var = tk.BooleanVar(value=False)
        self.read_rom_var = tk.BooleanVar(value=False)
        self.log_file_var = tk.StringVar()
        self.short_log_file_var = tk.StringVar()
        self.status_var = tk.StringVar(value="未连接")
        self.monitor_state_var = tk.StringVar(value="监听模式：未连接")
        self.command_args_var = tk.StringVar()
        self.console_command_var = tk.StringVar()
        self.script_delay_var = tk.StringVar(value="0.2")
        self.script_dialect_var = tk.StringVar(value="AUTO")
        self._script_path_hint = ""
        self.device_filter_var = tk.StringVar()
        self.sim_role_var = tk.StringVar(value="x")
        self.sim_connect_var = tk.BooleanVar(value=False)
        self.occupancy_connect_var = tk.BooleanVar(value=False)
        self.occupancy_la_vars = [tk.BooleanVar(value=(la == 5)) for la in range(15)]
        self.occupancy_state_var = tk.StringVar(value="地址占用：未启用")
        self.occupancy_poll_var = tk.StringVar(value="尚无观测记录")
        self.sim_source_var = tk.StringVar(value="0")
        self.sim_physical_var = tk.StringVar(value="1000")
        self.sim_osd_var = tk.StringVar(value="TestTV")
        self.sim_vendor_var = tk.StringVar(value="080046")
        self.sim_version_var = tk.StringVar(value="05")
        self.sim_power_var = tk.StringVar(value="0")
        self.sim_language_var = tk.StringVar(value="eng")
        self.sim_volume_var = tk.StringVar(value="50")
        self.sim_mute_var = tk.BooleanVar(value=False)
        self.sim_audio_mode_var = tk.BooleanVar(value=True)
        self.sim_auto_reply_var = tk.BooleanVar(value=True)
        self.raw_source_var = tk.StringVar(value="0")
        self.raw_destination_var = tk.StringVar(value="F")
        self.raw_opcode_var = tk.StringVar(value="8F")
        self.raw_opcode_help_var = tk.StringVar()
        self.raw_params_var = tk.StringVar()
        self.raw_frame_var = tk.StringVar(value="0F:8F")
        self.raw_count_var = tk.StringVar(value="1")
        self.raw_interval_var = tk.StringVar(value="100")
        self.raw_auto_la_var = tk.BooleanVar(value=True)
        self.raw_no_ack_var = tk.BooleanVar(value=False)
        self.raw_monitor_var = tk.BooleanVar(value=False)
        self.raw_send_busy = False
        self.capture_records: list[dict] = []
        # rawFrame timestamps are monotonic milliseconds from libCEC.  Keep a
        # wall-clock anchor only for human-readable display; the original
        # native timestamp is also retained in each exported record.
        self.capture_timestamp_anchor: tuple[int, float] | None = None
        self.capture_condition = threading.Condition()
        self.capture_paused_var = tk.BooleanVar(value=False)
        self.capture_show_poll_var = tk.BooleanVar(value=False)
        self.capture_direction_var = tk.StringVar(value="ALL")
        self.capture_filter_var = tk.StringVar()
        self.capture_summary_var = tk.StringVar(value="当前显示 0 条：TX 0 / RX 0")
        self.capture_refresh_state_var = tk.StringVar(value="等待原生报文回调")
        self.capture_visible_tx_count = 0
        self.capture_visible_rx_count = 0
        self.capture_display_limit = 2000
        self.capture_highlight_vars = [tk.StringVar(value="") for _ in range(HIGHLIGHT_RULE_COUNT)]
        self.capture_highlight_color_vars = [tk.StringVar(value=color) for color, _ in HIGHLIGHT_PALETTE[:HIGHLIGHT_RULE_COUNT]]
        self._capture_highlight_tags: dict[int, str] = {}
        self._wb_loading_highlights = True
        self.capture_interval_var = tk.StringVar(value="时间间隔：未选择消息")
        self.capture_interval_marks: dict[int, int] = {}
        self.capture_context_item: str | None = None
        self.capture_context_column: str | None = None
        self.capture_direction_var.trace_add("write", lambda *_args: self._wb_base_changed())
        for highlight_var in (*self.capture_highlight_vars, *self.capture_highlight_color_vars):
            highlight_var.trace_add("write", lambda *_args: self._wb_highlights_changed())
        self.topology_scan_pending = False
        self.scenario_running = False
        self.scenario_stop_event = threading.Event()
        self.scenario_status_var = tk.StringVar(value="未运行")
        self.cts_cases = list(CASES)
        self.cts_case_by_id = dict(CASE_BY_ID)
        self.cts_case_vars: dict[str, tk.BooleanVar] = {}
        self.cts_case_status: dict[str, str] = {case.case_id: case.initial_status for case in self.cts_cases}
        self.cts_results: dict[str, dict] = {}
        self.cts_selected_ids: set[str] = set()
        self.cts_case_widgets: dict[str, ttk.Checkbutton] = {}
        self.cts_cdf_decisions: dict[str, str] = {case.case_id: "待决定" for case in self.cts_cases if case.execution_mode == CONDITIONAL}
        self.cts_cdf_reasons: dict[str, str] = {case.case_id: "" for case in self.cts_cases if case.execution_mode == CONDITIONAL}
        self.cts_chapter_var = tk.StringVar(value="全部")
        self.cts_mode_var = tk.StringVar(value="全部")
        self.cts_search_var = tk.StringVar()
        self.cts_dut_la_var = tk.StringVar(value="5")
        self.cts_te_la_var = tk.StringVar(value="0")
        self.cts_cdf_decision_var = tk.StringVar(value="待决定")
        self.cts_cdf_reason_var = tk.StringVar()
        self.cts_status_var = tk.StringVar(value=f"已加载 {len(self.cts_cases)} 个 CTS 测试项目")
        self.cts_running = False
        self.cts_stop_event = threading.Event()
        self.cts_current_case_id: str | None = None
        self.cts_run_started_at = ""
        self.vendor_id_var.trace_add("write", lambda *_args: self._refresh_vendor_id_help())
        self.sim_vendor_var.trace_add("write", lambda *_args: self._refresh_vendor_id_help())

    def _load_settings_into_widgets(self) -> None:
        for name, var in (
            ("cec_path", self.cec_path_var),
            ("port", self.port_var),
            ("types", self.types_var),
            ("hdmi_port", self.hdmi_port_var),
            ("base", self.base_var),
            ("osd_name", self.osd_name_var),
            ("vendor_id", self.vendor_id_var),
            ("log_level", self.log_level_var),
            ("autowake", self.autowake_var),
            ("automode", self.automode_var),
            ("monitor_only", self.monitor_only_var),
            ("read_rom", self.read_rom_var),
            ("log_file", self.log_file_var),
            ("short_log_file", self.short_log_file_var),
            ("script_delay", self.script_delay_var),
            ("script_dialect", self.script_dialect_var),
            ("sim_role", self.sim_role_var),
            ("sim_connect", self.sim_connect_var),
            ("sim_source", self.sim_source_var),
            ("sim_physical", self.sim_physical_var),
            ("sim_osd", self.sim_osd_var),
            ("sim_vendor", self.sim_vendor_var),
            ("sim_version", self.sim_version_var),
            ("sim_power", self.sim_power_var),
            ("sim_language", self.sim_language_var),
            ("sim_volume", self.sim_volume_var),
            ("sim_mute", self.sim_mute_var),
            ("sim_audio_mode", self.sim_audio_mode_var),
            ("sim_auto_reply", self.sim_auto_reply_var),
            ("raw_source", self.raw_source_var),
            ("raw_destination", self.raw_destination_var),
            ("raw_opcode", self.raw_opcode_var),
            ("raw_params", self.raw_params_var),
            ("raw_frame", self.raw_frame_var),
            ("raw_count", self.raw_count_var),
            ("raw_interval", self.raw_interval_var),
            ("raw_auto_la", self.raw_auto_la_var),
            ("raw_no_ack", self.raw_no_ack_var),
            ("raw_monitor", self.raw_monitor_var),
        ):
            if name in self.settings:
                try:
                    var.set(self.settings[name])
                except tk.TclError:
                    pass
        saved_addresses = self.settings.get("occupancy_addresses")
        if isinstance(saved_addresses, list):
            for la, var in enumerate(self.occupancy_la_vars):
                var.set(la in saved_addresses)
        self._wb_load_highlights()
        # Prefer the locally built raw-frame-enabled libCEC. The user can
        # still select another DLL manually after the GUI starts.
        if BUILT_CEC.is_file():
            self.cec_path_var.set(str(BUILT_CEC))
        geometry = self.settings.get("geometry")
        if geometry:
            try:
                self.geometry(geometry)
                self.update_idletasks()
                match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+)([+-]\d+)", self.geometry())
                if match:
                    min_width, min_height = self.minsize()
                    screen_width = self.winfo_screenwidth()
                    screen_height = self.winfo_screenheight()
                    width = min(max(int(match.group(1)), int(min_width)), screen_width)
                    height = min(max(int(match.group(2)), int(min_height)), screen_height)
                    x = min(max(0, int(match.group(3))), max(0, screen_width - width))
                    y = min(max(0, int(match.group(4))), max(0, screen_height - height))
                    self.geometry(f"{width}x{height}+{x}+{y}")
            except tk.TclError:
                pass

    def _create_widgets(self) -> None:
        self._create_menu()

        outer = ttk.Frame(self, padding=8)
        outer.pack(fill=BOTH, expand=True)

        connection_bar = ttk.LabelFrame(outer, text="设备连接（全局）", padding=6)
        connection_bar.pack(fill=X, pady=(0, 6))
        ttk.Label(connection_bar, text="COM").pack(side=LEFT, padx=(0, 4))
        ttk.Entry(connection_bar, textvariable=self.port_var, width=10).pack(side=LEFT, padx=(0, 5))
        ttk.Button(connection_bar, text="列出适配器", command=self._list_adapters).pack(side=LEFT, padx=4)
        self.start_button = ttk.Button(connection_bar, text="连接设备", command=self._connect_device)
        self.start_button.pack(side=LEFT, padx=4)
        self.stop_button = ttk.Button(connection_bar, text="断开设备", command=self._stop_session, state="disabled")
        self.stop_button.pack(side=LEFT, padx=4)
        self.monitor_on_button = ttk.Button(
            connection_bar,
            text="被动监听 ON",
            command=lambda: self._set_monitoring(True),
            state="disabled",
        )
        self.monitor_on_button.pack(side=LEFT, padx=4)
        self.monitor_off_button = ttk.Button(
            connection_bar,
            text="被动监听 OFF",
            command=lambda: self._set_monitoring(False),
            state="disabled",
        )
        self.monitor_off_button.pack(side=LEFT, padx=4)
        ttk.Label(connection_bar, textvariable=self.monitor_state_var, foreground="#555555").pack(side=LEFT, padx=(8, 0))
        ttk.Label(
            connection_bar,
            text="共用连接；连接前配置角色。",
            foreground="#555555",
        ).pack(side=RIGHT, padx=(10, 0))

        self._workspace_create(outer)

        status = ttk.Frame(outer)
        status.pack(fill=X, pady=(6, 0))
        ttk.Label(status, textvariable=self.status_var).pack(side=LEFT)
        ttk.Label(status, textvariable=self.occupancy_state_var, foreground="#9A4D00").pack(side=LEFT, padx=12)
        ttk.Label(status, text="  后端：Pulse-Eight libCEC C API").pack(side=RIGHT)

    def _create_menu(self) -> None:
        menu = tk.Menu(self)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="打开离线抓包…", command=self._workspace_open_capture)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)
        tools_menu = tk.Menu(menu, tearoff=False)
        tools_menu.add_command(label="连接设置…", command=self._workspace_show_connection_settings)
        tools_menu.add_command(label="原生日志…", command=self._workspace_show_diagnostics)
        tools_menu.add_command(label="API 控制台…", command=self._open_advanced_command)
        tools_menu.add_separator()
        info = tk.Menu(tools_menu, tearoff=False)
        for label, action in (("libCEC 编译信息", self._show_info), ("适配器统计", self._show_stats),
                              ("API 命令帮助", self._show_cli_help), ("后端接口说明", self._show_cli_options)):
            info.add_command(label=label, command=lambda run=action: self._workspace_diagnostic_action(run))
        tools_menu.add_cascade(label="诊断信息", menu=info)
        maintenance = tk.Menu(tools_menu, tearoff=False)
        maintenance.add_command(label="进入 Bootloader…", command=lambda: self._workspace_diagnostic_action(self._bootloader))
        tools_menu.add_cascade(label="固件维护", menu=maintenance)
        tools_menu.add_command(label="选择 OUI 厂商表…", command=self._browse_oui)
        menu.add_cascade(label="工具", menu=tools_menu)
        self.config(menu=menu)

    def _vendor_id_description(self, value: object) -> str:
        """Return a readable raw Vendor ID plus its OUI organization."""
        if not self.oui_lookup.entries:
            return f"{OuiLookup.format_raw(value)}（未加载 OUI 厂商表）"
        return self.oui_lookup.describe(value)

    def _vendor_display(self, value: object) -> str:
        raw = str(value).strip()
        if not raw or raw.upper() in {"UNKNOWN", "NONE", "0"}:
            return raw
        return self._vendor_id_description(raw)

    def _refresh_vendor_id_help(self) -> None:
        if not hasattr(self, "vendor_id_help_var"):
            return
        self.vendor_id_help_var.set(f"查表：{self._vendor_display(self.vendor_id_var.get())}" if self.vendor_id_var.get().strip() else "查表：请输入 6 位十六进制 Vendor ID")
        self.sim_vendor_help_var.set(f"查表：{self._vendor_display(self.sim_vendor_var.get())}" if self.sim_vendor_var.get().strip() else "查表：请输入 6 位十六进制 Vendor ID")

    def _browse_oui(self) -> None:
        path = filedialog.askopenfilename(
            title="选择 IEEE OUI 厂商表",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if not path:
            return
        if not self.oui_lookup.load(path):
            messagebox.showerror(APP_NAME, f"加载 OUI 表失败：\n{self.oui_lookup.error}")
            return
        self.oui_path_var.set(str(self.oui_lookup.path))
        self._refresh_vendor_id_help()
        self._refresh_topology_tree()
        self._refresh_capture_tree()
        self._append_output(f">>> 已加载 OUI 厂商表：{self.oui_lookup.path}（{len(self.oui_lookup.entries)} 条）\n")

    def _create_connection_settings(self, parent: ttk.Frame) -> None:
        config = ttk.LabelFrame(parent, text="libCEC 原生连接参数", padding=8)
        config.pack(fill=X)
        config.columnconfigure(1, weight=1)
        config.columnconfigure(3, weight=1)

        ttk.Label(config, text="libCEC DLL 路径").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.cec_path_var).grid(row=0, column=1, columnspan=3, sticky="ew", padx=4, pady=3)
        ttk.Button(config, text="浏览…", command=self._browse_cec).grid(row=0, column=4, padx=4, pady=3)

        ttk.Label(config, text="设备类型 -t").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.types_var, width=12).grid(row=1, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="端口和连接动作已移至窗口顶部的设备连接栏。", foreground="#555555").grid(row=1, column=2, columnspan=4, sticky=W, padx=4, pady=3)

        ttk.Label(config, text="HDMI 端口").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.hdmi_port_var, width=12).grid(row=2, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="Base 地址").grid(row=2, column=2, sticky=E, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.base_var, width=12).grid(row=2, column=3, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="日志级别").grid(row=2, column=4, sticky=E, padx=4, pady=3)
        RawValueCombobox(config, raw_variable=self.log_level_var, options=LOG_LEVEL_OPTIONS, width=5, state="readonly").grid(row=2, column=5, sticky=W, padx=4, pady=3)

        ttk.Label(config, text="OSD 名称").grid(row=3, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.osd_name_var).grid(row=3, column=1, sticky="ew", padx=4, pady=3)
        ttk.Label(config, text="Vendor ID").grid(row=3, column=2, sticky=E, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.vendor_id_var, width=12).grid(row=3, column=3, sticky=W, padx=4, pady=3)
        ttk.Label(config, textvariable=self.vendor_id_help_var, foreground="#555555").grid(row=3, column=4, columnspan=2, sticky=W, padx=4, pady=3)

        ttk.Label(config, text="Auto Wake AVR").grid(row=4, column=0, sticky=W, padx=4, pady=3)
        RawValueCombobox(config, raw_variable=self.autowake_var, options=BOOL_MODE_OPTIONS, width=9, state="readonly").grid(row=4, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="Autonomous Mode").grid(row=4, column=2, sticky=E, padx=4, pady=3)
        RawValueCombobox(config, raw_variable=self.automode_var, options=BOOL_MODE_OPTIONS, width=9, state="readonly").grid(row=4, column=3, sticky=W, padx=4, pady=3)
        ttk.Checkbutton(config, text="只监听总线", variable=self.monitor_only_var).grid(row=4, column=4, sticky=W, padx=4, pady=3)
        ttk.Checkbutton(config, text="从 EEPROM 读取", variable=self.read_rom_var).grid(row=4, column=5, sticky=W, padx=4, pady=3)

        ttk.Label(config, text="完整日志文件").grid(row=5, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.log_file_var).grid(row=5, column=1, columnspan=3, sticky="ew", padx=4, pady=3)
        ttk.Button(config, text="选择…", command=lambda: self._browse_file(self.log_file_var, save=True)).grid(row=5, column=4, padx=4, pady=3)
        ttk.Label(config, text="短日志文件").grid(row=6, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.short_log_file_var).grid(row=6, column=1, columnspan=3, sticky="ew", padx=4, pady=3)
        ttk.Button(config, text="选择…", command=lambda: self._browse_file(self.short_log_file_var, save=True)).grid(row=6, column=4, padx=4, pady=3)
        ttk.Label(config, text="角色：p/r/t/a/x", foreground="#555555").grid(row=7, column=0, columnspan=2, sticky=W, padx=4, pady=3)
        ttk.Label(config, textvariable=self.oui_path_var, foreground="#555555").grid(row=7, column=2, columnspan=4, sticky=W, padx=4, pady=3)

    def _create_command_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="选择命令后补充参数，或直接输入完整 libCEC API 命令；执行结果显示在下方原生日志中。", foreground="#555555").grid(row=0, column=0, sticky=W, pady=(0, 6))

        frame = ttk.Frame(parent)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.command_tree = ttk.Treeview(frame, columns=("command", "args", "description"), show="headings", selectmode="browse")
        self.command_tree.heading("command", text="命令")
        self.command_tree.heading("args", text="参数")
        self.command_tree.heading("description", text="说明")
        self.command_tree.column("command", width=100, anchor=W)
        self.command_tree.column("args", width=170, anchor=W)
        self.command_tree.column("description", width=700, anchor=W)
        self.command_tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.command_tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.command_tree.configure(yscrollcommand=yscroll.set)
        for command, args, description in COMMANDS:
            self.command_tree.insert("", END, values=(command, args, description))

        bottom = ttk.Frame(parent)
        bottom.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        bottom.columnconfigure(1, weight=1)
        ttk.Label(bottom, text="完整命令").grid(row=0, column=0, padx=(0, 5))
        self.console_entry = ttk.Entry(bottom, textvariable=self.console_command_var)
        self.console_entry.grid(row=0, column=1, sticky="ew", padx=5)
        self.console_entry.bind("<Return>", lambda _e: self._send_selected())
        ttk.Button(bottom, text="执行命令", command=self._send_selected).grid(row=0, column=2, padx=3)



    def _create_simulation_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)

        intro = ttk.Label(
            parent,
            text="角色模拟器监听 libCEC commandReceived 回调，并按内置角色规则或自定义 opcode 规则自动回复。建议在隔离的 TV/Audio System 测试链路上使用。",
            foreground="#555555",
            wraplength=1100,
        )
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        config = ttk.LabelFrame(parent, text="模拟角色配置", padding=8)
        config.grid(row=1, column=0, sticky="ew")
        for column in (1, 3, 5):
            config.columnconfigure(column, weight=1)

        ttk.Label(config, text="角色 -t").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        RawValueCombobox(config, raw_variable=self.sim_role_var, options=ROLE_OPTIONS, width=8, state="readonly").grid(row=0, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="逻辑地址").grid(row=0, column=2, sticky=E, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.sim_source_var, width=8).grid(row=0, column=3, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="物理地址").grid(row=0, column=4, sticky=E, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.sim_physical_var, width=10).grid(row=0, column=5, sticky=W, padx=4, pady=3)

        ttk.Label(config, text="OSD 名称").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.sim_osd_var).grid(row=1, column=1, sticky="ew", padx=4, pady=3)
        ttk.Label(config, text="Vendor ID").grid(row=1, column=2, sticky=E, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.sim_vendor_var, width=10).grid(row=1, column=3, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="CEC 版本").grid(row=1, column=4, sticky=E, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.sim_version_var, width=10).grid(row=1, column=5, sticky=W, padx=4, pady=3)

        ttk.Label(config, text="电源状态").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        RawValueCombobox(config, raw_variable=self.sim_power_var, options=POWER_OPTIONS, width=8, state="readonly").grid(row=2, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="raw 值会直接用于 Report Power Status 参数").grid(row=2, column=2, columnspan=4, sticky=W, padx=4, pady=3)

        ttk.Label(config, text="菜单语言").grid(row=3, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.sim_language_var, width=8).grid(row=3, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="音量 0-100").grid(row=3, column=2, sticky=E, padx=4, pady=3)
        ttk.Entry(config, textvariable=self.sim_volume_var, width=8).grid(row=3, column=3, sticky=W, padx=4, pady=3)
        ttk.Checkbutton(config, text="Mute", variable=self.sim_mute_var).grid(row=3, column=4, sticky=W, padx=4, pady=3)
        ttk.Checkbutton(config, text="System Audio Mode", variable=self.sim_audio_mode_var).grid(row=3, column=5, sticky=W, padx=4, pady=3)

        ttk.Checkbutton(config, text="启用内置常用回复", variable=self.sim_auto_reply_var).grid(row=4, column=0, columnspan=2, sticky=W, padx=4, pady=3)
        ttk.Label(config, text="角色：x=TV，a=Audio System，p=Playback，r=Recorder，t=Tuner").grid(row=4, column=2, columnspan=4, sticky=W, padx=4, pady=3)
        ttk.Label(config, textvariable=self.sim_vendor_help_var, foreground="#555555").grid(row=5, column=0, columnspan=6, sticky=W, padx=4, pady=3)

        controls = ttk.Frame(parent)
        controls.grid(row=2, column=0, sticky="ew", pady=(7, 5))
        ttk.Checkbutton(controls, text="连接时启用角色模拟", variable=self.sim_connect_var,
                        command=lambda: self.occupancy_connect_var.set(False) if self.sim_connect_var.get() else None).pack(side=LEFT, padx=(0, 10))
        self.sim_start_button = ttk.Button(controls, text="应用角色配置", command=self._start_simulation)
        self.sim_start_button.pack(side=LEFT, padx=(0, 5))
        ttk.Button(controls, text="停止模拟", command=self._stop_simulation).pack(side=LEFT, padx=5)
        ttk.Button(controls, text="清空交互记录", command=self._clear_sim_events).pack(side=LEFT, padx=5)
        ttk.Label(controls, text="连接前勾选此项并填写角色参数，再点击窗口顶部‘连接设备’。", foreground="#555555").pack(side=RIGHT)

        event_frame = ttk.LabelFrame(parent, text="消息交互记录", padding=5)
        event_frame.grid(row=3, column=0, sticky="nsew")
        event_frame.rowconfigure(0, weight=1)
        event_frame.columnconfigure(0, weight=1)
        self.sim_events = ttk.Treeview(event_frame, columns=("time", "frame", "from", "to", "opcode", "response"), show="headings", height=10)
        headings = {"time": "时间", "frame": "入站帧", "from": "From", "to": "To", "opcode": "Opcode", "response": "自动回复"}
        widths = {"time": 115, "frame": 180, "from": 70, "to": 70, "opcode": 90, "response": 260}
        for column in headings:
            self.sim_events.heading(column, text=headings[column])
            self.sim_events.column(column, width=widths[column], anchor=W)
        self.sim_events.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(event_frame, orient="vertical", command=self.sim_events.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        self.sim_events.configure(yscrollcommand=yscroll.set)

        rules_frame = ttk.LabelFrame(parent, text="自定义响应规则（可选）", padding=5)
        rules_frame.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        rules_frame.columnconfigure(0, weight=1)
        self.sim_rule_text = tk.Text(rules_frame, height=5, wrap="none", font=("Consolas", 9))
        self.sim_rule_text.grid(row=0, column=0, sticky="ew")
        self.sim_rule_text.insert("1.0", "# 入站 opcode|目标(origin/broadcast/十六进制)|响应字节（不含 header）\n# 8F|origin|90:{power}\n# 46|origin|47:{osd}\n# A0|origin|00:01\n")

    def _create_occupancy_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(4, weight=1)
        ttk.Label(parent, text="让 USB-CEC 适配器在总线上实际应答选定 LA 的 Poll/直接报文，以测试 DUT 地址分配失败、重新分配及冲突恢复。",
                  wraplength=1050).grid(row=0, column=0, sticky="ew", pady=(0, 6))
        config = ttk.LabelFrame(parent, text="占用地址（多选；F 广播/未注册地址不可占用）", padding=8)
        config.grid(row=1, column=0, sticky="ew")
        labels = ["TV", "Recorder 1", "Recorder 2", "Tuner 1", "Playback 1", "Audio System",
                  "Tuner 2", "Tuner 3", "Playback 2", "Recorder 3", "Tuner 4", "Playback 3",
                  "Backup 1 (CEC 2.0)", "Backup 2 (CEC 2.0)", "Free Use"]
        for la, (var, label) in enumerate(zip(self.occupancy_la_vars, labels)):
            ttk.Checkbutton(config, text=f"{la:X} — {label}", variable=var).grid(
                row=la // 5, column=la % 5, sticky=W, padx=8, pady=3)
            config.columnconfigure(la % 5, weight=1)
        presets = ttk.Frame(config)
        presets.grid(row=3, column=0, columnspan=5, sticky="ew", pady=(6, 0))
        for label, addresses in (("Audio 5", [5]), ("Playback 4/8/B", [4, 8, 11]),
                                 ("Recorder 1/2/9", [1, 2, 9]), ("Tuner 3/6/7/A", [3, 6, 7, 10]),
                                 ("全选 0-E", list(range(15))), ("清空选择", [])):
            ttk.Button(presets, text=label, command=lambda values=addresses: self._occupancy_select(values)).pack(side=LEFT, padx=3)
        controls = ttk.Frame(parent)
        controls.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Checkbutton(controls, text="连接时启用地址占用", variable=self.occupancy_connect_var,
                        command=lambda: self.sim_connect_var.set(False) if self.occupancy_connect_var.get() else None).pack(side=LEFT, padx=(0, 8))
        self.occupancy_buttons = []
        for label, action in (("开始占用 / 更新占用", "apply"), ("释放全部", "release"), ("读取占用状态", "read")):
            button = ttk.Button(controls, text=label, command=lambda name=action: self._occupancy_action(name))
            button.pack(side=LEFT, padx=4)
            self.occupancy_buttons.append(button)
        ttk.Label(parent, textvariable=self.occupancy_state_var, foreground="#9A4D00").grid(row=3, column=0, sticky=W)
        observations = ttk.LabelFrame(parent, text="实验记录（原生 rawFrame；全部总线报文见 CEC 报文监视）", padding=6)
        observations.grid(row=4, column=0, sticky="nsew", pady=6)
        observations.columnconfigure(0, weight=1)
        observations.rowconfigure(1, weight=1)
        ttk.Label(observations, textvariable=self.occupancy_poll_var).grid(row=0, column=0, sticky=W)
        ttk.Button(observations, text="清空实验记录", command=self._occupancy_clear_records).grid(row=0, column=1, sticky=E)
        self.occupancy_events = ttk.Treeview(observations, columns=("time", "mask", "frame", "result", "note"), show="headings")
        for col, title, width in (("time", "时间", 110), ("mask", "占用 Mask", 95), ("frame", "报文/操作", 165),
                                  ("result", "状态", 100), ("note", "解释", 450)):
            self.occupancy_events.heading(col, text=title)
            self.occupancy_events.column(col, width=width, anchor=W, stretch=(col == "note"))
        self.occupancy_events.grid(row=1, column=0, columnspan=2, sticky="nsew")
        scrollbar = ttk.Scrollbar(observations, command=self.occupancy_events.yview)
        scrollbar.grid(row=1, column=2, sticky="ns")
        self.occupancy_events.configure(yscrollcommand=scrollbar.set)
        help_text = (
            "前提：DUT CEC 已启用且具备有效物理地址/测试拓扑。测试流程：先占用候选 LA → 重启 DUT 或触发其 CEC 地址重新分配 → 观察自 Poll（例如 55）得到 ACK、"
            "备用地址轮询及 DUT 后续行为 → 释放全部 → 再触发分配，比较恢复行为。DUT 重启/HPD 由测试人员或外部夹具触发。\n"
            "通过条件：DUT 对已占用候选地址按适用规范/CDF 放弃、选择可用地址或进入未注册状态；"
            "失败条件：仍使用已占用地址、无合法回退、异常重启或无法恢复。仅凭没有报文不能判定通过。\n"
            "占用期间停止角色回复，保持监听路由；选定地址的硬件 ACK 会开启，所以此时不是纯被动监听。"
            "Raw 可另外发送同 Source 的宣告，例如 5F:84:20:00:05；相同 LA 的发帧设备仅凭 Header 无法区分。\n"
            "读取状态显示适配器最后接受的配置，实际 ACK/NACK 以抓到的 Poll 为准。支持多个 LA 的稳定占用，"
            "不提供位级仲裁/同时发送时序注入。释放或正常断开会清除测试占用；下次启动默认不自动启用。")
        ttk.Label(parent, text=help_text, wraplength=1050, justify=LEFT, foreground="#555555").grid(row=5, column=0, sticky="ew", pady=5)
        ttk.Button(parent, text="导出实验 JSON", command=self._occupancy_export).grid(row=6, column=0, sticky=W, pady=4)

    def _occupancy_select(self, addresses: list[int]) -> None:
        for la, var in enumerate(self.occupancy_la_vars):
            var.set(la in addresses)

    def _occupancy_selection_mask(self) -> int:
        return sum(1 << la for la, var in enumerate(self.occupancy_la_vars) if var.get())

    def _occupancy_clear_records(self) -> None:
        self.occupancy_events.delete(*self.occupancy_events.get_children())
        self.occupancy_poll_counts.clear()
        self.occupancy_poll_var.set("尚无观测记录")

    def _occupancy_export(self) -> None:
        path = filedialog.asksaveasfilename(title="导出逻辑地址占用实验", defaultextension=".json",
            initialfile=f"la-occupancy-{datetime.now():%Y%m%d-%H%M%S}.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        payload = {
            "generated_at": datetime.now().isoformat(timespec="milliseconds"),
            "port": self.backend.open_port or self.port_var.get(),
            "libcec_dll": str(self.backend.dll_path),
            "selected_mask": self._occupancy_selection_mask(),
            "accepted_mask": self.backend.occupancy_mask,
            "poll_counts": {f"{la:X}": dict(zip(("ack", "nack", "unknown"), counts))
                            for la, counts in self.occupancy_poll_counts.items()},
            "records": [dict(zip(("time", "accepted_mask", "frame_or_action", "status", "note"),
                                 self.occupancy_events.item(item, "values")))
                        for item in self.occupancy_events.get_children()],
            "record_limit": 500,
            "result": "观测记录；DUT 分配失败/恢复及 CDF 适用性需测试人员判定",
        }
        try:
            Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.status_var.set(f"占用实验已导出：{path}")

    def _occupancy_record(self, frame: str, result: str, note: str, timestamp: int | None = None) -> None:
        item = self.occupancy_events.insert("", END, values=(self._format_capture_time(timestamp),
            f"0x{self.backend.occupancy_mask:04X}", frame, result, note))
        children = self.occupancy_events.get_children()
        if len(children) > 500:
            self.occupancy_events.delete(children[0])
        self.occupancy_events.see(item)

    def _occupancy_refresh_state(self) -> None:
        mask = self.backend.occupancy_mask
        if mask:
            addresses = "/".join(f"{la:X}" for la in range(15) if mask & (1 << la))
            self.occupancy_state_var.set(f"地址占用：LA {addresses}（ACK Mask 0x{mask:04X}）")
            self.monitor_state_var.set("监听路由：开启；地址占用硬件 ACK：开启")
            self.monitor_off_button.configure(state="disabled")
            self.sim_start_button.configure(state="disabled")
        else:
            self.occupancy_state_var.set("地址占用：未启用")
            state = "normal" if self.backend.is_open else "disabled"
            self.monitor_off_button.configure(state=state)
            self.sim_start_button.configure(state="normal")

    def _occupancy_action(self, action: str) -> None:
        if self.occupancy_busy:
            return
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先使用顶部‘连接设备’；也可勾选‘连接时启用地址占用’后连接。")
            return
        if self.cts_running or self.scenario_running or self.raw_send_busy or self.oneshot_busy:
            messagebox.showwarning(APP_NAME, "请等待当前测试/发送结束，再改变地址占用。")
            return
        mask = self._occupancy_selection_mask() if action == "apply" else 0
        if action == "apply" and not mask:
            messagebox.showwarning(APP_NAME, "请至少选择一个 LA；释放地址请点击‘释放全部’。")
            return
        self.occupancy_busy = True
        if action == "apply":
            self.simulation_active = False
        for button in self.occupancy_buttons:
            button.configure(state="disabled")
        threading.Thread(target=self._occupancy_thread, args=(action, mask), daemon=True).start()

    def _occupancy_thread(self, action: str, mask: int) -> None:
        try:
            result = (self.backend.address_occupancy() if action == "read"
                      else self.backend.set_address_occupancy(mask))
        except Exception as exc:
            result = BackendResult(False, str(exc))
        self.output_queue.put(("occupancy_done", (action, result)))

    def _occupancy_observe(self, frame: list[int], event: dict[str, object], status: str, timestamp: int | None) -> None:
        if not self.occupancy_observed_mask or event.get("direction") != "RX":
            return
        source, destination = frame[0] >> 4, frame[0] & 15
        if len(frame) == 1 and destination < 15:
            counts = self.occupancy_poll_counts.setdefault(destination, [0, 0, 0])
            counts[0 if status.startswith("ACK") else 1 if status.startswith("NACK") else 2] += 1
            summary = "；".join(f"LA {la:X}: ACK {n[0]} / NACK {n[1]} / 未知 {n[2]}"
                                for la, n in sorted(self.occupancy_poll_counts.items()))
            self.occupancy_poll_var.set(summary)
            note = "自 Poll，可能是地址分配/重新分配" if source == destination else "设备探测 Poll"
            self._occupancy_record(self._format_frame(frame), status, note, timestamp)
        elif len(frame) > 1 and frame[1] == 0x84:
            self._occupancy_record(self._format_frame(frame), status,
                f"LA {source:X} 宣告 Report Physical Address；同 LA 的 DUT 和测试报文需结合实验流程区分", timestamp)

    def _create_raw_tab(self, parent: ttk.Frame) -> None:
        self.raw_tab_widget = parent
        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)
        ttk.Label(
            parent,
            text="Raw CEC 注入：手动指定发送方/目标 LA，组装任意完整 CEC 帧，并通过 libCEC transmit 原样注入。此页面不生成角色回复。",
            foreground="#555555",
            wraplength=1100,
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))

        session = ttk.LabelFrame(parent, text="发包选项", padding=8)
        session.grid(row=1, column=0, sticky="ew")
        ttk.Label(session, text="Raw 复用顶部的通用连接；监听状态请查看窗口顶部。", foreground="#555555").pack(side=LEFT, padx=(0, 12))
        ttk.Button(session, text="应用 LA", command=self._raw_apply_la).pack(side=LEFT, padx=5)
        ttk.Checkbutton(session, text="发送前自动应用 LA", variable=self.raw_auto_la_var).pack(side=LEFT, padx=12)
        ttk.Checkbutton(session, text="txn（不等待 ACK）", variable=self.raw_no_ack_var).pack(side=LEFT, padx=5)

        builder = ttk.LabelFrame(parent, text="任意帧组装", padding=8)
        builder.grid(row=2, column=0, sticky="ew", pady=(7, 6))
        builder.columnconfigure(7, weight=1)
        ttk.Label(builder, text="Source LA").grid(row=0, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(builder, textvariable=self.raw_source_var, width=7).grid(row=0, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(builder, text="Destination LA").grid(row=0, column=2, sticky=E, padx=4, pady=3)
        ttk.Entry(builder, textvariable=self.raw_destination_var, width=7).grid(row=0, column=3, sticky=W, padx=4, pady=3)
        ttk.Label(builder, text="Opcode").grid(row=0, column=4, sticky=E, padx=4, pady=3)
        self.raw_opcode_combo = RawValueCombobox(
            builder,
            raw_variable=self.raw_opcode_var,
            options=OPCODE_CATALOG_OPTIONS,
            width=7,
            state="normal",
        )
        self.raw_opcode_combo.grid(row=0, column=5, sticky=W, padx=4, pady=3)
        ttk.Label(builder, text="Parameters").grid(row=0, column=6, sticky=E, padx=4, pady=3)
        ttk.Entry(builder, textvariable=self.raw_params_var).grid(row=0, column=7, sticky="ew", padx=4, pady=3)
        ttk.Button(builder, text="组装帧", command=self._raw_build_frame).grid(row=0, column=8, padx=4, pady=3)

        ttk.Label(builder, text="完整报文").grid(row=1, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(builder, textvariable=self.raw_frame_var).grid(row=1, column=1, columnspan=7, sticky="ew", padx=4, pady=3)
        ttk.Button(builder, text="发送 tx", command=lambda: self._raw_send(False)).grid(row=1, column=8, padx=4, pady=3)
        ttk.Button(builder, text="发送 txn", command=lambda: self._raw_send(True)).grid(row=2, column=8, padx=4, pady=3)
        ttk.Label(builder, text="次数").grid(row=2, column=0, sticky=W, padx=4, pady=3)
        ttk.Entry(builder, textvariable=self.raw_count_var, width=7).grid(row=2, column=1, sticky=W, padx=4, pady=3)
        ttk.Label(builder, text="间隔(ms)").grid(row=2, column=2, sticky=E, padx=4, pady=3)
        ttk.Entry(builder, textvariable=self.raw_interval_var, width=7).grid(row=2, column=3, sticky=W, padx=4, pady=3)
        ttk.Label(builder, text="完整报文格式：header:opcode:param1:param2…；header=(Source LA<<4)|Destination LA", foreground="#555555").grid(row=2, column=4, columnspan=4, sticky=W, padx=4, pady=3)
        ttk.Label(builder, text="Opcode 说明").grid(row=3, column=0, sticky="nw", padx=4, pady=(4, 2))
        ttk.Label(
            builder,
            textvariable=self.raw_opcode_help_var,
            foreground="#555555",
            justify=LEFT,
            wraplength=1100,
        ).grid(row=3, column=1, columnspan=8, sticky="ew", padx=4, pady=(4, 2))

        history_frame = ttk.LabelFrame(parent, text="发送历史", padding=5)
        history_frame.grid(row=3, column=0, sticky="nsew")
        history_frame.rowconfigure(0, weight=1)
        history_frame.columnconfigure(0, weight=1)
        self.raw_history = ttk.Treeview(history_frame, columns=("time", "mode", "frame", "count"), show="headings")
        for column, title, width in (("time", "时间", 115), ("mode", "模式", 70), ("frame", "报文", 520), ("count", "次数", 70)):
            self.raw_history.heading(column, text=title)
            self.raw_history.column(column, width=width, anchor=W)
        self.raw_history.grid(row=0, column=0, sticky="nsew")
        raw_scroll = ttk.Scrollbar(history_frame, orient="vertical", command=self.raw_history.yview)
        raw_scroll.grid(row=0, column=1, sticky="ns")
        self.raw_history.configure(yscrollcommand=raw_scroll.set)

        batch_frame = ttk.LabelFrame(parent, text="批量原始报文（每行一个 tx/txn 或纯报文字节）", padding=5)
        batch_frame.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        batch_frame.columnconfigure(0, weight=1)
        self.raw_batch_text = tk.Text(batch_frame, height=5, wrap="none", font=("Consolas", 9))
        self.raw_batch_text.grid(row=0, column=0, sticky="ew")
        self.raw_batch_text.insert("1.0", "# 例：\ntx 0F:8F\ntx 05:70:10:00\ntxn 1F:82:10:00\n")
        ttk.Button(batch_frame, text="发送批量报文", command=self._raw_send_batch).grid(row=0, column=1, padx=6)

    def _create_capture_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        ttk.Checkbutton(toolbar, text="暂停刷新", variable=self.capture_paused_var,
                        command=self._capture_pause_changed).pack(side=LEFT, padx=(0, 10))
        ttk.Checkbutton(
            toolbar,
            text="显示 Polling",
            variable=self.capture_show_poll_var,
            command=self._wb_base_changed,
        ).pack(side=LEFT, padx=(0, 10))
        ttk.Label(toolbar, text="方向").pack(side=LEFT)
        RawValueCombobox(toolbar, raw_variable=self.capture_direction_var, options=CAPTURE_DIRECTION_OPTIONS, width=7, state="readonly").pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="清空", command=self._clear_capture).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="导出 CSV", command=self._export_capture_csv).pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="导出 JSON", command=self._export_capture_json).pack(side=LEFT, padx=4)
        self._ai_export_button = ttk.Button(toolbar, text="导出 AI 分析包", command=self._ai_show_export)
        self._ai_export_button.pack(side=LEFT, padx=4)
        ttk.Button(toolbar, text="重置列宽", command=self._reset_capture_columns).pack(side=LEFT, padx=4)
        self.capture_layout_button = ttk.Button(toolbar, text="列布局", command=self._layout_show_menu)
        self.capture_layout_button.pack(side=LEFT, padx=4)
        show_help = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text="说明", variable=show_help,
                        command=lambda: help_frame.grid() if show_help.get() else help_frame.grid_remove()).pack(side=LEFT, padx=4)

        interval_frame = ttk.LabelFrame(parent, text="时间间隔（右键两条消息分别设为时间点 1/2）", padding=(6, 3))
        interval_frame.grid(row=1, column=0, sticky="ew", pady=(0, 3))
        interval_frame.columnconfigure(0, weight=1)
        ttk.Label(
            interval_frame,
            textvariable=self.capture_interval_var,
            foreground="#1F3B53",
            anchor=W,
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(interval_frame, textvariable=self.capture_summary_var,
                  foreground="#555555", anchor=E).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Label(interval_frame, textvariable=self.capture_refresh_state_var,
                  foreground="#555555", anchor=W).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(1, 0))
        help_frame = ttk.Frame(interval_frame)
        help_frame.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(2, 0))
        for note in (
            "双击消息查看完整详情；右键“载入 Raw 注入”可复制报文到发送页，等待手动发送。",
            "RX/TX 来自 libCEC rawFrame 原生回调；ACK 规则：单播 0=ACK/1=NACK，广播 1=ACK/0=NACK；行底色按 Source LA 区分。",
            "理论帧时长 = Start 4.5 ms + 每字节 24.0 ms；不含总线空闲、重试和响应等待。",
            "拖动表头分隔线调整列宽；双击分隔线按当前内容调整；重置列宽恢复紧凑布局。长报文、Opcode 和解释会自动换行。",
        ):
            ttk.Label(help_frame, text=note, foreground="#666666", anchor=W, wraplength=920).pack(anchor=W)
        help_frame.grid_remove()

        self._wb_create(parent, toolbar, RawValueCombobox, OPCODE_CATALOG_OPTIONS)
        frame = ttk.Frame(parent)
        frame.grid(row=3, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        columns = CAPTURE_COLUMNS
        self.capture_tree = CaptureTable(frame, columns=columns)
        headings = CAPTURE_HEADINGS
        for column in columns:
            self.capture_tree.heading(column, text=headings[column])
        self._layout_install()
        self.capture_tree.on_column_resize = self._layout_changed
        self.capture_tree.header_canvas.bind("<Button-3>", self._layout_show_menu)
        self.capture_tree.grid(row=0, column=0, sticky="nsew")
        # Use a soft, deterministic color per source LA.  The source and
        # destination columns remain visible, so the color is only a visual
        # grouping aid and never replaces the raw address values.
        source_colors = (
            "#FDECEC", "#FFF4E5", "#FFFBE6", "#EEF8E8",
            "#E8F7F2", "#E8F4FB", "#EEF0FF", "#F3ECFB",
            "#FBEAF3", "#F1F4F6", "#EAF7F8", "#F4F0E8",
            "#EAF0F8", "#F5EAF5", "#EAF5EF", "#F0F0F0",
        )
        for source, background in enumerate(source_colors):
            self.capture_tree.tag_configure(f"capture_source_{source:X}", background=background, foreground="#263238")
        self.capture_tree.tag_configure("capture_source_unknown", background="#F5F5F5", foreground="#37474F")
        self._wb_apply_highlight_styles()
        self.capture_tree.tag_configure("capture_length_error")
        self.capture_tree.on_user_scroll = self._capture_user_scrolled
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self._capture_user_yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.capture_tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.capture_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.capture_tree.bind("<Double-1>", self._capture_show_details)
        self.capture_tree.bind("<Button-3>", self._capture_context_menu, add="+")
        self.capture_tree.bind("<Control-c>", self._copy_capture_current_row, add="+")
        self.capture_tree.bind("<Control-a>", self._capture_select_all, add="+")

    def _reset_capture_columns(self) -> None:
        self.capture_tree.set_column_layout(PREFERRED_WIDTHS, MINIMUM_WIDTHS)
        self._layout_changed()

    def _create_scenario_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.rowconfigure(3, weight=1)
        parent.columnconfigure(0, weight=1)
        intro = ttk.Frame(parent)
        intro.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(intro, text="统一脚本：libCEC API 命令、wait / @wait 毫秒、expect、expect-opcode 和 assert；# 为注释。",
                  foreground="#555555", wraplength=1080).pack(anchor=W)
        pacing = ttk.Frame(intro)
        pacing.pack(fill=X, pady=(4, 0))
        ttk.Label(pacing, text="执行节奏").pack(side=LEFT)
        RawValueCombobox(pacing, raw_variable=self.script_dialect_var,
                         options=[("AUTO", "自动识别"), ("SCENARIO", "场景（命令间隔80ms）"), ("LEGACY", "旧命令脚本（自定间隔）")],
                         width=29, state="readonly").pack(side=LEFT, padx=4)
        ttk.Label(pacing, text="旧命令间隔(ms)").pack(side=LEFT, padx=(8, 3))
        ttk.Entry(pacing, textvariable=self.script_delay_var, width=8).pack(side=LEFT)
        ttk.Label(intro, text="纯命令旧文件可选 LEGACY；AUTO 无格式标记时按场景节奏。", foreground="#555555", wraplength=1080).pack(anchor=W, pady=(2, 0))
        scenario_frame = ttk.Frame(parent)
        scenario_frame.grid(row=1, column=0, sticky="nsew")
        scenario_frame.rowconfigure(0, weight=1)
        scenario_frame.columnconfigure(0, weight=1)
        self.scenario_text = tk.Text(scenario_frame, wrap="none", font=("Consolas", 10))
        self.script_text = self.scenario_text
        self.scenario_text.grid(row=0, column=0, sticky="nsew")
        scenario_scroll = ttk.Scrollbar(scenario_frame, orient="vertical", command=self.scenario_text.yview)
        scenario_scroll.grid(row=0, column=1, sticky="ns")
        self.scenario_text.configure(yscrollcommand=scenario_scroll.set)
        self.scenario_text.insert("1.0", "# 先在窗口顶部连接设备，再运行场景\nla 0\ntx 05:70:10:00\nwait 500\nexpect-opcode 72 timeout=1500\n")

        controls = ttk.Frame(parent)
        controls.grid(row=2, column=0, sticky="ew", pady=(7, 6))
        ttk.Button(controls, text="运行脚本", command=self._run_scenario).pack(side=LEFT, padx=(0, 5))
        ttk.Button(controls, text="停止脚本", command=self._stop_scenario).pack(side=LEFT, padx=5)
        ttk.Button(controls, text="打开", command=self._open_scenario).pack(side=LEFT, padx=5)
        ttk.Button(controls, text="保存", command=self._save_scenario).pack(side=LEFT, padx=5)
        ttk.Label(controls, textvariable=self.scenario_status_var).pack(side=RIGHT)

        result_frame = ttk.LabelFrame(parent, text="脚本执行结果", padding=5)
        result_frame.grid(row=3, column=0, sticky="nsew")
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        self.scenario_log = tk.Text(result_frame, wrap="none", font=("Consolas", 9), state="disabled", background="#101418", foreground="#d7e3ea")
        self.scenario_log.grid(row=0, column=0, sticky="nsew")
        result_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.scenario_log.yview)
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.scenario_log.configure(yscrollcommand=result_scroll.set)

    def _create_topology_tab(self, parent: ttk.Frame) -> None:
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="扫描 CEC 总线", command=self._run_topology_scan).pack(side=LEFT, padx=(0, 5))
        ttk.Button(toolbar, text="清空", command=self._clear_topology).pack(side=LEFT, padx=5)
        ttk.Button(toolbar, text="导出 CSV", command=self._export_topology_csv).pack(side=LEFT, padx=5)
        ttk.Button(toolbar, text="导出 JSON", command=self._export_topology_json).pack(side=LEFT, padx=5)
        ttk.Label(toolbar, text="扫描使用当前设备连接", foreground="#555555").pack(side=RIGHT)

        frame = ttk.Frame(parent)
        frame.grid(row=1, column=0, sticky="nsew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        columns = ("number", "type", "address", "active", "vendor", "osd", "version", "power", "language")
        self.topology_tree = ttk.Treeview(frame, columns=columns, show="headings")
        headings = {"number": "LA", "type": "类型", "address": "物理地址", "active": "Active", "vendor": "Vendor", "osd": "OSD", "version": "CEC 版本", "power": "电源", "language": "语言"}
        widths = {"number": 55, "type": 120, "address": 110, "active": 70, "vendor": 120, "osd": 150, "version": 85, "power": 85, "language": 85}
        for column in columns:
            self.topology_tree.heading(column, text=headings[column])
            self.topology_tree.column(column, width=widths[column], anchor=W)
        self.topology_tree.grid(row=0, column=0, sticky="nsew")
        topology_scroll = ttk.Scrollbar(frame, orient="vertical", command=self.topology_tree.yview)
        topology_scroll.grid(row=0, column=1, sticky="ns")
        self.topology_tree.configure(yscrollcommand=topology_scroll.set)

    def _create_cts_tab(self, parent: ttk.Frame) -> None:
        """Create the CTS catalogue, case detail and execution/report view."""
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        filter_bar = ttk.Frame(parent)
        filter_bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(filter_bar, text="章节").pack(side=LEFT)
        chapter_values = ["全部"] + sorted({case.chapter for case in self.cts_cases})
        chapter_options = [(chapter, "全部 CTS 章节" if chapter == "全部" else f"{chapter} 测试项目") for chapter in chapter_values]
        chapter_combo = RawValueCombobox(filter_bar, raw_variable=self.cts_chapter_var, options=chapter_options, width=14, state="readonly")
        chapter_combo.pack(side=LEFT, padx=4)
        ttk.Label(filter_bar, text="执行方式").pack(side=LEFT, padx=(10, 0))
        mode_combo = RawValueCombobox(filter_bar, raw_variable=self.cts_mode_var, options=CTS_MODE_OPTIONS, width=13, state="readonly")
        mode_combo.pack(side=LEFT, padx=4)
        ttk.Label(filter_bar, text="搜索").pack(side=LEFT, padx=(10, 0))
        search_entry = ttk.Entry(filter_bar, textvariable=self.cts_search_var, width=27)
        search_entry.pack(side=LEFT, padx=4)
        search_entry.bind("<KeyRelease>", lambda _event: self._cts_refresh_case_list())
        chapter_combo.bind("<<ComboboxSelected>>", lambda _event: self._cts_refresh_case_list(), add="+")
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: self._cts_refresh_case_list(), add="+")

        ttk.Button(filter_bar, text="全选自动项", command=self._cts_select_all_auto).pack(side=LEFT, padx=(10, 3))
        ttk.Button(filter_bar, text="全选当前筛选", command=self._cts_select_all_visible).pack(side=LEFT, padx=3)
        ttk.Button(filter_bar, text="清空勾选", command=self._cts_clear_selection).pack(side=LEFT, padx=3)
        ttk.Button(filter_bar, text="执行已勾选", command=self._cts_run_selected).pack(side=LEFT, padx=(12, 3))
        ttk.Button(filter_bar, text="停止执行", command=self._cts_stop_run).pack(side=LEFT, padx=3)
        ttk.Button(filter_bar, text="导出报告", command=self._cts_export_report).pack(side=LEFT, padx=3)
        ttk.Label(filter_bar, text="条件执行项目由测试人员依据当前 CDF 决定是否勾选", foreground="#8a5a00").pack(side=RIGHT, padx=(8, 0))

        body = ttk.PanedWindow(parent, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew")
        left = ttk.Frame(body, padding=(0, 0, 6, 0))
        right = ttk.Frame(body, padding=(6, 0, 0, 0))
        body.add(left)
        body.add(right)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        right.columnconfigure(0, weight=1)

        left_top = ttk.Frame(left)
        left_top.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        ttk.Label(left_top, text="CTS 测试项目（真实复选框）", font=("Segoe UI", 10, "bold")).pack(side=LEFT)
        ttk.Label(left_top, textvariable=self.cts_status_var, foreground="#555555").pack(side=RIGHT)

        list_frame = ttk.Frame(left)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.rowconfigure(0, weight=1)
        list_frame.columnconfigure(0, weight=1)
        self.cts_case_canvas = tk.Canvas(list_frame, highlightthickness=0, borderwidth=0)
        self.cts_case_canvas.grid(row=0, column=0, sticky="nsew")
        cts_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.cts_case_canvas.yview)
        cts_scroll.grid(row=0, column=1, sticky="ns")
        self.cts_case_canvas.configure(yscrollcommand=cts_scroll.set)
        self.cts_case_inner = ttk.Frame(self.cts_case_canvas)
        self.cts_case_window = self.cts_case_canvas.create_window((0, 0), window=self.cts_case_inner, anchor="nw")
        self.cts_case_inner.bind("<Configure>", lambda _event: self.cts_case_canvas.configure(scrollregion=self.cts_case_canvas.bbox("all")))
        self.cts_case_canvas.bind("<Configure>", lambda event: self.cts_case_canvas.itemconfigure(self.cts_case_window, width=event.width))

        detail_frame = ttk.LabelFrame(right, text="测试项目详情", padding=6)
        detail_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        detail_frame.columnconfigure(1, weight=1)
        ttk.Label(detail_frame, text="DUT LA").grid(row=0, column=0, sticky=W, padx=3, pady=2)
        ttk.Entry(detail_frame, textvariable=self.cts_dut_la_var, width=5).grid(row=0, column=1, sticky=W, padx=3, pady=2)
        ttk.Label(detail_frame, text="测试端 LA").grid(row=0, column=2, sticky=E, padx=3, pady=2)
        ttk.Entry(detail_frame, textvariable=self.cts_te_la_var, width=5).grid(row=0, column=3, sticky=W, padx=3, pady=2)
        ttk.Label(detail_frame, text="当前端口").grid(row=0, column=4, sticky=E, padx=3, pady=2)
        ttk.Label(detail_frame, textvariable=self.port_var).grid(row=0, column=5, sticky=W, padx=3, pady=2)
        ttk.Button(detail_frame, text="查看当前项", command=lambda: self._cts_show_case(self.cts_current_case_id)).grid(row=0, column=6, padx=3, pady=2)
        ttk.Button(detail_frame, text="人工通过", command=lambda: self._cts_mark_manual("PASS-MANUAL")).grid(row=0, column=7, padx=3, pady=2)
        ttk.Button(detail_frame, text="人工失败", command=lambda: self._cts_mark_manual("FAIL-MANUAL")).grid(row=0, column=8, padx=3, pady=2)

        self.cts_detail_title = ttk.Label(detail_frame, text="请选择左侧测试项目", font=("Segoe UI", 10, "bold"))
        self.cts_detail_title.grid(row=1, column=0, columnspan=9, sticky=W, padx=3, pady=(5, 0))
        ttk.Label(detail_frame, text="CDF 决策").grid(row=2, column=0, sticky=W, padx=3, pady=2)
        self.cts_cdf_decision_combo = RawValueCombobox(detail_frame, raw_variable=self.cts_cdf_decision_var, options=CDF_DECISION_OPTIONS, width=12, state="readonly")
        self.cts_cdf_decision_combo.grid(row=2, column=1, sticky=W, padx=3, pady=2)
        ttk.Label(detail_frame, text="CDF 版本/依据").grid(row=2, column=2, sticky=E, padx=3, pady=2)
        ttk.Entry(detail_frame, textvariable=self.cts_cdf_reason_var, width=52).grid(row=2, column=3, columnspan=4, sticky="ew", padx=3, pady=2)
        ttk.Button(detail_frame, text="保存 CDF 决策", command=self._cts_save_cdf_decision).grid(row=2, column=7, columnspan=2, padx=3, pady=2)

        detail_text_frame = ttk.Frame(right)
        detail_text_frame.grid(row=1, column=0, sticky="nsew")
        detail_text_frame.rowconfigure(0, weight=1)
        detail_text_frame.columnconfigure(0, weight=1)
        self.cts_detail_text = tk.Text(detail_text_frame, wrap="word", font=("Segoe UI", 10), state="disabled")
        self.cts_detail_text.grid(row=0, column=0, sticky="nsew")
        detail_scroll = ttk.Scrollbar(detail_text_frame, orient="vertical", command=self.cts_detail_text.yview)
        detail_scroll.grid(row=0, column=1, sticky="ns")
        self.cts_detail_text.configure(yscrollcommand=detail_scroll.set)

        result_frame = ttk.LabelFrame(right, text="本次执行结果", padding=5)
        result_frame.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        result_frame.rowconfigure(0, weight=1)
        result_frame.columnconfigure(0, weight=1)
        result_columns = ("case_id", "mode", "result", "message")
        self.cts_result_tree = ttk.Treeview(result_frame, columns=result_columns, show="headings", height=7)
        for column, title, width in (("case_id", "Case", 125), ("mode", "方式", 95), ("result", "结果", 105), ("message", "证据/说明", 520)):
            self.cts_result_tree.heading(column, text=title)
            self.cts_result_tree.column(column, width=width, anchor=W)
        self.cts_result_tree.grid(row=0, column=0, sticky="ew")
        result_scroll = ttk.Scrollbar(result_frame, orient="vertical", command=self.cts_result_tree.yview)
        result_scroll.grid(row=0, column=1, sticky="ns")
        self.cts_result_tree.configure(yscrollcommand=result_scroll.set)
        self.cts_result_tree.bind("<<TreeviewSelect>>", self._cts_result_selected)
        for tag, color in (("PASS", "#176b2c"), ("FAIL", "#a11b1b"), ("MANUAL", "#775500"), ("EXTERNAL", "#5a3c88"), ("CONDITIONAL", "#8a5a00")):
            self.cts_result_tree.tag_configure(tag, foreground=color)

        self._cts_refresh_case_list()

    @staticmethod
    def _cts_mode_text(mode: str) -> str:
        return {
            AUTO: "自动",
            MANUAL: "人工",
            EXTERNAL: "外部 CTS",
            CONDITIONAL: "条件执行",
        }.get(mode, mode)

    def _cts_visible_cases(self) -> list[CtsTestCase]:
        chapter = self.cts_chapter_var.get()
        mode = self.cts_mode_var.get()
        search = self.cts_search_var.get().strip().lower()
        visible = []
        for case in self.cts_cases:
            if chapter != "全部" and case.chapter != chapter:
                continue
            if mode != "全部" and case.execution_mode != mode:
                continue
            if search and search not in f"{case.case_id} {case.title} {case.flow}".lower():
                continue
            visible.append(case)
        return visible

    def _cts_refresh_case_list(self) -> None:
        if not hasattr(self, "cts_case_inner"):
            return
        for child in self.cts_case_inner.winfo_children():
            child.destroy()
        self.cts_case_widgets.clear()
        visible = self._cts_visible_cases()
        previous_chapter = ""
        for case in visible:
            if case.chapter != previous_chapter:
                ttk.Label(self.cts_case_inner, text=case.chapter, font=("Segoe UI", 9, "bold"), foreground="#1b4f72").pack(fill=X, padx=5, pady=(7 if previous_chapter else 2, 2))
                previous_chapter = case.chapter
            row = ttk.Frame(self.cts_case_inner)
            row.pack(fill=X, padx=2, pady=1)
            variable = self.cts_case_vars.setdefault(case.case_id, tk.BooleanVar(value=case.case_id in self.cts_selected_ids))
            variable.set(case.case_id in self.cts_selected_ids)
            label = self._cts_case_label(case)
            check = ttk.Checkbutton(row, text=label, variable=variable, command=lambda case_id=case.case_id: self._cts_case_toggled(case_id))
            check.pack(side=LEFT, fill=X, expand=True, padx=3)
            ttk.Button(row, text="详情", width=5, command=lambda case_id=case.case_id: self._cts_show_case(case_id)).pack(side=RIGHT, padx=3)
            self.cts_case_widgets[case.case_id] = check
        self.cts_status_var.set(f"已加载 {len(self.cts_cases)} 项，当前显示 {len(visible)} 项，已勾选 {len(self.cts_selected_ids)} 项")

    def _cts_case_label(self, case: CtsTestCase) -> str:
        status = self.cts_case_status.get(case.case_id, case.initial_status)
        return f"{case.case_id}  [{self._cts_mode_text(case.execution_mode)}]  {case.title}  — {status}"

    def _cts_case_toggled(self, case_id: str) -> None:
        variable = self.cts_case_vars[case_id]
        if variable.get():
            self.cts_selected_ids.add(case_id)
        else:
            self.cts_selected_ids.discard(case_id)
        case = self.cts_case_by_id.get(case_id)
        if case:
            self._cts_show_case(case_id)
        self.cts_status_var.set(f"已加载 {len(self.cts_cases)} 项，当前显示 {len(self._cts_visible_cases())} 项，已勾选 {len(self.cts_selected_ids)} 项")

    def _cts_save_cdf_decision(self) -> None:
        case_id = self.cts_current_case_id
        case = self.cts_case_by_id.get(case_id or "")
        if not case:
            messagebox.showwarning(APP_NAME, "请先选择一个测试项目。")
            return
        if case.execution_mode != CONDITIONAL:
            messagebox.showinfo(APP_NAME, "只有条件执行项目需要填写 CDF 决策。")
            return
        decision = self.cts_cdf_decision_var.get().strip() or "待决定"
        reason = self.cts_cdf_reason_var.get().strip()
        self.cts_cdf_decisions[case.case_id] = decision
        self.cts_cdf_reasons[case.case_id] = reason
        if decision == "适用":
            self.cts_case_status[case.case_id] = "CDF 已确认适用"
        elif decision == "不适用":
            self.cts_case_status[case.case_id] = "CDF 已确认不适用"
        else:
            self.cts_case_status[case.case_id] = case.initial_status
        self._cts_refresh_case_list()
        self._cts_show_case(case.case_id)
        self.cts_status_var.set(f"已保存 {case.case_id} 的 CDF 决策：{decision}")

    def _cts_select_all_auto(self) -> None:
        for case in self._cts_visible_cases():
            if case.execution_mode == AUTO:
                self.cts_selected_ids.add(case.case_id)
                self.cts_case_vars.setdefault(case.case_id, tk.BooleanVar()).set(True)
        self._cts_refresh_case_list()

    def _cts_select_all_visible(self) -> None:
        for case in self._cts_visible_cases():
            self.cts_selected_ids.add(case.case_id)
            self.cts_case_vars.setdefault(case.case_id, tk.BooleanVar()).set(True)
        self._cts_refresh_case_list()

    def _cts_clear_selection(self) -> None:
        self.cts_selected_ids.clear()
        for variable in self.cts_case_vars.values():
            variable.set(False)
        self._cts_refresh_case_list()

    def _cts_show_case(self, case_id: str | None) -> None:
        if not case_id or case_id not in self.cts_case_by_id:
            return
        case = self.cts_case_by_id[case_id]
        self.cts_current_case_id = case_id
        status = self.cts_case_status.get(case_id, case.initial_status)
        if case.execution_mode == CONDITIONAL:
            self.cts_cdf_decision_var.set(self.cts_cdf_decisions.get(case_id, "待决定"))
            self.cts_cdf_reason_var.set(self.cts_cdf_reasons.get(case_id, ""))
            self.cts_cdf_decision_combo.configure(state="readonly")
        else:
            self.cts_cdf_decision_var.set("—")
            self.cts_cdf_reason_var.set("")
            self.cts_cdf_decision_combo.configure(state="disabled")
        self.cts_detail_title.configure(text=f"{case.case_id}  |  {case.title}  |  {self._cts_mode_text(case.execution_mode)}  |  {status}")
        sections = [
            ("执行方式与状态", f"{case.execution_mode} / {status}"),
            ("前置条件", case.preconditions),
            ("测试流程", case.flow),
            ("通过条件", case.pass_criteria),
            ("失败条件", case.fail_criteria),
        ]
        if case.execution_mode == CONDITIONAL:
            sections.append(("CDF 决策", f"{self.cts_cdf_decisions.get(case_id, '待决定')}\n依据：{self.cts_cdf_reasons.get(case_id, '') or '未填写'}"))
        if case.limitation:
            sections.append(("自动化边界/注意事项", case.limitation))
        if case.actions:
            action_lines = []
            for action in case.actions:
                if action.get("type") == "send":
                    action_lines.append(f"发送 Opcode 0x{int(action['opcode']):02X}，目的={action.get('destination', 'dut')}，参数={':'.join(f'{int(v):02X}' for v in action.get('params', [])) or '-'}")
                elif action.get("type") == "expect":
                    action_lines.append(f"等待 RX Opcode 0x{int(action['opcode']):02X}，超时 {action.get('timeout_ms', 1000)}ms")
                elif action.get("type") == "expect_none":
                    action_lines.append(f"确认 {action.get('timeout_ms', 1000)}ms 内不出现 RX Opcode 0x{int(action['opcode']):02X}")
                else:
                    action_lines.append(str(action))
            sections.append(("自动化动作", "\n".join(action_lines)))
        result = self.cts_results.get(case_id)
        if result:
            sections.append(("本次执行证据", "\n".join(result.get("evidence", [])) or result.get("message", "")))
        text = "\n\n".join(f"【{heading}】\n{value}" for heading, value in sections)
        self.cts_detail_text.configure(state="normal")
        self.cts_detail_text.delete("1.0", END)
        self.cts_detail_text.insert("1.0", text)
        self.cts_detail_text.configure(state="disabled")

    def _cts_result_selected(self, _event=None) -> None:
        selection = self.cts_result_tree.selection()
        if selection:
            self._cts_show_case(self.cts_result_tree.item(selection[0], "values")[0])

    def _cts_run_selected(self) -> None:
        self._files_show_live()
        if self._wb_quick_busy:
            self.status_var.set("请等待快捷命令发送结束，再运行 CTS")
            return
        if self.backend.occupancy_mask or self.occupancy_busy:
            messagebox.showwarning(APP_NAME, "地址占用实验期间请使用占用页面观察 DUT；运行普通 CTS 项目前先释放占用。")
            return
        if self.cts_running:
            messagebox.showinfo(APP_NAME, "CTS 测试已经在执行。")
            return
        selected = [case for case in self.cts_cases if case.case_id in self.cts_selected_ids]
        if not selected:
            messagebox.showwarning(APP_NAME, "请先勾选至少一个 CTS 测试项目。")
            return
        try:
            dut_la = self._parse_la(self.cts_dut_la_var.get())
            te_la = self._parse_la(self.cts_te_la_var.get())
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"DUT LA/测试端 LA 格式错误：\n{exc}")
            return
        if dut_la == te_la:
            messagebox.showerror(APP_NAME, "DUT LA 和测试端 LA 不能相同。")
            return
        if not self.port_var.get().strip():
            messagebox.showerror(APP_NAME, "CTS 执行需要填写 COM 端口。")
            return
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备，再执行 CTS 测试。")
            return
        self._configure_backend_dll()
        self.cts_results.clear()
        self.cts_case_status = {case.case_id: case.initial_status for case in self.cts_cases}
        self._cts_refresh_result_tree()
        self.cts_stop_event.clear()
        self.cts_running = True
        self.cts_run_started_at = datetime.now().isoformat(timespec="seconds")
        self.cts_status_var.set(f"执行中：0/{len(selected)}，自动项会访问当前 CEC 总线")
        self.status_var.set("CTS 测试执行中")
        self._append_output(f"\n>>> CTS 执行开始：{len(selected)} 项，DUT LA={dut_la:X}，测试端 LA={te_la:X}\n")
        port = self.port_var.get().strip()
        cdf_decisions = dict(self.cts_cdf_decisions)
        cdf_reasons = dict(self.cts_cdf_reasons)
        threading.Thread(target=self._cts_thread, args=(selected, port, dut_la, te_la, cdf_decisions, cdf_reasons), daemon=True).start()

    def _cts_stop_run(self) -> None:
        if self.cts_running:
            self.cts_stop_event.set()
            self.cts_status_var.set("正在停止 CTS 执行…")

    @staticmethod
    def _cts_result_status_text(status: str) -> str:
        return {
            "PASS": "通过",
            "PASS-MANUAL": "通过（人工）",
            "FAIL": "失败",
            "FAIL-MANUAL": "失败（人工）",
            "MANUAL": "待人工",
            "EXTERNAL": "待外部 CTS",
            "CONDITIONAL": "待按 CDF 决定",
            "STOPPED": "已停止",
            "ERROR": "错误",
        }.get(status, status)

    def _cts_thread(self, selected: list[CtsTestCase], port: str, dut_la: int, te_la: int, cdf_decisions: dict[str, str], cdf_reasons: dict[str, str]) -> None:
        error = ""
        try:
            if not self.backend.is_open:
                error = "设备连接已断开，请重新连接后再执行 CTS。"
                return
            address_result = self.backend.set_logical_address(te_la)
            if not address_result.ok:
                error = f"测试端逻辑地址设置失败：{address_result.text}"
                return
            for case in selected:
                if self.cts_stop_event.is_set():
                    break
                result = self._cts_execute_case(case, dut_la, te_la, cdf_decisions, cdf_reasons)
                self.output_queue.put(("cts_case_result", result))
            if self.cts_stop_event.is_set():
                error = "用户停止"
        except (LibCecError, OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
        finally:
            self.output_queue.put(("cts_finished", error))

    def _cts_execute_case(self, case: CtsTestCase, dut_la: int, te_la: int, cdf_decisions: dict[str, str] | None = None, cdf_reasons: dict[str, str] | None = None) -> dict:
        started = time.monotonic()
        evidence: list[str] = []
        capture_start = len(self.capture_records) + 1
        result: dict = {
            "case_id": case.case_id,
            "mode": case.execution_mode,
            "status": "ERROR",
            "message": "",
            "evidence": evidence,
            "capture_start": capture_start,
            "capture_end": capture_start - 1,
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }

        if case.execution_mode == CONDITIONAL:
            decision = (cdf_decisions or {}).get(case.case_id, "待决定")
            reason = (cdf_reasons or {}).get(case.case_id, "")
            evidence.append(f"CDF 决策：{decision}；依据：{reason or '未填写'}")
            if decision == "待决定":
                result.update(status="CONDITIONAL", message="尚未填写 CDF 决策；请先选择适用/不适用并保存。")
                return self._cts_finish_result(result, started)
            if decision == "不适用":
                result.update(status="CONDITIONAL", message=f"CDF 确认不适用：{reason or '未填写排除理由'}")
                return self._cts_finish_result(result, started)
            if not case.actions:
                result.update(status="CONDITIONAL", message="CDF 已确认适用；本项目需按详情人工或外部 CTS 执行。")
                return self._cts_finish_result(result, started)
        if case.execution_mode == EXTERNAL:
            result.update(status="EXTERNAL", message=case.limitation or "需要外部 CTS 设备")
            return self._cts_finish_result(result, started)
        if case.execution_mode in {MANUAL, CONDITIONAL} and not case.actions:
            result.update(status="MANUAL" if case.execution_mode == MANUAL else "CONDITIONAL", message=case.preconditions)
            return self._cts_finish_result(result, started)
        if not case.actions:
            result.update(status="MANUAL", message="本用例尚未配置安全的自动动作，请按详情人工执行。")
            return self._cts_finish_result(result, started)

        cursor = len(self.capture_records)
        try:
            for action in case.actions:
                if self.cts_stop_event.is_set():
                    result.update(status="STOPPED", message="用户停止")
                    break
                kind = action.get("type")
                if kind == "send":
                    frame = self._cts_action_frame(action, dut_la, te_la)
                    send_result = self.backend.transmit(frame, wait_for_ack=bool(action.get("wait_for_ack", True)))
                    evidence.append(f"TX {self._format_frame(frame)} -> {send_result.text}")
                    if not send_result.ok:
                        result.update(status="FAIL", message=send_result.text)
                        break
                elif kind == "command":
                    command_result = self.backend.execute_command(str(action.get("command", "")))
                    evidence.append(f"CMD {action.get('command', '')} -> {command_result.text}")
                    if not command_result.ok:
                        result.update(status="FAIL", message=command_result.text)
                        break
                elif kind == "wait":
                    if self.cts_stop_event.wait(max(0.0, float(action.get("milliseconds", 0)) / 1000.0)):
                        result.update(status="STOPPED", message="用户停止")
                        break
                elif kind in {"expect", "expect_none"}:
                    matched, next_cursor, record = self._cts_wait_for_match(cursor, action, dut_la, te_la)
                    cursor = next_cursor
                    if kind == "expect":
                        if not matched:
                            result.update(status="FAIL", message=f"等待 RX Opcode 0x{int(action['opcode']):02X} 超时")
                            break
                        evidence.append(f"RX {record.get('frame', '')}（匹配期望 Opcode 0x{int(action['opcode']):02X}）")
                    elif matched:
                        result.update(status="FAIL", message=f"出现不应出现的 RX Opcode 0x{int(action['opcode']):02X}")
                        evidence.append(f"异常 RX {record.get('frame', '')}")
                        break
                else:
                    result.update(status="ERROR", message=f"未知自动化动作：{kind}")
                    break
            else:
                result.update(status="PASS", message="全部自动化动作和期望条件满足")
        except (LibCecError, OSError, RuntimeError, ValueError) as exc:
            result.update(status="ERROR", message=str(exc))
        result["capture_end"] = len(self.capture_records)
        return self._cts_finish_result(result, started)

    @staticmethod
    def _cts_finish_result(result: dict, started: float) -> dict:
        result["duration_ms"] = round((time.monotonic() - started) * 1000)
        return result

    @staticmethod
    def _cts_address(value: str | int | None, dut_la: int, te_la: int) -> int | None:
        if value is None:
            return None
        if isinstance(value, int):
            return value & 0x0F
        value = value.lower()
        if value == "dut":
            return dut_la
        if value == "te":
            return te_la
        if value in {"broadcast", "f"}:
            return 0x0F
        return int(value.replace("0x", ""), 16) & 0x0F

    def _cts_action_frame(self, action: dict, dut_la: int, te_la: int) -> list[int]:
        source = self._cts_address(action.get("source", "te"), dut_la, te_la)
        destination = self._cts_address(action.get("destination", "dut"), dut_la, te_la)
        if source is None or destination is None:
            raise ValueError("CTS 自动动作缺少 source/destination")
        return [((source & 0x0F) << 4) | (destination & 0x0F), int(action["opcode"]) & 0xFF] + [int(value) & 0xFF for value in action.get("params", [])]

    def _cts_wait_for_match(self, cursor: int, action: dict, dut_la: int, te_la: int) -> tuple[bool, int, dict]:
        timeout_ms = max(1, int(action.get("timeout_ms", 1000)))
        deadline = time.monotonic() + timeout_ms / 1000.0
        last_record: dict = {}
        while not self.cts_stop_event.is_set():
            records = self.capture_records
            for index in range(cursor, len(records)):
                record = records[index]
                if record.get("direction") != "RX":
                    continue
                last_record = record
                if self._cts_record_matches(record, action, dut_la, te_la):
                    return True, index + 1, record
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with self.capture_condition:
                self.capture_condition.wait(timeout=min(remaining, 0.1))
        return False, len(self.capture_records), last_record

    def _cts_record_matches(self, record: dict, action: dict, dut_la: int, te_la: int) -> bool:
        if record.get("opcode") != int(action.get("opcode", -1)):
            return False
        source = self._cts_address(action.get("source"), dut_la, te_la)
        destination = self._cts_address(action.get("destination"), dut_la, te_la)
        try:
            actual_source = int(str(record.get("source", "0")), 16)
            actual_destination = int(str(record.get("destination", "0")), 16)
        except ValueError:
            return False
        if source is not None and source != actual_source:
            return False
        if destination is not None and destination != actual_destination:
            return False
        prefix = action.get("params_prefix")
        if prefix is not None:
            expected = [int(value) & 0xFF for value in prefix]
            actual = [int(value, 16) for value in str(record.get("params", "")).split(":") if value]
            if actual[:len(expected)] != expected:
                return False
        return True

    def _cts_mark_manual(self, status: str) -> None:
        case_id = self.cts_current_case_id
        if not case_id or case_id not in self.cts_case_by_id:
            messagebox.showwarning(APP_NAME, "请先选择一个测试项目。")
            return
        case = self.cts_case_by_id[case_id]
        if self.cts_running:
            messagebox.showinfo(APP_NAME, "自动执行期间不能修改人工结果。")
            return
        self.cts_results[case_id] = {
            "case_id": case_id,
            "mode": case.execution_mode,
            "status": status,
            "message": "由操作员在真实设备/外部夹具上确认",
            "evidence": ["人工判定：请结合测试流程、抓包记录和外部仪器证据填写正式报告。"],
            "cdf_decision": self.cts_cdf_decisions.get(case_id, "—"),
            "cdf_reason": self.cts_cdf_reasons.get(case_id, ""),
            "capture_start": len(self.capture_records) + 1,
            "capture_end": len(self.capture_records),
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "duration_ms": 0,
        }
        self.cts_case_status[case_id] = self._cts_result_status_text(status)
        self._cts_refresh_case_list()
        self._cts_refresh_result_tree()
        self._cts_show_case(case_id)

    def _cts_refresh_result_tree(self) -> None:
        if not hasattr(self, "cts_result_tree"):
            return
        for item in self.cts_result_tree.get_children():
            self.cts_result_tree.delete(item)
        for case in self.cts_cases:
            result = self.cts_results.get(case.case_id)
            if not result:
                continue
            status = str(result.get("status", ""))
            tag = "MANUAL" if "MANUAL" in status else status.split("-", 1)[0]
            if tag not in {"PASS", "FAIL", "MANUAL", "EXTERNAL", "CONDITIONAL"}:
                tag = "MANUAL"
            self.cts_result_tree.insert("", END, iid=case.case_id, values=(case.case_id, self._cts_mode_text(case.execution_mode), self._cts_result_status_text(status), result.get("message", "")), tags=(tag,))
        summary = {"PASS": 0, "FAIL": 0, "MANUAL": 0, "EXTERNAL": 0, "CONDITIONAL": 0, "OTHER": 0}
        for result in self.cts_results.values():
            status = str(result.get("status", ""))
            if status.startswith("PASS"):
                summary["PASS"] += 1
            elif status.startswith("FAIL") or status == "ERROR":
                summary["FAIL"] += 1
            elif status == "MANUAL":
                summary["MANUAL"] += 1
            elif status == "EXTERNAL":
                summary["EXTERNAL"] += 1
            elif status == "CONDITIONAL":
                summary["CONDITIONAL"] += 1
            else:
                summary["OTHER"] += 1
        self.cts_status_var.set(f"结果：通过 {summary['PASS']} / 失败 {summary['FAIL']} / 人工 {summary['MANUAL']} / 外部 {summary['EXTERNAL']} / 条件 {summary['CONDITIONAL']}")

    def _cts_export_report(self) -> None:
        if not self.cts_results:
            messagebox.showwarning(APP_NAME, "当前没有执行结果可导出。")
            return
        path = filedialog.asksaveasfilename(title="导出 CTS 测试报告", defaultextension=".html", filetypes=[("HTML 报告", "*.html"), ("JSON 报告", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        payload = {
            "application": APP_NAME,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "run_started_at": self.cts_run_started_at,
            "port": self.port_var.get().strip(),
            "cec_dll": self.cec_path_var.get().strip(),
            "dut_logical_address": self.cts_dut_la_var.get().strip(),
            "test_logical_address": self.cts_te_la_var.get().strip(),
            "case_catalogue_count": len(self.cts_cases),
            "selected_count": len(self.cts_selected_ids),
            "cdf_decisions": dict(self.cts_cdf_decisions),
            "cdf_reasons": dict(self.cts_cdf_reasons),
            "results": [],
        }
        for case in self.cts_cases:
            result = self.cts_results.get(case.case_id)
            if not result:
                continue
            item = {
                "case_id": case.case_id,
                "chapter": case.chapter,
                "title": case.title,
                "execution_mode": case.execution_mode,
                "result": result,
                "preconditions": case.preconditions,
                "flow": case.flow,
                "pass_criteria": case.pass_criteria,
                "fail_criteria": case.fail_criteria,
                "limitation": case.limitation,
                "cdf_decision": self.cts_cdf_decisions.get(case.case_id, "—"),
                "cdf_reason": self.cts_cdf_reasons.get(case.case_id, ""),
            }
            payload["results"].append(item)
        try:
            target = Path(path)
            if target.suffix.lower() == ".json":
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            else:
                target.write_text(self._cts_html_report(payload), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"导出 CTS 报告失败：\n{exc}")
            return
        messagebox.showinfo(APP_NAME, f"CTS 报告已导出：\n{path}")

    @staticmethod
    def _cts_html_report(payload: dict) -> str:
        counts = {"通过": 0, "失败": 0, "待人工": 0, "待外部 CTS": 0, "待按 CDF 决定": 0, "其他": 0}
        rows = []
        for item in payload["results"]:
            status_text = CECClientGUI._cts_result_status_text(str(item["result"].get("status", "")))
            counts[status_text if status_text in counts else "其他"] += 1
            result = item["result"]
            evidence = "<br>".join(html.escape(str(line)) for line in result.get("evidence", []))
            rows.append(
                "<tr>"
                f"<td>{html.escape(item['case_id'])}</td>"
                f"<td>{html.escape(item['chapter'])}</td>"
                f"<td>{html.escape(item['title'])}</td>"
                f"<td>{html.escape(item['execution_mode'])}</td>"
                f"<td>{html.escape(status_text)}</td>"
                f"<td>{html.escape(str(item.get('cdf_decision', '—')))}<br>{html.escape(str(item.get('cdf_reason', '') or '未填写'))}</td>"
                f"<td>{html.escape(str(result.get('message', '')))}<br>{evidence}</td>"
                f"<td><details><summary>查看条件</summary><b>流程：</b>{html.escape(item['flow'])}<br><b>通过：</b>{html.escape(item['pass_criteria'])}<br><b>失败：</b>{html.escape(item['fail_criteria'])}<br><b>边界：</b>{html.escape(item.get('limitation', ''))}</details></td>"
                "</tr>"
            )
        summary = "；".join(f"{key} {value}" for key, value in counts.items())
        return "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>CEC CTS 测试报告</title>" \
            "<style>body{font-family:'Segoe UI',sans-serif;margin:24px;color:#202124}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #ccd3da;padding:7px;vertical-align:top}th{background:#eaf1f7}tr:nth-child(even){background:#f8fafb}.meta{background:#f3f6f8;padding:12px;margin:12px 0}code{font-family:Consolas,monospace}</style></head><body>" \
            f"<h1>CEC CTS 测试报告</h1><div class='meta'>生成时间：{html.escape(str(payload['generated_at']))}<br>端口：<code>{html.escape(str(payload['port']))}</code><br>DUT LA：{html.escape(str(payload['dut_logical_address']))}；测试端 LA：{html.escape(str(payload['test_logical_address']))}<br>用例目录：{payload['case_catalogue_count']} 项；本次结果：{len(payload['results'])} 项<br>汇总：{html.escape(summary)}</div>" \
            "<table><thead><tr><th>Case</th><th>章节</th><th>项目</th><th>执行方式</th><th>结果</th><th>CDF 决策/依据</th><th>证据/说明</th><th>流程与判定条件</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></body></html>"

    def _open_advanced_command(self) -> None:
        self._workspace_show_diagnostics(console=True)

    def _bind_events(self) -> None:
        self._wb_bind()
        self.command_tree.bind("<<TreeviewSelect>>", self._command_selected)
        self.command_tree.bind("<Double-1>", lambda _event: self._send_selected())
        self.raw_opcode_var.trace_add("write", self._raw_opcode_changed)
        self.raw_opcode_combo.bind("<<ComboboxSelected>>", self._raw_opcode_changed, add="+")
        self._raw_opcode_changed()
        for variable in (self.raw_source_var, self.raw_destination_var, self.raw_opcode_var, self.raw_params_var):
            variable.trace_add("write", lambda *_: self._sync_raw_builder())
        if not self.settings.get("raw_frame"):
            self._sync_raw_builder()

    def _configure_backend_dll(self) -> None:
        value = self.cec_path_var.get().strip().strip('"')
        if value:
            self.backend.dll_path = Path(value)

    def _backend_open(self, **kwargs: object) -> BackendResult:
        self._configure_backend_dll()
        if "device_types" not in kwargs:
            kwargs["device_types"] = self.types_var.get().strip() or "x"
        if "auto_wake_avr" not in kwargs:
            kwargs["auto_wake_avr"] = self.autowake_var.get() == "1" if self.autowake_var.get() in ("0", "1") else None
        if "autonomous_mode" not in kwargs:
            kwargs["autonomous_mode"] = self.automode_var.get() == "1" if self.automode_var.get() in ("0", "1") else None
        if "get_settings_from_rom" not in kwargs:
            kwargs["get_settings_from_rom"] = bool(self.read_rom_var.get())
        return self.backend.open(self.port_var.get().strip(), **kwargs)

    def _read_simulation_profile(self) -> dict[str, object] | None:
        role = self.sim_role_var.get().strip().lower()
        if role not in {"x", "a", "p", "r", "t"}:
            messagebox.showerror(APP_NAME, "角色必须是 x、a、p、r 或 t。")
            return None
        try:
            source = int(self.sim_source_var.get().strip(), 16)
            physical = int(self.sim_physical_var.get().strip(), 16)
            vendor = int(self.sim_vendor_var.get().strip(), 16)
            version = int(self.sim_version_var.get().strip(), 16)
            power = int(self.sim_power_var.get().strip(), 10)
            volume = int(self.sim_volume_var.get().strip(), 10)
            language = self.sim_language_var.get().strip()
            if not 0 <= source <= 14 or not 0 <= physical <= 0xFFFF or not 0 <= vendor <= 0xFFFFFF:
                raise ValueError
            if not 0 <= version <= 0xFF or not 0 <= power <= 3 or not 0 <= volume <= 100:
                raise ValueError
            if len(language) != 3:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_NAME, "逻辑地址、物理地址、Vendor ID、CEC 版本、电源和音量参数格式不正确。")
            return None
        return {
            "role": role,
            "source": source,
            "physical": physical,
            "vendor": vendor,
            "version": version,
            "power": power,
            "volume": volume,
            "language": language,
        }

    def _connect_device(self) -> None:
        """Open the single shared libCEC connection used by every page."""
        if self.backend.is_open:
            messagebox.showinfo(APP_NAME, "设备已经连接；所有页面正在共用当前 libCEC 会话。")
            return
        self.simulation_active = False
        kwargs: dict[str, object]
        profile: dict[str, object] | None = None
        if self.occupancy_connect_var.get():
            if not self._occupancy_selection_mask():
                messagebox.showerror(APP_NAME, "请在地址占用页面至少选择一个 LA。")
                return
            kwargs = {"role": "x", "monitor_only": True, "autonomous_mode": False,
                      "device_name": "LA Occupancy", "get_settings_from_rom": False}
        elif self.sim_connect_var.get():
            profile = self._read_simulation_profile()
            if profile is None:
                return
            self._append_output(f"\n>>> libCEC API 连接角色模拟：{self.port_var.get().strip()} / {profile['role']} / LA {int(profile['source']):X}\n")
            kwargs = {
                "role": str(profile["role"]),
                "device_types": str(profile["role"]),
                "monitor_only": True,
                "physical_address": int(profile["physical"]),
                "device_name": self.sim_osd_var.get().strip() or "CEC Simulator",
                "vendor_id": int(profile["vendor"]),
            }
        else:
            try:
                types = self.types_var.get().strip().lower()
                role = next((item for item in types if item in {"x", "a", "p", "r", "t"}), "x")
                base = int(self.base_var.get().strip(), 16) if self.base_var.get().strip() else None
                hdmi_port = int(self.hdmi_port_var.get().strip(), 0) if self.hdmi_port_var.get().strip() else None
                vendor = int(self.vendor_id_var.get().strip(), 16) if self.vendor_id_var.get().strip() else None
            except ValueError:
                messagebox.showerror(APP_NAME, "Base 地址、HDMI 端口或 Vendor ID 格式不正确。")
                return
            self._append_output(f"\n>>> libCEC API 连接设备：{self.port_var.get().strip()}\n")
            kwargs = {
                "role": role,
                "monitor_only": self.monitor_only_var.get(),
                "base_device": base,
                "hdmi_port": hdmi_port,
                "device_name": self.osd_name_var.get().strip() or "CEC Tester",
                "vendor_id": vendor,
            }
        result = self._backend_open(**kwargs)
        if not result.ok:
            messagebox.showerror(APP_NAME, f"打开 libCEC 失败：\n{result.text}")
            return
        if self.occupancy_connect_var.get():
            occupied = self.backend.set_address_occupancy(self._occupancy_selection_mask())
            if not occupied.ok:
                self.backend.close()
                messagebox.showerror(APP_NAME, occupied.text)
                return
            self.connection_profile = "occupancy"
            self.connection_role_source = None
            connection_text = "地址占用实验运行中"
        elif profile is not None:
            address_result = self.backend.set_logical_address(int(profile["source"]))
            if not address_result.ok:
                self.backend.close()
                self.connection_kwargs = {}
                self.connection_role_source = None
                messagebox.showerror(APP_NAME, f"设置角色逻辑地址失败：\n{address_result.text}")
                return
            self.simulation_active = True
            self.connection_profile = "role"
            self.connection_role_source = int(profile["source"])
            connection_text = f"角色模拟运行中：{self.port_var.get().strip()}"
        else:
            self.connection_profile = "general"
            self.connection_role_source = None
            connection_text = f"通用连接运行中：{self.port_var.get().strip()}"
        self.connection_kwargs = dict(kwargs)
        monitor_enabled = bool(kwargs.get("monitor_only", False))
        self.raw_monitor_var.set(monitor_enabled)
        self.monitor_state_var.set(f"监听模式：{'开启' if monitor_enabled else '关闭'}")
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.monitor_on_button.configure(state="normal")
        self.monitor_off_button.configure(state="normal")
        self.sim_start_button.configure(state="normal")
        self.status_var.set(connection_text)
        self._occupancy_refresh_state()

    def _start_session(self) -> None:
        """Compatibility wrapper for older settings or external callers."""
        self._connect_device()

    def _start_simulation(self) -> None:
        if self.backend.occupancy_mask or self.occupancy_busy:
            messagebox.showwarning(APP_NAME, "先释放全部地址占用，再应用角色配置。")
            return
        profile = self._read_simulation_profile()
        if profile is None:
            return
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在角色模拟页勾选‘连接时启用角色模拟’，填写参数后点击顶部‘连接设备’。")
            return
        if self.connection_profile != "role":
            messagebox.showwarning(APP_NAME, "当前是通用连接。请先断开设备，在角色模拟页勾选‘连接时启用角色模拟’后重新连接。")
            return
        self.simulation_active = True
        address_result = self.backend.set_logical_address(int(profile["source"]))
        self._append_output(f">>> {address_result.text}\n")
        physical_result = self.backend.set_physical_address(int(profile["physical"]))
        self._append_output(f">>> {physical_result.text}\n")
        if not address_result.ok or not physical_result.ok:
            self.simulation_active = False
            messagebox.showerror(APP_NAME, "角色配置应用失败，请检查逻辑地址或物理地址。")
            return
        self.status_var.set(f"角色模拟运行中：{profile['role']} / LA {int(profile['source']):X}")
        self._append_output(">>> 角色回复已启用；设备连接仍由窗口顶部统一管理。\n")

    def _apply_simulation_address(self) -> None:
        if not self.simulation_active or not self.backend.is_open:
            return
        self.backend.set_logical_address(int(self.sim_source_var.get().strip(), 16))
        self.backend.set_physical_address(int(self.sim_physical_var.get().strip(), 16))

    def _stop_simulation(self) -> None:
        self.simulation_active = False
        if self.backend.is_open:
            self.status_var.set("角色模拟已停止；设备仍保持连接")
            self._append_output(">>> 角色自动回复已停止，设备连接仍保持。\n")

    def _handle_backend_event(self, event: dict[str, object]) -> None:
        kind = str(event.get("kind", ""))
        if kind == "occupancy":
            mask = self.backend.occupancy_mask
            if mask:
                self.occupancy_observed_mask = mask
            self.raw_monitor_var.set(True)
            self.monitor_state_var.set("监听模式：开启")
            self.capture_show_poll_var.set(True)
            self._refresh_capture_tree()
            self._occupancy_refresh_state()
            return
        if kind == "log":
            level = event.get("level", 0)
            text = str(event.get("text", ""))
            log_line = f"[L{level}] {text}\n"
            self._append_event_file(self.log_file_var.get(), log_line)
            try:
                mask = int(self.log_level_var.get())
            except ValueError:
                mask = 31
            if int(level) and not (int(level) & mask):
                return
            self._append_output(f"[libCEC L{level}] {text}\n")
            return
        if kind == "rx_notification":
            frame = [int(value) & 0xFF for value in event.get("frame", [])]  # type: ignore[arg-type]
            if frame and self.simulation_active:
                # The role simulator consumes the native commandReceived
                # notification. The evidence page consumes rawFrame, which
                # is emitted once from libCEC's receive/transmit paths.
                self._simulation_handle_frame(frame)
            return
        if kind == "raw_frame":
            frame = [int(value) & 0xFF for value in event.get("frame", [])]  # type: ignore[arg-type]
            if not frame:
                return
            direction = str(event.get("direction", "RX")).upper()
            if direction not in {"RX", "TX"}:
                direction = "RX"
            status = self._capture_status_text(direction, frame, event)
            raw_timestamp = event.get("raw_timestamp")
            try:
                raw_timestamp_ms = int(raw_timestamp) if raw_timestamp is not None else None
            except (TypeError, ValueError):
                raw_timestamp_ms = None
            self._capture_backend_frame(direction, frame, status, raw_timestamp_ms)
            # Experiment widgets must never stop the common native capture pump.
            try:
                self._occupancy_observe(frame, event, status, raw_timestamp_ms)
            except Exception as exc:
                self._report_ui_error("地址占用实验观测", exc)
            frame_line = f"{direction} {self._format_frame(frame)}" + (f" -> {status}" if direction == "TX" else "") + "\n"
            self._append_event_file(self.short_log_file_var.get(), frame_line)
            self._append_output(frame_line)
            return
        if kind == "alert":
            self._append_output(f"[libCEC ALERT] {event.get('text', '')}\n")
            return
        if kind == "error":
            self._append_output(f"[libCEC ERROR] {event.get('text', '')}\n")
            return
        if kind == "state":
            connected = bool(event.get("connected", False))
            # A reconnect queues a close event followed by an open event. If
            # the backend is already open, the close notification is stale
            # and must not clear the new connection profile or monitor state.
            if not connected and not self.backend.is_open:
                self.connection_profile = ""
                self.connection_kwargs = {}
                self.connection_role_source = None
                self.simulation_active = False
                self.raw_send_busy = False
                self.raw_monitor_var.set(False)
                self.monitor_state_var.set("监听模式：未连接")
                self.start_button.configure(state="normal")
                self.stop_button.configure(state="disabled")
                self.monitor_on_button.configure(state="disabled")
                self.monitor_off_button.configure(state="disabled")
                self.sim_start_button.configure(state="normal")
                self.occupancy_observed_mask = 0
                self._occupancy_refresh_state()
            self.status_var.set(str(event.get("text", "")))
            return
        if kind == "scan":
            devices = event.get("devices", [])
            if isinstance(devices, list):
                self.topology_devices = devices
                self._refresh_topology_tree()
            return

    def _append_event_file(self, path_text: str, text: str) -> None:
        self.log_writer.write(path_text, text)

    @staticmethod
    def _format_ui_time_ms() -> str:
        """Format a local UI event time with millisecond precision."""
        return datetime.now().strftime("%H:%M:%S.%f")[:-3]

    def _format_capture_time(self, raw_timestamp_ms: int | None) -> str:
        """Convert libCEC's monotonic millisecond timestamp for display.

        The wall-clock value is only a presentation value.  The original
        monotonic timestamp is stored separately so exports and comparisons
        do not depend on the PC clock being adjusted.
        """
        if raw_timestamp_ms is None:
            return self._format_ui_time_ms()
        now_wall = time.time()
        anchor = self.capture_timestamp_anchor
        if anchor is None or raw_timestamp_ms < anchor[0]:
            anchor = (raw_timestamp_ms, now_wall)
            self.capture_timestamp_anchor = anchor
        anchor_timestamp_ms, anchor_wall = anchor
        try:
            return datetime.fromtimestamp(
                anchor_wall + (raw_timestamp_ms - anchor_timestamp_ms) / 1000.0
            ).strftime("%H:%M:%S.%f")[:-3]
        except (OverflowError, OSError, ValueError):
            return self._format_ui_time_ms()

    @staticmethod
    def _capture_theoretical_duration_ms(frame: list[int]) -> float:
        """Return the nominal on-wire duration of one CEC frame.

        A CEC frame consists of a 4.5 ms nominal start bit followed by one
        10-bit header/data block per raw byte.  Each block has a nominal
        2.4 ms bit period, including EOM and ACK/NACK.  ``frame`` includes
        the header byte, so a polling frame has one byte and no opcode.
        This is a protocol calculation, not an edge timestamp measured from
        the adapter's physical CEC line.
        """
        return round(4.5 + len(frame) * 10 * 2.4, 1)

    @staticmethod
    def _capture_duration_text(value: object) -> str:
        if isinstance(value, (int, float)):
            return f"{float(value):.1f} ms"
        return "—"

    @staticmethod
    def _capture_status_text(direction: str, frame: list[int], event: dict[str, object]) -> str:
        """Return the ACK result shown in the capture table.

        ``cec_command.ack`` is the physical CEC ACK-bit level, not the
        semantic result.  The CEC bus uses active-low acknowledgement for a
        directed frame (0 means ACK), while the ACK polarity is reversed for
        a broadcast frame (1 means no follower rejected it).  TX statuses
        from the structured libCEC callback are already normalized by the
        Pulse-Eight adapter, but broadcast context is retained in the label.
        """
        native_status = str(event.get("raw_status_text", "")).strip().upper()
        is_broadcast = bool(frame) and (frame[0] & 0x0F) == 0x0F

        if native_status == "ERROR":
            return "ERROR"

        if direction == "TX":
            if native_status == "ACK":
                return "ACK（广播通过）" if is_broadcast else "ACK"
            if native_status == "NACK":
                return "NACK（广播拒绝）" if is_broadcast else "NACK"
            # SENT means that libCEC reported transmission without a final
            # ACK result (for example, fire-and-forget).  Do not infer one.
            return "—"

        try:
            ack_bit = int(event.get("ack", -1))
        except (TypeError, ValueError):
            ack_bit = -1
        if ack_bit not in (0, 1):
            return "—"

        # Directed frames are active-low: the addressed follower pulls the
        # ACK bit low.  Broadcast frames use the opposite polarity: a high
        # bit means no follower rejected the broadcast, while low means that
        # at least one follower rejected it.
        acknowledged = (ack_bit == 1) if is_broadcast else (ack_bit == 0)
        if acknowledged:
            return "ACK（广播通过）" if is_broadcast else "ACK"
        return "NACK（广播拒绝）" if is_broadcast else "NACK"

    def _capture_backend_frame(
        self,
        direction: str,
        frame: list[int],
        status: str,
        raw_timestamp_ms: int | None = None,
    ) -> None:
        live = self._files_live if self._files_mode == "OFFLINE" else None
        records = live["records"] if live else self.capture_records
        source = (frame[0] >> 4) & 0x0F
        destination = frame[0] & 0x0F
        opcode = frame[1] if len(frame) > 1 else None
        theoretical_duration_ms = self._capture_theoretical_duration_ms(frame)
        previous_record = records[-1] if records else None
        callback_interval_ms: float | None = None
        estimated_gap_ms: float | None = None
        if previous_record is not None and isinstance(raw_timestamp_ms, (int, float)):
            previous_timestamp = previous_record.get("timestamp_ms")
            if isinstance(previous_timestamp, (int, float)):
                callback_interval_ms = round(float(raw_timestamp_ms) - float(previous_timestamp), 1)
                # Compare with the current row's theoretical duration.  The
                # displayed value is only a callback-level residual; it is
                # not a physical CEC line idle-time measurement.
                estimated_gap_ms = round(callback_interval_ms - theoretical_duration_ms, 1)
        record = {
            "seq": len(records) + 1,
            "time": self._format_capture_time(raw_timestamp_ms),
            "timestamp_ms": raw_timestamp_ms,
            "direction": direction,
            "frame": self._format_frame(frame),
            "frame_bytes": len(frame),
            "theoretical_duration_ms": theoretical_duration_ms,
            "callback_interval_ms": callback_interval_ms,
            "estimated_gap_ms": estimated_gap_ms,
            "source": f"{source:X}",
            "destination": f"{destination:X}",
            "opcode": opcode,
            "opcode_text": "POLL" if opcode is None else f"0x{opcode:02X} {self._opcode_name(opcode)}",
            "opcode_explanation": "轮询帧，无 Opcode" if opcode is None else self._opcode_explanation(opcode),
            "params": self._format_frame(frame[2:]) if len(frame) > 2 else "",
            "params_explanation": self._decode_opcode_parameters(opcode, frame[2:]),
            "status": status,
        }
        records.append(record)
        if live:
            live["map"][record["seq"]] = record
            self._files_record_added(record, live)
        else:
            self._wb_on_record(record)
        self.capture_last_frame_at = time.monotonic()
        if direction == "TX":
            self.capture_last_tx = record
        try:
            if not live:
                self._append_capture_record_to_tree(record)
        except Exception as exc:
            self._capture_display_dirty = True
            self._report_ui_error("报文表格增量更新", exc)
        with self.capture_condition:
            self.capture_condition.notify_all()

    def _poll_output(self) -> None:
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        if self._closing:
            return
        try:
            # Leave time for Tk painting, scrolling and input even under a burst.
            # Drain one event at a time: a bad event cannot discard a pre-drained batch.
            deadline = time.perf_counter() + 0.015
            for _ in range(100):
                events = self.backend.drain_events(limit=1)
                if not events:
                    break
                event = events[0]
                try:
                    self._handle_backend_event(event)
                except Exception as exc:
                    self._report_ui_error(f"原生事件 {event.get('kind', '?')}", exc)
                if time.perf_counter() >= deadline:
                    break
            self._drain_output_queue()
            if self._capture_display_dirty and time.monotonic() >= self._capture_display_retry_at:
                self._capture_display_retry_at = time.monotonic() + 1.0
                self._refresh_capture_tree()
            self._update_capture_refresh_state()
        except Exception as exc:
            self._report_ui_error("界面消息轮询", exc)
        finally:
            # Reschedule even when an auxiliary widget/decoder/output task failed.
            if not self._closing:
                busy = self.backend.pending_event_count or not self.output_queue.empty()
                self._poll_after_id = self.after(10 if busy else 80, self._poll_output)

    def _report_ui_error(self, context: str, error: Exception) -> None:
        self.gui_error_count += 1
        details = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        try:
            self.log_writer.write(self.gui_error_path, f"\n[{datetime.now().isoformat(timespec='milliseconds')}] {context}\n{details}")
            self.status_var.set(f"界面异常已记录，继续接收：{context}")
        except (tk.TclError, OSError):
            pass

    def report_callback_exception(self, exc_type, error, tb) -> None:
        if hasattr(self, "capture_tree"):
            self._capture_display_dirty = True
        self._report_ui_error("Tk 界面回调", error.with_traceback(tb))

    def _capture_pause_changed(self) -> None:
        if not self.capture_paused_var.get():
            self._refresh_capture_tree()
        self._update_capture_refresh_state()

    def _update_capture_refresh_state(self) -> None:
        self._wb_update_search_status()
        self._files_update_summary()
        if self._files_mode == "OFFLINE":
            self.capture_refresh_state_var.set(
                f"离线查看：{self._files_path.name if self._files_path else '已载入文件'}；本文件 {len(self.capture_records)} 条；"
                f"后台实时记录 {len(self._files_live['records']) if self._files_live else 0} 条；! 表示非法数据块")
            return
        if self.capture_paused_var.get():
            mode = "显示已暂停（仍在记录）"
        elif self._capture_follow_suspended:
            mode = "浏览定位（返回最新或滚到底部恢复跟随）"
        elif self.capture_tree.is_at_bottom():
            mode = "跟随最新"
        else:
            mode = "浏览历史（滚到底部恢复跟随）"
        recent = ("尚无报文" if self.capture_last_frame_at is None else
                  f"最近处理帧 {time.monotonic() - self.capture_last_frame_at:.1f}s 前")
        errors = f"；界面异常 {self.gui_error_count} 次（已记录）" if self.gui_error_count else ""
        self.capture_refresh_state_var.set(
            f"{mode}；累计记录 {len(self.capture_records)} 帧；待处理事件 {self.backend.pending_event_count}；{recent}{errors}")

    def _drain_output_queue(self) -> None:
        deadline = time.perf_counter() + 0.008
        try:
            for _ in range(100):
                if time.perf_counter() >= deadline:
                    break
                kind, value = self.output_queue.get_nowait()
                if self._wb_handle_output(kind, value):
                    continue
                if kind == "script_command":
                    self._send_command(str(value))
                elif kind == "raw_command":
                    if self.backend.is_open:
                        self._send_command(str(value))
                elif kind == "raw_done":
                    self.raw_send_busy = False
                    self.status_var.set("Raw 报文发送完成")
                elif kind == "occupancy_done":
                    action, result = value
                    self.occupancy_busy = False
                    for button in self.occupancy_buttons:
                        button.configure(state="normal")
                    self._occupancy_refresh_state()
                    if self.backend.monitor_only:
                        self.raw_monitor_var.set(True)
                        if not self.backend.occupancy_mask:
                            self.monitor_state_var.set("监听模式：开启")
                    self._occupancy_record(action, "成功" if result.ok else "失败", result.text)
                    self._append_output(f">>> 地址占用 {action}: {result.text}\n")
                    if not result.ok:
                        messagebox.showerror(APP_NAME, result.text)
                elif kind == "scenario_command":
                    if self.backend.is_open:
                        self._send_command(str(value))
                elif kind == "scenario_log":
                    self._append_scenario_log(str(value))
                elif kind == "scenario_status":
                    self.scenario_status_var.set(str(value))
                elif kind == "log_writer_error":
                    self._append_output(f"[日志写入错误] {value}\n")
                elif kind == "cts_case_result":
                    result = value if isinstance(value, dict) else {}
                    case_id = str(result.get("case_id", ""))
                    if case_id:
                        self.cts_results[case_id] = result
                        status = str(result.get("status", ""))
                        self.cts_case_status[case_id] = self._cts_result_status_text(status)
                        self._cts_refresh_case_list()
                        self._cts_refresh_result_tree()
                        self._cts_show_case(case_id)
                        self._append_output(f"CTS {case_id}: {self._cts_result_status_text(status)} - {result.get('message', '')}\n")
                elif kind == "cts_finished":
                    error = str(value or "")
                    self.cts_running = False
                    if error == "用户停止":
                        self.cts_status_var.set("CTS 执行已停止")
                    elif error:
                        self.cts_status_var.set(f"CTS 执行结束，但会话错误：{error}")
                        self._append_output(f"CTS 执行错误：{error}\n")
                    else:
                        self.cts_status_var.set("CTS 执行完成，可导出报告")
                    self.status_var.set("未连接" if not self.backend.is_open else "libCEC 会话运行中")
                elif kind == "topology_result":
                    devices, error = value  # type: ignore[misc]
                    self.topology_scan_pending = False
                    self.oneshot_busy = False
                    if error:
                        self._append_output(f">>> 拓扑扫描失败：{error}\n")
                    else:
                        self.topology_devices = list(devices)
                        self._refresh_topology_tree()
                        self._append_output(f">>> 拓扑扫描完成：{len(devices)} 个设备\n")
                    self.status_var.set("未连接" if not self.backend.is_open else "libCEC 会话运行中")
                elif kind == "utility":
                    title, result = value  # type: ignore[misc]
                    self._append_output(f"{title}：{result.text}\n")
                    self.oneshot_busy = False
                    self.status_var.set("未连接" if not self.backend.is_open else "libCEC 会话运行中")
                elif kind == "oneshot":
                    result = value if isinstance(value, BackendResult) else BackendResult(True, str(value))
                    self._append_output(f"{result.text}\n")
                    if self.topology_scan_pending:
                        self.topology_scan_pending = False
                    self.oneshot_busy = False
                    self.status_var.set("未连接" if not self.backend.is_open else "libCEC 会话运行中")
        except queue.Empty:
            pass

    def _capture_record_matches_filter(self, record: dict) -> bool:
        return self._wb_base_matches(record) and (
            self._wb_applied_mode == "FIND" or self.wb_query.matches(record, self._wb_annotation(record)))

    def _capture_highlight_opcodes(self) -> set[int]:
        return set(self._capture_highlight_tags)

    def _capture_highlight_tag(self, opcode: object) -> str:
        return self._capture_highlight_tags.get(opcode, "") if isinstance(opcode, int) else ""

    def _capture_record_values_and_tags(self, record: dict) -> tuple[tuple[object, ...], tuple[str, ...]]:
        source = str(record.get("source", "")).strip().upper()
        row_tag = f"capture_source_{source}" if source in "0123456789ABCDEF" else "capture_source_unknown"
        tags = [row_tag]
        if record.get("length_status") == "ERROR":
            tags.append("capture_length_error")
        highlight_tag = self._capture_highlight_tag(record.get("opcode"))
        if highlight_tag:
            tags.append(highlight_tag)
        direction = str(record.get("direction", "")).strip().upper()
        if direction in {"TX", "RX"}:
            tags.append(f"capture_direction_{direction}")
        values = (
            record["seq"], self._wb_record_mark(record), record["time"], record["source"], record["destination"], record["frame"],
            self._capture_duration_text(record.get("theoretical_duration_ms")),
            self._capture_duration_text(record.get("callback_interval_ms")),
            self._capture_duration_text(record.get("estimated_gap_ms")),
            record["opcode_text"],
            record["opcode_explanation"], record["params"], record["params_explanation"],
            record["status"],
        )
        return values, tuple(tags)

    def _update_capture_summary(self) -> None:
        visible_count = len(self.capture_tree.get_children()) if hasattr(self, "capture_tree") else 0
        self.capture_summary_var.set(
            f"当前显示 {visible_count} 条：TX {self.capture_visible_tx_count} / RX {self.capture_visible_rx_count}"
            + ("（仅非法数据块）" if self._files_errors_only.get() else "")
        )

    def _append_capture_record_to_tree(self, record: dict) -> None:
        """Append one matching frame without rebuilding the complete capture table."""
        if not hasattr(self, "capture_tree") or self.capture_paused_var.get():
            return
        if not self._capture_record_matches_filter(record):
            return
        follow_tail = self.capture_tree.is_at_bottom() and not self._capture_follow_suspended
        values, tags = self._capture_record_values_and_tags(record)
        self.capture_tree.insert("", END, values=values, tags=tags)
        direction = str(record.get("direction", ""))
        if direction == "TX":
            self.capture_visible_tx_count += 1
        elif direction == "RX":
            self.capture_visible_rx_count += 1

        children = self.capture_tree.get_children()
        while len(children) > self.capture_display_limit:
            selected = set(self.capture_tree.selection())
            oldest = next((item for item in children if item not in selected), children[0])
            old_tags = self.capture_tree.item(oldest, "tags")
            if "capture_direction_TX" in old_tags:
                self.capture_visible_tx_count -= 1
            elif "capture_direction_RX" in old_tags:
                self.capture_visible_rx_count -= 1
            self.capture_tree.delete(oldest)
            children = self.capture_tree.get_children()
        if follow_tail:
            self.capture_tree.see_end()
        self._update_capture_summary()

    def _refresh_capture_tree(self, *, force: bool = False) -> None:
        if not hasattr(self, "capture_tree") or (self.capture_paused_var.get() and not force):
            return
        selected_sequence = self._capture_selected_sequence()
        selected_sequences = [int(self.capture_tree.item(item, "values")[0]) for item in self.capture_tree.selection()]
        follow_tail = self.capture_tree.is_at_bottom() and not self._capture_follow_suspended
        previous_view = self.capture_tree.yview()
        self.capture_tree.begin_update()
        try:
            self.capture_tree.delete(*self.capture_tree.get_children())
            self.capture_visible_tx_count = 0
            self.capture_visible_rx_count = 0
            sequence_items: dict[int, str] = {}
            candidates = [record for record in self.capture_records if self._capture_record_matches_filter(record)]
            visible = candidates[-self.capture_display_limit:]
            pinned = [self._wb_records[seq] for seq in selected_sequences if seq in self._wb_records
                      and self._capture_record_matches_filter(self._wb_records[seq])][-self.capture_display_limit:]
            pinned_ids = {r["seq"] for r in pinned}
            remaining = self.capture_display_limit - len(pinned)
            tail = [r for r in candidates if r["seq"] not in pinned_ids]
            visible = sorted([*pinned, *(tail[-remaining:] if remaining else [])], key=lambda r: int(r["seq"]))
            for record in visible:
                if record.get("direction") == "TX":
                    self.capture_visible_tx_count += 1
                elif record.get("direction") == "RX":
                    self.capture_visible_rx_count += 1
                values, tags = self._capture_record_values_and_tags(record)
                item = self.capture_tree.insert(
                    "",
                    END,
                    values=values,
                    tags=tags,
                )
                sequence_items[int(record["seq"])] = item
            selected_items = tuple(sequence_items[seq] for seq in selected_sequences if seq in sequence_items)
            if selected_items:
                self.capture_tree.selection_set(selected_items)
                self.capture_tree.focus(sequence_items.get(selected_sequence, selected_items[0]))
        finally:
            self.capture_tree.end_update()
        if follow_tail:
            self.capture_tree.see_end()
        else:
            self.capture_tree.yview("moveto", previous_view[0])
        self._capture_display_dirty = False
        self._update_capture_summary()

    def _capture_user_yview(self, *args) -> None:
        self.capture_tree.yview(*args)
        self._capture_user_scrolled()

    def _capture_user_scrolled(self) -> None:
        # Only an explicit user scroll to the bottom resumes tail following.
        # Programmatic centering must not undo a navigation hold.
        self._capture_follow_suspended = not self.capture_tree.is_at_bottom()
        self._update_capture_refresh_state()

    def _capture_selected_sequence(self) -> int | None:
        selection = self.capture_tree.selection()
        if not selection:
            return None
        values = self.capture_tree.item(selection[0], "values")
        columns = tuple(self.capture_tree["columns"])
        try:
            sequence_index = columns.index("seq")
            return int(values[sequence_index])
        except (ValueError, TypeError, IndexError, tk.TclError):
            return None

    def _capture_context_menu(self, event):
        """Select the clicked row; explicit actions retain its message snapshot."""
        item = self.capture_tree.identify_row(event.y)
        if not item:
            return "break"
        if item not in self.capture_tree.selection():
            self.capture_tree.selection_set(item)
        self.capture_tree.focus(item)
        self.capture_context_item = item
        self.capture_context_column = self.capture_tree.identify_column(event.x)

        record = self._capture_context_record()
        snapshot = dict(record) if record else None
        menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="查看报文详情…", command=lambda: self._capture_show_details(record=snapshot),
                         state="normal" if snapshot else "disabled")
        menu.add_command(label="载入 Raw 注入", command=lambda: self._capture_reuse_frame(snapshot),
                         state="normal" if snapshot else "disabled")
        menu.add_command(label="查找相同 Opcode", command=lambda: self._wb_find_same_opcode(snapshot),
                         state="normal" if snapshot and (snapshot.get("opcode") is not None
                                                          or snapshot.get("is_poll") is not False) else "disabled")
        menu.add_separator()
        menu.add_command(label="批量提取/导出选中行…", command=lambda: self._files_choose_export(scope="SELECTED"))
        menu.add_command(label="上一非法数据块", command=lambda: self._files_navigate_error(-1))
        menu.add_command(label="下一非法数据块", command=lambda: self._files_navigate_error(1))
        menu.add_separator()
        menu.add_command(label="复制当前单元格", command=self._copy_capture_cell)
        menu.add_command(label="复制整行（TSV）", command=self._copy_capture_current_row)
        menu.add_command(label="复制整行（含列名）", command=self._copy_capture_current_row_with_header)
        menu.add_separator()
        menu.add_command(label="复制报文", command=self._copy_capture_frame)
        menu.add_separator()
        is_bookmark = bool(record and self._wb_annotation(record).get("bookmark"))
        menu.add_command(label="取消书签" if is_bookmark else "添加书签", command=self._wb_toggle_bookmark)
        menu.add_command(label="编辑注释与标签…", command=self._wb_edit_annotation)
        menu.add_separator()
        menu.add_command(label="设为时间点 1（起点）", command=lambda: self._mark_capture_timepoint(1))
        menu.add_command(label="设为时间点 2（终点）", command=lambda: self._mark_capture_timepoint(2))
        menu.add_command(
            label="计算两条消息时间间隔",
            command=self._calculate_capture_interval,
            state="normal" if len(self.capture_interval_marks) == 2 else "disabled",
        )
        menu.add_command(label="清除时间点标记", command=self._clear_capture_timepoints)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    def _capture_context_values(self) -> tuple[str, tuple[object, ...]] | None:
        item = self.capture_context_item
        if not item:
            selection = self.capture_tree.selection()
            item = selection[0] if selection else None
        if not item:
            return None
        values = self.capture_tree.item(item, "values")
        if not values:
            return None
        return item, tuple(values)

    def _capture_context_record(self) -> dict | None:
        context = self._capture_context_values()
        if context is None:
            return None
        item, values = context
        del item
        columns = tuple(self.capture_tree["columns"])
        try:
            sequence_index = columns.index("seq")
            sequence = int(values[sequence_index])
        except (ValueError, TypeError):
            return None
        for record in self.capture_records:
            if int(record.get("seq", -1)) == sequence:
                return record
        return None

    def _refresh_capture_interval_display(self) -> None:
        labels = []
        for slot in (1, 2):
            sequence = self.capture_interval_marks.get(slot)
            labels.append(f"消息{slot} #{sequence}" if sequence is not None else f"消息{slot} —")
        marked = self._capture_marked_records()
        if marked is not None:
            first, second = marked
            first_timestamp = first.get("timestamp_ms")
            second_timestamp = second.get("timestamp_ms")
            if isinstance(first_timestamp, (int, float)) and isinstance(second_timestamp, (int, float)):
                delta = second_timestamp - first_timestamp
                self.capture_interval_var.set(
                    "时间间隔："
                    + " / ".join(labels)
                    + f"  |  消息2 - 消息1：{delta:+.0f} ms（绝对值 {abs(delta):.0f} ms）"
                )
                return
        self.capture_interval_var.set("时间间隔：" + " / ".join(labels))

    def _mark_capture_timepoint(self, slot: int) -> None:
        record = self._capture_context_record()
        if record is None:
            return
        self.capture_interval_marks[slot] = int(record["seq"])
        self._refresh_capture_interval_display()

    def _capture_marked_records(self) -> tuple[dict, dict] | None:
        first_sequence = self.capture_interval_marks.get(1)
        second_sequence = self.capture_interval_marks.get(2)
        if first_sequence is None or second_sequence is None:
            return None
        records = {int(record.get("seq", -1)): record for record in self.capture_records}
        first = records.get(first_sequence)
        second = records.get(second_sequence)
        return (first, second) if first is not None and second is not None else None

    def _calculate_capture_interval(self) -> None:
        marked = self._capture_marked_records()
        if marked is None:
            messagebox.showwarning(APP_NAME, "请先右键两条消息，分别设为时间点 1 和时间点 2。")
            return
        first, second = marked
        first_timestamp = first.get("timestamp_ms")
        second_timestamp = second.get("timestamp_ms")
        if not isinstance(first_timestamp, (int, float)) or not isinstance(second_timestamp, (int, float)):
            messagebox.showwarning(APP_NAME, "所选消息没有可用的 libCEC 原生时间戳，无法计算间隔。")
            return
        delta = second_timestamp - first_timestamp
        self.capture_interval_var.set(f"时间间隔：{delta:+.0f} ms（绝对值 {abs(delta):.0f} ms）")
        messagebox.showinfo(
            APP_NAME,
            f"消息 1（起点）\n#{first['seq']}  {first['time']}  {first['frame']}\n\n"
            f"消息 2（终点）\n#{second['seq']}  {second['time']}  {second['frame']}\n\n"
            f"时间间隔（消息 2 - 消息 1）：{delta:+.0f} ms\n"
            f"绝对时间间隔：{abs(delta):.0f} ms",
        )

    def _clear_capture_timepoints(self) -> None:
        self.capture_interval_marks.clear()
        self._refresh_capture_interval_display()

    def _copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update_idletasks()

    def _copy_capture_cell(self) -> None:
        context = self._capture_context_values()
        column = self.capture_context_column
        if context is None or not column or not column.startswith("#"):
            return
        column_index = int(column[1:]) - 1
        if column_index < 0 or column_index >= len(context[1]):
            return
        value = str(context[1][column_index])
        columns = tuple(self.capture_tree["columns"])
        if column_index < len(columns) and columns[column_index] == "opcode":
            value = value.removeprefix("★ ")
        self._copy_to_clipboard(value)

    def _copy_capture_current_row(self, _event=None) -> str:
        selection = self.capture_tree.selection()
        if len(selection) > 1:
            self._copy_to_clipboard("\n".join("\t".join(self._capture_clean_display_values(
                self.capture_tree.item(item, "values"))) for item in selection))
            return "break"
        context = self._capture_context_values()
        if context is not None:
            self._copy_to_clipboard("\t".join(self._capture_clean_display_values(context[1])))
        return "break"

    def _capture_select_all(self, _event=None) -> str:
        self.capture_tree.selection_set(self.capture_tree.get_children())
        return "break"

    def _copy_capture_current_row_with_header(self) -> None:
        context = self._capture_context_values()
        if context is None:
            return
        columns = tuple(self.capture_tree["columns"])
        headers = tuple(self.capture_tree.heading(column, "text") for column in columns)
        row = "\t".join(self._capture_clean_display_values(context[1]))
        self._copy_to_clipboard("\t".join(headers) + "\n" + row)

    def _capture_clean_display_values(self, values: tuple[object, ...]) -> tuple[str, ...]:
        columns = tuple(self.capture_tree["columns"])
        cleaned = [str(value) for value in values]
        try:
            opcode_index = columns.index("opcode")
        except ValueError:
            return tuple(cleaned)
        if opcode_index < len(cleaned):
            cleaned[opcode_index] = cleaned[opcode_index].removeprefix("★ ")
        return tuple(cleaned)

    def _copy_capture_frame(self) -> None:
        context = self._capture_context_values()
        if context is None:
            return
        columns = tuple(self.capture_tree["columns"])
        try:
            frame_index = columns.index("frame")
        except ValueError:
            return
        self._copy_to_clipboard(str(context[1][frame_index]))

    def _clear_capture(self) -> None:
        self.capture_records.clear()
        self._wb_clear_session()
        self.capture_last_frame_at = None
        self._capture_display_dirty = False
        self.capture_interval_marks.clear()
        self.capture_visible_tx_count = 0
        self.capture_visible_rx_count = 0
        if hasattr(self, "capture_tree"):
            self.capture_tree.begin_update()
            try:
                self.capture_tree.delete(*self.capture_tree.get_children())
            finally:
                self.capture_tree.end_update()
        self.capture_summary_var.set("当前显示 0 条：TX 0 / RX 0")
        self._refresh_capture_interval_display()

    def _capture_details_content(self, record: dict) -> str:
        """Use the stored decode results; no device queries or new traffic."""
        annotation = self._wb_annotation(record)
        la_names = dict(LA_CHOICES)
        source = str(record.get("source", "—"))
        destination = str(record.get("destination", "—"))
        timestamp = record.get("timestamp_ms")
        timestamp_text = f"{timestamp} ms" if isinstance(timestamp, (int, float)) else "—"
        fields = (
            ("消息编号", f"#{record.get('seq', '—')}"),
            ("时间", record.get("time", "—")),
            ("方向", record.get("direction", "—")),
            ("源 LA", f"{source} — {la_names.get(source.upper(), '未知')}"),
            ("目标 LA", f"{destination} — {la_names.get(destination.upper(), '未知')}"),
            ("完整报文", record.get("frame", "")),
            ("Opcode", record.get("opcode_text", "—")),
            ("ACK 状态", record.get("status", "—")),
            ("Opcode 解释", record.get("opcode_explanation", "—")),
            ("参数（Raw）", record.get("params") or "无参数"),
            ("参数解释", record.get("params_explanation", "—")),
            ("原生回调时间戳", timestamp_text),
            ("理论帧时长", self._capture_duration_text(record.get("theoretical_duration_ms"))),
            ("相邻回调间隔", self._capture_duration_text(record.get("callback_interval_ms"))),
            ("回调间隔 − 本帧理论时长", self._capture_duration_text(record.get("estimated_gap_ms"))),
            ("书签", "是" if annotation.get("bookmark") else "否"),
            ("标签", ", ".join(annotation.get("tags", [])) or "—"),
            ("注释", annotation.get("note") or "—"),
        )
        groups = (fields[:8], fields[8:11], fields[11:15], fields[15:])
        content = "\n\n".join("\n".join(f"{label}：{value}" for label, value in group) for group in groups)
        content += f"\n\n长度检查：{record.get('length_status', 'NOT_COVERED')}\n{record.get('length_message', '暂无检查结果')}"
        if self.wb_query.has_conditions or not self._wb_query_valid:
            content += "\n\n查找命中原因：" + self._wb_reason_for_record(record)
        if "origin_seq" in record:
            content += f"\n\n离线导入前编号：{record['origin_seq']}\n原会话：{record.get('origin_capture_session_id') or '—'}"
        return content

    def _capture_show_details(self, event=None, *, record: dict | None = None) -> tk.Toplevel | None:
        if event is not None:
            item = self.capture_tree.identify_row(event.y)
            if not item:
                return None
            self.capture_tree.selection_set(item)
            self.capture_tree.focus(item)
            self.capture_context_item = item
        if record is None:
            record = self._wb_current_record()
        if not record:
            return None
        snapshot = dict(record)
        content = self._capture_details_content(snapshot)
        dialog = tk.Toplevel(self)
        dialog.title(f"CEC 报文详情 — #{snapshot.get('seq', '')}  {snapshot.get('time', '')}")
        dialog.transient(self)
        width = min(780, max(320, self.winfo_screenwidth() - 80))
        height = min(700, max(320, self.winfo_screenheight() - 100))
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(min(480, width), min(360, height))
        body = ttk.Frame(dialog, padding=10)
        body.pack(fill=BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        ttk.Label(body, text=f"#{snapshot.get('seq', '')}   {snapshot.get('source', '?')} → {snapshot.get('destination', '?')}   "
                            f"{snapshot.get('opcode_text', '')}", wraplength=width - 40).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        text = tk.Text(body, wrap="word", font="TkDefaultFont", padx=10, pady=8, height=18)
        text.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        text.configure(yscrollcommand=scroll.set)
        text.insert("1.0", content)
        # Disabled Text remains selectable/copyable, and does not modify the capture.
        text.configure(state="disabled")
        buttons = ttk.Frame(body)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Button(buttons, text="复制报文", command=lambda: self._copy_to_clipboard(str(snapshot.get("frame", "")))).pack(side=LEFT)
        ttk.Button(buttons, text="复制全部详情", command=lambda: self._copy_to_clipboard(content)).pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="关闭", command=dialog.destroy).pack(side=RIGHT)
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.capture_detail_text = text
        text.focus_set()
        return dialog

    def _capture_reuse_frame(self, record: dict | None = None) -> None:
        record = record if record is not None else self._wb_current_record()
        if not record:
            return
        frame_text = str(record.get("frame", ""))
        try:
            frame = self._parse_byte_list(frame_text)
        except ValueError:
            return
        if not frame:
            return
        self.raw_source_var.set(f"{frame[0] >> 4:X}")
        self.raw_destination_var.set(f"{frame[0] & 0x0F:X}")
        self.raw_opcode_var.set(f"{frame[1]:02X}" if len(frame) > 1 else "")
        self.raw_params_var.set(self._format_frame(frame[2:]))
        self.raw_frame_var.set(frame_text)
        self.notebook.select(self.device_tab_widget)
        self.device_notebook.select(self.raw_tab_widget)
        self.status_var.set(f"消息 #{record.get('seq', '')} 已载入 Raw 注入，待手动发送")

    def _export_capture_csv(self) -> None:
        self._files_choose_export(scope="ALL", format_name="CSV", full=True)

    def _export_capture_json(self) -> None:
        self._files_choose_export(scope="ALL", format_name="JSON", full=True)

    def _append_scenario_log(self, text: str) -> None:
        self.scenario_log.configure(state="normal")
        self.scenario_log.insert(END, text + ("" if text.endswith("\n") else "\n"))
        self.scenario_log.see(END)
        self.scenario_log.configure(state="disabled")

    def _run_scenario(self, *, dialect: str | None = None) -> None:
        self._files_show_live()
        if self.scenario_running:
            messagebox.showinfo(APP_NAME, "脚本已经在运行。")
            return
        if self.cts_running or self.raw_send_busy or self._wb_quick_busy:
            messagebox.showinfo(APP_NAME, "请等待当前测试或发送结束，再运行脚本。")
            return
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备，再运行脚本。")
            return
        lines = self.scenario_text.get("1.0", END).splitlines()
        try:
            plan = compile_script(lines, dialect or self.script_dialect_var.get(), self.script_delay_var.get(), self._script_path_hint)
        except ValueError as exc:
            messagebox.showwarning(APP_NAME, str(exc))
            return
        if not plan.steps:
            self.scenario_status_var.set("脚本没有可执行步骤")
            return
        self.scenario_log.configure(state="normal")
        self.scenario_log.delete("1.0", END)
        self.scenario_log.configure(state="disabled")
        self.scenario_stop_event.clear()
        self.scenario_running = True
        self.scenario_status_var.set(f"运行中：{plan.dialect}，命令间隔 {plan.interval_ms:g} ms")
        threading.Thread(target=self._scenario_thread, args=(plan,), daemon=True).start()

    def _stop_scenario(self) -> None:
        self.scenario_stop_event.set()
        if self.scenario_running:
            self.scenario_status_var.set("正在停止")

    def _scenario_thread(self, lines: list[str] | ScriptPlan) -> None:
        cursor = len(self.capture_records)
        passed = 0
        failed = 0
        sent = 0
        try:
            plan = lines if isinstance(lines, ScriptPlan) else compile_script(lines, "SCENARIO")
            self.output_queue.put(("scenario_log", f"开始脚本：{plan.dialect}，命令间隔 {plan.interval_ms:g} ms，起始抓包序号：{cursor + 1}"))
            for step in plan.steps:
                line_number = step.line_number
                if self.scenario_stop_event.is_set():
                    raise RuntimeError("用户停止")
                kind = "wait" if step.kind == "@wait" else step.kind
                line = step.value if kind == "command" else f"{kind} {step.value}"
                lower = line.lower()
                if lower.startswith("wait "):
                    milliseconds = float(line.split(maxsplit=1)[1])
                    self.output_queue.put(("scenario_log", f"[{line_number}] 等待 {milliseconds:g} ms"))
                    if self.scenario_stop_event.wait(max(0.0, milliseconds / 1000.0)):
                        raise RuntimeError("用户停止")
                    continue
                if lower.startswith("expect-opcode "):
                    opcode_text, timeout = self._scenario_target_and_timeout(line.split(maxsplit=1)[1], opcode=True)
                    self.output_queue.put(("scenario_log", f"[{line_number}] 等待 Opcode {opcode_text.upper()}（{timeout} ms）"))
                    matched, cursor = self._scenario_wait_for_match(cursor, opcode=int(opcode_text, 16), timeout_ms=timeout)
                    if not matched:
                        failed += 1
                        raise RuntimeError(f"第 {line_number} 行期望 Opcode {opcode_text.upper()} 超时")
                    passed += 1
                    continue
                if lower.startswith("expect "):
                    target, timeout = self._scenario_target_and_timeout(line.split(maxsplit=1)[1], opcode=False)
                    expected = self._format_frame(self._parse_frame(target))
                    self.output_queue.put(("scenario_log", f"[{line_number}] 等待报文 {expected}（{timeout} ms）"))
                    matched, cursor = self._scenario_wait_for_match(cursor, frame=expected, timeout_ms=timeout)
                    if not matched:
                        failed += 1
                        raise RuntimeError(f"第 {line_number} 行期望报文 {expected} 超时")
                    passed += 1
                    continue
                if lower.startswith("assert "):
                    if not self._scenario_assert(line[7:].strip(), cursor):
                        failed += 1
                        raise RuntimeError(f"第 {line_number} 行断言失败：{line}")
                    passed += 1
                    self.output_queue.put(("scenario_log", f"[{line_number}] 断言通过"))
                    continue
                self.output_queue.put(("scenario_command", line))
                self.output_queue.put(("scenario_log", f"[{line_number}] 发送：{line}"))
                sent += 1
                if self.scenario_stop_event.wait(plan.interval_ms / 1000.0):
                    raise RuntimeError("用户停止")
            self.output_queue.put(("scenario_log", f"脚本完成：{sent} 条命令，{passed} 个检查，失败：{failed}"))
            self.output_queue.put(("scenario_status", f"完成（{sent} 条命令，{passed} 项检查）"))
        except Exception as exc:  # scenario errors should become test results, not crash the GUI
            self.output_queue.put(("scenario_log", f"场景失败：{exc}"))
            self.output_queue.put(("scenario_status", f"失败（通过 {passed}，失败 {failed + 1}）"))
        finally:
            self.scenario_running = False

    @staticmethod
    def _scenario_target_and_timeout(value: str, opcode: bool) -> tuple[str, int]:
        parts = value.split()
        if not parts:
            raise ValueError("expect 缺少目标")
        target = parts[0]
        timeout = 1000
        for part in parts[1:]:
            if part.lower().startswith("timeout="):
                timeout = max(1, int(part.split("=", 1)[1]))
        if opcode:
            int(target.replace("0x", ""), 16)
        return target, timeout

    def _scenario_wait_for_match(self, cursor: int, frame: str | None = None, opcode: int | None = None, timeout_ms: int = 1000) -> tuple[bool, int]:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while not self.scenario_stop_event.is_set():
            for index in range(cursor, len(self.capture_records)):
                record = self.capture_records[index]
                if record.get("direction") != "RX":
                    continue
                if frame is not None and record.get("frame") == frame:
                    return True, index + 1
                if opcode is not None and record.get("opcode") == opcode:
                    return True, index + 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            with self.capture_condition:
                self.capture_condition.wait(timeout=min(remaining, 0.1))
        return False, len(self.capture_records)

    def _scenario_assert(self, expression: str, cursor: int) -> bool:
        lower = expression.lower().replace(" ", "")
        normalized = lower.replace("-", "")
        if normalized.startswith("rxcount>="):
            try:
                expected = int(normalized.split(">=", 1)[1])
            except ValueError:
                return False
            count = sum(1 for record in self.capture_records[cursor:] if record.get("direction") == "RX")
            return count >= expected
        if normalized.startswith("lastrxopcode="):
            try:
                expected = int(normalized.split("=", 1)[1].replace("0x", ""), 16)
            except ValueError:
                return False
            for record in reversed(self.capture_records[cursor:]):
                if record.get("direction") == "RX":
                    return record.get("opcode") == expected
            return False
        return False

    def _open_scenario(self, *, dialect="AUTO") -> None:
        path = filedialog.askopenfilename(title="打开 CEC 脚本", filetypes=[("CEC 脚本", "*.cec;*.txt;*.cmd"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            messagebox.showerror(APP_NAME, f"读取场景失败：\n{exc}")
            return
        self.scenario_text.delete("1.0", END)
        self.scenario_text.insert("1.0", text)
        self._script_path_hint = str(path)
        self.script_dialect_var.set(dialect)

    def _save_scenario(self) -> None:
        path = filedialog.asksaveasfilename(title="保存 CEC 脚本", defaultextension=".cec", filetypes=[("CEC 脚本", "*.cec"), ("文本文件", "*.txt"), ("旧命令文件", "*.cmd"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            Path(path).write_text(self.scenario_text.get("1.0", END), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"保存场景失败：\n{exc}")

    def _run_topology_scan(self) -> None:
        if self.oneshot_busy:
            messagebox.showinfo(APP_NAME, "已有单命令正在执行。")
            return
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备，再执行拓扑扫描。")
            return
        self.topology_scan_pending = True
        self.oneshot_busy = True
        self._append_output("\n>>> libCEC API 拓扑扫描\n")
        self.status_var.set("拓扑扫描中")
        threading.Thread(target=self._topology_scan_thread, daemon=True).start()

    def _topology_scan_thread(self) -> None:
        try:
            if not self.backend.is_open:
                self.output_queue.put(("topology_result", ([], "设备连接已断开，请重新连接后再扫描。")))
                return
            devices = self.backend.scan(settle_seconds=0.04)
            self.output_queue.put(("topology_result", (devices, "")))
        except (LibCecError, OSError, RuntimeError) as exc:
            self.output_queue.put(("topology_result", ([], str(exc))))

    def _refresh_topology_tree(self) -> None:
        if not hasattr(self, "topology_tree"):
            return
        for item in self.topology_tree.get_children():
            self.topology_tree.delete(item)
        for device in getattr(self, "topology_devices", []):
            self.topology_tree.insert(
                "",
                END,
                values=(
                    device.get("la", ""), device.get("type", ""), device.get("address", ""),
                    device.get("active", ""), self._vendor_display(device.get("vendor", "")), device.get("osd", ""),
                    device.get("version", ""), device.get("power", ""), device.get("language", ""),
                ),
            )

    def _clear_topology(self) -> None:
        self.topology_devices = []
        self._refresh_topology_tree()

    def _export_topology_csv(self) -> None:
        path = filedialog.asksaveasfilename(title="导出拓扑 CSV", defaultextension=".csv", filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")])
        if not path:
            return
        fields = ["la", "type", "address", "active", "vendor", "osd", "version", "power", "language"]
        try:
            with Path(path).open("w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(getattr(self, "topology_devices", []))
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"导出拓扑失败：\n{exc}")

    def _export_topology_json(self) -> None:
        path = filedialog.asksaveasfilename(title="导出拓扑 JSON", defaultextension=".json", filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(getattr(self, "topology_devices", []), ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"导出拓扑失败：\n{exc}")

    def _simulation_handle_frame(self, frame: list[int]) -> None:
        """Process one RX frame and optionally answer it through libCEC."""
        if not frame:
            return
        self.simulation_frame_count += 1
        source = (frame[0] >> 4) & 0x0F
        destination = frame[0] & 0x0F
        opcode = frame[1] if len(frame) > 1 else None
        params = frame[2:] if len(frame) > 2 else []
        response = None
        if opcode is not None:
            custom_found, response = self._simulation_custom_response(source, opcode)
            if not custom_found:
                response = self._simulation_builtin_response(source, destination, opcode, params)

        frame_text = self._format_frame(frame)
        opcode_text = "POLL" if opcode is None else f"0x{opcode:02X} {self._opcode_name(opcode)}"
        response_text = "无回复"
        if response:
            response_text = self._format_frame(response)
            self._send_command("tx " + self._format_frame(response))
        elif opcode is not None:
            response_text = "状态更新/无回复"
        self.sim_events.insert(
            "",
            END,
            values=(self._format_ui_time_ms(), frame_text, f"0x{source:X}", f"0x{destination:X}", opcode_text, response_text),
        )
        children = self.sim_events.get_children()
        if len(children) > 500:
            self.sim_events.delete(children[0])

    def _simulation_builtin_response(self, source: int, _destination: int, opcode: int, params: list[int]) -> list[int] | None:
        if not self.sim_auto_reply_var.get():
            return None
        try:
            own = int(self.sim_source_var.get().strip(), 16)
        except ValueError:
            return None
        role = self.sim_role_var.get().strip().lower()
        physical = self._simulation_physical_bytes()
        vendor = self._simulation_vendor_bytes()
        version = self._simulation_byte(self.sim_version_var.get(), 0x05)
        power = self._simulation_byte(self.sim_power_var.get(), 0)
        device_type = {"x": 0, "r": 1, "t": 3, "p": 4, "a": 5}.get(role, 0)

        def directed(response_opcode: int, response_params: list[int] | None = None) -> list[int]:
            return [((own & 0x0F) << 4) | (source & 0x0F), response_opcode] + (response_params or [])

        def broadcast(response_opcode: int, response_params: list[int] | None = None) -> list[int]:
            return [((own & 0x0F) << 4) | 0x0F, response_opcode] + (response_params or [])

        # Device identity and topology discovery.
        if opcode == 0x8F:  # Give Device Power Status
            return directed(0x90, [power])
        if opcode == 0x46:  # Give OSD Name
            return directed(0x47, self._simulation_osd_bytes())
        if opcode == 0x8C:  # Give Device Vendor ID
            return directed(0x87, vendor)
        if opcode == 0x9F:  # Get CEC Version
            return directed(0x9E, [version])
        if opcode == 0x83:  # Give Physical Address
            return broadcast(0x84, physical + [device_type])
        if opcode == 0x91:  # Get Menu Language
            return broadcast(0x32, self._simulation_language_bytes())
        if opcode == 0x8D:  # Give Deck/TV Menu Status
            return directed(0x8E, [0x00])
        if opcode == 0x85:  # Request Active Source
            return broadcast(0x82, physical)

        # Audio System role behavior.
        if role == "a" and opcode == 0x71:  # Give Audio Status
            return directed(0x7A, [self._simulation_audio_status()])
        if role == "a" and opcode == 0x7D:  # Give System Audio Mode Status
            return directed(0x7E, [0x01 if self.sim_audio_mode_var.get() else 0x00])
        if role == "a" and opcode == 0x70:  # System Audio Mode Request
            self.sim_audio_mode_var.set(True)
            return broadcast(0x72, [0x01])

        # Basic stateful interaction, useful for TV/Audio System smoke tests.
        if opcode in (0x04, 0x0D, 0x82):  # Image/Text View On or Active Source
            self.sim_power_var.set("0")
        elif opcode == 0x36:  # Standby
            self.sim_power_var.set("1")
        elif opcode == 0x47 and params:  # Set OSD Name
            self.sim_osd_var.set(bytes(params).decode("ascii", errors="replace"))
        elif role == "a" and opcode == 0x44 and params:
            self._simulation_apply_key(params[0])
        elif role == "a" and opcode == 0x72 and params:
            self.sim_audio_mode_var.set(params[0] != 0)
        return None

    def _simulation_custom_response(self, source: int, opcode: int) -> tuple[bool, list[int] | None]:
        """Return (matched, frame). A matched rule may intentionally send no reply."""
        try:
            own = int(self.sim_source_var.get().strip(), 16)
        except ValueError:
            return False, None
        for raw_line in self.sim_rule_text.get("1.0", END).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "|" not in line:
                continue
            fields = [field.strip() for field in line.split("|", 2)]
            if len(fields) != 3:
                continue
            try:
                rule_opcode = int(fields[0].lower().replace("0x", ""), 16)
            except ValueError:
                continue
            if rule_opcode != opcode:
                continue
            target = fields[1].lower()
            if target == "origin":
                destination = source
            elif target in {"broadcast", "f"}:
                destination = 0x0F
            else:
                try:
                    destination = int(target.replace("0x", ""), 16) & 0x0F
                except ValueError:
                    return True, None
            response_text = fields[2].strip()
            if response_text.lower() in {"", "none", "-"}:
                return True, None
            response_text = self._expand_simulation_tokens(response_text)
            try:
                response_bytes = [int(value.strip().replace("0x", ""), 16) for value in response_text.split(":") if value.strip()]
            except ValueError:
                self._append_output(f"SIMULATOR: 无法解析自定义响应：{raw_line}\n")
                return True, None
            return True, [((own & 0x0F) << 4) | destination] + response_bytes
        return False, None

    def _expand_simulation_tokens(self, text: str) -> str:
        tokens = {
            "{power}": f"{self._simulation_byte(self.sim_power_var.get(), 0):02X}",
            "{volume}": f"{self._simulation_audio_status():02X}",
            "{audio_status}": f"{self._simulation_audio_status():02X}",
            "{mute}": "80" if self.sim_mute_var.get() else "00",
            "{version}": f"{self._simulation_byte(self.sim_version_var.get(), 5):02X}",
            "{vendor}": self._colon_bytes(self._simulation_vendor_bytes()),
            "{physical}": self._colon_bytes(self._simulation_physical_bytes()),
            "{type}": f"{self._simulation_device_type():02X}",
            "{language}": self._colon_bytes(self._simulation_language_bytes()),
            "{osd}": self._colon_bytes(self._simulation_osd_bytes()),
        }
        for token, replacement in tokens.items():
            text = text.replace(token, replacement)
        return text

    def _simulation_apply_key(self, key: int) -> None:
        try:
            volume = max(0, min(100, int(self.sim_volume_var.get().strip())))
        except ValueError:
            volume = 50
        if key == 0x41:  # Volume Up
            volume = min(100, volume + 1)
        elif key == 0x42:  # Volume Down
            volume = max(0, volume - 1)
        elif key == 0x43:  # Mute
            self.sim_mute_var.set(not self.sim_mute_var.get())
        self.sim_volume_var.set(str(volume))

    def _simulation_audio_status(self) -> int:
        try:
            volume = max(0, min(100, int(self.sim_volume_var.get().strip())))
        except ValueError:
            volume = 50
        return (0x80 if self.sim_mute_var.get() else 0x00) | (volume & 0x7F)

    def _simulation_device_type(self) -> int:
        return {"x": 0, "r": 1, "t": 3, "p": 4, "a": 5}.get(self.sim_role_var.get().strip().lower(), 0)

    def _simulation_physical_bytes(self) -> list[int]:
        try:
            value = int(self.sim_physical_var.get().strip().replace(":", ""), 16) & 0xFFFF
        except ValueError:
            value = 0x1000
        return [(value >> 8) & 0xFF, value & 0xFF]

    def _simulation_vendor_bytes(self) -> list[int]:
        try:
            value = int(self.sim_vendor_var.get().strip().replace(":", ""), 16) & 0xFFFFFF
        except ValueError:
            value = 0x080046
        return [(value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF]

    def _simulation_language_bytes(self) -> list[int]:
        value = (self.sim_language_var.get().strip() or "eng")[:3].ljust(3)
        return list(value.encode("ascii", errors="replace"))

    def _simulation_osd_bytes(self) -> list[int]:
        return list((self.sim_osd_var.get().strip() or "CEC Simulator").encode("ascii", errors="replace")[:14])

    @staticmethod
    def _simulation_byte(value: str, fallback: int) -> int:
        try:
            return int(value.strip(), 16) & 0xFF
        except ValueError:
            return fallback

    @staticmethod
    def _colon_bytes(values: list[int]) -> str:
        return ":".join(f"{value:02X}" for value in values)

    @staticmethod
    def _format_frame(frame: list[int]) -> str:
        return ":".join(f"{value:02X}" for value in frame)

    @staticmethod
    def _opcode_name(opcode: int) -> str:
        guide = OPCODE_GUIDE.get(opcode)
        if guide:
            return guide[0]
        names = {
            0x00: "Feature Abort", 0x04: "Image View On", 0x0D: "Text View On",
            0x32: "Set Menu Language", 0x36: "Standby", 0x44: "User Control Pressed",
            0x45: "User Control Released", 0x46: "Give OSD Name", 0x47: "Set OSD Name",
            0x70: "System Audio Mode Request", 0x71: "Give Audio Status", 0x72: "Set System Audio Mode",
            0x7A: "Report Audio Status", 0x7D: "Give System Audio Mode Status", 0x7E: "System Audio Mode Status",
            0x82: "Active Source", 0x83: "Give Physical Address", 0x84: "Report Physical Address",
            0x85: "Request Active Source", 0x87: "Device Vendor ID", 0x8C: "Give Device Vendor ID",
            0x8D: "Give Menu Status", 0x8E: "Menu Status", 0x8F: "Give Device Power Status",
            0x90: "Report Power Status", 0x91: "Get Menu Language", 0x9D: "Inactive Source",
            0x9E: "CEC Version", 0x9F: "Get CEC Version", 0xA0: "Give Features",
        }
        return names.get(opcode, OPCODE_NAMES.get(opcode, "Unknown"))

    @staticmethod
    def _opcode_explanation(opcode: int) -> str:
        guide = OPCODE_GUIDE.get(opcode)
        if guide:
            return guide[3]
        return "未收录的 Opcode；可能是厂商扩展或当前 Opcode 目录尚未覆盖。"

    def _decode_opcode_parameters(self, opcode: int | None, params: list[int]) -> str:
        """Best-effort decoding of the fixed fields carried by a CEC opcode.

        CEC has several messages whose payload is defined as a small fixed
        structure, while others intentionally carry device/vendor-specific
        data.  This method decodes the former and keeps the raw bytes visible
        for the latter; it must never silently guess a vendor payload.
        """

        raw = CECClientGUI._colon_bytes(params) if params else "—"
        if opcode is None:
            return "轮询帧，无参数"

        guide = OPCODE_GUIDE.get(opcode)
        if not params:
            if guide and guide[1] == "无":
                return "无参数（符合定义）"
            if guide:
                return f"未收到参数；规范提示：{guide[1]}"
            return "未收录的 Opcode；原始参数为空"

        if guide and guide[1] == "无":
            return f"该 Opcode 按定义无参数；收到原始参数 {raw}"

        def physical(values: list[int]) -> str:
            if len(values) < 2:
                return f"物理地址长度不足：需要 2 字节，收到 {raw}"
            address = ".".join(
                str(nibble)
                for byte in values[:2]
                for nibble in ((byte >> 4) & 0x0F, byte & 0x0F)
            )
            return f"物理地址={address}（{values[0]:02X}:{values[1]:02X}）"

        def text(values: list[int]) -> str:
            decoded = bytes(values).decode("ascii", errors="replace").rstrip("\x00")
            if not decoded or any(ord(char) < 0x20 for char in decoded):
                return f"文本不可打印；原始参数={CECClientGUI._colon_bytes(values)}"
            return f"文本=“{decoded}”"

        def named(value: int, names: dict[int, str], field: str) -> str:
            return f"{field}={names.get(value, '未知')}（0x{value:02X}）"

        def user_control_code_name(value: int) -> str:
            name = USER_CONTROL_CODE_NAMES.get(value)
            if name is not None:
                return name
            if value in USER_CONTROL_CODE_RESERVED:
                return "保留（Reserved）"
            return "未分配/未知"

        abort_reasons = {
            0x00: "不认识的 Opcode",
            0x01: "当前模式不能响应",
            0x02: "无法提供请求的源",
            0x03: "操作数无效",
            0x04: "拒绝请求",
        }
        power_names = {
            0x00: "On", 0x01: "Standby", 0x02: "Transition → On",
            0x03: "Transition → Standby",
        }
        mode_names = {0x00: "Off", 0x01: "On"}
        menu_names = {0x00: "Activate", 0x01: "Deactivate", 0x02: "Query"}
        device_type_names = {
            0x00: "TV", 0x01: "Recording Device", 0x02: "Reserved",
            0x03: "Tuner", 0x04: "Playback Device", 0x05: "Audio System",
            0x06: "Pure CEC Switch", 0x07: "Video Processor",
        }
        audio_format_names = {
            1: "L-PCM", 2: "AC-3", 3: "MPEG-1",
            4: "MP3", 5: "MPEG-2", 6: "AAC-LC", 7: "DTS",
            8: "ATRAC", 9: "One Bit Audio", 10: "Dolby Digital+",
            11: "DTS-HD", 12: "MAT", 13: "DST", 14: "WMA Pro",
            15: "Extended",
        }

        if opcode == 0x00:
            if len(params) < 2:
                return f"被 Abort 的 Opcode=0x{params[0]:02X}；缺少 Abort Reason"
            reason = abort_reasons.get(params[1], "未知原因")
            return f"被 Abort 的 Opcode=0x{params[0]:02X}；原因={reason}（0x{params[1]:02X}）"
        if opcode == 0x32:
            if len(params) < 3:
                return f"语言码长度不足：需要 3 字节，收到 {raw}"
            language = bytes(params[:3]).decode("ascii", errors="replace")
            suffix = f"；尾随参数={CECClientGUI._colon_bytes(params[3:])}" if len(params) > 3 else ""
            return f"菜单语言={language}（ASCII {CECClientGUI._colon_bytes(params[:3])}）{suffix}"
        if opcode == 0x44:
            key_name = user_control_code_name(params[0])
            return f"按键={key_name}（0x{params[0]:02X}）" + (f"；尾随参数={CECClientGUI._colon_bytes(params[1:])}" if len(params) > 1 else "")
        if opcode == 0x47:
            return text(params[:14]) + (f"；尾随参数={CECClientGUI._colon_bytes(params[14:])}" if len(params) > 14 else "")
        if opcode == 0x64:
            display_names = {0x00: "正常显示", 0x40: "按默认时长显示", 0x80: "清除之前消息"}
            display = named(params[0], display_names, "显示控制")
            return display + (f"；{text(params[1:])}" if len(params) > 1 else "；缺少 OSD 文本")
        if opcode == 0x67:
            return text(params)
        if opcode in {0x70, 0x82, 0x86, 0x9D}:
            return physical(params)
        if opcode == 0x80:
            if len(params) < 4:
                return f"路由地址长度不足：需要旧地址+新地址共 4 字节，收到 {raw}"
            return f"旧物理地址：{physical(params[:2])}；新物理地址：{physical(params[2:4])}"
        if opcode == 0x84:
            if len(params) < 3:
                return f"参数长度不足：需要物理地址 2 字节+设备类型 1 字节，收到 {raw}"
            result = f"{physical(params[:2])}；{named(params[2], device_type_names, '设备类型')}"
            return result + (f"；尾随参数={CECClientGUI._colon_bytes(params[3:])}" if len(params) > 3 else "")
        if opcode == 0x87:
            if len(params) < 3:
                return f"Vendor ID 长度不足：需要 3 字节，收到 {raw}"
            vendor_id = CECClientGUI._colon_bytes(params[:3])
            return f"Vendor ID={self._vendor_id_description(vendor_id)}" + (f"；尾随参数={CECClientGUI._colon_bytes(params[3:])}" if len(params) > 3 else "")
        if opcode in {0x72, 0x7E}:
            return named(params[0], mode_names, "System Audio Mode")
        if opcode == 0x7A:
            status = params[0]
            volume = status & 0x7F
            muted = "是" if status & 0x80 else "否"
            return f"音量={volume}/100；静音={muted}（原始状态 0x{status:02X}）"
        if opcode == 0x8D:
            return named(params[0], menu_names, "菜单请求")
        if opcode == 0x8E:
            return named(params[0], menu_names, "菜单状态")
        if opcode == 0x90:
            return named(params[0], power_names, "电源状态")
        if opcode == 0x9E:
            version_names = {0x05: "CEC 1.4", 0x06: "CEC 2.0"}
            return named(params[0], version_names, "CEC 版本")
        if opcode == 0xA0:
            if len(params) < 3:
                return f"Vendor ID 长度不足：需要 3 字节，收到 {raw}"
            vendor_id = CECClientGUI._colon_bytes(params[:3])
            data = CECClientGUI._colon_bytes(params[3:]) if len(params) > 3 else "无"
            return f"Vendor ID={self._vendor_id_description(vendor_id)}；厂商数据={data}"
        if opcode == 0xA3:
            if len(params) % 3:
                complete = len(params) - (len(params) % 3)
                suffix = f"；尾随不完整字节={CECClientGUI._colon_bytes(params[complete:])}"
            else:
                complete = len(params)
                suffix = ""
            descriptors = []
            sample_rates = {
                0: "32kHz", 1: "44.1kHz", 2: "48kHz", 3: "88.2kHz",
                4: "96kHz", 5: "176.4kHz", 6: "192kHz",
            }
            for index in range(0, complete, 3):
                first, second, third = params[index:index + 3]
                format_code = (first >> 3) & 0x0F
                channels = (first & 0x07) + 1
                rates = [name for bit, name in sample_rates.items() if second & (1 << bit)]
                rate_text = "/".join(rates) if rates else "未知采样率"
                descriptors.append(
                    f"SAD{index // 3}: 格式={audio_format_names.get(format_code, '未知')}"
                    f"(0x{format_code:X})，声道={channels}，采样率={rate_text}，原始={first:02X}:{second:02X}:{third:02X}"
                )
            return "；".join(descriptors) + suffix
        if opcode == 0xA4:
            formats = [audio_format_names.get(value & 0x0F, "未知") for value in params]
            return "请求音频格式=" + ", ".join(
                f"{name}(0x{value & 0x0F:X})" for value, name in zip(params, formats)
            )

        if guide:
            return f"固定格式尚未细分：{guide[1]}；原始参数={raw}"
        return f"未收录的 Opcode；仅保留原始参数={raw}"

    def _raw_opcode_changed(self, *_args: object) -> None:
        """Refresh the inline explanation when the Raw opcode changes."""
        value = self.raw_opcode_var.get().strip()
        if not value:
            self.raw_opcode_help_var.set("请输入两位十六进制 Opcode；下拉框提供常用消息，也允许输入未收录或厂商自定义 Opcode。")
            return
        try:
            opcode = self._parse_byte(value)
        except ValueError:
            self.raw_opcode_help_var.set("Opcode 应为 00-FF 的十六进制字节，例如 8F、90 或 0x70。")
            return
        guide = OPCODE_GUIDE.get(opcode)
        if guide:
            name, params, direction, description = guide
            self.raw_opcode_help_var.set(
                f"0x{opcode:02X} {name}｜作用：{description}｜方向：{direction}｜参数：{params}"
            )
            return
        name = OPCODE_NAMES.get(opcode, "未收录/厂商自定义 Opcode")
        self.raw_opcode_help_var.set(
            f"0x{opcode:02X} {name}｜当前没有内置参数说明；请按 CEC 规范或厂商协议填写参数，仍可直接组装并发送。"
        )

    def _clear_sim_events(self) -> None:
        for item in self.sim_events.get_children():
            self.sim_events.delete(item)
        self.simulation_frame_count = 0

    def _start_raw_session(self) -> None:
        self._connect_device()

    def _raw_apply_la(self) -> None:
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备。Raw 页面复用当前通用连接。")
            return
        try:
            source = self._parse_la(self.raw_source_var.get())
        except ValueError:
            messagebox.showerror(APP_NAME, "Source LA 必须是 0-F。")
            return
        self._send_command(f"la {source:X}")

    def _set_monitoring(self, enabled: bool) -> None:
        if self.occupancy_busy or self.backend.occupancy_mask:
            messagebox.showinfo(APP_NAME, "地址占用期间保留监听路由及硬件 ACK；先释放全部占用再切换监听模式。")
            return
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备，再切换监听模式。")
            return
        result = self.backend.switch_monitoring(enabled)
        self._append_output(f">>> {'mon 1' if enabled else 'mon 0'}\n")
        if result.text:
            self._append_output(f"{result.text}\n")
        if result.ok:
            self.raw_monitor_var.set(enabled)
            self.monitor_state_var.set(f"监听模式：{'开启' if enabled else '关闭'}")
            return

        # A connection opened with bMonitorOnly=1 has no registered
        # controlled client. libCEC may therefore reject SwitchMonitoring(0)
        # even though the user explicitly requested normal control mode. In
        # that case reopen the same profile with monitor-only disabled.
        if not enabled and bool(self.connection_kwargs.get("monitor_only", False)):
            self._append_output(">>> 当前会话是初始被动监听，正在重连为正常控制模式…\n")
            previous_profile = self.connection_profile
            previous_role_source = self.connection_role_source
            reconnect_kwargs = dict(self.connection_kwargs)
            reconnect_kwargs["monitor_only"] = False
            self.backend.close()
            reopened = self._backend_open(**reconnect_kwargs)
            if reopened.ok:
                if previous_role_source is not None:
                    address_result = self.backend.set_logical_address(previous_role_source)
                    if not address_result.ok:
                        self.backend.close()
                        self.connection_profile = ""
                        self.connection_kwargs = {}
                        self.connection_role_source = None
                        self.monitor_state_var.set("监听模式：未连接")
                        self._append_output(f">>> 重连后恢复角色地址失败：{address_result.text}\n")
                        return
                self.connection_profile = previous_profile
                self.connection_role_source = previous_role_source
                self.connection_kwargs = reconnect_kwargs
                self.monitor_only_var.set(False)
                self.raw_monitor_var.set(False)
                self.monitor_state_var.set("监听模式：关闭")
                self.start_button.configure(state="disabled")
                self.stop_button.configure(state="normal")
                self.monitor_on_button.configure(state="normal")
                self.monitor_off_button.configure(state="normal")
                self._append_output(">>> 已重连，监听模式：关闭，恢复正常控制。\n")
            else:
                self.connection_profile = ""
                self.connection_kwargs = {}
                self.connection_role_source = None
                self.monitor_state_var.set("监听模式：未连接")
                self._append_output(f">>> 重连失败：{reopened.text}\n")
            return

        self.monitor_state_var.set(f"监听模式：{'开启失败' if enabled else '关闭失败'}")

    def _raw_monitor(self, enabled: bool) -> None:
        """Compatibility wrapper for older callers; monitoring is global now."""
        self._set_monitoring(enabled)

    def _sync_raw_builder(self) -> None:
        """Shared field edits update the preview; direct raw text remains editable."""
        try:
            source = self._parse_la(self.raw_source_var.get())
            destination = self._parse_la(self.raw_destination_var.get())
            frame = [(source << 4) | destination]
            if self.raw_opcode_var.get().strip():
                frame.extend([self._parse_byte(self.raw_opcode_var.get()), *self._parse_byte_list(self.raw_params_var.get())])
            elif self.raw_params_var.get().strip():
                return
            if len(frame) <= 16:
                self.raw_frame_var.set(self._format_frame(frame))
        except (ValueError, tk.TclError):
            pass

    def _raw_build_frame(self) -> None:
        try:
            source = self._parse_la(self.raw_source_var.get())
            destination = self._parse_la(self.raw_destination_var.get())
            opcode = self._parse_byte(self.raw_opcode_var.get())
            params = self._parse_byte_list(self.raw_params_var.get())
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"报文参数错误：\n{exc}")
            return
        frame = [((source & 0x0F) << 4) | (destination & 0x0F), opcode] + params
        self.raw_frame_var.set(self._format_frame(frame))

    def _raw_send(self, no_ack: bool) -> None:
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备。Raw 页面复用当前通用连接。")
            return
        try:
            frame = self._parse_frame(self.raw_frame_var.get())
            source = (frame[0] >> 4) & 0x0F
            self._parse_la(self.raw_source_var.get())
            count = max(1, int(self.raw_count_var.get().strip()))
            interval = max(0.0, float(self.raw_interval_var.get().strip()) / 1000.0)
        except ValueError as exc:
            messagebox.showerror(APP_NAME, f"原始报文参数错误：\n{exc}")
            return
        if self.raw_send_busy:
            messagebox.showinfo(APP_NAME, "上一批 Raw 报文仍在发送。")
            return
        mode = "txn" if no_ack or self.raw_no_ack_var.get() else "tx"
        frame_text = self._format_frame(frame)
        if self.raw_auto_la_var.get() and not self.backend.occupancy_mask:
            self.raw_source_var.set(f"{source:X}")
            self._send_command(f"la {source:X}")
        self._raw_add_history(mode, frame_text, count)
        if count == 1:
            self._send_command(f"{mode} {frame_text}")
            return
        self.raw_send_busy = True
        self.status_var.set(f"Raw 报文发送中：{count} 帧")
        threading.Thread(target=self._raw_burst_thread, args=(mode, frame_text, count, interval), daemon=True).start()

    def _raw_burst_thread(self, mode: str, frame: str, count: int, interval: float) -> None:
        for index in range(count):
            if not self.backend.is_open:
                break
            self.output_queue.put(("raw_command", f"{mode} {frame}"))
            if index + 1 < count:
                time.sleep(interval)
        self.output_queue.put(("raw_done", None))

    def _raw_send_batch(self) -> None:
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备。Raw 页面复用当前通用连接。")
            return
        if self.raw_send_busy:
            messagebox.showinfo(APP_NAME, "上一批 Raw 报文仍在发送。")
            return
        try:
            interval = max(0.0, float(self.raw_interval_var.get().strip()) / 1000.0)
        except ValueError:
            messagebox.showerror(APP_NAME, "间隔必须是数字。")
            return
        batch: list[tuple[str, str, int]] = []
        for raw_line in self.raw_batch_text.get("1.0", END).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 1:
                mode, frame_text = ("txn" if self.raw_no_ack_var.get() else "tx"), parts[0]
            elif parts[0].lower() in {"tx", "txn"}:
                mode, frame_text = parts[0].lower(), parts[1]
            else:
                mode, frame_text = ("txn" if self.raw_no_ack_var.get() else "tx"), line
            try:
                frame = self._parse_frame(frame_text)
            except ValueError as exc:
                messagebox.showerror(APP_NAME, f"批量报文错误：{line}\n{exc}")
                return
            batch.append((mode, self._format_frame(frame), (frame[0] >> 4) & 0x0F))
        if not batch:
            messagebox.showwarning(APP_NAME, "批量报文为空。")
            return
        self.raw_send_busy = True
        for mode, frame, _source in batch:
            self._raw_add_history(mode, frame, 1)
        self.status_var.set(f"批量发送中：{len(batch)} 帧")
        threading.Thread(target=self._raw_batch_thread,
            args=(batch, interval, self.raw_auto_la_var.get() and not self.backend.occupancy_mask), daemon=True).start()

    def _raw_batch_thread(self, batch: list[tuple[str, str, int]], interval: float, auto_la: bool) -> None:
        last_source: int | None = None
        for mode, frame, source in batch:
            if not self.backend.is_open:
                break
            if auto_la and source != last_source:
                self.output_queue.put(("raw_command", f"la {source:X}"))
                last_source = source
            self.output_queue.put(("raw_command", f"{mode} {frame}"))
            time.sleep(interval)
        self.output_queue.put(("raw_done", None))

    @staticmethod
    def _parse_la(value: str) -> int:
        text = value.strip().lower().replace("0x", "")
        if not text:
            raise ValueError("LA 不能为空")
        number = int(text, 16)
        if not 0 <= number <= 0x0F:
            raise ValueError("LA 必须在 0-F 范围内")
        return number

    @staticmethod
    def _parse_byte(value: str) -> int:
        text = value.strip().lower().replace("0x", "")
        if not text:
            raise ValueError("字节不能为空")
        number = int(text, 16)
        if not 0 <= number <= 0xFF:
            raise ValueError(f"字节超出范围：{value}")
        return number

    def _parse_byte_list(self, value: str) -> list[int]:
        if not value.strip():
            return []
        parts = [part for part in re.split(r"[,:\s]+", value.strip()) if part]
        return [self._parse_byte(part) for part in parts]

    def _parse_frame(self, value: str) -> list[int]:
        frame = self._parse_byte_list(value)
        if not frame:
            raise ValueError("CEC 报文至少需要 header；仅 header 表示 Poll，例如 55")
        if len(frame) > 16:
            raise ValueError("CEC 单帧最多 16 个字节")
        return frame

    def _raw_add_history(self, mode: str, frame: str, count: int) -> None:
        self.raw_history.insert("", END, values=(self._format_ui_time_ms(), mode, frame, count))
        children = self.raw_history.get_children()
        if len(children) > 500:
            self.raw_history.delete(children[0])

    def _send_command(self, command: str) -> bool:
        command = command.strip()
        if not command:
            return False
        if not self.backend.is_open:
            messagebox.showwarning(APP_NAME, "请先在窗口顶部连接设备。")
            return False
        result = self.backend.execute_command(command)
        self._add_history(command)
        self._append_output(f">>> {command}\n")
        if result.text:
            self._append_output(f"{result.text}\n")
        if not result.ok:
            self._append_output("[命令失败]\n")
        parts = command.split()
        if result.ok and parts and parts[0].lower() == "mon" and len(parts) > 1 and parts[1] in {"0", "1"}:
            enabled = parts[1] == "1"
            self.raw_monitor_var.set(enabled)
            self.monitor_state_var.set(f"监听模式：{'开启' if enabled else '关闭'}")
        return result.ok

    @staticmethod
    def _command_needs_confirmation(command: str) -> bool:
        name = command.strip().split(None, 1)[0].lower() if command.strip() else ""
        return name in {"q", "quit", "bl", "am", "on", "standby", "stby", "r"}

    def _command_selected(self, _event=None) -> None:
        selection = self.command_tree.selection()
        if not selection:
            return
        self.command_args_var.set("")
        values = self.command_tree.item(selection[0], "values")
        self.console_command_var.set(str(values[0]))

    def _selected_command(self) -> str:
        selection = self.command_tree.selection()
        if not selection:
            return ""
        values = self.command_tree.item(selection[0], "values")
        command = str(values[0])
        args = self.command_args_var.get().strip()
        return f"{command} {args}".strip()

    def _send_selected(self) -> None:
        command = self.console_command_var.get().strip() or self._selected_command()
        self._run_one_shot_command(command)

    def _run_selected_one_shot(self) -> None:
        self._run_one_shot_command(self._selected_command())

    def _run_one_shot_command(self, command: str) -> None:
        if not command:
            messagebox.showwarning(APP_NAME, "请输入或选择一个命令。")
            return
        if self._command_needs_confirmation(command) and not messagebox.askyesno(APP_NAME, f"命令 {command} 可能改变设备或适配器状态，确定执行？"):
            return
        if self.oneshot_busy:
            return
        self._send_command(command)

    def _run_script(self) -> None:
        """Legacy entry point; the single editor uses legacy command pacing."""
        self._run_scenario(dialect="LEGACY")

    def _script_thread(self, commands: list[tuple[str, str]], delay: float) -> None:
        lines = [f"@wait {value}" if kind == "wait" else value for kind, value in commands]
        self._scenario_thread(compile_script(lines, "LEGACY", delay * 1000.0))

    def _stop_session(self) -> None:
        if not self.backend.is_open:
            return
        self.simulation_active = False
        try:
            self.backend.close()
        except OSError:
            pass
        self.log_writer.flush(wait=False)
        self.connection_profile = ""
        self.connection_kwargs = {}
        self.connection_role_source = None
        self.raw_monitor_var.set(False)
        self.monitor_state_var.set("监听模式：未连接")
        self.monitor_on_button.configure(state="disabled")
        self.monitor_off_button.configure(state="disabled")
        self.status_var.set("正在停止会话…")

    def _list_adapters(self) -> None:
        self._run_utility(["-l"], "列出适配器")

    def _show_cli_help(self) -> None:
        self._run_utility(["-H"], "显示 libCEC API 命令帮助")

    def _show_cli_options(self) -> None:
        self._run_utility(["-h"], "显示 libCEC API 说明")

    def _show_info(self) -> None:
        self._run_utility(["-i"], "显示 libCEC 编译信息")

    def _show_stats(self) -> None:
        self._run_utility(["--stats"], "读取适配器统计")

    def _bootloader(self) -> None:
        if not messagebox.askyesno(APP_NAME, "这会让适配器进入 Bootloader。确定继续？"):
            return
        self._run_utility(["--bootloader"], "进入 Bootloader")

    def _run_utility(self, extra: list[str], title: str) -> None:
        if self.oneshot_busy:
            return
        self.oneshot_busy = True
        self._append_output(f"\n>>> libCEC API：{title}\n")
        self.status_var.set(f"{title}…")
        threading.Thread(target=self._api_utility_thread, args=(extra, title), daemon=True).start()

    def _api_utility_thread(self, extra: list[str], title: str) -> None:
        try:
            if extra == ["-l"]:
                adapters = self.backend.list_adapters()
                text = "\n".join(f"{item['comm']}  {item['path']}" for item in adapters) or "未发现适配器"
                result = BackendResult(bool(adapters), text, adapters)
            elif extra == ["-H"]:
                result = BackendResult(True, LibCecBackend.help_text())
            elif extra == ["-h"]:
                result = BackendResult(True, "本 GUI 已移除 cec-client CLI 子进程；所有连接、发送和查询均通过 libCEC C API。")
            elif extra == ["-i"]:
                result = BackendResult(True, self.backend.lib_info())
            elif extra == ["--stats"]:
                if not self.backend.is_open:
                    result = BackendResult(False, "请先在窗口顶部连接设备，再读取适配器统计。")
                else:
                    stats = self.backend.stats()
                    result = BackendResult(bool(stats), json.dumps(stats, ensure_ascii=False, indent=2), stats)
            elif extra == ["--bootloader"]:
                if not self.backend.is_open:
                    result = BackendResult(False, "请先在窗口顶部连接设备，再进入 Bootloader。")
                else:
                    result = self.backend.start_bootloader()
            else:
                result = BackendResult(False, f"未识别的 libCEC 工具操作：{extra}")
            self.output_queue.put(("utility", (title, result)))
        except (LibCecError, OSError, RuntimeError) as exc:
            self.output_queue.put(("utility", (title, BackendResult(False, str(exc)))))

    def _browse_cec(self) -> None:
        path = filedialog.askopenfilename(title="选择 libCEC cec.dll", filetypes=[("libCEC DLL", "cec.dll"), ("所有 DLL", "*.dll"), ("所有文件", "*.*")])
        if path:
            self.cec_path_var.set(path)

    @staticmethod
    def _browse_file(variable: tk.StringVar, save: bool = False) -> None:
        path = filedialog.asksaveasfilename(title="选择日志文件", defaultextension=".log", filetypes=[("日志文件", "*.log"), ("所有文件", "*.*")]) if save else filedialog.askopenfilename()
        if path:
            variable.set(path)

    def _open_script(self) -> None:
        self._open_scenario(dialect="LEGACY")

    def _save_script(self) -> None:
        self._save_scenario()

    def _save_output(self) -> None:
        path = filedialog.asksaveasfilename(title="保存 libCEC 输出", defaultextension=".txt", filetypes=[("文本文件", "*.txt"), ("日志文件", "*.log"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            Path(path).write_text(self.output.get("1.0", END), encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(APP_NAME, f"保存输出失败：\n{exc}")

    def _clear_output(self) -> None:
        self.output.delete("1.0", END)

    def _append_output(self, text: str) -> None:
        self.output.insert(END, text)
        self.output.see(END)

    def _add_history(self, command: str) -> None:
        if not self.history or self.history[-1] != command:
            self.history.append(command)

    def _on_close(self) -> None:
        self.simulation_active = False
        if self.backend.is_open:
            if not messagebox.askyesno(APP_NAME, "libCEC 会话仍在运行，直接停止并退出？"):
                return
            try:
                self.backend.close()
            except OSError:
                pass
        self.backend.close()
        self.scenario_stop_event.set()
        self._save_settings()
        self.log_writer.close()
        self.destroy()

    def destroy(self) -> None:
        self._closing = True
        self.scenario_stop_event.set()
        for name in ("_layout_save_job", "_wb_chips_job"):
            job = getattr(self, name, None)
            if job is not None:
                self.after_cancel(job)
                setattr(self, name, None)
        if getattr(self, "_files_load_job", None) is not None:
            self.after_cancel(self._files_load_job)
            self._files_load_job = None
        if getattr(self, "_wb_highlight_save_job", None) is not None:
            self.after_cancel(self._wb_highlight_save_job)
            self._wb_highlight_save_job = None
        if getattr(self, "_wb_query_job", None) is not None:
            self.after_cancel(self._wb_query_job)
            self._wb_query_job = None
        if self._poll_after_id is not None:
            self.after_cancel(self._poll_after_id)
            self._poll_after_id = None
        super().destroy()


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test-report":
        from portable_selftest import run_self_test
        raise SystemExit(run_self_test(CECClientGUI, Path(sys.argv[2])))
    app = CECClientGUI()
    app.mainloop()


if __name__ == "__main__":
    main()
