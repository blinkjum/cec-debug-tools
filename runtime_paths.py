"""Locate portable resources independently of the current working directory."""
from pathlib import Path
import sys


def application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_directories() -> list[Path]:
    directories = [application_directory()]
    bundled = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()
    if bundled not in directories:
        directories.append(bundled)
    return directories
