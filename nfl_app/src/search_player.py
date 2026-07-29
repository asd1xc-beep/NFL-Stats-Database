import nflreadpy as nfl
import polars as pl


OFFENSIVE_COLUMNS = [
    "player_id",
    "player_display_name",
    "position",
    "team",
    "season",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_fumbles",
    "receiving_fumbles_lost",
    "fantasy_points_ppr",
]


def load_season_totals(season: int):
    print(f"Loading {season} regular season player stats...")

    stats = nfl.load_player_stats(
        seasons=[season],
        summary_level="reg"
    )

    print("Data loaded.")
    return stats


def search_player(stats, player_name: str):
    player_name = player_name.lower().strip()

    results = stats.filter(
        pl.col("player_display_name").str.to_lowercase().str.contains(player_name)
    )

    if results.height == 0:
        print(f"No players found for: {player_name}")
        return

    results = results.select([
        col for col in OFFENSIVE_COLUMNS
        if col in results.columns
    ])

    print()
    print(f"Search results for: {player_name}")
    print(results)


def main():
    season = 2024
    stats = load_season_totals(season)

    player_name = input("Enter player name: ")
    search_player(stats, player_name)


if __name__ == "__main__":
    main()