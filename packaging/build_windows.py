"""Build and verify an isolated, relocatable Windows x64 application folder."""
from datetime import datetime, timedelta, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from libcec_backend import LibCecBackend

SOURCE_FILES = (
    "cec-client-gui.py", "libcec_backend.py", "vendor_oui.py", "cts_testcases.py",
    "runtime_paths.py", "portable_selftest.py", "cec_workbench.py", "cec_workbench_ui.py", "README.md", "run-cec-client-gui.cmd",
    "cec_capture_tools.py", "capture_files_ui.py", "capture_layout.py", "capture_layout_ui.py", "workspace_ui.py", "cec_scripts.py",
    "build-libcec-custom.ps1", "build-windows.ps1", ".gitignore", ".gitmodules",
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def git(*arguments, directory=ROOT):
    return subprocess.check_output(["git", "-C", str(directory), *arguments], text=True).strip()


def main():
    if sys.platform != "win32" or platform.architecture()[0] != "64bit":
        raise RuntimeError("Build with 64-bit Python on Windows")
    import pefile
    tool_revision = git("rev-parse", "HEAD")
    native_revision = git("rev-parse", "HEAD", directory=ROOT / "libcec-upstream")
    native_dirty = git("status", "--porcelain", "--untracked-files=no", directory=ROOT / "libcec-upstream")
    if native_dirty:
        raise RuntimeError("Commit native libCEC changes first so the DLL source archive is exact")
    dll = next((path for path in LibCecBackend.bundled_dlls() if path.is_file()), None)
    if dll is None:
        raise FileNotFoundError("Build the custom libCEC DLL first")
    pe = pefile.PE(str(dll))
    if pe.FILE_HEADER.Machine != 0x8664:
        raise RuntimeError("The packaged native DLL must be x64")
    exports = {symbol.name for symbol in pe.DIRECTORY_ENTRY_EXPORT.symbols}
    for name in (b"libcec_set_test_ack_mask", b"libcec_get_test_ack_mask", b"libcec_set_callbacks"):
        if name not in exports:
            raise RuntimeError(f"Missing native export: {name!r}")
    native_imports = sorted(entry.dll.decode("ascii") for entry in pe.DIRECTORY_ENTRY_IMPORT)
    pe.close()
    work = ROOT / "work"
    work.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="windows-package-", dir=work))
    # Exercise non-ASCII/space-containing application paths in the actual EXE.
    dist_stage = stage / "便携目录 空格"
    command = [sys.executable, "-m", "PyInstaller", "--onedir", "--windowed", "--noupx",
               "--noconfirm", "--name", "CEC-Debug-Tools", "--distpath", str(dist_stage),
               "--workpath", str(stage / "build"), "--specpath", str(stage / "spec"),
               "--paths", str(ROOT), "--version-file", str(ROOT / "packaging/windows-version.txt"),
               str(ROOT / "cec-client-gui.py")]
    subprocess.run(command, cwd=ROOT, check=True)
    product = dist_stage / "CEC-Debug-Tools"
    shutil.copy2(dll, product / "cec.dll")
    shutil.copy2(ROOT / "oui.csv", product / "oui.csv")
    shutil.copy2(ROOT / "packaging/README-portable.txt", product / "README.txt")
    licenses = product / "licenses"
    licenses.mkdir()
    shutil.copy2(ROOT / "libcec-upstream/LICENSE.md", licenses / "libcec-LICENSE.md")
    shutil.copy2(Path(sys.base_prefix) / "LICENSE.txt", licenses / "Python-LICENSE.txt")
    toolchain = ROOT / "work/build-tools/llvm-mingw-20261006-ucrt-x86_64"
    if (toolchain / "LICENSE.TXT").is_file():
        shutil.copy2(toolchain / "LICENSE.TXT", licenses / "LLVM-MinGW-LICENSE.TXT")
    for license_path in (toolchain / "x86_64-w64-mingw32/share/mingw32").glob("COPYING*"):
        if license_path.is_file():
            shutil.copy2(license_path, licenses / ("MinGW-" + license_path.name))
    for license_path in (Path(sys.base_prefix) / "tcl").glob("*/license.terms"):
        shutil.copy2(license_path, licenses / f"{license_path.parent.name}-license.terms")
    for entry in metadata.distribution("pyinstaller").files:
        if entry.name.upper().startswith(("LICENSE", "COPYING")):
            path = metadata.distribution("pyinstaller").locate_file(entry)
            if path.is_file():
                shutil.copy2(path, licenses / f"PyInstaller-{entry.name}")
    source = product / "source"
    source.mkdir()
    shutil.copy2(ROOT / "packaging/README-rebuild.txt", source / "README.txt")
    subprocess.run(["git", "-C", str(ROOT / "libcec-upstream"), "archive", "--format=tar.gz",
                    "--prefix=libcec-source/", "--output", str(source / "libcec-source.tar.gz"),
                    native_revision], check=True)
    tool_source = source / "cec-debug-tools"
    for relative in SOURCE_FILES:
        target = tool_source / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    for folder in ("packaging", "tests"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                target = tool_source / path.relative_to(ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    source_hashes = {path.relative_to(tool_source).as_posix(): digest(path)
                     for path in sorted(tool_source.rglob("*")) if path.is_file()}
    info = {"product": "CEC Debug Tools", "platform": "Windows x64", "version": "2026.11.17.4",
            "built_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "python": platform.python_version(), "pyinstaller": metadata.version("pyinstaller"),
            "tool_base_commit": tool_revision, "libcec_commit": native_revision,
            "original_dll": dll.name, "dll_sha256": digest(dll), "oui_sha256": digest(ROOT / "oui.csv"),
            "native_imports": native_imports, "tool_source_sha256": source_hashes,
            "signed": False, "usb_driver_included": False}
    (product / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    clean_environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "LIBCEC_DLL", "PULSE_EIGHT_CEC_DLL"):
        clean_environment.pop(name, None)
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    clean_environment["PATH"] = str(system_root / "System32") + os.pathsep + str(system_root)
    empty_cwd = stage / "空工作目录"
    empty_cwd.mkdir()
    report_path = stage / "self-test.json"
    subprocess.run([str(product / "CEC-Debug-Tools.exe"), "--self-test-report", str(report_path)],
                   cwd=empty_cwd, env=clean_environment, timeout=60, check=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report["ok"] or not report["frozen"] or report["adapter_opened"]:
        raise RuntimeError(f"Portable smoke check failed: {report}")
    if Path(report["dll"]).resolve() != (product / "cec.dll").resolve():
        raise RuntimeError("EXE loaded a DLL outside its portable directory")
    if Path(report["oui"]).resolve() != (product / "oui.csv").resolve():
        raise RuntimeError("EXE loaded the OUI table from outside its portable directory")
    if report["dll_sha256"] != info["dll_sha256"]:
        raise RuntimeError("Packaged DLL checksum mismatch")
    info["offline_self_test"] = {key: report[key] for key in (
        "ok", "frozen", "adapter_opened", "test_ack_api", "oui_entries", "sony_vendor",
        "capture_test_frame", "capture_rows", "cts_cases", "gui_errors",
        "workbench_commands", "workbench_search_matches", "workbench_annotation",
        "highlight_rules", "highlight_custom_color", "capture_details", "raw_frame_loaded",
        "offline_import", "length_check", "batch_export", "search_context_navigation",
        "removable_conditions", "column_layout", "match_reasons", "task_pages", "default_monitor",
        "script_compatibility", "shared_send_fields")}
    (product / "build-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    checksums = [f"{digest(path)}  {path.relative_to(product).as_posix()}" for path in sorted(product.rglob("*"))
                 if path.is_file()]
    (product / "SHA256SUMS.txt").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    label = "CEC-Debug-Tools-Windows-x64-" + datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d-%H%M%S")
    destination = output / label
    shutil.copytree(product, destination)
    archive = output / (label + ".zip")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as package:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                package.write(path, path.relative_to(output).as_posix())
    (output / (archive.name + ".sha256")).write_text(f"{digest(archive)}  {archive.name}\n", encoding="ascii")
    print(json.dumps({"folder": str(destination), "zip": str(archive), "zip_bytes": archive.stat().st_size,
                      "dll": dll.name, "self_test": info["offline_self_test"], "report": str(report_path)},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
