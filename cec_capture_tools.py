"""Independent CEC length rules, offline capture readers and export writers.

Length counts include the header and opcode. This is a structural check,
not a CTS verdict. No reference-checker files, DLL calls or log-event parser
are used by live capture. Standard layouts were cross-checked against Linux
include/uapi/linux/cec-funcs.h; conditional/vendor data stays explicit.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
from uuid import uuid4


RULE_VERSION = "cec-length-1"
# Operand lengths: fixed, inclusive range, or discrete alternatives.
FIXED_PARAMETERS = {
    **dict.fromkeys((0x04, 0x05, 0x06, 0x0B, 0x0D, 0x0F, 0x36, 0x45, 0x46,
                     0x71, 0x7D, 0x83, 0x85, 0x8B, 0x8C, 0x8F, 0x91, 0x9F,
                     0xA5, 0xC0, 0xC1, 0xC2, 0xC3, 0xC4, 0xC5, 0xFF), 0),
    **dict.fromkeys((0x0A, 0x1A, 0x1B, 0x41, 0x42, 0x72, 0x73, 0x7A, 0x7E,
                     0x8D, 0x8E, 0x90, 0x9E), 1),
    **dict.fromkeys((0x00, 0x81, 0x82, 0x86, 0x9D), 2),
    **dict.fromkeys((0x32, 0x84, 0x87), 3),
    0x80: 4,
}
RANGE_PARAMETERS = {0x47: (1, 14), 0x64: (1, 14), 0xA0: (3, 14),
                    0xA4: (1, 4), 0xA6: (4, 14), 0xF8: (3, 14)}
DISCRETE_PARAMETERS = {0x43: (1, 3), 0x70: (0, 2), 0xA3: (3, 6, 9, 12)}
CHECK_LABELS = {"OK": "长度符合已知规则", "ERROR": "非法数据块",
                "NOT_COVERED": "暂无长度规则"}


def parse_frame(value: str) -> list[int]:
    value = str(value).strip()
    if not value:
        raise ValueError("完整报文为空")
    if re.fullmatch(r"[0-9A-Fa-f]{2,}", value) and len(value) % 2 == 0:
        return [int(value[i:i+2], 16) for i in range(0, len(value), 2)]
    parts = re.split(r"[:\s,]+", value)
    if not all(re.fullmatch(r"(?:0[xX])?[0-9A-Fa-f]{1,2}", p) for p in parts):
        raise ValueError("报文含非十六进制字节或不完整字节")
    return [int(p, 16) for p in parts]


def check_frame_length(value: str | list[int]) -> dict:
    result = {"version": RULE_VERSION, "status": "OK", "actual_bytes": 0,
              "expected": "", "message": ""}
    try:
        frame = parse_frame(value) if isinstance(value, str) else list(value)
        if not all(isinstance(x, int) and 0 <= x <= 255 for x in frame):
            raise ValueError("报文含无效字节值")
    except (ValueError, TypeError) as exc:
        return dict(result, status="ERROR", message=str(exc))
    result["actual_bytes"] = len(frame)
    if not 1 <= len(frame) <= 16:
        return dict(result, status="ERROR", expected="1–16 字节",
                    message=f"CEC 完整帧应为 1–16 字节，实际 {len(frame)} 字节")
    if len(frame) == 1:
        return dict(result, expected="1 字节", message="Polling 帧，只有 Header，无 Opcode")
    opcode, params = frame[1], frame[2:]
    allowed = None
    if opcode in FIXED_PARAMETERS:
        allowed = (FIXED_PARAMETERS[opcode],)
    elif opcode in DISCRETE_PARAMETERS:
        allowed = DISCRETE_PARAMETERS[opcode]
    elif opcode == 0x09 and params:
        # Record Source Type controls the remaining Record On operands.
        size = {1: 1, 2: 8, 3: 5, 4: 2, 5: 3}.get(params[0])
        if size is not None:
            allowed = (size,)
    elif opcode == 0x44 and params:
        optional_one = {0x56, 0x57, 0x60, 0x68, 0x69, 0x6A}
        if params[0] <= 0x76:
            allowed = (1, 2) if params[0] in optional_one else (1, 5) if params[0] == 0x67 else (1,)
    if opcode in (0x09, 0x44) and not params:
        return dict(result, status="ERROR", expected="至少 3 字节",
                    message=f"0x{opcode:02X} 缺少决定后续格式的首个参数")
    if allowed is not None:
        sizes = tuple(n + 2 for n in allowed)
        result["expected"] = "/".join(map(str, sizes)) + " 字节"
        valid = len(frame) in sizes
    elif opcode in RANGE_PARAMETERS:
        low, high = RANGE_PARAMETERS[opcode]
        result["expected"] = f"{low + 2}–{high + 2} 字节"
        valid = low <= len(params) <= high
    else:
        return dict(result, status="NOT_COVERED",
                    message=f"0x{opcode:02X} 暂无可确认的长度规则；保留原始报文")
    result["status"] = "OK" if valid else "ERROR"
    result["message"] = (f"0x{opcode:02X} 完整帧应为 {result['expected']}，实际 {len(frame)} 字节"
                         + ("；长度符合" if valid else f"（参数 {len(params)} 字节）"))
    return result


def finite_number(value):
    if value is None or str(value).strip() in {"", "None", "null", "—"}:
        return None
    try:
        number = float(value)
        return (int(number) if number.is_integer() else number) if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def boolean(value) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes", "是"}


@dataclass
class CaptureDocument:
    records: list[dict]
    sha256: str
    skipped_lines: int = 0


def _decode_file(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("gb18030")


def read_capture_file(path: str | Path) -> CaptureDocument:
    path = Path(path)
    raw = path.read_bytes()
    text = _decode_file(raw)
    skipped = 0
    if path.suffix.lower() == ".json" or text.lstrip().startswith(("[", "{")):
        payload = json.loads(text)
        rows = payload.get("records") if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ValueError("JSON 应为抓包记录数组，或包含 records 数组")
    elif path.suffix.lower() in {".csv", ".tsv"}:
        first = text.splitlines()[0] if text.splitlines() else ""
        reader = csv.DictReader(io.StringIO(text), delimiter="\t" if "\t" in first else ",")
        if not reader.fieldnames or not any(x in reader.fieldnames for x in ("frame", "报文", "完整报文")):
            raise ValueError("表格缺少完整报文列（frame / 报文 / 完整报文）")
        rows = list(reader)
    else:
        rows = []
        # Only the application's own packet log or explicit hexadecimal lines.
        for number, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"(?:(\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s+)?(RX|TX)\s+(.+)", line, re.I)
            if match:
                stamp, direction, packet = match.groups()
                packet, separator, status = packet.partition(" -> ")
                rows.append(dict(frame=packet.strip(), direction=direction.upper(), time=stamp or "—",
                                 status=status.strip() if separator else "—", source_line=number))
            elif re.fullmatch(r"[0-9A-Fa-f:,\s]+", line):
                rows.append(dict(frame=line, source_line=number))
            else:
                skipped += 1
        if not rows:
            raise ValueError("未找到可读取的报文；支持本工具 CSV/JSON、RX/TX 报文日志及逐行十六进制报文")
    records = []
    for number, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"第 {number} 条记录不是对象")
        frame = row.get("frame", row.get("报文", row.get("完整报文")))
        if not isinstance(frame, str):
            raise ValueError(f"第 {number} 条记录缺少完整报文文本")
        record = dict(row)
        record.update(frame=frame, origin_seq=row.get("origin_seq") or row.get("seq") or number,
                      origin_capture_session_id=row.get("origin_capture_session_id") or row.get("capture_session_id") or "",
                      timestamp_ms=finite_number(row.get("timestamp_ms")),
                      time=str(row.get("time") or "—"),
                      direction=str(row.get("direction") or "UNKNOWN").upper(),
                      status=str(row.get("status") or "—"), bookmark=boolean(row.get("bookmark")),
                      note=str(row.get("note") or ""))
        tags = row.get("tags", [])
        if isinstance(tags, str):
            try:
                decoded_tags = json.loads(tags) if tags.lstrip().startswith("[") else None
            except ValueError:
                decoded_tags = None
            tags = decoded_tags if isinstance(decoded_tags, list) else [x.strip() for x in re.split(r"[,，]", tags) if x.strip()]
        record["tags"] = [str(x) for x in tags] if isinstance(tags, list) else []
        records.append(record)
    return CaptureDocument(records, hashlib.sha256(raw).hexdigest(), skipped)


EXPORT_FIELDS = (
    ("seq", "消息编号"), ("time", "时间"), ("direction", "方向"),
    ("source", "源 LA"), ("destination", "目标 LA"), ("frame", "完整报文"),
    ("frame_bytes", "字节数"),
    ("opcode_text", "Opcode 名称"), ("opcode_explanation", "Opcode 解释"),
    ("params", "原始参数"), ("params_explanation", "参数解释"), ("status", "ACK 状态"),
    ("timestamp_ms", "原生时间戳"), ("theoretical_duration_ms", "理论帧时长"),
    ("callback_interval_ms", "相邻回调间隔"), ("estimated_gap_ms", "间隔减理论"),
    ("length_status", "长度检查状态"), ("length_message", "长度检查说明"),
    ("bookmark", "书签"), ("tags", "标签"), ("note", "注释"),
    ("capture_session_id", "会话"), ("origin_seq", "导入前编号"),
    ("origin_capture_session_id", "原会话"),
)


def export_text(records: list[dict], format_name: str, fields: list[str] | None = None) -> str:
    format_name = format_name.upper()
    if format_name == "RAW":
        return "".join(str(r.get("frame", "")) + "\n" for r in records)
    if format_name == "OPCODE":
        return "".join(str(r.get("opcode_text", "")) + "\n" for r in records)
    if format_name == "JSON":
        values = records if fields is None else [{key: r.get(key) for key in fields} for r in records]
        return json.dumps(values, ensure_ascii=False, indent=2)
    if format_name not in {"CSV", "TSV"}:
        raise ValueError(f"不支持的导出格式：{format_name}")
    fields = fields or [name for name, _ in EXPORT_FIELDS]
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, delimiter="\t" if format_name == "TSV" else ",", lineterminator="\n")
    writer.writerow(fields)
    for record in records:
        row = []
        for key in fields:
            value = record.get(key, "")
            row.append(json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value)
        writer.writerow(row)
    return stream.getvalue()


def write_export(path: str | Path, records: list[dict], format_name: str, fields: list[str] | None = None) -> None:
    path = Path(path)
    payload = export_text(records, format_name, fields)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temporary.write_text(payload, encoding="utf-8-sig" if format_name.upper() == "CSV" else "utf-8", newline="")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
