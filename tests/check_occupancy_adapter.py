"""Opt-in hardware API smoke test; always releases occupancy in finally.

Usage: python tests/check_occupancy_adapter.py --port COM5
Does not reset a DUT or claim CTS compliance. A busy port is reported as such.
"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from libcec_backend import LibCecBackend


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True)
    args = parser.parse_args()
    backend = LibCecBackend()
    report = {"port": args.port, "checks": [], "raw_frame_count": 0}
    result = backend.open(args.port, monitor_only=True, autonomous_mode=False,
                          physical_address=0x2000, device_name="LA Test")
    print("OPEN", result.ok, result.text, flush=True)
    report["open"] = {"ok": result.ok, "text": result.text}
    try:
        if not result.ok:
            report["diagnostics"] = backend.drain_events()
            for event in report["diagnostics"]:
                if event.get("kind") in {"log", "error", "alert"}:
                    print(event.get("text", ""), flush=True)
            return 2
        for mask in (0x20, 0x910, 0):
            result = backend.set_address_occupancy(mask)
            state = backend.address_occupancy()
            passed = result.ok and state.ok and state.value == mask
            print(f"MASK 0x{mask:04X}", passed, state.text, flush=True)
            report["checks"].append({"requested_mask": mask, "accepted_mask": state.value, "ok": passed})
            if not passed:
                return 1
            time.sleep(0.1)
        # Confirm normal transmit/capture still works, using an unregistered
        # source. Only query the TV's physical address; never reset a device.
        query = backend.transmit([0xF0, 0x83])
        report["query"] = {"ok": query.ok, "text": query.text}
        time.sleep(0.2)
        report["raw_frame_count"] = sum(event.get("kind") == "raw_frame" for event in backend.drain_events())
        return 0
    finally:
        if backend.is_open:
            release = backend.set_address_occupancy(0)
            report["release"] = {"ok": release.ok, "text": release.text}
        backend.close()
        report["dll"] = str(backend.dll_path)
        path = ROOT / "work" / "occupancy-adapter-check.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REPORT", path, flush=True)


if __name__ == "__main__":
    sys.exit(main())
