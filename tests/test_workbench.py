"""Compound query, native GUI integration and persistence without adapter I/O."""
import importlib.util
import json
from pathlib import Path
import queue
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, PropertyMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cec_workbench import AnnotationLibrary, CaptureQuery, QuickCommand, default_quick_commands
from cec_workbench import HIGHLIGHT_PALETTE, highlight_settings, highlight_style
from libcec_backend import BackendResult


def record(seq=1, **overrides):
    return dict({"seq": seq, "source": "0", "destination": "5", "opcode": 0x90,
                 "params": "01:AC:2F", "frame": "05:90:01:AC:2F", "status": "ACK",
                 "opcode_text": "0x90 Report Power Status", "direction": "RX", "time": "12:00:00.100"}, **overrides)


class WorkbenchModelTests(unittest.TestCase):
    def test_empty_conditions_and_normalized_summary(self):
        self.assertFalse(CaptureQuery.from_dict({"source": "*", "opcode": "*", "params": "*", "status": "ALL", "text": "  "}).has_conditions)
        query = CaptureQuery.from_dict({"source": "5,0", "opcode": "90,POLL", "params": "a?:??", "bookmarks_only": True})
        self.assertTrue(query.has_conditions)
        self.assertEqual(query.describe(), "源LA=0,5；Opcode=90,POLL；参数=A?:??；仅书签")

    def test_highlight_migration_custom_colors_and_invalid_saved_rules(self):
        migrated = highlight_settings({"capture_highlight_opcodes": ["72", "C2", "90", "C1"]})
        self.assertEqual(len(migrated), 12)
        self.assertEqual([r["opcode"] for r in migrated[:4]], ["72", "C2", "90", "C1"])
        self.assertEqual([r["color"] for r in migrated[:4]], [r[0] for r in HIGHLIGHT_PALETTE[:4]])
        current = highlight_settings({"capture_highlight_rules": [
            {"opcode": "0xff", "color": "#123abc"}, {"opcode": "GG", "color": "bad"}, None],
            "capture_highlight_opcodes": ["90"]})
        self.assertEqual(current[0], {"opcode": "FF", "color": "#123ABC"})
        self.assertEqual(current[1], {"opcode": "", "color": HIGHLIGHT_PALETTE[1][0]})
        self.assertEqual(highlight_style("#000000")["foreground"], "#FFFFFF")
        self.assertEqual(highlight_style("#FFFFFF")["foreground"], "#000000")
        self.assertEqual(len({color for color, _ in HIGHLIGHT_PALETTE}), 16)

    def test_compound_values_mask_and_ack_polarity_labels(self):
        query = CaptureQuery.from_dict({"source": "0, 4", "destination": "5", "opcode": "8F,90",
                                        "params": "01:A?:??", "status": "ACK"})
        self.assertTrue(query.matches(record()))
        self.assertFalse(query.matches(record(source="5")))
        self.assertFalse(query.matches(record(status="NACK")))
        self.assertTrue(query.matches(record(status="ACK（广播通过）")))
        self.assertFalse(query.matches(record(params="01:BC:2F")))
        self.assertFalse(query.matches(record(params="01:AC")))
        self.assertTrue(CaptureQuery.from_dict({"status": "NACK"}).matches(record(status="NACK（广播拒绝）")))

    def test_poll_unknown_status_and_note_search(self):
        query = CaptureQuery.from_dict({"opcode": "POLL", "status": "UNKNOWN", "text": "拔插", "bookmarks_only": True})
        self.assertTrue(query.matches(record(opcode=None, status="—"), {"bookmark": True, "tags": ["拔插HDMI"]}))
        self.assertFalse(query.matches(record(opcode=None, status="—"), {"bookmark": False, "note": "拔插"}))
        for bad in ({"source": "10"}, {"opcode": "GG"}, {"params": "X?"}, {"status": "INVALID"}):
            with self.assertRaises(ValueError):
                CaptureQuery.from_dict(bad)

    def test_quick_command_boundaries_and_release_pair(self):
        command = QuickCommand.from_dict({"name": "音量加", "source": "0x0", "destination": "5",
                                          "opcode": "44", "params": "41", "release_key": True, "shortcut": "f5"})
        self.assertEqual(command.frames(), ["05:44:41", "05:45"])
        self.assertEqual(command.shortcut, "F5")
        for change in ({"params": " ".join(["01"]*15)}, {"source": "10"}, {"shortcut": "Control-c"}, {"opcode": "36"}):
            with self.assertRaises(ValueError):
                QuickCommand.from_dict(dict(command.to_dict(), **change))
        self.assertEqual(len(default_quick_commands()), 13)

    def test_annotations_survive_sequence_reuse_and_snapshot_mutation(self):
        lib = AnnotationLibrary()
        item = record()
        lib.set("session-a", item, bookmark=True, note="触发待机", tags=["电源", "电源", " ARC "])
        item["frame"] = "changed"
        reloaded = AnnotationLibrary(json.loads(json.dumps(lib.payload())))
        self.assertEqual(reloaded.get("session-a", 1)["record"]["frame"], "05:90:01:AC:2F")
        self.assertEqual(reloaded.get("session-a", 1)["tags"], ["电源", "ARC"])
        self.assertEqual(reloaded.get("session-b", 1), {})
        self.assertFalse(reloaded.enrich("session-b", record())["bookmark"])


class WorkbenchGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("workbench_gui", ROOT / "cec-client-gui.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        scratch = ROOT / "work/ui-workbench-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=scratch)
        self.gui = self.module.CECClientGUI(read_settings=False, start_hidden=True)
        self.gui.settings_path = Path(self.folder.name)/"settings.json"
        self.gui.wb_annotations_path = Path(self.folder.name)/"bookmarks.json"

    def tearDown(self):
        self.gui.update_idletasks()
        self.gui.log_writer.close()
        self.gui.destroy()
        self.folder.cleanup()

    def frame(self, frame, index=1, ack=0):
        self.gui._handle_backend_event({"kind": "raw_frame", "frame": frame, "direction": "RX",
                                       "raw_status_text": "INCOMING", "ack": ack, "raw_timestamp": index*100})

    def selected_sequence(self):
        return self.gui._capture_selected_sequence()

    def test_empty_query_disables_navigation_and_does_not_select_all_frames(self):
        g = self.gui
        self.frame([0x05, 0x90, 0])
        self.frame([0x50, 0x7A, 0x20], 2)
        self.assertEqual(g.wb_mode_var.get(), "FIND")
        self.assertEqual(g._wb_matches, [])
        g._wb_navigate(1)
        self.assertIsNone(self.selected_sequence())
        self.assertTrue(g.wb_next_button.instate(["disabled"]))
        self.assertTrue(g.wb_previous_button.instate(["disabled"]))
        self.assertIn("未设置", g.wb_search_status.get())
        g.wb_query_vars["opcode"].set("*")
        g._wb_navigate(-1)
        self.assertEqual(g._wb_matches, [])
        self.assertIsNone(self.selected_sequence())

    def test_find_preserves_context_and_row_ids_with_optional_filter(self):
        g = self.gui
        for i, frame in enumerate(([0x05, 0x8F], [0x50, 0x90, 0], [0x05, 0x44, 0x41], [0x50, 0x90, 1]), 1):
            self.frame(frame, i)
        children = g.capture_tree.get_children()
        g.wb_query_vars["opcode"].set("90")
        with patch.object(g.capture_tree, "delete") as delete:
            g._wb_apply_query()
            g._wb_navigate(1)
            g._wb_navigate(1)
            delete.assert_not_called()
        self.assertEqual(g.capture_tree.get_children(), children)
        self.assertEqual(self.selected_sequence(), 4)
        self.assertEqual(g._wb_matches, [2, 4])
        self.assertIn("Opcode=90", g.wb_search_summary.get())
        self.assertIn("第 2 / 2", g.wb_search_status.get())
        g.wb_only_matches_button.invoke()
        self.assertEqual(g._wb_applied_mode, "FILTER")
        self.assertEqual([g.capture_tree.item(item, "values")[0] for item in g.capture_tree.get_children()], [2, 4])
        g.wb_only_matches_button.invoke()
        self.assertEqual(g._wb_applied_mode, "FIND")
        self.assertEqual(len(g.capture_tree.get_children()), 4)
        self.assertEqual(self.selected_sequence(), 4)
        g._wb_clear_query()
        self.assertFalse(g.wb_only_matches_var.get())
        self.assertTrue(g.wb_next_button.instate(["disabled"]))

    def test_query_fields_auto_apply_and_invalid_draft_disables_navigation(self):
        g = self.gui
        self.frame([0x05, 0x90, 0])
        self.frame([0x50, 0x7A, 0x20], 2)
        g.wb_query_vars["opcode"].set("90")
        g.after(260, g.quit)
        g.mainloop()
        self.assertEqual(g._wb_matches, [1])
        self.assertFalse(g.wb_next_button.instate(["disabled"]))
        g.wb_query_vars["opcode"].set("GG")
        self.assertFalse(g._wb_apply_query())
        g._wb_navigate(1)
        self.assertIsNone(self.selected_sequence())
        self.assertTrue(g.wb_next_button.instate(["disabled"]))
        self.frame([0x05, 0x90, 1], 3)
        self.assertEqual(g._wb_matches, [1, 3])
        self.assertEqual(len(g.capture_tree.get_children()), 3)
        g.wb_query_vars["opcode"].set("7A")
        g._wb_apply_query()
        self.assertEqual(g._wb_matches, [2])
        self.assertFalse(g.wb_next_button.instate(["disabled"]))

    def test_same_opcode_action_uses_right_clicked_row_in_multi_selection(self):
        g = self.gui
        self.frame([0x05, 0x44, 0x41])
        self.frame([0x50, 0x90, 1], 2)
        first, second = g.capture_tree.get_children()
        g.capture_tree.selection_set((first, second))
        pointer = SimpleNamespace(x=10, y=5, x_root=10, y_root=10)
        with patch.object(g.capture_tree, "identify_row", return_value=second), patch.object(self.module.tk.Menu, "tk_popup"):
            g._capture_context_menu(pointer)
        menu, action = next((widget, i) for widget in g.winfo_children() if isinstance(widget, self.module.tk.Menu)
                            and widget.index("end") is not None for i in range(widget.index("end") + 1)
                            if widget.type(i) == "command" and widget.entrycget(i, "label") == "查找相同 Opcode")
        with patch.object(g.backend, "transmit") as transmit:
            menu.invoke(action)
            transmit.assert_not_called()
        self.assertEqual(g.wb_query_vars["opcode"].get(), "90")
        self.assertEqual(self.selected_sequence(), 2)
        self.assertEqual(len(g.capture_tree.get_children()), 2)
        self.assertEqual(g._wb_matches, [2])
        self.frame([0x05], 3)
        g._wb_find_same_opcode(g.capture_records[-1])
        self.assertTrue(g.capture_show_poll_var.get())
        self.assertEqual(g._wb_matches, [3])

    def test_navigation_centers_hit_and_holds_view_until_explicit_scroll_or_latest(self):
        g = self.gui
        g.geometry("1280x860")
        g.notebook.select(g.capture_tab_widget)
        g.deiconify()
        for i in range(70):
            self.frame([0x05, 0x90 if i == 35 else 0x8F, 0] if i == 35 else [0x05, 0x8F], i)
        g.update()
        g.wb_query_vars["opcode"].set("90")
        g._wb_navigate(1)
        g.update_idletasks()
        canvas = g.capture_tree.body_canvas
        row = g.capture_tree._order.index(g.capture_tree.selection()[0])
        top, bottom = g.capture_tree._row_offsets[row:row+2]
        self.assertLess(abs((top+bottom)/2 - canvas.canvasy(0) - canvas.winfo_height()/2), 5)
        self.assertTrue(g._capture_follow_suspended)
        before = g.capture_tree.yview()[0] * g.capture_tree._row_offsets[-1]
        for i in range(10):
            self.frame([0x50, 0x90, 0], 100+i)
        after = g.capture_tree.yview()[0] * g.capture_tree._row_offsets[-1]
        self.assertAlmostEqual(before, after, delta=2)
        self.assertEqual(self.selected_sequence(), 36)
        g._wb_jump(80)
        self.assertTrue(g.capture_tree.is_at_bottom())
        with patch.object(g.capture_tree, "see_end") as follow:
            self.frame([0x50, 0x90, 0], 200)
            follow.assert_not_called()
        g._capture_user_yview("moveto", 1)
        self.assertFalse(g._capture_follow_suspended)
        self.frame([0x50, 0x90, 0], 201)
        self.assertTrue(g.capture_tree.is_at_bottom())
        g._wb_jump(36)
        g._wb_latest()
        self.assertFalse(g._capture_follow_suspended)
        self.assertTrue(g.capture_tree.is_at_bottom())
        self.assertEqual(g.gui_error_count, 0)

    def test_jump_restores_context_around_pinned_old_selection(self):
        g = self.gui
        g.capture_display_limit = 20
        for i in range(60):
            self.frame([0x05, 0x90, 0], i)
        g._wb_jump(5)
        g._refresh_capture_tree(force=True)
        self.assertNotIn(6, [g.capture_tree.item(item, "values")[0] for item in g.capture_tree.get_children()])
        g._wb_jump(5)
        visible = [g.capture_tree.item(item, "values")[0] for item in g.capture_tree.get_children()]
        self.assertEqual(visible, list(range(1, 21)))
        self.assertEqual(self.selected_sequence(), 5)


    def test_double_click_opens_read_only_details_without_leaving_monitor(self):
        g = self.gui
        self.frame([0x05, 0x44, 0x6D])
        self.frame([0x50, 0x90, 0x01], 2)
        target = g.capture_tree.get_children()[0]
        g.capture_tree.selection_set(g.capture_tree.get_children()[1])
        g.capture_context_item = g.capture_tree.get_children()[1]
        g.capture_records[0]["params_explanation"] = "Power On Function；长参数解释。" * 30
        g.wb_annotations.set(g.capture_session_id, g.capture_records[0], bookmark=True,
                             note="双击前的注释", tags=["上电"])
        raw_before = tuple(var.get() for var in (g.raw_frame_var, g.raw_source_var, g.raw_destination_var, g.raw_opcode_var, g.raw_params_var))
        g.notebook.select(g.capture_tab_widget)
        g.geometry("1280x860")
        g.deiconify()
        g.update()
        canvas = g.capture_tree.body_canvas
        with patch.object(g, "_send_command") as send:
            for event, timestamp in (("<ButtonPress-1>", 1000), ("<ButtonRelease-1>", 1050),
                                     ("<ButtonPress-1>", 1100), ("<ButtonRelease-1>", 1150)):
                canvas.event_generate(event, x=20, y=5, time=timestamp)
                g.update()
            g.update_idletasks()
            send.assert_not_called()
        dialogs = [widget for widget in g.winfo_children() if isinstance(widget, self.module.tk.Toplevel)
                   and hasattr(widget, "capture_detail_text")]
        self.assertEqual(len(dialogs), 1, "Native double click must open the detail window")
        dialog = dialogs[0]
        self.assertEqual(g.notebook.select(), str(g.capture_tab_widget))
        self.assertEqual(g.capture_tree.selection(), (target,))
        self.assertEqual(tuple(var.get() for var in (g.raw_frame_var, g.raw_source_var, g.raw_destination_var, g.raw_opcode_var, g.raw_params_var)), raw_before)
        text = dialog.capture_detail_text
        self.assertEqual(str(text["state"]), "disabled")
        self.assertEqual(str(text["wrap"]), "word")
        content = text.get("1.0", "end-1c")
        for expected in ("05:44:6D", "User Control Pressed", "Power On Function", "双击前的注释", "上电", "ACK", "原生回调时间戳"):
            self.assertIn(expected, content)
        self.assertIsNone(g.grab_current(), "Details must not block continuing capture")
        self.frame([0x05, 0x8F], 3)
        self.assertEqual(len(g.capture_records), 3)
        self.assertEqual(text.get("1.0", "end-1c"), content)
        with patch.object(g, "_copy_to_clipboard") as copy:
            buttons = [widget for widget in text.master.winfo_children() if isinstance(widget, self.module.ttk.Frame)][0]
            for button in buttons.winfo_children():
                if button["text"] == "复制全部详情":
                    button.invoke()
            copy.assert_called_once_with(content)
        dialog.destroy()

    def test_context_loads_clicked_snapshot_and_poll_clears_stale_raw_fields(self):
        g = self.gui
        self.frame([0x05, 0x44, 0x6D])
        self.frame([0x50, 0x90, 0x01], 2)
        first, second = g.capture_tree.get_children()
        pointer = SimpleNamespace(x=10, y=5, x_root=10, y_root=10)
        with patch.object(g.capture_tree, "identify_row", return_value=first), patch.object(self.module.tk.Menu, "tk_popup"):
            g._capture_context_menu(pointer)
        menu = next(widget for widget in g.winfo_children() if isinstance(widget, self.module.tk.Menu) and widget.index("end") is not None
                    and any(widget.type(i) == "command" and widget.entrycget(i, "label") == "载入 Raw 注入" for i in range(widget.index("end") + 1)))
        action = next(i for i in range(menu.index("end") + 1) if menu.type(i) == "command" and menu.entrycget(i, "label") == "载入 Raw 注入")
        g.capture_tree.selection_set(second)
        with patch.object(g, "_send_command") as send, patch.object(g.backend, "set_logical_address") as change_la:
            menu.invoke(action)
            self.assertEqual([g.raw_source_var.get(), g.raw_destination_var.get(), g.raw_opcode_var.get(), g.raw_params_var.get(), g.raw_frame_var.get()],
                             ["0", "5", "44", "6D", "05:44:6D"])
            self.assertEqual(g.notebook.select(), str(g.device_tab_widget))
            self.assertEqual(g.device_notebook.select(), str(g.raw_tab_widget))
            self.frame([0x05], 3)
            g._capture_reuse_frame(g.capture_records[-1])
            self.assertEqual(g.raw_frame_var.get(), "05")
            self.assertEqual(g.raw_opcode_var.get(), "")
            self.assertEqual(g.raw_params_var.get(), "")
            send.assert_not_called()
            change_la.assert_not_called()
        self.assertIn("POLL", g._capture_details_content(g.capture_records[-1]))

    def test_double_click_empty_space_does_not_open_selected_row(self):
        g = self.gui
        self.frame([0x05, 0x90, 0])
        g.capture_tree.selection_set(g.capture_tree.get_children()[0])
        windows_before = {str(widget) for widget in g.winfo_children() if isinstance(widget, self.module.tk.Toplevel)}
        with patch.object(g.capture_tree, "identify_row", return_value=""):
            self.assertIsNone(g._capture_show_details(SimpleNamespace(y=10000)))
        self.assertEqual(windows_before, {str(widget) for widget in g.winfo_children() if isinstance(widget, self.module.tk.Toplevel)})

    def test_highlight_panel_expands_hides_and_twelve_rules_match_live_frames(self):
        g = self.gui
        self.assertIn("Opcode 关注", g.wb_pages)
        self.assertEqual(g.wb_panel.winfo_manager(), "")
        g._wb_toggle("Opcode 关注")
        self.assertEqual(g.wb_panel.select(), str(g.wb_pages["Opcode 关注"]))
        self.assertEqual(len(g.wb_highlight_combos), 12)
        self.assertTrue(all(combo.winfo_class() == "TCombobox" for combo in g.wb_highlight_combos))
        for index, var in enumerate(g.capture_highlight_vars):
            var.set(f"{0x90 + index:02X}")
            self.frame([0x05, 0x90 + index], index)
        self.assertEqual(len(g._capture_highlight_opcodes()), 12)
        for index, item in enumerate(g.capture_tree.get_children(), 1):
            self.assertIn(f"capture_opcode_highlight_{index}", g.capture_tree.item(item, "tags"))
        g._wb_toggle("Opcode 关注")
        self.assertEqual(g.wb_panel.winfo_manager(), "")
        self.frame([0x05, 0x9B], 15)
        self.assertIn("capture_opcode_highlight_12", g.capture_tree.item(g.capture_tree.get_children()[-1], "tags"))

    def test_custom_opcode_color_is_cell_only_and_recolor_keeps_rows_and_selection(self):
        g = self.gui
        self.frame([0x05, 0x90, 0])
        self.frame([0x50, 0x7A, 0x28], 2)
        items = g.capture_tree.get_children()
        g.capture_tree.selection_set(items[1])
        g.capture_highlight_vars[11].set("90")
        with patch.object(g.capture_tree, "delete") as delete, patch.object(g.capture_tree, "_measure_row_height") as measure:
            g.capture_highlight_color_vars[11].set("#492572")
            self.assertEqual(g.capture_tree.get_children(), items)
            self.assertEqual(g.capture_tree.selection(), (items[1],))
            delete.assert_not_called()
            measure.assert_not_called()
        g.geometry("1280x860")
        g.notebook.select(g.capture_tab_widget)
        g.deiconify()
        g.update_idletasks()
        canvas = g.capture_tree.body_canvas
        rectangles = [obj for obj in canvas.find_withtag(f"capture_item_{items[0]}") if canvas.type(obj) == "rectangle"]
        fills = [canvas.itemcget(obj, "fill") for obj in rectangles]
        self.assertEqual(fills.count("#492572"), 1)
        self.assertEqual(fills[g.capture_tree.columns.index("opcode")], "#492572")
        self.assertEqual(fills[g.capture_tree.columns.index("frame")], "#FDECEC")
        self.assertEqual(g.capture_tree._tags["capture_opcode_highlight_12"]["foreground"], "#FFFFFF")

    def test_duplicate_priority_invalid_draft_color_picker_and_reset(self):
        g = self.gui
        g.capture_highlight_vars[0].set("90")
        g.capture_highlight_vars[4].set("90")
        self.assertEqual(g._capture_highlight_tag(0x90), "capture_opcode_highlight_1")
        g.capture_highlight_vars[0].set("")
        self.assertEqual(g._capture_highlight_tag(0x90), "capture_opcode_highlight_5")
        g.capture_highlight_vars[2].set("GG")
        g.capture_highlight_color_vars[2].set("bad")
        self.assertIn("请检查", g.wb_highlight_status.get())
        self.frame([0x05, 0x90, 0])
        self.assertEqual(g.gui_error_count, 0)
        with patch("cec_workbench_ui.colorchooser.askcolor", return_value=(None, None)):
            g._wb_pick_highlight_color(4)
        self.assertEqual(g.capture_highlight_color_vars[4].get(), HIGHLIGHT_PALETTE[4][0])
        with patch("cec_workbench_ui.colorchooser.askcolor", return_value=((1, 2, 3), "#010203")):
            g._wb_pick_highlight_color(4)
        self.assertEqual(g.capture_highlight_color_vars[4].get(), "#010203")
        g._wb_reset_highlight_colors()
        self.assertEqual(g.capture_highlight_vars[4].get(), "90")
        self.assertEqual(g.capture_highlight_color_vars[4].get(), HIGHLIGHT_PALETTE[4][0])
        g._wb_clear_highlights()
        self.assertEqual(g._capture_highlight_opcodes(), set())
        self.assertFalse(any(t.startswith("capture_opcode_highlight_") for t in g.capture_tree.item(g.capture_tree.get_children()[0], "tags")))

    def test_highlights_auto_save_once_and_reload_custom_color(self):
        g = self.gui
        g._wb_persist_highlights = True
        with patch.object(g, "_save_settings", wraps=g._save_settings) as save:
            g.capture_highlight_vars[11].set("FF")
            g.capture_highlight_color_vars[11].set("#010203")
            g.after(470, g.quit)
            g.mainloop()
            save.assert_called_once()
        self.assertTrue(g.log_writer.flush(wait=True))
        saved = json.loads(g.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["capture_highlight_rules"][11], {"opcode": "FF", "color": "#010203"})
        g._wb_persist_highlights = False
        g._wb_clear_highlights()
        g.settings = saved
        g._wb_load_highlights()
        self.assertEqual(g._capture_highlight_tag(0xFF), "capture_opcode_highlight_12")
        self.assertEqual(g.capture_highlight_color_vars[11].get(), "#010203")

    def test_compound_search_find_only_and_invalid_draft(self):
        g = self.gui
        self.frame([0x05, 0x90, 0x01])
        self.frame([0x50, 0x7A, 0x20], 2)
        self.frame([0x05, 0x90, 0x02], 3, ack=1)
        for name, value in {"source": "0", "destination": "5", "opcode": "90", "params": "0?", "status": "ACK"}.items():
            g.wb_query_vars[name].set(value)
        g.wb_mode_var.set("FILTER")
        self.assertTrue(g._wb_apply_query())
        self.assertEqual(g._wb_matches, [1])
        self.assertEqual(len(g.capture_tree.get_children()), 1)
        g.wb_mode_var.set("FIND")
        g._wb_apply_query()
        self.assertEqual(len(g.capture_tree.get_children()), 3)
        g._wb_navigate(1)
        self.assertEqual(self.selected_sequence(), 1)
        g.wb_query_vars["source"].set("GG")
        self.assertFalse(g._wb_apply_query())
        self.assertEqual(g._wb_matches, [1])
        self.frame([0x05, 0x90, 0x01], 4)
        self.assertEqual(g._wb_matches, [1, 4], "Invalid draft must not break live capture")

    def test_search_navigates_all_history_and_incoming_frames_keep_selection(self):
        g = self.gui
        g.capture_tree.begin_update()
        for index in range(2201):
            self.frame([0x05, 0x90, 0x00], index)
        g.capture_tree.end_update()
        self.assertEqual(len(g.capture_tree.get_children()), 2000)
        g.wb_query_vars["opcode"].set("90")
        g._wb_navigate(1)
        self.assertEqual(self.selected_sequence(), 1)
        g._wb_navigate(1)
        self.assertEqual(self.selected_sequence(), 2)
        self.frame([0x50, 0x90, 0x01], 2202)
        self.assertEqual(self.selected_sequence(), 2)
        g.capture_highlight_vars[0].set("90")
        self.assertEqual(self.selected_sequence(), 2, "Refresh must keep selected historical row")

    def test_bookmark_annotations_round_trip_clear_and_exports(self):
        g = self.gui
        self.frame([0x05, 0x90, 1])
        g._wb_jump(1)
        old_session = g.capture_session_id
        g._wb_store_annotation(old_session, g.capture_records[0], True, "拔插后首次应答", ["HDMI", "电源"])
        mark_index = g.capture_tree.columns.index("mark")
        self.assertEqual(g.capture_tree.item(g.capture_tree.selection()[0], "values")[mark_index], "★")
        self.assertTrue(g.log_writer.flush(wait=True))
        saved = AnnotationLibrary(json.loads(g.wb_annotations_path.read_text(encoding="utf-8")))
        self.assertEqual(saved.get(old_session, 1)["note"], "拔插后首次应答")
        export = Path(self.folder.name)/"capture.json"
        with patch.object(self.module.filedialog, "asksaveasfilename", return_value=str(export)):
            g._export_capture_json()
        self.assertTrue(g.log_writer.flush(wait=True))
        item = json.loads(export.read_text(encoding="utf-8"))[0]
        self.assertEqual(item["capture_session_id"], old_session)
        self.assertEqual(item["tags"], ["HDMI", "电源"])
        g._clear_capture()
        self.frame([0x05, 0x90, 1])
        self.assertNotEqual(g.capture_session_id, old_session)
        self.assertEqual(g._wb_record_mark(g.capture_records[0]), "")
        self.assertEqual(len(g.wb_annotations.entries), 1)
        self.assertEqual(len(g.wb_notes_tree.get_children()), 1)

    def test_note_filter_and_poll_search(self):
        g = self.gui
        self.frame([0x05])
        self.frame([0x05, 0x90, 0x00], 2)
        g.wb_query_vars["opcode"].set("POLL")
        g._wb_apply_query()
        self.assertTrue(g.capture_show_poll_var.get())
        self.assertEqual(g._wb_matches, [1])
        g._wb_store_annotation(g.capture_session_id, g.capture_records[0], True, "地址冲突", ["启动"])
        g.capture_filter_var.set("冲突")
        g.wb_bookmarks_only.set(True)
        g._wb_apply_query()
        self.assertEqual(g._wb_matches, [1])

    def test_quick_worker_calls_native_api_and_releases_key_without_Tk_calls(self):
        g = self.gui
        command = QuickCommand.from_dict({"name": "音量", "opcode": "44", "params": "41", "release_key": True})
        with patch.object(type(g.backend), "is_open", new_callable=PropertyMock, return_value=True), \
             patch.object(g.backend, "execute_command", return_value=BackendResult(True, "mock ACK")) as execute:
            worker = threading.Thread(target=g._wb_quick_worker, args=(command, "tx", True))
            worker.start()
            worker.join(3)
            self.assertFalse(worker.is_alive())
        self.assertEqual([c.args[0] for c in execute.call_args_list], ["la 0", "tx 05:44:41", "tx 05:45"])
        while not g.output_queue.empty():
            g._drain_output_queue()
        self.assertFalse(g._wb_quick_busy)
        self.assertEqual(len(g.raw_history.get_children()), 2)

    def test_failed_logical_address_change_never_sends_frame(self):
        g = self.gui
        with patch.object(g.backend, "execute_command", return_value=BackendResult(False, "failed")) as execute:
            g._wb_quick_worker(default_quick_commands()[0], "tx", True)
        self.assertEqual([c.args[0] for c in execute.call_args_list], ["la 0"])
        self.assertEqual([g.output_queue.get_nowait()[0] for _ in range(2)], ["wb_command", "wb_done"])

    def test_settings_preserve_groups_shortcuts_and_filter_presets(self):
        g = self.gui
        g.wb_filter_presets["TV电源"] = {"source": "0", "opcode": "90", "mode": "FIND"}
        g._save_settings()
        self.assertTrue(g.log_writer.flush(wait=True))
        data = json.loads(g.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(data["workbench"]["quick_commands"][0]["shortcut"], "F5")
        self.assertEqual(data["workbench"]["filter_presets"]["TV电源"]["source"], "0")
        with patch.object(self.module.CECClientGUI, "_load_settings", return_value=data):
            other = self.module.CECClientGUI(start_hidden=True)
        try:
            self.assertEqual(other.wb_commands[0].shortcut, "F5")
            self.assertIn("TV电源", other.wb_filter_presets)
        finally:
            other.log_writer.close()
            other.destroy()

    def test_edit_quick_command_group_and_duplicate_shortcut(self):
        g = self.gui
        g._wb_new_quick()
        for key, value in {"name": "自定义SAD", "group": "实验", "source": "4", "destination": "5",
                           "opcode": "A4", "params": "01:07", "shortcut": "F9"}.items():
            g.wb_quick_vars[key].set(value)
        g._wb_save_quick()
        command = next(c for c in g.wb_commands if c.name == "自定义SAD")
        self.assertEqual(command.frames(), ["45:A4:01:07"])
        self.assertEqual(g.wb_group_var.get(), "实验")
        self.assertEqual(g.wb_quick_tree.selection(), (command.id,))
        g.wb_quick_vars["shortcut"].set("F5")
        with patch.object(self.module.messagebox, "showerror") as error:
            g._wb_save_quick()
        error.assert_called_once()
        self.assertEqual(next(c for c in g.wb_commands if c.id == command.id).shortcut, "F9")

    def test_saved_query_reload_and_manual_filter_while_paused(self):
        g = self.gui
        self.frame([0x05, 0x90, 0])
        self.frame([0x50, 0x7A, 0x20], 2)
        g.capture_paused_var.set(True)
        g.wb_query_vars["source"].set("5")
        g.wb_mode_var.set("FILTER")
        with patch("cec_workbench_ui.simpledialog.askstring", return_value="AMP回复"):
            g._wb_save_filter()
        self.assertEqual(len(g.capture_tree.get_children()), 1)
        g._wb_clear_query()
        self.assertEqual(len(g.capture_tree.get_children()), 2)
        g.wb_preset_var.set("AMP回复")
        g._wb_load_filter()
        self.assertEqual(g.wb_query_vars["source"].get(), "5")
        self.assertEqual(g._wb_matches, [2])
        g._wb_latest()
        self.assertFalse(g.capture_paused_var.get())

    def test_atomic_json_writes_are_ordered_and_complete_at_close(self):
        target = Path(self.folder.name)/"nested"/"annotations.json"
        self.gui.log_writer.replace_json(target, {"note": "first"})
        self.gui.log_writer.replace_json(target, {"note": "最终注释"})
        self.gui.log_writer.close()
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"note": "最终注释"})
        self.assertFalse(list(target.parent.glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
