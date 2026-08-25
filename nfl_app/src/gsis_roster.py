"""Read official GSIS game rosters and overlay them on the working roster.

GSIS is the league's own system, so its per-game ROSTER.xml outranks every other
source the app has: verified membership, jersey numbers, positions, and GSIS
player ids for the two teams actually dressing that night. It is also the only
source that disambiguates shared jersey numbers, which it does with an
offense/defense/specialist suffix (``35D`` and ``35O`` are two different players
both wearing 35).

The file covers one game, so this is applied as a runtime overlay on the two
teams in the current matchup rather than folded into the rebuilt databases. A
font coordinator can drop the file in on game day and pick it up without
re-running a full data update.

Two ways in, both the same format:

* the GSIS Real-time Stats Exporter, which writes ``<stamp>_<Club>_ROSTER.xml``
* the GSIS website, which serves the same file as plain ``ROSTER.xml`` at
  ``/{season}/{type}/{week}/{gamekey}/ROSTER.xml``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import pandas as pd

from broadcast_tools import normalize_team_code


# Both naming conventions above. The exporter prefixes a timestamp and club, the
# website does not, so a bare `ROSTER.xml` has to match too.
ROSTER_FILE_PATTERNS = ("*_ROSTER.xml", "ROSTER.xml")

# GSIS marks a shared number with a trailing unit letter: 35D / 35O / 19S.
JERSEY_PATTERN = re.compile(r"^\s*(\d{1,2})\s*([A-Za-z])?\s*$")

JERSEY_UNIT_LABELS = {"D": "defense", "O": "offense", "S": "specialist"}

ROSTER_SOURCE = "GSIS official"

# Per the GSIS Stats Exporter documentation. These are recorded against a
# specific game and several of them are only knowable once it has been played,
# so treat them as "what happened in that game", not "what will happen tonight".
GSIS_STATUS_LABELS = {
    "S": "Started",
    "P": "Played (substitution)",
    "X": "Active, did not play",
    "I": "Injured, did not play",
    "N": "Not active",
    "R": "Injured reserve",
}


def status_label(code) -> str:
    """Human-readable form of a GSIS game status code."""
    code = str(code or "").strip().upper()
    return GSIS_STATUS_LABELS.get(code, code)


def status_breakdown(rows: list[dict]) -> dict[str, int]:
    """Count players per status label, most common first."""
    counts: dict[str, int] = {}
    for row in rows:
        label = status_label(row.get("gsis_status"))
        if label:
            counts[label] = counts.get(label, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


@dataclass
class GsisGameRoster:
    """One parsed GSIS ROSTER.xml."""

    path: Path
    game_key: str = ""
    season: str = ""
    season_type: str = ""
    week: str = ""
    game_date: str = ""
    home_team: str = ""
    away_team: str = ""
    stamped_at: datetime | None = None
    players: list[dict] = field(default_factory=list)

    @property
    def teams(self) -> list[str]:
        return [code for code in (self.home_team, self.away_team) if code]

    @property
    def label(self) -> str:
        parts = [f"{self.away_team} at {self.home_team}"]
        if self.season and self.season_type and self.week:
            parts.append(f"{self.season} {self.season_type} wk {self.week}")
        if self.stamped_at:
            parts.append(self.stamped_at.strftime("%b %d %I:%M %p UTC"))
        return " — ".join(parts)


def find_roster_files(folder: str | Path) -> list[Path]:
    """Every GSIS roster file under a folder, newest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found: dict[Path, None] = {}
    for pattern in ROSTER_FILE_PATTERNS:
        for path in folder.rglob(pattern):
            found[path] = None
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


def split_jersey(value) -> tuple[int | None, str]:
    """Return (number, unit-suffix) for a GSIS jersey value.

    ``"35D"`` -> ``(35, "D")``. The suffix is what tells two players wearing the
    same number apart, so it is kept rather than thrown away.
    """
    match = JERSEY_PATTERN.match(str(value or ""))
    if not match:
        return None, ""
    return int(match.group(1)), (match.group(2) or "").upper()


def _parse_stamp(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    # GSIS stamps sub-second precision that fromisoformat cannot always take.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text.replace("Z", "+00:00"))
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_roster_file(path: str | Path) -> GsisGameRoster:
    """Parse a GSIS ROSTER.xml into game metadata plus player rows."""
    path = Path(path)
    root = ET.parse(path).getroot()
    if root.tag != "RosterFile":
        raise ValueError(f"Not a GSIS roster file (root element is {root.tag!r}): {path.name}")

    roster = GsisGameRoster(path=path, stamped_at=_parse_stamp(root.attrib.get("DateTimeStampUTC")))
    header = root.find("GameKey")
    club_codes: dict[str, str] = {}
    if header is not None:
        attrs = header.attrib
        roster.game_key = str(attrs.get("GameKey", ""))
        roster.season = str(attrs.get("Season", ""))
        roster.season_type = str(attrs.get("SeasonType", ""))
        roster.week = str(attrs.get("Week", ""))
        roster.game_date = str(attrs.get("GameDate", ""))
        roster.home_team = normalize_team_code(attrs.get("HomeClubCode"))
        roster.away_team = normalize_team_code(attrs.get("VisitClubCode"))
        # ClubKey is the only thing on each Player row tying it to a team.
        for key_attr, code in (
            ("HomeClubKey", roster.home_team), ("VisitClubKey", roster.away_team)
        ):
            club_key = str(attrs.get(key_attr, "")).strip()
            if club_key and code:
                club_codes[club_key] = code

    for element in root.findall("Player"):
        attrs = element.attrib
        team = club_codes.get(str(attrs.get("ClubKey", "")).strip(), "")
        first = str(attrs.get("FirstName", "")).strip()
        last = str(attrs.get("LastName", "")).strip()
        name = " ".join(part for part in (first, last) if part) or str(attrs.get("Name", "")).strip()
        if not name:
            continue
        number, unit = split_jersey(attrs.get("JerseyNumber"))
        roster.players.append({
            "team": team,
            "player_display_name": name,
            "position": str(attrs.get("Position", "")).strip().upper(),
            "player_id": str(attrs.get("GSISPlayer_ID", "")).strip(),
            "jersey_number": number,
            "jersey_raw": str(attrs.get("JerseyNumber", "")).strip(),
            "jersey_unit": unit,
            "gsis_status": str(attrs.get("Status", "")).strip().upper(),
            "gsis_short_name": str(attrs.get("Name", "")).strip(),
            "gsis_verified": str(attrs.get("IsVerified", "")).strip().lower() == "true",
        })
    return roster


def roster_rows(roster: GsisGameRoster) -> list[dict]:
    """GSIS players shaped like the app's roster frame."""
    stamp = roster.stamped_at.isoformat(timespec="seconds") if roster.stamped_at else ""
    rows = []
    for player in roster.players:
        if not player["team"]:
            continue
        rows.append({
            "team": player["team"],
            "player_display_name": player["player_display_name"],
            "position": player["position"],
            "depth_chart_position": player["position"],
            "player_id": player["player_id"],
            "jersey_number": player["jersey_number"],
            "jersey_raw": player["jersey_raw"],
            "jersey_unit": player["jersey_unit"],
            # The GSIS letter codes are game-day designations, not the ACT/DEV
            # vocabulary the rest of the app uses, so they are kept verbatim in
            # their own column rather than forced into roster_status.
            "gsis_status": player["gsis_status"],
            "gsis_verified": player["gsis_verified"],
            "roster_source": ROSTER_SOURCE,
            "roster_source_fetched_at": stamp,
        })
    return rows


def apply_to_roster(roster: pd.DataFrame, gsis: GsisGameRoster,
                    enrich: bool = True) -> tuple[pd.DataFrame, dict]:
    """Replace the GSIS game's two teams in `roster` with the official rows.

    Only those teams are touched — the rest of the league keeps whatever source
    it already had. Bio fields (college, height, experience, headshot) are copied
    across from the existing row by player_id where one exists, since GSIS does
    not carry them.
    """
    rows = roster_rows(gsis)
    teams = {row["team"] for row in rows}
    if not rows or not teams:
        raise ValueError(f"{gsis.path.name} contained no usable player rows")

    frame = pd.DataFrame(rows)
    report = {
        "teams": sorted(teams),
        "players": len(rows),
        "per_team": {team: sum(1 for row in rows if row["team"] == team) for team in sorted(teams)},
        "missing_player_id": sum(1 for row in rows if not row["player_id"]),
        "shared_numbers": sorted(
            {row["jersey_raw"] for row in rows if row["jersey_unit"]}
        ),
        "no_jersey": [row["player_display_name"] for row in rows if row["jersey_number"] is None],
        "enriched": 0,
        "replaced": 0,
        "label": gsis.label,
        "status_counts": status_breakdown(rows),
    }

    if roster is None or roster.empty:
        return frame, report

    if enrich and "player_id" in roster.columns:
        carry = [
            column for column in (
                "height", "weight", "college", "years_experience", "birth_date",
                "headshot_url", "rookie_year", "injury_status", "injury_body_part",
                "injury_notes",
            )
            if column in roster.columns
        ]
        if carry:
            existing = roster.dropna(subset=["player_id"]).copy()
            existing["player_id"] = existing["player_id"].astype(str)
            lookup = existing.drop_duplicates(subset=["player_id"]).set_index("player_id")[carry]
            frame = frame.join(lookup, on="player_id")
            report["enriched"] = int(frame[carry].notna().any(axis=1).sum()) if carry else 0

    report["replaced"] = int((roster["team"].isin(teams)).sum())
    remainder = roster[~roster["team"].isin(teams)]
    combined = pd.concat([remainder, frame], ignore_index=True, sort=False)
    return combined.reset_index(drop=True), report
