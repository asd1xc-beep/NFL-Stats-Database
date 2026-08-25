from contextlib import contextmanager
from pathlib import Path

import nflreadpy as nfl
from nflreadpy.config import get_config, update_config
import polars as pl
import requests

from app_paths import EXPORT_DIR
from broadcast_tools import TEAM_NAMES
from official_rosters import (
    build_hybrid_roster, load_official_rosters, reject_incomplete_team_pages,
)

CURRENT_SEASON = nfl.get_current_season()
PRESEASON_SEASON = nfl.get_current_season(roster=True)
CAREER_SEASONS = list(range(1999, CURRENT_SEASON + 1))
OUTPUT_DIR = EXPORT_DIR
PRESEASON_FILE = OUTPUT_DIR / "nfl_current_preseason_database.xlsx"
LAST_ROSTER_UPDATE_WARNING = ""
LAST_ROSTER_SOURCE = "nflverse"
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

# nflreadpy prefixes punting stats with `pt_` (pt_att, pt_yards, pt_inside_20...).
# Note that `punt_returns`/`punt_return_yards` are the RETURNER's stats, not the
# punter's, so they are deliberately not mapped here. The app and the Sleeper
# preseason build both read broadcast-friendly names, so rename on the way in.
PUNTING_SUM_COLUMNS = {
    "pt_att": "punts",
    "pt_yards": "punt_yards",
    "pt_net_yards": "punt_net_yards",
    "pt_inside_20": "punts_inside_20",
    "pt_touchback": "punt_touchbacks",
    "pt_blocked": "punts_blocked",
}

# Longest-kick columns are a max across seasons, never a sum. Both are optional:
# if the upstream feed drops one, it is simply skipped like any other column.
MAX_COLUMNS = {
    "fg_long": "fg_long",
    "pt_long": "punt_long",
}


def load_stats() -> pl.DataFrame:
    print(f"Loading regular-season player stats for {CAREER_SEASONS[0]}-{CAREER_SEASONS[-1]}...")
    stats = nfl.load_player_stats(seasons=CAREER_SEASONS, summary_level="reg")
    print(f"Stats loaded: {stats.height:,} rows.\n")
    return stats


def load_team_history_stats() -> pl.DataFrame:
    """Load weekly rows so midseason team changes are not lost."""
    print(f"Loading weekly team history for {CAREER_SEASONS[0]}-{CAREER_SEASONS[-1]}...")
    stats = nfl.load_player_stats(seasons=CAREER_SEASONS, summary_level="week")
    print(f"Weekly team-history rows loaded: {stats.height:,}.\n")
    return stats


def build_team_history(weekly_stats: pl.DataFrame) -> pl.DataFrame:
    """Build chronological regular-season team codes for each player."""
    required = {"player_id", "season", "team"}
    missing = sorted(required.difference(weekly_stats.columns))
    if missing:
        raise ValueError(f"Weekly team history is missing columns: {', '.join(missing)}")

    history = weekly_stats
    if "season_type" in history.columns:
        history = history.filter(pl.col("season_type") == "REG")
    sort_columns = [column for column in ("player_id", "season", "week") if column in history.columns]
    return (
        history.filter(
            pl.col("player_id").is_not_null()
            & pl.col("team").is_not_null()
            & (pl.col("team").str.strip_chars() != "")
        )
        .with_columns(pl.col("team").str.strip_chars().str.to_uppercase())
        .sort(sort_columns)
        .group_by("player_id", maintain_order=True)
        .agg(
            pl.col("team").unique(maintain_order=True).str.join(", ").alias("teams_played_for")
        )
    )


def apply_team_history(totals: pl.DataFrame, team_history: pl.DataFrame | None) -> pl.DataFrame:
    """Replace season-summary history while retaining its value as a safe fallback."""
    if team_history is None or team_history.is_empty():
        return totals
    if "teams_played_for" not in totals.columns:
        return totals.join(team_history, on="player_id", how="left")

    fallback = "_summary_team_history"
    return (
        totals.rename({"teams_played_for": fallback})
        .join(team_history, on="player_id", how="left")
        .with_columns(
            pl.coalesce(pl.col("teams_played_for"), pl.col(fallback)).alias("teams_played_for")
        )
        .drop(fallback)
    )


def build_totals(
    stats: pl.DataFrame, prefix: str, team_history: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Build one row per player and prefix every summed statistic."""
    stats = stats.filter(
        pl.col("player_id").is_not_null()
        & pl.col("player_display_name").is_not_null()
        & (pl.col("player_display_name").str.strip_chars() != "")
    )
    available = [column for column in TOTAL_COLUMNS if column in stats.columns]
    renamed = {
        source: target for source, target in PUNTING_SUM_COLUMNS.items()
        if source in stats.columns
    }
    maxed = {
        source: target for source, target in MAX_COLUMNS.items()
        if source in stats.columns
    }
    wanted = list(TOTAL_COLUMNS) + list(PUNTING_SUM_COLUMNS) + list(MAX_COLUMNS)
    missing = [column for column in wanted if column not in stats.columns]
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
            *[
                pl.col(source).fill_null(0).sum().alias(f"{prefix}{target}")
                for source, target in renamed.items()
            ],
            *[
                pl.col(source).fill_null(0).max().alias(f"{prefix}{target}")
                for source, target in maxed.items()
            ],
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

    # Punting averages only exist when the punting columns made it through above.
    punting_averages = []
    if f"{prefix}punts" in totals.columns:
        punts = pl.col(f"{prefix}punts")
        for yards_column, average_column in (
            (f"{prefix}punt_yards", f"{prefix}punt_average"),
            (f"{prefix}punt_net_yards", f"{prefix}punt_net_average"),
        ):
            if yards_column in totals.columns:
                punting_averages.append(
                    pl.when(punts > 0)
                    .then((pl.col(yards_column) / punts).round(1))
                    .otherwise(0.0).alias(average_column)
                )

    totals = totals.with_columns(
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
        *punting_averages,
    ).sort("player_display_name")
    return apply_team_history(totals, team_history)


def export_database(database: pl.DataFrame, filename: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_file = OUTPUT_DIR / filename
    database.write_excel(str(output_file))
    print(f"Exported {database.height:,} players to {output_file}")


def get_last_roster_update_warning() -> str:
    return LAST_ROSTER_UPDATE_WARNING


def get_last_roster_source() -> str:
    return LAST_ROSTER_SOURCE


@contextmanager
def _fresh_nflreadpy_downloads():
    """Temporarily bypass nflreadpy's 24-hour memory/filesystem cache."""
    config = get_config()
    original_duration = config.cache_duration
    update_config(cache_duration=0)
    try:
        yield
    finally:
        update_config(cache_duration=original_duration)


def build_active_roster() -> pl.DataFrame:
    """Build current rosters with NFL.com truth and nflverse ID/bio enrichment."""
    global LAST_ROSTER_SOURCE, LAST_ROSTER_UPDATE_WARNING

    roster_year = nfl.get_current_season(roster=True)
    print(f"Loading current {roster_year} official NFL.com rosters...")
    official = reject_incomplete_team_pages(
        load_official_rosters(), TEAM_ROSTER_STATUSES
    )
    warnings = []

    nflverse_roster = pl.DataFrame()
    players = pl.DataFrame()
    with _fresh_nflreadpy_downloads():
        try:
            print("Loading fresh nflverse roster data for fallback and bio enrichment...")
            nflverse_roster = nfl.load_rosters()
        except Exception as error:
            warnings.append(
                f"nflverse roster enrichment failed ({type(error).__name__}: {error})"
            )
        try:
            print("Loading fresh nflverse player IDs and bio data...")
            players = nfl.load_players()
        except Exception as error:
            warnings.append(
                f"nflverse player-ID enrichment failed ({type(error).__name__}: {error})"
            )

    roster, unresolved_ids = build_hybrid_roster(
        official, nflverse_roster, players, TEAM_ROSTER_STATUSES
    )
    fallback_teams = sorted(official.failures)
    if fallback_teams:
        warnings.append(
            "NFL.com failed for " + ", ".join(fallback_teams)
            + "; fresh nflverse fallback was used for those teams"
        )
    missing_teams = sorted(set(TEAM_NAMES).difference(roster["team"].unique().to_list()))
    if missing_teams:
        warnings.append("no roster rows were available for " + ", ".join(missing_teams))
    if unresolved_ids:
        warnings.append(
            f"{unresolved_ids} official roster player(s) could not be matched to a GSIS ID"
        )

    LAST_ROSTER_UPDATE_WARNING = "; ".join(warnings)
    LAST_ROSTER_SOURCE = (
        "NFL.com official + nflverse fallback" if fallback_teams else "NFL.com official"
    )
    source_counts = roster.group_by("roster_source").len().sort("roster_source")
    print(f"Official roster pages loaded: {len(official.successful_teams)}/32 teams.")
    print(f"Roster source rows: {source_counts.to_dicts()}")
    if LAST_ROSTER_UPDATE_WARNING:
        print(f"WARNING: {LAST_ROSTER_UPDATE_WARNING}")
    return roster


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
        # Sleeper's punting keys are inconsistently abbreviated (punt_yds but
        # punt_net_yd), and it publishes no longest-punt field at all — the *_lng
        # keys only cover passing/rushing/receiving/returns and fgm_lng. So
        # preseason_punt_long is intentionally absent rather than a bogus 0;
        # the punter card and stat tab already skip columns that are missing.
        "punts": "punts", "punt_yards": "punt_yds",
        "punt_net_yards": "punt_net_yd", "punts_inside_20": "punt_in_20",
        "punt_touchbacks": "punt_tb",
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

        punts = row["preseason_punts"]
        row["preseason_punt_average"] = round(row["preseason_punt_yards"] / punts, 1) if punts else 0.0
        row["preseason_punt_net_average"] = round(row["preseason_punt_net_yards"] / punts, 1) if punts else 0.0
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
            "preseason_punt_average", "preseason_punt_net_average",
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
    # Export each dataset as soon as it is built. The Sleeper preseason endpoint is
    # unofficial and can fail or change shape, and it must not be able to discard
    # career/season/roster data that already built successfully.
    stats = load_stats()
    weekly_stats = load_team_history_stats()
    career_team_history = build_team_history(weekly_stats)
    current_team_history = build_team_history(
        weekly_stats.filter(pl.col("season") == CURRENT_SEASON)
    )
    print("Building career totals...")
    export_database(
        build_totals(stats, "career_", career_team_history),
        "nfl_career_database.xlsx",
    )
    print(f"Building {CURRENT_SEASON} totals...")
    export_database(
        build_totals(
            stats.filter(pl.col("season") == CURRENT_SEASON),
            f"season_{CURRENT_SEASON}_",
            current_team_history,
        ),
        "nfl_latest_season_database.xlsx",
    )
    export_database(build_active_roster(), "nfl_current_active_rosters.xlsx")
    try:
        export_database(build_preseason_totals(), PRESEASON_FILE.name)
    except Exception as error:
        print(
            f"WARNING: preseason build failed ({type(error).__name__}: {error}).\n"
            f"         Skipped {PRESEASON_FILE.name}; career, season, and roster data were exported."
        )
        print("Done, with the preseason file skipped.")
        return
    print("Done.")


if __name__ == "__main__":
    main()
