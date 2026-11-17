"""Offline portable-build check. Never open a serial port or send CEC frames."""
import ctypes
import hashlib
import json
from pathlib import Path
import sys
import traceback
import tempfile
import time

from runtime_paths import application_directory


def run_self_test(gui_class, report_path: Path) -> int:
    report = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)),
              "application_directory": str(application_directory()),
              "working_directory": str(Path.cwd()), "adapter_opened": False}
    app = None
    connection = None
    try:
        app = gui_class(read_settings=False, start_hidden=True)
        app.log_file_var.set("")
        app.short_log_file_var.set("")
        app.update_idletasks()
        backend = app.backend
        backend._load()
        dll_path = Path(backend.dll_path).resolve()
        report["dll"] = str(dll_path)
        report["dll_sha256"] = hashlib.sha256(dll_path.read_bytes()).hexdigest()
        report["test_ack_api"] = bool(backend._f_test_ack_set and backend._f_test_ack_get)
        if not report["test_ack_api"]:
            raise RuntimeError("Bundled DLL is missing the test ACK-mask API")
        config = backend._new_configuration(monitor_only=True, physical_address=0x1000)
        connection = backend._f_initialise(ctypes.byref(config))
        if not connection:
            raise RuntimeError("Native libCEC initialization failed")
        report["libcec_info"] = backend._f_lib_info(connection).decode("utf-8", errors="replace")
        backend._f_destroy(connection)
        connection = None
        report["oui"] = str(app.oui_lookup.path)
        report["oui_entries"] = len(app.oui_lookup.entries)
        report["sony_vendor"] = app.oui_lookup.describe(0x080046)
        if report["oui_entries"] < 30000 or "sony" not in report["sony_vendor"].lower():
            raise RuntimeError("Bundled OUI table did not load correctly")
        backend._create_callbacks()
        command = backend.frame_to_command([0x05, 0x90, 0])
        backend._callbacks.rawFrame(None, ctypes.byref(command), 0, 1, 100)
        app._poll_output()
        app.update_idletasks()
        report["capture_test_frame"] = app.capture_records[0]["frame"]
        report["capture_rows"] = len(app.capture_tree.get_children())
        report["cts_cases"] = len(app.cts_cases)
        report["gui_errors"] = app.gui_error_count
        if report["capture_rows"] != 1 or app.gui_error_count:
            raise RuntimeError("Packaged GUI capture smoke check failed")
        app.wb_query_vars["source"].set("0")
        app.wb_query_vars["destination"].set("5")
        app.wb_query_vars["opcode"].set("90")
        if not app._wb_apply_query() or app._wb_matches != [1]:
            raise RuntimeError("Packaged compound query check failed")
        command = backend.frame_to_command([0x50, 0x7A, 0x20])
        backend._callbacks.rawFrame(None, ctypes.byref(command), 0, 1, 200)
        app._poll_output()
        if app._wb_applied_mode != "FIND" or len(app.capture_tree.get_children()) != 2:
            raise RuntimeError("Packaged search did not retain unmatched context")
        app._wb_navigate(1)
        if app._capture_selected_sequence() != 1 or not app._capture_follow_suspended:
            raise RuntimeError("Packaged search navigation did not hold its position")
        app.wb_only_matches_button.invoke()
        if len(app.capture_tree.get_children()) != 1 or app._wb_applied_mode != "FILTER":
            raise RuntimeError("Packaged optional match filter failed")
        app.wb_only_matches_button.invoke()
        if len(app.capture_tree.get_children()) != 2:
            raise RuntimeError("Packaged optional match filter did not restore context")
        report["search_context_navigation"] = True
        app.wb_annotations.set(app.capture_session_id, app.capture_records[0], bookmark=True,
                               note="offline check", tags=["selftest"])
        if app._wb_export_records()[0]["note"] != "offline check":
            raise RuntimeError("Packaged annotation check failed")
        report["workbench_commands"] = len(app.wb_commands)
        report["workbench_search_matches"] = len(app._wb_matches)
        report["workbench_annotation"] = True
        if set(app.wb_condition_buttons) != {"source", "destination", "opcode"}:
            raise RuntimeError("Packaged condition chips are incomplete")
        app.wb_condition_buttons["source"].invoke()
        if app.wb_query_vars["source"].get() or app.wb_query_vars["opcode"].get() != "90" or app._wb_matches != [1]:
            raise RuntimeError("Packaged condition removal changed unrelated criteria")
        report["removable_conditions"] = True
        app.capture_filter_var.set("Report Power Status")
        app._wb_navigate(1)
        if "全文命中：Opcode" not in app.wb_match_reason.get():
            raise RuntimeError("Packaged text search did not explain the matching field")
        report["match_reasons"] = True
        app.capture_filter_var.set("")
        app._wb_apply_query()
        app._layout_preset("COMPACT")
        if "opcode_explanation" in app.capture_tree.display_columns:
            raise RuntimeError("Packaged compact column preset failed")
        layout = app._layout_preferences()
        layout.update(visible=["frame", "opcode", "status"],
                      order=["frame", *[c for c in layout["order"] if c != "frame"]], auto_fit=False)
        layout["widths"]["frame"] = 321
        app._layout_apply(json.loads(json.dumps(layout)))
        if app.capture_tree.display_columns != ("frame", "opcode", "status") or app.capture_tree.column("frame", "width") != 321:
            raise RuntimeError("Packaged custom column layout failed")
        app._layout_preset("FULL")
        report["column_layout"] = True
        app._wb_toggle("Opcode 关注", open_only=True)
        app.capture_highlight_vars[11].set("90")
        app.capture_highlight_color_vars[11].set("#492572")
        tag = app._capture_highlight_tag(0x90)
        if tag != "capture_opcode_highlight_12" or app.capture_tree._tags[tag]["background"] != "#492572":
            raise RuntimeError("Packaged custom Opcode highlight check failed")
        item = app.capture_tree.get_children()[0]
        if tag not in app.capture_tree.item(item, "tags"):
            raise RuntimeError("Packaged Opcode cell was not recolored")
        app._wb_toggle("Opcode 关注")
        app.update_idletasks()
        if app.wb_panel.winfo_manager() or app.gui_error_count:
            raise RuntimeError("Packaged highlight panel did not collapse correctly")
        report["gui_errors"] = app.gui_error_count
        report["highlight_rules"] = len(app.capture_highlight_vars)
        report["highlight_custom_color"] = app.capture_tree._tags[tag]["background"]
        previous_page = app.notebook.select()
        dialog = app._capture_show_details(record=app.capture_records[0])
        if dialog is None or app.notebook.select() != previous_page:
            raise RuntimeError("Packaged capture details changed the main page")
        content = dialog.capture_detail_text.get("1.0", "end-1c")
        if "05:90:00" not in content or "Report Power Status" not in content:
            raise RuntimeError("Packaged capture details are incomplete")
        dialog.destroy()
        app._capture_reuse_frame(app.capture_records[0])
        if app.raw_frame_var.get() != "05:90:00" or app.raw_params_var.get() != "00":
            raise RuntimeError("Packaged Raw load did not populate the frame")
        report["capture_details"] = True
        report["raw_frame_loaded"] = app.raw_frame_var.get()
        from cec_capture_tools import read_capture_file
        with tempfile.TemporaryDirectory(prefix="offline-check-", dir=application_directory()) as scratch:
            folder = Path(scratch)
            app.settings_path = folder / "settings.json"
            app.wb_annotations_path = folder / "bookmarks.json"
            packet_file = folder / "离线 抓包.json"
            packet_file.write_text(json.dumps([{"seq": 33, "frame": "50:90", "note": "离线自检"},
                                               {"seq": 34, "frame": "05", "timestamp_ms": 123}]), encoding="utf-8")
            app._files_load(packet_file)
            deadline = time.monotonic() + 15
            while app._files_load_busy and time.monotonic() < deadline:
                app.update()
                time.sleep(0.005)
            if app._files_mode != "OFFLINE" or app._files_errors != [1] or app.gui_error_count:
                raise RuntimeError("Packaged offline import or length check failed")
            exported = folder / "提取.csv"
            app._files_start_export(exported, app._files_extract_records("ALL"), "CSV", ["seq", "frame", "note"])
            if not app.log_writer.flush(wait=True, timeout=15) or len(read_capture_file(exported).records) != 2:
                raise RuntimeError("Packaged background batch export failed")
            app._files_show_live()
            if len(app.capture_records) != 2 or app.capture_records[0]["frame"] != "05:90:00":
                raise RuntimeError("Packaged offline view did not retain live capture")
            report["offline_import"] = True
            report["length_check"] = True
            report["batch_export"] = True
        report["ok"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if app is not None:
            if connection:
                app.backend._f_destroy(connection)
            app.log_writer.close()
            app.destroy()
        report_path = report_path.resolve()
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
