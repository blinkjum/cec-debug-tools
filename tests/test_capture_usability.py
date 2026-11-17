"""Removable criteria, truthful match reasons and persistent display layouts."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from capture_layout import CAPTURE_COLUMNS, COMPACT_COLUMNS, MINIMUM_WIDTHS, PREFERRED_WIDTHS, normalize_layout
from cec_workbench import CaptureQuery


class CaptureUsabilityModelTests(unittest.TestCase):
    def test_layout_sanitizes_corrupt_preferences_and_preserves_new_columns(self):
        state = normalize_layout({"order": ["status", "frame", "status", [], "removed"],
                                  "visible": ["status", "removed"], "auto_fit": "bad",
                                  "widths": {"frame": -100, "opcode": float("nan"), "status": 10000, "seq": True}})
        self.assertEqual(state["order"][:2], ["status", "frame"])
        self.assertEqual(set(state["order"]), set(CAPTURE_COLUMNS))
        self.assertEqual(state["visible"], ["status"])
        self.assertEqual(state["widths"]["frame"], MINIMUM_WIDTHS["frame"])
        self.assertEqual(state["widths"]["opcode"], PREFERRED_WIDTHS["opcode"])
        self.assertEqual(state["widths"]["status"], 1600)
        self.assertEqual(state["widths"]["seq"], PREFERRED_WIDTHS["seq"])
        self.assertTrue(state["auto_fit"])
        self.assertEqual(normalize_layout({"visible": []})["visible"], list(CAPTURE_COLUMNS))

    def test_explanations_identify_exact_fields_including_notes_and_tags(self):
        record = dict(source="0", destination="5", opcode=0x7A, frame="05:7A:42", params="42",
                      params_explanation="音量=66/100；静音=否", status="ACK")
        query = CaptureQuery.from_dict({"source": "0", "opcode": "7A", "params": "4?", "text": "音量"})
        reasons = "；".join(query.explain_match(record))
        self.assertIn("源LA=0", reasons)
        self.assertIn("参数=4?", reasons)
        self.assertIn("全文命中：参数解释", reasons)
        self.assertNotIn("全文命中：报文", reasons)
        for annotation, expected in (({"note": "ARC启动失败"}, "注释"), ({"tags": ["ARC"]}, "标签")):
            text_query = CaptureQuery.from_dict({"text": "arc"})
            self.assertTrue(text_query.matches(record, annotation))
            self.assertIn(expected, "".join(text_query.explain_match(record, annotation)))
        self.assertEqual(CaptureQuery.from_dict({"opcode": "90"}).explain_match(record), [])
        self.assertEqual(CaptureQuery().explain_match(record), [])

    def test_poll_and_cross_field_text_reasons_follow_actual_matching(self):
        poll = dict(source="0", destination="5", opcode=None, is_poll=True, frame="05", status="NACK")
        query = CaptureQuery.from_dict({"opcode": "POLL", "status": "NACK"})
        self.assertEqual(query.explain_match(poll), ["Opcode=POLL", "状态=NACK"])
        self.assertFalse(query.explain_match(dict(poll, is_poll=False)))
        text_query = CaptureQuery.from_dict({"text": "05 0"})
        self.assertTrue(text_query.matches(poll))
        self.assertIn("跨字段", "".join(text_query.explain_match(poll)))


class CaptureUsabilityGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("usability_gui", ROOT / "cec-client-gui.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        scratch = ROOT / "work/ui-usability-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=scratch)
        self.gui = self.module.CECClientGUI(read_settings=False, start_hidden=True)
        self.gui.settings_path = Path(self.folder.name) / "settings.json"
        self.gui.wb_annotations_path = Path(self.folder.name) / "bookmarks.json"
        self.gui.log_file_var.set("")
        self.gui.short_log_file_var.set("")

    def tearDown(self):
        self.gui.update_idletasks()
        self.gui.log_writer.close()
        self.gui.destroy()
        self.folder.cleanup()

    def frame(self, frame, index=1):
        self.gui._handle_backend_event(dict(kind="raw_frame", frame=frame, direction="RX",
                                            raw_status_text="INCOMING", ack=0, raw_timestamp=index*100))

    def test_removing_individual_criteria_retains_others_and_live_rows(self):
        g = self.gui
        self.frame([0x05, 0x90, 0])
        self.frame([0x50, 0x90, 0], 2)
        self.frame([0x50, 0x7A, 0x42], 3)
        rows = g.capture_tree.get_children()
        g.wb_query_vars["source"].set("0")
        g.wb_query_vars["opcode"].set("90")
        g._wb_apply_query()
        self.assertEqual(set(g.wb_condition_buttons), {"source", "opcode"})
        with patch.object(g.capture_tree, "delete") as delete:
            g.wb_condition_buttons["source"].invoke()
            self.assertEqual(g._wb_matches, [1, 2])
            self.assertEqual(g.wb_query_vars["opcode"].get(), "90")
            self.assertEqual(set(g.wb_condition_buttons), {"opcode"})
            delete.assert_not_called()
        g.wb_only_matches_button.invoke()
        self.assertEqual(len(g.capture_tree.get_children()), 2)
        g.wb_condition_buttons["opcode"].invoke()
        self.assertEqual(g._wb_applied_mode, "FIND")
        self.assertEqual(len(g.capture_tree.get_children()), 3)
        self.assertFalse(g.wb_condition_area.winfo_manager())
        self.assertTrue(g.wb_next_button.instate(["disabled"]))
        self.assertNotEqual(rows, (), "Original capture remains available")

    def test_invalid_condition_can_be_removed_without_erasing_valid_draft(self):
        g = self.gui
        self.frame([0x50, 0x90, 0])
        g.wb_query_vars["opcode"].set("90")
        g._wb_apply_query()
        g.wb_query_vars["source"].set("GG")
        self.assertFalse(g._wb_apply_query())
        self.assertIn("source", g.wb_condition_buttons)
        self.assertEqual(g.wb_condition_caption["text"], "待修正")
        self.assertIn("条件有误", g.wb_match_reason.get())
        g.wb_condition_buttons["source"].invoke()
        self.assertTrue(g._wb_query_valid)
        self.assertEqual(g._wb_matches, [1])
        self.assertEqual(g.wb_query_vars["opcode"].get(), "90")

    def test_chips_clear_text_bookmarks_and_all_without_resetting_opcode_colors(self):
        g = self.gui
        self.frame([0x05, 0x90, 0])
        g.wb_annotations.set(g.capture_session_id, g.capture_records[0], bookmark=True, note="电源", tags=[])
        g.capture_filter_var.set("电源")
        g.wb_bookmarks_only.set(True)
        g.wb_query_vars["opcode"].set("90")
        g.capture_highlight_vars[0].set("90")
        g._wb_apply_query()
        g.wb_condition_buttons["text"].invoke()
        self.assertEqual(g.capture_filter_var.get(), "")
        self.assertTrue(g.wb_bookmarks_only.get())
        g.wb_condition_buttons["bookmarks_only"].invoke()
        self.assertEqual(g._wb_matches, [1])
        g.wb_clear_conditions_button.invoke()
        self.assertFalse(g.wb_query.has_conditions)
        self.assertEqual(g.capture_highlight_vars[0].get(), "90")

    def test_reason_tracks_selection_notes_queries_and_details(self):
        g = self.gui
        self.frame([0x05, 0x44, 0x41])
        self.frame([0x50, 0x7A, 0x42], 2)
        g.capture_filter_var.set("Volume Up")
        g._wb_navigate(1)
        self.assertIn("参数解释", g.wb_match_reason.get())
        self.assertIn("Volume Up", g.wb_match_reason.get())
        self.assertIn("查找命中原因", g._capture_details_content(g.capture_records[0]))
        g._wb_jump(2)
        self.assertIn("未命中", g.wb_match_reason.get())
        g.wb_annotations.set(g.capture_session_id, g.capture_records[1], bookmark=False, note="ARC failure", tags=[])
        g.capture_filter_var.set("ARC")
        g._wb_apply_query()
        self.assertIn("注释", g.wb_match_reason.get())
        self.assertIn("ARC failure", g.wb_match_reason.get())

    def test_conditions_wrap_with_long_values_at_narrow_window_width(self):
        g = self.gui
        g.geometry("1000x860")
        g.notebook.select(g.capture_tab_widget)
        g.deiconify()
        for key, value in dict(source="0,4,5", destination="0,5", opcode="44,7A,90", status="ACK", params="??:"*13+"??").items():
            g.wb_query_vars[key].set(value)
        g.capture_filter_var.set("长文字" * 35)
        g.wb_bookmarks_only.set(True)
        g._wb_apply_query()
        g.update()
        g._wb_layout_chips()
        width = g.wb_condition_chips.winfo_width()
        self.assertTrue(all(b.winfo_x()+b.winfo_width() <= width for b in g.wb_condition_buttons.values()))
        self.assertGreater(g.wb_condition_chips.winfo_height(), 27)
        self.assertEqual(g.gui_error_count, 0)

    def test_hidden_reordered_columns_keep_values_copy_colors_and_selection(self):
        g = self.gui
        self.frame([0x05, 0x44, 0x41])
        table = g.capture_tree
        item = table.get_children()[0]
        original = table.item(item, "values")
        g.capture_highlight_vars[0].set("44")
        state = g._layout_preferences()
        visible = ["params_explanation", "frame", "opcode"]
        state.update(order=visible + [c for c in CAPTURE_COLUMNS if c not in visible], visible=visible)
        with patch.object(table, "delete") as delete, patch.object(g.backend, "transmit") as transmit:
            g._layout_apply(state)
            self.assertEqual(table.display_columns, tuple(visible))
            self.assertEqual(table.item(item, "values"), original)
            g._wb_jump(1)
            self.assertEqual(table.selection(), (item,))
            self.assertEqual(table.identify_column(table._widths["params_explanation"] + 10), f"#{CAPTURE_COLUMNS.index('frame')+1}")
            g.capture_context_item = item
            g.capture_context_column = table.identify_column(table._widths["params_explanation"] + 10)
            with patch.object(g, "_copy_to_clipboard") as copy:
                g._copy_capture_cell()
                copy.assert_called_once_with("05:44:41")
            delete.assert_not_called()
            transmit.assert_not_called()
        table.selection_set(())
        g.geometry("1280x860")
        g.notebook.select(g.capture_tab_widget)
        g.deiconify()
        g.update()
        canvas = table.body_canvas
        rectangles = [r for r in canvas.find_withtag(f"capture_item_{item}") if canvas.type(r) == "rectangle"]
        self.assertEqual(len(rectangles), 3)
        self.assertEqual(canvas.itemcget(rectangles[2], "fill"), g.capture_highlight_color_vars[0].get())
        self.assertEqual(len(g._wb_export_records()[0]["frame"]), 8)

    def test_preset_toggle_and_width_reset_preserve_selected_records(self):
        g = self.gui
        self.frame([0x50, 0x90, 0])
        item = g.capture_tree.get_children()[0]
        g.capture_tree.selection_set(item)
        g._layout_preset("COMPACT")
        self.assertEqual(g.capture_tree.display_columns, COMPACT_COLUMNS)
        g._layout_toggle_column("opcode_explanation")
        self.assertIn("opcode_explanation", g.capture_tree.display_columns)
        before = g.capture_tree.display_columns
        g._reset_capture_columns()
        self.assertEqual(g.capture_tree.display_columns, before)
        self.assertTrue(g.capture_tree.auto_fit_columns)
        self.assertEqual(g.capture_tree.selection(), (item,))
        state = g._layout_preferences()
        state["visible"] = ["frame"]
        g._layout_apply(state)
        g._layout_toggle_column("frame")
        self.assertEqual(g.capture_tree.display_columns, ("frame",))
        g._layout_preset("FULL")
        self.assertEqual(g.capture_tree.display_columns, CAPTURE_COLUMNS)

    def test_manual_width_order_and_visibility_are_saved_and_reloaded(self):
        g = self.gui
        g._layout_persist = True
        state = g._layout_preferences()
        state.update(order=["frame", *[c for c in CAPTURE_COLUMNS if c != "frame"]],
                     visible=["frame", "opcode", "status"], auto_fit=False)
        state["widths"]["frame"] = 321
        with patch.object(g, "_save_settings", wraps=g._save_settings) as save:
            g._layout_apply(state)
            g.after(420, g.quit)
            g.mainloop()
            save.assert_called_once()
        self.assertTrue(g.log_writer.flush(wait=True))
        saved = json.loads(g.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["workbench"]["capture_layout"]["widths"]["frame"], 321)
        with patch.object(self.module.CECClientGUI, "_settings_path", return_value=g.settings_path):
            restored = self.module.CECClientGUI(read_settings=True, start_hidden=True)
        try:
            self.assertEqual(restored.capture_tree.display_columns, ("frame", "opcode", "status"))
            self.assertEqual(restored.capture_tree.column("frame", "width"), 321)
            self.assertFalse(restored.capture_tree.auto_fit_columns)
            restored.capture_tree.fit_columns()
            self.assertEqual(restored.capture_tree.column("frame", "width"), 321)
        finally:
            restored.log_writer.close()
            restored.destroy()

    def test_native_menu_and_layout_editor_validate_and_apply_drafts(self):
        g = self.gui
        with patch.object(self.module.tk.Menu, "tk_popup"):
            g._layout_show_menu()
        g._layout_menu.invoke(0)
        self.assertEqual(g.capture_tree.display_columns, COMPACT_COLUMNS)
        g._layout_edit()
        dialog = g._layout_dialog
        original = g.capture_tree.display_columns
        dialog.tree.selection_set("frame")
        dialog.on_select()
        dialog.width_var.set("1")
        self.assertFalse(dialog.apply())
        self.assertEqual(g.capture_tree.display_columns, original)
        dialog.width_var.set("333")
        dialog.move(-1)
        self.assertTrue(dialog.apply())
        self.assertEqual(g.capture_tree.column("frame", "width"), 333)
        self.assertFalse(g.capture_tree.auto_fit_columns)
        dialog.state_data["visible"] = []
        dialog.visible_var.set(False)
        self.assertFalse(dialog.apply())
        dialog.destroy()


if __name__ == "__main__":
    unittest.main()
