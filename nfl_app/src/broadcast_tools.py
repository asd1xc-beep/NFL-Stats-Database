from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd


TEAM_CODE_NORMALIZATION = {
    "LA": "LAR",
    "JAC": "JAX",
    "ARZ": "ARI",
    "STL": "LAR",
    "OAK": "LV",
    "SD": "LAC",
    "SDG": "LAC",
    "GNB": "GB",
    "KAN": "KC",
    "NWE": "NE",
    "NOR": "NO",
    "SFO": "SF",
    "TAM": "TB",
    "WAS": "WAS",
    "WSH": "WAS",
}

TEAM_NAMES = {
    "ARI": "Arizona Cardinals", "ATL": "Atlanta Falcons", "BAL": "Baltimore Ravens",
    "BUF": "Buffalo Bills", "CAR": "Carolina Panthers", "CHI": "Chicago Bears",
    "CIN": "Cincinnati Bengals", "CLE": "Cleveland Browns", "DAL": "Dallas Cowboys",
    "DEN": "Denver Broncos", "DET": "Detroit Lions", "GB": "Green Bay Packers",
    "HOU": "Houston Texans", "IND": "Indianapolis Colts", "JAX": "Jacksonville Jaguars",
    "KC": "Kansas City Chiefs", "LV": "Las Vegas Raiders", "LAC": "Los Angeles Chargers",
    "LAR": "Los Angeles Rams", "MIA": "Miami Dolphins", "MIN": "Minnesota Vikings",
    "NE": "New England Patriots", "NO": "New Orleans Saints", "NYG": "New York Giants",
    "NYJ": "New York Jets", "PHI": "Philadelphia Eagles", "PIT": "Pittsburgh Steelers",
    "SF": "San Francisco 49ers", "SEA": "Seattle Seahawks", "TB": "Tampa Bay Buccaneers",
    "TEN": "Tennessee Titans", "WAS": "Washington Commanders",
}

GRAPHIC_SUGGESTIONS = {
    "Touchdown": ["Scoring Drive", "TD Player Lower Third", "QB Game Line", "Team Scoring Summary", "Extra Point / Scoring Update"],
    "Field Goal": ["Kicker Player Card", "Scoring Drive", "Field Goal Summary", "Updated Score"],
    "Interception": ["Defensive Player Card", "Turnovers Tonight", "QB Passing Line", "Momentum / Scoring Opportunity"],
    "Fumble": ["Turnovers Tonight", "Recovery Player Card", "Drive Summary", "Momentum Graphic"],
    "Sack": ["Defensive Player Card", "Team Sacks Tonight", "Third Down Defense Note", "Offensive Line Pressure Note"],
    "Big Run": ["Running Back Player Card", "Leading Rushers", "Longest Plays Tonight", "Drive Summary"],
    "Big Catch": ["Receiver Player Card", "QB Passing Line", "Longest Plays Tonight", "Drive Summary"],
    "Penalty": ["Team Penalties / Yards", "Drive Impact", "Team Comparison"],
    "Quarter Break": ["Team Comparison", "Scoring Summary", "Game Leaders", "Drive Chart"],
    "Halftime": ["First Half Team Comparison", "Leading Passers", "Leading Rushers", "Leading Receivers", "Scoring Summary", "Turnovers / Penalties"],
    "Player Injury": ["Player Identification", "Depth Chart / Replacement", "Player Career Note"],
    "Two-Minute Warning": ["Game Situation", "Timeouts / Possession", "QB Game Line", "Drive Summary"],
}


def safe_get(row, column: str, default=""):
    if row is None or column not in row.index:
        return default
    value = row[column]
    return default if pd.isna(value) else value


def normalize_team_code(value) -> str:
    if value is None or pd.isna(value):
        return ""
    code = str(value).strip().upper()
    return TEAM_CODE_NORMALIZATION.get(code, code)


def clean_dataframe(df: pd.DataFrame, require_player_id: bool = False) -> pd.DataFrame:
    """Remove unusable player rows and normalize every known team-code column."""
    cleaned = df.copy()
    if "player_display_name" in cleaned.columns:
        cleaned = cleaned.dropna(subset=["player_display_name"])
        cleaned = cleaned[
            cleaned["player_display_name"].astype(str).str.strip().ne("")
        ]
    if require_player_id and "player_id" in cleaned.columns:
        cleaned = cleaned.dropna(subset=["player_id"])
        cleaned = cleaned[cleaned["player_id"].astype(str).str.strip().ne("")]
    for column in ("team", "recent_team", "last_team"):
        if column in cleaned.columns:
            cleaned[column] = cleaned[column].map(normalize_team_code)
    return cleaned.reset_index(drop=True)


def format_number(value) -> str:
    if value is None or pd.isna(value):
        return "0"
    number = float(value)
    return f"{int(number):,}" if number.is_integer() else f"{number:,.1f}"


def ordinal_experience(value) -> str:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return ""
    try:
        season = int(float(value)) + 1
    except (TypeError, ValueError):
        return str(value)
    suffix = "th" if 10 <= season % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(season % 10, "th")
    return f"{season}{suffix} season"


MILESTONE_COUNT_FRACTION = 0.35
MILESTONE_COUNT_FLOOR = 5
MILESTONE_YARDAGE_WINDOW = 200


def milestone_window(column: str, interval: int) -> float:
    """How close to the next round number counts as 'near a milestone'.

    A 35% window is fine for counting stats (TDs, sacks, field goals) but on
    yardage intervals it would flag a passer 1,750 yards out, which is not
    something you would say on air. Yardage milestones therefore use a flat
    one-good-game window instead. Any future `*_yards` candidate inherits the
    tight window automatically, which is the safer default.
    """
    if column.endswith("_yards"):
        return min(MILESTONE_YARDAGE_WINDOW, interval * MILESTONE_COUNT_FRACTION)
    return max(interval * MILESTONE_COUNT_FRACTION, MILESTONE_COUNT_FLOOR)


def milestone_note(position: str, career_row: pd.Series | None) -> str:
    if career_row is None:
        return "No verified career milestone note available."
    position = position.upper()
    candidates = []
    if position == "QB":
        candidates = [("career_passing_yards", 5000, "passing yards"), ("career_passing_tds", 25, "passing TD")]
    elif position in {"RB", "FB"}:
        candidates = [("career_rushing_yards", 1000, "rushing yards"), ("career_rushing_tds", 10, "rushing TD")]
    elif position in {"WR", "TE"}:
        candidates = [("career_receiving_yards", 1000, "receiving yards"), ("career_receptions", 100, "receptions"), ("career_receiving_tds", 10, "receiving TD")]
    elif position in {"K", "PK"}:
        candidates = [("career_fg_made", 50, "field goals made")]
    elif position in {"P", "PT"}:
        candidates = [("career_punt_yards", 5000, "punting yards"), ("career_punts", 100, "punts")]
    else:
        candidates = [("career_def_sacks", 10, "sacks"), ("career_def_interceptions", 5, "interceptions")]
    for column, interval, label in candidates:
        if column not in career_row.index or pd.isna(career_row[column]):
            continue
        current = float(career_row[column])
        if current <= 0:
            # A player with none of a stat is not "approaching" its first round number.
            # Without this, every punter/lineman got "Needs 5 interceptions to reach 5".
            continue
        target = (int(current // interval) + 1) * interval
        needed = target - current
        if 0 < needed <= milestone_window(column, interval):
            return f"Needs {format_number(needed)} {label} to reach {target:,} career {label}."
    return "No nearby standard career milestone identified."


def write_game_packet(path: Path, lines: Iterable[str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def packet_filename(home: str, away: str) -> str:
    return f"{date.today().isoformat()}_{home}_vs_{away}_pregame_packet.txt"
