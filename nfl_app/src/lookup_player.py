from difflib import get_close_matches
from pathlib import Path
import re

import pandas as pd
import nflreadpy as nfl

from app_paths import EXPORT_DIR

CURRENT_SEASON = nfl.get_current_season()
CAREER_FILE = EXPORT_DIR / "nfl_career_database.xlsx"
SEASON_FILE = EXPORT_DIR / "nfl_latest_season_database.xlsx"
ROSTER_FILE = EXPORT_DIR / "nfl_current_active_rosters.xlsx"
PRESEASON_FILE = EXPORT_DIR / "nfl_current_preseason_database.xlsx"

OFFENSIVE_POSITIONS = {
    "QB", "RB", "FB", "WR", "TE", "C", "G", "OG", "T", "OT", "OL",
}
DEFENSIVE_POSITIONS = {
    "DE", "DT", "NT", "DL", "EDGE", "LB", "ILB", "OLB", "MLB", "DB",
    "CB", "S", "FS", "SS",
}
SPECIAL_TEAMS_POSITIONS = {"K", "PK", "P", "PT", "LS"}
TEAM_ALIASES = {
    "CARDINALS": "ARI", "FALCONS": "ATL", "RAVENS": "BAL", "BILLS": "BUF",
    "PANTHERS": "CAR", "BEARS": "CHI", "BENGALS": "CIN", "BROWNS": "CLE",
    "COWBOYS": "DAL", "BRONCOS": "DEN", "LIONS": "DET", "PACKERS": "GB",
    "TEXANS": "HOU", "COLTS": "IND", "JAGUARS": "JAX", "CHIEFS": "KC",
    "RAIDERS": "LV", "CHARGERS": "LAC", "RAMS": "LAR", "DOLPHINS": "MIA",
    "VIKINGS": "MIN", "PATRIOTS": "NE", "SAINTS": "NO", "GIANTS": "NYG",
    "JETS": "NYJ", "EAGLES": "PHI", "STEELERS": "PIT", "49ERS": "SF",
    "SEAHAWKS": "SEA", "BUCCANEERS": "TB", "TITANS": "TEN",
    "COMMANDERS": "WAS",
}

STAT_GROUPS = {
    "QB": [("Games", "games"), ("Seasons Played", "seasons_played"),
           ("Completions", "completions"), ("Attempts", "attempts"),
           ("Completion %", "completion_percentage"),
           ("Passing Yards", "passing_yards"), ("Passing TD", "passing_tds"),
           ("Interceptions", "passing_interceptions"),
           ("Yards/Attempt", "passing_yards_per_attempt"),
           ("Sacks", "sacks_suffered"), ("Rushing Yards", "rushing_yards"),
           ("Rushing TD", "rushing_tds"), ("QB Rating", "passer_rating")],
    "RB": [("Games", "games"), ("Seasons Played", "seasons_played"),
           ("Rush Attempts", "carries"), ("Rushing Yards", "rushing_yards"),
           ("Average", "rushing_average"), ("Rushing TD", "rushing_tds"),
           ("Receptions", "receptions"), ("Receiving Yards", "receiving_yards"),
           ("Receiving TD", "receiving_tds"), ("Total TD", "total_tds")],
    "RECEIVER": [("Games", "games"), ("Seasons Played", "seasons_played"),
                 ("Receptions", "receptions"), ("Receiving Yards", "receiving_yards"),
                 ("Average", "receiving_average"), ("Receiving TD", "receiving_tds"),
                 ("Targets", "targets"), ("Catch %", "catch_percentage"),
                 ("Yards/Game", "receiving_yards_per_game"), ("Total TD", "total_tds")],
    "K": [("Games", "games"), ("Seasons Played", "seasons_played"),
          ("FG Made", "fg_made"), ("FG Attempts", "fg_att"),
          ("FG Long", "fg_long"), ("PAT Made", "pat_made"), ("PAT Attempts", "pat_att")],
    "PUNTER": [("Games", "games"), ("Seasons Played", "seasons_played"),
               ("Punts", "punts"), ("Punt Yards", "punt_yards"),
               ("Average", "punt_average"), ("Long", "punt_long"),
               ("Inside 20", "punts_inside_20")],
    "DEFENSE": [("Games", "games"), ("Seasons Played", "seasons_played"),
                ("Solo Tackles", "def_tackles_solo"),
                ("Assists", "def_tackle_assists"), ("Sacks", "def_sacks"),
                ("Interceptions", "def_interceptions"), ("Passes Defended", "def_pass_defended"),
                ("Forced Fumbles", "def_fumbles_forced"), ("Defensive TD", "def_tds")],
}


def load_databases() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    missing = [str(path) for path in (CAREER_FILE, SEASON_FILE, ROSTER_FILE) if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing database file(s):\n  " + "\n  ".join(missing)
                                + "\nRun: python .\\src\\build_career_database.py")
    return pd.read_excel(CAREER_FILE), pd.read_excel(SEASON_FILE), pd.read_excel(ROSTER_FILE)


def parse_team_command(query: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"(.+?)(off|def|kp)", query.strip(), flags=re.IGNORECASE)
    if not match:
        return None
    team_text, unit = match.groups()
    normalized = re.sub(r"[^A-Za-z0-9]", "", team_text).upper()
    team = TEAM_ALIASES.get(normalized, normalized)
    return team, unit.lower()


def display_team_roster(team: str, unit: str, roster: pd.DataFrame) -> None:
    positions = {
        "off": OFFENSIVE_POSITIONS,
        "def": DEFENSIVE_POSITIONS,
        "kp": SPECIAL_TEAMS_POSITIONS,
    }[unit]
    team_rows = roster[
        (roster["team"].fillna("").astype(str).str.upper() == team)
        & (roster["position"].fillna("").astype(str).str.upper().isin(positions))
    ].copy()
    title = {"off": "OFFENSE", "def": "DEFENSE", "kp": "SPECIAL TEAMS"}[unit]
    print("\n" + "=" * 52)
    print(f"{team} {title} — ACTIVE ROSTER")
    if team_rows.empty:
        print("  No active players found. Check the team code and rebuild the database.")
    else:
        team_rows["position"] = team_rows["position"].astype(str).str.upper()
        team_rows = team_rows.sort_values(["position", "player_display_name"])
        for _, player in team_rows.iterrows():
            print(f"  {player['position']:<5} {player['player_display_name']}")
        print(f"\n  {len(team_rows)} players")
    print("=" * 52)


def choose_player(query: str, career: pd.DataFrame) -> pd.Series | None:
    names = career["player_display_name"].fillna("").astype(str)
    query_lower = query.casefold().strip()
    exact = career[names.str.casefold() == query_lower]
    if len(exact) == 1:
        return exact.iloc[0]

    candidates = career[names.str.casefold().str.contains(query_lower, regex=False)]
    if candidates.empty:
        close_names = get_close_matches(query, names.tolist(), n=8, cutoff=0.55)
        candidates = career[names.isin(close_names)]
    if candidates.empty:
        print("No matching players found.")
        return None
    if len(candidates) == 1:
        return candidates.iloc[0]

    candidates = candidates.head(10).reset_index(drop=True)
    print("\nMatches:")
    for number, row in candidates.iterrows():
        print(f"  {number + 1}. {row['player_display_name']} | {row.get('position', '')} | {row.get('last_team', '')}")
    selection = input("Choose a number (or press Enter to cancel): ").strip()
    if selection.isdigit() and 1 <= int(selection) <= len(candidates):
        return candidates.iloc[int(selection) - 1]
    return None


def stat_group(position: str) -> str:
    position = position.upper()
    if position == "QB": return "QB"
    if position in {"RB", "FB"}: return "RB"
    if position in {"WR", "TE"}: return "RECEIVER"
    if position in {"K", "PK"}: return "K"
    if position in {"P", "PT"}: return "PUNTER"
    # LS stays on DEFENSE: long snappers have no meaningful counting stats, and the
    # defensive group at least shows their games and any special-teams tackles.
    return "DEFENSE"


def format_value(value, column: str = "") -> str:
    if pd.isna(value): return "0"
    if column in {
        "passer_rating", "rushing_average", "receiving_average",
        "passing_yards_per_attempt", "receiving_yards_per_game",
        "punt_average", "punt_net_average",
    }:
        return f"{float(value):.1f}"
    if column in {"completion_percentage", "catch_percentage"}:
        return f"{float(value):.1f}%"
    # Excel values arrive as numpy scalars, which are not instances of int/float, so
    # convert instead of type-checking — otherwise 68411 prints without separators.
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"{int(number):,}" if number.is_integer() else f"{number:,.1f}"


def print_section(title: str, row: pd.Series | None, prefix: str,
                  stats: list[tuple[str, str]]) -> None:
    print(f"\n{title}")
    if row is None:
        print("  No stats available.")
        return
    for label, column in stats:
        if column == "seasons_played" and prefix != "career_":
            continue
        full_column = f"{prefix}{column}"
        value = format_value(row[full_column], column) if full_column in row.index else "N/A"
        print(f"  {label:<20} {value}")


def display_player(career_row: pd.Series, season: pd.DataFrame) -> None:
    season_match = season[season["player_id"] == career_row["player_id"]]
    season_row = None if season_match.empty else season_match.iloc[0]
    position = str(career_row.get("position", ""))
    stats = STAT_GROUPS[stat_group(position)]
    print("\n" + "=" * 52)
    teams = career_row.get("teams_played_for", career_row.get("last_team", ""))
    print(f"{career_row['player_display_name']} | {position}")
    print(f"Teams: {teams}")
    print_section("CAREER (REG SEASON)", career_row, "career_", stats)
    print_section(f"{CURRENT_SEASON} SEASON (REG SEASON)", season_row, f"season_{CURRENT_SEASON}_", stats)
    print("=" * 52)


def main() -> None:
    try:
        career, season, roster = load_databases()
    except (FileNotFoundError, ImportError, ValueError) as error:
        print(error)
        return
    print("NFL Player Lookup (type Q to quit)")
    while True:
        query = input("\nEnter player name: ").strip()
        if query.casefold() == "q": break
        if not query: continue
        team_command = parse_team_command(query)
        if team_command:
            display_team_roster(*team_command, roster)
            continue
        player = choose_player(query, career)
        if player is not None:
            display_player(player, season)


if __name__ == "__main__":
    main()
