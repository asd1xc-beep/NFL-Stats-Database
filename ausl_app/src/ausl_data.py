"""Download and normalize the public data used by the AUSL broadcast app."""

from __future__ import annotations

import json
import math
import os
import re
import ssl
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

try:
    import certifi
except ImportError:  # Source installs can still use Python's normal trust store.
    certifi = None


BASE_URL = "https://theausl.com"
SEASONS = {2025: 270, 2026: 369}
TEAM_NAMES = {
    "Bandits": "Chicago Bandits",
    "Blaze": "Carolina Blaze",
    "Cascade": "Portland Cascade",
    "Spark": "Oklahoma City Spark",
    "Talons": "Utah Talons",
    "Volts": "Texas Volts",
}
TEAM_CODES = {
    "Bandits": "CHI",
    "Blaze": "CAR",
    "Cascade": "PDX",
    "Spark": "OKC",
    "Talons": "UTA",
    "Volts": "TEX",
}
CODE_TO_TEAM = {code: TEAM_NAMES[short] for short, code in TEAM_CODES.items()}
SPLIT_TYPES = {
    "regularSeason": "Regular Season",
    "postSeason": "Postseason",
    "vsRight": "Vs Right",
    "vsLeft": "Vs Left",
    "home": "Home",
    "away": "Away",
    "runnersInScoringPosition": "Scoring Position",
    "runnersInScoringPositionTwoOuts": "Scoring Position and 2 Outs",
}
ENRICHMENT_SOURCES = [
    {
        "source_name": "Official AUSL JSON/API",
        "source_type": "official_stats",
        "source_url": "https://theausl.com",
        "purpose": "Authoritative rosters, player IDs, AUSL stats, live game data, and box scores.",
        "status": "active",
    },
    {
        "source_name": "2026 AUSL Media Guide",
        "source_type": "media_guide",
        "source_url": "https://theausl.com/wp-content/uploads/2026/06/2026-AUSL-Media-Guide.pdf",
        "purpose": "Static bios, team context, coaches, records, and media-guide story notes.",
        "status": "registered_for_storyline_enrichment",
    },
    {
        "source_name": "Official AUSL Split Stats",
        "source_type": "official_splits",
        "source_url": "https://theausl.com/stats/",
        "purpose": "Vs RHP/LHP, home/away, RISP, and two-out RISP broadcast angles.",
        "status": "active",
    },
    {
        "source_name": "Official AUSL Standings",
        "source_type": "team_context",
        "source_url": "https://theausl.com/standings/",
        "purpose": "Team records, streaks, games back, runs scored/allowed, and run differential.",
        "status": "active",
    },
    {
        "source_name": "Official AUSL Schedule",
        "source_type": "schedule_results",
        "source_url": "https://theausl.com/schedule/",
        "purpose": "Game IDs, results, venues, rematch notes, upcoming games, and game-note links.",
        "status": "active",
    },
    {
        "source_name": "AUSL News",
        "source_type": "news",
        "source_url": "https://theausl.com/news/",
        "purpose": "Recent feature stories, returns from injury, highlights, and human-interest context.",
        "status": "registered_for_storyline_enrichment",
    },
    {
        "source_name": "MLB AUSL News",
        "source_type": "news",
        "source_url": "https://www.mlb.com/ausl",
        "purpose": "Broader feature/context articles related to AUSL.",
        "status": "registered_for_storyline_enrichment",
    },
    {
        "source_name": "AUSL Draft Results",
        "source_type": "draft",
        "source_url": "https://theausl.com/news/2026-ausl-college-draft-results/",
        "purpose": "Draft position, school, team selection, and rookie storylines.",
        "status": "registered_for_storyline_enrichment",
    },
    {
        "source_name": "AUSL Golden Ticket Tracker",
        "source_type": "draft",
        "source_url": "https://theausl.com/golden-ticket-moment-tracker/",
        "purpose": "Golden Ticket selection context and first-pro-season storylines.",
        "status": "registered_for_storyline_enrichment",
    },
    {
        "source_name": "College/Awards Sources",
        "source_type": "external_context",
        "source_url": "NCAA, D1Softball, NFCA, USA Softball, Academic All-America",
        "purpose": "College stats, awards, and background context with manual confidence review.",
        "status": "registered_manual_review_required",
    },
]


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def export_dir() -> Path:
    path = app_root() / "data" / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _get_json(path: str) -> dict:
    url = path if path.startswith("http") else BASE_URL + path
    request = Request(
        url,
        headers={"User-Agent": "AUSL-Broadcast-Stats/1.0 (local desktop tool)"},
    )
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    with urlopen(request, timeout=45, context=context) as response:
        return json.loads(response.read().decode("utf-8"))


def _get_text(path: str) -> str:
    url = path if path.startswith("http") else BASE_URL + path
    request = Request(
        url,
        headers={"User-Agent": "AUSL-Broadcast-Stats/1.0 (local desktop tool)"},
    )
    context = ssl.create_default_context(cafile=certifi.where()) if certifi else ssl.create_default_context()
    with urlopen(request, timeout=45, context=context) as response:
        return response.read().decode("utf-8", "ignore")


def source_registry_frame() -> pd.DataFrame:
    imported_at = datetime.now(timezone.utc).isoformat()
    return pd.DataFrame([{**row, "imported_at": imported_at} for row in ENRICHMENT_SOURCES])


def _json_array_after(text: str, marker: str) -> list[dict]:
    index = text.find(marker)
    if index < 0:
        return []
    start = text.find("[", index)
    if start < 0:
        return []
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : position + 1])
    return []


def _safe(value, default=""):
    return default if value is None else value


def _nested(data: dict, *keys, default=""):
    value = data
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _team_fields(franchise: dict | None) -> tuple[str, str, str]:
    short = _safe((franchise or {}).get("teamName"))
    return short, TEAM_NAMES.get(short, short), TEAM_CODES.get(short, short[:3].upper())


def roster_frame(payload: dict, season: int) -> pd.DataFrame:
    rows = []
    for player in payload.get("players", []):
        info = _nested(player, "sportInfoBySport", "ausl", default={})
        franchise = player.get("franchise") or {}
        short, team, code = _team_fields(franchise)
        status = info.get("currentRosterStatus") or {}
        position = info.get("primaryPosition") or {}
        school = player.get("school") or {}
        birth = player.get("birthAddress") or {}
        image = player.get("imageResource") or {}
        height = player.get("height")
        rows.append(
            {
                "season": season,
                "player_id": player.get("playerId"),
                "player_name": f"{_safe(player.get('firstName'))} {_safe(player.get('lastName'))}".strip(),
                "first_name": _safe(player.get("firstName")),
                "last_name": _safe(player.get("lastName")),
                "team": team,
                "team_short": short,
                "team_code": code,
                "jersey_number": _safe(info.get("uniformNumberDisplay"), _safe(info.get("uniformNumber"))),
                "position": _safe(position.get("positionLk")),
                "position_name": _safe(position.get("description")),
                "bats_throws": _safe(info.get("batsThrows")),
                "roster_status": _safe(status.get("description"), "Active"),
                "status_comments": _safe(status.get("comments")),
                "transaction_type": _safe(status.get("transactionType")),
                "college": _safe(school.get("schoolNameShort"), _safe(school.get("schoolName"))),
                "graduation_year": _safe(school.get("graduationYear")),
                "hometown": ", ".join(str(x) for x in [birth.get("city"), birth.get("state")] if x),
                "height_inches": _safe(height),
                "height": f"{int(height)//12}-{int(height)%12}" if isinstance(height, (int, float)) and not math.isnan(height) else "",
                "age": _safe(player.get("age")),
                "date_of_birth": _safe(player.get("dateOfBirth")),
                "headshot_url": _safe(image.get("imageUrl")),
                "player_slug": _safe(player.get("playerSlug")),
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.dropna(subset=["player_id"])
        frame = frame[frame["player_name"].str.strip().ne("")]
        frame = frame.sort_values(["team", "jersey_number", "last_name"], kind="stable")
    return frame


def _base_stat_row(row: dict, season: int) -> dict:
    short = _safe(row.get("franchiseName"))
    return {
        "season": season,
        "player_id": row.get("playerId"),
        "player_name": f"{_safe(row.get('firstName'))} {_safe(row.get('lastName'))}".strip(),
        "team": TEAM_NAMES.get(short, short),
        "team_short": short,
        "team_code": TEAM_CODES.get(short, short[:3].upper()),
        "jersey_number": _safe(row.get("uniformNumberDisplay"), _safe(row.get("uniformNumber"))),
        "position": _safe(row.get("primaryPosition")),
    }


def stat_frames(payload: dict, season: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    batting, pitching, fielding = [], [], []
    for row in payload.get("stats", []):
        if row.get("playerId") is None:
            continue
        base = _base_stat_row(row, season)
        if row.get("battingStats"):
            batting.append({**base, **row["battingStats"][0]})
        if row.get("pitchingStats"):
            pitching.append({**base, **row["pitchingStats"][0]})
        total_fielding = next((x for x in row.get("fieldingStats", []) if x.get("position") == "TOTAL"), None)
        if total_fielding is None and row.get("fieldingStats"):
            total_fielding = row["fieldingStats"][0]
        if total_fielding:
            positions = sorted({str(x.get("position")) for x in row.get("fieldingStats", []) if x.get("position") not in (None, "TOTAL")})
            fielding.append({**base, **total_fielding, "fielding_positions": "/".join(positions)})
    return pd.DataFrame(batting), pd.DataFrame(pitching), pd.DataFrame(fielding)


def split_stat_frames(payload: dict, season: int, split_key: str, split_label: str, source_url: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    imported_at = datetime.now(timezone.utc).isoformat()
    batting, pitching, fielding = stat_frames(payload, season)
    for frame in (batting, pitching, fielding):
        if not frame.empty:
            frame["split_key"] = split_key
            frame["split_label"] = split_label
            frame["source_name"] = "Official AUSL Split Stats"
            frame["source_url"] = source_url
            frame["imported_at"] = imported_at
    return batting, pitching, fielding


def fetch_split_payload(season_id: int, split_key: str) -> tuple[dict, str]:
    static_path = f"/data/statsApiData_{season_id}_{split_key}.json"
    try:
        return _get_json(static_path), BASE_URL + static_path
    except HTTPError as exc:
        if exc.code != 404:
            raise
    api_path = f"/api/season-stats/{season_id}?statSplitType={split_key}"
    return _get_json(api_path), BASE_URL + api_path


def fetch_standings_frame() -> pd.DataFrame:
    imported_at = datetime.now(timezone.utc).isoformat()
    page = _get_text("/standings/")
    decoded = page.encode("utf-8").decode("unicode_escape", errors="ignore")
    rows = _json_array_after(decoded, '"standings":')
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    frame["team_name"] = frame["franchiseSlug"].map(TEAM_NAMES).fillna(frame["franchiseSlug"])
    frame["team_code"] = frame["franchiseSlug"].map(TEAM_CODES).fillna(frame["franchiseSlug"].str[:3].str.upper())
    frame["source_name"] = "Official AUSL Standings"
    frame["source_url"] = BASE_URL + "/standings/"
    frame["imported_at"] = imported_at
    return frame


def fetch_schedule_frame() -> pd.DataFrame:
    imported_at = datetime.now(timezone.utc).isoformat()
    page = _get_text("/schedule/")
    decoded = page.encode("utf-8").decode("unicode_escape", errors="ignore")
    games = _json_array_after(decoded, '"games":')
    rows = []
    for game in games:
        competitors = game.get("competitors") or []
        competitor_by_id = {item.get("competitorId"): item for item in competitors if isinstance(item, dict)}
        home_team = game.get("homeTeam") or {}
        away_team = game.get("awayTeam") or {}
        home_team_obj = home_team if isinstance(home_team, dict) else {"name": home_team}
        away_team_obj = away_team if isinstance(away_team, dict) else {"name": away_team}
        home_competitor = competitor_by_id.get(game.get("homeTeamId"), {})
        away_competitor = competitor_by_id.get(game.get("awayTeamId"), {})
        home_name = home_competitor.get("name") or home_team_obj.get("name") or (competitors[0].get("name") if competitors and isinstance(competitors[0], dict) else "")
        away_name = away_competitor.get("name") or away_team_obj.get("name") or (competitors[1].get("name") if len(competitors) > 1 and isinstance(competitors[1], dict) else "")
        if home_name in TEAM_NAMES:
            home_name = TEAM_NAMES[home_name]
        if away_name in TEAM_NAMES:
            away_name = TEAM_NAMES[away_name]
        venue = game.get("venue") or {}
        address = venue.get("address") or {}
        game_notes = game.get("previewNotes") or game.get("boxScoreNotes") or []
        if isinstance(game_notes, list):
            game_notes = "; ".join(
                str(item.get("url") or item.get("link") or item.get("title") or item) if isinstance(item, dict) else str(item)
                for item in game_notes
            )
        rows.append(
            {
                "season": game.get("seasonId"),
                "game_id": game.get("gameId"),
                "game_date": game.get("gameDateIso") or game.get("gameDate"),
                "game_time": game.get("gameTime"),
                "game_time_zone": game.get("gameTimeZone"),
                "away_team": away_name,
                "home_team": home_name,
                "away_team_code": TEAM_CODES.get(away_competitor.get("name"), TEAM_CODES.get(away_team_obj.get("name"), str(away_name)[:3].upper())),
                "home_team_code": TEAM_CODES.get(home_competitor.get("name"), TEAM_CODES.get(home_team_obj.get("name"), str(home_name)[:3].upper())),
                "away_score": game.get("awayTeamScore"),
                "home_score": game.get("homeTeamScore"),
                "venue": venue.get("name"),
                "city": address.get("city"),
                "state": address.get("state"),
                "status": game.get("recordStatus"),
                "game_type": game.get("gameTypeLk"),
                "broadcast": ", ".join(str(item.get("name")) for item in (game.get("broadcasts") or []) if item.get("name")),
                "game_notes": game_notes,
                "source_name": "Official AUSL Schedule",
                "source_url": f"{BASE_URL}/game/{game.get('gameId')}",
                "imported_at": imported_at,
            }
        )
    return pd.DataFrame(rows)


def ensure_manual_notes_file() -> Path:
    path = app_root() / "data" / "manual" / "player_notes.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        columns = [
            "note_id",
            "player_id",
            "player_name",
            "team_code",
            "note_text",
            "note_type",
            "entered_by",
            "source",
            "last_verified_date",
            "air_safe",
            "created_at",
        ]
        pd.DataFrame(columns=columns).to_csv(path, index=False)
    return path


def load_manual_notes() -> pd.DataFrame:
    path = ensure_manual_notes_file()
    return pd.read_csv(path)


def _ip_to_outs(value) -> int:
    try:
        whole = int(float(value))
        partial = int(round((float(value) - whole) * 10))
        return whole * 3 + min(max(partial, 0), 2)
    except (TypeError, ValueError):
        return 0


def _outs_to_ip(outs: int) -> float:
    return float(f"{outs // 3}.{outs % 3}")


def career_batting(frames: list[pd.DataFrame]) -> pd.DataFrame:
    source = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if source.empty:
        return source
    sum_cols = ["gamesPlayed", "gamesStarted", "plateAppearances", "atBat", "runs", "hits", "doubles", "triples", "homeRuns", "runsBattedIn", "baseonBalls", "intentionalWalks", "hitByPitch", "strikeOuts", "stolenBases", "stolenBasesAttempts", "caughtStealing", "totalBases", "sacrificeFly", "sacrificeHit"]
    present = [c for c in sum_cols if c in source]
    grouped = source.groupby(["player_id", "player_name"], as_index=False)[present].sum(numeric_only=True)
    grouped["seasons"] = source.groupby("player_id")["season"].nunique().reindex(grouped["player_id"]).to_numpy()
    ab = grouped.get("atBat", 0).replace(0, pd.NA)
    grouped["battingAverage"] = grouped.get("hits", 0) / ab
    grouped["sluggingPercentage"] = grouped.get("totalBases", 0) / ab
    ob_denom = grouped.get("atBat", 0) + grouped.get("baseonBalls", 0) + grouped.get("hitByPitch", 0) + grouped.get("sacrificeFly", 0)
    grouped["onBasePercentage"] = (grouped.get("hits", 0) + grouped.get("baseonBalls", 0) + grouped.get("hitByPitch", 0)) / ob_denom.replace(0, pd.NA)
    grouped["opsPercentage"] = grouped["onBasePercentage"] + grouped["sluggingPercentage"]
    return grouped


def career_pitching(frames: list[pd.DataFrame]) -> pd.DataFrame:
    source = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if source.empty:
        return source
    source["_outs"] = source.get("inningsPitched", 0).map(_ip_to_outs)
    sum_cols = ["_outs", "appearances", "gamesStarted", "wins", "losses", "saves", "completeGames", "shutout", "hitsAllowed", "runs", "earnedRuns", "baseOnBalls", "intentionalWalksAllowed", "hitByPitch", "strikeOuts", "homeRuns", "numberOfPitches", "balls", "strikes", "wildPitch"]
    present = [c for c in sum_cols if c in source]
    grouped = source.groupby(["player_id", "player_name"], as_index=False)[present].sum(numeric_only=True)
    grouped["seasons"] = source.groupby("player_id")["season"].nunique().reindex(grouped["player_id"]).to_numpy()
    grouped["inningsPitched"] = grouped["_outs"].map(_outs_to_ip)
    innings = grouped["_outs"] / 3
    grouped["earnedRunAverage"] = grouped.get("earnedRuns", 0) * 7 / innings.replace(0, pd.NA)
    grouped["whip"] = (grouped.get("hitsAllowed", 0) + grouped.get("baseOnBalls", 0)) / innings.replace(0, pd.NA)
    return grouped.drop(columns=["_outs"])


def career_fielding(frames: list[pd.DataFrame]) -> pd.DataFrame:
    source = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if source.empty:
        return source
    sum_cols = ["gamesPlayed", "putOuts", "assists", "errors", "doublePlays", "totalChances"]
    present = [c for c in sum_cols if c in source]
    grouped = source.groupby(["player_id", "player_name"], as_index=False)[present].sum(numeric_only=True)
    grouped["seasons"] = source.groupby("player_id")["season"].nunique().reindex(grouped["player_id"]).to_numpy()
    grouped["fieldingPercent"] = (grouped.get("putOuts", 0) + grouped.get("assists", 0)) / grouped.get("totalChances", 0).replace(0, pd.NA)
    return grouped


def _write_excel_atomic(path: Path, sheets: dict[str, pd.DataFrame]) -> None:
    fd, temp_name = tempfile.mkstemp(suffix=".xlsx", dir=path.parent)
    os.close(fd)
    try:
        with pd.ExcelWriter(temp_name, engine="openpyxl") as writer:
            for name, frame in sheets.items():
                frame.to_excel(writer, sheet_name=name[:31], index=False)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.remove(temp_name)


def update_all_data(progress=None) -> dict[str, Path]:
    """Refresh roster, season, and calculated AUSL career workbooks."""
    progress = progress or (lambda _message: None)
    imported_at = datetime.now(timezone.utc).isoformat()
    roster_payloads, stats_payloads = {}, {}
    for year, season_id in SEASONS.items():
        progress(f"Downloading {year} AUSL rosters...")
        roster_payloads[year] = _get_json(f"/data/playersApiData_{season_id}.json")
        progress(f"Downloading {year} AUSL stats...")
        stats_payloads[year] = _get_json(f"/data/statsApiData_{season_id}.json")

    rosters = {year: roster_frame(payload, year) for year, payload in roster_payloads.items()}
    batting, pitching, fielding = {}, {}, {}
    for year, payload in stats_payloads.items():
        batting[year], pitching[year], fielding[year] = stat_frames(payload, year)

    split_batting, split_pitching, split_fielding = [], [], []
    for year, season_id in SEASONS.items():
        for split_key, split_label in SPLIT_TYPES.items():
            progress(f"Downloading {year} AUSL split stats: {split_label}...")
            try:
                payload, source_url = fetch_split_payload(season_id, split_key)
            except Exception as exc:
                progress(f"Skipping {year} {split_label} split stats: {exc}")
                continue
            b, p, f = split_stat_frames(payload, year, split_key, split_label, source_url)
            split_batting.append(b)
            split_pitching.append(p)
            split_fielding.append(f)

    progress("Downloading AUSL standings and schedule context...")
    try:
        standings = fetch_standings_frame()
    except Exception as exc:
        progress(f"Skipping standings context: {exc}")
        standings = pd.DataFrame()
    try:
        schedule = fetch_schedule_frame()
    except Exception as exc:
        progress(f"Skipping schedule context: {exc}")
        schedule = pd.DataFrame()

    def combined(frames):
        frames = [frame for frame in frames if not frame.empty]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    output = export_dir()
    roster_path = output / "ausl_rosters.xlsx"
    season_path = output / "ausl_season_stats.xlsx"
    career_path = output / "ausl_career_stats.xlsx"
    batting_splits_path = output / "ausl_batting_splits.xlsx"
    pitching_splits_path = output / "ausl_pitching_splits.xlsx"
    fielding_splits_path = output / "ausl_fielding_splits.xlsx"
    team_context_path = output / "ausl_team_context.xlsx"
    source_registry_path = output / "ausl_storyline_sources.xlsx"
    progress("Writing local Excel databases...")
    _write_excel_atomic(roster_path, {f"roster_{year}": frame for year, frame in rosters.items()})
    _write_excel_atomic(
        season_path,
        {
            **{f"batting_{year}": batting[year] for year in SEASONS},
            **{f"pitching_{year}": pitching[year] for year in SEASONS},
            **{f"fielding_{year}": fielding[year] for year in SEASONS},
        },
    )
    _write_excel_atomic(
        career_path,
        {
            "career_batting": career_batting(list(batting.values())),
            "career_pitching": career_pitching(list(pitching.values())),
            "career_fielding": career_fielding(list(fielding.values())),
        },
    )
    _write_excel_atomic(batting_splits_path, {"batting_splits": combined(split_batting)})
    _write_excel_atomic(pitching_splits_path, {"pitching_splits": combined(split_pitching)})
    _write_excel_atomic(fielding_splits_path, {"fielding_splits": combined(split_fielding)})
    _write_excel_atomic(team_context_path, {"standings": standings, "schedule_results": schedule})
    _write_excel_atomic(source_registry_path, {"sources": source_registry_frame()})
    ensure_manual_notes_file()
    manifest = {
        "updated_at": imported_at,
        "seasons": SEASONS,
        "current_roster_players": int(len(rosters[max(SEASONS)])),
        "source": BASE_URL,
        "enrichment_sources": [row["source_name"] for row in ENRICHMENT_SOURCES],
    }
    (output / "update_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    progress("AUSL database update complete.")
    return {
        "rosters": roster_path,
        "season": season_path,
        "career": career_path,
        "batting_splits": batting_splits_path,
        "pitching_splits": pitching_splits_path,
        "fielding_splits": fielding_splits_path,
        "team_context": team_context_path,
        "sources": source_registry_path,
    }


def load_database() -> dict[str, pd.DataFrame]:
    output = export_dir()
    paths = {
        "roster": output / "ausl_rosters.xlsx",
        "season": output / "ausl_season_stats.xlsx",
        "career": output / "ausl_career_stats.xlsx",
    }
    if not all(path.exists() for path in paths.values()):
        update_all_data()
    manifest_path = output / "update_manifest.json"
    result = {"roster": pd.read_excel(paths["roster"], sheet_name=f"roster_{max(SEASONS)}")}
    for year in SEASONS:
        for category in ("batting", "pitching", "fielding"):
            result[f"{category}_{year}"] = pd.read_excel(paths["season"], sheet_name=f"{category}_{year}")
    for category in ("batting", "pitching", "fielding"):
        result[f"career_{category}"] = pd.read_excel(paths["career"], sheet_name=f"career_{category}")
    optional_workbooks = {
        "batting_splits": (output / "ausl_batting_splits.xlsx", "batting_splits"),
        "pitching_splits": (output / "ausl_pitching_splits.xlsx", "pitching_splits"),
        "fielding_splits": (output / "ausl_fielding_splits.xlsx", "fielding_splits"),
        "standings": (output / "ausl_team_context.xlsx", "standings"),
        "schedule_results": (output / "ausl_team_context.xlsx", "schedule_results"),
        "storyline_sources": (output / "ausl_storyline_sources.xlsx", "sources"),
    }
    for key, (path, sheet) in optional_workbooks.items():
        if path.exists():
            try:
                result[key] = pd.read_excel(path, sheet_name=sheet)
            except Exception:
                result[key] = pd.DataFrame()
        else:
            result[key] = pd.DataFrame()
    result["manual_notes"] = load_manual_notes()
    if manifest_path.exists():
        result["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        result["manifest"] = {}
    return result


def fetch_live_game(game_id: str) -> tuple[dict, dict]:
    game_id = str(game_id).strip()
    if not game_id.isdigit():
        raise ValueError("Enter the numeric game ID from an AUSL game-page URL.")
    game = _get_json(f"/api/game-data/{game_id}/?sport=AUSL")
    box = _get_json(f"/api/box-score/ausl/{game_id}")
    return game, box.get("data", box)


if __name__ == "__main__":
    update_all_data(print)
