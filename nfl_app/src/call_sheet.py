"""Persistence and validation for per-matchup GFX call sheets.

A call sheet maps each player to the number the GFX system uses to call up that
player's graphic. It is deliberately kept out of the stat databases, which are
fully overwritten by every "Update All Data" run.

The file is keyed by matchup rather than by date: pre-game prep for one game
spans several days, and a dated filename would silently start a fresh, empty
sheet each morning and lose the previous day's work.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import tempfile

from app_paths import EXPORT_DIR


CALL_SHEET_DIR = EXPORT_DIR / "call_sheets"
CALL_SHEET_VERSION = 1


def call_sheet_path(home: str, away: str) -> Path:
    """Stable path for a matchup — no date component, on purpose."""
    return CALL_SHEET_DIR / f"{away}_at_{home}_call_sheet.json"


def normalize_call_up(value) -> str:
    """Canonical form used for duplicate comparison.

    Call-up codes are free text because some CG systems use letter/number codes,
    so "a12" and "A12" address the same graphic and must collide.
    """
    return "" if value is None else str(value).strip().casefold()


def load_call_sheet(home: str, away: str) -> dict[str, dict]:
    """Return {entry_key: entry} for a matchup, or {} when none exists yet.

    A corrupt or unreadable file returns {} rather than raising: losing the typed
    numbers is bad, but blocking the app minutes before air is worse.
    """
    path = call_sheet_path(home, away)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        return {}
    return {
        key: value for key, value in entries.items()
        if isinstance(value, dict) and str(value.get("call_up", "")).strip()
    }


def save_call_sheet(home: str, away: str, entries: dict[str, dict]) -> Path:
    """Write the sheet atomically so an interrupted save cannot corrupt it."""
    path = call_sheet_path(home, away)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CALL_SHEET_VERSION,
        "home": home,
        "away": away,
        "updated": datetime.now().isoformat(timespec="seconds"),
        "entries": {
            key: value for key, value in entries.items()
            if str(value.get("call_up", "")).strip()
        },
    }
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2, ensure_ascii=False)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    return path


def duplicate_keys(entries: dict[str, dict]) -> set[str]:
    """Entry keys whose call-up number is shared with at least one other player.

    Duplicates are detected across both teams, not per team — the same call-up
    number pointing at two players is equally wrong regardless of which side
    they are on.
    """
    by_value: dict[str, list[str]] = {}
    for key, entry in entries.items():
        value = normalize_call_up(entry.get("call_up"))
        if value:
            by_value.setdefault(value, []).append(key)
    return {key for keys in by_value.values() if len(keys) > 1 for key in keys}


def duplicate_values(entries: dict[str, dict]) -> list[str]:
    """Human-readable list of the call-up numbers that are assigned twice."""
    by_value: dict[str, list[str]] = {}
    for key, entry in entries.items():
        value = normalize_call_up(entry.get("call_up"))
        if value:
            by_value.setdefault(value, []).append(str(entry.get("call_up", "")).strip())
    return sorted({originals[0] for originals in by_value.values() if len(originals) > 1})
