from pathlib import Path
import sys
import unittest

import pandas as pd
import polars as pl


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from broadcast_tools import career_team_codes
from build_career_database import apply_team_history, build_team_history


class CareerTeamCodesTests(unittest.TestCase):
    def test_normalizes_and_deduplicates_team_history(self) -> None:
        row = pd.Series({"teams_played_for": "DET, LA, STL, NYG"})

        self.assertEqual(career_team_codes(row), ["DET", "LAR", "NYG"])

    def test_falls_back_to_last_team(self) -> None:
        row = pd.Series({"last_team": "JAC"})

        self.assertEqual(career_team_codes(row), ["JAX"])

    def test_returns_empty_list_without_career_history(self) -> None:
        self.assertEqual(career_team_codes(None), [])


class TeamHistoryExportTests(unittest.TestCase):
    def test_builds_chronological_regular_season_team_history(self) -> None:
        weekly = pl.DataFrame(
            {
                "player_id": ["p1", "p1", "p1", "p1"],
                "season": [2022, 2022, 2022, 2023],
                "week": [1, 14, 19, 1],
                "season_type": ["REG", "REG", "POST", "REG"],
                "team": ["CAR", "LA", "SF", "TB"],
            }
        )

        history = build_team_history(weekly)

        self.assertEqual(history.to_dicts(), [{"player_id": "p1", "teams_played_for": "CAR, LA, TB"}])

    def test_complete_history_replaces_season_summary_history(self) -> None:
        totals = pl.DataFrame(
            {
                "player_id": ["p1", "p2"],
                "teams_played_for": ["CAR, TB", "SEA"],
                "career_games": [30, 12],
            }
        )
        history = pl.DataFrame(
            {"player_id": ["p1"], "teams_played_for": ["CAR, LA, TB"]}
        )

        enriched = apply_team_history(totals, history)

        self.assertEqual(enriched["teams_played_for"].to_list(), ["CAR, LA, TB", "SEA"])


if __name__ == "__main__":
    unittest.main()
