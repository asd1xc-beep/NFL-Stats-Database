from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import re
import unicodedata
from typing import Callable, Mapping

from bs4 import BeautifulSoup
import polars as pl
import requests

from broadcast_tools import TEAM_NAMES, normalize_team_code


NFL_ROSTER_URL = "https://www.nfl.com/teams/{slug}/roster"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; NFL Stats Lookup Broadcast Tool/1.0)",
}
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


@dataclass
class OfficialRosterResult:
    rows: list[dict] = field(default_factory=list)
    successful_teams: set[str] = field(default_factory=set)
    failures: dict[str, str] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def team_slug(team_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", team_name.casefold()).strip("-")


def _integer(value) -> int | None:
    text = str(value or "").strip()
    if not text or not re.fullmatch(r"-?\d+", text):
        return None
    return int(text)


def _experience(value) -> int | None:
    text = str(value or "").strip().upper()
    if text == "R":
        return 0
    return _integer(text)


def parse_official_roster_html(html: str, team: str, source_url: str = "") -> list[dict]:
    """Parse the consistent roster table used by all 32 NFL.com team pages."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise ValueError("NFL.com roster table was not found")

    headers = [cell.get_text(" ", strip=True).casefold() for cell in table.find_all("th")]
    required = {"player", "no", "pos", "status"}
    if not required.issubset(headers):
        raise ValueError(
            "NFL.com roster table changed shape; missing "
            + ", ".join(sorted(required.difference(headers)))
        )

    rows = []
    for table_row in table.find_all("tr")[1:]:
        values = [cell.get_text(" ", strip=True) for cell in table_row.find_all("td")]
        if len(values) != len(headers):
            continue
        raw = dict(zip(headers, values))
        name = raw.get("player", "").strip()
        position = raw.get("pos", "").strip().upper()
        status = raw.get("status", "").strip().upper()
        if not name or not position or not status:
            continue
        rows.append(
            {
                "team": normalize_team_code(team),
                "player_display_name": name,
                "position": position,
                "jersey_number": _integer(raw.get("no")),
                "roster_status": status,
                "height": _integer(raw.get("height")),
                "weight": _integer(raw.get("weight")),
                "years_experience": _experience(raw.get("experience")),
                "college": raw.get("college", "").strip() or None,
                "official_roster_url": source_url,
            }
        )
    if not rows:
        raise ValueError("NFL.com roster table contained no usable player rows")
    return rows


def fetch_official_team_roster(
    team: str,
    team_name: str,
    timeout: int = 25,
    request_get: Callable = requests.get,
) -> list[dict]:
    url = NFL_ROSTER_URL.format(slug=team_slug(team_name))
    response = request_get(url, headers=REQUEST_HEADERS, timeout=timeout)
    response.raise_for_status()
    return parse_official_roster_html(response.text, team, url)


def load_official_rosters(
    team_names: Mapping[str, str] = TEAM_NAMES,
    timeout: int = 25,
    max_workers: int = 8,
    fetch_team: Callable[[str, str, int], list[dict]] | None = None,
) -> OfficialRosterResult:
    """Fetch current official roster tables, retaining per-team failures for fallback."""
    result = OfficialRosterResult()
    fetch = fetch_team or fetch_official_team_roster
    workers = max(1, min(max_workers, len(team_names)))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(fetch, code, name, timeout): normalize_team_code(code)
            for code, name in team_names.items()
        }
        for future in as_completed(futures):
            team = futures[future]
            try:
                rows = future.result()
            except Exception as error:
                result.failures[team] = f"{type(error).__name__}: {error}"
                continue
            result.rows.extend(rows)
            result.successful_teams.add(team)
    return result


def reject_incomplete_team_pages(
    result: OfficialRosterResult,
    allowed_statuses: set[str],
    minimum_rows: int = 40,
) -> OfficialRosterResult:
    """Turn suspiciously short team pages into explicit fallback failures."""
    controlled_counts: dict[str, int] = defaultdict(int)
    for row in result.rows:
        if row.get("roster_status") in allowed_statuses:
            controlled_counts[row["team"]] += 1

    rejected = {
        team for team in result.successful_teams
        if controlled_counts.get(team, 0) < minimum_rows
    }
    if not rejected:
        return result

    result.rows = [row for row in result.rows if row.get("team") not in rejected]
    result.successful_teams.difference_update(rejected)
    for team in rejected:
        count = controlled_counts.get(team, 0)
        result.failures[team] = (
            f"NFL.com returned only {count} team-controlled roster row(s); "
            f"expected at least {minimum_rows}"
        )
    return result


def normalize_player_name(value) -> str:
    """Build a stable comparison key while ignoring suffixes and quoted nicknames."""
    text = re.sub(r'["“][^"”]+["”]', " ", str(value or ""))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", text.casefold())
    while tokens and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return "".join(tokens)


def _last_name_key(value) -> str:
    text = re.sub(r'["“][^"”]+["”]', " ", str(value or ""))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    tokens = re.findall(r"[a-z0-9]+", text.casefold())
    while tokens and tokens[-1] in NAME_SUFFIXES:
        tokens.pop()
    return tokens[-1] if tokens else ""


def _position_group(value) -> str:
    position = str(value or "").upper()
    if position in {"C", "G", "OG", "T", "OT", "OL"}:
        return "OL"
    if position in {"DE", "DT", "NT", "DL"}:
        return "DL"
    if position in {"LB", "ILB", "OLB", "MLB", "EDGE"}:
        return "LB"
    if position in {"DB", "CB", "S", "SAF", "FS", "SS"}:
        return "DB"
    if position in {"K", "PK"}:
        return "K"
    if position in {"P", "PT"}:
        return "P"
    return position


def _player_aliases(player: dict) -> set[str]:
    names = {
        player.get("display_name"),
        player.get("full_name"),
        player.get("player_display_name"),
    }
    last_name = player.get("last_name")
    if last_name:
        for first_column in ("first_name", "common_first_name", "football_name"):
            if player.get(first_column):
                names.add(f"{player[first_column]} {last_name}")
    return {normalize_player_name(name) for name in names if name}


def _unique_candidates(candidates: list[dict]) -> list[dict]:
    unique = {}
    for candidate in candidates:
        identity = candidate.get("gsis_id") or candidate.get("player_id")
        if not identity:
            identity = (
                normalize_player_name(candidate.get("display_name") or candidate.get("full_name")),
                normalize_team_code(candidate.get("latest_team") or candidate.get("team")),
            )
        unique[identity] = candidate
    return list(unique.values())


def _prefer_candidate(candidates: list[dict], team: str, position: str) -> dict | None:
    candidates = _unique_candidates(candidates)
    if len(candidates) <= 1:
        return candidates[0] if candidates else None

    same_team = [
        candidate for candidate in candidates
        if normalize_team_code(candidate.get("latest_team") or candidate.get("team")) == team
    ]
    if same_team:
        candidates = same_team
    same_position = [
        candidate for candidate in candidates
        if _position_group(candidate.get("position") or candidate.get("position_group"))
        == _position_group(position)
    ]
    if same_position:
        candidates = same_position
    return candidates[0] if len(candidates) == 1 else None


def _build_enrichment_indexes(
    nflverse_roster: pl.DataFrame, players: pl.DataFrame,
) -> tuple[dict, dict, dict]:
    roster_index: dict[tuple[str, str], list[dict]] = defaultdict(list)
    alias_index: dict[str, list[dict]] = defaultdict(list)
    last_name_index: dict[str, list[dict]] = defaultdict(list)

    for row in nflverse_roster.to_dicts() if not nflverse_roster.is_empty() else []:
        team = normalize_team_code(row.get("team") or row.get("recent_team"))
        for alias in _player_aliases(row):
            roster_index[(team, alias)].append(row)

    for row in players.to_dicts() if not players.is_empty() else []:
        for alias in _player_aliases(row):
            alias_index[alias].append(row)
        last_name = _last_name_key(row.get("last_name") or row.get("display_name"))
        if last_name:
            last_name_index[last_name].append(row)
    return roster_index, alias_index, last_name_index


def _enrichment_for(
    official: dict,
    roster_index: dict,
    alias_index: dict,
    last_name_index: dict,
) -> dict:
    team = official["team"]
    name_key = normalize_player_name(official["player_display_name"])
    position = official["position"]
    roster_match = _prefer_candidate(roster_index.get((team, name_key), []), team, position)
    player_match = _prefer_candidate(alias_index.get(name_key, []), team, position)

    if player_match is None:
        # Handles rare display-name errors and quoted nicknames conservatively: only
        # accept a unique same-team, same-position player with the same last name.
        last_name = _last_name_key(official["player_display_name"])
        player_match = _prefer_candidate(last_name_index.get(last_name, []), team, position)

    enriched = {}
    if player_match:
        enriched.update(player_match)
    if roster_match:
        # The season roster has useful injury/depth-chart fields. It must never
        # override official membership, status, number, position, or display name.
        enriched.update({key: value for key, value in roster_match.items() if value is not None})
    return enriched


def _coalesce(row: dict, *names: str):
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _date_text(value) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def build_hybrid_roster(
    official: OfficialRosterResult,
    nflverse_roster: pl.DataFrame,
    players: pl.DataFrame,
    allowed_statuses: set[str],
) -> tuple[pl.DataFrame, int]:
    """Use NFL.com for roster truth and nflverse for IDs/bio fields and fallback."""
    roster_index, alias_index, last_name_index = _build_enrichment_indexes(
        nflverse_roster, players
    )
    fetched_at = official.fetched_at.astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = []
    unresolved_ids = 0

    for source in official.rows:
        if source["roster_status"] not in allowed_statuses:
            continue
        enrichment = _enrichment_for(source, roster_index, alias_index, last_name_index)
        player_id = _coalesce(enrichment, "gsis_id", "player_id")
        if not player_id:
            unresolved_ids += 1
        rows.append(
            {
                "team": source["team"],
                "player_display_name": source["player_display_name"],
                "position": source["position"],
                "player_id": player_id,
                "jersey_number": source["jersey_number"],
                "roster_status": source["roster_status"],
                "height": source["height"] or _coalesce(enrichment, "height"),
                "weight": source["weight"] or _coalesce(enrichment, "weight"),
                "college": source["college"] or _coalesce(
                    enrichment, "college", "college_name"
                ),
                "years_experience": (
                    source["years_experience"]
                    if source["years_experience"] is not None
                    else _coalesce(enrichment, "years_exp", "years_experience", "years_of_experience")
                ),
                "birth_date": _date_text(
                    _coalesce(enrichment, "birth_date", "birthdate")
                ),
                "headshot_url": _coalesce(enrichment, "headshot_url", "headshot"),
                "depth_chart_position": source["position"],
                "injury_status": _coalesce(enrichment, "injury_status"),
                "injury_body_part": _coalesce(enrichment, "injury_body_part"),
                "injury_notes": _coalesce(enrichment, "injury_notes"),
                "rookie_year": _coalesce(enrichment, "rookie_year", "rookie_season"),
                "roster_source": "NFL.com official",
                "roster_source_fetched_at": fetched_at,
            }
        )

    # An official page failure must not drop an entire team. Fall back only those
    # teams to the fresh nflverse roster and make the downgrade visible to the app.
    failed_teams = set(official.failures)
    if failed_teams and not nflverse_roster.is_empty():
        for source in nflverse_roster.to_dicts():
            team = normalize_team_code(source.get("team") or source.get("recent_team"))
            status = str(source.get("status") or "").upper()
            if team not in failed_teams or status not in allowed_statuses:
                continue
            name = _coalesce(
                source, "full_name", "player_name", "player_display_name", "football_name"
            )
            position = _coalesce(source, "position", "depth_chart_position")
            if not name or not position:
                continue
            rows.append(
                {
                    "team": team,
                    "player_display_name": name,
                    "position": position,
                    "player_id": _coalesce(source, "gsis_id", "player_id"),
                    "jersey_number": _coalesce(source, "jersey_number", "jersey"),
                    "roster_status": status,
                    "height": _coalesce(source, "height"),
                    "weight": _coalesce(source, "weight"),
                    "college": _coalesce(source, "college", "college_name"),
                    "years_experience": _coalesce(source, "years_exp", "years_experience"),
                    "birth_date": _date_text(
                        _coalesce(source, "birth_date", "birthdate")
                    ),
                    "headshot_url": _coalesce(source, "headshot_url", "headshot"),
                    "depth_chart_position": _coalesce(source, "depth_chart_position"),
                    "injury_status": _coalesce(source, "injury_status"),
                    "injury_body_part": _coalesce(source, "injury_body_part"),
                    "injury_notes": _coalesce(source, "injury_notes"),
                    "rookie_year": _coalesce(source, "rookie_year", "rookie_season"),
                    "roster_source": "nflverse fallback",
                    "roster_source_fetched_at": fetched_at,
                }
            )

    if not rows:
        raise RuntimeError("No usable roster rows were available from NFL.com or nflverse")

    return (
        pl.DataFrame(rows, infer_schema_length=None)
        .unique(subset=["team", "player_display_name"], keep="first")
        .sort(["team", "position", "player_display_name"]),
        unresolved_ids,
    )
