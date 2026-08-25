from __future__ import annotations

from difflib import get_close_matches
from datetime import datetime
from pathlib import Path
import os
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import pandas as pd

from app_paths import APP_ROOT, EXPORT_DIR
from broadcast_tools import (
    GRAPHIC_SUGGESTIONS, TEAM_NAMES, career_team_codes, clean_dataframe,
    format_number, milestone_note, normalize_team_code, ordinal_experience,
    packet_filename, safe_get, write_game_packet,
)
from build_career_database import (
    CURRENT_SEASON, PRESEASON_SEASON, build_active_roster, build_preseason_totals,
    build_team_history, build_totals, export_database, load_stats,
    load_team_history_stats,
)
from font_sheet import write_font_sheet
from call_sheet import (
    duplicate_keys, duplicate_values, load_call_sheet, normalize_call_up,
    save_call_sheet,
)
from lookup_player import (
    CAREER_FILE, DEFENSIVE_POSITIONS, OFFENSIVE_POSITIONS, PRESEASON_FILE,
    ROSTER_FILE, SEASON_FILE, SPECIAL_TEAMS_POSITIONS, STAT_GROUPS,
    TEAM_ALIASES, format_value, parse_team_command, stat_group,
)
from live_game import ESPNLiveProvider, GSISReplayProvider, LiveGameSnapshot


APP_TITLE = "NFL Stats Lookup — Broadcast Tool"
TEAM_OPTIONS = sorted(TEAM_NAMES.items(), key=lambda item: item[1])
TEAM_LABEL_TO_CODE = {f"{name} ({code})": code for code, name in TEAM_OPTIONS}
TEAM_CODE_TO_LABEL = {code: label for label, code in TEAM_LABEL_TO_CODE.items()}

ROSTER_STATUS_LABELS = {
    "ACT": "Active", "DEV": "Practice Squad", "INA": "Inactive",
    "PUP": "PUP", "RES": "Injured Reserve", "RSN": "Non-Football Injury Reserve",
    "SUS": "Suspended", "EXE": "Commissioner Exempt",
    "E14": "International Player Exemption",
}

CALL_SHEET_UNITS = ("All", "Offense", "Defense", "Special Teams")
JERSEY_NUMBERS_PER_ROW = 10
JERSEY_GRID_ROWS = 10

COUNTER_FIELDS = [
    ("third_down_made", "3rd Down Made"),
    ("third_down_attempts", "3rd Down Attempts"),
    ("red_zone_scores", "Red Zone Scores"),
    ("red_zone_trips", "Red Zone Trips"),
    ("sacks", "Sacks"),
    ("turnovers", "Turnovers"),
    ("penalties", "Penalties"),
    ("penalty_yards", "Penalty Yards"),
    ("explosive_plays", "Explosive Plays"),
]


class NFLStatsApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1180x820")
        self.minsize(980, 680)

        self.career = pd.DataFrame()
        self.season = pd.DataFrame()
        self.preseason = pd.DataFrame()
        self.roster = pd.DataFrame()
        self.matches = pd.DataFrame()
        self.team_matches = {"off": pd.DataFrame(), "def": pd.DataFrame(), "kp": pd.DataFrame()}
        self.result_mode = "player"
        self.current_player = {}

        self.search_text = tk.StringVar()
        self.search_game_teams_only = tk.BooleanVar(value=True)
        self.home_team_selection = tk.StringVar(value=TEAM_CODE_TO_LABEL["TB"])
        self.away_team_selection = tk.StringVar(value=TEAM_CODE_TO_LABEL["NYJ"])
        self.team_selection = tk.StringVar(value=TEAM_CODE_TO_LABEL["TB"])
        self.home_team_label = tk.StringVar(value="TB")
        self.away_team_label = tk.StringVar(value="NYJ")
        self.status_text = tk.StringVar(value="Loading databases...")
        self.data_freshness_text = tk.StringVar(value="Data as of — checking...")

        self.player_title = tk.StringVar(value="Search for a player to begin")
        self.player_bio = tk.StringVar(value="")
        self.player_status = tk.StringVar(value="")
        self.player_teams = tk.StringVar(value="Career Teams: —")
        self.career_summary = tk.StringVar(value="Career (Reg Season): No player selected")
        self.season_summary = tk.StringVar(value=f"{CURRENT_SEASON} (Reg Season): No player selected")
        self.preseason_summary = tk.StringVar(value=f"{PRESEASON_SEASON} Preseason: No player selected")
        self.broadcast_note = tk.StringVar(value="Broadcast Note: —")
        self.confidence_text = tk.StringVar(value="Stats: RED — no player selected")
        self.suggestion_title = tk.StringVar(value="Select an event")
        self.live_game_text = tk.StringVar(value="No live game connected")
        self.live_status_text = tk.StringVar(value="Feed: disconnected")
        self.live_updated_text = tk.StringVar(value="Last update: —")
        self.live_leaders_text = tk.StringVar(value="Player leaders will appear after connecting.")
        self.live_player_search_text = tk.StringVar()
        self.live_player_result_text = tk.StringVar(value="Search the current game for any player.")
        self.live_provider_selection = tk.StringVar(value="ESPN Experimental")
        self.live_provider = ESPNLiveProvider()
        self.live_event_id: str | None = None
        self.live_snapshot: LiveGameSnapshot | None = None
        self.halftime_snapshot: LiveGameSnapshot | None = None
        self.live_polling = False
        self.live_after_id = None
        self.live_request_in_progress = False

        self.counters = {
            side: {key: tk.IntVar(value=0) for key, _label in COUNTER_FIELDS}
            for side in ("home", "away")
        }

        # Call sheet: {entry_key: {"call_up", "name", "jersey", "position", "team"}}.
        # Kept out of the stat databases, which "Update All Data" fully overwrites.
        self.call_sheet_entries: dict[str, dict] = {}
        self.call_sheet_matchup: tuple[str, str] | None = None
        self.call_sheet_rows: dict[str, list[dict]] = {"home": [], "away": []}
        self.call_sheet_unit = {
            side: tk.StringVar(value="All") for side in ("home", "away")
        }
        self.call_sheet_counter = {
            side: tk.StringVar(value="0/0 assigned") for side in ("home", "away")
        }
        self.call_sheet_team_label = {
            side: tk.StringVar(value=side.title()) for side in ("home", "away")
        }
        self.call_sheet_search_text = tk.StringVar()
        self.call_sheet_warning = tk.StringVar(value="")
        self._callup_editor: ttk.Entry | None = None
        self._callup_highlight_job: dict[str, str] = {}
        self.jersey_cells: dict[str, dict[int, dict]] = {"home": {}, "away": {}}

        self._set_style()
        self._build_window()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self.load_databases)

    def _set_style(self) -> None:
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Player.TLabel", font=("Segoe UI", 16, "bold"))
        style.configure("Card.TLabel", font=("Segoe UI", 10))
        style.configure("CardBold.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Status.TLabel", foreground="#555555")
        style.configure("Yellow.TLabel", foreground="#8a6500", font=("Segoe UI", 9, "bold"))
        style.configure("Green.TLabel", foreground="#176b2c", font=("Segoe UI", 9, "bold"))
        style.configure("Treeview", rowheight=26, font=("Segoe UI", 10))
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        style.configure("Warning.TLabel", foreground="#b00020", font=("Segoe UI", 10, "bold"))
        style.configure("Counter.TLabel", font=("Segoe UI", 10, "bold"))

    def _build_window(self) -> None:
        header = ttk.Frame(self, padding=(16, 12, 16, 8))
        header.pack(fill="x")
        ttk.Label(header, text="NFL Stats Lookup", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="Generate Pre-Game Packet", command=self.generate_pregame_packet).pack(side="right")
        ttk.Button(header, text="Build Font Sheet", command=self.build_font_sheet).pack(side="right", padx=(0, 8))
        self.update_button = ttk.Button(header, text="Update All Data", command=self.update_all_data)
        self.update_button.pack(side="right", padx=(0, 8))

        self._build_game_setup()

        self.workspace_tabs = ttk.Notebook(self)
        self.workspace_tabs.pack(fill="both", expand=True, padx=16)
        self.player_page = ttk.Frame(self.workspace_tabs)
        self.call_sheet_page = ttk.Frame(self.workspace_tabs, padding=10)
        self.jersey_page = ttk.Frame(self.workspace_tabs, padding=10)
        self.live_page = ttk.Frame(self.workspace_tabs, padding=14)
        self.situational_page = ttk.Frame(self.workspace_tabs, padding=14)
        self.suggestions_page = ttk.Frame(self.workspace_tabs, padding=14)
        self.workspace_tabs.add(self.player_page, text="Player Lookup")
        self.workspace_tabs.add(self.call_sheet_page, text="Call Sheet")
        self.workspace_tabs.add(self.jersey_page, text="Jersey Grid")
        self.workspace_tabs.add(self.live_page, text="Live Game")
        self.workspace_tabs.add(self.situational_page, text="Situational Stats")
        self.workspace_tabs.add(self.suggestions_page, text="Suggested Next Graphics")

        self._build_player_lookup()
        self._build_call_sheet_tab()
        self._build_jersey_grid_tab()
        self._build_live_tab()
        self._build_situational_tab()
        self._build_suggestions_tab()
        self.workspace_tabs.bind("<<NotebookTabChanged>>", self._workspace_tab_changed)

        status = ttk.Frame(self, padding=(16, 8, 16, 10))
        status.pack(fill="x")
        ttk.Label(status, textvariable=self.status_text, style="Status.TLabel").pack(side="left")

    def _build_game_setup(self) -> None:
        frame = ttk.Labelframe(self, text="Game Setup", padding=(10, 7))
        frame.pack(fill="x", padx=16, pady=(0, 8))
        team_labels = list(TEAM_LABEL_TO_CODE)

        ttk.Label(frame, text="Home Team:").grid(row=0, column=0, sticky="w")
        home = ttk.Combobox(frame, textvariable=self.home_team_selection, values=team_labels, state="readonly", width=29)
        home.grid(row=0, column=1, padx=(6, 14))
        home.bind("<<ComboboxSelected>>", self._game_teams_changed)

        ttk.Label(frame, text="Away Team:").grid(row=0, column=2, sticky="w")
        away = ttk.Combobox(frame, textvariable=self.away_team_selection, values=team_labels, state="readonly", width=29)
        away.grid(row=0, column=3, padx=(6, 14))
        away.bind("<<ComboboxSelected>>", self._game_teams_changed)

        ttk.Checkbutton(
            frame, text="Search game teams only", variable=self.search_game_teams_only
        ).grid(row=0, column=4, padx=(4, 12))
        ttk.Button(frame, text="Open Home Roster", command=lambda: self._open_game_roster("home")).grid(row=0, column=5, padx=3)
        ttk.Button(frame, text="Open Away Roster", command=lambda: self._open_game_roster("away")).grid(row=0, column=6, padx=3)

        ttk.Label(
            frame, textvariable=self.data_freshness_text, style="Status.TLabel",
        ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))

    def _build_player_lookup(self) -> None:
        search_frame = ttk.Frame(self.player_page, padding=(0, 2, 0, 8))
        search_frame.pack(fill="x")
        self.search_entry = ttk.Entry(search_frame, textvariable=self.search_text, font=("Segoe UI", 12))
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Return>", lambda _event: self.search_players())
        ttk.Button(search_frame, text="Search", command=self.search_players).pack(side="left", padx=(8, 0))

        team_frame = ttk.Frame(self.player_page, padding=(0, 0, 0, 8))
        team_frame.pack(fill="x")
        ttk.Label(team_frame, text="Browse roster:").pack(side="left", padx=(0, 8))
        self.team_dropdown = ttk.Combobox(
            team_frame, textvariable=self.team_selection, values=list(TEAM_LABEL_TO_CODE),
            state="readonly", font=("Segoe UI", 10), width=34,
        )
        self.team_dropdown.pack(side="left")
        self.team_dropdown.bind("<<ComboboxSelected>>", self.show_selected_team)

        content = ttk.Panedwindow(self.player_page, orient="horizontal")
        content.pack(fill="both", expand=True)
        results_frame = ttk.Labelframe(content, text="Players", padding=6)
        details_frame = ttk.Frame(content, padding=(10, 0, 0, 0))
        content.add(results_frame, weight=1)
        content.add(details_frame, weight=2)

        self.result_tabs = ttk.Notebook(results_frame)
        self.result_tabs.pack(fill="both", expand=True)
        self.results = self._create_player_list_tab("Search")
        self.results.bind("<<ListboxSelect>>", self.show_selected_player)
        self.team_lists = {
            "off": self._create_player_list_tab("Offense"),
            "def": self._create_player_list_tab("Defense"),
            "kp": self._create_player_list_tab("Special Teams"),
        }
        for unit, player_list in self.team_lists.items():
            player_list.bind(
                "<<ListboxSelect>>",
                lambda event, unit=unit: self.show_selected_team_player(event, unit),
            )

        self._build_player_card(details_frame)
        self.stat_tabs = ttk.Notebook(details_frame)
        self.stat_tabs.pack(fill="both", expand=True, pady=(8, 0))
        self.career_tree = self._create_stat_tab("Career (Reg Season)")
        self.season_tree = self._create_stat_tab(f"{CURRENT_SEASON} Season (Reg Season)")
        self.preseason_tree = self._create_stat_tab(f"{PRESEASON_SEASON} Preseason")

    def _build_player_card(self, parent: ttk.Frame) -> None:
        card = ttk.Labelframe(parent, text="Broadcast Player Card", padding=10)
        card.pack(fill="x")
        ttk.Label(card, textvariable=self.player_title, style="Player.TLabel").pack(anchor="w")
        ttk.Label(card, textvariable=self.player_status, style="CardBold.TLabel", wraplength=690).pack(anchor="w", pady=(2, 0))
        ttk.Label(card, textvariable=self.player_bio, style="Card.TLabel", wraplength=690).pack(anchor="w", pady=(1, 0))
        ttk.Label(card, textvariable=self.player_teams, style="Card.TLabel", wraplength=690).pack(anchor="w", pady=(1, 6))
        ttk.Separator(card).pack(fill="x", pady=3)
        ttk.Label(card, textvariable=self.career_summary, style="CardBold.TLabel", wraplength=690).pack(anchor="w")
        ttk.Label(card, textvariable=self.season_summary, style="Card.TLabel", wraplength=690).pack(anchor="w")
        ttk.Label(card, textvariable=self.preseason_summary, style="Card.TLabel", wraplength=690).pack(anchor="w")
        ttk.Label(card, textvariable=self.broadcast_note, style="Card.TLabel", wraplength=690).pack(anchor="w", pady=(5, 0))
        ttk.Label(card, textvariable=self.confidence_text, style="Yellow.TLabel").pack(anchor="w", pady=(3, 5))

        buttons = ttk.Frame(card)
        buttons.pack(fill="x")
        for label, kind in (
            ("Copy Lower Third", "lower"), ("Copy Fullscreen Stat", "fullscreen"),
            ("Copy Announcer Note", "announcer"), ("Copy Jersey ID", "jersey"),
        ):
            ttk.Button(buttons, text=label, command=lambda kind=kind: self.copy_player_gfx(kind)).pack(side="left", padx=(0, 6))

    # ------------------------------------------------------------------ #
    # Call Sheet                                                          #
    # ------------------------------------------------------------------ #

    def _build_call_sheet_tab(self) -> None:
        top = ttk.Frame(self.call_sheet_page)
        top.pack(fill="x", pady=(0, 8))
        ttk.Label(top, text="Find #:").pack(side="left")
        search = ttk.Entry(top, textvariable=self.call_sheet_search_text, width=18, font=("Segoe UI", 11))
        search.pack(side="left", padx=(6, 6))
        search.bind("<Return>", lambda _event: self.call_sheet_search())
        ttk.Button(top, text="Jump", command=self.call_sheet_search).pack(side="left")
        ttk.Label(
            top, text="searches both teams and every unit, whatever the filters are set to",
            style="Status.TLabel",
        ).pack(side="left", padx=(10, 0))
        ttk.Button(top, text="Copy Both", command=lambda: self.copy_call_sheet("both")).pack(side="right")
        ttk.Button(top, text="Copy Away", command=lambda: self.copy_call_sheet("away")).pack(side="right", padx=6)
        ttk.Button(top, text="Copy Home", command=lambda: self.copy_call_sheet("home")).pack(side="right")

        ttk.Label(
            self.call_sheet_page, textvariable=self.call_sheet_warning, style="Warning.TLabel",
        ).pack(fill="x", pady=(0, 6))

        tables = ttk.Frame(self.call_sheet_page)
        tables.pack(fill="both", expand=True)
        tables.columnconfigure(0, weight=1, uniform="callsheet")
        tables.columnconfigure(1, weight=1, uniform="callsheet")
        tables.rowconfigure(0, weight=1)
        self.call_sheet_trees = {
            "home": self._build_call_sheet_table(tables, "home", 0),
            "away": self._build_call_sheet_table(tables, "away", 1),
        }

    def _build_call_sheet_table(self, parent: ttk.Frame, side: str, column: int) -> ttk.Treeview:
        frame = ttk.Labelframe(parent, padding=6)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column == 0 else (6, 0))
        frame.configure(labelwidget=ttk.Label(parent, textvariable=self.call_sheet_team_label[side],
                                              style="CardBold.TLabel"))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, 5))
        ttk.Label(controls, text="Unit:").pack(side="left")
        unit = ttk.Combobox(
            controls, textvariable=self.call_sheet_unit[side], values=CALL_SHEET_UNITS,
            state="readonly", width=14,
        )
        unit.pack(side="left", padx=(5, 0))
        unit.bind("<<ComboboxSelected>>", lambda _event: self._refresh_call_sheet())
        ttk.Label(
            controls, textvariable=self.call_sheet_counter[side], style="Counter.TLabel",
        ).pack(side="right")

        holder = ttk.Frame(frame)
        holder.pack(fill="both", expand=True)
        tree = ttk.Treeview(holder, columns=("jersey", "pos", "name", "callup"), show="headings")
        tree.heading("jersey", text="#")
        tree.heading("pos", text="Pos")
        tree.heading("name", text="Name")
        tree.heading("callup", text="Call-Up #")
        tree.column("jersey", width=44, anchor="center", stretch=False)
        tree.column("pos", width=50, anchor="center", stretch=False)
        tree.column("name", width=180, anchor="w")
        tree.column("callup", width=90, anchor="center", stretch=False)
        tree.tag_configure("dupe", background="#ffd6d6", foreground="#8a0000")
        tree.tag_configure("offroster", background="#eeeeee", foreground="#777777")
        tree.tag_configure("hit", background="#ffe89a")
        tree.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")

        tree.bind("<Double-1>", lambda event, side=side: self._begin_callup_edit(event, side))
        tree.bind("<Return>", lambda event, side=side: self._edit_selected_callup(side))
        return tree

    @staticmethod
    def _call_sheet_keys(row) -> tuple[str, str]:
        """Return (primary, legacy) keys for a roster row.

        player_id is the GSIS id used to join roster/career/season data everywhere
        else, so it is the right key: a mid-week roster refresh must not disturb
        numbers already typed against players who are still on the roster. A small
        number of camp-roster rookies have no GSIS id yet, so those fall back to a
        name/team composite; if an id is assigned later the value migrates on load.
        """
        team = normalize_team_code(safe_get(row, "team"))
        name = str(safe_get(row, "player_display_name", "")).strip().casefold()
        legacy = f"name:{team}:{name}"
        player_id = str(safe_get(row, "player_id", "")).strip()
        return (player_id or legacy), legacy

    def _game_sides(self) -> dict[str, str]:
        """Home/away codes keyed by side.

        get_game_team_codes() de-duplicates, which loses the side distinction when
        the same team is picked twice, so read the two selections directly here.
        """
        return {
            "home": TEAM_LABEL_TO_CODE.get(self.home_team_selection.get(), ""),
            "away": TEAM_LABEL_TO_CODE.get(self.away_team_selection.get(), ""),
        }

    def _load_call_sheet_for_matchup(self, home: str, away: str) -> None:
        self.call_sheet_entries = load_call_sheet(home, away)
        self.call_sheet_matchup = (home, away)

    def _call_sheet_side_rows(self, side: str, team: str) -> list[dict]:
        """Every row for one side: current roster plus assigned players who left it."""
        rows: list[dict] = []
        seen: set[str] = set()
        if not self.roster.empty and team:
            team_rows = self.roster[self.roster["team"] == team]
            for _, player in team_rows.iterrows():
                key, legacy = self._call_sheet_keys(player)
                entry = self.call_sheet_entries.get(key) or self.call_sheet_entries.get(legacy) or {}
                seen.add(key)
                rows.append({
                    "key": key,
                    "jersey": self._format_jersey(safe_get(player, "jersey_number")),
                    "position": str(safe_get(player, "position", "")).upper(),
                    "name": str(safe_get(player, "player_display_name", "")),
                    "team": team,
                    "call_up": str(entry.get("call_up", "")).strip(),
                    "on_roster": True,
                    "roster_row": player,
                })
        # Keep assigned players who dropped off the roster rather than deleting their
        # work — roster moves get reversed, and re-typing numbers is exactly the
        # friction this tab exists to remove.
        for key, entry in self.call_sheet_entries.items():
            if key in seen or normalize_team_code(entry.get("team")) != team:
                continue
            rows.append({
                "key": key,
                "jersey": str(entry.get("jersey", "")),
                "position": str(entry.get("position", "")).upper(),
                "name": str(entry.get("name", "")),
                "team": team,
                "call_up": str(entry.get("call_up", "")).strip(),
                "on_roster": False,
                "roster_row": None,
            })
        rows.sort(key=lambda row: (
            int(row["jersey"]) if row["jersey"].isdigit() else 1000, row["name"]
        ))
        return rows

    @staticmethod
    def _matches_unit(position: str, unit: str) -> bool:
        if unit == "Offense":
            return position in OFFENSIVE_POSITIONS
        if unit == "Defense":
            return position in DEFENSIVE_POSITIONS
        if unit == "Special Teams":
            return position in SPECIAL_TEAMS_POSITIONS
        return True

    def _refresh_call_sheet(self, force_reload: bool = False) -> None:
        if not hasattr(self, "call_sheet_trees"):
            return
        sides = self._game_sides()
        home, away = sides["home"], sides["away"]
        if not home or not away:
            return
        if force_reload or self.call_sheet_matchup != (home, away):
            self._cancel_callup_edit()
            self._load_call_sheet_for_matchup(home, away)

        dupes = duplicate_keys(self.call_sheet_entries)
        for side in ("home", "away"):
            team = sides[side]
            self.call_sheet_team_label[side].set(
                f"{side.title()} — {TEAM_NAMES.get(team, team)}"
            )
            self.call_sheet_rows[side] = self._call_sheet_side_rows(side, team)
            unit = self.call_sheet_unit[side].get()
            visible = [
                row for row in self.call_sheet_rows[side]
                if self._matches_unit(row["position"], unit)
            ]
            tree = self.call_sheet_trees[side]
            tree.delete(*tree.get_children())
            for row in visible:
                tags = []
                if row["key"] in dupes:
                    tags.append("dupe")
                elif not row["on_roster"]:
                    tags.append("offroster")
                name = row["name"] if row["on_roster"] else f"{row['name']}  (not on current roster)"
                tree.insert(
                    "", "end", iid=row["key"],
                    values=(row["jersey"] or "—", row["position"] or "—", name, row["call_up"]),
                    tags=tags,
                )
            assigned = sum(1 for row in visible if row["call_up"])
            self.call_sheet_counter[side].set(f"{assigned}/{len(visible)} assigned")

        values = duplicate_values(self.call_sheet_entries)
        self.call_sheet_warning.set(
            "⚠  DUPLICATE CALL-UP NUMBER"
            + ("S" if len(values) > 1 else "")
            + f" — {', '.join(values)}  (every affected row is highlighted red)"
            if values else ""
        )

    # -- inline Call-Up # editing ---------------------------------------- #

    def _cancel_callup_edit(self) -> None:
        if self._callup_editor is not None:
            editor, self._callup_editor = self._callup_editor, None
            editor.destroy()

    def _edit_selected_callup(self, side: str) -> None:
        selection = self.call_sheet_trees[side].selection()
        if selection:
            self._open_callup_editor(side, selection[0])

    def _begin_callup_edit(self, event, side: str) -> None:
        tree = self.call_sheet_trees[side]
        if tree.identify_region(event.x, event.y) != "cell":
            return
        if tree.identify_column(event.x) != "#4":  # only Call-Up # is editable
            return
        key = tree.identify_row(event.y)
        if key:
            self._open_callup_editor(side, key)

    def _open_callup_editor(self, side: str, key: str) -> None:
        self._cancel_callup_edit()
        tree = self.call_sheet_trees[side]
        tree.see(key)
        tree.update_idletasks()
        box = tree.bbox(key, "#4")
        if not box:
            return
        x, y, width, height = box
        editor = ttk.Entry(tree, justify="center", font=("Segoe UI", 10))
        editor.place(x=x, y=y, width=width, height=height)
        editor.insert(0, tree.set(key, "callup"))
        editor.select_range(0, tk.END)
        editor.focus_set()
        self._callup_editor = editor

        def commit(_event=None, advance: bool = False) -> None:
            if self._callup_editor is not editor:
                return
            self._callup_editor = None
            value = editor.get()
            editor.destroy()
            self._commit_callup(side, key, value)
            if advance:
                following = tree.next(key)
                if following:
                    self._open_callup_editor(side, following)

        editor.bind("<Return>", lambda event: commit(event, advance=True))
        editor.bind("<KP_Enter>", lambda event: commit(event, advance=True))
        editor.bind("<FocusOut>", commit)
        editor.bind("<Escape>", lambda _event: self._cancel_callup_edit())

    def _commit_callup(self, side: str, key: str, value: str) -> None:
        """Auto-save on every commit — no Save button to forget minutes before air."""
        value = value.strip()
        row = next((item for item in self.call_sheet_rows[side] if item["key"] == key), None)
        existing = self.call_sheet_entries.get(key, {})
        if value:
            self.call_sheet_entries[key] = {
                "call_up": value,
                "name": row["name"] if row else existing.get("name", ""),
                "jersey": row["jersey"] if row else existing.get("jersey", ""),
                "position": row["position"] if row else existing.get("position", ""),
                "team": row["team"] if row else existing.get("team", ""),
            }
        else:
            self.call_sheet_entries.pop(key, None)
        self._save_call_sheet()
        self._refresh_call_sheet()

    def _save_call_sheet(self) -> None:
        if not self.call_sheet_matchup:
            return
        home, away = self.call_sheet_matchup
        try:
            save_call_sheet(home, away, self.call_sheet_entries)
        except OSError as error:
            self.status_text.set(f"CALL SHEET SAVE FAILED — {error}")
            messagebox.showerror(APP_TITLE, f"Could not save the call sheet:\n\n{error}")
            return
        self.status_text.set(
            f"Call sheet saved — {len(self.call_sheet_entries)} assigned "
            f"({away} at {home})"
        )

    def call_sheet_search(self) -> None:
        """Find a jersey number (or name) across both teams, ignoring unit filters."""
        query = self.call_sheet_search_text.get().strip().lstrip("#").strip()
        if not query:
            return
        wanted = query.lstrip("0").casefold()
        matches: list[tuple[str, dict]] = []
        for side in ("home", "away"):
            for row in self.call_sheet_rows[side]:
                jersey = row["jersey"].lstrip("0").casefold()
                if query.isdigit():
                    if jersey == wanted:
                        matches.append((side, row))
                elif wanted and wanted in row["name"].casefold():
                    matches.append((side, row))
        if not matches:
            self.status_text.set(f'Call sheet: no player matching "{query}" on either team')
            return
        # The searched row may be hidden by that table's unit filter; the whole point
        # of this box is an instant answer, so clear the filter that would hide it.
        for side, row in matches:
            if not self._matches_unit(row["position"], self.call_sheet_unit[side].get()):
                self.call_sheet_unit[side].set("All")
        self._refresh_call_sheet()
        for side, row in matches:
            self._flash_call_sheet_row(side, row["key"])
        side, row = matches[0]
        self.call_sheet_trees[side].focus_set()
        found = ", ".join(f"{item['jersey'] or '?'} {item['name']} ({side.upper()})" for side, item in matches[:4])
        self.status_text.set(f"Call sheet: {len(matches)} match(es) — {found}")

    def _flash_call_sheet_row(self, side: str, key: str) -> None:
        tree = self.call_sheet_trees[side]
        if not tree.exists(key):
            return
        tree.see(key)
        tree.selection_set(key)
        original = tree.item(key, "tags")
        tree.item(key, tags=("hit",))
        job = self._callup_highlight_job.pop(f"{side}:{key}", None)
        if job:
            self.after_cancel(job)

        def restore() -> None:
            self._callup_highlight_job.pop(f"{side}:{key}", None)
            if tree.exists(key):
                tree.item(key, tags=original)

        self._callup_highlight_job[f"{side}:{key}"] = self.after(2500, restore)

    def call_sheet_lines(self, side: str) -> list[str]:
        """Copy-ready block for one team; assigned rows only."""
        team = self._game_sides()[side]
        rows = [row for row in self.call_sheet_rows[side] if row["call_up"]]
        lines = [f"{TEAM_NAMES.get(team, team).upper()} — GFX CALL SHEET"]
        if not rows:
            lines.append("  (no call-up numbers assigned yet)")
            return lines
        for row in rows:
            flag = "" if row["on_roster"] else "   [NOT ON CURRENT ROSTER]"
            lines.append(
                f"#{row['jersey'] or '?'} — {row['name']} — {row['position'] or '?'} — {row['call_up']}{flag}"
            )
        lines.append(f"  {len(rows)} assigned")
        return lines

    def copy_call_sheet(self, which: str) -> None:
        if which == "both":
            text = "\n".join(self.call_sheet_lines("home") + [""] + self.call_sheet_lines("away"))
            label = "both call sheets"
        else:
            text = "\n".join(self.call_sheet_lines(which))
            label = f"{which} call sheet"
        self._copy_to_clipboard(text, f"Copied {label} to clipboard")

    # ------------------------------------------------------------------ #
    # Jersey Grid                                                         #
    # ------------------------------------------------------------------ #

    def _build_jersey_grid_tab(self) -> None:
        ttk.Label(
            self.jersey_page,
            text="Who is #__ — active roster numbers for the selected game. Click a filled square to open that player.",
            style="Status.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        grids = ttk.Frame(self.jersey_page)
        grids.pack(fill="both", expand=True)
        grids.columnconfigure(0, weight=1, uniform="jersey")
        grids.columnconfigure(1, weight=1, uniform="jersey")
        grids.rowconfigure(0, weight=1)
        self.jersey_frames = {
            "home": self._build_jersey_grid(grids, "home", 0),
            "away": self._build_jersey_grid(grids, "away", 1),
        }

    def _build_jersey_grid(self, parent: ttk.Frame, side: str, column: int) -> ttk.Labelframe:
        frame = ttk.Labelframe(parent, text=side.title(), padding=6)
        frame.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column == 0 else (6, 0))
        for index in range(JERSEY_NUMBERS_PER_ROW):
            frame.columnconfigure(index, weight=1, uniform="cell")
        for index in range(JERSEY_GRID_ROWS):
            frame.rowconfigure(index, weight=1, uniform="cell")

        self.jersey_cells[side] = {}
        for number in range(JERSEY_GRID_ROWS * JERSEY_NUMBERS_PER_ROW):
            cell = tk.Label(
                frame, text=str(number), font=("Segoe UI", 7), width=7, height=2,
                relief="solid", borderwidth=1, background="#f5f5f5", foreground="#c0c0c0",
                justify="center", anchor="center",
            )
            cell.grid(
                row=number // JERSEY_NUMBERS_PER_ROW,
                column=number % JERSEY_NUMBERS_PER_ROW,
                sticky="nsew", padx=1, pady=1,
            )
            cell.bind("<Button-1>", lambda _event, side=side, number=number:
                      self._jersey_cell_clicked(side, number))
            self.jersey_cells[side][number] = {"widget": cell, "players": []}
        return frame

    def _refresh_jersey_grid(self) -> None:
        if not hasattr(self, "jersey_frames"):
            return
        sides = self._game_sides()
        for side in ("home", "away"):
            team = sides[side]
            self.jersey_frames[side].configure(text=f"{side.title()} — {TEAM_NAMES.get(team, team)}")
            by_number: dict[int, list[pd.Series]] = {}
            if not self.roster.empty and team:
                for _, player in self.roster[self.roster["team"] == team].iterrows():
                    number = self._jersey_int(safe_get(player, "jersey_number"))
                    if number is None:
                        continue  # blank/odd jersey: still findable via Player Lookup
                    by_number.setdefault(number, []).append(player)
            for number, cell in self.jersey_cells[side].items():
                players = by_number.get(number, [])
                cell["players"] = players
                widget = cell["widget"]
                if not players:
                    widget.configure(
                        text=str(number), background="#f5f5f5", foreground="#c0c0c0",
                        font=("Segoe UI", 7),
                    )
                    continue
                name = self._short_name(safe_get(players[0], "player_display_name", ""))
                extra = f" +{len(players) - 1}" if len(players) > 1 else ""
                widget.configure(
                    text=f"{number}\n{name}{extra}",
                    # Amber flags a number worn by more than one player, which is
                    # common on 90-man preseason rosters.
                    background="#fdebc8" if len(players) > 1 else "#e6f0fb",
                    foreground="#102a43",
                    font=("Segoe UI", 7, "bold" if len(players) > 1 else "normal"),
                )

    @staticmethod
    def _jersey_int(value) -> int | None:
        try:
            number = int(float(value))
        except (TypeError, ValueError):
            return None
        return number if 0 <= number < JERSEY_GRID_ROWS * JERSEY_NUMBERS_PER_ROW else None

    # Suffixes must be skipped or "Chris Godwin Jr." renders as a cell reading "Jr.",
    # which defeats the point of a grid you scan for instant recognition.
    NAME_SUFFIXES = {"JR", "JR.", "SR", "SR.", "II", "III", "IV", "V"}

    @classmethod
    def _short_name(cls, value: str) -> str:
        parts = [part for part in str(value).split() if part]
        while len(parts) > 1 and parts[-1].upper() in cls.NAME_SUFFIXES:
            parts.pop()
        return parts[-1][:9] if parts else ""

    def _jersey_cell_clicked(self, side: str, number: int) -> None:
        players = self.jersey_cells[side][number]["players"]
        if not players:
            self.status_text.set(f"No #{number} on the {side} roster")
            return
        self.workspace_tabs.select(self.player_page)
        if len(players) > 1:
            # Preseason 90-man rosters routinely double up on a number. List every
            # player wearing it — but only on the team whose grid was clicked, so a
            # home-grid click never surfaces the away team first.
            team = normalize_team_code(safe_get(players[0], "team"))
            self.result_tabs.select(0)
            self.results.delete(0, tk.END)
            self._show_roster_rows(pd.DataFrame(players), f"#{number} on {team}")
            return
        self.show_selected_player(selected_row=players[0], mode="roster")
        self.status_text.set(
            f"#{number} — {safe_get(players[0], 'player_display_name')} "
            f"({normalize_team_code(safe_get(players[0], 'team'))})"
        )

    def _build_live_tab(self) -> None:
        controls = ttk.Frame(self.live_page)
        controls.pack(fill="x")
        ttk.Label(controls, text="Provider:").pack(side="left")
        provider_dropdown = ttk.Combobox(
            controls,
            textvariable=self.live_provider_selection,
            values=("ESPN Experimental", "GSIS Sample Replay"),
            state="readonly",
            width=24,
        )
        provider_dropdown.pack(side="left", padx=(6, 10))
        provider_dropdown.bind("<<ComboboxSelected>>", self._live_provider_changed)
        ttk.Button(controls, text="Find Selected Game", command=self.find_live_game).pack(side="right", padx=3)
        ttk.Button(controls, text="Refresh Now", command=self.refresh_live_game).pack(side="right", padx=3)
        ttk.Button(controls, text="Start Live Updates", command=self.start_live_updates).pack(side="right", padx=3)
        ttk.Button(controls, text="Stop", command=self.stop_live_updates).pack(side="right", padx=3)

        actions = ttk.Frame(self.live_page)
        actions.pack(fill="x", pady=(8, 0))
        ttk.Button(actions, text="Use Live Values in Manual Counters", command=self.apply_live_to_counters).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Freeze Halftime Snapshot", command=self.freeze_halftime_snapshot).pack(side="left", padx=6)
        ttk.Button(actions, text="Copy Halftime Comparison", command=self.copy_halftime_comparison).pack(side="left", padx=6)

        player_lookup = ttk.Labelframe(self.live_page, text="Live Player Search", padding=8)
        player_lookup.pack(fill="x", pady=(8, 0))
        player_search = ttk.Frame(player_lookup)
        player_search.pack(fill="x")
        live_entry = ttk.Entry(
            player_search, textvariable=self.live_player_search_text, font=("Segoe UI", 10)
        )
        live_entry.pack(side="left", fill="x", expand=True)
        live_entry.bind("<Return>", lambda _event: self.search_live_player())
        ttk.Button(player_search, text="Search Game Stats", command=self.search_live_player).pack(
            side="left", padx=(8, 0)
        )
        ttk.Label(
            player_lookup, textvariable=self.live_player_result_text,
            wraplength=1060, justify="left", style="CardBold.TLabel",
        ).pack(anchor="w", pady=(7, 0))

        heading = ttk.Labelframe(self.live_page, text="Connection", padding=10)
        heading.pack(fill="x", pady=(10, 8))
        ttk.Label(heading, textvariable=self.live_game_text, style="Player.TLabel").pack(anchor="w")
        ttk.Label(heading, textvariable=self.live_status_text, style="Yellow.TLabel").pack(anchor="w", pady=(2, 0))
        ttk.Label(heading, textvariable=self.live_updated_text, style="Status.TLabel").pack(anchor="w")

        comparison = ttk.Labelframe(self.live_page, text="Live Team Comparison", padding=8)
        comparison.pack(fill="both", expand=True)
        self.live_tree = ttk.Treeview(
            comparison, columns=("metric", "home", "away", "confidence"), show="headings", height=10
        )
        self.live_tree.heading("metric", text="Statistic")
        self.live_tree.heading("home", text="Home")
        self.live_tree.heading("away", text="Away")
        self.live_tree.heading("confidence", text="Confidence")
        self.live_tree.column("metric", width=190, anchor="w")
        self.live_tree.column("home", width=120, anchor="center")
        self.live_tree.column("away", width=120, anchor="center")
        self.live_tree.column("confidence", width=260, anchor="w")
        self.live_tree.pack(side="left", fill="both", expand=True)
        live_scroll = ttk.Scrollbar(comparison, orient="vertical", command=self.live_tree.yview)
        self.live_tree.configure(yscrollcommand=live_scroll.set)
        live_scroll.pack(side="right", fill="y")

        leaders = ttk.Labelframe(self.live_page, text="Live Player Leaders", padding=8)
        leaders.pack(fill="x", pady=(8, 0))
        ttk.Label(leaders, textvariable=self.live_leaders_text, wraplength=1060, justify="left").pack(anchor="w")

    def _build_situational_tab(self) -> None:
        ttk.Label(
            self.situational_page,
            text="Manual Game Counters — GREEN: manually entered during the game",
            style="Green.TLabel",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(self.situational_page, text="Metric", style="CardBold.TLabel").grid(row=1, column=0, sticky="w")
        ttk.Label(self.situational_page, textvariable=self.home_team_label, style="CardBold.TLabel").grid(row=1, column=1)
        ttk.Label(self.situational_page, textvariable=self.away_team_label, style="CardBold.TLabel").grid(row=1, column=2)

        for row_number, (key, label) in enumerate(COUNTER_FIELDS, start=2):
            ttk.Label(self.situational_page, text=label).grid(row=row_number, column=0, sticky="w", pady=3, padx=(0, 18))
            self._counter_control(self.situational_page, self.counters["home"][key]).grid(row=row_number, column=1, padx=8)
            self._counter_control(self.situational_page, self.counters["away"][key]).grid(row=row_number, column=2, padx=8)

        copy_frame = ttk.Labelframe(self.situational_page, text="Copy-Ready Game Notes", padding=10)
        copy_frame.grid(row=2, column=3, rowspan=9, sticky="nsew", padx=(25, 0))
        for label, kind in (
            ("Copy 3rd Down Note", "third"), ("Copy Red Zone Note", "red_zone"),
            ("Copy Turnover Note", "turnovers"), ("Copy Sack Note", "sacks"),
            ("Copy Team Comparison", "comparison"),
        ):
            ttk.Button(copy_frame, text=label, command=lambda kind=kind: self.copy_counter_note(kind)).pack(fill="x", pady=3)
        ttk.Button(copy_frame, text="Reset Game Counters", command=self.reset_counters).pack(fill="x", pady=(14, 3))

    @staticmethod
    def _counter_control(parent, variable: tk.IntVar) -> ttk.Frame:
        frame = ttk.Frame(parent)
        ttk.Button(frame, text="−", width=3, command=lambda: variable.set(max(0, variable.get() - 1))).pack(side="left")
        ttk.Label(frame, textvariable=variable, width=5, anchor="center", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Button(frame, text="+", width=3, command=lambda: variable.set(variable.get() + 1)).pack(side="left")
        return frame

    def _build_suggestions_tab(self) -> None:
        ttk.Label(self.suggestions_page, text="What just happened?", style="Player.TLabel").pack(anchor="w")
        buttons = ttk.Frame(self.suggestions_page)
        buttons.pack(fill="x", pady=(8, 12))
        for index, event_name in enumerate(GRAPHIC_SUGGESTIONS):
            ttk.Button(
                buttons, text=event_name, command=lambda name=event_name: self.show_graphic_suggestions(name)
            ).grid(row=index // 4, column=index % 4, sticky="ew", padx=4, pady=4)
        for column in range(4):
            buttons.columnconfigure(column, weight=1)

        ttk.Label(self.suggestions_page, textvariable=self.suggestion_title, style="Player.TLabel").pack(anchor="w")
        self.suggestion_text = tk.Text(self.suggestions_page, height=14, font=("Segoe UI", 12), wrap="word")
        self.suggestion_text.pack(fill="both", expand=True, pady=(6, 8))
        self.suggestion_text.configure(state="disabled")
        ttk.Button(self.suggestions_page, text="Copy Suggestions", command=self.copy_suggestions).pack(anchor="e")

    def _create_player_list_tab(self, title: str) -> tk.Listbox:
        frame = ttk.Frame(self.result_tabs, padding=5)
        self.result_tabs.add(frame, text=title)
        player_list = tk.Listbox(
            frame, font=("Segoe UI", 10), activestyle="none",
            selectmode="browse", borderwidth=0, highlightthickness=0,
        )
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=player_list.yview)
        player_list.configure(yscrollcommand=scrollbar.set)
        player_list.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        return player_list

    def _create_stat_tab(self, title: str) -> ttk.Treeview:
        frame = ttk.Frame(self.stat_tabs, padding=8)
        self.stat_tabs.add(frame, text=title)
        tree = ttk.Treeview(frame, columns=("stat", "value"), show="headings")
        tree.heading("stat", text="Statistic")
        tree.heading("value", text="Value")
        tree.column("stat", width=230, anchor="w")
        tree.column("value", width=150, anchor="e")
        tree.pack(fill="both", expand=True)
        return tree

    def _refresh_data_freshness(self) -> None:
        """Show each export's file mtime so staleness is visible at a glance.

        nfl.load_rosters() is a periodic upstream snapshot, not a live feed. During
        roster-cutdown periods it can lag real moves by hours, so the roster age is
        called out explicitly before anyone trusts an "Active" tag on air.
        """
        now = datetime.now()
        parts = []
        for label, path in (
            ("Roster", ROSTER_FILE), ("Career", CAREER_FILE),
            ("Season", SEASON_FILE), ("Preseason", PRESEASON_FILE),
        ):
            if not path.exists():
                parts.append(f"{label}: MISSING")
                continue
            stamp = datetime.fromtimestamp(path.stat().st_mtime)
            text = stamp.strftime("%b %d %I:%M %p")
            if label == "Roster":
                hours = (now - stamp).total_seconds() / 3600
                text += f" ({hours:.0f}h old)" if hours >= 1 else " (under 1h old)"
            parts.append(f"{label}: {text}")
        self.data_freshness_text.set("Data as of —   " + "   |   ".join(parts))

    def load_databases(self) -> None:
        self._refresh_data_freshness()
        missing = [
            path.name for path in (CAREER_FILE, SEASON_FILE, ROSTER_FILE, PRESEASON_FILE)
            if not path.exists()
        ]
        if missing:
            self.status_text.set("Missing database files. Click Update All Data.")
            messagebox.showwarning(APP_TITLE, "Missing database files:\n\n" + "\n".join(missing))
            return
        try:
            self._install_dataframes(
                pd.read_excel(CAREER_FILE), pd.read_excel(SEASON_FILE),
                pd.read_excel(ROSTER_FILE), pd.read_excel(PRESEASON_FILE),
            )
        except Exception as error:
            self.status_text.set("Could not load the databases.")
            messagebox.showerror(APP_TITLE, str(error))
            return
        self.status_text.set(f"Ready — {len(self.career):,} valid career players loaded")
        self.search_entry.focus_set()
        self.show_selected_team()

    def _install_dataframes(self, career, season, roster, preseason) -> None:
        self.career = clean_dataframe(career, require_player_id=True)
        self.season = clean_dataframe(season, require_player_id=True)
        self.roster = clean_dataframe(roster)
        self.preseason = clean_dataframe(preseason)
        if "teams_played_for" in self.career.columns:
            self.career["teams_played_for"] = self.career["teams_played_for"].fillna("").map(
                lambda value: ", ".join(
                    dict.fromkeys(normalize_team_code(code) for code in str(value).split(", ") if code)
                )
            )
        # Both new tabs read self.roster, so rebuild them whenever it is replaced.
        self._refresh_call_sheet()
        self._refresh_jersey_grid()

    def get_game_team_codes(self) -> list[str]:
        codes = [
            TEAM_LABEL_TO_CODE.get(self.home_team_selection.get(), ""),
            TEAM_LABEL_TO_CODE.get(self.away_team_selection.get(), ""),
        ]
        return [code for code in dict.fromkeys(codes) if code]

    def filter_to_game_teams(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.search_game_teams_only.get() or df.empty:
            return df
        teams = self.get_game_team_codes()
        if not teams or "team" not in df.columns:
            return df
        return df[df["team"].isin(teams)]

    def _filter_career_to_game_teams(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.search_game_teams_only.get() or df.empty:
            return df
        game_roster = self.filter_to_game_teams(self.roster)
        ids = set(game_roster.get("player_id", pd.Series(dtype=str)).dropna().astype(str))
        names = set(game_roster["player_display_name"].astype(str).str.casefold())
        id_match = df["player_id"].astype(str).isin(ids) if "player_id" in df.columns else False
        name_match = df["player_display_name"].astype(str).str.casefold().isin(names)
        return df[id_match | name_match]

    def _game_teams_changed(self, _event=None) -> None:
        home = TEAM_LABEL_TO_CODE.get(self.home_team_selection.get(), "HOME")
        away = TEAM_LABEL_TO_CODE.get(self.away_team_selection.get(), "AWAY")
        self.home_team_label.set(home)
        self.away_team_label.set(away)
        self.status_text.set(f"Game setup: {away} at {home}")
        # A new matchup means a different call sheet file and different rosters.
        self._refresh_call_sheet()
        self._refresh_jersey_grid()

    def _workspace_tab_changed(self, _event=None) -> None:
        current = self.workspace_tabs.select()
        if current == str(self.call_sheet_page):
            self._refresh_call_sheet()
        elif current == str(self.jersey_page):
            self._refresh_jersey_grid()

    def _open_game_roster(self, side: str) -> None:
        code = TEAM_LABEL_TO_CODE.get(
            self.home_team_selection.get() if side == "home" else self.away_team_selection.get()
        )
        if code:
            self.team_selection.set(TEAM_CODE_TO_LABEL[code])
            self.workspace_tabs.select(self.player_page)
            self.show_selected_team()

    def search_players(self) -> None:
        query = self.search_text.get().strip()
        self.result_tabs.select(0)
        self.results.delete(0, tk.END)
        if not query or self.career.empty:
            return

        if query.lstrip("#").isdigit() and "jersey_number" in self.roster.columns:
            wanted_number = int(query.lstrip("#"))
            roster_pool = self.filter_to_game_teams(self.roster)
            numbers = pd.to_numeric(roster_pool["jersey_number"], errors="coerce")
            rows = roster_pool[numbers == wanted_number].copy()
            if rows.empty and self.search_game_teams_only.get():
                teams = " and ".join(self.get_game_team_codes())
                self.status_text.set(
                    f"No #{wanted_number} found on {teams}. Turn off Game teams only to search all NFL."
                )
                return
            self._show_roster_rows(rows, f"Who is #{wanted_number}?")
            return

        team_command = parse_team_command(query)
        if team_command:
            self._show_roster_results(*team_command)
            return
        normalized_team = TEAM_ALIASES.get(query.upper(), normalize_team_code(query))
        if normalized_team in set(self.roster["team"]):
            self._show_roster_results(normalized_team, "all")
            return

        pool = self._filter_career_to_game_teams(self.career)
        names = pool["player_display_name"].astype(str)
        query_lower = query.casefold()
        matches = pool[names.str.casefold().str.contains(query_lower, regex=False)]
        if matches.empty:
            close_names = get_close_matches(query, names.tolist(), n=12, cutoff=0.5)
            matches = pool[names.isin(close_names)]
        if matches.empty:
            roster_pool = self.filter_to_game_teams(self.roster)
            roster_names = roster_pool["player_display_name"].astype(str)
            roster_matches = roster_pool[
                roster_names.str.casefold().str.contains(query_lower, regex=False)
            ]
            if roster_matches.empty:
                close_names = get_close_matches(query, roster_names.tolist(), n=12, cutoff=0.5)
                roster_matches = roster_pool[roster_names.isin(close_names)]
            if not roster_matches.empty:
                self._show_roster_rows(roster_matches.head(50).copy(), "Current-roster matches")
                return
        self.matches = matches.head(50).reset_index(drop=True)
        self.result_mode = "player"
        if self.matches.empty:
            scope = "selected game teams" if self.search_game_teams_only.get() else "NFL"
            self.status_text.set(f'No players found for "{query}" in {scope}')
            return
        for _, player in self.matches.iterrows():
            roster_player = self._find_roster_player(player)
            self.results.insert(
                tk.END, self._result_line(roster_player if roster_player is not None else player)
            )
        self.status_text.set(f"{len(self.matches)} matching player(s)")
        self.results.selection_set(0)
        self.results.event_generate("<<ListboxSelect>>")

    def show_selected_team(self, _event=None) -> None:
        team = TEAM_LABEL_TO_CODE.get(self.team_selection.get())
        if not team or self.roster.empty:
            return
        team_rows = self.roster[self.roster["team"] == team].copy()
        units = {"off": OFFENSIVE_POSITIONS, "def": DEFENSIVE_POSITIONS, "kp": SPECIAL_TEAMS_POSITIONS}
        for unit, positions in units.items():
            rows = team_rows[team_rows["position"].astype(str).str.upper().isin(positions)].copy()
            rows["position"] = rows["position"].astype(str).str.upper()
            rows = rows.sort_values(["position", "jersey_number", "player_display_name"], na_position="last").reset_index(drop=True)
            self.team_matches[unit] = rows
            player_list = self.team_lists[unit]
            player_list.delete(0, tk.END)
            for _, player in rows.iterrows():
                player_list.insert(tk.END, self._result_line(player, include_team=False))
        self.result_tabs.select(1)
        if not self.team_matches["off"].empty:
            self.team_lists["off"].selection_set(0)
            self.team_lists["off"].event_generate("<<ListboxSelect>>")
        self.status_text.set(f"{TEAM_NAMES[team]} roster — {sum(map(len, self.team_matches.values()))} listed players")

    def show_selected_team_player(self, _event, unit: str) -> None:
        selection = self.team_lists[unit].curselection()
        rows = self.team_matches[unit]
        if selection and not rows.empty:
            self.show_selected_player(selected_row=rows.iloc[selection[0]], mode="roster")

    def _show_roster_results(self, team: str, unit: str) -> None:
        rows = self.roster[self.roster["team"] == normalize_team_code(team)].copy()
        positions = {"off": OFFENSIVE_POSITIONS, "def": DEFENSIVE_POSITIONS, "kp": SPECIAL_TEAMS_POSITIONS}.get(unit)
        if positions is not None:
            rows = rows[rows["position"].astype(str).str.upper().isin(positions)]
        self._show_roster_rows(rows, f"{team} {unit}")

    def _show_roster_rows(self, rows: pd.DataFrame, label: str) -> None:
        self.matches = rows.sort_values(["team", "jersey_number", "player_display_name"], na_position="last").reset_index(drop=True)
        self.result_mode = "roster"
        for _, player in self.matches.iterrows():
            self.results.insert(tk.END, self._result_line(player))
        self.status_text.set(f"{label} — {len(self.matches)} player(s)")
        if not self.matches.empty:
            self.results.selection_set(0)
            self.results.event_generate("<<ListboxSelect>>")

    def _result_line(self, player: pd.Series, include_team: bool = True) -> str:
        team = normalize_team_code(safe_get(player, "team", safe_get(player, "last_team")))
        jersey = self._format_jersey(safe_get(player, "jersey_number"))
        position = str(safe_get(player, "position"))
        name = str(safe_get(player, "player_display_name"))
        reason = self._roster_reason(player)
        prefix = f"{team} — " if include_team and team else ""
        line = f"{prefix}#{jersey or '?'} — {position} — {name}"
        return f"{line}  [{reason}]" if reason else line

    def show_selected_player(self, _event=None, selected_row: pd.Series | None = None,
                             mode: str | None = None) -> None:
        if selected_row is None:
            selection = self.results.curselection()
            if not selection or self.matches.empty:
                return
            selected_row = self.matches.iloc[selection[0]]
        active_mode = mode or self.result_mode

        roster_row = selected_row if active_mode == "roster" else self._find_roster_player(selected_row)
        career_row = selected_row if active_mode == "player" else self._find_stats_player(self.career, selected_row)
        if career_row is None and active_mode == "player":
            career_row = selected_row
        stats_source = career_row if career_row is not None else selected_row
        preseason_source = roster_row if roster_row is not None else stats_source
        season_row = self._find_stats_player(self.season, stats_source)
        preseason_row = self._find_stats_player(self.preseason, preseason_source, use_name=True)

        position = str(safe_get(roster_row, "position", safe_get(career_row, "position"))).upper()
        stats = STAT_GROUPS[stat_group(position)]
        self._populate_player_card(roster_row, career_row, season_row, preseason_row, position)
        self._fill_stats(self.career_tree, career_row, "career_", stats)
        self._fill_stats(self.season_tree, season_row, f"season_{CURRENT_SEASON}_", stats)
        self._fill_stats(self.preseason_tree, preseason_row, "preseason_", stats)

    def _populate_player_card(self, roster, career, season, preseason, position: str) -> None:
        source = roster if roster is not None else career
        name = str(safe_get(source, "player_display_name", "Unknown Player"))
        team = normalize_team_code(safe_get(roster, "team", safe_get(career, "last_team")))
        jersey = self._format_jersey(safe_get(roster, "jersey_number"))
        self.player_title.set(f"{name.upper()}   #{jersey or '?'}   {position}   {team}")

        reason = self._roster_reason(roster) if roster is not None else ""
        self.player_status.set(f"Status: {reason or 'Active / no exception reported'}")
        bio_parts = []
        college = safe_get(roster, "college")
        experience = ordinal_experience(safe_get(roster, "years_experience"))
        height = self._format_height(safe_get(roster, "height"))
        weight = safe_get(roster, "weight")
        age = safe_get(roster, "age")
        if college: bio_parts.append(f"College: {college}")
        if experience: bio_parts.append(f"Experience: {experience}")
        if height or weight: bio_parts.append(f"{height}{', ' if height and weight else ''}{format_number(weight) + ' lbs' if weight else ''}")
        if age: bio_parts.append(f"Age: {format_number(age)}")
        self.player_bio.set(" | ".join(bio_parts) if bio_parts else "Bio information not available")

        team_history = career_team_codes(career)
        self.player_teams.set(
            f"Career Teams: {', '.join(team_history)}"
            if team_history else "Career Teams: No regular-season history available"
        )

        career_text = self._position_summary(career, "career_", position)
        season_text = self._position_summary(season, f"season_{CURRENT_SEASON}_", position)
        preseason_text = self._position_summary(preseason, "preseason_", position)
        self.career_summary.set(f"Career (Reg Season): {career_text}")
        self.season_summary.set(f"{CURRENT_SEASON} (Reg Season): {season_text}")
        self.preseason_summary.set(f"{PRESEASON_SEASON} Preseason: {preseason_text}")
        self.broadcast_note.set(f"Broadcast Note: {milestone_note(position, career)}")

        confidence = []
        confidence.append("Career/season: YELLOW — imported, not manually checked" if career is not None else "Career: RED — missing")
        confidence.append("Roster: YELLOW — imported" if roster is not None else "Roster: RED — missing")
        self.confidence_text.set(" | ".join(confidence))
        self.current_player = {
            "name": name, "team": team, "position": position, "jersey": jersey,
            "career": career, "season": season, "preseason": preseason,
            "career_text": career_text, "season_text": season_text,
        }

    def _position_summary(self, row: pd.Series | None, prefix: str, position: str) -> str:
        if row is None:
            return "No stats available"
        def value(column): return format_number(safe_get(row, f"{prefix}{column}", 0))
        if position == "QB":
            return f"{value('games')} G | {value('completions')}/{value('attempts')} CMP/ATT | {value('passing_yards')} PASS YDS | {value('passing_tds')} TD | {value('passing_interceptions')} INT | {value('rushing_yards')} RUSH YDS"
        if position in {"RB", "FB"}:
            return f"{value('games')} G | {value('carries')} CAR | {value('rushing_yards')} RUSH YDS | {value('rushing_tds')} TD | {value('receptions')} REC | {value('receiving_yards')} REC YDS"
        if position in {"WR", "TE"}:
            return f"{value('games')} G | {value('receptions')} REC | {value('receiving_yards')} YDS | {value('receiving_tds')} TD | {value('receiving_average')} AVG"
        if position in {"K", "PK"}:
            made, attempts = float(safe_get(row, f"{prefix}fg_made", 0)), float(safe_get(row, f"{prefix}fg_att", 0))
            pct = made / attempts * 100 if attempts else 0
            return f"{value('games')} G | {format_number(made)}/{format_number(attempts)} FG ({pct:.1f}%) | LONG {value('fg_long')} | {value('pat_made')}/{value('pat_att')} XP"
        if position in {"P", "PT"}:
            fields = [("punts", "PUNTS"), ("punt_yards", "YDS"), ("punt_average", "AVG"), ("punt_long", "LONG"), ("punts_inside_20", "IN 20")]
            available = [f"{value(column)} {label}" for column, label in fields if f"{prefix}{column}" in row.index]
            return " | ".join(available) if available else "No punting stats available"
        tackles = float(safe_get(row, f"{prefix}def_tackles_solo", 0)) + float(safe_get(row, f"{prefix}def_tackle_assists", 0))
        return f"{value('games')} G | {format_number(tackles)} TKL | {value('def_sacks')} SACK | {value('def_interceptions')} INT | {value('def_fumbles_forced')} FF | {value('def_pass_defended')} PD"

    def copy_player_gfx(self, kind: str) -> None:
        if not self.current_player:
            self.status_text.set("Select a player before copying GFX text.")
            return
        player = self.current_player
        name, position, team, jersey = player["name"], player["position"], player["team"], player["jersey"]
        career = player["career"]
        summary = player["career_text"]
        if kind == "lower":
            text = f"{name.upper()}\n{summary}"
            label = "Lower Third"
        elif kind == "fullscreen":
            text = f"{name.upper()} — CAREER (REG SEASON) {position}\n{summary}"
            label = "Fullscreen Stat"
        elif kind == "announcer":
            text = f"{name} enters with {summary.lower().replace(' | ', ', ')}."
            label = "Announcer Note"
        else:
            text = f"#{jersey or '?'} — {name.upper()} — {position} — {TEAM_NAMES.get(team, team).upper()}"
            label = "Jersey ID"
        self._copy_to_clipboard(text, f"Copied {label} to clipboard")

    def find_live_game(self) -> None:
        teams = self.get_game_team_codes()
        if len(teams) != 2:
            messagebox.showwarning(APP_TITLE, "Select two different game teams first.")
            return
        if self.live_request_in_progress:
            return
        home, away = teams
        self.live_request_in_progress = True
        self.live_status_text.set("Feed: searching ESPN schedule...")

        def worker():
            try:
                game = self.live_provider.discover_game(home, away)
            except Exception as error:
                self.after(0, lambda error=error: self._finish_live_find(error=error))
                return
            self.after(0, lambda: self._finish_live_find(game=game))

        threading.Thread(target=worker, daemon=True).start()

    def _live_provider_changed(self, _event=None) -> None:
        self.stop_live_updates()
        self.live_event_id = None
        self.live_snapshot = None
        self.halftime_snapshot = None
        if self.live_provider_selection.get() == "GSIS Sample Replay":
            source_folder = APP_ROOT / "Tests" / "fixtures" / "gsis_2014_nyg_nyj"
            packaged_folder = APP_ROOT / "gsis_samples" / "gsis_2014_nyg_nyj"
            folder = source_folder if source_folder.exists() else packaged_folder
            self.live_provider = GSISReplayProvider(folder)
            self.live_game_text.set("GSIS sample replay ready")
            self.live_status_text.set("Feed: select Find Selected Game to load the archive")
        else:
            self.live_provider = ESPNLiveProvider()
            self.live_game_text.set("No live game connected")
            self.live_status_text.set("Feed: disconnected")
        self.live_tree.delete(*self.live_tree.get_children())
        self.live_leaders_text.set("Player leaders will appear after connecting.")

    def _finish_live_find(self, game: dict | None = None, error: Exception | None = None) -> None:
        self.live_request_in_progress = False
        if error is not None:
            self.live_status_text.set(f"Feed: game search failed — {error}")
            return
        self.live_event_id = game["event_id"]
        self.live_game_text.set(game["name"])
        self.live_status_text.set(f"Feed: game found — {game['status']}")
        self.live_updated_text.set(f"Scheduled: {game['date']}")
        self.refresh_live_game()

    def start_live_updates(self) -> None:
        self.live_polling = True
        if self.live_event_id:
            self.refresh_live_game()
        else:
            self.find_live_game()
        seconds = getattr(self.live_provider, "poll_interval_ms", 15000) / 1000
        self.status_text.set(f"Live updates enabled — polling every {seconds:g} seconds")

    def stop_live_updates(self) -> None:
        self.live_polling = False
        if self.live_after_id is not None:
            self.after_cancel(self.live_after_id)
            self.live_after_id = None
        self.live_status_text.set("Feed: automatic updates stopped")

    def _on_close(self) -> None:
        self.live_polling = False
        if self.live_after_id is not None:
            self.after_cancel(self.live_after_id)
        self.destroy()

    def refresh_live_game(self) -> None:
        if not self.live_event_id:
            self.find_live_game()
            return
        if self.live_request_in_progress:
            return
        self.live_request_in_progress = True
        self.live_status_text.set("Feed: refreshing...")
        event_id = self.live_event_id

        def worker():
            try:
                snapshot = self.live_provider.fetch_game(event_id)
            except Exception as error:
                self.after(0, lambda error=error: self._finish_live_refresh(error=error))
                return
            self.after(0, lambda: self._finish_live_refresh(snapshot=snapshot))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_live_refresh(self, snapshot: LiveGameSnapshot | None = None,
                             error: Exception | None = None) -> None:
        self.live_request_in_progress = False
        if error is not None:
            self.live_status_text.set(f"Feed: refresh failed — {error}")
        else:
            self.live_snapshot = snapshot
            self._render_live_snapshot(snapshot)
        if self.live_polling:
            self.live_after_id = self.after(
                getattr(self.live_provider, "poll_interval_ms", 15000), self.refresh_live_game
            )

    def _render_live_snapshot(self, snapshot: LiveGameSnapshot) -> None:
        if snapshot.home_team in TEAM_CODE_TO_LABEL and snapshot.away_team in TEAM_CODE_TO_LABEL:
            self.home_team_selection.set(TEAM_CODE_TO_LABEL[snapshot.home_team])
            self.away_team_selection.set(TEAM_CODE_TO_LABEL[snapshot.away_team])
            self._game_teams_changed()
        home = snapshot.teams[snapshot.home_team]
        away = snapshot.teams[snapshot.away_team]
        self.live_game_text.set(
            f"{snapshot.away_team} {away.score}  at  {snapshot.home_team} {home.score}"
        )
        self.live_status_text.set(
            f"Feed: connected — {snapshot.status} | Period {snapshot.period} | {snapshot.clock}"
        )
        self.live_updated_text.set(
            f"Last update: {datetime.now().strftime('%I:%M:%S %p')} — {self.live_provider.confidence_label}"
        )
        self.live_tree.heading("home", text=snapshot.home_team)
        self.live_tree.heading("away", text=snapshot.away_team)
        self.live_tree.delete(*self.live_tree.get_children())
        rows = [
            ("Score", home.score, away.score),
            ("Total Yards", home.total_yards, away.total_yards),
            ("Passing Yards", home.passing_yards, away.passing_yards),
            ("Rushing Yards", home.rushing_yards, away.rushing_yards),
            ("First Downs", home.first_downs, away.first_downs),
            ("Third Down", f"{home.third_down_made}/{home.third_down_attempts}", f"{away.third_down_made}/{away.third_down_attempts}"),
            ("Red Zone", f"{home.red_zone_scores}/{home.red_zone_trips}", f"{away.red_zone_scores}/{away.red_zone_trips}"),
            ("Turnovers", home.turnovers, away.turnovers),
            ("Sacks", home.sacks, away.sacks),
            ("Penalties-Yards", f"{home.penalties}-{home.penalty_yards}", f"{away.penalties}-{away.penalty_yards}"),
            ("Possession", home.possession_time, away.possession_time),
        ]
        for metric, home_value, away_value in rows:
            self.live_tree.insert(
                "", "end",
                values=(metric, home_value, away_value, self.live_provider.confidence_label),
            )
        leader_lines = []
        for team in (away, home):
            leader_lines.append(f"{team.team}: " + "  |  ".join(team.leaders.values()) if team.leaders else f"{team.team}: No player leaders available")
        self.live_leaders_text.set("\n".join(leader_lines))
        if self.live_player_search_text.get().strip():
            self.search_live_player()

    def search_live_player(self) -> None:
        query = self.live_player_search_text.get().strip()
        if self.live_snapshot is None:
            self.live_player_result_text.set("Connect to or replay a game before searching player stats.")
            return
        if not query:
            self.live_player_result_text.set("Type a player name to search the current game.")
            return
        players = self.live_snapshot.players
        query_lower = query.casefold().lstrip("#")
        if query.startswith("#") and query_lower.isdigit():
            matches = [player for player in players if player.jersey.lstrip("0") == query_lower.lstrip("0")]
        else:
            matches = [player for player in players if query_lower in player.name.casefold()]
            if not matches:
                close_names = get_close_matches(query, [player.name for player in players], n=5, cutoff=0.55)
                matches = [player for player in players if player.name in close_names]
        if not matches:
            self.live_player_result_text.set(
                f'No game statistics found for "{query}" in the current snapshot.'
            )
            return
        output = []
        for player in matches[:8]:
            heading = f"{player.team} — #{player.jersey or '?'} — {player.position or '?'} — {player.name}"
            lines = " | ".join(
                f"{category}: {line}" for category, line in player.stat_lines.items()
            )
            output.append(f"{heading}\n{lines or 'No recorded game statistics yet'}")
        self.live_player_result_text.set("\n\n".join(output))

    def apply_live_to_counters(self) -> None:
        if self.live_snapshot is None:
            self.status_text.set("Connect to a live game first.")
            return
        snapshot = self.live_snapshot
        mappings = {
            "home": snapshot.teams[snapshot.home_team],
            "away": snapshot.teams[snapshot.away_team],
        }
        for side, live in mappings.items():
            values = {
                "third_down_made": live.third_down_made,
                "third_down_attempts": live.third_down_attempts,
                "red_zone_scores": live.red_zone_scores,
                "red_zone_trips": live.red_zone_trips,
                "sacks": live.sacks,
                "turnovers": live.turnovers,
                "penalties": live.penalties,
                "penalty_yards": live.penalty_yards,
            }
            for key, value in values.items():
                self.counters[side][key].set(value)
        self.status_text.set("Live values copied into manual counters — review before air")

    def freeze_halftime_snapshot(self) -> None:
        if self.live_snapshot is None:
            self.status_text.set("Connect to a live game before freezing halftime stats.")
            return
        self.halftime_snapshot = self.live_snapshot
        self.status_text.set(
            f"Halftime snapshot frozen at {datetime.now().strftime('%I:%M:%S %p')}"
        )

    def copy_halftime_comparison(self) -> None:
        if self.halftime_snapshot is None:
            self.status_text.set("Freeze a halftime snapshot first.")
            return
        snapshot = self.halftime_snapshot
        home, away = snapshot.teams[snapshot.home_team], snapshot.teams[snapshot.away_team]
        text = (
            f"HALFTIME — {away.team} {away.score}, {home.team} {home.score}\n"
            f"TOTAL YARDS: {away.team} {away.total_yards} | {home.team} {home.total_yards}\n"
            f"PASS YARDS: {away.team} {away.passing_yards} | {home.team} {home.passing_yards}\n"
            f"RUSH YARDS: {away.team} {away.rushing_yards} | {home.team} {home.rushing_yards}\n"
            f"FIRST DOWNS: {away.team} {away.first_downs} | {home.team} {home.first_downs}\n"
            f"3RD DOWN: {away.team} {away.third_down_made}/{away.third_down_attempts} | {home.team} {home.third_down_made}/{home.third_down_attempts}\n"
            f"TURNOVERS: {away.team} {away.turnovers} | {home.team} {home.turnovers}\n"
            f"SACKS: {away.team} {away.sacks} | {home.team} {home.sacks}\n"
            f"PENALTIES: {away.team} {away.penalties}-{away.penalty_yards} | {home.team} {home.penalties}-{home.penalty_yards}\n"
            f"SOURCE: {self.live_provider.confidence_label.upper()}"
        )
        self._copy_to_clipboard(text, "Copied halftime comparison to clipboard")

    def copy_counter_note(self, kind: str) -> None:
        teams = self.get_game_team_codes()
        if len(teams) != 2:
            messagebox.showwarning(APP_TITLE, "Select two different game teams first.")
            return
        home, away = teams
        h, a = self.counters["home"], self.counters["away"]
        if kind == "third":
            text = f"{home} IS {h['third_down_made'].get()}/{h['third_down_attempts'].get()} ON THIRD DOWN TONIGHT\n{away} IS {a['third_down_made'].get()}/{a['third_down_attempts'].get()} ON THIRD DOWN TONIGHT"
        elif kind == "red_zone":
            text = f"{home} RED ZONE: {h['red_zone_scores'].get()}/{h['red_zone_trips'].get()}\n{away} RED ZONE: {a['red_zone_scores'].get()}/{a['red_zone_trips'].get()}"
        elif kind == "turnovers":
            text = f"{home} TURNOVERS: {h['turnovers'].get()} | {away} TURNOVERS: {a['turnovers'].get()}"
        elif kind == "sacks":
            text = f"{home} SACKS: {h['sacks'].get()} | {away} SACKS: {a['sacks'].get()}"
        else:
            text = (
                f"{away} AT {home} — TEAM COMPARISON\n"
                f"3RD DOWN: {home} {h['third_down_made'].get()}/{h['third_down_attempts'].get()} | {away} {a['third_down_made'].get()}/{a['third_down_attempts'].get()}\n"
                f"RED ZONE: {home} {h['red_zone_scores'].get()}/{h['red_zone_trips'].get()} | {away} {a['red_zone_scores'].get()}/{a['red_zone_trips'].get()}\n"
                f"SACKS: {home} {h['sacks'].get()} | {away} {a['sacks'].get()}\n"
                f"TURNOVERS: {home} {h['turnovers'].get()} | {away} {a['turnovers'].get()}\n"
                f"PENALTIES: {home} {h['penalties'].get()}-{h['penalty_yards'].get()} | {away} {a['penalties'].get()}-{a['penalty_yards'].get()}"
            )
        self._copy_to_clipboard(text, "Copied game note to clipboard")

    def reset_counters(self) -> None:
        if messagebox.askyesno(APP_TITLE, "Reset every manual game counter to zero?"):
            for side in self.counters.values():
                for variable in side.values():
                    variable.set(0)
            self.status_text.set("Game counters reset")

    def show_graphic_suggestions(self, event_name: str) -> None:
        suggestions = GRAPHIC_SUGGESTIONS[event_name]
        self.suggestion_title.set(f"{event_name} — Suggested graphics")
        text = "\n".join(f"{index}. {suggestion}" for index, suggestion in enumerate(suggestions, 1))
        self.suggestion_text.configure(state="normal")
        self.suggestion_text.delete("1.0", tk.END)
        self.suggestion_text.insert("1.0", text)
        self.suggestion_text.configure(state="disabled")

    def copy_suggestions(self) -> None:
        text = self.suggestion_text.get("1.0", tk.END).strip()
        if text:
            self._copy_to_clipboard(text, "Copied graphic suggestions")

    def build_font_sheet(self) -> None:
        """Write the GFX font sheet for the selected matchup and open it.

        The sheet's own engine computes every call code from the roster data, so
        this only supplies the rosters and the confirmed overrides.
        """
        if self.roster.empty:
            messagebox.showwarning(APP_TITLE, "Load or update the roster data first.")
            return
        sides = self._game_sides()
        home, away = sides["home"], sides["away"]
        if not home or not away:
            messagebox.showwarning(APP_TITLE, "Select two game teams first.")
            return
        stamp = (
            datetime.fromtimestamp(ROSTER_FILE.stat().st_mtime)
            if ROSTER_FILE.exists() else None
        )
        try:
            path, report = write_font_sheet(self.roster, home, away, TEAM_NAMES, stamp)
        except Exception as error:
            self.status_text.set(f"Font sheet failed — {error}")
            messagebox.showerror(APP_TITLE, f"Could not build the font sheet:\n\n{error}")
            return

        notes = [
            f"{report['our_team']} {report['our_count']} players, "
            f"{report['opponent']} {report['opp_count']} players.",
            f"{len(report['overrides_applied'])} confirmed override(s) applied.",
        ]
        # Anything that could not be applied is said out loud rather than dropped:
        # a missing override silently becomes an amber guess on air.
        if report["overrides_unmatched"]:
            notes.append(
                "COULD NOT MATCH these confirmed overrides to anyone on the roster — "
                "check the spelling in config/tb_overrides.json:\n  "
                + "\n  ".join(report["overrides_unmatched"])
            )
        if report["skipped_no_jersey"]:
            notes.append(
                f"{len(report['skipped_no_jersey'])} player(s) had no jersey number and "
                "were left off the sheet:\n  " + "\n  ".join(report["skipped_no_jersey"])
            )
        notes.append(
            "Storylines, players-to-have-ready and the opponent coach block are NOT "
            "rebuilt — type those in. Confirm every amber code with the op."
        )
        self.status_text.set(f"Font sheet built — {path.name}")
        messagebox.showinfo(APP_TITLE, f"Font sheet written:\n\n{path}\n\n" + "\n\n".join(notes))
        try:
            os.startfile(path)
        except OSError as error:
            self.status_text.set(f"Font sheet written, but it could not be opened — {error}")

    def generate_pregame_packet(self) -> None:
        teams = self.get_game_team_codes()
        if len(teams) != 2 or teams[0] == teams[1]:
            messagebox.showwarning(APP_TITLE, "Select two different game teams first.")
            return
        home, away = teams
        lines = [
            f"{TEAM_NAMES[away].upper()} vs {TEAM_NAMES[home].upper()} — PRESEASON GAME PACKET",
            "", "GAME TEAMS", f"Home: {TEAM_NAMES[home]}", f"Away: {TEAM_NAMES[away]}", "",
        ]
        for team in (home, away):
            roster = self.roster[self.roster["team"] == team]
            lines.extend([f"{team} PLAYERS TO WATCH", "Offense:"])
            lines.extend(self._packet_player_lines(roster, OFFENSIVE_POSITIONS, 7))
            lines.append("Defense:")
            lines.extend(self._packet_player_lines(roster, DEFENSIVE_POSITIONS, 7))
            lines.extend(["", f"{team} QUICK JERSEY LOOKUP"])
            ordered = roster.sort_values("jersey_number", na_position="last")
            for _, player in ordered.iterrows():
                lines.append(f"#{self._format_jersey(safe_get(player, 'jersey_number')) or '?'} — {safe_get(player, 'player_display_name')} — {safe_get(player, 'position')}")
            lines.append("")

        lines.extend(["KEY CAREER PLAYERS (REG SEASON TOTALS — PLAYOFFS NOT INCLUDED)"])
        game_roster = self.roster[self.roster["team"].isin((home, away))]
        key_players = []
        for _, player in game_roster.iterrows():
            career = self._find_stats_player(self.career, player)
            if career is not None:
                key_players.append((float(safe_get(career, "career_games", 0)), player, career))
        for _games, player, career in sorted(key_players, reverse=True, key=lambda item: item[0])[:12]:
            position = str(safe_get(player, "position"))
            lines.append(f"- {safe_get(player, 'player_display_name')} ({safe_get(player, 'team')} {position}) — {self._position_summary(career, 'career_', position)}")

        lines.extend(["", "PRESEASON STAT LEADERS"])
        preseason = self.preseason[self.preseason.get("team", pd.Series(dtype=str)).isin((home, away))]
        for column, label in (("preseason_passing_yards", "Passing"), ("preseason_rushing_yards", "Rushing"), ("preseason_receiving_yards", "Receiving")):
            if column in preseason.columns and not preseason.empty:
                leader = preseason.sort_values(column, ascending=False).iloc[0]
                lines.append(f"- {label}: {safe_get(leader, 'player_display_name')} — {format_number(safe_get(leader, column))} yards")
        assigned = sum(1 for side in ("home", "away")
                       for row in self.call_sheet_rows[side] if row["call_up"])
        if assigned:
            lines.append("")
            lines.extend(self.call_sheet_lines("home"))
            lines.append("")
            lines.extend(self.call_sheet_lines("away"))

        lines.extend([
            "", "BROADCAST NOTES",
            "- Use # search to identify players quickly.",
            "- Use player-card copy buttons for lower thirds and stat graphics.",
            "- Imported stats are YELLOW until manually verified for air.",
            "- Manual game counters are GREEN because they are entered during the game.",
        ])
        path = EXPORT_DIR / "game_packets" / packet_filename(home, away)
        write_game_packet(path, lines)
        self.status_text.set(f"Pre-game packet saved: {path}")
        messagebox.showinfo(APP_TITLE, f"Pre-game packet created:\n\n{path}")

    @staticmethod
    def _packet_player_lines(roster: pd.DataFrame, positions: set[str], limit: int) -> list[str]:
        rows = roster[roster["position"].astype(str).str.upper().isin(positions)].head(limit)
        return [f"- #{NFLStatsApp._format_jersey(safe_get(row, 'jersey_number')) or '?'} {safe_get(row, 'player_display_name')} — {safe_get(row, 'position')}" for _, row in rows.iterrows()]

    def update_all_data(self) -> None:
        self.update_button.configure(state="disabled")
        self.status_text.set("Updating career, season, preseason, and roster data...")
        threading.Thread(target=self._update_all_data_worker, daemon=True).start()

    def _update_all_data_worker(self) -> None:
        # Export each dataset as soon as it is built. The Sleeper preseason endpoint is
        # unofficial and can fail or change shape; a failure there must still leave the
        # career/season/roster refresh in place instead of aborting the whole update.
        try:
            stats = load_stats()
            weekly_stats = load_team_history_stats()
            career_team_history = build_team_history(weekly_stats)
            current_team_history = build_team_history(
                weekly_stats.filter(weekly_stats["season"] == CURRENT_SEASON)
            )
            export_database(
                build_totals(stats, "career_", career_team_history),
                CAREER_FILE.name,
            )
            export_database(
                build_totals(
                    stats.filter(stats["season"] == CURRENT_SEASON),
                    f"season_{CURRENT_SEASON}_",
                    current_team_history,
                ),
                SEASON_FILE.name,
            )
            export_database(build_active_roster(), ROSTER_FILE.name)
        except Exception as error:
            self.after(0, lambda error=error: self._finish_data_update(error=error))
            return

        preseason_error: Exception | None = None
        try:
            export_database(build_preseason_totals(), PRESEASON_FILE.name)
        except Exception as error:
            preseason_error = error

        try:
            frames = tuple(
                pd.read_excel(path) if path.exists() else pd.DataFrame()
                for path in (CAREER_FILE, SEASON_FILE, ROSTER_FILE, PRESEASON_FILE)
            )
        except Exception as error:
            self.after(0, lambda error=error: self._finish_data_update(error=error))
            return
        self.after(
            0,
            lambda: self._finish_data_update(frames=frames, partial_error=preseason_error),
        )

    def _finish_data_update(self, frames=None, error: Exception | None = None,
                            partial_error: Exception | None = None) -> None:
        self.update_button.configure(state="normal")
        if error is not None:
            self.status_text.set("Data update failed.")
            messagebox.showerror(APP_TITLE, f"Could not update data:\n\n{error}")
            return
        self._install_dataframes(*frames)
        self.show_selected_team()
        self._refresh_data_freshness()
        if partial_error is not None:
            self.status_text.set(
                f"Career, season, and roster updated — PRESEASON REFRESH FAILED "
                f"({type(partial_error).__name__}: {partial_error})"
            )
            messagebox.showwarning(
                APP_TITLE,
                "Career, season, and roster data updated successfully.\n\n"
                f"The preseason file could NOT be refreshed:\n\n"
                f"{type(partial_error).__name__}: {partial_error}\n\n"
                f"{PRESEASON_FILE.name} still holds whatever it had before this update, "
                "so preseason numbers may be missing or out of date. Everything else is "
                "current — check the 'Data as of' line above.",
            )
            return
        self.status_text.set(f"All data updated — {len(self.career):,} valid players, {len(self.roster):,} roster entries")
        messagebox.showinfo(APP_TITLE, "Career, season, preseason, and roster data updated successfully.")

    def _find_roster_player(self, player: pd.Series | None) -> pd.Series | None:
        if player is None or self.roster.empty:
            return None
        if "player_id" in self.roster.columns and safe_get(player, "player_id"):
            match = self.roster[self.roster["player_id"].astype(str) == str(safe_get(player, "player_id"))]
            if not match.empty:
                return self._prefer_game_team(match).iloc[0]
        name = str(safe_get(player, "player_display_name")).casefold()
        match = self.roster[self.roster["player_display_name"].astype(str).str.casefold() == name]
        return None if match.empty else self._prefer_game_team(match).iloc[0]

    def _find_stats_player(self, dataframe: pd.DataFrame, player: pd.Series | None,
                           use_name: bool = False) -> pd.Series | None:
        if player is None or dataframe.empty:
            return None
        if not use_name and "player_id" in dataframe.columns and safe_get(player, "player_id"):
            match = dataframe[dataframe["player_id"].astype(str) == str(safe_get(player, "player_id"))]
            if not match.empty:
                return match.iloc[0]
        name = str(safe_get(player, "player_display_name")).casefold()
        match = dataframe[dataframe["player_display_name"].astype(str).str.casefold() == name]
        return None if match.empty else match.iloc[0]

    def _prefer_game_team(self, rows: pd.DataFrame) -> pd.DataFrame:
        game = rows[rows["team"].isin(self.get_game_team_codes())] if "team" in rows.columns else rows.iloc[0:0]
        return game if not game.empty else rows

    @staticmethod
    def _fill_stats(tree: ttk.Treeview, row: pd.Series | None, prefix: str,
                    stats: list[tuple[str, str]]) -> None:
        tree.delete(*tree.get_children())
        if row is None:
            tree.insert("", "end", values=("No stat found", "RED — missing"))
            return
        for label, column in stats:
            if column == "seasons_played" and prefix != "career_":
                continue
            full_column = f"{prefix}{column}"
            value = format_value(row[full_column], column) if full_column in row.index else "N/A"
            tree.insert("", "end", values=(label, value))

    def _copy_to_clipboard(self, text: str, confirmation: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        self.status_text.set(confirmation)

    @staticmethod
    def _format_jersey(value) -> str:
        if value is None or pd.isna(value) or str(value).strip() == "":
            return ""
        try:
            return str(int(float(value)))
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _format_height(value) -> str:
        if value is None or pd.isna(value) or str(value).strip() == "":
            return ""
        try:
            inches = int(float(value))
            return f"{inches // 12}-{inches % 12}"
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _roster_reason(player: pd.Series | None) -> str:
        if player is None:
            return ""
        def clean(value) -> str:
            return "" if value is None or pd.isna(value) else str(value).strip()
        status_code = clean(safe_get(player, "roster_status")).upper()
        values = []
        if status_code:
            values.append(ROSTER_STATUS_LABELS.get(status_code, status_code))
        for column in ("injury_status", "injury_body_part", "injury_notes"):
            value = clean(safe_get(player, column))
            if value and value.lower() not in {"none", "healthy", "active"}:
                values.append(value)
        return " — ".join(dict.fromkeys(values))


def main() -> None:
    NFLStatsApp().mainloop()


if __name__ == "__main__":
    main()
