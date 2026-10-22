"""Regression checks for native ACK occupancy and GUI state/evidence routing."""
import ctypes
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from libcec_backend import LibCecBackend


class FakeNative:
    """Adapter acceptance/failure simulation; no textual traffic logs."""
    def __init__(self):
        self.mask = 0
        self.accept = True
        self.supported = True
        self.calls = []

    def set_mask(self, connection, mask):
        self.calls.append(("mask", mask))
        if self.accept:
            self.mask = mask
            return 1
        return -1

    def get_mask(self, connection):
        return self.mask if self.supported else -1

    def monitor(self, connection, enabled):
        self.calls.append(("monitor", enabled))
        return 1


def backend_with_native():
    backend = LibCecBackend()
    native = FakeNative()
    backend._connection = ctypes.c_void_p(1)
    backend._f_test_ack_get = native.get_mask
    backend._f_test_ack_set = native.set_mask
    backend._f_monitor = native.monitor
    backend._f_disable_callbacks = lambda connection: None
    backend._f_close = lambda connection: native.calls.append(("close", None))
    backend._f_destroy = lambda connection: None
    return backend, native


class OccupancyBackendTests(unittest.TestCase):
    def test_multi_address_masks_monitor_first_and_release_without_registration(self):
        backend, native = backend_with_native()
        mask = (1 << 4) | (1 << 8) | (1 << 11)
        self.assertTrue(backend.set_address_occupancy(mask).ok)
        self.assertEqual(native.calls, [("monitor", 1), ("mask", mask)])
        self.assertEqual(backend.occupancy_mask, mask)
        self.assertFalse(backend.set_logical_address(5).ok)
        self.assertFalse(backend.switch_monitoring(False).ok)
        self.assertTrue(backend.switch_monitoring(True).ok)
        self.assertTrue(backend.set_address_occupancy(0).ok)
        self.assertEqual(backend.occupancy_mask, 0)
        self.assertTrue(backend.monitor_only)
        self.assertEqual(native.calls[-1], ("mask", 0))

    def test_rejected_update_keeps_last_adapter_accepted_mask(self):
        backend, native = backend_with_native()
        backend.set_address_occupancy(0x20)
        native.accept = False
        result = backend.set_address_occupancy(0x910)
        self.assertFalse(result.ok)
        self.assertEqual(backend.occupancy_mask, 0x20)
        self.assertFalse(backend.set_address_occupancy(0).ok)
        self.assertEqual(backend.occupancy_mask, 0x20)

    def test_invalid_masks_and_unsupported_adapter_do_not_change_mode(self):
        backend, native = backend_with_native()
        for mask in (-1, 0x8000, 0xFFFF, 0x10000):
            self.assertFalse(backend.set_address_occupancy(mask).ok)
        native.supported = False
        self.assertFalse(backend.set_address_occupancy(0x20).ok)
        self.assertEqual(native.calls, [])
        backend._f_test_ack_set = backend._f_test_ack_get = None
        self.assertIn("DLL", backend.set_address_occupancy(0x20).text)

    def test_disconnect_releases_mask_before_closing(self):
        backend, native = backend_with_native()
        backend.set_address_occupancy(0x20)
        backend.close()
        self.assertEqual(native.calls[-2:], [("mask", 0), ("close", None)])
        self.assertFalse(backend.is_open)
        self.assertEqual(backend.occupancy_mask, 0)

    def test_raw_poll_is_a_complete_header_only_command(self):
        for header in (0x55, 0x44, 0xF5):
            command = LibCecBackend.frame_to_command([header])
            self.assertFalse(command.opcode_set)
            self.assertEqual(command.parameters.size, 0)
            self.assertEqual(LibCecBackend.command_to_frame(command), [header])
        command = LibCecBackend.frame_to_command([0x5F, 0x84, 0x20, 0x00, 0x05])
        self.assertTrue(command.opcode_set)
        self.assertEqual(command.parameters.size, 3)
        with self.assertRaises(ValueError):
            LibCecBackend.frame_to_command([])

    def test_native_poll_callbacks_retain_raw_ack_and_eom(self):
        backend, native = backend_with_native()
        backend._create_callbacks()
        for ack in (0, 1):
            command = LibCecBackend.frame_to_command([0x55])
            command.ack = ack
            backend._callbacks.rawFrame(None, ctypes.byref(command), 0, 1, 100)
            event = backend.drain_events()[0]
            self.assertEqual((event["frame"], event["ack"], event["eom"]), ([0x55], ack, 1))


class OccupancyGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("cec_client_gui_tests", ROOT / "cec-client-gui.py")
        cls.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.module
        spec.loader.exec_module(cls.module)

    def setUp(self):
        with patch.object(self.module.CECClientGUI, "_load_settings", return_value={}):
            self.gui = self.module.CECClientGUI()
        self.gui.withdraw()
        self.gui.backend, self.native = backend_with_native()

    def tearDown(self):
        self.gui.backend.close()
        self.gui.log_writer.close()
        self.gui.destroy()

    def test_ack_occupancy_status_survives_monitor_buttons_and_release(self):
        gui = self.gui
        gui.backend.set_address_occupancy(0x20)
        for event in gui.backend.drain_events():
            gui._handle_backend_event(event)
        self.assertIn("LA 5", gui.occupancy_state_var.get())
        self.assertIn("硬件 ACK", gui.monitor_state_var.get())
        self.assertEqual(str(gui.monitor_off_button["state"]), "disabled")
        gui.backend.set_address_occupancy(0)
        for event in gui.backend.drain_events():
            gui._handle_backend_event(event)
        self.assertEqual(gui.occupancy_state_var.get(), "地址占用：未启用")
        self.assertEqual(str(gui.monitor_off_button["state"]), "normal")

    def test_raw_poll_evidence_and_recovery_export(self):
        gui = self.gui
        gui.backend.set_address_occupancy(0x20)
        for event in gui.backend.drain_events():
            gui._handle_backend_event(event)
        event = {"kind": "raw_frame", "frame": [0x55], "direction": "RX", "ack": 0,
                 "raw_timestamp": 100, "raw_status_text": "INCOMING"}
        gui._handle_backend_event(event)
        gui.backend.set_address_occupancy(0)
        for e in gui.backend.drain_events():
            gui._handle_backend_event(e)
        gui._handle_backend_event(dict(event, ack=1, raw_timestamp=200))
        self.assertEqual(gui.occupancy_poll_counts[5], [1, 1, 0])
        self.assertEqual(gui._parse_frame("55"), [0x55])
        with tempfile.TemporaryDirectory(dir=ROOT / "work") as folder:
            path = Path(folder) / "experiment.json"
            with patch.object(self.module.filedialog, "asksaveasfilename", return_value=str(path)):
                gui._occupancy_export()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["accepted_mask"], 0)
            self.assertEqual(payload["poll_counts"]["5"]["ack"], 1)
            self.assertEqual(payload["poll_counts"]["5"]["nack"], 1)


if __name__ == "__main__":
    unittest.main()
