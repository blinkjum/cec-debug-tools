"""Native column menus and a display-layout editor for the capture table."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from capture_layout import CAPTURE_COLUMNS, CAPTURE_HEADINGS, MINIMUM_WIDTHS, normalize_layout, preset_layout


class CaptureLayoutMixin:
    def _layout_init(self, read_settings: bool) -> None:
        prefs = self.settings.get("workbench", {})
        self._layout_state = normalize_layout(prefs.get("capture_layout") if isinstance(prefs, dict) else None)
        self._layout_save_job = None
        self._layout_persist = read_settings
        self._layout_dialog = None

    def _layout_preferences(self) -> dict:
        state = normalize_layout(self._layout_state)
        if hasattr(self, "capture_tree"):
            state.update(visible=list(self.capture_tree.display_columns),
                         widths=dict(self.capture_tree._preferred_widths),
                         auto_fit=self.capture_tree.auto_fit_columns)
        return normalize_layout(state)

    def _layout_install(self) -> None:
        state = self._layout_state
        self.capture_tree.set_column_layout(state["widths"], MINIMUM_WIDTHS, auto_fit=state["auto_fit"])
        self.capture_tree.set_display_columns(c for c in state["order"] if c in state["visible"])

    def _layout_apply(self, state: dict) -> None:
        self._layout_state = normalize_layout(state)
        self._layout_install()
        self._layout_changed()

    def _layout_changed(self) -> None:
        self._layout_state = self._layout_preferences()
        if self._layout_persist:
            if self._layout_save_job is not None:
                self.after_cancel(self._layout_save_job)
            self._layout_save_job = self.after(300, self._layout_save)

    def _layout_save(self) -> None:
        self._layout_save_job = None
        self._save_settings()

    def _layout_preset(self, name: str) -> None:
        self._layout_apply(preset_layout(name))

    def _layout_toggle_column(self, column: str) -> None:
        state = self._layout_preferences()
        if column in state["visible"]:
            if len(state["visible"]) == 1:
                self.status_var.set("至少保留一列；可从“列布局”恢复完整布局")
                return
            state["visible"].remove(column)
        else:
            state["visible"].append(column)
        self._layout_apply(state)

    def _layout_show_menu(self, event=None) -> str:
        old = getattr(self, "_layout_menu", None)
        if old is not None:
            old.destroy()
        menu = self._layout_menu = tk.Menu(self, tearoff=False)
        menu.add_command(label="精简布局", command=lambda: self._layout_preset("COMPACT"))
        menu.add_command(label="完整布局", command=lambda: self._layout_preset("FULL"))
        menu.add_command(label="自定义列、顺序和宽度…", command=self._layout_edit)
        menu.add_separator()
        self._layout_menu_vars = []
        state = self._layout_preferences()
        for column in state["order"]:
            var = tk.BooleanVar(value=column in state["visible"])
            self._layout_menu_vars.append(var)
            menu.add_checkbutton(label=CAPTURE_HEADINGS[column], variable=var,
                                 command=lambda c=column: self._layout_toggle_column(c))
        x = event.x_root if event else self.capture_layout_button.winfo_rootx()
        y = event.y_root if event else self.capture_layout_button.winfo_rooty() + self.capture_layout_button.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()
        return "break"

    def _layout_edit(self) -> None:
        dialog = self._layout_dialog
        if dialog is not None and dialog.winfo_exists():
            dialog.lift()
            return
        self._layout_dialog = CaptureLayoutDialog(self)


class CaptureLayoutDialog(tk.Toplevel):
    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.owner = owner
        self.state_data = owner._layout_preferences()
        self.selected_column = None
        self.visible_var = tk.BooleanVar(self)
        self.width_var = tk.StringVar(self)
        self.error_var = tk.StringVar(self)
        self.title("报文列表列布局")
        self.transient(owner)
        self.geometry("560x570")
        self.minsize(460, 440)
        body = ttk.Frame(self, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)
        top = ttk.Frame(body)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(top, text="精简布局", command=lambda: self.use_preset("COMPACT")).pack(side="left")
        ttk.Button(top, text="完整布局", command=lambda: self.use_preset("FULL")).pack(side="left", padx=4)
        ttk.Label(top, text="修改后点击应用；可继续接收报文").pack(side="left", padx=5)
        table_frame = ttk.Frame(body)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(table_frame, columns=("visible", "title", "width"), show="headings", selectmode="browse")
        for column, text, width in (("visible", "显示", 55), ("title", "列名称", 235), ("width", "宽度（px）", 90)):
            self.tree.heading(column, text=text)
            self.tree.column(column, width=width, minwidth=45, stretch=column == "title")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", self.toggle_selected)
        self.tree.bind("<space>", self.toggle_selected)
        editor = ttk.Frame(body)
        editor.grid(row=2, column=0, sticky="ew", pady=8)
        ttk.Checkbutton(editor, text="显示选中列", variable=self.visible_var, command=self.store_editor).pack(side="left")
        ttk.Label(editor, text="宽度").pack(side="left", padx=(8, 3))
        self.width_entry = ttk.Spinbox(editor, from_=30, to=1600, width=7, textvariable=self.width_var)
        self.width_entry.pack(side="left")
        self.width_entry.bind("<Return>", lambda _e: self.store_editor())
        ttk.Button(editor, text="上移", width=5, command=lambda: self.move(-1)).pack(side="left", padx=(12, 3))
        ttk.Button(editor, text="下移", width=5, command=lambda: self.move(1)).pack(side="left")
        ttk.Label(body, textvariable=self.error_var, foreground="#B71C1C", wraplength=510).grid(row=3, column=0, sticky="w")
        ttk.Label(body, text="隐藏列不删除报文数据，完整复制和导出仍包含原字段。\n手动设置宽度后保留该宽度；切换预设或重置列宽可恢复自动适配。",
                  foreground="#555555", wraplength=510).grid(row=4, column=0, sticky="w", pady=6)
        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, sticky="ew")
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="应用并保存", command=self.apply).pack(side="right", padx=5)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.refresh()

    def refresh(self) -> None:
        self.selected_column = None
        self.tree.delete(*self.tree.get_children())
        for column in self.state_data["order"]:
            self.tree.insert("", "end", iid=column, values=self.values(column))
        self.tree.selection_set(self.state_data["order"][0])
        self.on_select()

    def values(self, column: str) -> tuple:
        return ("✓" if column in self.state_data["visible"] else "", CAPTURE_HEADINGS[column], self.state_data["widths"][column])

    def store_editor(self) -> bool:
        column = self.selected_column
        if column is None:
            return True
        try:
            width = int(self.width_var.get())
            if not MINIMUM_WIDTHS[column] <= width <= 1600:
                raise ValueError()
        except ValueError:
            self.error_var.set(f"{CAPTURE_HEADINGS[column]} 宽度应为 {MINIMUM_WIDTHS[column]}–1600 px")
            return False
        if width != self.state_data["widths"][column]:
            self.state_data["auto_fit"] = False
        self.state_data["widths"][column] = width
        visible = self.state_data["visible"]
        if self.visible_var.get() and column not in visible:
            visible.append(column)
        elif not self.visible_var.get() and column in visible:
            visible.remove(column)
        self.tree.item(column, values=self.values(column))
        self.error_var.set("")
        return True

    def on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection or selection[0] == self.selected_column:
            return
        if not self.store_editor():
            self.tree.selection_set(self.selected_column)
            return
        self.selected_column = selection[0]
        self.visible_var.set(self.selected_column in self.state_data["visible"])
        self.width_var.set(str(self.state_data["widths"][self.selected_column]))

    def toggle_selected(self, _event=None) -> str:
        self.on_select()
        self.visible_var.set(not self.visible_var.get())
        self.store_editor()
        return "break"

    def move(self, direction: int) -> None:
        if not self.store_editor() or self.selected_column is None:
            return
        order = self.state_data["order"]
        index = order.index(self.selected_column)
        target = max(0, min(len(order)-1, index + direction))
        order[index], order[target] = order[target], order[index]
        self.tree.move(self.selected_column, "", target)
        self.tree.see(self.selected_column)

    def use_preset(self, name: str) -> None:
        self.state_data = preset_layout(name)
        self.error_var.set("")
        self.refresh()

    def apply(self) -> bool:
        if not self.store_editor():
            return False
        if not self.state_data["visible"]:
            self.error_var.set("至少保留一列")
            return False
        self.owner._layout_apply(self.state_data)
        self.error_var.set("已应用；列布局随工具配置保存")
        return True
