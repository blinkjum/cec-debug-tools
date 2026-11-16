"""Source/frozen portable resource resolution, without loading a native DLL."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import runtime_paths
from libcec_backend import LibCecBackend
from vendor_oui import OuiLookup


class RuntimePathsTests(unittest.TestCase):
    def test_source_resources_still_use_project_directory(self):
        self.assertEqual(runtime_paths.application_directory(), ROOT)
        self.assertEqual(runtime_paths.resource_directories(), [ROOT])

    def test_frozen_app_prefers_executable_directory_to_internal_resources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "中文 portable"
            internal = root / "_internal"
            internal.mkdir(parents=True)
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(root / "CEC-Debug-Tools.exe")), \
                 patch.object(sys, "_MEIPASS", str(internal), create=True):
                self.assertEqual(runtime_paths.application_directory(), root)
                self.assertEqual(runtime_paths.resource_directories(), [root, internal])
                candidates = LibCecBackend.bundled_dlls()
                self.assertLess(candidates.index(root / "cec.dll"), candidates.index(internal / "cec.dll"))

    def test_oui_loads_next_to_frozen_exe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "oui.csv").write_text("Assignment,Organization Name\n080046,Sony Corporation\n", encoding="utf-8")
            with patch.object(sys, "frozen", True, create=True), \
                 patch.object(sys, "executable", str(root / "CEC-Debug-Tools.exe")), \
                 patch.object(sys, "_MEIPASS", str(root / "_internal"), create=True):
                lookup = OuiLookup()
                self.assertTrue(lookup.load_default(root / "missing-old-setting.csv"))
                self.assertEqual(lookup.path, root / "oui.csv")
                self.assertEqual(lookup.lookup(0x080046), "Sony Corporation")
