"""Structured CEC CTS cases used by the GUI test executor.

The case catalogue is intentionally separate from the Tk application.  It is
based on the CTS/CDF review in ``CEC_认证测试明细_客户CDF对照.docx``.  The
``execution_mode`` field is important: libCEC can send and observe CEC
messages, but it cannot replace an electrical CTS fixture, force an ACK/NACK,
take over arbitration at a selected bit, or corrupt a wire waveform.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


AUTO = "AUTO"
MANUAL = "MANUAL"
EXTERNAL = "EXTERNAL"
CONDITIONAL = "CONDITIONAL"


@dataclass(frozen=True)
class CtsTestCase:
    case_id: str
    chapter: str
    title: str
    execution_mode: str
    initial_status: str
    preconditions: str
    flow: str
    pass_criteria: str
    fail_criteria: str
    limitation: str = ""
    actions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    tags: tuple[str, ...] = field(default_factory=tuple)


def send(
    opcode: int,
    *,
    destination: str | int = "dut",
    source: str | int = "te",
    params: Iterable[int] = (),
    wait_for_ack: bool = True,
) -> dict[str, Any]:
    """Build a message-level action.  ``dut``/``te`` are resolved by the GUI."""
    return {
        "type": "send",
        "opcode": opcode,
        "destination": destination,
        "source": source,
        "params": list(params),
        "wait_for_ack": wait_for_ack,
    }


def expect(
    opcode: int,
    *,
    source: str | int | None = None,
    destination: str | int | None = None,
    params_prefix: Iterable[int] | None = None,
    timeout_ms: int = 1500,
) -> dict[str, Any]:
    return {
        "type": "expect",
        "opcode": opcode,
        "source": source,
        "destination": destination,
        "params_prefix": list(params_prefix) if params_prefix is not None else None,
        "timeout_ms": timeout_ms,
    }


def expect_none(opcode: int, *, source: str | int | None = None, destination: str | int | None = None, timeout_ms: int = 1000) -> dict[str, Any]:
    return {
        "type": "expect_none",
        "opcode": opcode,
        "source": source,
        "destination": destination,
        "timeout_ms": timeout_ms,
    }


def wait(milliseconds: int) -> dict[str, Any]:
    return {"type": "wait", "milliseconds": milliseconds}


def command(command_line: str) -> dict[str, Any]:
    return {"type": "command", "command": command_line}


def _case(
    case_id: str,
    chapter: str,
    title: str,
    mode: str,
    flow: str,
    passed: str,
    failed: str,
    *,
    preconditions: str = "已完成 CDF/设备身份核对；DUT 通过 USB-CEC Adapter 接入目标 CEC 总线。",
    actions: Iterable[dict[str, Any]] = (),
    limitation: str = "",
    tags: Iterable[str] = (),
    initial_status: str | None = None,
) -> CtsTestCase:
    status = initial_status or {
        AUTO: "可自动执行",
        MANUAL: "需人工操作",
        EXTERNAL: "需外部 CTS 设备",
        CONDITIONAL: "条件执行",
    }.get(mode, "待执行")
    return CtsTestCase(
        case_id=case_id,
        chapter=chapter,
        title=title,
        execution_mode=mode,
        initial_status=status,
        preconditions=preconditions,
        flow=flow,
        pass_criteria=passed,
        fail_criteria=failed,
        limitation=limitation,
        actions=tuple(actions),
        tags=tuple(tags),
    )


def _external(case_id: str, chapter: str, title: str, flow: str, passed: str, failed: str, limitation: str, *, preconditions: str = "需要 CEC-CTT/Quantum Data/Simplay CEC Analyzer、CEC breakout 和示波器/故障注入夹具。") -> CtsTestCase:
    return _case(case_id, chapter, title, EXTERNAL, flow, passed, failed, preconditions=preconditions, limitation=limitation)


def _manual(case_id: str, chapter: str, title: str, flow: str, passed: str, failed: str, *, preconditions: str = "连接真实参考设备并准备人工操作；执行前先扫描拓扑并确认 DUT LA/PA。", limitation: str = "libCEC 只能发送/监听消息，不能代替本用例要求的 HPD、用户按键、电气条件或外部设备动作。") -> CtsTestCase:
    return _case(case_id, chapter, title, MANUAL, flow, passed, failed, preconditions=preconditions, limitation=limitation)


def _conditional_from_cdf(case_id: str, chapter: str, title: str, reason: str) -> CtsTestCase:
    return _case(
        case_id,
        chapter,
        title,
        CONDITIONAL,
        "先由测试人员对照当前产品 CDF 判断本项目是否适用；适用时按本项目流程执行，不适用时在测试记录中填写 CDF 排除依据。",
        "项目适用时满足本项目规定的通过条件；项目不适用时，报告中必须保留 CDF 字段、版本和排除理由。",
        "适用但未执行、未记录 CDF 依据，或执行后不满足通过条件。",
        preconditions=f"当前 CDF 未声明或未确认该角色/功能：{reason} 测试人员必须手动决定是否纳入本次测试。",
        limitation="本项目不再自动跳过。工具只提供条件标记，最终是否执行由测试人员依据实际 CDF 决定。",
        initial_status="待测试人员根据 CDF 决定",
    )


def all_cases() -> list[CtsTestCase]:
    cases: list[CtsTestCase] = []

    # CECT 7 - electrical characteristics.
    cases.extend([
        _external("CECT 7-1", "CECT 7", "CEC 电平、负载和波形", "在 27kΩ、3kΩ、150kΩ 等规定负载下发送 Feature Abort，并用示波器测量 L0/L1、过冲和恢复波形。", "L0=0–0.6V，L1=2.5–3.63V，过冲/恢复时间符合 CTS 限值。", "任一电平越界、过冲超限、波形畸变或 DUT 无法完成规定消息。", "libCEC 不提供引脚电气测量和可控负载。"),
        _external("CECT 7-2", "CECT 7", "CEC 上升/下降时间与电容负载", "分别接近 1600pF 和 7700pF 负载，发送 CEC 帧并测量边沿。", "上升时间≤250µs，下降时间≤50µs，所有负载下均满足限值。", "上升/下降时间超限、波形无法稳定识别或消息错误。", "需要外部电容负载、示波器和 CEC 夹具。"),
    ])

    # CECT 8 - bit timing.  These are deliberately external, not falsely
    # reported as passed merely because a frame was accepted by libCEC.
    timing_cases = [
        ("CECT 8.1-1", "Start bit 标称时序", "测量至少 3 个 Start bit，检查低电平和总周期。", "低电平 3.5–3.9ms，总周期 4.3–4.7ms。"),
        ("CECT 8.1-2", "逻辑 1 标称时序", "发送包含逻辑 1 的帧并测量低电平/总周期。", "低电平 0.4–0.8ms，总周期 2.05–2.75ms。"),
        ("CECT 8.1-3", "逻辑 0 标称时序", "发送包含逻辑 0 的帧并测量低电平/总周期。", "低电平 1.3–1.7ms，总周期 2.05–2.75ms。"),
        ("CECT 8.2-1", "Start bit 边界低电平", "在 3.5/3.7/3.9ms 边界发送消息，检查 DUT ACK 与后续处理。", "DUT 在允许边界上正确 ACK，并按规范返回 Feature Abort 或完成处理。"),
        ("CECT 8.2-2", "Start bit 边界高电平", "固定低电平约 3.7ms，分别使用 0.6ms/1.0ms 高电平测试。", "总周期 4.3/4.7ms 边界均被正确处理。"),
        ("CECT 8.2-3", "逻辑 1 低电平边界", "使用 0.4/0.6/0.8ms 低电平发送边界帧。", "边界帧 ACK 和 Feature Abort 行为符合规范。"),
        ("CECT 8.2-4", "逻辑 1 高电平边界", "固定低电平约 0.6ms，使用 1.45/2.15ms 高电平。", "总周期 2.05/2.75ms 边界均被正确处理。"),
        ("CECT 8.2-5", "逻辑 0 低电平边界", "使用 1.3/1.5/1.7ms 低电平发送边界帧。", "边界帧 ACK 和 Feature Abort 行为符合规范。"),
        ("CECT 8.2-6", "逻辑 0 高电平边界", "固定低电平约 1.5ms，使用 0.55/1.25ms 高电平。", "总周期 2.05/2.75ms 边界均被正确处理。"),
    ]
    for case_id, title, flow, passed in timing_cases:
        cases.append(_external(case_id, "CECT 8", title, flow, passed, "边界帧未 ACK、错误响应、丢帧或时序超限。", "libCEC 可以提交完整帧，但不能把每一 bit 的低/高电平精确控制到 CTS 边界。"))

    # CECT 9 - frame communication and robustness.
    cases.extend([
        _external("CECT 9.1-1", "CECT 9", "直接寻址 Abort 与流控重试", "TE 向 DUT 发送直接 Abort，注入流控 NACK，观察 7.2–12ms 重试，最多 5 次。", "每个消息块 ACK=0，重试次数、间隔和最终行为符合规范。", "ACK/重试次数或间隔错误、DUT 错误响应。", "需要可控 NACK/ACK 的 CEC 分析仪。"),
        _external("CECT 9.1-2", "CECT 9", "发往非 DUT 的直接 Abort", "向非 DUT 逻辑地址发送直接 Abort，监听 DUT 是否误响应。", "各消息块 ACK=1，DUT 无响应、无状态改变。", "DUT 产生任何响应或状态改变。", "需要外部设备控制发送地址并确认线上的 ACK。"),
        _external("CECT 9.1-3", "CECT 9", "广播 Abort 与流控重试", "广播 Abort，注入流控 NACK 并观察消息行为。", "ACK=1，DUT 无响应；重试满足规范。", "DUT 响应广播 Abort 或重试不符合规范。", "libCEC 无法强制总线 ACK/NACK。"),
        _case("CECT 9.2-1", "CECT 9", "DUT 对 Abort 的 Feature Abort", AUTO, "TE 以逻辑地址 0 向 DUT LA 发送 Abort；等待 DUT 返回 Feature Abort。", "收到来自 DUT 的 Feature Abort，Header/Initiator/Destination 与测试地址一致。", "无响应、响应地址错误、Feature Abort 参数不合法。", actions=(send(0xFF), expect(0x00, source="dut", destination="te")), limitation="这是消息语义级自动化；完整 ACK 电气判定仍应由外部 CTS 完成。"),
        _case("CECT 9.2-2", "CECT 9", "Give Physical Address 的广播响应", AUTO, "TE 向 DUT 发送 Give Physical Address。", "DUT 返回 Report Physical Address，目的地址必须为广播 0xF，PA/Device Type 正确。", "无响应、目的地址非广播、PA/Device Type 错误。", actions=(send(0x83), expect(0x84, source="dut", destination="broadcast")), limitation="消息级验证；HPD/拓扑切换仍需外部设备。"),
        _external("CECT 9.3-1", "CECT 9", "DUT 响应 Header 不 ACK 的重试", "TE 不 ACK DUT 响应 Header，记录 1–5 次重试及至少 3 个标称 bit 周期的间隔。", "重试次数和间隔符合规范，最终释放总线。", "不重试、无限重试、间隔不足或总线卡死。", "需要外部分析仪抑制 ACK。"),
        _external("CECT 9.3-2", "CECT 9", "DUT Feature Abort 数据块不 ACK 的重试", "对 DUT Feature Abort 数据块不 ACK，观察重试。", "DUT 按 1–5 次重试规则重新发送。", "重试策略或总线释放错误。", "libCEC 不能对指定数据块制造 NACK。"),
        _external("CECT 9.3-3", "CECT 9", "广播 Give Physical Address 的不 ACK 重试", "触发广播 Give Physical Address，TE 对 Header 和每次重试均 NACK。", "DUT 按规范重试 1–5 次，重试间隔合格。", "重试次数、间隔或消息内容错误。", "需要 ACK 抑制夹具。"),
        _external("CECT 9.3-4", "CECT 9", "DUT 高阻阶段被拉低", "在 DUT 消息高阻阶段将 CEC 线拉低，观察 DUT 检测低阻并重试。", "DUT 检测冲突并按规范重试/停止。", "DUT 未检测冲突、错误继续发送或总线锁死。", "需要电气故障注入。"),
        _case("CECT 9.4-1", "CECT 9", "缺少 Operand 的参数消息", AUTO, "针对要求参数的消息删除 Operand；至少覆盖 Active Source 缺少 PA。", "DUT 忽略非法消息，不改变状态、不产生错误响应。", "DUT 崩溃、改变状态、产生不合适响应。", actions=(send(0x82, params=()), expect_none(0x82, source="dut")), limitation="libCEC 可能对发送帧做长度/协议限制；若适配器拒绝构帧，应转 Raw/外部 CTS。"),
        _external("CECT 9.4-2", "CECT 9", "EOM 后追加数据块", "对 Abort 在 EOM=1 后追加第二个 EOM=1 数据块，观察 DUT 是否忽略额外数据。", "忽略多余数据，并对正常 Abort 返回规范 Feature Abort。", "解析越界、改变状态或响应错误。", "需要按数据块/EOM 注入，libCEC 只能发送完整合法帧。"),
        _external("CECT 9.5-1", "CECT 9", "Abort 数据位损坏", "分别翻转 Abort 数据块的 bit3、bit0、bit5、bit6、bit7，监测错误处理。", "低层错误被通知/忽略，不发送 Feature Abort。", "错误帧导致错误响应、状态改变或总线异常。", "需要位级故障注入与物理层监测。"),
        _external("CECT 9.6-1", "CECT 9", "Start bit 期间丢失仲裁", "约 3.5ms 时注入低电平，使 DUT 在 Start bit 期间丢失仲裁。", "DUT 停止当前发送并至少等待 5 个标称 bit 周期后重试。", "DUT 未停止、错误继续发送或重试节奏错误。", "需要精确 bit 时序注入。"),
        _external("CECT 9.6-2", "CECT 9", "数据 bit 期间丢失仲裁", "接管 source=1 的一个 bit 并发送 0，使 DUT LA=5 丢失仲裁。", "DUT 停止发送，至少等待 5 个标称 bit 周期后重试。", "DUT 未识别仲裁丢失或重试错误。", "需要总线主控级仲裁注入。"),
        _external("CECT 9.7-1", "CECT 9", "Signal Free Time", "Feature Abort 完成后用示波器测量总线重新空闲时间。", "Signal Free Time≥5 个标称 bit 周期。", "空闲时间不足或下一帧过早开始。", "需要外部时序测量。"),
        _case("CECT 9.7-2", "CECT 9", "连续发送两个消息", CONDITIONAL, "先根据 CDF 的 Can DUT send two consecutive messages 字段决定是否执行；适用时执行连续消息场景并测量 Signal Free Time。", "若 CDF 声明支持，连续消息和间隔满足 CTS；若 CDF 声明不支持，报告记录 CDF 字段和排除理由。", "适用但不执行、没有记录 CDF 依据，或连续发送行为不满足 CTS。", preconditions="当前 CDF：Can DUT send two consecutive messages=No；测试人员可根据实际产品版本/CDF 版本重新决定。", limitation="本项目不再自动跳过；libCEC 只能观测消息，精确时序仍建议使用外部 CTS 设备。", initial_status="待测试人员根据 CDF 决定"),
    ])

    # CECT 10 - addressing and discovery.
    cases.extend([
        _external("CECT 10.1.2-1", "CECT 10", "上游设备物理地址变化", "TE 先使用 2.0.0.0，断开/恢复 HPD，再改为 1.0.0.0；观察 DUT 广播 Report Physical Address。", "DUT 广播新的 1.0.0.0 物理地址。", "未广播、PA 仍为旧值或地址格式错误。", "HPD 断开/恢复需要 HDMI/CEC 夹具。"),
        _external("CECT 10.1.2-2", "CECT 10", "底层设备物理地址变化", "TE 先使用 1.0.0.0，HPD cycle 后改为 2.3.4.5，观察 DUT 更新。", "DUT 广播新的 2.3.4.5 物理地址。", "未更新、未广播或拓扑地址错误。", "libCEC 不能单独模拟完整 HPD 拓扑。"),
        _manual("CECT 10.2.5-1", "CECT 10", "DUT 逻辑地址分配与设备类型", "给 DUT 分配有效 PA；TE 不响应 LA=5 的轮询；观察 DUT 轮询、占用 LA、广播 Report Physical Address 和 Device Type Audio System。", "DUT 取得 LA=5，广播正确 PA，设备类型为 Audio System。", "未轮询、抢占错误 LA、PA/Device Type 错误或状态不一致。", preconditions="DUT CDF：逻辑地址 5、设备类型 Audio System；需要可控拓扑及 HPD。"),
    ])

    # CECT 11.2 feature cases.
    cases.extend([
        _manual("CECT 11.2.1-1", "CECT 11.2", "One Touch Play", "执行 DUT 本地/远端启动动作，观察 Image View On/Text View On 后的 Active Source。", "DUT 正确响应启动动作并发送 Active Source，画面/音频路径正确。", "无 Active Source、PA 错误、启动后状态不一致。", preconditions="CDF One Touch Play=Y；建议先修正 Active Source Initiator=Y 的 CDF 冲突。"),
        _manual("CECT 11.2.2-1", "CECT 11.2", "Routing Control / Set Stream Path", "TE 广播另一个 Active Source，再向 DUT 发送 Set Stream Path，观察 DUT 选路和 Active Source。", "DUT 正确跟随 Set Stream Path，路由和 Active Source 一致。", "不跟随、路由错误、错误广播 Active Source。", preconditions="CDF Set Stream Path Follower=Y 且 Active Source Initiator=Y。"),
        _manual("CECT 11.2.3-1", "CECT 11.2", "Standby 广播发起", "从 LA 0/1/3/4/5/15 发送广播 Standby，观察 DUT。", "DUT 对每个来源均进入待机。", "任一来源不生效、误响应或状态错误。", preconditions="仅在 CDF 声明 Broadcast Standby Initiator=Y 后执行；当前 CDF 为 No。"),
        _manual("CECT 11.2.3-2", "CECT 11.2", "Standby 广播跟随", "分别从 LA 1、3、4、5、15 广播 Standby。", "DUT 对每个广播来源进入待机。", "任一广播来源不生效或状态错误。"),
        _manual("CECT 11.2.3-3", "CECT 11.2", "Standby 直接寻址跟随", "分别从 LA 1、3、4、5、15 直接向 DUT 发送 Standby。", "DUT 对每个直接消息进入待机。", "不待机、响应错误或影响其他设备。"),
        _manual("CECT 11.2.3-4", "CECT 11.2", "DUT 本地 Standby", "执行 DUT 本地待机动作，监听总线。", "DUT 进入待机且不发送 CDF 禁止的广播 Standby。", "未待机、错误广播或其他设备被误操作。"),
        _case("CECT 11.2.6-1", "CECT 11.2", "逻辑地址轮询 ACK", AUTO, "轮询 DUT 声明的 LA=5。", "DUT 对轮询 ACK。", "NACK、超时或实际 LA 与 CDF 不一致。", actions=(command("poll 5"),), limitation="仅验证适配器看到的轮询结果；完整 LA 分配过程需外部拓扑控制。"),
        _case("CECT 11.2.6-2", "CECT 11.2", "Give Physical Address", AUTO, "模拟 LA 0/1/3/4/5/15（排除 DUT LA=5），向 DUT 发送 Give Physical Address。", "DUT 广播正确 Report Physical Address，包含正确 Device Type。", "未广播、目的地址错误、PA/Device Type 错误。", actions=(send(0x83, destination="dut"), expect(0x84, source="dut", destination="broadcast"))),
        _case("CECT 11.2.6-3", "CECT 11.2", "Menu Language - 支持语言", CONDITIONAL, "先根据 CDF 判断是否声明菜单/语言能力；适用时模拟 TV/菜单设备发送 Get Menu Language。", "适用时语言交互符合 CDF 与规范；不适用时报告保留 CDF 排除依据。", "适用但未执行、未记录 CDF 依据，或语言交互错误。", preconditions="当前 CDF 未声明菜单/OSD 功能；测试人员需手动决定是否纳入。", limitation="条件项目不自动跳过。"),
        _case("CECT 11.2.6-4", "CECT 11.2", "Menu Language - 不支持语言", CONDITIONAL, "先根据 CDF 判断是否声明菜单/语言能力；适用时发送不支持语言场景并检查 DUT。", "适用时不支持语言不导致错误状态或非法响应；不适用时记录 CDF 排除依据。", "适用但未执行、未记录 CDF 依据，或 DUT 错误响应/崩溃。", preconditions="当前 CDF 未声明菜单/OSD 功能；测试人员需手动决定是否纳入。", limitation="条件项目不自动跳过。"),
        _case("CECT 11.2.6-5", "CECT 11.2", "非 TV Source 的 Menu Language", CONDITIONAL, "先根据 CDF 判断是否声明菜单/语言能力；适用时由非 TV Source 发起 Get Menu Language。", "适用时按规范忽略或正确处理；不适用时记录 CDF 排除依据。", "适用但未执行、未记录 CDF 依据，或 DUT 错误接受/响应。", preconditions="当前 CDF 未声明菜单/OSD 功能；测试人员需手动决定是否纳入。", limitation="条件项目不自动跳过。"),
        _case("CECT 11.2.6-6", "CECT 11.2", "Get CEC Version", AUTO, "向 DUT 发送 Get CEC Version。", "DUT 返回与实际实现、CDF 一致的 CEC Version。", "无响应、版本错误或与 CDF 不一致。", actions=(send(0x9F), expect(0x9E, source="dut", destination="te")), limitation="当前 CDF CEC Version 为空，需先补齐 CDF 后才可作为认证结论。"),
        _case("CECT 11.2.6-7", "CECT 11.2", "无菜单能力时的 Get Menu Language", AUTO, "向 DUT 发送 Get Menu Language。", "DUT 不发送 Set Menu Language，不改变状态。", "DUT 错误发送语言消息、改变状态或异常响应。", actions=(send(0x91), expect_none(0x32, source="dut")), limitation="当前 CDF 未声明菜单/OSD，自动项只验证无误响应。"),
        _case("CECT 11.2.9-1", "CECT 11.2", "Give Vendor ID", AUTO, "模拟 LA 0/1/3/4/5/15（排除 DUT），发送 Give Vendor ID。", "DUT 广播 Device Vendor ID=08 00 46。", "无响应、Vendor ID 错误或目的地址错误。", actions=(send(0x8C), expect(0x87, source="dut", destination="broadcast")), limitation="当前 CDF 声明 Vendor ID=08 00 46；若产品实际 Vendor 不同应先修正 CDF。"),
        _external("CECT 11.2.9-2", "CECT 11.2", "HPD 后 Vendor ID 初始化广播", "HPD cycle 后将 TE PA 设为 1.0.0.0，观察 DUT 初始化广播 Vendor ID。", "DUT 广播正确 Vendor ID。", "未广播或 ID 错误。", "需要 HPD/拓扑夹具。"),
        _conditional_from_cdf("CECT 11.2.9-3", "CECT 11.2", "不同 Vendor 的 Vendor Command", "当前 CDF Vendor Command Initiator=No 且 Different Vendor ID 为空。"),
        _case("CECT 11.2.11-1", "CECT 11.2", "Give OSD Name", AUTO, "模拟 LA 0/1/3/4/5（排除 DUT），向 DUT 发送 Give OSD Name。", "DUT 返回正确 Set OSD Name，名称为 CDF 声明的 HW-B450F。", "无响应、地址错误、名称错误或乱码。", actions=(send(0x46), expect(0x47, source="dut", destination="te")), limitation="当前 CDF/设备资料中的 OSD 名称需统一后再判定认证结果。"),
        _case("CECT 11.2.11-2", "CECT 11.2", "未注册 LA 的 Give OSD Name", AUTO, "向未注册 LA=15 发送 Give OSD Name，监听 DUT。", "DUT 忽略，不响应，不改变 OSD/状态。", "DUT 误响应、显示变化或状态改变。", actions=(send(0x46, destination="broadcast"), expect_none(0x47, source="dut")), limitation="广播地址的具体响应还应结合外部拓扑确认。"),
        _manual("CECT 11.2.13-1", "CECT 11.2", "Remote Control Pressed/Released", "发送 User Control Pressed + Released，对比 DUT 本地按键行为。", "远程按键行为与本地按键一致，Released 后停止。", "按键不生效、释放不生效或产生危险持续动作。", preconditions="当前 CDF Remote Control Pass Through=Yes，但 Released Follower=No；需先修正冲突。"),
        _manual("CECT 11.2.13-2", "CECT 11.2", "Remote Control Press and Hold", "以约 450ms 周期重复 Pressed，持续至少 5s，随后 Released。", "持续动作正常，并在安全超时内停止。", "不连续、无法停止或超时后仍持续动作。", preconditions="CDF Press and Hold=Yes 后执行；当前为 No。"),
        _manual("CECT 11.2.13-3", "CECT 11.2", "Remote Control Press and Hold 安全超时", "持续发送 Pressed，超过安全超时后检查 DUT。", "DUT 在安全超时内停止持续动作。", "超过安全超时仍继续动作。", preconditions="CDF Press and Hold=Yes 后执行；当前为 No。"),
        _manual("CECT 11.2.13-4", "CECT 11.2", "Remote Control 中断", "持续 Pressed 后发送另一个 UI 命令，观察原动作是否停止。", "新 UI 命令到来后旧的持续动作停止。", "旧动作继续、两个动作叠加或无法恢复。", preconditions="CDF Press and Hold=Yes 后执行；当前为 No。"),
        _manual("CECT 11.2.14-1", "CECT 11.2", "Power Status - On", "让 DUT 上电，发送 Give Device Power Status。", "DUT 返回 Report Power Status=On。", "无响应或电源状态错误。"),
        _manual("CECT 11.2.14-2", "CECT 11.2", "Power Status - Standby", "让 DUT 待机，发送 Give Device Power Status。", "DUT 返回 Report Power Status=Standby。", "无响应或电源状态错误。"),
    ])

    # System Audio Control.  Message-only queries are automatic; stateful
    # power/volume/timeout cases remain conditional/manual.
    audio_cases: list[tuple[str, str, str, str, str, str, Iterable[dict[str, Any]]]] = [
        ("CECT 11.2.15-1", "System Audio Mode Request 基本流程", "模拟 LA0/3 发送 System Audio Mode Request [0.0.0.0]。", "DUT 广播 Set System Audio Mode On。", "无广播、状态错误或消息格式错误。", AUTO, (send(0x70, params=(0x00, 0x00)), expect(0x72, source="dut", destination="broadcast"))),
        ("CECT 11.2.15-2", "System Audio 直接启动成功", "TE LA0 与 DUT Request Active Source，DUT 返回 Active Source 后等待直接 Set System Audio Mode On。", "DUT 向 LA0 发送直接 Set On，并广播 Set On。", "未发送、地址错误或多余广播。", CONDITIONAL, ()),
        ("CECT 11.2.15-3", "System Audio 直接启动失败", "TE 对直接启动请求返回 Feature Abort，观察 DUT 后续行为。", "1s 内不出现广播 Set System Audio Mode On。", "仍广播 Set On 或状态被错误打开。", CONDITIONAL, ()),
        ("CECT 11.2.15-4", "System Audio Mode Status - On", "请求 System Audio On 后发送 Give System Audio Mode Status。", "收到 Set On 和 Status On。", "无响应、状态不一致或消息地址错误。", AUTO, (send(0x7D), expect(0x7E, source="dut", destination="te"))),
        ("CECT 11.2.15-5", "System Audio Mode Request 无 Operand", "发送无 Operand 或格式异常的 System Audio Mode Request。", "DUT 广播 Set System Audio Mode Off。", "崩溃、无响应或错误广播。", MANUAL, ()),
        ("CECT 11.2.15-6", "System Audio On 后待机", "System Audio On 状态下让 DUT 待机。", "进入待机前发送 Set System Audio Mode Off。", "未关音频模式、顺序错误或待机失败。", MANUAL, ()),
        ("CECT 11.2.15-7", "System Audio Mode Status - Off", "关闭 System Audio 后发送 Give System Audio Mode Status。", "报告 Status Off。", "状态错误或无响应。", MANUAL, ()),
        ("CECT 11.2.15-8", "System Audio Mute", "System Audio On 后发送 User Control Pressed/Released Mute。", "DUT 静音并正确处理 Released。", "静音状态错误、Released 无效或其他状态被改变。", MANUAL, ()),
        ("CECT 11.2.15-9", "Give Audio Status", "向 DUT 发送 Give Audio Status。", "Report Audio Status 与实际音量/静音状态一致。", "无响应、音量/静音位错误。", AUTO, (send(0x71), expect(0x7A, source="dut", destination="te"))),
        ("CECT 11.2.15-10", "Standby 后由 DUT 请求 System Audio On", "DUT 待机后向 LA5 发送 Give System Audio Mode Status，观察请求流程。", "DUT 重新进入 System Audio On 的行为符合规范。", "未请求、错误广播或状态不一致。", CONDITIONAL, ()),
        ("CECT 11.2.15-11", "DUT 作为 Set System Audio Mode Follower", "模拟 LA5 控制 System Audio Mode，观察 DUT 跟随。", "DUT 正确跟随 On/Off。", "不跟随或错误广播。", CONDITIONAL, ()),
        ("CECT 11.2.15-12", "DUT 作为 Set System Audio Mode Follower 的状态", "重复 On/Off 切换，检查音频状态和广播。", "状态、静音和广播均符合规范。", "任一状态不一致。", CONDITIONAL, ()),
        ("CECT 11.2.15-13", "Request Short Audio Descriptors - 支持格式", "请求 DUT 支持的短音频描述符。", "仅对请求且支持的格式返回 Report Short Audio Descriptor。", "漏回、回不支持格式或描述符错误。", MANUAL, ()),
        ("CECT 11.2.15-14", "Request Short Audio Descriptors - 不支持格式", "仅请求不支持的音频格式。", "返回 Feature Abort=Invalid Operand。", "返回描述符、无错误处理或状态改变。", MANUAL, ()),
        ("CECT 11.2.15-15", "DUT 发起 Request Short Audio Descriptor", "模拟下游设备并让 DUT 请求其音频描述符。", "请求和返回格式符合 CDF 声明。", "DUT 不请求、请求错误或格式错误。", CONDITIONAL, ()),
        ("CECT 11.2.15-16", "System Audio Off 到 On", "Off 状态下请求 System Audio On。", "广播 Set On 且取消静音。", "未广播、未取消静音或状态错误。", MANUAL, ()),
        ("CECT 11.2.15-17", "System Audio On 到 Off", "On 状态下请求 System Audio Off。", "广播 Set Off 且静音。", "未广播、未静音或状态错误。", MANUAL, ()),
        ("CECT 11.2.15-18", "896ms 延迟 Feature Abort - 成功路径", "对直接启动请求延迟约 896±16ms 后返回 Feature Abort。", "DUT 发送直接 Set On，并在 1s 内不产生额外错误广播。", "超时、提前广播或错误状态。", EXTERNAL, ()),
        ("CECT 11.2.15-19", "896ms 延迟 Feature Abort - 失败路径", "重复延迟 Feature Abort 场景并检查 DUT 超时策略。", "DUT 的直接消息、超时和广播行为符合规范。", "重试/广播/超时策略错误。", EXTERNAL, ()),
    ]
    for case_id, title, flow, passed, failed, mode, actions in audio_cases:
        limitation = ""
        preconditions = "CDF 声明 DUT 为 Audio System，且已连接真实 TV/Source。"
        if case_id in {"CECT 11.2.15-2", "CECT 11.2.15-3"}:
            preconditions += " 当前 CDF 的 System Audio 直接启动过程尚未完整定义，需先补齐。"
        if case_id in {"CECT 11.2.15-10", "CECT 11.2.15-11", "CECT 11.2.15-12", "CECT 11.2.15-15"}:
            preconditions += " 当前 CDF 对应 Initiator/Follower 能力为 No；测试人员需手动决定是否启用本项目。"
        if mode == AUTO:
            limitation = "自动项只验证 libCEC 可观察到的消息语义；音频实际切换、静音听感和电源联动仍需人工确认。"
        elif mode == EXTERNAL:
            limitation = "需要外部 CTS 设备生成精确延迟/Feature Abort。"
        cases.append(_case(case_id, "CECT 11.2", title, mode, flow, passed, failed, preconditions=preconditions, actions=actions, limitation=limitation))

    # ARC.
    arc_cases = [
        ("CECT 11.2.17-1", "本地 ARC 启动", "广播 Report Physical Address 后执行本地 ARC start。", "DUT 向对端直接发送 Initiate ARC。", "未发送、地址错误或非法启动。"),
        ("CECT 11.2.17-2", "本地 ARC 终止", "ARC 已建立后执行本地 terminate。", "DUT 向对端直接发送 Terminate ARC。", "未终止、消息错误或音频链路仍保持。"),
        ("CECT 11.2.17-3", "直接 Request ARC Initiation", "广播 PA 后，TE 直接向 DUT 发送 Request ARC Initiation。", "DUT 直接发送 Initiate ARC。", "未响应或向错误地址发送。"),
        ("CECT 11.2.17-4", "直接 Request ARC Termination", "TE 直接向 DUT 发送 Request ARC Termination。", "DUT 直接发送 Terminate ARC。", "未响应或消息错误。"),
        ("CECT 11.2.17-5", "非相邻设备 Request ARC", "TE PA=0.0.0.0，DUT PA=1.1.0.0，模拟非相邻请求。", "DUT 不发送 Initiate ARC。", "DUT 错误建立 ARC 或误发送 Initiate。"),
        ("CECT 11.2.17-6", "非 ARC 输出不触发 ARC", "向不支持 ARC 的输出发送 Request ARC Initiation。", "不发送 Initiate ARC。", "非 ARC 输出触发 ARC。"),
        ("CECT 11.2.17-7", "输入侧 ARC - Initiation", "在存在 HDMI 输入的配置下请求输入侧 ARC Initiation。", "输入侧消息和状态符合规范。", "消息、地址或状态错误。"),
        ("CECT 11.2.17-8", "输入侧 ARC - Termination", "在存在 HDMI 输入的配置下请求输入侧 ARC Termination。", "正确终止输入侧 ARC。", "无法终止或错误广播。"),
        ("CECT 11.2.17-9", "输入侧 ARC - 非相邻", "对非相邻输入设备请求 ARC。", "DUT 忽略请求，不建立 ARC。", "错误建立或误发送。"),
        ("CECT 11.2.17-10", "输入侧 ARC - 无 ARC 输出", "对不支持 ARC 的输入/输出组合请求 ARC。", "DUT 不发送 Initiate ARC。", "错误触发 ARC。"),
        ("CECT 11.2.17-11", "输入侧 ARC - 状态切换", "重复启动/终止输入侧 ARC 并检查状态。", "状态切换和消息顺序符合规范。", "状态卡死、顺序错误或重复消息。"),
        ("CECT 11.2.17-12", "输入侧 ARC - 异常恢复", "中断 ARC 建立流程后重新请求，检查恢复。", "异常后可重新建立或正确拒绝。", "总线/音频状态无法恢复。"),
    ]
    for index, (case_id, title, flow, passed, failed) in enumerate(arc_cases, start=1):
        if index <= 5:
            mode = MANUAL
            preconditions = "DUT CDF：1 个 HDMI 输出，ARC Rx on output 1=Yes；需要真实 ARC TV/Audio System。"
            limitation = "libCEC 可观察 CEC ARC 消息，但不能验证 HDMI ARC/eARC 音频链路本身。"
        else:
            mode = CONDITIONAL
            preconditions = "当前 CDF HDMI input count=0 或仅声明一个支持 ARC 的输出。"
            limitation = "本项目不再自动跳过；测试人员应根据当前 CDF 决定是否执行，适用时再接入对应 HDMI 输入/非 ARC 输出。"
        cases.append(_case(case_id, "CECT 11.2", title, mode, flow, passed, failed, preconditions=preconditions, limitation=limitation))

    # CECT 12 - message addressing and unsupported-feature behavior.
    cases.extend([
        _case("CECT 12-1", "CECT 12", "只能广播的消息被直接寻址", AUTO, "将 Active Source、Routing Change、Routing Information、Set Stream Path、Standby 等广播消息改为直接发往 DUT。", "DUT 忽略消息，不响应、不改变状态。", "DUT 响应、改变路由/待机状态或产生错误广播。", actions=(send(0x82, params=(0x10, 0x00)), expect_none(0x82, source="dut")), limitation="当前自动步骤覆盖 Active Source 代表项；其余 opcode 可在用例动作中扩展。"),
        _case("CECT 12-2", "CECT 12", "只能直接寻址的消息被广播", AUTO, "将 Give Physical Address、Get CEC Version、Give Vendor ID、Give OSD Name、User Control Pressed、Give Power Status 等改为广播。", "DUT 忽略消息，不响应、不改变状态。", "DUT 响应、改变状态或产生错误广播。", actions=(send(0x83, destination="broadcast"), expect_none(0x84, source="dut")), limitation="当前自动步骤覆盖 Give Physical Address 代表项；其余 opcode 可扩展。"),
        _case("CECT 12-3", "CECT 12", "CDF 未声明功能的广播消息", AUTO, "发送当前 CDF 未声明的广播功能消息，监听 DUT。", "DUT 忽略，不发送 Feature Abort，不改变状态。", "DUT 错误响应、发送 Feature Abort 或改变状态。", actions=(send(0x09, destination="broadcast"), expect_none(0x09, source="dut")), limitation="当前自动步骤使用 One Touch Record 代表项；完整 opcode 矩阵可按产品 CDF 继续扩展。"),
    ])

    # CDF-declared unsupported feature cases.  They remain visible so the
    # report explains why the authentication matrix is not silently smaller.
    skip_cases = [
        ("CECT 10.1.1", "设备类型寻址基础", "CDF 未声明 TV/Recording/Tuner/Playback 等对应角色。"),
        ("CECT 10.2.1", "TV 设备逻辑地址分配", "CDF 不声明 TV。"),
        ("CECT 10.2.2", "Recording Device 逻辑地址分配", "CDF 不声明 Recording Device。"),
        ("CECT 10.2.3", "Tuner 逻辑地址分配", "CDF 不声明 Tuner。"),
        ("CECT 10.2.4", "Playback Device 逻辑地址分配", "CDF 不声明 Playback Device。"),
        ("CECT 10.2.6", "CEC Switch 逻辑地址分配", "CDF 不声明 CEC Switch。"),
        ("CECT 11.2.4", "One Touch Record", "CDF One Touch Record=No。"),
        ("CECT 11.2.5", "Timer Programming", "CDF Timer Programming=No。"),
        ("CECT 11.2.7", "Deck Control", "CDF Deck Control=No。"),
        ("CECT 11.2.8", "Tuner Control", "CDF Tuner Control=No。"),
        ("CECT 11.2.10", "OSD Display", "CDF OSD Display=No。"),
        ("CECT 11.2.12", "Device Menu Control", "CDF Device Menu Control=No。"),
        ("CECT 11.2.16", "Audio Rate Control", "CDF Audio Rate Control=No。"),
        ("CECT 11.3", "CEC Switch 测试", "CDF CEC Switch=No。"),
    ]
    for case_id, title, reason in skip_cases:
        cases.append(_conditional_from_cdf(case_id, "CDF Conditional", title, reason))

    return cases


CASES = all_cases()
CASE_BY_ID = {case.case_id: case for case in CASES}
