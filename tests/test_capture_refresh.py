"""Sustained capture and UI polling regression tests, without adapter writes."""
import importlib.util
import ctypes
from pathlib import Path
import sys
import time
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class CaptureRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("capture_refresh_gui", ROOT / "cec-client-gui.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        with patch.object(self.module.CECClientGUI, "_load_settings", return_value={}):
            self.gui = self.module.CECClientGUI()
        self.gui.withdraw()
        # Synthetic failure tests must not contaminate real diagnostic logs.
        self.write_patch = patch.object(self.gui.log_writer, "write")
        self.log_write = self.write_patch.start()

    def tearDown(self):
        self.gui.update_idletasks()
        self.write_patch.stop()
        self.gui.log_writer.close()
        self.gui.destroy()

    @staticmethod
    def event(index=1):
        return {"kind": "raw_frame", "frame": [0x50, 0x90, 0], "direction": "RX",
                "raw_status_text": "INCOMING", "ack": 0, "raw_timestamp": index * 100}

    def test_single_bad_event_does_not_stop_the_pump_or_lose_later_frames(self):
        gui = self.gui
        gui.backend._events.put({"kind": "bad_test_event"})
        gui.backend._events.put(self.event())
        original = gui._handle_backend_event

        def handle(event):
            if event["kind"] == "bad_test_event":
                raise tk.TclError("injected UI event failure")
            original(event)

        with patch.object(gui, "_handle_backend_event", side_effect=handle), \
             patch.object(gui, "after", return_value="test-next-poll") as schedule:
            gui._poll_output()
        self.assertEqual(len(gui.capture_records), 1)
        self.assertTrue(schedule.called)

    def test_recent_occupancy_observer_failure_does_not_stop_capture(self):
        gui = self.gui
        gui.backend._events.put(self.event(1))
        gui.backend._events.put(self.event(2))
        with patch.object(gui, "_occupancy_observe", side_effect=tk.TclError("stale experiment widget")), \
             patch.object(gui, "after", return_value="test-next-poll") as schedule:
            gui._poll_output()
        self.assertEqual(len(gui.capture_records), 2)
        self.assertTrue(schedule.called)
        self.assertEqual(gui.gui_error_count, 2)
        errors = [call for call in self.log_write.call_args_list if call.args[0] == gui.gui_error_path]
        self.assertEqual(len(errors), 2)

    def test_long_history_draws_only_the_viewport(self):
        table = self.gui.capture_tree
        table.begin_update()
        for seq in range(2000):
            table.insert("", "end", values=(seq, "12:00:00.000", "5", "0", "50:90:00",
                         "76.5 ms", "100 ms", "23.5 ms", "Report Power Status", "", "00", "", "ACK"))
        table.end_update()
        self.gui.update_idletasks()
        self.assertEqual(len(table.get_children()), 2000)
        self.assertLess(len(table.body_canvas.find_all()), 3000,
                        "Offscreen rows must not become tens of thousands of canvas objects")

    def test_pause_resume_catches_up_without_requiring_another_frame(self):
        gui = self.gui
        gui.capture_paused_var.set(True)
        for seq in range(3):
            gui._handle_backend_event(self.event(seq))
        self.assertEqual(len(gui.capture_tree.get_children()), 0)
        gui.capture_paused_var.set(False)
        gui._capture_pause_changed()
        self.assertEqual(len(gui.capture_tree.get_children()), 3)

    def test_pump_bounds_queue_work_instead_of_draining_forever(self):
        gui = self.gui
        for seq in range(1000):
            gui.backend._events.put(self.event(seq))
        with patch.object(gui, "after", return_value="test-next-poll") as schedule:
            gui._poll_output()
        self.assertGreater(len(gui.capture_records), 0)
        self.assertGreater(gui.backend._events.qsize(), 0)
        self.assertTrue(schedule.called)

    def test_output_task_failure_still_schedules_later_work(self):
        gui = self.gui
        gui.output_queue.put(("scenario_status", "first"))
        gui.output_queue.put(("scenario_status", "second"))
        with patch.object(gui.scenario_status_var, "set", side_effect=tk.TclError("stale label")), \
             patch.object(gui, "after", return_value="test-next-poll") as schedule:
            gui._poll_output()
        self.assertTrue(schedule.called)
        self.assertEqual(gui.output_queue.qsize(), 1)
        gui._poll_output()
        self.assertEqual(gui.scenario_status_var.get(), "second")

    def test_table_failure_retains_record_then_rebuilds(self):
        gui = self.gui
        original = gui.capture_tree.insert
        calls = 0

        def transient_failure(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise tk.TclError("injected insert failure")
            return original(*args, **kwargs)

        gui.backend._events.put(self.event(1))
        gui.backend._events.put(self.event(2))
        with patch.object(gui.capture_tree, "insert", side_effect=transient_failure):
            gui._poll_output()
        self.assertEqual(len(gui.capture_records), 2)
        self.assertEqual(len(gui.capture_tree.get_children()), 2)
        self.assertFalse(gui._capture_display_dirty)
        self.assertEqual(gui.gui_error_count, 1)

    def test_sustained_native_callback_history_occupancy_and_selection(self):
        """Exercise the ctypes rawFrame entry point; no serial port or bus TX."""
        gui = self.gui
        table = gui.capture_tree
        gui.capture_show_poll_var.set(True)
        gui.occupancy_observed_mask = 0x20
        gui.backend._create_callbacks()
        gui._handle_backend_event(self.event(0))
        selected = table.get_children()[0]
        table.selection_set(selected)
        start = time.perf_counter()
        for index in range(1, 6001):
            frame = [0x05] if index % 2 else [0x50, 0x90, 0]
            command = gui.backend.frame_to_command(frame)
            command.ack = 0
            gui.backend._callbacks.rawFrame(None, ctypes.byref(command), 0, 1, index * 100)
        rounds = 0
        with patch.object(table.body_canvas, "winfo_height", return_value=540):
            while gui.backend.pending_event_count:
                gui._poll_output()
                gui.update_idletasks()
                rounds += 1
                self.assertLess(rounds, 6002)
            gui.update_idletasks()
        elapsed = time.perf_counter() - start
        self.assertEqual(len(gui.capture_records), 6001)
        self.assertEqual(len(table.get_children()), 2000)
        self.assertEqual(table.item(table.get_children()[-1], "values")[0], 6001)
        self.assertEqual(table.selection(), (selected,))
        self.assertEqual(gui.occupancy_poll_counts[5], [3000, 0, 0])
        self.assertEqual(len(gui.occupancy_events.get_children()), 500)
        self.assertEqual(gui.gui_error_count, 0)
        self.assertLess(len(table.body_canvas.find_all()), 3000)
        print(f"\n6000 native callbacks: {elapsed:.2f}s; {rounds} batches; "
              f"{len(table.body_canvas.find_all())} canvas objects; selected row retained")

    def test_viewport_redraw_keeps_wrapping_colors_and_hit_testing(self):
        gui = self.gui
        table = gui.capture_tree
        gui.capture_highlight_vars[0].set("90")
        gui._handle_backend_event(self.event())
        gui.capture_records[0]["params_explanation"] = "很长的参数说明，需正确换行显示。" * 30
        gui._refresh_capture_tree()
        table.yview("moveto", 0)
        gui.update_idletasks()
        item = table.get_children()[0]
        self.assertGreater(table._row_heights[item], table._base_row_height)
        self.assertEqual(table.identify_row(1), item)
        colors = {table.body_canvas.itemcget(obj, "fill")
                  for obj in table.body_canvas.find_all()
                  if table.body_canvas.type(obj) == "rectangle"}
        self.assertIn("#FFE68A", colors)
        self.assertIn("#E8F4FB", colors)
        table.selection_set(item)
        gui.update_idletasks()
        self.assertEqual(table.selection(), (item,))

    def test_scroll_to_bottom_resumes_following_after_history_browse(self):
        gui = self.gui
        for index in range(100):
            gui._handle_backend_event(self.event(index))
        table = gui.capture_tree
        table.yview("moveto", 0.2)
        gui._update_capture_refresh_state()
        self.assertIn("浏览历史", gui.capture_refresh_state_var.get())
        gui._handle_backend_event(self.event(101))
        self.assertFalse(table.is_at_bottom())
        table.yview("moveto", 1.0)
        gui._handle_backend_event(self.event(102))
        self.assertTrue(table.is_at_bottom())
        gui._update_capture_refresh_state()
        self.assertIn("跟随最新", gui.capture_refresh_state_var.get())

    def test_compact_columns_fit_viewport_without_hiding_ack(self):
        table = self.gui.capture_tree
        table.fit_columns(1900)
        self.assertLess(sum(table._widths.values()), 1700)
        table.fit_columns(1200)
        self.assertEqual(sum(table._widths.values()), 1200)
        for column in table.columns:
            self.assertGreaterEqual(table._widths[column], table._minimum_widths[column])
            self.assertGreaterEqual(table._widths[column], table._header_font.measure(table._headings[column]) + 12)
        self.assertEqual(table.columns[-1], "status")
        table.fit_columns(800)
        self.assertEqual(table._widths, table._minimum_widths)

    def test_long_raw_fields_wrap_instead_of_overlapping_adjacent_columns(self):
        gui = self.gui
        gui._handle_backend_event(dict(self.event(), frame=[0x05, 0xA0] + list(range(14))))
        table = gui.capture_tree
        table.fit_columns(1200)
        table.yview("moveto", 0)
        gui.update_idletasks()
        item = table.get_children()[0]
        values = table.item(item, "values")
        self.assertEqual(values[table.columns.index("frame")], gui.capture_records[0]["frame"])
        self.assertGreater(table._row_heights[item], table._base_row_height)
        for field in ("frame", "opcode", "params", "params_explanation"):
            index = table.columns.index(field)
            wrapped = table._wrap_text(values[index], table._widths[field] - 10)
            for line in wrapped.splitlines():
                self.assertLessEqual(table._font.measure(line), table._widths[field] - 10)

    def test_header_resize_autofit_and_reset_keep_selected_data(self):
        gui = self.gui
        gui._handle_backend_event(self.event())
        table = gui.capture_tree
        selected = table.get_children()[0]
        table.selection_set(selected)
        values = table.item(selected, "values")
        original = table.column("seq", "width")
        table._on_header_press(SimpleNamespace(x=original))
        table._on_header_release(SimpleNamespace(x=original + 50))
        self.assertEqual(table.column("seq", "width"), original + 50)
        table._on_header_autofit(SimpleNamespace(x=original + 50))
        self.assertEqual(table.column("seq", "width"), table._minimum_widths["seq"])
        gui._reset_capture_columns()
        self.assertEqual(table.column("seq", "width"), original)
        self.assertEqual(table.selection(), (selected,))
        self.assertEqual(table.item(selected, "values"), values)

    def test_resize_keeps_history_anchor_and_selection(self):
        gui = self.gui
        for index in range(100):
            gui._handle_backend_event(self.event(index))
        table = gui.capture_tree
        table.yview("moveto", 0.2)
        anchor = table.identify_row(0)
        selected = table.get_children()[50]
        table.selection_set(selected)
        table.fit_columns(1200)
        self.assertEqual(table.identify_row(0), anchor)
        self.assertEqual(table.selection(), (selected,))
        with patch.object(table, "_apply_column_widths") as resize:
            gui._handle_backend_event(self.event(101))
        resize.assert_not_called()


if __name__ == "__main__":
    unittest.main()
