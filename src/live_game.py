from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from io import BytesIO
import zipfile
import xml.etree.ElementTree as ET

import requests

from broadcast_tools import normalize_team_code


ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
ESPN_SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
REQUEST_HEADERS = {"User-Agent": "NFL Stats Lookup Broadcast Tool/1.0"}
GSIS_SAMPLE_URL = "https://www.nflgsis.com/gsis/Documentation/StatsExporter/2014-Pre-04-nyj.zip"


@dataclass
class LiveTeamStats:
    team: str
    score: int = 0
    first_downs: int = 0
    total_yards: int = 0
    passing_yards: int = 0
    rushing_yards: int = 0
    third_down_made: int = 0
    third_down_attempts: int = 0
    red_zone_scores: int = 0
    red_zone_trips: int = 0
    turnovers: int = 0
    sacks: int = 0
    penalties: int = 0
    penalty_yards: int = 0
    possession_time: str = "0:00"
    leaders: dict[str, str] = field(default_factory=dict)


@dataclass
class LivePlayerStats:
    player_id: str
    name: str
    team: str
    jersey: str = ""
    position: str = ""
    stat_lines: dict[str, str] = field(default_factory=dict)


@dataclass
class LiveGameSnapshot:
    event_id: str
    name: str
    status: str
    state: str
    period: int
    clock: str
    home_team: str
    away_team: str
    teams: dict[str, LiveTeamStats]
    players: list[LivePlayerStats] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ESPNLiveProvider:
    """Experimental, unofficial ESPN public-feed connector."""

    name = "ESPN Experimental"
    confidence_label = "YELLOW — ESPN experimental"
    poll_interval_ms = 15000

    def __init__(self, timeout: int = 20) -> None:
        self.timeout = timeout

    def discover_game(self, home_team: str, away_team: str, year: int | None = None) -> dict:
        year = year or datetime.now().year
        response = requests.get(
            ESPN_SCOREBOARD_URL,
            params={"dates": year, "limit": 1000},
            headers=REQUEST_HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        wanted = {normalize_team_code(home_team), normalize_team_code(away_team)}
        candidates = []
        now = datetime.now(timezone.utc)
        for event in response.json().get("events", []):
            competition = (event.get("competitions") or [{}])[0]
            teams = {
                normalize_team_code(item.get("team", {}).get("abbreviation"))
                for item in competition.get("competitors", [])
            }
            if teams != wanted:
                continue
            event_date = self._parse_date(event.get("date"))
            state = event.get("status", {}).get("type", {}).get("state", "pre")
            # Prefer live, then upcoming, then the nearest completed matchup.
            state_rank = {"in": 0, "pre": 1, "post": 2}.get(state, 3)
            distance = abs((event_date - now).total_seconds()) if event_date else float("inf")
            candidates.append((state_rank, distance, event))
        if not candidates:
            raise LookupError(f"No {away_team} at {home_team} game was found in ESPN's {year} schedule.")
        event = min(candidates, key=lambda item: (item[0], item[1]))[2]
        return {
            "event_id": str(event["id"]),
            "name": event.get("name", f"{away_team} at {home_team}"),
            "date": event.get("date", ""),
            "status": event.get("status", {}).get("type", {}).get("detail", "Scheduled"),
        }

    def fetch_game(self, event_id: str) -> LiveGameSnapshot:
        response = requests.get(
            ESPN_SUMMARY_URL,
            params={"event": event_id},
            headers=REQUEST_HEADERS,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        competition = data["header"]["competitions"][0]
        competitors = competition.get("competitors", [])
        home_item = next(item for item in competitors if item.get("homeAway") == "home")
        away_item = next(item for item in competitors if item.get("homeAway") == "away")
        home = normalize_team_code(home_item["team"]["abbreviation"])
        away = normalize_team_code(away_item["team"]["abbreviation"])

        teams = {
            home: LiveTeamStats(team=home, score=self._integer(home_item.get("score"))),
            away: LiveTeamStats(team=away, score=self._integer(away_item.get("score"))),
        }
        sacks_allowed = {}
        for team_box in data.get("boxscore", {}).get("teams", []):
            code = normalize_team_code(team_box.get("team", {}).get("abbreviation"))
            if code not in teams:
                continue
            values = {stat.get("name"): stat.get("displayValue", "0") for stat in team_box.get("statistics", [])}
            team = teams[code]
            team.first_downs = self._integer(values.get("firstDowns"))
            team.total_yards = self._integer(values.get("totalYards"))
            team.passing_yards = self._integer(values.get("netPassingYards"))
            team.rushing_yards = self._integer(values.get("rushingYards"))
            team.third_down_made, team.third_down_attempts = self._pair(values.get("thirdDownEff"))
            team.red_zone_scores, team.red_zone_trips = self._pair(values.get("redZoneAttempts"))
            team.turnovers = self._integer(values.get("turnovers"))
            sacks_allowed[code] = self._pair(values.get("sacksYardsLost"))[0]
            team.penalties, team.penalty_yards = self._pair(values.get("totalPenaltiesYards"))
            team.possession_time = values.get("possessionTime", "0:00")

        teams[home].sacks = sacks_allowed.get(away, 0)
        teams[away].sacks = sacks_allowed.get(home, 0)
        self._add_leaders(data, teams)
        players = self._extract_players(data)

        status = competition.get("status", {}).get("type", {})
        return LiveGameSnapshot(
            event_id=event_id,
            name=data["header"].get("gameNote") or f"{away} at {home}",
            status=status.get("detail", "Unknown"),
            state=status.get("state", "pre"),
            period=self._integer(competition.get("status", {}).get("period")),
            clock=competition.get("status", {}).get("displayClock", "0:00"),
            home_team=home,
            away_team=away,
            teams=teams,
            players=players,
        )

    @staticmethod
    def _add_leaders(data: dict, teams: dict[str, LiveTeamStats]) -> None:
        labels = {"passing": "Passing", "rushing": "Rushing", "receiving": "Receiving"}
        for player_box in data.get("boxscore", {}).get("players", []):
            code = normalize_team_code(player_box.get("team", {}).get("abbreviation"))
            if code not in teams:
                continue
            for group in player_box.get("statistics", []):
                group_name = group.get("name")
                if group_name not in labels or not group.get("athletes"):
                    continue
                athlete = group["athletes"][0]
                name = athlete.get("athlete", {}).get("displayName", "Unknown")
                stat_labels = group.get("labels", [])
                stats = athlete.get("stats", [])
                line = " | ".join(
                    f"{value} {label}" for label, value in zip(stat_labels, stats)
                    if label in {"C/ATT", "CAR", "REC", "YDS", "TD", "INT", "TGTS"}
                )
                teams[code].leaders[labels[group_name]] = f"{name}: {line}"

    @staticmethod
    def _extract_players(data: dict) -> list[LivePlayerStats]:
        players: dict[str, LivePlayerStats] = {}
        group_labels = {
            "passing": "Passing", "rushing": "Rushing", "receiving": "Receiving",
            "fumbles": "Fumbles", "defensive": "Defense", "interceptions": "Interceptions",
            "kickReturns": "Kick Returns", "puntReturns": "Punt Returns",
            "kicking": "Kicking", "punting": "Punting",
        }
        for player_box in data.get("boxscore", {}).get("players", []):
            team = normalize_team_code(player_box.get("team", {}).get("abbreviation"))
            for group in player_box.get("statistics", []):
                group_name = group.get("name")
                if group_name not in group_labels:
                    continue
                labels = group.get("labels", [])
                for item in group.get("athletes", []):
                    athlete = item.get("athlete", {})
                    player_id = str(athlete.get("id", athlete.get("displayName", "")))
                    key = f"{team}:{player_id}"
                    player = players.setdefault(
                        key,
                        LivePlayerStats(
                            player_id=player_id,
                            name=athlete.get("displayName", "Unknown"),
                            team=team,
                            jersey=str(athlete.get("jersey", "")),
                            position=athlete.get("position", {}).get("abbreviation", ""),
                        ),
                    )
                    line = " | ".join(
                        f"{label} {value}" for label, value in zip(labels, item.get("stats", []))
                    )
                    player.stat_lines[group_labels[group_name]] = line
        return sorted(players.values(), key=lambda player: (player.team, player.name))

    @staticmethod
    def _pair(value) -> tuple[int, int]:
        text = str(value or "0-0").replace("/", "-")
        pieces = text.split("-", 1)
        return ESPNLiveProvider._integer(pieces[0]), ESPNLiveProvider._integer(pieces[1] if len(pieces) > 1 else 0)

    @staticmethod
    def _integer(value) -> int:
        try:
            return int(float(str(value or 0).replace(",", "")))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _parse_date(value) -> datetime | None:
        if not value:
            return None


class GSISXMLProvider:
    """Parser and folder reader for official GSIS cumulative-stat XML snapshots."""

    name = "NFL GSIS XML"
    confidence_label = "GREEN — official GSIS XML"
    poll_interval_ms = 1000

    def parse_file(self, path: str | Path) -> LiveGameSnapshot:
        path = Path(path)
        root = ET.parse(path).getroot()
        if root.tag != "CumulativeStatisticsFile":
            raise ValueError(f"Unsupported GSIS XML root: {root.tag}")
        header = self._attributes(root, "CumeStatHeader")
        if not header:
            raise ValueError("GSIS XML is missing CumeStatHeader")

        home_code = normalize_team_code(header.get("HomeClubCode") or header.get("Home_Team"))
        away_code = normalize_team_code(header.get("VisitorClubCode"))
        if not home_code or not away_code:
            raise ValueError("GSIS XML is missing home/visitor club codes")
        home = self._team_stats(root, "Home", home_code)
        away = self._team_stats(root, "Visitor", away_code)

        passing = self._attributes(root, "PASSING")
        home.passing_yards = self._integer(passing.get("HomePassYards", home.passing_yards))
        away.passing_yards = self._integer(passing.get("VisitingPassYards", away.passing_yards))
        home.sacks = self._integer(passing.get("VisitingPassTimesSacked"))
        away.sacks = self._integer(passing.get("HomePassTimesSacked"))

        home.third_down_made, home.third_down_attempts = self._third_down(root, "H3RDDOWN_EFF")
        away.third_down_made, away.third_down_attempts = self._third_down(root, "V3RDDOWN_EFF")
        home.leaders = self._player_leaders(root, "H")
        away.leaders = self._player_leaders(root, "V")
        players = self._all_players(root, path.parent, home_code, away_code)

        quarter = self._integer(header.get("Quarter"))
        clock = str(header.get("GameClock", "0:00")).replace('"', "")
        status = f"GSIS — Q{quarter} {clock}" if quarter else "GSIS — Pregame"
        return LiveGameSnapshot(
            event_id=str(header.get("GameKey", path.stem)),
            name=f"{away_code} at {home_code}",
            status=status,
            state="in" if quarter else "pre",
            period=quarter,
            clock=clock,
            home_team=home_code,
            away_team=away_code,
            teams={home_code: home, away_code: away},
            players=players,
            fetched_at=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc),
        )

    def snapshot_files(self, folder: str | Path) -> list[Path]:
        folder = Path(folder)
        return sorted(folder.rglob("*_GSISGameStats.xml"))

    def fetch_latest(self, folder: str | Path) -> LiveGameSnapshot:
        files = self.snapshot_files(folder)
        if not files:
            raise FileNotFoundError(f"No *_GSISGameStats.xml files found under {folder}")
        return self.parse_file(max(files, key=lambda path: path.stat().st_mtime))

    def _team_stats(self, root: ET.Element, side: str, code: str) -> LiveTeamStats:
        values = self._attributes(root, f"{side}TeamStats")
        prefix = "Home" if side == "Home" else ""
        team = LiveTeamStats(team=code)
        team.score = self._integer(values.get("TotalScore"))
        team.first_downs = self._integer(values.get("TotalFirstDowns"))
        team.total_yards = self._integer(values.get("TotalYards"))
        team.passing_yards = self._integer(values.get("PassingYards"))
        team.rushing_yards = self._integer(values.get("RushingYards"))
        team.red_zone_scores = self._integer(values.get("RedZoneSuccesses"))
        team.red_zone_trips = self._integer(values.get("RedZoneAttempts"))
        team.turnovers = self._integer(values.get("Turnovers"))
        team.penalties = self._integer(values.get("Penalties"))
        team.penalty_yards = self._integer(values.get("PenaltyYards"))
        team.possession_time = values.get("TimeOfPossession", "0:00")
        return team

    def _player_leaders(self, root: ET.Element, side_prefix: str) -> dict[str, str]:
        definitions = {
            "Passing": (f"{side_prefix}PLAYER_PASS", "Yards", ("Completions", "Attempts", "Touchdowns", "Interceptions")),
            "Rushing": (f"{side_prefix}PLAYER_RUSH", "Yards", ("Attempts", "Touchdowns")),
            "Receiving": (f"{side_prefix}PLAYER_RECV", "Yards", ("Receptions", "Touchdowns", "PassTarget")),
        }
        leaders = {}
        for label, (tag, yard_field, extra_fields) in definitions.items():
            players = list(root.findall(tag))
            if not players:
                continue
            leader = max(players, key=lambda element: self._integer(element.attrib.get(yard_field)))
            attrs = leader.attrib
            parts = [f"{attrs.get(yard_field, '0')} YDS"]
            short_labels = {"Completions": "CMP", "Attempts": "ATT", "Touchdowns": "TD", "Interceptions": "INT", "Receptions": "REC", "PassTarget": "TGTS"}
            for field_name in extra_fields:
                if field_name in attrs:
                    parts.append(f"{attrs[field_name]} {short_labels[field_name]}")
            leaders[label] = f"{attrs.get('Player', 'Unknown')}: " + " | ".join(parts)
        return leaders

    def _all_players(self, root: ET.Element, folder: Path, home: str,
                     away: str) -> list[LivePlayerStats]:
        roster = self._roster_lookup(folder)
        players: dict[str, LivePlayerStats] = {}
        formatters = {
            "PASS": lambda a: f"{a.get('Completions', '0')}/{a.get('Attempts', '0')} CMP/ATT | {a.get('Yards', '0')} YDS | {a.get('Touchdowns', '0')} TD | {a.get('Interceptions', '0')} INT | {a.get('Rating', '0')} RTG",
            "RUSH": lambda a: f"{a.get('Attempts', '0')} CAR | {a.get('Yards', '0')} YDS | {a.get('Average', '0')} AVG | {a.get('Touchdowns', '0')} TD | LONG {a.get('Long', '0')}",
            "RECV": lambda a: f"{a.get('Receptions', '0')} REC | {a.get('Yards', '0')} YDS | {a.get('Average', '0')} AVG | {a.get('Touchdowns', '0')} TD | {a.get('PassTarget', '0')} TGTS",
            "DEFENSE": lambda a: f"{a.get('Combined', '0')} TKL | {a.get('Sacks', '0')} SACK | {a.get('Interceptions', '0')} INT | {a.get('ForcedFumbles', '0')} FF | {a.get('PassDefences', '0')} PD | {a.get('QuarterbackHits', '0')} QBH",
            "FG": lambda a: f"{a.get('FieldGoalsMade', '0')}/{a.get('FieldGoalAttempts', '0')} FG | LONG {a.get('LongestMadeFieldGoal', '0')}",
            "PAT": lambda a: f"{a.get('PATsMade', '0')}/{a.get('PATAttempts', '0')} PAT",
            "PUNT": lambda a: f"{a.get('Punts', '0')} PUNTS | {a.get('PuntYards', '0')} YDS | {a.get('GrossAvgPuntLength', '0')} AVG | LONG {a.get('Longest', '0')} | {a.get('Inside20', '0')} IN 20",
            "KICKRET": lambda a: f"{a.get('Number', '0')} RET | {a.get('Yards', '0')} YDS | {a.get('Average', '0')} AVG | LONG {a.get('Longest', '0')}",
            "PUNTRET": lambda a: f"{a.get('Number', '0')} RET | {a.get('Yards', '0')} YDS | {a.get('Average', '0')} AVG | LONG {a.get('Longest', '0')}",
            "FUMBLE": lambda a: f"{a.get('Fumbles', '0')} FUM | {a.get('FumblesLost', '0')} LOST",
        }
        line_labels = {
            "PASS": "Passing", "RUSH": "Rushing", "RECV": "Receiving",
            "DEFENSE": "Defense", "FG": "Field Goals", "PAT": "Extra Points",
            "PUNT": "Punting", "KICKRET": "Kick Returns", "PUNTRET": "Punt Returns",
            "FUMBLE": "Fumbles",
        }
        for element in root:
            if not (element.tag.startswith("HPLAYER_") or element.tag.startswith("VPLAYER_")):
                continue
            category = element.tag.split("_", 1)[1]
            if category not in formatters:
                continue
            attrs = element.attrib
            player_id = attrs.get("PlayerID") or f"{element.tag[0]}:{attrs.get('Player', '')}"
            side = element.tag[0]
            team = home if side == "H" else away
            roster_player = roster.get(player_id, {})
            player = players.setdefault(
                player_id,
                LivePlayerStats(
                    player_id=player_id,
                    name=roster_player.get("name") or attrs.get("Player", "Unknown"),
                    team=team,
                    jersey=attrs.get("JerseyNumber", roster_player.get("jersey", "")),
                    position=roster_player.get("position", ""),
                ),
            )
            player.stat_lines[line_labels[category]] = formatters[category](attrs)
        return sorted(players.values(), key=lambda player: (player.team, player.name))

    @staticmethod
    def _roster_lookup(folder: Path) -> dict[str, dict[str, str]]:
        roster_files = sorted(folder.glob("*_ROSTER.xml"))
        if not roster_files:
            return {}
        try:
            root = ET.parse(roster_files[-1]).getroot()
        except ET.ParseError:
            return {}
        result = {}
        for element in root.findall("Player"):
            attrs = element.attrib
            player_id = attrs.get("GSISPlayer_ID")
            if not player_id:
                continue
            full_name = " ".join(
                part for part in (attrs.get("FirstName", ""), attrs.get("LastName", "")) if part
            )
            result[player_id] = {
                "name": full_name or attrs.get("Name", ""),
                "position": attrs.get("Position", ""),
                "jersey": attrs.get("JerseyNumber", ""),
            }
        return result

    @staticmethod
    def _third_down(root: ET.Element, tag: str) -> tuple[int, int]:
        values = GSISXMLProvider._attributes(root, tag)
        return (
            GSISXMLProvider._integer(values.get("ThirdDownConversions")),
            GSISXMLProvider._integer(values.get("ThirdDownAttempts")),
        )

    @staticmethod
    def _attributes(root: ET.Element, tag: str) -> dict:
        element = root.find(tag)
        return {} if element is None else element.attrib

    @staticmethod
    def _integer(value) -> int:
        try:
            return int(float(str(value or 0).replace(",", "").replace('"', "")))
        except (TypeError, ValueError):
            return 0


class GSISReplayProvider:
    """Replays archived GSIS XML snapshots through the live-provider interface."""

    name = "GSIS Sample Replay"
    confidence_label = "GREEN — official GSIS sample replay"
    poll_interval_ms = 1000

    def __init__(self, sample_folder: str | Path) -> None:
        self.sample_folder = Path(sample_folder)
        self.parser = GSISXMLProvider()
        self.files = self.parser.snapshot_files(self.sample_folder)
        self.index = 0

    def discover_game(self, home_team: str, away_team: str, year: int | None = None) -> dict:
        self._ensure_files()
        if not self.files:
            raise FileNotFoundError(f"No GSIS sample snapshots found in {self.sample_folder}")
        first = self.parser.parse_file(self.files[0])
        self.index = 0
        return {
            "event_id": first.event_id,
            "name": f"GSIS REPLAY — {first.name}",
            "date": "2014 preseason archived sample",
            "status": f"Ready — {len(self.files)} snapshots",
        }

    def fetch_game(self, event_id: str) -> LiveGameSnapshot:
        self._ensure_files()
        if not self.files:
            raise FileNotFoundError("No GSIS replay snapshots are available")
        file_path = self.files[min(self.index, len(self.files) - 1)]
        snapshot = self.parser.parse_file(file_path)
        snapshot.status = f"GSIS replay {self.index + 1}/{len(self.files)} — Q{snapshot.period} {snapshot.clock}"
        if self.index < len(self.files) - 1:
            self.index += 1
        return snapshot

    def reset(self) -> None:
        self.index = 0

    def _ensure_files(self) -> None:
        if self.files:
            return
        response = requests.get(GSIS_SAMPLE_URL, headers=REQUEST_HEADERS, timeout=90)
        response.raise_for_status()
        self.sample_folder.mkdir(parents=True, exist_ok=True)
        root = self.sample_folder.resolve()
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            for member in archive.infolist():
                target = (root / member.filename).resolve()
                if root not in target.parents and target != root:
                    raise ValueError("Unsafe path found in GSIS sample archive")
            archive.extractall(root)
        self.files = self.parser.snapshot_files(self.sample_folder)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
