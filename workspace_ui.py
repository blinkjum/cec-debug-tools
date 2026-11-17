"""Task-oriented main pages and persistent, native diagnostics/settings windows."""
import tkinter as tk
from tkinter import ttk


class WorkspaceUIMixin:
    def _workspace_open_capture(self) -> None:
        self.notebook.select(self.capture_tab_widget)
        self._files_choose_open()

    def _workspace_create(self, outer) -> None:
        self.notebook = ttk.Notebook(outer)
        self.notebook.pack(fill="both", expand=True)
        pages = [("capture_tab_widget", "报文监视"), ("device_tab_widget", "发包与模拟"),
                 ("equipment_tab_widget", "设备与拓扑"), ("tests_tab_widget", "测试与报告")]
        for attr, title in pages:
            frame = ttk.Frame(self.notebook, padding=6)
            setattr(self, attr, frame)
            self.notebook.add(frame, text=title)

        self.device_notebook = ttk.Notebook(self.device_tab_widget)
        self.device_notebook.pack(fill="both", expand=True)
        self.raw_tab_widget = ttk.Frame(self.device_notebook, padding=6)
        self.simulation_page = ttk.Frame(self.device_notebook, padding=6)
        self.occupancy_page = ttk.Frame(self.device_notebook, padding=6)
        for frame, title in ((self.raw_tab_widget, "手动发包"), (self.simulation_page, "角色自动应答"),
                             (self.occupancy_page, "逻辑地址占用")):
            self.device_notebook.add(frame, text=title)
        self._create_raw_tab(self.raw_tab_widget)
        self._create_simulation_tab(self.simulation_page)
        self._create_occupancy_tab(self.occupancy_page)
        self._create_capture_tab(self.capture_tab_widget)
        self._workspace_create_equipment(self.equipment_tab_widget)

        self.test_notebook = ttk.Notebook(self.tests_tab_widget)
        self.test_notebook.pack(fill="both", expand=True)
        self.cts_page = ttk.Frame(self.test_notebook, padding=6)
        self.scenario_page = ttk.Frame(self.test_notebook, padding=6)
        self.test_notebook.add(self.cts_page, text="CTS 项目与报告")
        self.test_notebook.add(self.scenario_page, text="场景与命令脚本")
        self._create_cts_tab(self.cts_page)
        self._create_scenario_tab(self.scenario_page)
        self._workspace_create_diagnostics()
        self._connection_settings_window = None
        self._workspace_device_cache = None
        self.notebook.bind("<<NotebookTabChanged>>", lambda _e: self._workspace_refresh_equipment(), add="+")
        for variable in (self.status_var, self.monitor_state_var, self.occupancy_state_var):
            variable.trace_add("write", lambda *_: self._workspace_refresh_equipment())
        self._workspace_refresh_equipment()
        self.notebook.select(self.capture_tab_widget)

    def _workspace_create_equipment(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        info = ttk.LabelFrame(parent, text="适配器与本机身份", padding=8)
        info.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        info.columnconfigure(0, weight=1)
        self.equipment_summary_var = tk.StringVar()
        summary = ttk.Label(info, textvariable=self.equipment_summary_var, wraplength=1000)
        summary.grid(row=0, column=0, sticky="ew")
        info.bind("<Configure>", lambda event: summary.configure(wraplength=max(300, event.width-145)))
        ttk.Button(info, text="连接设置…", command=self._workspace_show_connection_settings).grid(row=0, column=1, padx=(10, 0))
        self.topology_page = ttk.Frame(parent)
        self.topology_page.grid(row=1, column=0, sticky="nsew")
        self._create_topology_tab(self.topology_page)

    def _workspace_refresh_equipment(self) -> None:
        if not hasattr(self, "equipment_summary_var"):
            return
        connected = self.backend.is_open
        port = (self.backend.open_port or self.port_var.get()) if connected else self.port_var.get()
        dll = str(self.backend.dll_path) if connected and self.backend.dll_path else self.cec_path_var.get()
        config = self.connection_kwargs if connected else {}
        role = str(config.get("device_types", config.get("role", self.types_var.get())))
        osd = str(config.get("device_name", self.osd_name_var.get()))
        vendor = config.get("vendor_id", self.vendor_id_var.get())
        if isinstance(vendor, int):
            vendor = f"{vendor:06X}"
        cache = (connected, port, role, osd, str(vendor), dll,
                 self.monitor_state_var.get(), self.occupancy_state_var.get())
        if cache == getattr(self, "_workspace_device_cache", None):
            return
        self._workspace_device_cache = cache
        heading = "当前连接配置" if connected else "待连接配置"
        self.equipment_summary_var.set(
            f"{heading}：{port}  ·  角色 {role}  ·  OSD {osd}  ·  Vendor {vendor or '默认'}\n"
            f"{self.monitor_state_var.get()}  ·  {self.occupancy_state_var.get()}\n"
            f"DLL：{dll or '自动查找'}")

    def _workspace_show_connection_settings(self) -> None:
        window = self._connection_settings_window
        if window is None or not window.winfo_exists():
            window = self._connection_settings_window = tk.Toplevel(self)
            window.title("连接设置")
            window.transient(self)
            window.geometry("1000x570")
            window.minsize(820, 520)
            window.protocol("WM_DELETE_WINDOW", window.withdraw)
            body = ttk.Frame(window, padding=10)
            body.pack(fill="both", expand=True)
            self._create_connection_settings(body)
            buttons = ttk.Frame(body)
            buttons.pack(fill="x", pady=(8, 0))
            ttk.Label(buttons, text="连接参数在下一次连接时生效；设备连接统一使用顶部按钮。", foreground="#555555").pack(side="left")
            ttk.Button(buttons, text="保存并关闭", command=self._workspace_save_connection_settings).pack(side="right")
        window.deiconify()
        window.lift()

    def _workspace_save_connection_settings(self) -> None:
        self._save_settings()
        self._workspace_refresh_equipment()
        self._connection_settings_window.withdraw()

    def _workspace_create_diagnostics(self) -> None:
        window = self.diagnostics_window = tk.Toplevel(self)
        window.withdraw()
        window.title("libCEC 原生日志")
        window.transient(self)
        window.geometry("1000x760")
        window.minsize(700, 450)
        window.protocol("WM_DELETE_WINDOW", window.withdraw)
        body = ttk.Frame(window, padding=8)
        body.pack(fill="both", expand=True)
        self.console_page = ttk.Frame(body, padding=(0, 0, 0, 6))
        self._create_command_tab(self.console_page)
        self._native_log_frame = ttk.Frame(body)
        self._native_log_frame.pack(fill="both", expand=True)
        toolbar = ttk.Frame(self._native_log_frame)
        toolbar.pack(fill="x", pady=(0, 5))
        ttk.Label(toolbar, text="所有页面共用的 libCEC 原生日志与报文输出").pack(side="left")
        ttk.Button(toolbar, text="保存输出", command=self._save_output).pack(side="right")
        ttk.Button(toolbar, text="清空输出", command=self._clear_output).pack(side="right", padx=4)
        frame = ttk.Frame(self._native_log_frame)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.output = tk.Text(frame, wrap="none", undo=False, height=12, font=("Consolas", 10),
                              background="#101418", foreground="#d7e3ea", insertbackground="white")
        self.output.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(frame, orient="vertical", command=self.output.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=self.output.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.output.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)

    def _workspace_show_diagnostics(self, console=False) -> None:
        if console:
            if not self.console_page.winfo_manager():
                self.console_page.pack(fill="both", expand=True, before=self._native_log_frame)
        else:
            self.console_page.pack_forget()
        self.diagnostics_window.title("API 控制台与原生日志" if console else "libCEC 原生日志")
        self.diagnostics_window.deiconify()
        self.diagnostics_window.lift()

    def _workspace_diagnostic_action(self, action) -> None:
        self._workspace_show_diagnostics()
        action()
