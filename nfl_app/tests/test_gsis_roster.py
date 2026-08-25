from pathlib import Path
import sys
import unittest

import pandas as pd


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from gsis_roster import (  # noqa: E402
    ROSTER_SOURCE, apply_to_roster, find_roster_files, parse_roster_file,
    roster_rows, split_jersey, status_breakdown, status_label,
)


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "gsis_ROSTER_sample.xml"


class JerseyParsingTests(unittest.TestCase):
    def test_shared_number_suffix_is_split_not_discarded(self) -> None:
        # The suffix is the only thing telling two players on the same number
        # apart, so it has to survive parsing.
        self.assertEqual(split_jersey("35D"), (35, "D"))
        self.assertEqual(split_jersey("35O"), (35, "O"))
        self.assertEqual(split_jersey("19S"), (19, "S"))

    def test_plain_and_zero_padded_numbers(self) -> None:
        self.assertEqual(split_jersey("00"), (0, ""))
        self.assertEqual(split_jersey("07"), (7, ""))
        self.assertEqual(split_jersey("90"), (90, ""))

    def test_unusable_values_return_no_number(self) -> None:
        for value in ("", None, "--", "abc", "123"):
            self.assertEqual(split_jersey(value), (None, ""), msg=repr(value))


class StatusCodeTests(unittest.TestCase):
    def test_documented_codes_are_expanded(self) -> None:
        self.assertEqual(status_label("S"), "Started")
        self.assertEqual(status_label("P"), "Played (substitution)")
        self.assertEqual(status_label("X"), "Active, did not play")
        self.assertEqual(status_label("I"), "Injured, did not play")
        self.assertEqual(status_label("N"), "Not active")
        self.assertEqual(status_label("R"), "Injured reserve")

    def test_unknown_or_blank_codes_pass_through(self) -> None:
        self.assertEqual(status_label("Q"), "Q")
        self.assertEqual(status_label(""), "")
        self.assertEqual(status_label(None), "")

    def test_breakdown_counts_by_label_most_common_first(self) -> None:
        rows = [{"gsis_status": c} for c in ("P", "P", "P", "X", "X", "S", "")]
        self.assertEqual(
            list(status_breakdown(rows).items()),
            [("Played (substitution)", 3), ("Active, did not play", 2), ("Started", 1)],
        )


class RosterFileParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.roster = parse_roster_file(FIXTURE)

    def test_reads_game_header(self) -> None:
        self.assertEqual(self.roster.game_key, "60469")
        self.assertEqual(self.roster.season, "2026")
        self.assertEqual(self.roster.season_type, "Pre")
        self.assertEqual(self.roster.week, "3")
        self.assertEqual(self.roster.home_team, "PIT")
        self.assertEqual(self.roster.away_team, "NYJ")
        self.assertEqual(sorted(self.roster.teams), ["NYJ", "PIT"])

    def test_subsecond_timestamp_parses(self) -> None:
        # GSIS stamps more precision than fromisoformat accepts.
        self.assertIsNotNone(self.roster.stamped_at)
        self.assertEqual(self.roster.stamped_at.year, 2026)

    def test_players_are_assigned_to_teams_via_club_key(self) -> None:
        by_team = {}
        for player in self.roster.players:
            by_team.setdefault(player["team"], []).append(player["player_display_name"])
        self.assertIn("Braelon Allen", by_team["NYJ"])
        self.assertIn("Aaron Rodgers", by_team["PIT"])
        self.assertEqual(len(by_team["NYJ"]), 5)
        self.assertEqual(len(by_team["PIT"]), 5)

    def test_full_names_and_gsis_ids_are_kept(self) -> None:
        watt = next(p for p in self.roster.players if p["player_display_name"] == "T.J. Watt")
        self.assertEqual(watt["player_id"], "00-0033886")
        self.assertEqual(watt["gsis_status"], "X")
        self.assertEqual(watt["gsis_short_name"], "T.Watt")
        self.assertTrue(watt["gsis_verified"])

    def test_rejects_a_file_that_is_not_a_gsis_roster(self) -> None:
        other = Path(__file__).resolve().parent / "fixtures" / "not_a_roster.xml"
        other.write_text("<CumulativeStatisticsFile/>", encoding="utf-8")
        try:
            with self.assertRaises(ValueError):
                parse_roster_file(other)
        finally:
            other.unlink(missing_ok=True)


class RosterFileDiscoveryTests(unittest.TestCase):
    def test_finds_both_exporter_and_website_naming(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            # website serves a bare name; the exporter prefixes stamp + club
            (root / "ROSTER.xml").write_text("<RosterFile/>", encoding="utf-8")
            (root / "200551_Jets_ROSTER.xml").write_text("<RosterFile/>", encoding="utf-8")
            (root / "GSISGameStats.xml").write_text("<x/>", encoding="utf-8")
            found = {path.name for path in find_roster_files(root)}
        self.assertEqual(found, {"ROSTER.xml", "200551_Jets_ROSTER.xml"})

    def test_missing_folder_is_not_an_error(self) -> None:
        self.assertEqual(find_roster_files(Path("no-such-folder")), [])


class OverlayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gsis = parse_roster_file(FIXTURE)
        self.roster = pd.DataFrame([
            # stale rows for the two GSIS teams, plus an untouched third team
            {"team": "NYJ", "player_display_name": "Cut Player", "position": "WR",
             "player_id": "00-0000001", "jersey_number": 88, "roster_status": "ACT",
             "college": "Old State", "height": 72, "roster_source": "NFL.com official"},
            {"team": "NYJ", "player_display_name": "Breece Hall", "position": "RB",
             "player_id": "00-0038120", "jersey_number": 20, "roster_status": "ACT",
             "college": "Iowa State", "height": 71, "roster_source": "NFL.com official"},
            {"team": "PIT", "player_display_name": "Aaron Rodgers", "position": "QB",
             "player_id": "00-0023459", "jersey_number": 8, "roster_status": "ACT",
             "college": "California", "height": 74, "roster_source": "NFL.com official"},
            {"team": "TB", "player_display_name": "Baker Mayfield", "position": "QB",
             "player_id": "00-0034855", "jersey_number": 6, "roster_status": "ACT",
             "college": "Oklahoma", "height": 73, "roster_source": "NFL.com official"},
        ])

    def test_only_the_games_two_teams_are_replaced(self) -> None:
        combined, report = apply_to_roster(self.roster, self.gsis)
        self.assertEqual(report["teams"], ["NYJ", "PIT"])
        # the third team is untouched, and keeps its original source
        tb = combined[combined["team"] == "TB"]
        self.assertEqual(len(tb), 1)
        self.assertEqual(tb.iloc[0]["roster_source"], "NFL.com official")
        # the stale NYJ player is gone
        self.assertNotIn("Cut Player", set(combined["player_display_name"]))

    def test_gsis_rows_are_labelled_as_the_source(self) -> None:
        combined, _ = apply_to_roster(self.roster, self.gsis)
        game = combined[combined["team"].isin(["NYJ", "PIT"])]
        self.assertEqual(set(game["roster_source"]), {ROSTER_SOURCE})
        self.assertEqual(len(game), 10)

    def test_bio_fields_carry_over_by_player_id(self) -> None:
        # GSIS has no college/height, so they come from whatever row we had.
        combined, report = apply_to_roster(self.roster, self.gsis)
        hall = combined[combined["player_display_name"] == "Breece Hall"].iloc[0]
        self.assertEqual(hall["college"], "Iowa State")
        self.assertEqual(hall["roster_source"], ROSTER_SOURCE)
        self.assertGreaterEqual(report["enriched"], 2)

    def test_report_surfaces_shared_numbers_and_missing_ids(self) -> None:
        _, report = apply_to_roster(self.roster, self.gsis)
        self.assertEqual(report["shared_numbers"], ["19D", "19S", "35D", "35O"])
        self.assertEqual(report["missing_player_id"], 1)   # Greg Crippen
        self.assertEqual(report["per_team"], {"NYJ": 5, "PIT": 5})
        self.assertEqual(report["replaced"], 3)

    def test_report_includes_the_status_breakdown(self) -> None:
        _, report = apply_to_roster(self.roster, self.gsis)
        self.assertEqual(
            report["status_counts"],
            {"Active, did not play": 4, "Played (substitution)": 3, "Started": 3},
        )

    def test_overlay_onto_an_empty_roster_still_works(self) -> None:
        combined, report = apply_to_roster(pd.DataFrame(), self.gsis)
        self.assertEqual(len(combined), 10)
        self.assertEqual(report["replaced"], 0)


if __name__ == "__main__":
    unittest.main()
