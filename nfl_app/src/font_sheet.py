"""Build a broadcast font sheet from the app's roster data.

The font sheet template is not a fill-in-the-blanks document — it is a
self-contained engine that renders the whole page from a single JSON block and
computes every call code itself (overrides first, then #0, then shared numbers).

So this module supplies that JSON and does **not** reimplement the numbering
rules. Duplicating them here would create a second source of truth that could
drift from the sheet the operator is actually reading.

The template also verifies its own payload with an FNV-1a signature and replaces
itself with a "do not use it on air" screen when the two disagree, so anything
written here has to be signed correctly.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import os
import re
import tempfile
import unicodedata

from app_paths import EXPORT_DIR, RESOURCE_ROOT


OUR_TEAM = "TB"
FONT_SHEET_DIR = EXPORT_DIR / "font_sheets"
TEMPLATE_FILE = RESOURCE_ROOT / "assets" / "font_sheet_TEMPLATE.html"
OVERRIDES_FILE = RESOURCE_ROOT / "config" / "tb_overrides.json"

# The payload lives in one <script id="sheetdata"> element; data-sig is checked
# against an FNV-1a hash of its raw text when the page loads.
SHEETDATA_PATTERN = re.compile(
    r'(<script id="sheetdata" type="application/json" data-sig=")([0-9a-f]*)("\s*>)(.*?)(</script>)',
    re.DOTALL,
)

# The roster feed's `position` is coarse (OL, DB, LB). `depth_chart_position` is
# closer to the vocabulary the sheet already uses, but not identical, so fold the
# few extra values onto the ones the template writes.
POSITION_ALIASES = {
    "DE": "DL", "DT": "DL",
    "FS": "S", "SS": "S",
    "ILB": "LB", "MLB": "LB",
}

NAME_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\.?$", re.IGNORECASE)


def fnv1a(text: str) -> str:
    """FNV-1a over UTF-16 code units, matching the template's own checker."""
    value = 0x811C9DC5
    for character in text:
        value ^= ord(character)
        value = (value + ((value << 1) + (value << 4) + (value << 7)
                          + (value << 8) + (value << 24))) & 0xFFFFFFFF
    return format(value, "08x")


def canonical_name(name) -> str:
    """Fold a name to a comparison key: no accents, suffixes or punctuation.

    Roster feeds disagree on these ("Dean Patterson IV" vs "Dean Patterson",
    "Nuñez-Roches" vs "Nunez-Roches"), and an override that silently stopped
    matching would hand a confirmed player an amber guess instead.
    """
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", NAME_SUFFIX.sub("", text.strip()).lower())


def load_template(path: Path | None = None) -> str:
    path = path or TEMPLATE_FILE
    if not path.exists():
        raise FileNotFoundError(f"Font sheet template is missing: {path}")
    return path.read_text(encoding="utf-8")


def template_payload(template: str) -> dict:
    """The template's own JSON, used as the base for coaches, colours, prefixes."""
    match = SHEETDATA_PATTERN.search(template)
    if not match:
        raise ValueError('Template has no <script id="sheetdata"> block')
    return json.loads(match.group(4))


def load_overrides(path: Path | None = None) -> dict[str, str]:
    path = path or OVERRIDES_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    overrides = data.get("overrides")
    return overrides if isinstance(overrides, dict) else {}


def _jersey(value) -> int | None:
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return None
    return number if 0 <= number <= 99 else None


def _position(row) -> str:
    for column in ("depth_chart_position", "position"):
        value = row.get(column)
        if value is not None and str(value).strip() and str(value) != "nan":
            code = str(value).strip().upper()
            return POSITION_ALIASES.get(code, code)
    return "—"


def roster_players(roster, team: str) -> list[dict]:
    """Roster rows as the template's {n, nm, p} records, sorted by number."""
    players: list[dict] = []
    if roster is None or getattr(roster, "empty", True):
        return players
    for _, row in roster[roster["team"] == team].iterrows():
        number = _jersey(row.get("jersey_number"))
        if number is None:
            continue  # the sheet is number-driven; unnumbered players have no code
        players.append({
            "n": str(number),
            "nm": str(row.get("player_display_name", "")).strip(),
            "p": _position(row),
        })
    players.sort(key=lambda player: (int(player["n"]), player["nm"]))
    return players


def unnumbered_players(roster, team: str) -> list[str]:
    if roster is None or getattr(roster, "empty", True):
        return []
    return [
        str(row.get("player_display_name", "")).strip()
        for _, row in roster[roster["team"] == team].iterrows()
        if _jersey(row.get("jersey_number")) is None
    ]


def resolve_overrides(players: list[dict],
                      overrides: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Re-key overrides onto the exact names being written into the roster.

    The template looks up D.overrides[player.nm] by exact string, so matching
    canonically here and emitting the roster's own spelling makes that lookup
    correct by construction. Anything that fails to resolve comes back so the
    caller can say so out loud rather than dropping it silently.
    """
    by_canonical = {canonical_name(player["nm"]): player["nm"] for player in players}
    resolved: dict[str, str] = {}
    unmatched: list[str] = []
    for name, base in overrides.items():
        actual = by_canonical.get(canonical_name(name))
        if actual:
            resolved[actual] = str(base)
        else:
            unmatched.append(name)
    return resolved, unmatched


def _tldr(our_count: int, opp_count: int, our_name: str, opp_name: str,
          roster_asof: datetime | None) -> str:
    stamp = (roster_asof or datetime.now()).strftime("%b %d, %I:%M %p")
    return (
        f"<b>Built from the NFL Stats app.</b> Rosters as of {stamp} — "
        f"{our_name} {our_count}, {opp_name} {opp_count}. Jersey index, base codes, "
        "shared-number detection and name traps were all regenerated. "
        "<b>Storylines, the players-to-have-ready tables and the opponent coach "
        "block are not written by a rebuild</b> — type those in, and confirm every "
        "amber code with the op before air."
    )


def build_payload(roster, opponent: str, template: str,
                  overrides: dict[str, str] | None = None,
                  team_names: dict[str, str] | None = None,
                  roster_asof: datetime | None = None) -> tuple[dict, dict]:
    """Assemble the sheetdata payload plus a report of what happened."""
    team_names = team_names or {}
    payload = template_payload(template)
    our_players = roster_players(roster, OUR_TEAM)
    opp_players = roster_players(roster, opponent)
    resolved, unmatched = resolve_overrides(
        our_players, load_overrides() if overrides is None else overrides
    )

    our_name = team_names.get(OUR_TEAM, OUR_TEAM)
    opp_name = team_names.get(opponent, opponent)

    game = dict(payload.get("game", {}))
    game.update({
        "ourTeam": OUR_TEAM, "ourName": our_name,
        "oppTeam": opponent, "oppName": opp_name,
        "title": f"{our_name} vs {opp_name}",
        "tldr": _tldr(len(our_players), len(opp_players), our_name, opp_name, roster_asof),
    })

    # The opponent coach cards carry the previous opponent's names, and a correct
    # backplate with a stale name reads as built. Blank them and mark them todo.
    coaches = []
    for entry in payload.get("coaches", []):
        coach = dict(entry)
        if coach.get("tm") != OUR_TEAM:
            coach["tm"] = opponent
            coach["n"] = ""
            coach["st"] = "todo"
        coaches.append(coach)

    payload.update({
        "game": game,
        "rosters": {OUR_TEAM: our_players, opponent: opp_players},
        "overrides": resolved,
        "coaches": coaches,
        # Storylines and the players-to-have-ready tables are editorial. A rebuild
        # never writes them, and the stale flag keeps the amber banner up until a
        # human replaces them.
        "storylines": [],
        "watch": {OUR_TEAM: [], opponent: []},
        "editorialStale": True,
    })
    report = {
        "our_team": OUR_TEAM,
        "opponent": opponent,
        "our_count": len(our_players),
        "opp_count": len(opp_players),
        "overrides_applied": resolved,
        "overrides_unmatched": unmatched,
        "skipped_no_jersey": (
            unnumbered_players(roster, OUR_TEAM) + unnumbered_players(roster, opponent)
        ),
    }
    return payload, report


def render(template: str, payload: dict) -> str:
    """Splice a signed payload into the template."""
    # `</` is escaped the way the template's own payload escapes it, and the hash
    # must be taken over the escaped text because that is what the page reads back
    # as textContent.
    text = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    signature = fnv1a(text)

    def substitute(match: re.Match) -> str:
        return match.group(1) + signature + match.group(3) + text + match.group(5)

    result, count = SHEETDATA_PATTERN.subn(substitute, template, count=1)
    if not count:
        raise ValueError("Could not find the sheetdata block to replace")
    return result


def font_sheet_path(home: str, away: str) -> Path:
    return FONT_SHEET_DIR / f"{away}_at_{home}_font_sheet.html"


def write_font_sheet(roster, home: str, away: str,
                     team_names: dict[str, str] | None = None,
                     roster_asof: datetime | None = None) -> tuple[Path, dict]:
    """Generate the sheet for the selected matchup and return its path + report."""
    if OUR_TEAM not in (home, away):
        raise ValueError(
            f"The font sheet is built around {OUR_TEAM}. Select {OUR_TEAM} as the home "
            f"or away team (currently {away} at {home})."
        )
    # Whichever side of the matchup Tampa is on, it is always `ourTeam`: the
    # three-digit / four-digit split is a broadcast-side rule, not home/away.
    opponent = away if home == OUR_TEAM else home
    template = load_template()
    payload, report = build_payload(
        roster, opponent, template, team_names=team_names, roster_asof=roster_asof,
    )
    document = render(template, payload)

    path = font_sheet_path(home, away)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as file:
            file.write(document)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    report["path"] = path
    return path, report
