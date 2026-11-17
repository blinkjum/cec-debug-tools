"""Length-check navigation, isolated offline views and batch export controls."""
from __future__ import annotations

import bisect
from collections import Counter
import json
from pathlib import Path
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from uuid import NAMESPACE_URL, uuid4, uuid5

from cec_capture_tools import (CHECK_LABELS, EXPORT_FIELDS, check_frame_length,
                               export_text, finite_number, parse_frame, read_capture_file)
from cec_workbench import AnnotationLibrary


class CaptureFilesMixin:
    def _files_init(self) -> None:
        workbench = self.settings.get("workbench", {})
        prefs = workbench.get("capture_files", {}) if isinstance(workbench, dict) else {}
        prefs = prefs if isinstance(prefs, dict) else {}
        self._files_mode = "LIVE"
        self._files_path = None
        self._files_errors = []
        self._files_counts = Counter()
        self._files_live = None
        self._files_load_busy = False
        self._files_load_job = None
        self._files_pending_load = None
        self._files_errors_only = tk.BooleanVar(value=False)
        self._files_source_var = tk.StringVar(value="实时会话；离线打开不会修改原文件")
        self._files_summary_var = tk.StringVar(value="长度检查：0 条")
        self._files_progress_var = tk.StringVar(value="Ctrl / Shift 可多选；匹配结果覆盖全部记录，不限于表格显示的 2000 行。")
        self._files_scope_var = tk.StringVar(value=prefs.get("scope", "MATCHED"))
        self._files_format_var = tk.StringVar(value=prefs.get("format", "CSV"))
        default_fields = {"seq", "time", "timestamp_ms", "source", "destination", "frame", "opcode_text",
                          "params", "status", "length_status", "length_message", "note", "tags"}
        chosen = prefs.get("fields", list(default_fields))
        chosen = chosen if isinstance(chosen, list) else list(default_fields)
        self._files_field_vars = {key: tk.BooleanVar(value=key in chosen) for key, _ in EXPORT_FIELDS}
        self._files_notes_path_var = tk.StringVar(value=f"自动保存：{self.wb_annotations_path}")

    def _files_preferences(self) -> dict:
        return {"scope": self._files_scope_var.get(), "format": self._files_format_var.get(),
                "fields": [key for key, var in self._files_field_vars.items() if var.get()]}

    def _files_create(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew")
        self._files_open_button = ttk.Button(top, text="打开离线日志…", command=self._files_choose_open)
        self._files_open_button.pack(side="left")
        ttk.Button(top, text="返回实时抓包", command=self._files_show_live).pack(side="left", padx=4)
        ttk.Label(top, textvariable=self._files_source_var, foreground="#1F3B53").pack(side="left", padx=7)
        checks = ttk.Frame(parent)
        checks.grid(row=1, column=0, sticky="ew", pady=5)
        ttk.Checkbutton(checks, text="仅非法数据块", variable=self._files_errors_only,
                        command=self._wb_base_changed).pack(side="left")
        ttk.Button(checks, text="上一异常", command=lambda: self._files_navigate_error(-1)).pack(side="left", padx=4)
        ttk.Button(checks, text="下一异常", command=lambda: self._files_navigate_error(1)).pack(side="left")
        ttk.Label(checks, textvariable=self._files_summary_var).pack(side="left", padx=10)
        export = ttk.Frame(parent)
        export.grid(row=2, column=0, sticky="ew")
        ttk.Label(export, text="提取范围").pack(side="left")
        self._wb_combo(export, raw_variable=self._files_scope_var,
                       options=[("SELECTED", "选中行"), ("MATCHED", "匹配结果（全部历史）"), ("ALL", "当前会话全部")],
                       width=34, state="readonly").pack(side="left", padx=4)
        ttk.Label(export, text="输出").pack(side="left", padx=(8, 0))
        self._wb_combo(export, raw_variable=self._files_format_var,
                       options=[("CSV", "自定义字段"), ("TSV", "自定义字段"), ("JSON", "自定义字段"),
                                ("RAW", "仅完整报文"), ("OPCODE", "仅 Opcode 名称")],
                       width=23, state="readonly").pack(side="left", padx=4)
        ttk.Button(export, text="提取导出…", command=self._files_choose_export).pack(side="left", padx=4)
        ttk.Button(export, text="复制提取结果", command=self._files_copy_extract).pack(side="left")
        fields = ttk.LabelFrame(parent, text="自定义字段（CSV / TSV / JSON）", padding=(4, 2))
        fields.grid(row=3, column=0, sticky="ew", pady=5)
        for index, (key, label) in enumerate(EXPORT_FIELDS):
            fields.columnconfigure(index % 6, weight=1)
            ttk.Checkbutton(fields, text=label, variable=self._files_field_vars[key]).grid(
                row=index // 6, column=index % 6, sticky="w")
        ttk.Label(parent, textvariable=self._files_progress_var, foreground="#555555", wraplength=1140).grid(row=4, column=0, sticky="w")

    def _files_record_added(self, record: dict, state: dict | None = None) -> None:
        check = record.get("length_check") or check_frame_length(str(record.get("frame", "")))
        record["length_check"] = check
        record["length_status"] = check["status"]
        record["length_message"] = check["message"]
        errors = state["errors"] if state else self._files_errors
        counts = state["counts"] if state else self._files_counts
        counts[check["status"]] += 1
        if check["status"] == "ERROR":
            errors.append(int(record["seq"]))

    def _files_update_summary(self) -> None:
        counts = self._files_counts
        self._files_summary_var.set(f"已查 {sum(counts.values())} 条 / 非法 {counts['ERROR']} / 未覆盖 {counts['NOT_COVERED']}")

    def _files_navigate_error(self, direction: int) -> None:
        errors = [seq for seq in self._files_errors if self._wb_matches_record(self._wb_records[seq])]
        if not errors:
            self._files_progress_var.set("当前查找范围内没有非法数据块")
            return
        selected = self._capture_selected_sequence()
        index = (bisect.bisect_right(errors, selected) if direction > 0 else bisect.bisect_left(errors, selected) - 1) if selected is not None else (0 if direction > 0 else -1)
        seq = errors[index % len(errors)]
        self._wb_jump(seq)
        self._files_progress_var.set(f"非法数据块 #{seq}：{self._wb_records[seq]['length_message']}")

    def _files_snapshot_context(self) -> dict:
        return dict(records=self.capture_records, map=self._wb_records, session=self.capture_session_id,
                    annotations=self.wb_annotations, annotations_path=self.wb_annotations_path,
                    notes_error=self._wb_notes_error, marks=self.capture_interval_marks,
                    errors=self._files_errors, counts=self._files_counts,
                    selected=self._capture_selected_sequence(),
                    selected_many=[int(self.capture_tree.item(item, 'values')[0]) for item in self.capture_tree.selection()],
                    paused=self.capture_paused_var.get(), follow_suspended=self._capture_follow_suspended)

    def _files_install_context(self, state: dict) -> None:
        self.capture_records = state["records"]
        self._wb_records = state["map"]
        self.capture_session_id = state["session"]
        self.wb_annotations = state["annotations"]
        self.wb_annotations_path = state["annotations_path"]
        self._wb_notes_error = state.get("notes_error", "")
        self.capture_interval_marks = state["marks"]
        self._files_errors, self._files_counts = state["errors"], state["counts"]
        self.capture_context_item = self.capture_context_column = None
        self._wb_search_seq = None
        self._capture_follow_suspended = state.get("follow_suspended", False)
        self.capture_paused_var.set(state.get("paused", False))
        self._wb_rebuild_matches()
        self._refresh_capture_tree(force=True)
        self._refresh_capture_interval_display()
        self._wb_refresh_notes()
        self._files_notes_path_var.set(f"自动保存：{self.wb_annotations_path}")
        if state.get("selected") in self._wb_records and self._capture_record_matches_filter(self._wb_records[state['selected']]):
            self._wb_jump(state["selected"])
        wanted = set(state.get("selected_many", []))
        if wanted:
            items = tuple(item for item in self.capture_tree.get_children() if int(self.capture_tree.item(item, 'values')[0]) in wanted)
            if items:
                self.capture_tree.selection_set(items)
        self._capture_follow_suspended = state.get("follow_suspended", False)
        self._files_update_summary()

    def _files_show_live(self) -> None:
        if self._files_mode == "OFFLINE" and self._files_live:
            self._files_mode = "LIVE"
            self._files_path = None
            state, self._files_live = self._files_live, None
            self._files_install_context(state)
            self.wb_latest_button.configure(text="返回最新")
            self._files_source_var.set("实时会话；离线打开不会修改原文件")
            self._update_capture_refresh_state()

    def _files_choose_open(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="离线打开抓包", filetypes=[
            ("本工具抓包", "*.json *.csv *.tsv *.log *.txt"), ("所有文件", "*.*")])
        if path:
            self._files_load(path)

    def _files_load(self, path: str | Path) -> bool:
        if self._files_load_busy:
            return False
        if self.cts_running or self.scenario_running:
            self._files_progress_var.set("测试执行期间保持实时视图；请在测试结束后打开离线日志")
            return False
        self._files_load_busy = True
        self._files_open_button.configure(state="disabled")
        self._files_progress_var.set("正在后台读取离线日志…")
        threading.Thread(target=self._files_load_worker, args=(Path(path), self.settings_path.parent), daemon=True).start()
        return True

    def _files_load_worker(self, path: Path, settings_dir: Path) -> None:
        try:
            document = read_capture_file(path)
            identity = str(uuid5(NAMESPACE_URL, str(path.resolve()).casefold() + "#" + document.sha256))
            notes_path = settings_dir / "offline-bookmarks" / f"{identity}.json"
            self.log_writer.flush(wait=True, timeout=30)
            notes, error = {}, ""
            if notes_path.is_file():
                try:
                    notes = json.loads(notes_path.read_text(encoding="utf-8"))
                    AnnotationLibrary(notes)
                except (OSError, ValueError, TypeError) as exc:
                    error = f"离线标注文件未加载，原文件保留：{exc}"
                    notes = {}
            self.output_queue.put(("files_loaded", (path, document, identity, notes_path, notes, error)))
        except Exception as exc:
            self.output_queue.put(("files_load_error", str(exc)))

    def _files_normalize_record(self, row: dict, sequence: int, previous: dict | None) -> dict:
        record = dict(row, seq=sequence)
        try:
            frame = parse_frame(row["frame"])
            source, destination = f"{frame[0] >> 4:X}", f"{frame[0] & 15:X}"
            opcode = frame[1] if len(frame) > 1 else None
            record.update(source=source, destination=destination, opcode=opcode, is_poll=len(frame) == 1,
                          frame_bytes=len(frame), params=self._format_frame(frame[2:]),
                          opcode_text="POLL" if opcode is None else f"0x{opcode:02X} {self._opcode_name(opcode)}",
                          opcode_explanation="轮询帧，无 Opcode" if opcode is None else self._opcode_explanation(opcode),
                          params_explanation=self._decode_opcode_parameters(opcode, frame[2:]),
                          theoretical_duration_ms=self._capture_theoretical_duration_ms(frame))
        except ValueError:
            record.update(source="—", destination="—", opcode=None, is_poll=False, frame_bytes=None,
                          params="", opcode_text="无法解析", opcode_explanation="原始报文含无效字节",
                          params_explanation="原始文本保留在完整报文中", theoretical_duration_ms=None)
        stamp = finite_number(record.get("timestamp_ms"))
        previous_stamp = previous.get("timestamp_ms") if previous else None
        interval = finite_number(row.get("callback_interval_ms"))
        if interval is None and stamp is not None and previous_stamp is not None:
            interval = stamp - previous_stamp
        gap = finite_number(row.get("estimated_gap_ms"))
        duration = record["theoretical_duration_ms"]
        if gap is None and interval is not None and duration is not None:
            gap = round(interval - duration, 1)
        record.update(timestamp_ms=stamp, callback_interval_ms=interval, estimated_gap_ms=gap)
        record["length_check"] = check_frame_length(record["frame"])
        record["length_status"] = record["length_check"]["status"]
        record["length_message"] = record["length_check"]["message"]
        return record

    def _files_import_chunk(self) -> None:
        self._files_load_job = None
        pending = self._files_pending_load
        if not pending or self._closing:
            return
        deadline = time.perf_counter() + 0.008
        try:
            while pending["index"] < len(pending["document"].records) and time.perf_counter() < deadline:
                index = pending["index"]
                row = pending["document"].records[index]
                record = self._files_normalize_record(row, index + 1, pending["records"][-1] if index else None)
                pending["records"].append(record)
                pending["map"][index + 1] = record
                self._files_record_added(record, pending)
                library = pending["annotations"]
                if not library.get(pending["session"], index + 1) and (row.get("bookmark") or row.get("note") or row.get("tags")):
                    library.set(pending["session"], record, bookmark=row.get("bookmark", False),
                                note=row.get("note", ""), tags=row.get("tags", []))
                pending["index"] += 1
            self._files_progress_var.set(f"正在解析离线日志：{pending['index']} / {len(pending['document'].records)}")
            if pending["index"] < len(pending["document"].records):
                self._files_load_job = self.after(1, self._files_import_chunk)
                return
            if self.cts_running or self.scenario_running:
                raise ValueError("测试已开始，离线数据未切入；实时记录保留")
            if self._files_mode == "LIVE":
                self._files_live = self._files_snapshot_context()
            self._files_mode = "OFFLINE"
            self._files_path = pending["path"]
            self._files_install_context(pending)
            self.wb_latest_button.configure(text="返回实时")
            self._files_source_var.set(f"离线：{pending['path'].name}")
            self._files_progress_var.set(f"已打开 {pending['index']} 条，略过 {pending['document'].skipped_lines} 行非报文文本；原文件保持只读。")
            self._update_capture_refresh_state()
        except Exception as exc:
            self._files_progress_var.set(f"离线读取失败：{exc}")
        finally:
            if self._files_load_job is None:
                self._files_load_busy = False
                self._files_pending_load = None
                self._files_open_button.configure(state="normal")

    def _files_handle_output(self, kind: str, value) -> bool:
        if kind == "files_loaded":
            path, document, identity, notes_path, notes, error = value
            self._files_pending_load = dict(path=path, document=document, session=identity, annotations_path=notes_path,
                                           annotations=AnnotationLibrary(notes) if notes else AnnotationLibrary(),
                                           notes_error=error, records=[], map={}, errors=[], counts=Counter(), marks={}, index=0)
            self._files_import_chunk()
        elif kind == "files_load_error":
            self._files_load_busy = False
            self._files_open_button.configure(state="normal")
            self._files_progress_var.set(f"离线读取失败：{value}；当前抓包记录保留")
            messagebox.showerror("离线读取失败", str(value), parent=self)
        elif kind == "files_exported":
            path, count, error = value
            self._files_progress_var.set(f"导出失败：{error}" if error else f"已导出 {count} 条 → {path}")
            if error:
                messagebox.showerror("导出失败", str(error), parent=self)
        elif kind == "files_copy_ready":
            count, text, error = value
            if error:
                self._files_progress_var.set(f"提取失败：{error}")
            else:
                self._copy_to_clipboard(text)
                self._files_progress_var.set(f"已复制 {count} 条提取结果")
        else:
            return False
        return True

    def _files_extract_records(self, scope: str | None = None) -> list[dict]:
        scope = scope or self._files_scope_var.get()
        if scope == "SELECTED":
            selected = {int(self.capture_tree.item(item, "values")[0]) for item in self.capture_tree.selection()}
            records = [record for record in self.capture_records if record["seq"] in selected]
        elif scope == "MATCHED":
            if not self._wb_apply_query():
                raise ValueError("查找条件无效，请先修正条件")
            records = [record for record in self.capture_records if self._wb_matches_record(record)]
        elif scope == "ALL":
            records = self.capture_records
        else:
            raise ValueError("请选择有效的提取范围")
        return [self.wb_annotations.enrich(self.capture_session_id, record) for record in records]

    def _files_export_fields(self, format_name: str) -> list[str] | None:
        if format_name in {"RAW", "OPCODE"}:
            return None
        fields = [key for key, var in self._files_field_vars.items() if var.get()]
        if not fields:
            raise ValueError("请至少勾选一个提取字段")
        return fields

    def _files_choose_export(self, *, scope: str | None = None, format_name: str | None = None, full: bool = False) -> None:
        try:
            kind = format_name or self._files_format_var.get()
            records = self._files_extract_records(scope)
            if not records:
                raise ValueError("该范围内没有可导出的记录")
            fields = None if full else self._files_export_fields(kind)
            extension = {"CSV": ".csv", "TSV": ".tsv", "JSON": ".json", "RAW": ".txt", "OPCODE": ".txt"}[kind]
            path = filedialog.asksaveasfilename(parent=self, title=f"提取导出 {len(records)} 条消息", defaultextension=extension,
                                               initialfile="capture-export" + extension, filetypes=[(kind, "*" + extension), ("所有文件", "*.*")])
            if path:
                self._files_start_export(Path(path), records, kind, fields)
        except (ValueError, KeyError) as exc:
            self._files_progress_var.set(str(exc))
            messagebox.showwarning("提取导出", str(exc), parent=self)

    def _files_start_export(self, path: Path, records: list[dict], kind: str, fields: list[str] | None = None) -> None:
        if self._files_mode == "OFFLINE" and self._files_path and path.resolve() == self._files_path.resolve():
            raise ValueError("请选择新的导出文件名，原始离线日志保持只读")
        self._files_progress_var.set(f"正在后台导出 {len(records)} 条…")
        self.log_writer.capture_export(path, records, kind, fields,
            lambda error: self.output_queue.put(("files_exported", (str(path), len(records), error))))

    def _files_copy_extract(self) -> None:
        try:
            kind = self._files_format_var.get()
            records = self._files_extract_records()
            fields = self._files_export_fields(kind)
            self._files_progress_var.set(f"正在后台整理 {len(records)} 条提取结果…")
            threading.Thread(target=self._files_copy_worker, args=(records, kind, fields), daemon=True).start()
        except (ValueError, KeyError) as exc:
            self._files_progress_var.set(str(exc))

    def _files_copy_worker(self, records, kind, fields) -> None:
        try:
            self.output_queue.put(("files_copy_ready", (len(records), export_text(records, kind, fields), None)))
        except Exception as exc:
            self.output_queue.put(("files_copy_ready", (len(records), "", str(exc))))

    def _files_clear_context(self) -> None:
        self._files_errors = []
        self._files_counts = Counter()
        self._files_update_summary()
