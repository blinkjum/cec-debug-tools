"""Protocol-value helpers for quick commands, compound search and annotations.

No Tk, DLL or filesystem access: these models also serve offline capture tools.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from copy import deepcopy
import re
from typing import Any
from uuid import uuid4


HIGHLIGHT_PALETTE = (
    ("#FFE68A", "明黄"), ("#FF9E9E", "珊瑚红"),
    ("#9CCBFF", "天蓝"), ("#A8E6A3", "草绿"),
    ("#FFBE76", "橙色"), ("#C9A7EB", "紫色"),
    ("#7EDBD4", "青色"), ("#F5A9D0", "粉色"),
    ("#D0E580", "黄绿"), ("#B5B7F3", "靛蓝"),
    ("#D9B99B", "棕色"), ("#B9CDD5", "蓝灰"),
    ("#FF7043", "深橙"), ("#8E44AD", "深紫"),
    ("#1565C0", "深蓝"), ("#26734D", "深绿"),
)
HIGHLIGHT_RULE_COUNT = 12


def highlight_color(value: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        raise ValueError("颜色必须是 #RRGGBB 格式")
    return value.upper()


def highlight_style(color: str) -> dict[str, str]:
    color = highlight_color(color)
    channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
    linear = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
    luminance = sum(x * weight for x, weight in zip(linear, (0.2126, 0.7152, 0.0722)))
    dark_text = luminance > 0.179
    outline = [round(x * 255 * 0.65 if dark_text else (x * 0.65 + 0.35) * 255) for x in channels]
    return {"background": color, "foreground": "#000000" if dark_text else "#FFFFFF",
            "outline": "#" + "".join(f"{x:02X}" for x in outline)}


def highlight_settings(settings: dict) -> list[dict[str, str]]:
    """Validate saved rules, migrating the former four fixed-color slots."""
    rules = [{"opcode": "", "color": color} for color, _ in HIGHLIGHT_PALETTE[:HIGHLIGHT_RULE_COUNT]]
    stored = settings.get("capture_highlight_rules")
    if not isinstance(stored, list):
        legacy = settings.get("capture_highlight_opcodes", [])
        stored = [{"opcode": value} for value in legacy] if isinstance(legacy, list) else []
    for target, item in zip(rules, stored):
        if not isinstance(item, dict):
            continue
        try:
            if str(item.get("opcode", "")).strip():
                target["opcode"] = f"{hex_value(item['opcode']):02X}"
        except (ValueError, TypeError):
            pass
        try:
            target["color"] = highlight_color(item.get("color", target["color"]))
        except ValueError:
            pass
    return rules


def hex_value(text: str, maximum: int = 255) -> int:
    text = str(text).strip()
    if not re.fullmatch(r"(?:0[xX])?[0-9a-fA-F]{1,2}", text):
        raise ValueError(f"无效十六进制值：{text or '空值'}")
    value = int(text, 16)
    if value > maximum:
        raise ValueError(f"数值必须在 0–{maximum:X} 范围内：{text}")
    return value


def byte_values(text: str) -> list[int]:
    return [hex_value(x) for x in re.split(r"[,:\s]+", text.strip()) if x]


@dataclass(frozen=True)
class QuickCommand:
    id: str
    name: str
    group: str = "常用"
    source: str = "0"
    destination: str = "5"
    opcode: str = "8F"
    params: str = ""
    shortcut: str = ""
    release_key: bool = False

    @classmethod
    def from_dict(cls, value: dict) -> "QuickCommand":
        name = str(value.get("name", "")).strip()
        if not name:
            raise ValueError("请填写快捷命令名称")
        shortcut = str(value.get("shortcut", "")).strip().upper()
        if shortcut and shortcut not in {f"F{x}" for x in range(5, 13)}:
            raise ValueError("快捷键可选 F5–F12，或留空")
        opcode = hex_value(value.get("opcode", ""))
        params = byte_values(str(value.get("params", "")))
        if len(params) > 14:
            raise ValueError("CEC 参数最多14字节（整帧最多16字节）")
        release = bool(value.get("release_key", False))
        if release and opcode != 0x44:
            raise ValueError("自动释放仅适用于 44 — User Control Pressed")
        return cls(str(value.get("id") or uuid4().hex), name,
                   str(value.get("group", "常用")).strip() or "常用",
                   f"{hex_value(value.get('source', '0'), 15):X}",
                   f"{hex_value(value.get('destination', '5'), 15):X}",
                   f"{opcode:02X}", ":".join(f"{b:02X}" for b in params), shortcut, release)

    def frames(self) -> list[str]:
        header = int(self.source, 16) << 4 | int(self.destination, 16)
        frame = f"{header:02X}:{self.opcode}" + (f":{self.params}" if self.params else "")
        return [frame, f"{header:02X}:45"] if self.release_key else [frame]

    def to_dict(self) -> dict:
        return asdict(self)


def default_quick_commands() -> list[QuickCommand]:
    rows = [
        ("power", "读取电源", "常用", "8F", "", "F5", False),
        ("audio", "读取音量", "常用", "71", "", "F6", False),
        ("osd", "读取OSD", "常用", "46", "", "F7", False),
        ("vendor", "读取厂商ID", "常用", "8C", "", "F8", False),
        ("physical", "读取物理地址", "常用", "83", "", "", False),
        ("version", "读取CEC版本", "常用", "9F", "", "", False),
        ("sad", "查询SAD", "音频", "A4", "01:02:07:0A", "", False),
        ("sam", "读取系统音频模式", "音频", "7D", "", "", False),
        ("arc_on", "请求启动ARC", "ARC", "C3", "", "", False),
        ("arc_off", "请求终止ARC", "ARC", "C4", "", "", False),
        ("volume_up", "音量加", "遥控", "44", "41", "", True),
        ("volume_down", "音量减", "遥控", "44", "42", "", True),
        ("mute", "静音切换", "遥控", "44", "43", "", True),
    ]
    return [QuickCommand(key, name, group, opcode=opcode, params=params, shortcut=keycode,
                         release_key=release) for key, name, group, opcode, params, keycode, release in rows]


def _values(text: str, maximum: int, *, poll: bool = False) -> frozenset:
    if str(text).strip().upper() in {"", "*", "ALL"}:
        return frozenset()
    values = set()
    for item in re.split(r"[,，\s]+", str(text).strip()):
        if poll and item.upper() == "POLL":
            values.add(None)
        else:
            values.add(hex_value(item, maximum))
    return frozenset(values)


def parameter_mask(text: str) -> tuple[tuple[int, int], ...]:
    if str(text).strip() in {"", "*"}:
        return ()
    result = []
    for part in re.split(r"[,:\s]+", str(text).strip()):
        token = part.upper().removeprefix("0X")
        if token in {"?", "*", "**"}:
            token = "??"
        if len(token) == 1 and token in "0123456789ABCDEF":
            token = "0" + token
        if not re.fullmatch(r"[0-9A-F?]{2}", token):
            raise ValueError(f"无效参数掩码：{part}；例如 01:??:A?")
        mask = value = 0
        for nibble in token:
            mask <<= 4
            value <<= 4
            if nibble != "?":
                mask |= 15
                value |= int(nibble, 16)
        result.append((mask, value))
    if len(result) > 14:
        raise ValueError("参数掩码最多14字节")
    return tuple(result)


@dataclass(frozen=True)
class CaptureQuery:
    sources: frozenset = frozenset()
    destinations: frozenset = frozenset()
    opcodes: frozenset = frozenset()
    status: str = ""
    parameters: tuple[tuple[int, int], ...] = ()
    text: str = ""
    bookmarks_only: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "CaptureQuery":
        status = str(data.get("status", "")).strip().upper()
        if status not in {"", "ALL", "ACK", "NACK", "ERROR", "UNKNOWN"}:
            raise ValueError("ACK状态应为 ACK、NACK、ERROR 或 UNKNOWN")
        return cls(_values(data.get("source", ""), 15), _values(data.get("destination", ""), 15),
                   _values(data.get("opcode", ""), 255, poll=True), "" if status == "ALL" else status,
                   parameter_mask(data.get("params", "")), str(data.get("text", "")).strip().casefold(),
                   bool(data.get("bookmarks_only", False)))

    def matches(self, record: dict, annotation: dict | None = None) -> bool:
        if self.sources and hex_value(record.get("source", ""), 15) not in self.sources:
            return False
        if self.destinations and hex_value(record.get("destination", ""), 15) not in self.destinations:
            return False
        if self.opcodes and record.get("opcode") not in self.opcodes:
            return False
        if self.status:
            actual = str(record.get("status", "")).upper()
            actual = next((s for s in ("NACK", "ACK", "ERROR") if actual.startswith(s)), "UNKNOWN")
            if actual != self.status:
                return False
        if self.parameters:
            params = byte_values(str(record.get("params", "")))
            if len(params) < len(self.parameters) or any((b & mask) != value for b, (mask, value) in zip(params, self.parameters)):
                return False
        annotation = annotation or {}
        if self.bookmarks_only and not annotation.get("bookmark"):
            return False
        if self.text:
            haystack = " ".join(str(record.get(key, "")) for key in (
                "frame", "source", "destination", "opcode_text", "opcode_explanation", "frame_bytes",
                "theoretical_duration_ms", "callback_interval_ms", "estimated_gap_ms", "params",
                "params_explanation", "status"))
            haystack += " " + str(annotation.get("note", "")) + " " + " ".join(annotation.get("tags", []))
            if self.text not in haystack.casefold():
                return False
        return True


class AnnotationLibrary:
    """Annotations use session UUID + sequence; clearing capture never rebinds them."""
    def __init__(self, payload: dict | None = None) -> None:
        self.entries: dict[str, dict] = {}
        if payload:
            if payload.get("version") != 1 or not isinstance(payload.get("entries"), list):
                raise ValueError("不支持的书签文件格式")
            for item in payload["entries"]:
                if not isinstance(item, dict) or not isinstance(item.get("record"), dict):
                    raise ValueError("书签记录格式错误")
                session, seq = str(item.get("session_id", "")), int(item.get("seq", 0))
                if not session or seq < 1:
                    raise ValueError("书签缺少会话标识或消息编号")
                if not isinstance(item.get("note", ""), str) or not isinstance(item.get("tags", []), list):
                    raise ValueError("注释应为文字，标签应为列表")
                if not all(isinstance(tag, str) for tag in item.get("tags", [])):
                    raise ValueError("标签应为文字")
                if int(item["record"].get("seq", 0)) != seq:
                    raise ValueError("书签消息编号与快照不一致")
                self.entries[self.key(session, seq)] = deepcopy(item)

    @staticmethod
    def key(session: str, seq: int) -> str:
        return f"{session}:{int(seq)}"

    def get(self, session: str, seq: int) -> dict:
        return self.entries.get(self.key(session, seq), {})

    def set(self, session: str, record: dict, *, bookmark: bool, note: str, tags: list[str]) -> None:
        seq = int(record["seq"])
        key = self.key(session, seq)
        tags = list(dict.fromkeys(tag.strip() for tag in tags if tag.strip()))
        if not bookmark and not note.strip() and not tags:
            self.entries.pop(key, None)
        else:
            self.entries[key] = {"session_id": session, "seq": seq, "bookmark": bool(bookmark),
                                 "note": note.strip(), "tags": tags, "record": deepcopy(record)}

    def payload(self) -> dict:
        return {"version": 1, "entries": list(self.entries.values())}

    def enrich(self, session: str, record: dict) -> dict[str, Any]:
        annotation = self.get(session, int(record["seq"]))
        return dict(record, capture_session_id=session, bookmark=bool(annotation.get("bookmark")),
                    note=annotation.get("note", ""), tags=list(annotation.get("tags", [])))
