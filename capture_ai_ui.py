"""Capture snapshot and operation-notes dialog for AI analysis ZIP exports."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from capture_ai_export import OPERATION_FIELDS, CEC_PROBLEM_PRESETS, CEC_PROBLEM_BY_ID, apply_problem_preset
from runtime_paths import application_directory


class CaptureAIExportMixin:
    def _ai_init(self, preferences: dict) -> None:
        saved = preferences.get("ai_export", {})
        self._ai_operations = {key: str(saved.get(key, "")) for key, _ in OPERATION_FIELDS} if isinstance(saved, dict) else {}
        preset_id = saved.get("problem_preset", "CUSTOM") if isinstance(saved, dict) else "CUSTOM"
        self._ai_operations["problem_preset"] = preset_id if isinstance(preset_id, str) and preset_id in CEC_PROBLEM_BY_ID else "CUSTOM"
        self._ai_window = None
        self._ai_busy = False
        self._ai_note_widgets = {}

    def _ai_show_export(self) -> None:
        if self._ai_window is not None and self._ai_window.winfo_exists():
            self._ai_window.deiconify()
            self._ai_window.lift()
            return
        window = self._ai_window = tk.Toplevel(self)
        window.title("导出 AI 分析包")
        window.transient(self)
        window.geometry("820x740")
        window.minsize(660, 630)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)
        ttk.Label(window, text="将完整报文、连接配置、版本、操作说明和分析提示词导出为 ZIP。\n"
                  "以点击“导出 ZIP”时当前查看的会话为准；覆盖全部历史，不受筛选或 Polling 显示影响。",
                  wraplength=770, padding=10).grid(row=0, column=0, sticky="ew")
        presets = ttk.Frame(window, padding=(10, 0, 10, 4))
        presets.grid(row=1, column=0, sticky="ew")
        presets.columnconfigure(1, weight=1)
        ttk.Label(presets, text="常见 CEC 问题").grid(row=0, column=0, padx=(0, 8))
        self._ai_problem_ids = ["CUSTOM", *(item["id"] for item in CEC_PROBLEM_PRESETS)]
        self._ai_problem_combo = ttk.Combobox(presets, state="readonly", height=12,
            values=["自定义 / 手动填写", *(item["label"] for item in CEC_PROBLEM_PRESETS)])
        self._ai_problem_combo.grid(row=0, column=1, sticky="ew")
        self._ai_problem_combo.current(self._ai_problem_ids.index(self._ai_operations["problem_preset"]))
        self._ai_problem_combo.bind("<<ComboboxSelected>>", self._ai_problem_changed)
        form = ttk.Frame(window, padding=(10, 0))
        form.grid(row=2, column=0, sticky="nsew")
        form.columnconfigure(0, weight=1)
        self._ai_note_widgets = {}
        for index, (key, label) in enumerate(OPERATION_FIELDS):
            ttk.Label(form, text=label).grid(row=index * 2, column=0, sticky="w", pady=(5, 1))
            text = tk.Text(form, height=1 if key == "title" else 3, wrap="word", undo=True, font=("Microsoft YaHei UI", 9))
            text.grid(row=index * 2 + 1, column=0, sticky="nsew")
            text.insert("1.0", self._ai_operations.get(key, ""))
            self._ai_note_widgets[key] = text
            if key != "title":
                form.rowconfigure(index * 2 + 1, weight=1)
        footer = ttk.Frame(window, padding=10)
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self._ai_progress = tk.StringVar(value="选择问题可填入分析重点，保留手动内容；导出时保存填写内容。")
        ttk.Label(footer, textvariable=self._ai_progress, wraplength=590).grid(row=0, column=0, sticky="w")
        self._ai_submit_button = ttk.Button(footer, text="导出 ZIP…", command=self._ai_choose_export,
                                            state="disabled" if self._ai_busy else "normal")
        self._ai_submit_button.grid(row=0, column=1, padx=(6, 0))
        ttk.Button(footer, text="关闭", command=window.withdraw).grid(row=0, column=2, padx=(6, 0))
        window.protocol("WM_DELETE_WINDOW", window.withdraw)

    def _ai_problem_changed(self, _event=None) -> None:
        index = self._ai_problem_combo.current()
        if index < 0:
            return
        preset_id = self._ai_problem_ids[index]
        operations = {key: widget.get("1.0", "end-1c") for key, widget in self._ai_note_widgets.items()}
        result = apply_problem_preset(operations, preset_id)
        for key, widget in self._ai_note_widgets.items():
            value = result.get(key, "")
            if value != operations[key]:
                widget.edit_separator()
                widget.delete("1.0", "end")
                widget.insert("1.0", value)
                widget.edit_separator()
        self._ai_operations = result
        if preset_id == "CUSTOM":
            self._ai_progress.set("已切换为手动填写，当前内容保留。")
        else:
            self._ai_progress.set("已填入分析方向并保留手动内容；问题名称/预期行为仅在空白或保持预设时替换。")

    def _ai_choose_export(self) -> None:
        if self._ai_busy:
            return
        if not self.capture_records:
            messagebox.showwarning("导出 AI 分析包", "当前会话没有可导出的报文。", parent=self._ai_window)
            return
        operations = {key: widget.get("1.0", "end-1c").strip() for key, widget in self._ai_note_widgets.items()}
        operations["problem_preset"] = self._ai_problem_ids[self._ai_problem_combo.current()]
        path = filedialog.asksaveasfilename(parent=self._ai_window, title="导出 AI 分析包",
                                          defaultextension=".zip", initialfile=datetime.now().strftime("cec-ai-%Y%m%d-%H%M%S.zip"),
                                          filetypes=[("ZIP 分析包", "*.zip")])
        if not path:
            return
        try:
            self._ai_start_export(Path(path), operations)
        except ValueError as exc:
            messagebox.showwarning("导出 AI 分析包", str(exc), parent=self._ai_window)
            return
        self._ai_operations = operations
        self._save_settings()

    def _ai_snapshot(self) -> tuple[list[dict], dict]:
        # Enrichment returns new dictionaries and new tag lists on the Tk thread.
        # Subsequent incoming messages and bookmark edits cannot change this snapshot.
        records = self._files_extract_records("ALL")
        if not records:
            raise ValueError("当前会话没有可导出的报文。")
        stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        offline = self._files_mode == "OFFLINE"
        connected = self.backend.is_open
        variables = {
            "port": "port_var", "device_types": "types_var", "hdmi_port": "hdmi_port_var",
            "base_device": "base_var", "device_name": "osd_name_var", "vendor_id_hex": "vendor_id_var",
            "monitor_only": "monitor_only_var", "auto_wake_avr": "autowake_var",
            "autonomous_mode": "automode_var", "get_settings_from_rom": "read_rom_var",
        }
        role_variables = {
            "role": "sim_role_var", "source_la_hex": "sim_source_var", "physical_address_hex": "sim_physical_var",
            "osd_name": "sim_osd_var", "vendor_id_hex": "sim_vendor_var", "cec_version_hex": "sim_version_var",
            "power": "sim_power_var", "language": "sim_language_var", "volume": "sim_volume_var",
            "mute": "sim_mute_var", "system_audio_mode": "sim_audio_mode_var", "auto_reply": "sim_auto_reply_var",
        }
        native = self.backend.cached_configuration
        connection = {
            "snapshot_at": stamp,
            "relation_to_capture": "OFFLINE_EXPORTER_ONLY" if offline else "LIVE_EXPORT_TIME_SNAPSHOT",
            "scope_note": "此处为导出时的本机配置，不代表离线文件原始采集环境。" if offline else
                          "此处为导出时快照；配置可能在采集过程中发生变化，未记录完整状态历史。",
            "connected": connected, "open_port": self.backend.open_port if connected else None,
            "profile": self.connection_profile if connected else None,
            "connection_open_arguments": deepcopy(self.connection_kwargs) if connected else None,
            "monitor_only_active": self.backend.monitor_only if connected else None,
            "native_configuration_last_reported": native,
            "native_configuration_note": "原生 configurationChanged 回调缓存；可能早于当前状态，导出不调用设备查询。",
            "configured_connection": {key: getattr(self, attr).get() for key, attr in variables.items()},
            "role_simulation": {
                "active": bool(self.simulation_active and connected), "enable_on_connect": self.sim_connect_var.get(),
                "configured": {key: getattr(self, attr).get() for key, attr in role_variables.items()},
                "custom_response_rules": self.sim_rule_text.get("1.0", "end-1c"),
            },
            "address_occupancy": {
                "active_mask": self.backend.occupancy_mask,
                "active_addresses": [f"{la:X}" for la in range(15) if self.backend.occupancy_mask & (1 << la)],
                "configured_addresses": [f"{la:X}" for la, var in enumerate(self.occupancy_la_vars) if var.get()],
                "enable_on_connect": self.occupancy_connect_var.get(),
            },
            "topology_cached": deepcopy(getattr(self, "topology_devices", [])),
            "topology_note": "已有拓扑缓存，未重新扫描；为空不代表总线没有其他设备。",
        }
        loaded_path = self.backend.loaded_dll_path
        context = {
            "snapshot_at": stamp, "connection": connection,
            "capture": {
                "capture_session_id": self.capture_session_id, "view_mode": self._files_mode,
                "record_source": "offline_import" if offline else "native_libcec_rawFrame",
                "offline_source_path": str(self._files_path) if offline and self._files_path else None,
                "pending_native_events_at_snapshot": self.backend.pending_event_count if not offline else None,
                "gui_error_count_at_export": self.gui_error_count,
                "complete_bus_capture_verified": False,
                "scope_note": "当前查看会话全部已处理记录；不受显示窗口、筛选、Polling 显示或列布局限制。",
                "timestamp_note": "原生单调毫秒回调时间，不是物理边沿；离线来源可能没有原生时间戳。",
            },
            "version_context": {
                "application_directory": str(application_directory()), "frozen": bool(getattr(sys, "frozen", False)),
                "executable": sys.executable,
                "dll_loaded_path": str(loaded_path) if loaded_path else None,
                "dll_requested_path": self.cec_path_var.get().strip().strip('"'),
                "oui_path": str(self.oui_lookup.path) if self.oui_lookup.path else None,
            },
        }
        return records, context

    def _ai_start_export(self, path: Path, operations: dict) -> None:
        if self._ai_busy:
            raise ValueError("AI 分析包正在导出，请等待完成。")
        if self._files_mode == "OFFLINE" and self._files_path and path.resolve() == self._files_path.resolve():
            raise ValueError("请选择新的导出文件名，原始离线日志保持只读。")
        records, context = self._ai_snapshot()
        self._ai_busy = True
        self._ai_export_button.configure(state="disabled")
        if self._ai_window is not None and self._ai_window.winfo_exists():
            self._ai_submit_button.configure(state="disabled")
            self._ai_progress.set(f"正在后台打包 {len(records)} 条报文…")
        message = f"正在后台导出 AI 分析包（{len(records)} 条）…"
        self._files_progress_var.set(message)
        self.status_var.set(message)
        self.log_writer.ai_package(path, records, context, dict(operations),
            lambda error: self.output_queue.put(("ai_package_exported", (str(path), len(records), error))))

    def _ai_handle_output(self, kind, value) -> bool:
        if kind != "ai_package_exported":
            return False
        path, count, error = value
        self._ai_busy = False
        self._ai_export_button.configure(state="normal")
        message = f"AI 分析包导出失败：{error}" if error else f"AI 分析包已导出：{count} 条 → {path}"
        self._files_progress_var.set(message)
        self.status_var.set(message)
        if self._ai_window is not None and self._ai_window.winfo_exists():
            self._ai_submit_button.configure(state="normal")
            self._ai_progress.set(message)
        if error:
            messagebox.showerror("AI 分析包导出失败", str(error), parent=self)
        return True
