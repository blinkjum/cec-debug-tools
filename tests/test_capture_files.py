"""Structural checks, lossless offline viewing and scoped background exports."""
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from cec_capture_tools import check_frame_length, export_text, read_capture_file, write_export


class CaptureFileModelTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "work/capture-file-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def test_fixed_range_discrete_and_conditional_lengths(self):
        valid = ["05", "50:90:01", "05:46", "50:47:54:56", "05:70", "05:70:10:00",
                 "50:A3:57:04:03", "05:44:6D", "05:44:60:24", "05:44:67:04:01:00:02",
                 "05:09:01", "05:09:04:01"]
        invalid = ["50:90", "50:90:01:02", "05:46:01", "50:47", "05:70:01",
                   "50:A3:57:04", "05:44", "05:44:6D:01", "05:44:67:01",
                   "05:09:02:01", "05:90:GG", "", ":".join(["00"] * 17)]
        for frame in valid:
            self.assertEqual(check_frame_length(frame)["status"], "OK", frame)
        for frame in invalid:
            self.assertEqual(check_frame_length(frame)["status"], "ERROR", frame)
        self.assertEqual(check_frame_length("05:FE:01:02")["status"], "NOT_COVERED")
        self.assertEqual(check_frame_length("05:A1:01:02")["status"], "NOT_COVERED")

    def test_csv_json_round_trip_preserves_unicode_notes_and_native_timestamp(self):
        rows = [dict(seq=23, frame="50:90:01", time="23:59:59.123", timestamp_ms=12345,
                     direction="RX", status="ACK", note='第一行，带逗号\n第二行 "引号"',
                     bookmark=True, tags=["音频,ARC", "上电"], capture_session_id="original-session")]
        for fmt, suffix in [("CSV", ".csv"), ("TSV", ".tsv"), ("JSON", ".json")]:
            path = self.root / ("中文 空格" + suffix)
            write_export(path, rows, fmt)
            before = path.read_bytes()
            document = read_capture_file(path)
            self.assertEqual(document.sha256, hashlib.sha256(before).hexdigest())
            item = document.records[0]
            for key in ("frame", "time", "timestamp_ms", "direction", "status", "note", "tags", "bookmark"):
                self.assertEqual(item[key], rows[0][key], (fmt, key))
            self.assertEqual(item["origin_seq"], 23 if fmt == "JSON" else "23")
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse(list(self.root.glob("*.tmp")))

    def test_packet_log_keeps_bad_frames_without_fabricating_time_or_ack(self):
        path = self.root / "packets.log"
        path.write_text("# own packet log\nRX 05\nTX 50:90:00 -> ACK\nRX 05:90:GG\nignored heading\n", encoding="utf-8")
        loaded = read_capture_file(path)
        self.assertEqual(len(loaded.records), 3)
        self.assertEqual(loaded.skipped_lines, 1)
        self.assertIsNone(loaded.records[0]["timestamp_ms"])
        self.assertEqual(loaded.records[0]["time"], "—")
        self.assertEqual(loaded.records[0]["status"], "—")
        self.assertEqual(loaded.records[1]["status"], "ACK")
        self.assertEqual(loaded.records[2]["frame"], "05:90:GG")
        self.assertEqual(check_frame_length(loaded.records[2]["frame"])["status"], "ERROR")

    def test_reader_rejects_non_capture_and_supports_wrapper_and_legacy_tags(self):
        path = self.root / "capture.json"
        path.write_text(json.dumps({"records": [dict(frame="05", tags="电源, ARC", bookmark="false", timestamp_ms="nan")]}), encoding="utf-8")
        item = read_capture_file(path).records[0]
        self.assertFalse(item["bookmark"])
        self.assertIsNone(item["timestamp_ms"])
        self.assertEqual(item["tags"], ["电源", "ARC"])
        path.write_text('{"not_records": []}', encoding="utf-8")
        with self.assertRaises(ValueError):
            read_capture_file(path)
        path = self.root / "fields.csv"
        path.write_text("opcode_text,note\n90,test\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_capture_file(path)

    def test_export_raw_names_and_custom_columns_preserves_order_and_duplicates(self):
        rows = [dict(frame="05:8F", opcode_text="0x8F Give Device Power Status", seq=n) for n in (1, 2)]
        self.assertEqual(export_text(rows, "RAW"), "05:8F\n05:8F\n")
        self.assertEqual(export_text(rows, "OPCODE").count("0x8F"), 2)
        exported = list(csv.reader(io.StringIO(export_text(rows, "CSV", ["frame", "seq"]))))
        self.assertEqual(exported, [["frame", "seq"], ["05:8F", "1"], ["05:8F", "2"]])


class CaptureFileGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("capture_file_gui", ROOT / "cec-client-gui.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        scratch = ROOT / "work/capture-file-gui-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.folder.name)
        self.gui = self.module.CECClientGUI(read_settings=False, start_hidden=True)
        self.gui.settings_path = self.root / "settings.json"
        self.gui.wb_annotations_path = self.root / "bookmarks.json"

    def tearDown(self):
        self.gui.update_idletasks()
        self.gui.log_writer.close()
        self.gui.destroy()
        self.folder.cleanup()

    def frame(self, frame, index=1, direction="RX", ack=0):
        self.gui._handle_backend_event(dict(kind="raw_frame", frame=frame, direction=direction,
                                           raw_status_text="INCOMING", ack=ack, raw_timestamp=index * 100))

    def wait_import(self):
        deadline = time.monotonic() + 8
        while self.gui._files_load_busy and time.monotonic() < deadline:
            self.gui.update()
            time.sleep(0.005)
        self.assertFalse(self.gui._files_load_busy, "Offline import did not complete")

    def load(self, rows):
        path = self.root / "capture.json"
        path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        self.assertTrue(self.gui._files_load(path))
        self.wait_import()
        return path

    def selected(self):
        return [int(self.gui.capture_tree.item(item, "values")[0]) for item in self.gui.capture_tree.selection()]

    def test_length_errors_mark_filter_and_navigate_without_changing_bus_status(self):
        g = self.gui
        for index, frame in enumerate(([0x05, 0x90, 1], [0x05, 0x90], [0x05, 0xFE], [0x05, 0x46, 1]), 1):
            self.frame(frame, index)
        self.assertEqual(g._files_errors, [2, 4])
        self.assertEqual(g.capture_records[1]["status"], "ACK")
        self.assertIn("!", g.capture_tree.item(g.capture_tree.get_children()[1], "values")[1])
        g._files_navigate_error(1)
        self.assertEqual(self.selected(), [2])
        g._files_navigate_error(1)
        self.assertEqual(self.selected(), [4])
        g._files_navigate_error(-1)
        self.assertEqual(self.selected(), [2])
        g._files_errors_only.set(True)
        g._wb_base_changed()
        self.assertEqual([g.capture_tree.item(item, "values")[0] for item in g.capture_tree.get_children()], [2, 4])
        self.assertIn("长度检查：ERROR", g._capture_details_content(g.capture_records[1]))

    def test_offline_view_keeps_live_capture_selection_and_annotations_isolated(self):
        g = self.gui
        self.frame([0x05, 0x8F])
        g._wb_jump(1)
        self.assertTrue(g._capture_follow_suspended)
        original_session = g.capture_session_id
        g.wb_annotations.set(original_session, g.capture_records[0], bookmark=True, note="live note", tags=[])
        live_library = g.wb_annotations
        path = self.root / "offline.json"
        path.write_text(json.dumps([dict(seq=91, frame="50:90:01", time="12:00:00.123", timestamp_ms=300,
                                         bookmark=True, note="offline note", tags=["离线"])]), encoding="utf-8")
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.object(g.backend, "execute_command") as execute, patch.object(g.backend, "transmit") as transmit:
            g._files_load(path)
            self.wait_import()
            self.assertEqual(g._files_mode, "OFFLINE")
            self.assertEqual(g.capture_records[0]["origin_seq"], 91)
            self.assertEqual(g.capture_records[0]["time"], "12:00:00.123")
            self.assertEqual(g._wb_annotation(g.capture_records[0])["note"], "offline note")
            self.frame([0x50, 0x90, 0], 2)
            self.assertEqual(len(g.capture_records), 1)
            self.assertEqual(len(g._files_live["records"]), 2)
            g._update_capture_refresh_state()
            self.assertIn("后台实时记录 2 条", g.capture_refresh_state_var.get())
            g._wb_store_annotation(g.capture_session_id, g.capture_records[0], True, "edited offline", ["离线"])
            self.assertTrue(g.log_writer.flush(wait=True))
            self.assertIn("offline-bookmarks", str(g.wb_annotations_path))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), original_hash)
            g._files_show_live()
            self.assertEqual(g.capture_session_id, original_session)
            self.assertIs(g.wb_annotations, live_library)
            self.assertEqual(g.wb_annotations.get(original_session, 1)["note"], "live note")
            self.assertEqual(len(g.capture_records), 2)
            self.assertEqual(self.selected(), [1])
            self.assertTrue(g._capture_follow_suspended, "Offline navigation must preserve the live browsing hold")
            g._files_load(path)
            self.wait_import()
            self.assertEqual(g._wb_annotation(g.capture_records[0])["note"], "edited offline")
            execute.assert_not_called()
            transmit.assert_not_called()

    def test_bad_offline_frames_remain_visible_and_bad_files_preserve_live_data(self):
        g = self.gui
        self.frame([0x05, 0x8F])
        self.load([dict(frame="05:90:GG"), dict(frame="05"), dict(frame="50:90")])
        self.assertEqual(g._files_errors, [1, 3])
        self.assertEqual(len(g.capture_tree.get_children()), 2, "Malformed text is not a hidden Poll")
        g.wb_query_vars["source"].set("5")
        self.assertTrue(g._wb_apply_query())
        self.assertEqual(g._wb_matches, [3])
        g._wb_clear_query()
        g.wb_query_vars["opcode"].set("POLL")
        g._wb_apply_query()
        self.assertEqual(g._wb_matches, [2])
        g._files_show_live()
        before = g.capture_records
        bad = self.root / "bad.json"
        bad.write_text('{"not_records":0}', encoding="utf-8")
        with patch("capture_files_ui.messagebox.showerror"):
            g._files_load(bad)
            self.wait_import()
        self.assertEqual(g._files_mode, "LIVE")
        self.assertIs(g.capture_records, before)
        self.assertEqual(g.gui_error_count, 0)

    def test_multi_selection_context_menu_and_refresh_preserve_batch_scope(self):
        g = self.gui
        for i in range(5):
            self.frame([0x05, 0x90, i % 4], i)
        items = g.capture_tree.get_children()
        for item, state in ((items[0], 0), (items[2], 4), (items[4], 1)):
            with patch.object(g.capture_tree, "identify_row", return_value=item):
                g.capture_tree._on_left_click(SimpleNamespace(y=0, state=state))
        self.assertEqual(self.selected(), [3, 4, 5])
        with patch.object(g.capture_tree, "identify_row", return_value=items[3]), patch.object(self.module.tk.Menu, "tk_popup"):
            g._capture_context_menu(SimpleNamespace(x=10, y=5, x_root=10, y_root=10))
        self.assertEqual(self.selected(), [3, 4, 5])
        g._refresh_capture_tree(force=True)
        self.assertEqual(self.selected(), [3, 4, 5])
        self.assertEqual([r["seq"] for r in g._files_extract_records("SELECTED")], [3, 4, 5])
        self.frame([0x50, 0x90, 0], 6)
        self.assertEqual(self.selected(), [3, 4, 5])

    def test_matched_export_uses_all_history_even_in_find_mode_and_writes_off_thread(self):
        g = self.gui
        g.capture_tree.begin_update()
        for i in range(2105):
            self.frame([0x50 if i % 2 else 0x05, 0x90, 0], i)
        g.capture_tree.end_update()
        g.wb_query_vars["source"].set("5")
        g.wb_mode_var.set("FIND")
        matches = g._files_extract_records("MATCHED")
        self.assertEqual(len(matches), 1052)
        self.assertEqual(matches[0]["seq"], 2)
        self.assertEqual(len(g._files_extract_records("ALL")), 2105)
        target = self.root / "selected.csv"
        thread_ids = []
        real_write = self.module.write_export
        def observe(*args):
            thread_ids.append(threading.get_ident())
            return real_write(*args)
        with patch.object(self.module, "write_export", side_effect=observe):
            g._files_start_export(target, matches, "CSV", ["seq", "frame"])
            self.assertTrue(g.log_writer.flush(wait=True, timeout=10))
        self.assertTrue(thread_ids and all(x != threading.get_ident() for x in thread_ids))
        self.assertEqual(len(read_capture_file(target).records), 1052)

    def test_export_cannot_overwrite_offline_input_and_loading_is_blocked_during_test(self):
        g = self.gui
        path = self.load([dict(frame="05:8F")])
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            g._files_start_export(path, g._files_extract_records("ALL"), "JSON")
        self.assertEqual(path.read_bytes(), before)
        g._files_show_live()
        g.cts_running = True
        self.assertFalse(g._files_load(path))
        g.cts_running = False
        self.assertEqual(g._files_mode, "LIVE")


if __name__ == "__main__":
    unittest.main()
