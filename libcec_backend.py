"""Direct libCEC C API backend for the CEC test GUI.

The GUI intentionally keeps all Tk work on the main thread.  libCEC invokes
callbacks from its worker threads, so this module copies callback data into a
thread-safe event queue and exposes a small, Python-friendly API to the UI.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


CEC_MAX_DATA_PACKET_SIZE = 64
CEC_MAX_FRAME_SIZE = 16
CEC_LIB_VERSION_CURRENT = (8 << 16) | (1 << 8) | 6
CEC_INVALID_PHYSICAL_ADDRESS = 0xFFFF
CEC_INVALID_VENDOR_ID = 0xFFFFFF
CECDEVICE_UNREGISTERED = 15
CECDEVICE_BROADCAST = 15


class LibCecError(RuntimeError):
    """Raised when the native libCEC backend cannot complete an operation."""


class CecDataPacket(ctypes.Structure):
    _fields_ = [("data", ctypes.c_uint8 * CEC_MAX_DATA_PACKET_SIZE), ("size", ctypes.c_uint8)]


class CecCommand(ctypes.Structure):
    _fields_ = [
        ("initiator", ctypes.c_int),
        ("destination", ctypes.c_int),
        ("ack", ctypes.c_int8),
        ("eom", ctypes.c_int8),
        ("opcode", ctypes.c_int),
        ("parameters", CecDataPacket),
        ("opcode_set", ctypes.c_int8),
        ("transmit_timeout", ctypes.c_int32),
    ]


class CecLogMessage(ctypes.Structure):
    _fields_ = [("message", ctypes.c_char_p), ("level", ctypes.c_int), ("time", ctypes.c_int64)]


class CecKeypress(ctypes.Structure):
    _fields_ = [("keycode", ctypes.c_int), ("duration", ctypes.c_uint)]


class LibCecParameter(ctypes.Structure):
    _fields_ = [("paramType", ctypes.c_int), ("paramData", ctypes.c_void_p)]


class CecLogicalAddresses(ctypes.Structure):
    _fields_ = [("primary", ctypes.c_int), ("addresses", ctypes.c_int * 16)]


class CecDeviceTypeList(ctypes.Structure):
    _fields_ = [("types", ctypes.c_int * 5)]


class LibCecConfiguration(ctypes.Structure):
    _fields_ = [
        ("clientVersion", ctypes.c_uint32),
        ("strDeviceName", ctypes.c_char * 15),
        ("deviceTypes", CecDeviceTypeList),
        ("bAutodetectAddress", ctypes.c_uint8),
        ("iPhysicalAddress", ctypes.c_uint16),
        ("baseDevice", ctypes.c_int),
        ("iHDMIPort", ctypes.c_uint8),
        ("tvVendor", ctypes.c_uint32),
        ("wakeDevices", CecLogicalAddresses),
        ("powerOffDevices", CecLogicalAddresses),
        ("serverVersion", ctypes.c_uint32),
        ("bGetSettingsFromROM", ctypes.c_uint8),
        ("bActivateSource", ctypes.c_uint8),
        ("bPowerOffOnStandby", ctypes.c_uint8),
        ("callbackParam", ctypes.c_void_p),
        ("callbacks", ctypes.c_void_p),
        ("logicalAddresses", CecLogicalAddresses),
        ("iFirmwareVersion", ctypes.c_uint16),
        ("strDeviceLanguage", ctypes.c_char * 3),
        ("iFirmwareBuildDate", ctypes.c_uint32),
        ("bMonitorOnly", ctypes.c_uint8),
        ("cecVersion", ctypes.c_int),
        ("adapterType", ctypes.c_int),
        ("comboKey", ctypes.c_int),
        ("iComboKeyTimeoutMs", ctypes.c_uint32),
        ("iButtonRepeatRateMs", ctypes.c_uint32),
        ("iButtonReleaseDelayMs", ctypes.c_uint32),
        ("iDoubleTapTimeoutMs", ctypes.c_uint32),
        ("bAutoWakeAVR", ctypes.c_uint8),
        ("bAutoPowerOn", ctypes.c_uint8),
        ("bAutonomousMode", ctypes.c_uint8),
        ("iButtonRepeatDelayMs", ctypes.c_uint32),
        ("iDeviceVendorId", ctypes.c_uint32),
    ]


class CecAdapter(ctypes.Structure):
    _fields_ = [("path", ctypes.c_char * 1024), ("comm", ctypes.c_char * 1024)]


class CecAdapterStats(ctypes.Structure):
    _fields_ = [
        ("tx_ack", ctypes.c_uint),
        ("tx_nack", ctypes.c_uint),
        ("tx_error", ctypes.c_uint),
        ("rx_total", ctypes.c_uint),
        ("rx_error", ctypes.c_uint),
    ]


LogCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(CecLogMessage))
KeyPressCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(CecKeypress))
CommandCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(CecCommand))
ConfigurationCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.POINTER(LibCecConfiguration))
AlertCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, LibCecParameter)
MenuStateCallback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
SourceActivatedCallback = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_int, ctypes.c_uint8)
CommandHandlerCallback = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(CecCommand))
RawFrameCallback = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,
    ctypes.POINTER(CecCommand),
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int64,
)

RAW_FRAME_DIRECTION_RX = 0
RAW_FRAME_DIRECTION_TX = 1
RAW_FRAME_STATUS_NAMES = {
    0: "UNKNOWN",
    1: "INCOMING",
    2: "SENT",
    3: "NACK",
    4: "ACK",
    5: "ERROR",
}


class IcecCallbacks(ctypes.Structure):
    _fields_ = [
        ("logMessage", LogCallback),
        ("keyPress", KeyPressCallback),
        ("commandReceived", CommandCallback),
        ("configurationChanged", ConfigurationCallback),
        ("alert", AlertCallback),
        ("menuStateChanged", MenuStateCallback),
        ("sourceActivated", SourceActivatedCallback),
        ("commandHandler", CommandHandlerCallback),
        ("rawFrame", RawFrameCallback),
    ]


@dataclass(frozen=True)
class BackendResult:
    ok: bool
    text: str = ""
    value: Any = None


ROLE_TO_DEVICE_TYPE = {
    "t": 0,
    "r": 1,
    "p": 4,
    "a": 5,
    "x": 0,
}

LOGICAL_ADDRESS_NAMES = {
    0: "TV",
    1: "Recorder 1",
    2: "Recorder 2",
    3: "Tuner 1",
    4: "Playback 1",
    5: "Audio System",
    6: "Tuner 2",
    7: "Tuner 3",
    8: "Playback 2",
    9: "Recorder 3",
    10: "Tuner 4",
    11: "Playback 3",
    12: "Reserved 1",
    13: "Reserved 2",
    14: "Free use",
    15: "Broadcast/Unregistered",
}

CEC_VERSION_NAMES = {
    0: "unknown",
    1: "CEC 1.2",
    2: "CEC 1.2a",
    3: "CEC 1.3",
    4: "CEC 1.3a",
    5: "CEC 1.4",
    6: "CEC 2.0",
}

POWER_STATUS_NAMES = {
    0x00: "on",
    0x01: "standby",
    0x02: "transition standby->on",
    0x03: "transition on->standby",
    0x99: "unknown",
}

ALERT_NAMES = {
    0: "service device",
    1: "connection lost",
    2: "permission error",
    3: "port busy",
    4: "physical address error",
    5: "TV poll failed",
}

OPCODE_NAMES = {
    0x00: "FEATURE_ABORT",
    0x04: "IMAGE_VIEW_ON",
    0x09: "RECORD_ON",
    0x0B: "RECORD_OFF",
    0x0D: "TEXT_VIEW_ON",
    0x32: "SET_MENU_LANGUAGE",
    0x36: "STANDBY",
    0x41: "PLAY",
    0x44: "USER_CONTROL_PRESSED",
    0x45: "USER_CONTROL_RELEASE",
    0x46: "GIVE_OSD_NAME",
    0x47: "SET_OSD_NAME",
    0x64: "SET_OSD_STRING",
    0x70: "SYSTEM_AUDIO_MODE_REQUEST",
    0x71: "GIVE_AUDIO_STATUS",
    0x72: "SET_SYSTEM_AUDIO_MODE",
    0x7A: "REPORT_AUDIO_STATUS",
    0x7D: "GIVE_SYSTEM_AUDIO_MODE_STATUS",
    0x7E: "SYSTEM_AUDIO_MODE_STATUS",
    0x80: "ROUTING_CHANGE",
    0x81: "ROUTING_INFORMATION",
    0x82: "ACTIVE_SOURCE",
    0x83: "GIVE_PHYSICAL_ADDRESS",
    0x84: "REPORT_PHYSICAL_ADDRESS",
    0x85: "REQUEST_ACTIVE_SOURCE",
    0x86: "SET_STREAM_PATH",
    0x87: "DEVICE_VENDOR_ID",
    0x8C: "GIVE_DEVICE_VENDOR_ID",
    0x8D: "MENU_REQUEST",
    0x8E: "MENU_STATUS",
    0x8F: "GIVE_DEVICE_POWER_STATUS",
    0x90: "REPORT_POWER_STATUS",
    0x91: "GET_MENU_LANGUAGE",
    0x9D: "INACTIVE_SOURCE",
    0x9E: "CEC_VERSION",
    0x9F: "GET_CEC_VERSION",
    0xA0: "VENDOR_COMMAND_WITH_ID",
    0xA1: "CLEAR_EXTERNAL_TIMER",
    0xA2: "SET_EXTERNAL_TIMER",
    0xA3: "REPORT_SHORT_AUDIO_DESCRIPTORS",
    0xA4: "REQUEST_SHORT_AUDIO_DESCRIPTORS",
    0xC0: "START_ARC",
    0xC1: "REPORT_ARC_STARTED",
    0xC2: "REPORT_ARC_ENDED",
    0xC3: "REQUEST_ARC_START",
    0xC4: "REQUEST_ARC_END",
    0xC5: "END_ARC",
    0xF8: "CDC",
}


def _decode_c_string(value: bytes | bytearray, encoding: str = "utf-8") -> str:
    return bytes(value).split(b"\0", 1)[0].decode(encoding, errors="replace")


def _format_hex(value: int, width: int = 2) -> str:
    return f"0x{value:0{width}X}"


class LibCecBackend:
    """A thread-safe, direct binding to the libCEC C API."""

    def __init__(self, dll_path: str | os.PathLike[str] | None = None) -> None:
        self.dll_path = Path(dll_path) if dll_path else None
        self._lib: Any = None
        self._connection = ctypes.c_void_p()
        self._callbacks: IcecCallbacks | None = None
        self._callback_refs: list[Any] = []
        self._events: queue.Queue[dict[str, Any]] = queue.Queue()
        self._lock = threading.RLock()
        self._open_port = ""
        self._monitor_only = False
        self._role = "x"
        self._logical_address = 4
        self._physical_address = 0x1000
        self._load_error: str | None = None

    @property
    def is_open(self) -> bool:
        return bool(self._connection.value)

    @property
    def open_port(self) -> str:
        return self._open_port

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _candidate_dlls(self) -> list[Path]:
        candidates: list[Path] = []
        if self.dll_path:
            candidates.append(self.dll_path)
            if self.dll_path.name.lower() != "cec.dll":
                candidates.append(self.dll_path.with_name("cec.dll"))
        for env_name in ("LIBCEC_DLL", "PULSE_EIGHT_CEC_DLL"):
            value = os.environ.get(env_name, "").strip()
            if value:
                candidates.append(Path(value))
        candidates.extend(
            [
                Path(__file__).resolve().with_name("libcec-custom-install") / "bin" / "cec.dll",
                Path(r"C:\Program Files\Pulse-Eight\USB-CEC Adapter\cec.dll"),
                Path(r"C:\Program Files (x86)\Pulse-Eight\USB-CEC Adapter\cec.dll"),
                Path(__file__).resolve().with_name("cec.dll"),
            ]
        )
        unique: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate).lower()
            if key not in seen:
                unique.append(candidate)
                seen.add(key)
        return unique

    def _load(self) -> Any:
        if self._lib is not None:
            return self._lib
        errors: list[str] = []
        for candidate in self._candidate_dlls():
            try:
                if not candidate.is_file():
                    continue
                self._lib = ctypes.WinDLL(str(candidate))
                self.dll_path = candidate
                self._bind_functions()
                self._load_error = None
                return self._lib
            except (OSError, AttributeError) as exc:
                errors.append(f"{candidate}: {exc}")
        self._load_error = "找不到或无法加载 libCEC cec.dll。" + ("\n" + "\n".join(errors) if errors else "")
        raise LibCecError(self._load_error)

    def _bind_functions(self) -> None:
        lib = self._lib

        def bind(name: str, restype: Any, argtypes: list[Any]) -> Any:
            function = getattr(lib, name)
            function.restype = restype
            function.argtypes = argtypes
            return function

        self._f_initialise = bind("libcec_initialise", ctypes.c_void_p, [ctypes.POINTER(LibCecConfiguration)])
        self._f_destroy = bind("libcec_destroy", None, [ctypes.c_void_p])
        self._f_open = bind("libcec_open", ctypes.c_int, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32])
        self._f_close = bind("libcec_close", None, [ctypes.c_void_p])
        self._f_clear_config = bind("libcec_clear_configuration", None, [ctypes.POINTER(LibCecConfiguration)])
        self._f_set_callbacks = bind("libcec_set_callbacks", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(IcecCallbacks), ctypes.c_void_p])
        self._f_disable_callbacks = bind("libcec_disable_callbacks", ctypes.c_int, [ctypes.c_void_p])
        self._f_find_adapters = bind("libcec_find_adapters", ctypes.c_int8, [ctypes.c_void_p, ctypes.POINTER(CecAdapter), ctypes.c_uint8, ctypes.c_char_p])
        self._f_ping = bind("libcec_ping_adapters", ctypes.c_int, [ctypes.c_void_p])
        self._f_bootloader = bind("libcec_start_bootloader", ctypes.c_int, [ctypes.c_void_p])
        self._f_power_on = bind("libcec_power_on_devices", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_standby = bind("libcec_standby_devices", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_active_source = bind("libcec_set_active_source", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_inactive_source = bind("libcec_set_inactive_view", ctypes.c_int, [ctypes.c_void_p])
        self._f_transmit = bind("libcec_transmit", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(CecCommand)])
        self._f_set_la = bind("libcec_set_logical_address", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_set_pa = bind("libcec_set_physical_address", ctypes.c_int, [ctypes.c_void_p, ctypes.c_uint16])
        self._f_set_osd = bind("libcec_set_osd_string", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_char_p])
        self._f_monitor = bind("libcec_switch_monitoring", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_get_version = bind("libcec_get_device_cec_version", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_get_language = bind("libcec_get_device_menu_language", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_char)])
        self._f_get_vendor = bind("libcec_get_device_vendor_id", ctypes.c_uint32, [ctypes.c_void_p, ctypes.c_int])
        self._f_get_pa = bind("libcec_get_device_physical_address", ctypes.c_uint16, [ctypes.c_void_p, ctypes.c_int])
        self._f_get_active_source = bind("libcec_get_active_source", ctypes.c_int, [ctypes.c_void_p])
        self._f_is_active_source = bind("libcec_is_active_source", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_get_power = bind("libcec_get_device_power_status", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_poll = bind("libcec_poll_device", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_get_active_devices = bind("libcec_get_active_devices", CecLogicalAddresses, [ctypes.c_void_p])
        self._f_is_active_device = bind("libcec_is_active_device", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_is_active_type = bind("libcec_is_active_device_type", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_set_hdmi_port = bind("libcec_set_hdmi_port", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint8])
        self._f_volume_up = bind("libcec_volume_up", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_volume_down = bind("libcec_volume_down", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_mute = bind("libcec_mute_audio", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_send_keypress = bind("libcec_send_keypress", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int])
        self._f_send_release = bind("libcec_send_key_release", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int])
        self._f_send_play = bind("libcec_send_play", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int])
        self._f_get_name = bind("libcec_get_device_osd_name", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_char)])
        self._f_set_stream_logical = bind("libcec_set_stream_path_logical", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_set_stream_physical = bind("libcec_set_stream_path_physical", ctypes.c_int, [ctypes.c_void_p, ctypes.c_uint16])
        self._f_get_logical = bind("libcec_get_logical_addresses", CecLogicalAddresses, [ctypes.c_void_p])
        self._f_get_config = bind("libcec_get_current_configuration", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(LibCecConfiguration)])
        self._f_set_config = bind("libcec_set_configuration", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(LibCecConfiguration)])
        self._f_rescan = bind("libcec_rescan_devices", None, [ctypes.c_void_p])
        self._f_lib_info = bind("libcec_get_lib_info", ctypes.c_char_p, [ctypes.c_void_p])
        self._f_stats = bind("libcec_get_stats", ctypes.c_int, [ctypes.c_void_p, ctypes.POINTER(CecAdapterStats)])
        self._f_audio_toggle = bind("libcec_audio_toggle_mute", ctypes.c_uint8, [ctypes.c_void_p])
        self._f_audio_mute = bind("libcec_audio_mute", ctypes.c_uint8, [ctypes.c_void_p])
        self._f_audio_unmute = bind("libcec_audio_unmute", ctypes.c_uint8, [ctypes.c_void_p])
        self._f_audio_status = bind("libcec_audio_get_status", ctypes.c_uint8, [ctypes.c_void_p])
        self._f_system_audio = bind("libcec_system_audio_mode", ctypes.c_int, [ctypes.c_void_p, ctypes.c_int])
        self._f_system_audio_status = bind("libcec_system_audio_mode_get_status", ctypes.c_uint8, [ctypes.c_void_p])
        self._string_functions = {}
        for name in ("libcec_opcode_to_string", "libcec_power_status_to_string", "libcec_cec_version_to_string", "libcec_vendor_id_to_string"):
            try:
                self._string_functions[name] = bind(name, None, [ctypes.c_int, ctypes.POINTER(ctypes.c_char), ctypes.c_size_t])
            except AttributeError:
                pass

    def _new_configuration(
        self,
        *,
        role: str = "x",
        device_types: str | None = None,
        monitor_only: bool = False,
        base_device: int | None = None,
        physical_address: int | None = None,
        hdmi_port: int | None = None,
        device_name: str = "CEC Tester",
        vendor_id: int | None = None,
        activate_source: bool = False,
        auto_wake_avr: bool | None = None,
        autonomous_mode: bool | None = None,
        get_settings_from_rom: bool = False,
    ) -> LibCecConfiguration:
        self._load()
        config = LibCecConfiguration()
        self._f_clear_config(ctypes.byref(config))
        config.clientVersion = CEC_LIB_VERSION_CURRENT
        config.strDeviceName = device_name.encode("ascii", errors="replace")[:14]
        config.bMonitorOnly = 1 if monitor_only else 0
        config.bActivateSource = 1 if activate_source else 0
        config.bPowerOffOnStandby = 0
        config.bAutoWakeAVR = 0 if auto_wake_avr is None else (1 if auto_wake_avr else 0)
        config.bAutoPowerOn = 0
        config.bAutonomousMode = 0 if autonomous_mode is None else (1 if autonomous_mode else 0)
        config.bGetSettingsFromROM = 1 if get_settings_from_rom else 0
        config.iDeviceVendorId = CEC_INVALID_VENDOR_ID if vendor_id is None else int(vendor_id)
        config.deviceTypes.types[:] = [2, 2, 2, 2, 2]
        if not monitor_only:
            selected_roles = device_types if device_types is not None else role
            selected_types: list[int] = []
            for role_char in selected_roles.lower():
                device_type = ROLE_TO_DEVICE_TYPE.get(role_char)
                if device_type is not None and device_type not in selected_types:
                    selected_types.append(device_type)
            if not selected_types:
                selected_types = [ROLE_TO_DEVICE_TYPE.get(role.lower(), 4)]
            for index, device_type in enumerate(selected_types[:5]):
                config.deviceTypes.types[index] = device_type
        if physical_address is None:
            config.bAutodetectAddress = 1
        else:
            config.bAutodetectAddress = 0
            config.iPhysicalAddress = int(physical_address) & 0xFFFF
        if base_device is not None:
            config.baseDevice = int(base_device)
        if hdmi_port is not None:
            config.iHDMIPort = int(hdmi_port) & 0xFF
        config.strDeviceLanguage = b"eng"
        return config

    def _create_callbacks(self) -> None:
        def log_message(_param: Any, message_ptr: Any) -> None:
            try:
                if not message_ptr:
                    return
                message = message_ptr.contents
                self._put_event(
                    "log",
                    text=_decode_c_string(message.message or b""),
                    level=int(message.level),
                    timestamp=int(message.time),
                )
            except Exception as exc:  # callback exceptions must never cross the DLL boundary
                self._put_event("error", text=f"libCEC log callback: {exc}")

        def key_press(_param: Any, key_ptr: Any) -> None:
            if key_ptr:
                key = key_ptr.contents
                self._put_event("key", keycode=int(key.keycode), duration=int(key.duration))

        def command_received(_param: Any, command_ptr: Any) -> None:
            try:
                if not command_ptr:
                    return
                # Keep this notification for the role simulator. It is not
                # used as the capture source because the commandHandler
                # callback below is the native path for commands dispatched to
                # a libCEC-controlled logical address.
                self._put_event("rx_notification", **self._command_to_dict(command_ptr.contents))
            except Exception as exc:
                self._put_event("error", text=f"libCEC command callback: {exc}")

        def configuration_changed(_param: Any, _config_ptr: Any) -> None:
            self._put_event("configuration", text="libCEC configuration changed")

        def alert(_param: Any, alert_code: int, _parameter: LibCecParameter) -> None:
            self._put_event("alert", alert=int(alert_code), text=ALERT_NAMES.get(int(alert_code), f"alert {int(alert_code)}"))

        def menu_state(_param: Any, state: int) -> int:
            self._put_event("menu", state=int(state))
            return 1

        def source_activated(_param: Any, logical_address: int, activated: int) -> None:
            self._put_event("source", logical_address=int(logical_address), activated=bool(activated))

        def command_handler(_param: Any, command_ptr: Any) -> int:
            # Returning 0 retains libCEC's normal device-role behavior.
            return 0

        def raw_frame(_param: Any, command_ptr: Any, direction: int, status: int, timestamp: int) -> None:
            try:
                if not command_ptr:
                    return
                self._put_event(
                    "raw_frame",
                    **self._command_to_dict(command_ptr.contents),
                    direction="RX" if int(direction) == RAW_FRAME_DIRECTION_RX else "TX",
                    raw_status=int(status),
                    raw_status_text=RAW_FRAME_STATUS_NAMES.get(int(status), f"STATUS_{int(status)}"),
                    raw_timestamp=int(timestamp),
                )
            except Exception as exc:
                self._put_event("error", text=f"libCEC raw frame callback: {exc}")

        self._callback_refs = [
            LogCallback(log_message),
            KeyPressCallback(key_press),
            CommandCallback(command_received),
            ConfigurationCallback(configuration_changed),
            AlertCallback(alert),
            MenuStateCallback(menu_state),
            SourceActivatedCallback(source_activated),
            CommandHandlerCallback(command_handler),
            RawFrameCallback(raw_frame),
        ]
        self._callbacks = IcecCallbacks(*self._callback_refs)

    def _put_event(self, kind: str, **payload: Any) -> None:
        payload["kind"] = kind
        payload["time"] = time.time()
        self._events.put(payload)

    def drain_events(self, limit: int = 1000) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for _ in range(max(0, limit)):
            try:
                result.append(self._events.get_nowait())
            except queue.Empty:
                break
        return result

    def open(
        self,
        port: str,
        *,
        role: str = "x",
        device_types: str | None = None,
        monitor_only: bool = False,
        base_device: int | None = None,
        physical_address: int | None = None,
        hdmi_port: int | None = None,
        device_name: str = "CEC Tester",
        vendor_id: int | None = None,
        activate_source: bool = False,
        auto_wake_avr: bool | None = None,
        autonomous_mode: bool | None = None,
        get_settings_from_rom: bool = False,
    ) -> BackendResult:
        with self._lock:
            if self.is_open:
                self.close()
            if not str(port).strip():
                return BackendResult(False, "COM 端口不能为空")
            try:
                config = self._new_configuration(
                    role=role,
                    device_types=device_types,
                    monitor_only=monitor_only,
                    base_device=base_device,
                    physical_address=physical_address,
                    hdmi_port=hdmi_port,
                    device_name=device_name,
                    vendor_id=vendor_id,
                    activate_source=activate_source,
                    auto_wake_avr=auto_wake_avr,
                    autonomous_mode=autonomous_mode,
                    get_settings_from_rom=get_settings_from_rom,
                )
                self._create_callbacks()
                connection = self._f_initialise(ctypes.byref(config))
                if not connection:
                    return BackendResult(False, "libcec_initialise 返回空连接")
                self._connection = ctypes.c_void_p(connection)
                self._f_set_callbacks(self._connection, ctypes.byref(self._callbacks), None)
                result = int(self._f_open(self._connection, str(port).encode("ascii", errors="replace"), 10000))
                if not result:
                    self._f_disable_callbacks(self._connection)
                    self._f_destroy(self._connection)
                    self._connection = ctypes.c_void_p()
                    return BackendResult(False, f"libcec_open 失败：{port}")
                self._open_port = str(port)
                self._monitor_only = bool(monitor_only)
                self._role = role.lower()
                if physical_address is not None:
                    self._physical_address = int(physical_address) & 0xFFFF
                self._put_event("state", text=f"libCEC 已打开 {port}", connected=True)
                return BackendResult(True, f"libCEC 已打开 {port}")
            except (OSError, ctypes.ArgumentError, LibCecError) as exc:
                self._connection = ctypes.c_void_p()
                return BackendResult(False, str(exc))

    def close(self) -> None:
        with self._lock:
            if not self.is_open:
                return
            connection = self._connection
            self._connection = ctypes.c_void_p()
            try:
                self._f_disable_callbacks(connection)
            except Exception:
                pass
            try:
                self._f_close(connection)
            finally:
                try:
                    self._f_destroy(connection)
                except Exception:
                    pass
            self._open_port = ""
            self._put_event("state", text="libCEC 已关闭", connected=False)

    def _require_open(self) -> ctypes.c_void_p:
        if not self.is_open:
            raise LibCecError("libCEC 尚未连接")
        return self._connection

    @staticmethod
    def frame_to_command(frame: Iterable[int], wait_for_ack: bool = True) -> CecCommand:
        values = [int(value) & 0xFF for value in frame]
        if len(values) < 2:
            raise ValueError("CEC 报文至少需要 header 和 opcode")
        if len(values) > CEC_MAX_FRAME_SIZE:
            raise ValueError("CEC 单帧最多 16 个字节")
        command = CecCommand()
        command.initiator = (values[0] >> 4) & 0x0F
        command.destination = values[0] & 0x0F
        command.ack = 0
        command.eom = 1
        command.opcode = values[1]
        command.opcode_set = 1
        command.transmit_timeout = 1000 if wait_for_ack else 0
        command.parameters.size = len(values) - 2
        for index, value in enumerate(values[2:]):
            command.parameters.data[index] = value
        return command

    @staticmethod
    def command_to_frame(command: CecCommand) -> list[int]:
        frame = [((int(command.initiator) & 0x0F) << 4) | (int(command.destination) & 0x0F)]
        if int(command.opcode_set):
            frame.append(int(command.opcode) & 0xFF)
        size = min(int(command.parameters.size), CEC_MAX_DATA_PACKET_SIZE)
        frame.extend(int(command.parameters.data[index]) & 0xFF for index in range(size))
        return frame

    def _command_to_dict(self, command: CecCommand) -> dict[str, Any]:
        frame = self.command_to_frame(command)
        opcode = frame[1] if len(frame) > 1 else None
        return {
            "frame": frame,
            "source": int(command.initiator),
            "destination": int(command.destination),
            "opcode": opcode,
            "opcode_set": bool(command.opcode_set),
            "ack": int(command.ack),
            "eom": int(command.eom),
            "opcode_text": "POLL" if opcode is None else f"0x{opcode:02X} {OPCODE_NAMES.get(opcode, 'UNKNOWN')}",
        }

    def transmit(self, frame: Iterable[int], wait_for_ack: bool = True) -> BackendResult:
        with self._lock:
            try:
                connection = self._require_open()
                values = [int(value) & 0xFF for value in frame]
                command = self.frame_to_command(values, wait_for_ack=wait_for_ack)
                result = int(self._f_transmit(connection, ctypes.byref(command)))
                text = f"TX {' : '.join(f'{value:02X}' for value in values)} -> {'ACK' if result else 'NACK/ERROR'}"
                return BackendResult(bool(result), text, result)
            except (ValueError, LibCecError, OSError) as exc:
                return BackendResult(False, str(exc))

    def set_logical_address(self, address: int) -> BackendResult:
        with self._lock:
            try:
                result = int(self._f_set_la(self._require_open(), int(address) & 0x0F))
                if result:
                    self._logical_address = int(address) & 0x0F
                return BackendResult(bool(result), f"逻辑地址设置为 {int(address) & 0x0F:X}")
            except (LibCecError, OSError) as exc:
                return BackendResult(False, str(exc))

    def set_physical_address(self, address: int) -> BackendResult:
        with self._lock:
            try:
                result = int(self._f_set_pa(self._require_open(), int(address) & 0xFFFF))
                if result:
                    self._physical_address = int(address) & 0xFFFF
                return BackendResult(bool(result), f"物理地址设置为 {int(address) & 0xFFFF:04X}")
            except (LibCecError, OSError) as exc:
                return BackendResult(False, str(exc))

    def switch_monitoring(self, enabled: bool) -> BackendResult:
        with self._lock:
            try:
                result = int(self._f_monitor(self._require_open(), 1 if enabled else 0))
                state = "开启" if enabled else "关闭"
                suffix = "成功" if result else "失败"
                return BackendResult(bool(result), f"总线监视 {state}{suffix}", result)
            except (LibCecError, OSError) as exc:
                return BackendResult(False, str(exc))

    def stats(self) -> dict[str, int]:
        with self._lock:
            stats = CecAdapterStats()
            if not self.is_open or not int(self._f_stats(self._connection, ctypes.byref(stats))):
                return {}
            return {name: int(getattr(stats, name)) for name, _ in CecAdapterStats._fields_}

    def list_adapters(self, device_path: str = "") -> list[dict[str, str]]:
        """Enumerate adapters through libcec_find_adapters, not cec-client."""
        with self._lock:
            temporary = False
            connection = self._connection
            try:
                if not self.is_open:
                    config = self._new_configuration(monitor_only=True)
                    connection = ctypes.c_void_p(self._f_initialise(ctypes.byref(config)))
                    if not connection:
                        return []
                    temporary = True
                adapters = (CecAdapter * 16)()
                count = int(self._f_find_adapters(connection, adapters, 16, device_path.encode("utf-8") if device_path else None))
                result: list[dict[str, str]] = []
                for index in range(max(0, count)):
                    result.append({"path": _decode_c_string(adapters[index].path), "comm": _decode_c_string(adapters[index].comm)})
                return result
            finally:
                if temporary and connection:
                    self._f_destroy(connection)

    def lib_info(self) -> str:
        with self._lock:
            temporary = False
            connection = self._connection
            try:
                if not self.is_open:
                    config = self._new_configuration(monitor_only=True)
                    connection = ctypes.c_void_p(self._f_initialise(ctypes.byref(config)))
                    if not connection:
                        return "无法初始化 libCEC"
                    temporary = True
                return _decode_c_string(self._f_lib_info(connection) or b"")
            finally:
                if temporary and connection:
                    self._f_destroy(connection)

    def start_bootloader(self) -> BackendResult:
        with self._lock:
            try:
                result = int(self._f_bootloader(self._require_open()))
                return BackendResult(bool(result), "已请求适配器进入 Bootloader" if result else "进入 Bootloader 失败")
            except (LibCecError, OSError) as exc:
                return BackendResult(False, str(exc))

    def reconnect(self) -> BackendResult:
        with self._lock:
            if not self._open_port:
                return BackendResult(False, "没有可重连的端口")
            port = self._open_port
            role = self._role
            monitor = self._monitor_only
            logical_address = self._logical_address
            physical_address = self._physical_address
            self.close()
            result = self.open(port, role=role, monitor_only=monitor, physical_address=physical_address)
            if result.ok:
                address_result = self.set_logical_address(logical_address)
                if not address_result.ok:
                    return address_result
            return result

    def device_info(self, logical_address: int, *, probe: bool = True) -> dict[str, Any]:
        with self._lock:
            connection = self._require_open()
            address = int(logical_address) & 0x0F
            name = ctypes.create_string_buffer(14)
            language = ctypes.create_string_buffer(4)
            self._f_get_name(connection, address, name)
            self._f_get_language(connection, address, language)
            version = int(self._f_get_version(connection, address))
            vendor = int(self._f_get_vendor(connection, address))
            physical = int(self._f_get_pa(connection, address))
            power = int(self._f_get_power(connection, address))
            present = True
            if probe:
                present = bool(int(self._f_poll(connection, address)))
            return {
                "la": f"{address:X}",
                "type": LOGICAL_ADDRESS_NAMES.get(address, "Unknown"),
                "address": "FFFF" if physical == CEC_INVALID_PHYSICAL_ADDRESS else f"{physical:04X}",
                "active": "yes" if int(self._f_is_active_source(connection, address)) else "no",
                "vendor": "Unknown" if vendor in (0, CEC_INVALID_VENDOR_ID) else f"{vendor:06X}",
                "osd": _decode_c_string(name),
                "version": CEC_VERSION_NAMES.get(version, f"0x{version:02X}"),
                "power": POWER_STATUS_NAMES.get(power, f"0x{power:02X}"),
                "language": _decode_c_string(language),
                "present": present,
            }

    def scan(self, settle_seconds: float = 0.15) -> list[dict[str, Any]]:
        with self._lock:
            connection = self._require_open()
            self._f_rescan(connection)
            devices: list[dict[str, Any]] = []
            for address in range(15):
                try:
                    present = bool(int(self._f_poll(connection, address)))
                except OSError:
                    present = False
                if present:
                    time.sleep(max(0.0, settle_seconds))
                    try:
                        info = self.device_info(address, probe=False)
                        info["present"] = True
                        devices.append(info)
                    except (LibCecError, OSError):
                        pass
            self._put_event("scan", devices=devices)
            return devices

    def execute_command(self, command_line: str) -> BackendResult:
        """Execute the GUI command syntax by dispatching directly to libCEC C APIs."""
        parts = command_line.strip().split()
        if not parts:
            return BackendResult(False, "空命令")
        command = parts[0].lower()
        args = parts[1:]
        try:
            if command in {"q", "quit"}:
                self.close()
                return BackendResult(True, "会话已关闭")
            if command in {"tx", "txn"}:
                if not args:
                    return BackendResult(False, f"用法：{command} <frame>")
                frame = [int(value, 16) for value in " ".join(args).replace(":", " ").split()]
                return self.transmit(frame, wait_for_ack=command == "tx")
            if command == "la":
                return self.set_logical_address(int(args[0], 16))
            if command == "pa":
                return self.set_physical_address(int(args[0], 16))
            if command == "mon":
                return self.switch_monitoring(bool(int(args[0])))
            if command == "p":
                port = int(args[0], 0)
                result = int(self._f_set_hdmi_port(self._require_open(), 0, port))
                return BackendResult(bool(result), f"HDMI port 设置为 {port}")
            if command == "on":
                target = int(args[0], 16) if args else 0
                result = int(self._f_power_on(self._require_open(), target))
                return BackendResult(bool(result), f"Power On {target:X}")
            if command in {"standby", "stby"}:
                target = int(args[0], 16) if args else CECDEVICE_BROADCAST
                result = int(self._f_standby(self._require_open(), target))
                return BackendResult(bool(result), f"Standby {target:X}")
            if command == "as":
                result = int(self._f_active_source(self._require_open(), ROLE_TO_DEVICE_TYPE.get(self._role, 4)))
                return BackendResult(bool(result), "已设为 Active Source")
            if command == "is":
                result = int(self._f_inactive_source(self._require_open()))
                return BackendResult(bool(result), "已设为 Inactive Source")
            if command in {"ea", "da"}:
                result = int(self._f_system_audio(self._require_open(), 1 if command == "ea" else 0))
                return BackendResult(bool(result), f"System Audio Mode {'开启' if command == 'ea' else '关闭'}")
            if command == "gas":
                return BackendResult(True, f"Audio status: 0x{int(self._f_audio_status(self._require_open())):02X}")
            if command == "gsam":
                return BackendResult(True, f"System Audio Mode: {int(self._f_system_audio_status(self._require_open()))}")
            if command == "osd":
                target = int(args[0], 16)
                text = command_line.split(None, 2)[2]
                result = int(self._f_set_osd(self._require_open(), target, 0x40, text.encode("utf-8")))
                return BackendResult(bool(result), f"OSD -> {target:X}: {text}")
            if command in {"ver", "ven", "lang", "pow", "name"}:
                target = int(args[0], 16)
                info = self.device_info(target)
                key = {"ver": "version", "ven": "vendor", "lang": "language", "pow": "power", "name": "osd"}[command]
                return BackendResult(True, f"{target:X} {key}: {info[key]}", info)
            if command == "poll":
                target = int(args[0], 16)
                result = int(self._f_poll(self._require_open(), target))
                return BackendResult(bool(result), f"Poll {target:X}: {'ACK' if result else 'NACK'}")
            if command in {"lad", "scan"}:
                devices = self.scan()
                return BackendResult(True, f"扫描到 {len(devices)} 个设备", devices)
            if command == "ad":
                target = int(args[0], 16)
                return BackendResult(True, f"Active Source: {bool(self._f_is_active_source(self._require_open(), target))}")
            if command == "at":
                if not args:
                    return BackendResult(False, "用法：at <type>")
                type_text = args[0].lower()
                type_value = ROLE_TO_DEVICE_TYPE[type_text] if type_text in ROLE_TO_DEVICE_TYPE else int(type_text, 0)
                return BackendResult(True, f"Active device type: {bool(self._f_is_active_type(self._require_open(), type_value))}")
            if command == "sp":
                result = int(self._f_set_stream_physical(self._require_open(), int(args[0], 16)))
                return BackendResult(bool(result), f"Stream Path -> {int(args[0], 16):04X}")
            if command == "spl":
                result = int(self._f_set_stream_logical(self._require_open(), int(args[0], 16)))
                return BackendResult(bool(result), f"Stream Path -> LA {int(args[0], 16):X}")
            if command == "volup":
                result = int(self._f_volume_up(self._require_open(), 1))
                return BackendResult(bool(result), "音量增加")
            if command == "voldown":
                result = int(self._f_volume_down(self._require_open(), 1))
                return BackendResult(bool(result), "音量降低")
            if command == "mute":
                result = int(self._f_audio_toggle(self._require_open()))
                return BackendResult(bool(result), "切换静音")
            if command == "play":
                target = int(args[0], 16)
                mode = int(args[1], 0) if len(args) > 1 else 0x24
                result = int(self._f_send_play(self._require_open(), target, mode))
                return BackendResult(bool(result), f"Play -> {target:X}")
            if command == "self":
                addresses = self._f_get_logical(self._require_open())
                return BackendResult(True, "当前逻辑地址：" + ", ".join(f"{i:X}" for i in range(16) if addresses.addresses[i]))
            if command == "ping":
                result = int(self._f_ping(self._require_open()))
                return BackendResult(bool(result), "适配器 Ping 成功" if result else "适配器 Ping 失败")
            if command == "r":
                return self.reconnect()
            if command == "bl":
                return self.start_bootloader()
            if command == "am":
                config = LibCecConfiguration()
                self._f_get_config(self._require_open(), ctypes.byref(config))
                if args:
                    config.bAutonomousMode = 1 if bool(int(args[0])) else 0
                    result = int(self._f_set_config(self._require_open(), ctypes.byref(config)))
                    return BackendResult(bool(result), f"Autonomous Mode = {config.bAutonomousMode}")
                return BackendResult(True, f"Autonomous Mode = {config.bAutonomousMode}")
            if command == "log":
                return BackendResult(True, "日志级别由 libCEC 回调提供，当前 GUI 接收全部原生日志")
            if command in {"h", "help"}:
                return BackendResult(True, self.help_text())
            return BackendResult(False, f"未实现或不安全的命令：{command}")
        except (ValueError, IndexError, TypeError, LibCecError, OSError) as exc:
            return BackendResult(False, f"命令失败：{exc}")

    @staticmethod
    def help_text() -> str:
        return (
            "libCEC API commands: tx/txn, la, pa, p, mon, on, standby, as, is, "
            "ea, da, gas, gsam, osd, ver, ven, lang, pow, name, poll, lad, scan, "
            "ad, at, sp, spl, volup, voldown, mute, play, self, ping, am, r, bl, q"
        )
