"""One script grammar with explicit compatibility for legacy command pacing."""
from dataclasses import dataclass
import math
from pathlib import Path


@dataclass(frozen=True)
class ScriptStep:
    line_number: int
    kind: str
    value: str


@dataclass(frozen=True)
class ScriptPlan:
    dialect: str
    interval_ms: float
    steps: tuple[ScriptStep, ...]


def compile_script(lines, dialect="AUTO", legacy_interval_ms=0.2, path_hint="") -> ScriptPlan:
    dialect = str(dialect).upper()
    if dialect not in {"AUTO", "SCENARIO", "LEGACY"}:
        raise ValueError("执行节奏应为 AUTO、SCENARIO 或 LEGACY")
    steps = []
    for number, raw in enumerate(lines, 1):
        line = raw.lstrip("\ufeff").strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(maxsplit=1)
        kind = parts[0].lower()
        if kind in {"wait", "@wait", "expect", "expect-opcode", "assert"}:
            if len(parts) != 2:
                raise ValueError(f"第 {number} 行 {parts[0]} 缺少参数")
            value = parts[1]
            if kind in {"wait", "@wait"}:
                try:
                    delay = float(value)
                except ValueError:
                    raise ValueError(f"第 {number} 行等待时间应为毫秒数") from None
                if not math.isfinite(delay) or delay < 0:
                    raise ValueError(f"第 {number} 行等待时间应为非负有限数")
            steps.append(ScriptStep(number, kind, value))
        else:
            steps.append(ScriptStep(number, "command", line))
    if dialect == "AUTO":
        if any(step.kind in {"wait", "expect", "expect-opcode", "assert"} for step in steps):
            dialect = "SCENARIO"
        elif any(step.kind == "@wait" for step in steps) or Path(path_hint).suffix.lower() == ".cmd":
            dialect = "LEGACY"
        else:
            dialect = "SCENARIO"
    if dialect == "LEGACY":
        try:
            interval = float(legacy_interval_ms)
        except (ValueError, TypeError):
            raise ValueError("旧命令间隔必须是毫秒数") from None
        if not math.isfinite(interval) or interval < 0:
            raise ValueError("旧命令间隔应为非负有限数")
    else:
        interval = 80.0
    return ScriptPlan(dialect, interval, tuple(steps))
