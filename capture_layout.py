"""Stable capture data columns and validated display-only preferences."""
CAPTURE_HEADINGS = {
    "seq": "#", "mark": "标记", "time": "时间", "source": "源 LA",
    "destination": "目标 LA", "frame": "报文",
    "theoretical_duration_ms": "理论时长", "callback_interval_ms": "回调间隔",
    "estimated_gap_ms": "间隔−理论", "opcode": "Opcode",
    "opcode_explanation": "Opcode 解释", "params": "参数",
    "params_explanation": "参数解释", "status": "ACK 状态",
}
CAPTURE_COLUMNS = tuple(CAPTURE_HEADINGS)
PREFERRED_WIDTHS = {
    "seq": 42, "mark": 44, "time": 106, "source": 52, "destination": 58, "frame": 140,
    "theoretical_duration_ms": 82, "callback_interval_ms": 86, "estimated_gap_ms": 90,
    "opcode": 170, "opcode_explanation": 215, "params": 100, "params_explanation": 275, "status": 108,
}
MINIMUM_WIDTHS = {
    "seq": 38, "mark": 42, "time": 94, "source": 48, "destination": 54, "frame": 90,
    "theoretical_duration_ms": 68, "callback_interval_ms": 68, "estimated_gap_ms": 76,
    "opcode": 120, "opcode_explanation": 110, "params": 65, "params_explanation": 145, "status": 80,
}
COMPACT_COLUMNS = tuple(c for c in CAPTURE_COLUMNS if c not in {
    "theoretical_duration_ms", "callback_interval_ms", "estimated_gap_ms", "opcode_explanation"})


def normalize_layout(value=None) -> dict:
    value = value if isinstance(value, dict) else {}
    order = value.get("order", [])
    order = order if isinstance(order, list) else []
    order = list(dict.fromkeys(c for c in order if isinstance(c, str) and c in CAPTURE_COLUMNS))
    order.extend(c for c in CAPTURE_COLUMNS if c not in order)
    visible = value.get("visible", list(CAPTURE_COLUMNS))
    visible = visible if isinstance(visible, list) else list(CAPTURE_COLUMNS)
    visible = [c for c in order if c in visible]
    if not visible:
        visible = list(order)
    saved = value.get("widths", {})
    saved = saved if isinstance(saved, dict) else {}
    widths = {}
    for column in CAPTURE_COLUMNS:
        width = saved.get(column, PREFERRED_WIDTHS[column])
        if isinstance(width, bool) or not isinstance(width, (int, float)):
            width = PREFERRED_WIDTHS[column]
        try:
            widths[column] = max(MINIMUM_WIDTHS[column], min(1600, int(width)))
        except (ValueError, OverflowError):
            widths[column] = PREFERRED_WIDTHS[column]
    auto_fit = value.get("auto_fit", True)
    return dict(version=1, order=order, visible=visible, widths=widths,
                auto_fit=auto_fit if isinstance(auto_fit, bool) else True)


def preset_layout(name: str) -> dict:
    return normalize_layout({"visible": list(COMPACT_COLUMNS if name == "COMPACT" else CAPTURE_COLUMNS)})
