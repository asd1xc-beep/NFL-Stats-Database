from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

import polars as pl


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from official_rosters import (
    OfficialRosterResult, build_hybrid_roster, load_official_rosters,
    normalize_player_name, parse_official_roster_html,
    reject_incomplete_team_pages,
)


TEAM_STATUSES = {"ACT", "RES", "PUP", "RSN"}
ROSTER_HTML = """
<table>
  <thead><tr>
    <th>Player</th><th>No</th><th>Pos</th><th>Status</th>
    <th>Height</th><th>Weight</th><th>Experience</th><th>College</th>
  </tr></thead>
  <tbody>
    <tr><td>Rueben Bain Jr.</td><td>3</td><td>LB</td><td>ACT</td>
        <td>75</td><td>270</td><td>R</td><td>Miami</td></tr>
    <tr><td>Former Player</td><td>39</td><td>DB</td><td>CUT</td>
        <td>72</td><td>190</td><td>1</td><td>Example</td></tr>
  </tbody>
</table>
"""


class OfficialRosterParsingTests(unittest.TestCase):
    def test_parses_official_table_and_rookie_experience(self) -> None:
        rows = parse_official_roster_html(ROSTER_HTML, "TB", "https://example.test")

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["player_display_name"], "Rueben Bain Jr.")
        self.assertEqual(rows[0]["jersey_number"], 3)
        self.assertEqual(rows[0]["years_experience"], 0)
        self.assertEqual(rows[0]["official_roster_url"], "https://example.test")

    def test_name_key_ignores_suffix_punctuation_and_quoted_nickname(self) -> None:
        self.assertEqual(normalize_player_name("Rueben Bain Jr."), "ruebenbain")
        self.assertEqual(
            normalize_player_name('Al\'zillion "AZ" Hamilton'),
            "alzillionhamilton",
        )

    def test_loader_retains_per_team_failure_for_fallback(self) -> None:
        def fetch(team, _name, _timeout):
            if team == "NYJ":
                raise ConnectionError("simulated outage")
            return parse_official_roster_html(ROSTER_HTML, team)

        result = load_official_rosters(
            {"TB": "Tampa Bay Buccaneers", "NYJ": "New York Jets"},
            fetch_team=fetch,
        )

        self.assertEqual(result.successful_teams, {"TB"})
        self.assertIn("NYJ", result.failures)
        self.assertEqual(len(result.rows), 2)

    def test_implausibly_short_page_is_rejected_for_fallback(self) -> None:
        result = OfficialRosterResult(
            rows=parse_official_roster_html(ROSTER_HTML, "TB"),
            successful_teams={"TB"},
        )

        reject_incomplete_team_pages(result, TEAM_STATUSES, minimum_rows=2)

        self.assertEqual(result.rows, [])
        self.assertNotIn("TB", result.successful_teams)
        self.assertIn("only 1", result.failures["TB"])


class HybridRosterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.official = OfficialRosterResult(
            rows=parse_official_roster_html(ROSTER_HTML, "TB"),
            successful_teams={"TB"},
            fetched_at=datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc),
        )
        self.players = pl.DataFrame(
            {
                "gsis_id": ["00-NEW", "00-OLD"],
                "display_name": ["Rueben Bain", "Former Player"],
                "common_first_name": ["Rueben", "Former"],
                "first_name": ["Rueben", "Former"],
                "last_name": ["Bain", "Player"],
                "football_name": ["Rueben", "Former"],
                "latest_team": ["TB", "TB"],
                "position": ["LB", "DB"],
                "headshot": ["https://headshot/new", "https://headshot/old"],
                "birth_date": ["2004-01-01", "2000-01-01"],
                "rookie_season": [2026, 2025],
            }
        )
        # Deliberately stale: it contains the cut player and not the current player.
        self.nflverse = pl.DataFrame(
            {
                "team": ["TB"],
                "full_name": ["Former Player"],
                "position": ["DB"],
                "gsis_id": ["00-OLD"],
                "jersey_number": [39],
                "status": ["ACT"],
            }
        )

    def test_official_membership_wins_while_nflverse_supplies_id_and_bio(self) -> None:
        roster, unresolved = build_hybrid_roster(
            self.official, self.nflverse, self.players, TEAM_STATUSES
        )

        self.assertEqual(roster.height, 1)
        row = roster.to_dicts()[0]
        self.assertEqual(row["player_display_name"], "Rueben Bain Jr.")
        self.assertEqual(row["player_id"], "00-NEW")
        self.assertEqual(row["headshot_url"], "https://headshot/new")
        self.assertEqual(row["roster_source"], "NFL.com official")
        self.assertEqual(unresolved, 0)

    def test_failed_team_uses_only_that_teams_nflverse_fallback(self) -> None:
        self.official.failures = {"NYJ": "simulated outage"}
        nflverse = pl.concat(
            [
                self.nflverse,
                pl.DataFrame(
                    {
                        "team": ["NYJ"],
                        "full_name": ["Fallback Jet"],
                        "position": ["WR"],
                        "gsis_id": ["00-JET"],
                        "jersey_number": [80],
                        "status": ["ACT"],
                    }
                ),
            ],
            how="diagonal_relaxed",
        )

        roster, _ = build_hybrid_roster(
            self.official, nflverse, self.players, TEAM_STATUSES
        )

        by_team = {row["team"]: row for row in roster.to_dicts()}
        self.assertEqual(by_team["TB"]["roster_source"], "NFL.com official")
        self.assertEqual(by_team["NYJ"]["roster_source"], "nflverse fallback")
        self.assertEqual(by_team["NYJ"]["player_display_name"], "Fallback Jet")


if __name__ == "__main__":
    unittest.main()
