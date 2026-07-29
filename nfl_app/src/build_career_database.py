from pathlib import Path

import nflreadpy as nfl
import polars as pl
import requests

from app_paths import EXPORT_DIR

CURRENT_SEASON = nfl.get_current_season()
PRESEASON_SEASON = nfl.get_current_season(roster=True)
CAREER_SEASONS = list(range(1999, CURRENT_SEASON + 1))
OUTPUT_DIR = EXPORT_DIR
PRESEASON_FILE = OUTPUT_DIR / "nfl_current_preseason_database.xlsx"
TEAM_ROSTER_STATUSES = {
    "ACT",  # Active roster
    "DEV",  # Practice squad/developmental
    "INA",  # Under contract but inactive
    "PUP",  # Physically unable to perform
    "RES",  # Injured/reserve list
    "RSN",  # Non-football injury reserve
    "SUS",  # Suspended but under team control
    "EXE",  # Commissioner exempt
    "E14",  # International player exemption
}

TOTAL_COLUMNS = [
    "games", "completions", "attempts", "passing_yards", "passing_tds",
    "passing_interceptions", "sacks_suffered", "sack_yards_lost", "carries",
    "rushing_yards", "rushing_tds", "rushing_fumbles", "rushing_fumbles_lost",
    "receptions", "targets", "receiving_yards", "receiving_tds",
    "receiving_fumbles", "receiving_fumbles_lost", "special_teams_tds",
    "def_tackles_solo", "def_tackle_assists", "def_sacks", "def_interceptions",
    "def_pass_defended", "def_fumbles_forced", "def_tds", "fg_made", "fg_att",
    "pat_made", "pat_att", "fantasy_points", "fantasy_points_ppr",
]


def load_stats() -> pl.DataFrame:
    print(f"Loading regular-season player stats for {CAREER_SEASONS[0]}-{CAREER_SEASONS[-1]}...")
    stats = nfl.load_player_stats(seasons=CAREER_SEASONS, summary_level="reg")
    print(f"Stats loaded: {stats.height:,} rows.\n")
    return stats


def build_totals(stats: pl.DataFrame, prefix: str) -> pl.DataFrame:
    """Build one row per player and prefix every summed statistic."""
    stats = stats.filter(
        pl.col("player_id").is_not_null()
        & pl.col("player_display_name").is_not_null()
        & (pl.col("player_display_name").str.strip_chars() != "")
    )
    available = [column for column in TOTAL_COLUMNS if column in stats.columns]
    missing = [column for column in TOTAL_COLUMNS if column not in stats.columns]
    if missing:
        print(f"Skipping unavailable columns: {', '.join(missing)}")

    totals = (
        stats.sort(["player_id", "season"])
        .group_by("player_id", maintain_order=True)
        .agg(
            pl.col("player_display_name").drop_nulls().last().alias("player_display_name"),
            pl.col("position").drop_nulls().last().alias("position"),
            pl.col("season").min().alias("first_season"),
            pl.col("season").max().alias("last_season"),
            pl.col("season").n_unique().alias(f"{prefix}seasons_played"),
            pl.col("recent_team").drop_nulls().last().alias("last_team"),
            pl.col("recent_team").drop_nulls().unique(maintain_order=True).str.join(", ").alias("teams_played_for"),
            pl.col("headshot_url").drop_nulls().last().alias("headshot_url"),
            *[pl.col(column).fill_null(0).sum().alias(f"{prefix}{column}") for column in available],
            *(
                [pl.col("fg_long").fill_null(0).max().alias(f"{prefix}fg_long")]
                if "fg_long" in stats.columns else []
            ),
        )
    )
    td_columns = [
        f"{prefix}{column}"
        for column in ("passing_tds", "rushing_tds", "receiving_tds", "special_teams_tds", "def_tds")
        if f"{prefix}{column}" in totals.columns
    ]
    totals = totals.with_columns(
        pl.sum_horizontal([pl.col(column).fill_null(0) for column in td_columns])
        .alias(f"{prefix}total_tds")
    )

    attempts = pl.col(f"{prefix}attempts")
    completions = pl.col(f"{prefix}completions")
    pass_yards = pl.col(f"{prefix}passing_yards")
    pass_tds = pl.col(f"{prefix}passing_tds")
    interceptions = pl.col(f"{prefix}passing_interceptions")
    passer_rating = (
        ((completions / attempts - 0.3) * 5).clip(0, 2.375)
        + ((pass_yards / attempts - 3) * 0.25).clip(0, 2.375)
        + (pass_tds / attempts * 20).clip(0, 2.375)
        + (2.375 - interceptions / attempts * 25).clip(0, 2.375)
    ) / 6 * 100

    return totals.with_columns(
        pl.when(attempts > 0).then(passer_rating.round(1)).otherwise(0.0)
        .alias(f"{prefix}passer_rating"),
        pl.when(attempts > 0).then((completions / attempts * 100).round(1)).otherwise(0.0)
        .alias(f"{prefix}completion_percentage"),
        pl.when(attempts > 0).then((pass_yards / attempts).round(1)).otherwise(0.0)
        .alias(f"{prefix}passing_yards_per_attempt"),
        pl.when(pl.col(f"{prefix}carries") > 0)
        .then((pl.col(f"{prefix}rushing_yards") / pl.col(f"{prefix}carries")).round(1))
        .otherwise(0.0).alias(f"{prefix}rushing_average"),
        pl.when(pl.col(f"{prefix}receptions") > 0)
        .then((pl.col(f"{prefix}receiving_yards") / pl.col(f"{prefix}receptions")).round(1))
        .otherwise(0.0).alias(f"{prefix}receiving_average"),
        pl.when(pl.col(f"{prefix}targets") > 0)
        .then((pl.col(f"{prefix}receptions") / pl.col(f"{prefix}targets") * 100).round(1))
        .otherwise(0.0).alias(f"{prefix}catch_percentage"),
        pl.when(pl.col(f"{prefix}games") > 0)
        .then((pl.col(f"{prefix}receiving_yards") / pl.col(f"{prefix}games")).round(1))
        .otherwise(0.0).alias(f"{prefix}receiving_yards_per_game"),
    ).sort("player_display_name")


def export_database(database: pl.DataFrame, filename: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / filename
    database.write_excel(str(output_file))
    print(f"Exported {database.height:,} players to {output_file}")


def build_active_roster() -> pl.DataFrame:
    """Load all current team-controlled players and standardize roster fields."""
    roster_year = nfl.get_current_season(roster=True)
    print(f"Loading current {roster_year} rosters...")
    roster = nfl.load_rosters()

    def first_column(*names: str) -> str:
        for name in names:
            if name in roster.columns:
                return name
        raise ValueError(f"Roster data is missing all expected columns: {', '.join(names)}")

    team_column = first_column("team", "recent_team")
    name_column = first_column("full_name", "player_name", "player_display_name", "football_name")
    position_column = first_column("position", "depth_chart_position")
    id_column = next((name for name in ("gsis_id", "player_id") if name in roster.columns), None)
    jersey_column = next((name for name in ("jersey_number", "jersey") if name in roster.columns), None)
    optional_roster_fields = {
        "height": ("height",),
        "weight": ("weight",),
        "college": ("college",),
        "years_experience": ("years_exp", "years_experience"),
        "age": ("age",),
        "birth_date": ("birth_date", "birthdate"),
        "headshot_url": ("headshot_url",),
        "depth_chart_position": ("depth_chart_position",),
        "injury_status": ("injury_status",),
        "injury_body_part": ("injury_body_part",),
        "injury_notes": ("injury_notes",),
        "rookie_year": ("rookie_year",),
    }
    available_optional_fields = {
        target: next((source for source in sources if source in roster.columns), None)
        for target, sources in optional_roster_fields.items()
    }

    if "status" in roster.columns:
        roster = roster.filter(pl.col("status").is_in(TEAM_ROSTER_STATUSES))

    return (
        roster.select(
            pl.col(team_column).alias("team"),
            pl.col(name_column).alias("player_display_name"),
            pl.col(position_column).alias("position"),
            *([pl.col(id_column).alias("player_id")] if id_column else []),
            *([pl.col(jersey_column).alias("jersey_number")] if jersey_column else []),
            *([pl.col("status").alias("roster_status")] if "status" in roster.columns else []),
            *[
                pl.col(source).alias(target)
                for target, source in available_optional_fields.items()
                if source is not None
            ],
        )
        .drop_nulls(subset=["team", "player_display_name", "position"])
        .unique()
        .sort(["team", "position", "player_display_name"])
    )


def build_preseason_totals() -> pl.DataFrame:
    """Load current preseason player totals from Sleeper and normalize the columns."""
    print(f"Loading {PRESEASON_SEASON} preseason stats from Sleeper...")
    url = f"https://api.sleeper.com/stats/nfl/{PRESEASON_SEASON}?season_type=pre"
    response = requests.get(url, headers={"User-Agent": "NFL Stats Database/1.0"}, timeout=90)
    response.raise_for_status()

    field_map = {
        "games": "gp", "completions": "pass_cmp", "attempts": "pass_att",
        "passing_yards": "pass_yd", "passing_tds": "pass_td",
        "passing_interceptions": "pass_int", "sacks_suffered": "pass_sack",
        "carries": "rush_att", "rushing_yards": "rush_yd", "rushing_tds": "rush_td",
        "receptions": "rec", "targets": "rec_tgt", "receiving_yards": "rec_yd",
        "receiving_tds": "rec_td", "def_tackles_solo": "idp_tkl_solo",
        "def_tackle_assists": "idp_tkl_ast", "def_sacks": "idp_sack",
        "def_interceptions": "idp_int", "def_pass_defended": "idp_pass_def",
        "def_fumbles_forced": "idp_ff", "def_tds": "idp_def_td",
        "fg_made": "fgm", "fg_att": "fga", "fg_long": "fgm_lng",
        "pat_made": "xpm", "pat_att": "xpa",
    }
    rows = []
    for item in response.json():
        player = item.get("player") or {}
        stats = item.get("stats") or {}
        if not stats.get("gp", 0):
            continue
        first_name = player.get("first_name") or ""
        last_name = player.get("last_name") or ""
        row = {
            "sleeper_player_id": item.get("player_id"),
            "player_display_name": f"{first_name} {last_name}".strip(),
            "position": player.get("position"),
            "team": item.get("team") or player.get("team"),
        }
        for target, source in field_map.items():
            row[f"preseason_{target}"] = stats.get(source, 0) or 0

        attempts = row["preseason_attempts"]
        completions = row["preseason_completions"]
        pass_yards = row["preseason_passing_yards"]
        pass_tds = row["preseason_passing_tds"]
        interceptions = row["preseason_passing_interceptions"]
        if attempts:
            components = [
                max(0, min(2.375, (completions / attempts - 0.3) * 5)),
                max(0, min(2.375, (pass_yards / attempts - 3) * 0.25)),
                max(0, min(2.375, pass_tds / attempts * 20)),
                max(0, min(2.375, 2.375 - interceptions / attempts * 25)),
            ]
            row["preseason_passer_rating"] = round(sum(components) / 6 * 100, 1)
            row["preseason_completion_percentage"] = round(completions / attempts * 100, 1)
            row["preseason_passing_yards_per_attempt"] = round(pass_yards / attempts, 1)
        else:
            row["preseason_passer_rating"] = 0.0
            row["preseason_completion_percentage"] = 0.0
            row["preseason_passing_yards_per_attempt"] = 0.0

        carries = row["preseason_carries"]
        receptions = row["preseason_receptions"]
        targets = row["preseason_targets"]
        games = row["preseason_games"]
        row["preseason_rushing_average"] = round(row["preseason_rushing_yards"] / carries, 1) if carries else 0.0
        row["preseason_receiving_average"] = round(row["preseason_receiving_yards"] / receptions, 1) if receptions else 0.0
        row["preseason_catch_percentage"] = round(receptions / targets * 100, 1) if targets else 0.0
        row["preseason_receiving_yards_per_game"] = round(row["preseason_receiving_yards"] / games, 1) if games else 0.0
        row["preseason_total_tds"] = sum(
            row[f"preseason_{name}"]
            for name in ("passing_tds", "rushing_tds", "receiving_tds", "def_tds")
        )
        rows.append(row)

    if not rows:
        columns = ["sleeper_player_id", "player_display_name", "position", "team"]
        columns += [f"preseason_{name}" for name in field_map]
        columns += [
            "preseason_passer_rating", "preseason_completion_percentage",
            "preseason_passing_yards_per_attempt", "preseason_rushing_average",
            "preseason_receiving_average", "preseason_catch_percentage",
            "preseason_receiving_yards_per_game", "preseason_total_tds",
        ]
        schema = {
            column: pl.Utf8 if column in {
                "sleeper_player_id", "player_display_name", "position", "team"
            } else pl.Float64
            for column in columns
        }
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(rows).sort("player_display_name")


def main() -> None:
    stats = load_stats()
    print("Building career totals...")
    career = build_totals(stats, "career_")
    print(f"Building {CURRENT_SEASON} totals...")
    season = build_totals(stats.filter(pl.col("season") == CURRENT_SEASON), f"season_{CURRENT_SEASON}_")
    roster = build_active_roster()
    preseason = build_preseason_totals()
    export_database(career, "nfl_career_database.xlsx")
    export_database(season, "nfl_latest_season_database.xlsx")
    export_database(roster, "nfl_current_active_rosters.xlsx")
    export_database(preseason, PRESEASON_FILE.name)
    print("Done.")


if __name__ == "__main__":
    main()
