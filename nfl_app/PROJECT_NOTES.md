# NFL Stats Database Project Notes

## Goal

Build a Windows-based NFL stats lookup/database tool for broadcast/GFX workflow, specifically for Tampa Bay Buccaneers preseason game prep.

The user works as a font coordinator with a GFX operator. The goal is to reduce manual stat entry and create a fast local lookup/database tool that can eventually feed Excel workbooks used by graphics systems.

## Current Project Folder

NFL_STATS_DATABASE\nfl_app

## Python Environment

Use Python 3.12 virtual environment.

Important commands:

```powershell
.\.venv\Scripts\Activate.ps1
python .\src\build_career_database.py
python .\src\search_player.py
Packages Installed
nflreadpy
pandas
polars
openpyxl
requests
beautifulsoup4
lxml
Important Decision

Do not rely on live scraping Pro Football Reference for production use.

We tried PFR scraping and got a 403 response. For reliability, use nflverse data through nflreadpy.

Working Data Source

nflreadpy.load_player_stats

Current roster membership, jersey number, position, and status come from the
official NFL.com team roster pages. nflreadpy/nflverse supplies GSIS IDs and
biographical enrichment, and is used as a clearly labeled per-team fallback if
an official page cannot be loaded or parsed safely.

Tested signature:

load_player_stats(
    seasons: int | list[int] | bool | None = None,
    summary_level: Literal["week", "reg", "post", "reg+post"] = "week"
)

Using:

stats = nfl.load_player_stats(seasons=[2024], summary_level="reg")

worked successfully.

Useful Columns Found
player_id
player_name
player_display_name
position
position_group
headshot_url
season
season_type
recent_team
games
completions
attempts
passing_yards
passing_tds
passing_interceptions
sacks_suffered
sack_yards_lost
carries
rushing_yards
rushing_tds
rushing_fumbles
rushing_fumbles_lost
receptions
targets
receiving_yards
receiving_tds
receiving_fumbles
receiving_fumbles_lost
special_teams_tds
def_tackles_solo
def_tackle_assists
def_sacks
def_interceptions
def_pass_defended
def_tds
fg_made
fg_att
fg_long
pat_made
pat_att
fantasy_points
fantasy_points_ppr
Working Milestone

build_career_database.py successfully created:

data\exports\nfl_career_database.xlsx

Mike Evans appeared correctly in the file.

Current Desired Next Step

Build a local terminal lookup tool:

python .\src\lookup_player.py

The tool should allow typing an NFL player name and return position-specific stats.

It should show both:

Career stats
Single-season 2025 stats
Desired Lookup Behavior

Example:

Enter player name: Mike Evans

Mike Evans | WR | TB

CAREER
Games:
Receptions:
Receiving Yards:
Receiving TD:
Targets:
Total TD:

2025 SEASON
Games:
Receptions:
Receiving Yards:
Receiving TD:
Targets:
Total TD:

Position-specific sections:

QB:

games
completions
attempts
passing yards
passing TD
interceptions
sacks
rushing yards
rushing TD
total TD

RB/FB:

games
carries
rushing yards
rushing TD
receptions
receiving yards
receiving TD
total TD

WR/TE:

games
targets
receptions
receiving yards
receiving TD
rushing yards
rushing TD
total TD

K:

games
FG made
FG attempts
FG long
PAT made
PAT attempts

Defense:

games
solo tackles
assists
sacks
interceptions
passes defended
forced fumbles
defensive TD
Important Code Note

When using summary_level="reg", the team column is named:

recent_team

not:

team
Build Script Direction

Update build_career_database.py so it creates two files:

data\exports\nfl_career_database.xlsx
data\exports\nfl_2025_database.xlsx

Career should probably include 1999 through 2025:

CAREER_SEASONS = list(range(1999, 2026))
CURRENT_SEASON = 2025

Use a reusable function like:

def build_totals(stats: pl.DataFrame, prefix: str) -> pl.DataFrame:

Career columns should be prefixed with:

career_

2025 columns should be prefixed with:

season_2025_

Add calculated total TD:

passing_tds + rushing_tds + receiving_tds + special_teams_tds + def_tds
Next Coding Task

Create or update:

src\lookup_player.py

It should:

Load nfl_career_database.xlsx
Load nfl_2025_database.xlsx
Ask user for a player name
Find player by exact, partial, or close match
Merge/display the career row and 2025 row by player_id
Print different stat sections based on position
Loop until user types Q

## Broadcast Tool Expansion (July 2026)

The Tkinter desktop app is now the primary interface. It includes:

- Home/away game setup with game-team-only search scope
- Player, jersey-number, and team-roster lookup
- Offense, defense, and special-teams roster tabs
- Broadcast player card with career, latest-season, and preseason summaries
- Copy-ready lower third, fullscreen, announcer-note, and jersey-ID text
- Manual situational game counters with copy-ready notes
- Rule-based next-graphic suggestions
- Text pre-game packet generation in `data/exports/game_packets/`
- Imported/manual/missing confidence labels
- Team-code normalization and blank-player data cleanup
- Experimental ESPN Live Game tab with 15-second polling
- Live team comparison, player leaders, feed health, and last-update time
- Manual-counter handoff plus frozen/copy-ready halftime comparisons

The ESPN connector is deliberately marked YELLOW/unverified. Manual counters remain
the fallback and can be edited after importing live values. The live provider is isolated
in `src/live_game.py` so a future Sportradar integration can replace it cleanly.

GSIS development now includes an official archived 2014 NYG-at-NYJ preseason XML parser
and replay provider. Choose `GSIS Sample Replay` in the Live Game tab. The replay advances
through 296 official game-to-date XML snapshots at one snapshot per second. If the sample
is absent, the provider downloads it from the NFL GSIS documentation site on first use.

The Live Game tab also has a player-name/jersey search. Results come from the current live
snapshot and refresh automatically, with available passing, rushing, receiving, defensive,
kicking, punting, return, and fumble lines. Both GSIS XML and ESPN are supported.

The baseline tag `working-base-v1` predates the desktop broadcast expansion.
