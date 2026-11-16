"""Collapsible capture workbench, sharing the application's native connection."""
from __future__ import annotations

import bisect
import json
from pathlib import Path
import re
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, colorchooser
from uuid import uuid4

from cec_workbench import AnnotationLibrary, CaptureQuery, QuickCommand, default_quick_commands
from cec_workbench import HIGHLIGHT_PALETTE, highlight_color, highlight_settings, highlight_style, hex_value


LA_CHOICES = list(zip("0123456789ABCDEF", (
    "TV", "Recorder 1", "Recorder 2", "Tuner 1", "Playback 1", "Audio System",
    "Tuner 2", "Tuner 3", "Playback 2", "Recorder 3", "Tuner 4", "Playback 3",
    "Reserved 1", "Reserved 2", "Specific Use", "Broadcast / Unregistered")))


class CaptureWorkbenchMixin:
    def _wb_init(self, read_settings: bool) -> None:
        self.capture_session_id = uuid4().hex
        self._wb_records: dict[int, dict] = {}
        self._wb_matches: list[int] = []
        self._wb_search_dirty = True
        self._wb_query_job = None
        self._wb_quick_busy = False
        self._wb_panel_name = ""
        self._wb_loading_quick = False
        self._wb_search_seq = None
        self._wb_applied_mode = "FILTER"
        self._wb_notes_error = ""
        self._wb_highlight_save_job = None
        self._wb_persist_highlights = read_settings
        self.wb_highlight_status = tk.StringVar()
        self.wb_highlight_swatches = []
        self.wb_annotations_path = self.settings_path.with_name("bookmarks.json")
        self.wb_annotations = AnnotationLibrary()
        if read_settings:
            try:
                payload = json.loads(self.wb_annotations_path.read_text(encoding="utf-8"))
                self.wb_annotations = AnnotationLibrary(payload)
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as exc:
                self._wb_notes_error = f"书签文件未加载，原文件保留：{exc}"
        prefs = self.settings.get("workbench", {})
        prefs = prefs if isinstance(prefs, dict) else {}
        self.wb_commands = []
        stored_commands = prefs.get("quick_commands", [])
        stored_commands = stored_commands if isinstance(stored_commands, list) else []
        for item in stored_commands:
            try:
                self.wb_commands.append(QuickCommand.from_dict(item))
            except (ValueError, TypeError, AttributeError):
                continue
        if "quick_commands" not in prefs:
            self.wb_commands = default_quick_commands()
        self.wb_filter_presets = {str(k): v for k, v in prefs.get("filter_presets", {}).items()
                                 if isinstance(v, dict)} if isinstance(prefs.get("filter_presets", {}), dict) else {}
        self.wb_query_vars = {name: tk.StringVar() for name in ("source", "destination", "opcode", "status", "params")}
        self.wb_bookmarks_only = tk.BooleanVar(value=False)
        self.wb_mode_var = tk.StringVar(value="FILTER")
        self.wb_preset_var = tk.StringVar()
        self.wb_search_status = tk.StringVar(value="匹配 0 条")
        self.wb_query_status = tk.StringVar(value="所有条件按 AND 组合；LA/Opcode可用逗号列出多个值")
        self.wb_note_preview = tk.StringVar(value="右键消息可添加书签、注释和标签；F2 / Shift+F2 跳转标记")
        self.wb_scope_var = tk.StringVar(value="全部会话")
        self.wb_group_var = tk.StringVar(value="常用")
        self.wb_quick_vars = {name: tk.StringVar() for name in (
            "id", "name", "group", "source", "destination", "opcode", "params", "shortcut")}
        self.wb_release_var = tk.BooleanVar(value=False)
        self.wb_quick_preview = tk.StringVar()
        self.wb_query = CaptureQuery()

    def _wb_preferences(self) -> dict:
        return {"quick_commands": [x.to_dict() for x in self.wb_commands],
                "filter_presets": self.wb_filter_presets}

    def _wb_create(self, parent, toolbar, combo_class, opcode_options) -> None:
        self._wb_combo = combo_class
        self._wb_opcode_options = opcode_options
        controls = ttk.Frame(parent)
        controls.grid(row=2, column=0, sticky="ew", pady=(0, 3))
        pages = ("快捷命令", "组合查找", "书签注释", "Opcode 关注")
        for name in pages:
            ttk.Button(controls, text=name, command=lambda page=name: self._wb_toggle(page)).pack(side="left", padx=(0, 4))
        ttk.Button(controls, text="上一命中", command=lambda: self._wb_navigate(-1)).pack(side="left", padx=2)
        ttk.Button(controls, text="下一命中", command=lambda: self._wb_navigate(1)).pack(side="left", padx=2)
        ttk.Label(controls, textvariable=self.wb_search_status).pack(side="left", padx=8)
        ttk.Button(controls, text="返回最新", command=self._wb_latest).pack(side="right")
        self.wb_panel = ttk.Notebook(parent, height=244)
        self.wb_panel.grid(row=4, column=0, sticky="ew", pady=(3, 0))
        self.wb_pages = {name: ttk.Frame(self.wb_panel, padding=4) for name in pages}
        for name, page in self.wb_pages.items():
            self.wb_panel.add(page, text=name)
        self._wb_create_quick(self.wb_pages["快捷命令"])
        self._wb_create_search(self.wb_pages["组合查找"])
        self._wb_create_notes(self.wb_pages["书签注释"])
        self._wb_create_highlights(self.wb_pages["Opcode 关注"])
        self.wb_panel.grid_remove()

    def _wb_create_highlights(self, parent) -> None:
        header = ttk.Frame(parent)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        ttk.Label(header, textvariable=self.wb_highlight_status).pack(side="left")
        ttk.Button(header, text="清空关注", command=self._wb_clear_highlights).pack(side="right", padx=2)
        ttk.Button(header, text="恢复默认颜色", command=self._wb_reset_highlight_colors).pack(side="right", padx=2)
        self.wb_highlight_combos = []
        self.wb_highlight_color_combos = []
        for column in range(2):
            parent.columnconfigure(column, weight=1, uniform="highlight_rule")
        for index, (opcode, color) in enumerate(zip(self.capture_highlight_vars, self.capture_highlight_color_vars)):
            rule = ttk.Frame(parent)
            rule.grid(row=index // 2 + 1, column=index % 2, sticky="ew", padx=(0, 12) if index % 2 == 0 else 0, pady=2)
            rule.columnconfigure(1, weight=1)
            ttk.Label(rule, text=f"{index + 1:02d}", width=3).grid(row=0, column=0)
            combo = self._wb_combo(rule, raw_variable=opcode,
                                   options=[("", "不高亮"), *self._wb_opcode_options],
                                   width=24, display_width_limit=40, state="normal")
            combo.grid(row=0, column=1, sticky="ew", padx=(0, 5))
            self.wb_highlight_combos.append(combo)
            swatch = tk.Label(rule, width=2, background=color.get(), relief="solid", borderwidth=1)
            swatch.grid(row=0, column=2, padx=(0, 3))
            self.wb_highlight_swatches.append(swatch)
            color_combo = self._wb_combo(rule, raw_variable=color, options=HIGHLIGHT_PALETTE,
                                         width=16, display_width_limit=16, state="readonly")
            color_combo.grid(row=0, column=3)
            self.wb_highlight_color_combos.append(color_combo)
            ttk.Button(rule, text="自选", width=4, command=lambda i=index: self._wb_pick_highlight_color(i)).grid(row=0, column=4, padx=(3, 0))
        ttk.Label(parent, text="仅给 Opcode 单元格上色；相同 Opcode 按编号最小的项生效。隐藏面板后仍生效，配置自动保存。",
                  foreground="#555555").grid(row=7, column=0, columnspan=2, sticky="w", pady=(4, 0))

    def _wb_highlight_preferences(self) -> list[dict[str, str]]:
        return highlight_settings({"capture_highlight_rules": [
            {"opcode": opcode.get(), "color": color.get()}
            for opcode, color in zip(self.capture_highlight_vars, self.capture_highlight_color_vars)]})

    def _wb_load_highlights(self) -> None:
        self._wb_loading_highlights = True
        try:
            for index, rule in enumerate(highlight_settings(self.settings)):
                self.capture_highlight_vars[index].set(rule["opcode"])
                self.capture_highlight_color_vars[index].set(rule["color"])
        finally:
            self._wb_loading_highlights = False
        self._wb_highlights_changed(persist=False)

    def _wb_apply_highlight_styles(self) -> None:
        for index, rule in enumerate(self._wb_highlight_preferences()):
            style = highlight_style(rule["color"])
            self.capture_tree.tag_configure(f"capture_opcode_highlight_{index + 1}", **style)
            if index < len(self.wb_highlight_swatches):
                self.wb_highlight_swatches[index].configure(background=rule["color"])

    def _wb_highlights_changed(self, *, persist: bool = True) -> None:
        if self._wb_loading_highlights:
            return
        tags = {}
        errors = []
        duplicates = []
        active = 0
        for index, (opcode_var, color_var) in enumerate(zip(self.capture_highlight_vars, self.capture_highlight_color_vars), 1):
            try:
                highlight_color(color_var.get())
                if opcode_var.get().strip():
                    opcode = hex_value(opcode_var.get())
                    active += 1
                    if opcode in tags:
                        duplicates.append(str(index))
                    else:
                        tags[opcode] = f"capture_opcode_highlight_{index}"
            except ValueError:
                errors.append(str(index))
        previous_tags = self._capture_highlight_tags
        self._capture_highlight_tags = tags
        status = f"已启用 {active}/{len(self.capture_highlight_vars)} 项 · 16 色预设 / 自选颜色"
        if errors:
            status = "请检查第 " + ", ".join(errors) + " 项的 Opcode / 颜色"
        elif duplicates:
            status += "；重复项 " + ", ".join(duplicates) + " 由前项覆盖"
        self.wb_highlight_status.set(status)
        if hasattr(self, "capture_tree"):
            tree = self.capture_tree
            tree.begin_update()
            try:
                self._wb_apply_highlight_styles()
                if previous_tags != tags:
                    for item in tree.get_children():
                        data = tree.item(item)
                        record = self._wb_records.get(int(data["values"][0]), {})
                        row_tags = tuple(t for t in data["tags"] if not t.startswith("capture_opcode_highlight_"))
                        highlight = self._capture_highlight_tag(record.get("opcode"))
                        row_tags += (highlight,) if highlight else ()
                        if row_tags != data["tags"]:
                            tree.item(item, tags=row_tags)
            finally:
                tree.end_update()
        if persist and self._wb_persist_highlights:
            if self._wb_highlight_save_job is not None:
                self.after_cancel(self._wb_highlight_save_job)
                self._wb_highlight_save_job = None
            if not errors:
                self._wb_highlight_save_job = self.after(400, self._wb_save_highlights)

    def _wb_save_highlights(self) -> None:
        self._wb_highlight_save_job = None
        self._save_settings()

    def _wb_pick_highlight_color(self, index: int) -> None:
        initial = self._wb_highlight_preferences()[index]["color"]
        _rgb, color = colorchooser.askcolor(color=initial, parent=self, title=f"Opcode 关注 {index + 1} — 自选颜色")
        if color:
            self.capture_highlight_color_vars[index].set(highlight_color(color))

    def _wb_clear_highlights(self) -> None:
        self._wb_loading_highlights = True
        try:
            for var in self.capture_highlight_vars:
                var.set("")
        finally:
            self._wb_loading_highlights = False
        self._wb_highlights_changed()

    def _wb_reset_highlight_colors(self) -> None:
        self._wb_loading_highlights = True
        try:
            for var, (color, _name) in zip(self.capture_highlight_color_vars, HIGHLIGHT_PALETTE):
                var.set(color)
        finally:
            self._wb_loading_highlights = False
        self._wb_highlights_changed()

    def _wb_toggle(self, name: str, *, open_only: bool = False) -> None:
        same = self.wb_panel.winfo_manager() == "grid" and self.wb_panel.select() == str(self.wb_pages[name])
        if same and not open_only:
            self.wb_panel.grid_remove()
            self._wb_panel_name = ""
        else:
            self.wb_panel.grid()
            self.wb_panel.select(self.wb_pages[name])
            self._wb_panel_name = name
            if name == "书签注释":
                self._wb_refresh_notes()

    def _wb_bind(self) -> None:
        self.capture_tree.bind("<<TreeviewSelect>>", lambda _e: self._wb_selection_changed(), add="+")
        for key, action in (("<Control-f>", lambda: self._wb_toggle("组合查找", open_only=True)),
                            ("<F3>", lambda: self._wb_navigate(1)),
                            ("<Shift-F3>", lambda: self._wb_navigate(-1)),
                            ("<F2>", lambda: self._wb_navigate_annotation(1)),
                            ("<Shift-F2>", lambda: self._wb_navigate_annotation(-1))):
            self.bind(key, lambda _e, fn=action: self._wb_capture_key(fn), add="+")
        self._wb_bind_shortcuts()

    def _wb_capture_key(self, action):
        if self.notebook.select() == str(self.capture_tab_widget):
            action()
            return "break"
        return None

    def _wb_bind_shortcuts(self) -> None:
        for number in range(5, 13):
            key = f"F{number}"
            self.bind(f"<{key}>", lambda _e, shortcut=key: self._wb_capture_key(
                lambda: self._wb_run_shortcut(shortcut)))

    def _wb_create_quick(self, parent) -> None:
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(1, weight=1)
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(top, text="分组").pack(side="left")
        self.wb_group_combo = ttk.Combobox(top, textvariable=self.wb_group_var, width=12, state="readonly")
        self.wb_group_combo.pack(side="left", padx=4)
        self.wb_group_combo.bind("<<ComboboxSelected>>", lambda _e: self._wb_refresh_quick())
        self.wb_quick_buttons = ttk.Frame(top)
        self.wb_quick_buttons.pack(side="left", fill="x", expand=True)
        left = ttk.Frame(parent)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self.wb_quick_tree = ttk.Treeview(left, columns=("name", "key"), show="headings", height=5, selectmode="browse")
        self.wb_quick_tree.heading("name", text="命令（双击发送）")
        self.wb_quick_tree.heading("key", text="快捷键")
        self.wb_quick_tree.column("name", width=170)
        self.wb_quick_tree.column("key", width=55, stretch=False)
        self.wb_quick_tree.pack(fill="both", expand=True)
        self.wb_quick_tree.bind("<<TreeviewSelect>>", self._wb_quick_selected)
        self.wb_quick_tree.bind("<Double-1>", lambda _e: self._wb_send_quick())
        actions = ttk.Frame(left)
        actions.pack(fill="x")
        ttk.Button(actions, text="新建", command=self._wb_new_quick).pack(side="left")
        ttk.Button(actions, text="删除", command=self._wb_delete_quick).pack(side="left", padx=3)
        editor = ttk.Frame(parent)
        editor.grid(row=1, column=1, sticky="nsew")
        editor.columnconfigure(1, weight=1)
        editor.columnconfigure(3, weight=1)
        for label, key, row, col in (("名称", "name", 0, 0), ("分组", "group", 0, 2),
                                    ("参数", "params", 2, 0), ("快捷键", "shortcut", 2, 2)):
            ttk.Label(editor, text=label).grid(row=row, column=col, sticky="w", padx=2)
            if key == "shortcut":
                self._wb_combo(editor, raw_variable=self.wb_quick_vars[key],
                               options=[("", "无"), *[(f"F{x}", "发送本命令") for x in range(5, 13)]],
                               width=9, display_width_limit=15, state="readonly").grid(row=row, column=col+1, sticky="ew", padx=2)
            else:
                ttk.Entry(editor, textvariable=self.wb_quick_vars[key], width=16).grid(row=row, column=col+1, sticky="ew", padx=2)
        addressing = ttk.Frame(editor)
        addressing.grid(row=1, column=0, columnspan=4, sticky="ew", pady=4)
        for label, key in (("源LA", "source"), ("目标LA", "destination")):
            ttk.Label(addressing, text=label).pack(side="left")
            self._wb_combo(addressing, raw_variable=self.wb_quick_vars[key], options=LA_CHOICES,
                           width=12, display_width_limit=20, state="normal").pack(side="left", padx=3)
        ttk.Label(addressing, text="Opcode").pack(side="left")
        self._wb_combo(addressing, raw_variable=self.wb_quick_vars["opcode"], options=self._wb_opcode_options,
                       width=18, display_width_limit=30, state="normal").pack(side="left", padx=3, fill="x", expand=True)
        options = ttk.Frame(editor)
        options.grid(row=3, column=0, columnspan=4, sticky="ew", pady=3)
        ttk.Checkbutton(options, text="44按下后自动45释放", variable=self.wb_release_var).pack(side="left")
        ttk.Checkbutton(options, text="发送前应用源LA", variable=self.raw_auto_la_var).pack(side="left", padx=6)
        ttk.Checkbutton(options, text="txn（不等ACK）", variable=self.raw_no_ack_var).pack(side="left")
        ttk.Label(editor, textvariable=self.wb_quick_preview, foreground="#1F3B53", wraplength=750).grid(row=4, column=0, columnspan=4, sticky="w")
        buttons = ttk.Frame(editor)
        buttons.grid(row=5, column=0, columnspan=4, sticky="w", pady=4)
        ttk.Button(buttons, text="发送预览报文", command=self._wb_send_quick).pack(side="left")
        ttk.Button(buttons, text="保存命令", command=self._wb_save_quick).pack(side="left", padx=4)
        ttk.Button(buttons, text="填入Raw", command=self._wb_to_raw).pack(side="left")
        for var in self.wb_quick_vars.values():
            var.trace_add("write", lambda *_: self._wb_update_quick_preview())
        self.wb_release_var.trace_add("write", lambda *_: self._wb_update_quick_preview())
        self._wb_refresh_quick()

    def _wb_refresh_quick(self) -> None:
        groups = sorted({c.group for c in self.wb_commands} | {"常用"})
        self.wb_group_combo.configure(values=groups)
        if self.wb_group_var.get() not in groups:
            self.wb_group_var.set(groups[0])
        self.wb_quick_tree.delete(*self.wb_quick_tree.get_children())
        for child in self.wb_quick_buttons.winfo_children():
            child.destroy()
        commands = [c for c in self.wb_commands if c.group == self.wb_group_var.get()]
        for index, command in enumerate(commands):
            self.wb_quick_tree.insert("", "end", iid=command.id, values=(command.name, command.shortcut))
            if index < 6:
                ttk.Button(self.wb_quick_buttons, text=command.name,
                           command=lambda c=command: self._wb_execute_saved(c)).pack(side="left", padx=2)
        if commands:
            self.wb_quick_tree.selection_set(commands[0].id)
            self._wb_load_quick(commands[0])
        else:
            self._wb_new_quick()

    def _wb_load_quick(self, command: QuickCommand) -> None:
        self._wb_loading_quick = True
        for key, var in self.wb_quick_vars.items():
            var.set(getattr(command, key))
        self.wb_release_var.set(command.release_key)
        self._wb_loading_quick = False
        self._wb_update_quick_preview()

    def _wb_quick_selected(self, _event=None) -> None:
        selection = self.wb_quick_tree.selection()
        if selection:
            command = next((c for c in self.wb_commands if c.id == selection[0]), None)
            if command:
                self._wb_load_quick(command)

    def _wb_quick_draft(self) -> QuickCommand:
        return QuickCommand.from_dict({**{k: v.get() for k, v in self.wb_quick_vars.items()},
                                       "release_key": self.wb_release_var.get()})

    def _wb_update_quick_preview(self) -> None:
        if self._wb_loading_quick:
            return
        try:
            command = self._wb_quick_draft()
            frames = command.frames()
            self.wb_quick_preview.set(f"源 {command.source} → 目标 {command.destination}：" + "  →100ms→  ".join(frames))
        except ValueError as exc:
            self.wb_quick_preview.set(str(exc))

    def _wb_new_quick(self) -> None:
        self._wb_load_quick(QuickCommand(uuid4().hex, "新命令", self.wb_group_var.get()))

    def _wb_save_quick(self) -> None:
        try:
            command = self._wb_quick_draft()
            if command.shortcut and any(c.shortcut == command.shortcut and c.id != command.id for c in self.wb_commands):
                raise ValueError(f"{command.shortcut} 已被其他快捷命令使用")
        except ValueError as exc:
            messagebox.showerror("快捷命令", str(exc), parent=self)
            return
        self.wb_commands = [command if c.id == command.id else c for c in self.wb_commands]
        if not any(c.id == command.id for c in self.wb_commands):
            self.wb_commands.append(command)
        self.wb_group_var.set(command.group)
        self._save_settings()
        self._wb_refresh_quick()
        self.wb_quick_tree.selection_set(command.id)
        self._wb_load_quick(command)
        self.status_var.set(f"已保存快捷命令：{command.name}")

    def _wb_delete_quick(self) -> None:
        key = self.wb_quick_vars["id"].get()
        self.wb_commands = [c for c in self.wb_commands if c.id != key]
        self._save_settings()
        self._wb_refresh_quick()

    def _wb_execute_saved(self, command: QuickCommand) -> None:
        self._wb_toggle("快捷命令", open_only=True)
        if self.wb_group_var.get() != command.group:
            self.wb_group_var.set(command.group)
            self._wb_refresh_quick()
        if self.wb_quick_tree.exists(command.id):
            self.wb_quick_tree.selection_set(command.id)
            self.wb_quick_tree.see(command.id)
        self._wb_load_quick(command)
        self._wb_send_quick()

    def _wb_run_shortcut(self, shortcut: str) -> None:
        command = next((c for c in self.wb_commands if c.shortcut == shortcut), None)
        if command:
            self._wb_execute_saved(command)

    def _wb_to_raw(self) -> None:
        try:
            command = self._wb_quick_draft()
        except ValueError as exc:
            self.wb_quick_preview.set(str(exc))
            return
        for name in ("source", "destination", "opcode", "params"):
            getattr(self, f"raw_{name}_var").set(getattr(command, name))
        self.raw_frame_var.set(command.frames()[0])
        self.notebook.select(self.device_tab_widget)
        self.device_notebook.select(self.raw_tab_widget)

    def _wb_send_quick(self) -> None:
        try:
            command = self._wb_quick_draft()
        except ValueError as exc:
            messagebox.showerror("快捷命令", str(exc), parent=self)
            return
        if not self.backend.is_open:
            self.status_var.set("请先在顶部连接设备，再发送快捷命令")
            return
        if self._wb_quick_busy or self.raw_send_busy or self.oneshot_busy or self.cts_running or self.scenario_running:
            self.status_var.set("当前发送/测试尚未结束，请稍后发送快捷命令")
            return
        self._wb_quick_busy = self.raw_send_busy = self.oneshot_busy = True
        frames = command.frames()
        mode = "txn" if self.raw_no_ack_var.get() else "tx"
        auto_la = self.raw_auto_la_var.get() and not self.backend.occupancy_mask
        self.status_var.set(f"发送快捷命令：{command.name}；{frames[0]}")
        threading.Thread(target=self._wb_quick_worker, args=(command, mode, auto_la), daemon=True).start()

    def _wb_quick_worker(self, command: QuickCommand, mode: str, auto_la: bool) -> None:
        try:
            if auto_la:
                result = self.backend.execute_command(f"la {command.source}")
                self.output_queue.put(("wb_command", (f"la {command.source}", result)))
                if not result.ok:
                    return
            frames = command.frames()
            for index, frame in enumerate(frames):
                if not self.backend.is_open:
                    break
                result = self.backend.execute_command(f"{mode} {frame}")
                self.output_queue.put(("wb_command", (f"{mode} {frame}", result)))
                # A key release is attempted even if Pressed returned a NACK/error.
                if not result.ok and len(frames) == 1:
                    break
                if index + 1 < len(frames):
                    time.sleep(0.1)
        except Exception as exc:
            self.output_queue.put(("wb_error", str(exc)))
        finally:
            self.output_queue.put(("wb_done", command.name))

    def _wb_handle_output(self, kind: str, value) -> bool:
        if kind == "wb_command":
            command, result = value
            self._add_history(command)
            self._append_output(f">>> 快捷命令 {command}\n{result.text}\n")
            if command.startswith(("tx ", "txn ")):
                mode, frame = command.split(" ", 1)
                self._raw_add_history(mode, frame, 1)
            if not result.ok:
                self._append_output("[快捷命令失败]\n")
        elif kind == "wb_error":
            self._append_output(f"[快捷命令异常] {value}\n")
        elif kind == "wb_done":
            self._wb_quick_busy = self.raw_send_busy = self.oneshot_busy = False
            self.status_var.set(f"快捷命令执行结束：{value}（ACK结果见报文列表）")
        else:
            return False
        return True

    def _wb_create_search(self, parent) -> None:
        for col in (1, 3, 5):
            parent.columnconfigure(col, weight=1)
        specs = (("源LA", "source", 0, 0), ("目标LA", "destination", 0, 2), ("Opcode", "opcode", 0, 4),
                 ("ACK状态", "status", 1, 0), ("参数掩码", "params", 1, 2))
        for label, key, row, col in specs:
            ttk.Label(parent, text=label).grid(row=row, column=col, sticky="w", padx=3, pady=3)
            if key in ("source", "destination"):
                self._wb_combo(parent, raw_variable=self.wb_query_vars[key], options=[("", "任意"), *LA_CHOICES],
                               width=14, display_width_limit=25, state="normal").grid(row=row, column=col+1, sticky="ew", padx=3)
            elif key == "opcode":
                self._wb_combo(parent, raw_variable=self.wb_query_vars[key],
                               options=[("", "任意"), ("POLL", "轮询帧"), *self._wb_opcode_options],
                               width=22, display_width_limit=38, state="normal").grid(row=row, column=col+1, sticky="ew", padx=3)
            elif key == "status":
                self._wb_combo(parent, raw_variable=self.wb_query_vars[key],
                               options=[("", "任意"), ("ACK", "已确认"), ("NACK", "未确认"), ("ERROR", "错误"), ("UNKNOWN", "未知")],
                               width=14, state="readonly").grid(row=row, column=col+1, sticky="ew", padx=3)
            else:
                ttk.Entry(parent, textvariable=self.wb_query_vars[key]).grid(row=row, column=col+1, sticky="ew", padx=3)
        ttk.Checkbutton(parent, text="仅书签", variable=self.wb_bookmarks_only).grid(row=1, column=4, sticky="w")
        self._wb_combo(parent, raw_variable=self.wb_mode_var,
                       options=[("FILTER", "筛选列表"), ("FIND", "仅定位，保留列表")], width=20,
                       state="readonly").grid(row=1, column=5, sticky="ew", padx=3)
        actions = ttk.Frame(parent)
        actions.grid(row=2, column=0, columnspan=6, sticky="w", pady=7)
        ttk.Button(actions, text="应用条件", command=self._wb_apply_query).pack(side="left")
        ttk.Button(actions, text="清除条件", command=self._wb_clear_query).pack(side="left", padx=4)
        ttk.Label(actions, text="已保存").pack(side="left", padx=(10, 2))
        self.wb_preset_combo = ttk.Combobox(actions, textvariable=self.wb_preset_var, state="readonly", width=20,
                                           values=sorted(self.wb_filter_presets))
        self.wb_preset_combo.pack(side="left")
        self.wb_preset_combo.bind("<<ComboboxSelected>>", lambda _e: self._wb_load_filter())
        ttk.Button(actions, text="保存条件", command=self._wb_save_filter).pack(side="left", padx=4)
        ttk.Button(actions, text="删除条件", command=self._wb_delete_filter).pack(side="left")
        ttk.Label(parent, textvariable=self.wb_query_status, foreground="#555555").grid(row=3, column=0, columnspan=6, sticky="w", pady=4)
        ttk.Label(parent, text="参数按前缀匹配：?? 任意字节、A? 任意低半字节，例如 01:??:A?。顶部文字同时搜索报文、解释、注释和标签。\nCtrl+F 打开；F3 / Shift+F3 前后定位，命中可跨越2000条显示窗口。", foreground="#555555", wraplength=1020).grid(row=4, column=0, columnspan=6, sticky="w", pady=3)

    def _wb_query_data(self) -> dict:
        return {**{k: v.get() for k, v in self.wb_query_vars.items()}, "text": self.capture_filter_var.get(),
                "bookmarks_only": self.wb_bookmarks_only.get(), "mode": self.wb_mode_var.get()}

    def _wb_schedule_query(self, _event=None) -> None:
        if self._wb_query_job is not None:
            self.after_cancel(self._wb_query_job)
        self._wb_query_job = self.after(180, self._wb_apply_query)

    def _wb_apply_query(self) -> bool:
        if self._wb_query_job is not None:
            self.after_cancel(self._wb_query_job)
            self._wb_query_job = None
        try:
            query = CaptureQuery.from_dict(self._wb_query_data())
        except ValueError as exc:
            self.wb_query_status.set(f"条件未应用：{exc}")
            return False
        changed = query != self.wb_query or self.wb_mode_var.get() != self._wb_applied_mode
        self.wb_query = query
        self._wb_applied_mode = self.wb_mode_var.get()
        if None in query.opcodes:
            changed = changed or not self.capture_show_poll_var.get()
            self.capture_show_poll_var.set(True)
        if changed:
            self._wb_rebuild_matches()
            self._refresh_capture_tree(force=True)
        else:
            self._wb_update_search_status()
        self.wb_query_status.set("条件已应用；" + ("仅定位，不隐藏其他消息" if self._wb_applied_mode == "FIND" else "列表显示匹配消息"))
        return True

    def _wb_base_changed(self) -> None:
        if hasattr(self, "wb_query"):
            self._wb_rebuild_matches()
        self._refresh_capture_tree()

    def _wb_base_matches(self, record: dict) -> bool:
        return (record.get("opcode") is not None or self.capture_show_poll_var.get()) and (
            self.capture_direction_var.get() == "ALL" or record.get("direction") == self.capture_direction_var.get())

    def _wb_annotation(self, record: dict) -> dict:
        return self.wb_annotations.get(self.capture_session_id, int(record["seq"]))

    def _wb_matches_record(self, record: dict) -> bool:
        return self._wb_base_matches(record) and self.wb_query.matches(record, self._wb_annotation(record))

    def _wb_rebuild_matches(self) -> None:
        self._wb_matches = [int(r["seq"]) for r in self.capture_records if self._wb_matches_record(r)]
        self._wb_search_dirty = True
        self._wb_update_search_status()

    def _wb_on_record(self, record: dict) -> None:
        self._wb_records[int(record["seq"])] = record
        if self._wb_matches_record(record):
            self._wb_matches.append(int(record["seq"]))
        self._wb_search_dirty = True

    def _wb_update_search_status(self) -> None:
        if not self._wb_search_dirty:
            return
        seq = self._capture_selected_sequence() if hasattr(self, "capture_tree") else None
        index = bisect.bisect_left(self._wb_matches, seq) if seq is not None else 0
        position = f"；第 {index+1} 条" if seq is not None and index < len(self._wb_matches) and self._wb_matches[index] == seq else ""
        mode = "筛选" if self._wb_applied_mode == "FILTER" else "定位"
        self.wb_search_status.set(f"{mode}匹配 {len(self._wb_matches)} 条{position}")
        self._wb_search_dirty = False

    def _wb_navigate(self, direction: int) -> None:
        if not self._wb_apply_query() or not self._wb_matches:
            return
        seq = self._capture_selected_sequence()
        if seq is None:
            index = 0 if direction > 0 else len(self._wb_matches)-1
        else:
            index = bisect.bisect_right(self._wb_matches, seq) if direction > 0 else bisect.bisect_left(self._wb_matches, seq)-1
            index %= len(self._wb_matches)
        self._wb_jump(self._wb_matches[index])

    def _wb_jump(self, sequence: int) -> None:
        record = self._wb_records.get(sequence)
        if record is None:
            return
        items = {int(self.capture_tree.item(item, "values")[0]): item for item in self.capture_tree.get_children()}
        if sequence not in items:
            records = [r for r in self.capture_records if self._capture_record_matches_filter(r) or int(r["seq"]) == sequence]
            index = next(i for i, r in enumerate(records) if int(r["seq"]) == sequence)
            start = min(max(0, index-self.capture_display_limit//2), max(0, len(records)-self.capture_display_limit))
            records = records[start:start+self.capture_display_limit]
            self.capture_tree.begin_update()
            try:
                self.capture_tree.delete(*self.capture_tree.get_children())
                self.capture_visible_tx_count = self.capture_visible_rx_count = 0
                for row in records:
                    values, tags = self._capture_record_values_and_tags(row)
                    item = self.capture_tree.insert("", "end", values=values, tags=tags)
                    items[int(row["seq"])] = item
                    if row.get("direction") == "TX":
                        self.capture_visible_tx_count += 1
                    else:
                        self.capture_visible_rx_count += 1
            finally:
                self.capture_tree.end_update()
            self._update_capture_summary()
        item = items[sequence]
        self.capture_tree.selection_set(item)
        self.capture_tree.focus(item)
        self.capture_tree.see(item)
        self._wb_selection_changed()

    def _wb_latest(self) -> None:
        self.capture_paused_var.set(False)
        self._refresh_capture_tree(force=True)
        self.capture_tree.see_end()

    def _wb_clear_query(self) -> None:
        for var in self.wb_query_vars.values():
            var.set("")
        self.capture_filter_var.set("")
        self.wb_bookmarks_only.set(False)
        self._wb_apply_query()

    def _wb_save_filter(self) -> None:
        if not self._wb_apply_query():
            return
        name = simpledialog.askstring("保存查找条件", "名称", initialvalue=self.wb_preset_var.get(), parent=self)
        if name and name.strip():
            self.wb_filter_presets[name.strip()] = self._wb_query_data()
            self.wb_preset_var.set(name.strip())
            self.wb_preset_combo.configure(values=sorted(self.wb_filter_presets))
            self._save_settings()

    def _wb_load_filter(self) -> None:
        data = self.wb_filter_presets.get(self.wb_preset_var.get())
        if data:
            for key, var in self.wb_query_vars.items():
                var.set(data.get(key, ""))
            self.capture_filter_var.set(data.get("text", ""))
            self.wb_bookmarks_only.set(bool(data.get("bookmarks_only")))
            self.wb_mode_var.set(data.get("mode", "FILTER"))
            self._wb_apply_query()

    def _wb_delete_filter(self) -> None:
        self.wb_filter_presets.pop(self.wb_preset_var.get(), None)
        self.wb_preset_var.set("")
        self.wb_preset_combo.configure(values=sorted(self.wb_filter_presets))
        self._save_settings()

    def _wb_create_notes(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        controls = ttk.Frame(parent)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 3))
        scope = ttk.Combobox(controls, values=("全部会话", "当前会话"), textvariable=self.wb_scope_var, width=12, state="readonly")
        scope.pack(side="left")
        scope.bind("<<ComboboxSelected>>", lambda _e: self._wb_refresh_notes())
        for label, action in (("定位/查看", self._wb_note_jump), ("编辑注释", self._wb_note_edit),
                              ("上一标记", lambda: self._wb_navigate_annotation(-1)),
                              ("下一标记", lambda: self._wb_navigate_annotation(1))):
            ttk.Button(controls, text=label, command=action).pack(side="left", padx=3)
        columns = ("session", "seq", "time", "frame", "tags", "note")
        self.wb_notes_tree = ttk.Treeview(parent, columns=columns, show="headings", height=5, selectmode="browse")
        self.wb_notes_tree.grid(row=1, column=0, sticky="nsew")
        for key, label, width in zip(columns, ("会话", "消息#", "时间", "报文", "标签", "注释"), (100, 60, 115, 180, 150, 300)):
            self.wb_notes_tree.heading(key, text=label)
            self.wb_notes_tree.column(key, width=width, minwidth=45)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.wb_notes_tree.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.wb_notes_tree.configure(yscrollcommand=scrollbar.set)
        self.wb_notes_tree.bind("<Double-1>", lambda _e: self._wb_note_jump())
        ttk.Label(parent, textvariable=self.wb_note_preview, foreground="#555555", wraplength=1100).grid(row=2, column=0, sticky="w", pady=3)
        ttk.Label(parent, text=f"自动保存：{self.wb_annotations_path}", foreground="#666666").grid(row=3, column=0, sticky="w")

    def _wb_refresh_notes(self) -> None:
        if not hasattr(self, "wb_notes_tree"):
            return
        selection = self.wb_notes_tree.selection()
        self.wb_notes_tree.delete(*self.wb_notes_tree.get_children())
        for key, entry in self.wb_annotations.entries.items():
            if self.wb_scope_var.get() == "当前会话" and entry["session_id"] != self.capture_session_id:
                continue
            row = entry["record"]
            self.wb_notes_tree.insert("", "end", iid=key, values=("当前" if entry["session_id"] == self.capture_session_id else entry["session_id"][:8],
                ("★ " if entry.get("bookmark") else "") + str(entry["seq"]), row.get("time", ""), row.get("frame", ""),
                ", ".join(entry.get("tags", [])), entry.get("note", "")))
        if selection and self.wb_notes_tree.exists(selection[0]):
            self.wb_notes_tree.selection_set(selection[0])
        if self._wb_notes_error:
            self.wb_note_preview.set(self._wb_notes_error)

    def _wb_selection_changed(self) -> None:
        sequence = self._capture_selected_sequence()
        annotation = self.wb_annotations.get(self.capture_session_id, sequence) if sequence else {}
        if sequence:
            self.wb_note_preview.set(f"消息 #{sequence}：" + (annotation.get("note", "") or "未添加注释") +
                                     ("；标签：" + ", ".join(annotation.get("tags", [])) if annotation.get("tags") else ""))
        self._wb_search_dirty = True
        self._wb_update_search_status()

    def _wb_current_record(self) -> dict | None:
        seq = self._capture_selected_sequence()
        return self._wb_records.get(seq) if seq is not None else None

    def _wb_record_mark(self, record: dict) -> str:
        annotation = self._wb_annotation(record)
        if annotation.get("bookmark"):
            return "★"
        return "✎" if annotation.get("note") or annotation.get("tags") else ""

    def _wb_toggle_bookmark(self) -> None:
        record = self._wb_current_record()
        if not record:
            return
        old = self._wb_annotation(record)
        self._wb_store_annotation(self.capture_session_id, record, not old.get("bookmark", False), old.get("note", ""), old.get("tags", []))

    def _wb_store_annotation(self, session: str, record: dict, bookmark: bool, note: str, tags: list[str]) -> None:
        if self._wb_notes_error:
            messagebox.showerror("书签文件未加载", self._wb_notes_error + "\n请修复或备份该文件后重新打开工具。", parent=self)
            return
        self.wb_annotations.set(session, record, bookmark=bookmark, note=note, tags=tags)
        self.log_writer.replace_json(self.wb_annotations_path, self.wb_annotations.payload())
        if session == self.capture_session_id:
            for item in self.capture_tree.get_children():
                if int(self.capture_tree.item(item, "values")[0]) == int(record["seq"]):
                    values, row_tags = self._capture_record_values_and_tags(record)
                    self.capture_tree.item(item, values=values, tags=row_tags)
                    break
            self._wb_rebuild_matches()
            if self.wb_query.bookmarks_only or self.wb_query.text:
                self._refresh_capture_tree(force=True)
            self._wb_selection_changed()
        self._wb_refresh_notes()

    def _wb_edit_annotation(self, record: dict | None = None, session: str | None = None) -> None:
        record = record or self._wb_current_record()
        if not record:
            return
        session = session or self.capture_session_id
        old = self.wb_annotations.get(session, int(record["seq"]))
        dialog = tk.Toplevel(self)
        dialog.title(f"消息 #{record['seq']} — 书签与注释")
        dialog.transient(self)
        dialog.geometry("640x340")
        body = ttk.Frame(dialog, padding=12)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(3, weight=1)
        ttk.Label(body, text=f"{record.get('time', '')}  {record.get('frame', '')}\n{record.get('opcode_text', '')}  {record.get('params_explanation', '')}", wraplength=600).grid(row=0, column=0, columnspan=2, sticky="w")
        bookmark_var = tk.BooleanVar(value=bool(old.get("bookmark", True)))
        ttk.Checkbutton(body, text="加入书签", variable=bookmark_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=5)
        ttk.Label(body, text="标签（逗号分隔）").grid(row=2, column=0, sticky="w")
        tag_var = tk.StringVar(value=", ".join(old.get("tags", [])))
        ttk.Entry(body, textvariable=tag_var).grid(row=2, column=1, sticky="ew")
        note_text = tk.Text(body, height=6, wrap="word", undo=True)
        note_text.grid(row=3, column=0, columnspan=2, sticky="nsew", pady=6)
        note_text.insert("1.0", old.get("note", ""))
        def save():
            self._wb_store_annotation(session, record, bookmark_var.get(), note_text.get("1.0", "end-1c"), re.split(r"[,，]", tag_var.get()))
            dialog.destroy()
        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="保存", command=save).pack(side="left", padx=4)
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="left")
        note_text.focus_set()

    def _wb_note_entry(self) -> dict | None:
        selection = self.wb_notes_tree.selection()
        return self.wb_annotations.entries.get(selection[0]) if selection else None

    def _wb_note_jump(self) -> None:
        entry = self._wb_note_entry()
        if entry:
            if entry["session_id"] == self.capture_session_id:
                self._wb_jump(entry["seq"])
            else:
                self._wb_edit_annotation(entry["record"], entry["session_id"])

    def _wb_note_edit(self) -> None:
        entry = self._wb_note_entry()
        if entry:
            self._wb_edit_annotation(entry["record"], entry["session_id"])

    def _wb_navigate_annotation(self, direction: int) -> None:
        entries = sorted(e["seq"] for e in self.wb_annotations.entries.values() if e["session_id"] == self.capture_session_id)
        if not entries:
            return
        seq = self._capture_selected_sequence()
        index = (bisect.bisect_right(entries, seq) if direction > 0 else bisect.bisect_left(entries, seq)-1) if seq is not None else (0 if direction > 0 else -1)
        self._wb_jump(entries[index % len(entries)])

    def _wb_clear_session(self) -> None:
        self.capture_session_id = uuid4().hex
        self._wb_records.clear()
        self._wb_matches.clear()
        self._wb_search_dirty = True
        self.wb_note_preview.set("已开始新会话；旧书签和注释保留在“全部会话”")
        self._wb_update_search_status()
        self._wb_refresh_notes()

    def _wb_export_records(self) -> list[dict]:
        return [self.wb_annotations.enrich(self.capture_session_id, record) for record in self.capture_records]
