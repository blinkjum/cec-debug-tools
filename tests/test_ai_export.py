"""AI bundle evidence preservation, version provenance and nonblocking GUI exports."""
import ctypes
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app_version import APP_VERSION
from capture_ai_export import (CEC_PROBLEM_PRESETS, apply_problem_preset, collect_versions,
                               sha256_file, windows_file_versions, write_ai_package)
from libcec_backend import LibCecBackend, LibCecConfiguration


class AIBundleTests(unittest.TestCase):
    def setUp(self):
        scratch = ROOT / "work/ai-export-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def context(self):
        return {"snapshot_at": "2026-11-18T12:00:00.123+08:00",
                "capture": {"capture_session_id": "test-session", "view_mode": "LIVE", "pending_native_events_at_snapshot": 2},
                "connection": {"connected": False, "monitor_only_active": None},
                "version_context": {"application_directory": str(self.root)}}

    def test_zip_is_lossless_self_contained_and_every_payload_has_a_hash(self):
        records = [dict(seq=n, time="12:00:00.123", frame="05:44:6D", timestamp_ms=100,
                        status="ACK", direction="RX", note="按键\n测试", bookmark=True, tags=["ARC"],
                        capture_session_id="test-session", custom_extra={"keep": True}) for n in (1, 2)]
        path = self.root / "中文 分析包.zip"
        write_ai_package(path, records, self.context(), {"title": "验证按键", "steps": "#1 按下电源\n#2 放开"})
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), {"capture.json", "connection.json", "versions.json", "operation.md",
                                                       "analysis_prompt.md", "README.md", "manifest.json"})
            self.assertEqual(json.loads(archive.read("capture.json")), records)
            manifest = json.loads(archive.read("manifest.json"))
            self.assertEqual(manifest["capture"]["record_count"], 2)
            self.assertEqual(manifest["capture"]["pending_native_events_at_snapshot"], 2)
            self.assertEqual(manifest["capture"]["first_record"]["seq"], 1)
            self.assertEqual(manifest["capture"]["last_record"]["seq"], 2)
            for name, info in manifest["files"].items():
                data = archive.read(name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), info["sha256"])
                self.assertEqual(len(data), info["size_bytes"])
            operations = archive.read("operation.md").decode()
            self.assertIn("#1 按下电源\n#2 放开", operations)
            self.assertIn("（未填写）", operations)
            self.assertIn("负值不能单独证明", archive.read("analysis_prompt.md").decode())
            self.assertEqual(json.loads(archive.read("versions.json"))["application"]["version"], APP_VERSION)

    def test_failed_export_keeps_existing_zip_and_cleans_partial_files(self):
        target = self.root / "keep.zip"
        target.write_bytes(b"existing archive")
        with patch("capture_ai_export.collect_versions", side_effect=OSError("injected disk failure")):
            with self.assertRaises(OSError):
                write_ai_package(target, [dict(frame="05")], self.context(), {})
        self.assertEqual(target.read_bytes(), b"existing archive")
        self.assertEqual(list(self.root.iterdir()), [target])

    def test_actual_dll_has_priority_over_configured_dll_and_sources_are_allowlisted(self):
        loaded = self.root / "loaded.dll"
        loaded.write_bytes(b"actual loaded bytes")
        (self.root / "cec-client-gui.py").write_text("# tool", encoding="utf-8")
        (self.root / "CEC Checker private.bin").write_bytes(b"private")
        context = dict(application_directory=str(self.root), dll_loaded_path=str(loaded), dll_requested_path="wrong.dll")
        with patch("capture_ai_export.windows_file_versions", return_value={"file_version": "8.1.6.0", "product_version": "8.1.6.0"}):
            versions = collect_versions(context)
        self.assertEqual(versions["libcec_dll"]["sha256"], sha256_file(loaded))
        self.assertTrue(versions["libcec_dll"]["loaded"])
        self.assertEqual(versions["libcec_dll"]["product_version"], "8.1.6.0")
        self.assertEqual(set(versions["application"]["source_files_sha256"]), {"cec-client-gui.py"})

    @unittest.skipUnless(sys.platform == "win32", "Windows PE metadata")
    def test_fixed_pe_version_is_read_from_real_custom_libcec(self):
        dll = next((path for path in LibCecBackend.bundled_dlls() if path.is_file()), None)
        if not dll:
            self.skipTest("No local custom DLL")
        versions = windows_file_versions(dll)
        self.assertRegex(versions["product_version"], r"^\d+\.\d+\.\d+\.\d+$")

    def test_configuration_cache_copies_native_callback_without_querying_adapter(self):
        backend = LibCecBackend()
        backend._create_callbacks()
        config = LibCecConfiguration()
        config.serverVersion = (8 << 16) | (1 << 8) | 6
        config.strDeviceName = b"TestTV"
        config.logicalAddresses.addresses[0] = 1
        config.iPhysicalAddress = 0x1000
        config.bMonitorOnly = 1
        backend._callbacks.configurationChanged(None, ctypes.byref(config))
        config.iPhysicalAddress = 0x2000
        self.assertEqual(backend.cached_configuration["physical_address_hex"], "1000")
        self.assertEqual(backend.cached_configuration["libcec_server_version"], "8.1.6")
        self.assertEqual(backend.cached_configuration["logical_addresses"], ["0"])
        copy = backend.cached_configuration
        copy["logical_addresses"].append("5")
        self.assertEqual(backend.cached_configuration["logical_addresses"], ["0"])
        self.assertEqual(backend.drain_events()[0]["kind"], "configuration")

    def test_problem_presets_switch_without_losing_edited_notes_or_duplicating_focus(self):
        self.assertEqual(len({preset["id"] for preset in CEC_PROBLEM_PRESETS}), 14)
        for preset in CEC_PROBLEM_PRESETS:
            result = apply_problem_preset({"steps": "现场步骤", "actual": "实测异常"}, preset["id"])
            self.assertEqual(result["title"], preset["title"])
            self.assertEqual(result["expected"], preset["expected"])
            self.assertEqual(result["questions"], preset["questions"])
            self.assertEqual(apply_problem_preset(result, preset["id"]), result)
            self.assertEqual(result["steps"], "现场步骤")
        old, new = CEC_PROBLEM_PRESETS[:2]
        result = apply_problem_preset({}, old["id"])
        result.update(title="手写名称", expected="手写预期", actual="手写现象",
                      questions=result["questions"] + "\n\n我还想比较两次测试")
        switched = apply_problem_preset(result, new["id"])
        for key in ("title", "expected", "actual"):
            self.assertEqual(switched[key], result[key])
        self.assertIn("我还想比较两次测试", switched["questions"])
        self.assertNotIn(old["questions"], switched["questions"])
        self.assertIn(new["questions"], switched["questions"])
        custom = apply_problem_preset(switched, "CUSTOM")
        self.assertEqual(custom["questions"], switched["questions"])


class AIBundleGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("ai_export_gui", ROOT / "cec-client-gui.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        scratch = ROOT / "work/ai-export-gui-tests"
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

    def frame(self, frame, index=1):
        self.gui._handle_backend_event(dict(kind="raw_frame", frame=frame, direction="RX",
                                           raw_status_text="INCOMING", ack=0, raw_timestamp=index * 100))

    def wait_export(self):
        self.assertTrue(self.gui.log_writer.flush(wait=True, timeout=15))
        self.gui._drain_output_queue()
        self.assertFalse(self.gui._ai_busy)

    def test_full_history_hidden_polls_annotations_snapshot_and_background_writer(self):
        g = self.gui
        g.capture_tree.begin_update()
        for i in range(2005):
            self.frame([0x05] if i % 2 else [0x50, 0x90, 0], i)
        g.capture_tree.end_update()
        g.wb_query_vars["source"].set("5")
        g.wb_mode_var.set("FILTER")
        g._wb_apply_query()
        g.wb_annotations.set(g.capture_session_id, g.capture_records[0], bookmark=True, note="上电", tags=["power"])
        g.topology_devices = [dict(la=0, osd="Sony TV", address="0.0.0.0")]
        g.backend._loaded_dll_path = ROOT / "libcec-custom-install/bin/cec-occupancy-75c659799ad5.dll"
        g.cec_path_var.set("not-the-loaded-dll.dll")
        target = self.root / "complete.zip"
        thread_ids = []
        real_write = self.module.write_ai_package
        def observe(path, records, context, operations):
            thread_ids.append(threading.get_ident())
            return real_write(path, records, context, operations)
        with patch.object(self.module, "write_ai_package", side_effect=observe), \
             patch.object(g.backend, "transmit") as transmit, patch.object(g.backend, "lib_info") as info:
            g._ai_start_export(target, {"steps": "按下遥控器"})
            self.frame([0x05, 0x8F], 2006)
            g.wb_annotations.set(g.capture_session_id, g.capture_records[0], bookmark=False, note="later", tags=[])
            self.wait_export()
            transmit.assert_not_called()
            info.assert_not_called()
        self.assertTrue(thread_ids and all(n != threading.get_ident() for n in thread_ids))
        with zipfile.ZipFile(target) as archive:
            records = json.loads(archive.read("capture.json"))
            self.assertEqual(len(records), 2005)
            self.assertEqual(records[0]["note"], "上电")
            self.assertTrue(records[0]["bookmark"])
            self.assertTrue(any(r["frame"] == "05" for r in records))
            self.assertEqual(records[-1]["seq"], 2005)
            connection = json.loads(archive.read("connection.json"))
            self.assertEqual(connection["topology_cached"][0]["osd"], "Sony TV")
            dll = json.loads(archive.read("versions.json"))["libcec_dll"]
            self.assertNotEqual(dll["path"], "not-the-loaded-dll.dll")
        self.assertEqual(len(g.capture_records), 2006)

    def test_dialog_saves_notes_and_offline_export_does_not_mix_live_records(self):
        g = self.gui
        self.frame([0x05, 0x8F])
        offline = self.root / "offline.json"
        offline.write_text(json.dumps([dict(seq=42, frame="50:90:01", note="原始离线")]), encoding="utf-8")
        g._files_load(offline)
        deadline = time.monotonic() + 8
        while g._files_load_busy and time.monotonic() < deadline:
            g.update()
            time.sleep(.005)
        self.assertFalse(g._files_load_busy)
        self.frame([0x05, 0x46], 2)
        with self.assertRaises(ValueError):
            g._ai_start_export(offline, {})
        g._ai_show_export()
        g._ai_note_widgets["steps"].insert("1.0", "拔插 HDMI\n重新上电")
        target = self.root / "offline-analysis.zip"
        with patch("capture_ai_ui.filedialog.asksaveasfilename", return_value=str(target)):
            g._ai_choose_export()
        self.wait_export()
        with zipfile.ZipFile(target) as archive:
            records = json.loads(archive.read("capture.json"))
            self.assertEqual([r["frame"] for r in records], ["50:90:01"])
            self.assertEqual(records[0]["origin_seq"], 42)
            self.assertEqual(json.loads(archive.read("connection.json"))["relation_to_capture"], "OFFLINE_EXPORTER_ONLY")
            self.assertIn("拔插 HDMI\n重新上电", archive.read("operation.md").decode())
        saved = json.loads(g.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["workbench"]["capture_files"]["ai_export"]["steps"], "拔插 HDMI\n重新上电")
        self.assertEqual(len(g._files_live["records"]), 2)
        g._ai_window.destroy()

    def test_failure_restores_buttons_and_next_export_can_succeed(self):
        g = self.gui
        self.frame([0x05, 0x8F])
        with patch.object(self.module, "write_ai_package", side_effect=OSError("disk full")), \
             patch("capture_ai_ui.messagebox.showerror") as error:
            g._ai_start_export(self.root / "failed.zip", {})
            self.wait_export()
            error.assert_called_once()
        self.assertEqual(str(g._ai_export_button["state"]), "normal")
        g._ai_start_export(self.root / "retry.zip", {})
        self.wait_export()
        self.assertTrue((self.root / "retry.zip").is_file())

    def test_native_problem_dropdown_exports_selected_preset_and_remembers_editable_notes(self):
        g = self.gui
        self.frame([0x05, 0x8F])
        g._ai_show_export()
        self.assertEqual(g._ai_problem_combo.winfo_class(), "TCombobox")
        self.assertEqual(str(g._ai_problem_combo["state"]), "readonly")
        g._ai_note_widgets["steps"].insert("1.0", "拔插后上电")
        g._ai_note_widgets["actual"].insert("1.0", "实际仍未唤醒")
        g._ai_problem_combo.current(g._ai_problem_ids.index("POWER"))
        g._ai_problem_combo.event_generate("<<ComboboxSelected>>")
        self.assertIn("联动", g._ai_note_widgets["title"].get("1.0", "end-1c"))
        self.assertIn("8F/90", g._ai_note_widgets["questions"].get("1.0", "end-1c"))
        target = self.root / "preset.zip"
        with patch("capture_ai_ui.filedialog.asksaveasfilename", return_value=str(target)):
            g._ai_choose_export()
        self.wait_export()
        with zipfile.ZipFile(target) as archive:
            operations = archive.read("operation.md").decode("utf-8")
            for text in ("开关机 / 待机联动异常", "非已确认故障原因", "拔插后上电", "实际仍未唤醒"):
                self.assertIn(text, operations)
        saved = json.loads(g.settings_path.read_text(encoding="utf-8"))["workbench"]["capture_files"]
        self.assertEqual(saved["ai_export"]["problem_preset"], "POWER")
        g._ai_window.destroy()
        g._ai_init(saved)
        g._ai_show_export()
        self.assertEqual(g._ai_problem_ids[g._ai_problem_combo.current()], "POWER")
        self.assertEqual(g._ai_note_widgets["steps"].get("1.0", "end-1c"), "拔插后上电")


if __name__ == "__main__":
    unittest.main()
