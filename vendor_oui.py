"""Local IEEE OUI lookup for three-byte CEC Vendor IDs."""

from __future__ import annotations

import csv
from pathlib import Path

from runtime_paths import application_directory, resource_directories


class OuiLookup:
    """Load an IEEE MA-L CSV and resolve CEC's three-byte Vendor ID."""

    def __init__(self) -> None:
        self.path: Path | None = None
        self.entries: dict[str, str] = {}
        self.error: str = ""

    @staticmethod
    def normalize(value: object) -> str | None:
        if isinstance(value, int):
            number = value
        else:
            text = str(value).strip().upper()
            for prefix in ("0X",):
                if text.startswith(prefix):
                    text = text[len(prefix):]
            text = text.replace(":", "").replace("-", "").replace(" ", "")
            if len(text) != 6:
                return None
            try:
                number = int(text, 16)
            except ValueError:
                return None
        if not 0 <= number <= 0xFFFFFF:
            return None
        return f"{number:06X}"

    @staticmethod
    def format_raw(value: object) -> str:
        normalized = OuiLookup.normalize(value)
        if normalized is None:
            return str(value).strip()
        return ":".join(normalized[index:index + 2] for index in range(0, 6, 2))

    def load(self, path: str | Path) -> bool:
        candidate = Path(path).expanduser()
        entries: dict[str, str] = {}
        try:
            with candidate.open("r", newline="", encoding="utf-8-sig", errors="replace") as handle:
                for row in csv.DictReader(handle):
                    assignment = self.normalize(row.get("Assignment", ""))
                    organization = " ".join(str(row.get("Organization Name", "")).split())
                    if assignment and organization:
                        entries[assignment] = organization
        except (OSError, csv.Error, UnicodeError) as exc:
            self.error = str(exc)
            return False
        if not entries:
            self.error = "CSV 中没有可用的 6 位 Assignment/OUI 记录。"
            return False
        self.path = candidate
        self.entries = entries
        self.error = ""
        return True

    def load_default(self, preferred: str | Path | None = None) -> bool:
        tool_path = application_directory() / "oui.csv"
        downloads_path = Path.home() / "Downloads" / "oui.csv"
        candidates: list[Path] = []
        if preferred:
            preferred_path = Path(preferred).expanduser()
            # A previous version automatically saved Downloads/oui.csv.  Treat
            # that value as the old default so the bundled tool-directory copy
            # takes precedence, while preserving an explicitly chosen table.
            try:
                is_old_default = preferred_path.resolve() == downloads_path.resolve()
            except OSError:
                is_old_default = False
            if not is_old_default:
                candidates.append(preferred_path)
        candidates.extend(
            (
                tool_path,
                *(directory / "oui.csv" for directory in resource_directories()),
                downloads_path,
            )
        )
        for candidate in candidates:
            if candidate.is_file() and self.load(candidate):
                return True
        self.path = None
        self.entries = {}
        return False

    def lookup(self, value: object) -> str | None:
        normalized = self.normalize(value)
        return self.entries.get(normalized) if normalized else None

    def describe(self, value: object) -> str:
        raw = self.format_raw(value)
        organization = self.lookup(value)
        return f"{raw}（{organization}）" if organization else f"{raw}（未知厂商）"
