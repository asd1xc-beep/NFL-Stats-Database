AUSL BROADCAST STATS LOOKUP

This is a separate softball version of the NFL tool. It does not modify or
replace the NFL application.

START
If you are using the shared ZIP version, unzip the folder and double-click:
  AUSL Broadcast Stats.exe

If you are working from the project/source folder, you can also use:
  Launch AUSL Stats App.bat

DATA
Click "Update All Data" in the app. The updater downloads the official AUSL
2025 and 2026 roster/stat JSON files and builds local Excel workbooks under:
  data\exports

AUSL Career totals in this version combine the available 2025 and 2026 AUSL
regular-season files. They are not full softball career totals, college career
totals, Team USA totals, or All-Star Cup totals.

The app displays the latest local data timestamp in the header. Any exported
producer packet includes that timestamp plus a reminder to verify lineups,
availability, and milestones before air.

STORYLINE / ENRICHMENT DATA
The updater also imports official AUSL split stats, standings, schedule/results,
and a storyline source registry for producer prep. These are saved under:
  data\exports\ausl_batting_splits.xlsx
  data\exports\ausl_pitching_splits.xlsx
  data\exports\ausl_fielding_splits.xlsx
  data\exports\ausl_team_context.xlsx
  data\exports\ausl_storyline_sources.xlsx

Registered enrichment sources include the 2026 AUSL Media Guide, AUSL news,
MLB AUSL news, draft/Golden Ticket pages, college/award sources, and manual
producer notes. Treat those as context layers unless verified against official
game notes.

LIVE GAME
Open an official AUSL game page. If its URL ends in /game/969/, enter 969 as
the Game ID. The app will load the official game/box-score feed and can refresh
it every 30 seconds.

PRE-GAME REPORT
Select two different teams in Game Setup and click "Generate Producer Packet."
The text report includes stat-based projected lineups and starting pitchers,
team totals, milestone notes, players to watch, suggested graphics, and
jersey/roster references. Projections are not official and must be checked when
the teams release their game lineups.

PRODUCER PREP
The Producer Prep tab builds a quick matchup summary, players to watch,
milestone-watch notes, and a copyable graphics queue for the selected teams.

MANUAL NOTES
Use the Manual Notes tab to save pronunciation notes, producer reminders,
injury context, story ideas, or do-not-use notes. They are saved locally to:
  data\manual\player_notes.csv

TEAM TOTALS
The Team Totals tab provides MLB-style sortable Batting, Pitching, and Fielding
tables. Each table includes player rows and a highlighted TEAM TOTALS row.

Broadcast reminder: imported and live feed values should be verified against
the official game book before being aired.

SSL / UPDATE NOTE
The packaged app includes a current trusted certificate bundle for official
AUSL data downloads. If a shared copy reports that an SSL certificate has
expired, rebuild the ZIP with "Build Shareable AUSL App.bat" and replace the
older copy on that computer.
