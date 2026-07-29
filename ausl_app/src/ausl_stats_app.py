"""Tkinter game-day lookup tool for Athletes Unlimited Softball League."""

from __future__ import annotations

import json
import threading
import tkinter as tk
from datetime import date, datetime, timezone
from tkinter import messagebox, ttk

import pandas as pd

from ausl_data import SEASONS, TEAM_CODES, TEAM_NAMES, export_dir, fetch_live_game, load_database, update_all_data


CURRENT_YEAR = max(SEASONS)
TEAM_OPTIONS = sorted(TEAM_NAMES.values())
TEAM_TO_CODE = {full: TEAM_CODES[short] for short, full in TEAM_NAMES.items()}
VERIFY_NOTE = "Verify lineups, player availability, and milestones before air."
CAREER_LABEL = f"AUSL Career ({min(SEASONS)}-{max(SEASONS)} regular-season data)"


def value(row, key, default="—"):
    if row is None or key not in row or pd.isna(row[key]) or row[key] == "":
        return default
    return row[key]


def whole(row, key):
    item = value(row, key, 0)
    try:
        return str(int(float(item)))
    except (TypeError, ValueError):
        return str(item)


def decimal(row, key, places=3):
    item = value(row, key, None)
    if item is None:
        return "—"
    try:
        return f"{float(item):.{places}f}".lstrip("0")
    except (TypeError, ValueError):
        return str(item)


class AUSLStatsApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("AUSL Broadcast Stats Lookup")
        self.root.geometry("1440x880")
        self.root.minsize(1120, 720)
        self.db = {}
        self.selected_player_id = None
        self.search_rows = []
        self.live_game = None
        self.live_box = None
        self.live_player_copy_text = ""
        self.current_broadcast_note = ""
        self.data_freshness_text = f"Data updated: unknown - {VERIFY_NOTE}"
        self.producer_prep_copy_text = ""
        self._style()
        self._build()
        self._load_initial()

    def _style(self):
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Player.TLabel", font=("Segoe UI", 22, "bold"))
        style.configure("Sub.TLabel", font=("Segoe UI", 10), foreground="#555555")
        style.configure("Accent.TButton", font=("Segoe UI", 10, "bold"))

    def _build(self):
        top = ttk.Frame(self.root, padding=10)
        top.pack(fill="x")
        ttk.Label(top, text="AUSL Broadcast Stats", style="Header.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 18))
        ttk.Button(top, text="Update All Data", style="Accent.TButton", command=self.update_data).grid(row=0, column=1, padx=4)
        self.status_var = tk.StringVar(value="Loading local database...")
        ttk.Label(top, textvariable=self.status_var, style="Sub.TLabel").grid(row=0, column=2, sticky="w", padx=10)
        self.data_freshness_var = tk.StringVar(value=self.data_freshness_text)
        ttk.Label(top, textvariable=self.data_freshness_var, style="Sub.TLabel").grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 0))
        top.columnconfigure(2, weight=1)

        game = ttk.LabelFrame(self.root, text="Game Setup", padding=8)
        game.pack(fill="x", padx=10, pady=(0, 8))
        self.away_var = tk.StringVar(value=TEAM_OPTIONS[0])
        self.home_var = tk.StringVar(value=TEAM_OPTIONS[1])
        ttk.Label(game, text="Away Team").grid(row=0, column=0, padx=4)
        away_box = ttk.Combobox(game, values=TEAM_OPTIONS, textvariable=self.away_var, state="readonly", width=23)
        away_box.grid(row=0, column=1, padx=4)
        away_box.bind("<<ComboboxSelected>>", lambda _event: self.render_producer_prep())
        ttk.Label(game, text="Home Team").grid(row=0, column=2, padx=(16, 4))
        home_box = ttk.Combobox(game, values=TEAM_OPTIONS, textvariable=self.home_var, state="readonly", width=23)
        home_box.grid(row=0, column=3, padx=4)
        home_box.bind("<<ComboboxSelected>>", lambda _event: self.render_producer_prep())
        self.scope_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(game, text="Search game teams only", variable=self.scope_var, command=self.search).grid(row=0, column=4, padx=18)
        ttk.Button(game, text="Show Away Roster", command=lambda: self.show_team(self.away_var.get())).grid(row=0, column=5, padx=4)
        ttk.Button(game, text="Show Home Roster", command=lambda: self.show_team(self.home_var.get())).grid(row=0, column=6, padx=4)
        ttk.Button(game, text="Generate Producer Packet", style="Accent.TButton", command=self.generate_pregame_report).grid(row=0, column=7, padx=(16, 4))

        self.main_tabs = ttk.Notebook(self.root)
        self.main_tabs.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        lookup = ttk.Frame(self.main_tabs, padding=8)
        team_totals = ttk.Frame(self.main_tabs, padding=8)
        producer = ttk.Frame(self.main_tabs, padding=8)
        manual = ttk.Frame(self.main_tabs, padding=8)
        live = ttk.Frame(self.main_tabs, padding=8)
        self.main_tabs.add(lookup, text="Player Lookup")
        self.main_tabs.add(team_totals, text="Team Totals")
        self.main_tabs.add(producer, text="Producer Prep")
        self.main_tabs.add(manual, text="Manual Notes")
        self.main_tabs.add(live, text="Live Game")
        self._build_lookup(lookup)
        self._build_team_totals(team_totals)
        self._build_producer_prep(producer)
        self._build_manual_notes(manual)
        self._build_live(live)

    def _build_lookup(self, parent):
        parent.columnconfigure(0, weight=2)
        parent.columnconfigure(1, weight=3)
        parent.rowconfigure(1, weight=1)
        search = ttk.Frame(parent)
        search.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(search, text="Player, team, position, or #number:").pack(side="left")
        self.search_var = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.search_var, width=36)
        entry.pack(side="left", padx=8)
        entry.bind("<KeyRelease>", lambda _e: self.search())
        entry.bind("<Return>", lambda _e: self.search())
        ttk.Button(search, text="Search", command=self.search).pack(side="left")
        ttk.Button(search, text="All Players", command=self.show_all).pack(side="left", padx=5)

        left = ttk.LabelFrame(parent, text="Players", padding=6)
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 6))
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.results = tk.Listbox(left, font=("Consolas", 11), activestyle="none")
        self.results.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(left, orient="vertical", command=self.results.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.results.configure(yscrollcommand=scroll.set)
        self.results.bind("<<ListboxSelect>>", self.select_result)

        right = ttk.LabelFrame(parent, text="Broadcast Player Card", padding=10)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)
        self.player_title = tk.StringVar(value="Select a player")
        self.player_meta = tk.StringVar(value="")
        ttk.Label(right, textvariable=self.player_title, style="Player.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(right, textvariable=self.player_meta, style="Sub.TLabel", wraplength=720).grid(row=1, column=0, sticky="w", pady=(2, 8))
        buttons = ttk.Frame(right)
        buttons.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        for label, kind in [("Copy Lower Third", "lower"), ("Copy Fullscreen Stat", "full"), ("Copy Announcer Note", "note"), ("Copy Jersey ID", "jersey")]:
            ttk.Button(buttons, text=label, command=lambda k=kind: self.copy_gfx(k)).pack(side="left", padx=(0, 5))
        self.copy_status = tk.StringVar()
        ttk.Label(right, textvariable=self.copy_status, style="Sub.TLabel").grid(row=3, column=0, sticky="w")
        self.stat_tabs = ttk.Notebook(right)
        self.stat_tabs.grid(row=4, column=0, sticky="nsew", pady=(6, 0))
        self.stat_texts = {}
        for key, label in [
            ("card", "Quick Card"),
            ("storylines", "Storylines"),
            ("splits", "Best Splits"),
            ("sources", "Sources / Notes"),
            ("career", "AUSL Career"),
            ("current", str(CURRENT_YEAR)),
            ("previous", str(CURRENT_YEAR - 1)),
            ("fielding", "Fielding"),
        ]:
            frame = ttk.Frame(self.stat_tabs)
            text = tk.Text(frame, wrap="word", font=("Consolas", 12), relief="flat", padx=12, pady=12)
            text.pack(fill="both", expand=True)
            text.configure(state="disabled")
            self.stat_tabs.add(frame, text=label)
            self.stat_texts[key] = text

    def _build_producer_prep(self, parent):
        controls = ttk.LabelFrame(parent, text="Producer Prep Assistant", padding=8)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Button(controls, text="Refresh Storylines / Graphics", command=self.render_producer_prep).pack(side="left")
        ttk.Button(controls, text="Copy Graphic Queue", command=self.copy_producer_prep).pack(side="left", padx=8)
        ttk.Button(controls, text="Generate Producer Packet", style="Accent.TButton", command=self.generate_pregame_report).pack(side="left", padx=8)
        self.producer_prep_status = tk.StringVar(value="Select game teams, then refresh.")
        ttk.Label(controls, textvariable=self.producer_prep_status, style="Sub.TLabel").pack(side="left", padx=8)

        body = ttk.LabelFrame(parent, text="Suggested Prep Notes", padding=6)
        body.pack(fill="both", expand=True)
        self.producer_prep_text = tk.Text(body, wrap="word", font=("Consolas", 12), padx=12, pady=12)
        self.producer_prep_text.pack(fill="both", expand=True)
        self._set_text(self.producer_prep_text, "Producer prep suggestions will appear here once the database loads.")

    def _build_manual_notes(self, parent):
        form = ttk.LabelFrame(parent, text="Manual Producer Note", padding=10)
        form.pack(fill="x", pady=(0, 8))
        self.note_player_var = tk.StringVar()
        self.note_team_var = tk.StringVar(value="")
        self.note_type_var = tk.StringVar(value="producer")
        self.note_air_safe_var = tk.BooleanVar(value=False)
        ttk.Label(form, text="Player name").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.note_player_var, width=28).grid(row=0, column=1, padx=6, sticky="w")
        ttk.Label(form, text="Team code").grid(row=0, column=2, sticky="w", padx=(12, 0))
        ttk.Entry(form, textvariable=self.note_team_var, width=8).grid(row=0, column=3, padx=6, sticky="w")
        ttk.Label(form, text="Type").grid(row=0, column=4, sticky="w", padx=(12, 0))
        ttk.Combobox(form, values=["producer", "pronunciation", "injury", "story", "do-not-use", "verified"], textvariable=self.note_type_var, state="readonly", width=16).grid(row=0, column=5, padx=6, sticky="w")
        ttk.Checkbutton(form, text="Air safe", variable=self.note_air_safe_var).grid(row=0, column=6, padx=8, sticky="w")
        ttk.Label(form, text="Note").grid(row=1, column=0, sticky="nw", pady=(8, 0))
        self.note_text = tk.Text(form, height=4, wrap="word", width=90)
        self.note_text.grid(row=1, column=1, columnspan=6, sticky="ew", padx=6, pady=(8, 0))
        form.columnconfigure(6, weight=1)
        buttons = ttk.Frame(form)
        buttons.grid(row=2, column=1, columnspan=6, sticky="w", pady=8, padx=6)
        ttk.Button(buttons, text="Save Manual Note", style="Accent.TButton", command=self.save_manual_note).pack(side="left")
        ttk.Button(buttons, text="Reload Notes", command=self.reload_manual_notes).pack(side="left", padx=8)
        self.manual_note_status = tk.StringVar(value="Manual notes are saved locally under data/manual/player_notes.csv")
        ttk.Label(form, textvariable=self.manual_note_status, style="Sub.TLabel").grid(row=3, column=1, columnspan=6, sticky="w", padx=6)

        list_frame = ttk.LabelFrame(parent, text="Current Manual Notes", padding=6)
        list_frame.pack(fill="both", expand=True)
        self.manual_notes_view = tk.Text(list_frame, wrap="word", font=("Consolas", 11), padx=10, pady=10)
        self.manual_notes_view.pack(fill="both", expand=True)
        self._set_text(self.manual_notes_view, "Manual notes will appear here after the database loads.")

    def _build_live(self, parent):
        controls = ttk.LabelFrame(parent, text="Official AUSL Live Feed", padding=8)
        controls.pack(fill="x")
        ttk.Label(controls, text="Game ID").pack(side="left")
        self.game_id_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.game_id_var, width=12).pack(side="left", padx=6)
        ttk.Button(controls, text="Load / Refresh Game", command=self.refresh_live).pack(side="left")
        self.auto_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(controls, text="Auto-refresh every 30 seconds", variable=self.auto_var, command=self._schedule_live).pack(side="left", padx=16)
        self.live_status = tk.StringVar(value="Enter the number at the end of an AUSL game-page URL.")
        ttk.Label(controls, textvariable=self.live_status, style="Sub.TLabel").pack(side="left", padx=8)

        search = ttk.Frame(parent, padding=(0, 8))
        search.pack(fill="x")
        ttk.Label(search, text="Live player search:").pack(side="left")
        self.live_search_var = tk.StringVar()
        live_entry = ttk.Entry(search, textvariable=self.live_search_var, width=30)
        live_entry.pack(side="left", padx=6)
        live_entry.bind("<KeyRelease>", lambda _e: self.search_live_player())
        ttk.Button(search, text="Find Player", command=self.search_live_player).pack(side="left")
        ttk.Button(search, text="Copy Player Line", command=self.copy_live_player).pack(side="left", padx=(6, 0))
        ttk.Button(search, text="Copy Game Comparison", command=self.copy_live_comparison).pack(side="left", padx=12)

        pane = ttk.Panedwindow(parent, orient="horizontal")
        pane.pack(fill="both", expand=True)
        summary_frame = ttk.LabelFrame(pane, text="Game / Team Comparison", padding=6)
        player_frame = ttk.LabelFrame(pane, text="Live Player Lines", padding=6)
        pane.add(summary_frame, weight=3)
        pane.add(player_frame, weight=2)
        self.live_summary = tk.Text(summary_frame, wrap="word", font=("Consolas", 12), padx=10, pady=10)
        self.live_summary.pack(fill="both", expand=True)
        self.live_player = tk.Text(player_frame, wrap="word", font=("Consolas", 12), padx=10, pady=10)
        self.live_player.pack(fill="both", expand=True)
        self._set_text(self.live_summary, "Waiting for a game ID.\n\nLive data is marked YELLOW until checked against the official game book.")
        self._set_text(self.live_player, "Load a game, then type a player name above.")

    def _build_team_totals(self, parent):
        controls = ttk.LabelFrame(parent, text="Team Season Totals", padding=10)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Team").pack(side="left")
        self.team_totals_team_var = tk.StringVar(value=TEAM_OPTIONS[0])
        team_box = ttk.Combobox(controls, values=TEAM_OPTIONS, textvariable=self.team_totals_team_var, state="readonly", width=25)
        team_box.pack(side="left", padx=(6, 16))
        team_box.bind("<<ComboboxSelected>>", lambda _event: self.render_team_totals())
        ttk.Label(controls, text="Season").pack(side="left")
        self.team_totals_season_var = tk.StringVar(value=str(CURRENT_YEAR))
        season_box = ttk.Combobox(controls, values=[str(year) for year in sorted(SEASONS, reverse=True)], textvariable=self.team_totals_season_var, state="readonly", width=8)
        season_box.pack(side="left", padx=6)
        season_box.bind("<<ComboboxSelected>>", lambda _event: self.render_team_totals())
        ttk.Button(controls, text="Copy Team Totals", command=self.copy_team_totals).pack(side="left", padx=12)
        self.team_totals_status = tk.StringVar(value="")
        ttk.Label(controls, textvariable=self.team_totals_status, style="Sub.TLabel").pack(side="left", padx=6)

        self.team_totals_summary_var = tk.StringVar(value="Loading team totals...")
        ttk.Label(parent, textvariable=self.team_totals_summary_var, font=("Segoe UI", 14, "bold")).pack(anchor="w", pady=(2, 8))
        tables = ttk.Notebook(parent)
        tables.pack(fill="both", expand=True)
        self.team_stat_trees = {}
        specs = {
            "batting": ("PLAYER", "G", "AB", "R", "H", "2B", "3B", "HR", "RBI", "BB", "SO", "SB", "AVG", "OBP", "SLG", "OPS"),
            "pitching": ("PLAYER", "APP", "GS", "W", "L", "ERA", "IP", "H", "R", "ER", "BB", "SO", "WHIP", "SV"),
            "fielding": ("PLAYER", "POS", "G", "PO", "A", "E", "FLD%", "DP"),
        }
        for key, columns in specs.items():
            frame = ttk.Frame(tables)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            tree = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse")
            for column in columns:
                width = 210 if column == "PLAYER" else 72
                tree.heading(column, text=column, command=lambda c=column, t=tree: self.sort_stats_tree(t, c, False))
                tree.column(column, width=width, minwidth=55, anchor="w" if column == "PLAYER" else "center")
            vertical = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
            horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.grid(row=0, column=0, sticky="nsew")
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            tree.tag_configure("total", background="#dfe8f3", font=("Segoe UI", 10, "bold"))
            tables.add(frame, text=key.title())
            self.team_stat_trees[key] = tree
        self.team_totals_copy_text = ""

    @staticmethod
    def sort_stats_tree(tree, column, reverse):
        def sort_key(item_id):
            item = tree.set(item_id, column)
            try:
                return (0, float(str(item).replace("—", "nan")))
            except ValueError:
                return (1, str(item).lower())
        normal = [item for item in tree.get_children("") if "total" not in tree.item(item, "tags")]
        totals = [item for item in tree.get_children("") if "total" in tree.item(item, "tags")]
        normal.sort(key=sort_key, reverse=reverse)
        for index, item in enumerate(normal + totals):
            tree.move(item, "", index)
        tree.heading(column, command=lambda: AUSLStatsApp.sort_stats_tree(tree, column, not reverse))

    def _load_initial(self):
        def work():
            try:
                data = load_database()
                self.root.after(0, lambda: self._finish_load(data))
            except Exception as exc:
                self.root.after(0, lambda: self._load_failed(exc))
        threading.Thread(target=work, daemon=True).start()

    def _finish_load(self, data):
        self.db = data
        self.data_freshness_text = self.format_data_freshness(data.get("manifest", {}))
        self.data_freshness_var.set(self.data_freshness_text)
        self.status_var.set(f"Ready — {len(data['roster'])} current players loaded")
        self.show_all()
        self.render_team_totals()
        self.render_producer_prep()
        self.render_manual_notes()

    def _load_failed(self, exc):
        self.status_var.set("Database could not be loaded")
        messagebox.showerror("AUSL Data", str(exc))

    def manual_notes_path(self):
        path = export_dir().parent / "manual" / "player_notes.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def render_manual_notes(self):
        if not hasattr(self, "manual_notes_view"):
            return
        notes = self.db.get("manual_notes", pd.DataFrame()) if self.db else pd.DataFrame()
        if notes.empty:
            self._set_text(self.manual_notes_view, "No manual producer notes saved yet.")
            return
        lines = ["MANUAL PRODUCER NOTES", "These are user-entered notes. Verify before air unless marked AIR SAFE.", ""]
        for _, row in notes.tail(60).iterrows():
            safe = "AIR SAFE" if str(value(row, "air_safe", "")).lower() in {"true", "1", "yes"} else "VERIFY"
            lines.append(f"[{safe}] {value(row, 'player_name')} {value(row, 'team_code')} - {value(row, 'note_type')}: {value(row, 'note_text')}")
        self._set_text(self.manual_notes_view, "\n".join(lines))

    def reload_manual_notes(self):
        path = self.manual_notes_path()
        self.db["manual_notes"] = pd.read_csv(path) if path.exists() else pd.DataFrame()
        self.render_manual_notes()
        self.manual_note_status.set("Manual notes reloaded")

    def save_manual_note(self):
        if not self.db:
            self.manual_note_status.set("Database is still loading")
            return
        player_name = self.note_player_var.get().strip()
        team_code = self.note_team_var.get().strip().upper()
        note_text = self.note_text.get("1.0", "end").strip()
        if not note_text:
            self.manual_note_status.set("Type a note before saving")
            return
        roster = self.db.get("roster", pd.DataFrame())
        player_id = ""
        if player_name and not roster.empty:
            matches = roster[roster["player_name"].astype(str).str.lower().eq(player_name.lower())]
            if matches.empty:
                matches = roster[roster["player_name"].astype(str).str.lower().str.contains(player_name.lower(), regex=False)]
            if not matches.empty:
                player_id = matches.iloc[0]["player_id"]
                player_name = matches.iloc[0]["player_name"]
                team_code = team_code or str(matches.iloc[0].get("team_code", "")).upper()
        path = self.manual_notes_path()
        existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
        existing_ids = pd.to_numeric(existing.get("note_id", pd.Series(dtype=float)), errors="coerce") if not existing.empty else pd.Series(dtype=float)
        note_id = int(existing_ids.max()) + 1 if not existing_ids.empty and pd.notna(existing_ids.max()) else 1
        row = {
            "note_id": note_id,
            "player_id": player_id,
            "player_name": player_name,
            "team_code": team_code,
            "note_text": note_text,
            "note_type": self.note_type_var.get(),
            "entered_by": "producer",
            "source": "manual",
            "last_verified_date": date.today().isoformat() if self.note_air_safe_var.get() else "",
            "air_safe": bool(self.note_air_safe_var.get()),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        updated = pd.concat([existing, pd.DataFrame([row])], ignore_index=True)
        updated.to_csv(path, index=False)
        self.db["manual_notes"] = updated
        self.note_text.delete("1.0", "end")
        self.manual_note_status.set("Manual note saved locally")
        self.render_manual_notes()

    def update_data(self):
        self.status_var.set("Updating official AUSL data...")
        def progress(message):
            self.root.after(0, lambda m=message: self.status_var.set(m))
        def work():
            try:
                update_all_data(progress)
                data = load_database()
                self.root.after(0, lambda: self._finish_load(data))
            except Exception as exc:
                self.root.after(0, lambda: self._load_failed(exc))
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def format_data_freshness(manifest):
        updated_at = (manifest or {}).get("updated_at")
        if not updated_at:
            return f"Data updated: unknown - {VERIFY_NOTE}"
        try:
            timestamp = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            timestamp = timestamp.astimezone(timezone.utc)
            time_text = timestamp.strftime("%I:%M %p").lstrip("0")
            return f"Data updated: {timestamp.strftime('%B')} {timestamp.day}, {timestamp.year} at {time_text} UTC - {VERIFY_NOTE}"
        except ValueError:
            return f"Data updated: {updated_at} - {VERIFY_NOTE}"

    def game_codes(self):
        return {TEAM_TO_CODE.get(self.away_var.get()), TEAM_TO_CODE.get(self.home_var.get())}

    @staticmethod
    def roster_status(row):
        status = str(value(row, "roster_status", "Status unknown")).strip()
        return status if status and status != "â€”" else "Status unknown"

    @classmethod
    def is_active_roster(cls, row):
        return cls.roster_status(row).strip().lower() == "active"

    @classmethod
    def availability_warning(cls, row):
        status = cls.roster_status(row)
        if status.lower() == "active":
            return ""
        if status.lower() == "status unknown":
            return "Warning: roster status is unknown. Verify availability before using on air."
        return f"Warning: player is listed as {status}. Verify availability before using on air."


    def show_all(self):
        self.search_var.set("")
        self.search()

    def show_team(self, team):
        self.scope_var.set(False)
        self.search_var.set(team)
        self.search()

    def search(self):
        if not self.db:
            return
        frame = self.db["roster"].copy()
        if self.scope_var.get():
            frame = frame[frame["team_code"].isin(self.game_codes())]
        query = self.search_var.get().strip().lower()
        if query:
            if query.startswith("#"):
                number = query[1:].strip()
                frame = frame[frame["jersey_number"].astype(str).str.replace(".0", "", regex=False).eq(number)]
            else:
                fields = frame[["player_name", "team", "team_code", "position", "college"]].fillna("").astype(str)
                mask = fields.apply(lambda column: column.str.lower().str.contains(query, regex=False)).any(axis=1)
                frame = frame[mask]
        frame = frame.sort_values(["team_code", "last_name", "first_name"])
        self.search_rows = [row for _, row in frame.iterrows()]
        self.results.delete(0, "end")
        for row in self.search_rows:
            number = str(value(row, "jersey_number", "")).replace(".0", "")
            status = self.roster_status(row)
            flag = "" if self.is_active_roster(row) else " !"
            self.results.insert("end", f"{row['team_code']:<3}  #{number:<3} {row['position']:<4} {row['player_name']}  [{status}]{flag}")
        if not self.search_rows and query.startswith("#") and self.scope_var.get():
            self.results.insert("end", "No matching number on selected game teams")

    def select_result(self, _event=None):
        selected = self.results.curselection()
        if not selected or selected[0] >= len(self.search_rows):
            return
        row = self.search_rows[selected[0]]
        self.selected_player_id = int(row["player_id"])
        self.render_player(row)

    def stat_row(self, key):
        frame = self.db.get(key, pd.DataFrame())
        if frame.empty or self.selected_player_id is None or "player_id" not in frame:
            return None
        rows = frame[pd.to_numeric(frame["player_id"], errors="coerce").eq(self.selected_player_id)]
        return None if rows.empty else rows.iloc[0]

    def batting_line(self, row):
        if row is None:
            return "No batting stats available"
        return f"{whole(row,'gamesPlayed')} G | {whole(row,'plateAppearances')} PA | {whole(row,'hits')} H | {whole(row,'homeRuns')} HR | {whole(row,'runsBattedIn')} RBI | {decimal(row,'battingAverage')} AVG | {decimal(row,'onBasePercentage')} OBP | {decimal(row,'sluggingPercentage')} SLG | {decimal(row,'opsPercentage')} OPS"

    def pitching_line(self, row):
        if row is None:
            return "No pitching stats available"
        return f"{whole(row,'appearances')} APP / {whole(row,'gamesStarted')} GS | {whole(row,'wins')}-{whole(row,'losses')} | {value(row,'inningsPitched')} IP | {decimal(row,'earnedRunAverage',2)} ERA | {whole(row,'strikeOuts')} SO | {whole(row,'baseOnBalls')} BB | {decimal(row,'whip',2)} WHIP | {whole(row,'saves')} SV"

    def fielding_line(self, row):
        if row is None:
            return "No fielding stats available"
        return f"{whole(row,'gamesPlayed')} G | {whole(row,'putOuts')} PO | {whole(row,'assists')} A | {whole(row,'errors')} E | {decimal(row,'fieldingPercent')} FLD%"

    @staticmethod
    def _frame_sum(frame, column):
        if frame.empty or column not in frame:
            return 0.0
        return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())

    @staticmethod
    def _innings_to_outs(value_to_convert):
        try:
            number = float(value_to_convert)
            whole_innings = int(number)
            partial = int(round((number - whole_innings) * 10))
            return whole_innings * 3 + min(max(partial, 0), 2)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _outs_to_innings(outs):
        return f"{int(outs) // 3}.{int(outs) % 3}"

    def render_team_totals(self):
        if not self.db or not hasattr(self, "team_stat_trees"):
            return
        team = self.team_totals_team_var.get()
        year = int(self.team_totals_season_var.get())
        code = TEAM_TO_CODE.get(team, "")

        def team_frame(category):
            frame = self.db.get(f"{category}_{year}", pd.DataFrame())
            if frame.empty or "team_code" not in frame:
                return frame.iloc[0:0]
            return frame[frame["team_code"].astype(str).eq(code)]

        batting = team_frame("batting")
        pitching = team_frame("pitching")
        fielding = team_frame("fielding")
        for tree in self.team_stat_trees.values():
            tree.delete(*tree.get_children(""))
        if batting.empty and pitching.empty:
            self.team_totals_copy_text = f"No {year} totals are available for {team}."
            self.team_totals_summary_var.set(self.team_totals_copy_text)
            return

        ab = self._frame_sum(batting, "atBat")
        hits = self._frame_sum(batting, "hits")
        walks = self._frame_sum(batting, "baseonBalls")
        hbp = self._frame_sum(batting, "hitByPitch")
        sac_flies = self._frame_sum(batting, "sacrificeFly")
        total_bases = self._frame_sum(batting, "totalBases")
        avg = hits / ab if ab else 0
        obp_denominator = ab + walks + hbp + sac_flies
        obp = (hits + walks + hbp) / obp_denominator if obp_denominator else 0
        slg = total_bases / ab if ab else 0

        wins = int(self._frame_sum(pitching, "wins"))
        losses = int(self._frame_sum(pitching, "losses"))
        innings_outs = sum(self._innings_to_outs(item) for item in pitching.get("inningsPitched", pd.Series(dtype=float)))
        innings_decimal = innings_outs / 3
        hits_allowed = self._frame_sum(pitching, "hitsAllowed")
        earned_runs = self._frame_sum(pitching, "earnedRuns")
        pitching_walks = self._frame_sum(pitching, "baseOnBalls")
        era = earned_runs * 7 / innings_decimal if innings_decimal else 0
        whip = (hits_allowed + pitching_walks) / innings_decimal if innings_decimal else 0

        putouts = self._frame_sum(fielding, "putOuts")
        assists = self._frame_sum(fielding, "assists")
        errors = self._frame_sum(fielding, "errors")
        chances = self._frame_sum(fielding, "totalChances")
        fielding_percent = (putouts + assists) / chances if chances else 0

        self.team_totals_summary_var.set(f"{team}  |  {year}  |  {wins}-{losses}  •  Click any column heading to sort")
        batting_tree = self.team_stat_trees["batting"]
        for _, row in batting.sort_values("atBat", ascending=False, na_position="last").iterrows():
            batting_tree.insert("", "end", values=(
                value(row, "player_name"), whole(row, "gamesPlayed"), whole(row, "atBat"), whole(row, "runs"), whole(row, "hits"),
                whole(row, "doubles"), whole(row, "triples"), whole(row, "homeRuns"), whole(row, "runsBattedIn"), whole(row, "baseonBalls"),
                whole(row, "strikeOuts"), whole(row, "stolenBases"), decimal(row, "battingAverage"), decimal(row, "onBasePercentage"),
                decimal(row, "sluggingPercentage"), decimal(row, "opsPercentage"),
            ))
        batting_tree.insert("", "end", tags=("total",), values=(
            "TEAM TOTALS", wins + losses, int(ab), int(self._frame_sum(batting, "runs")), int(hits), int(self._frame_sum(batting, "doubles")),
            int(self._frame_sum(batting, "triples")), int(self._frame_sum(batting, "homeRuns")), int(self._frame_sum(batting, "runsBattedIn")),
            int(walks), int(self._frame_sum(batting, "strikeOuts")), int(self._frame_sum(batting, "stolenBases")), f"{avg:.3f}", f"{obp:.3f}",
            f"{slg:.3f}", f"{obp + slg:.3f}",
        ))

        pitching_tree = self.team_stat_trees["pitching"]
        pitching_rows = list(pitching.iterrows())
        pitching_rows.sort(key=lambda item: self._innings_to_outs(value(item[1], "inningsPitched", 0)), reverse=True)
        for _, row in pitching_rows:
            player_outs = self._innings_to_outs(value(row, "inningsPitched", 0))
            player_innings = player_outs / 3
            player_whip = (self._stat_number(row, "hitsAllowed") + self._stat_number(row, "baseOnBalls")) / player_innings if player_innings else 0
            pitching_tree.insert("", "end", values=(
                value(row, "player_name"), whole(row, "appearances"), whole(row, "gamesStarted"), whole(row, "wins"), whole(row, "losses"),
                decimal(row, "earnedRunAverage", 2), value(row, "inningsPitched"), whole(row, "hitsAllowed"), whole(row, "runs"), whole(row, "earnedRuns"),
                whole(row, "baseOnBalls"), whole(row, "strikeOuts"), f"{player_whip:.2f}", whole(row, "saves"),
            ))
        pitching_tree.insert("", "end", tags=("total",), values=(
            "TEAM TOTALS", int(self._frame_sum(pitching, "appearances")), "—", wins, losses, f"{era:.2f}", self._outs_to_innings(innings_outs),
            int(hits_allowed), int(self._frame_sum(pitching, "runs")), int(earned_runs), int(pitching_walks), int(self._frame_sum(pitching, "strikeOuts")),
            f"{whip:.2f}", int(self._frame_sum(pitching, "saves")),
        ))

        fielding_tree = self.team_stat_trees["fielding"]
        for _, row in fielding.sort_values("gamesPlayed", ascending=False, na_position="last").iterrows():
            fielding_tree.insert("", "end", values=(
                value(row, "player_name"), value(row, "fielding_positions", value(row, "position")), whole(row, "gamesPlayed"), whole(row, "putOuts"),
                whole(row, "assists"), whole(row, "errors"), decimal(row, "fieldingPercent"), whole(row, "doublePlays"),
            ))
        fielding_tree.insert("", "end", tags=("total",), values=(
            "TEAM TOTALS", "—", wins + losses, int(putouts), int(assists), int(errors), f"{fielding_percent:.3f}", int(self._frame_sum(fielding, "doublePlays")),
        ))

        output = [
            f"{team.upper()} — {year} TEAM TOTALS",
            self.data_freshness_text,
            "IMPORTED DATA - VERIFY BEFORE AIR",
            "",
            "TEAM RECORD",
            f"{wins}-{losses}  |  {wins + losses} games",
            "",
            "BATTING",
            f"{int(ab)} AB  |  {int(self._frame_sum(batting, 'runs'))} R  |  {int(hits)} H  |  {int(self._frame_sum(batting, 'doubles'))} 2B  |  {int(self._frame_sum(batting, 'triples'))} 3B",
            f"{int(self._frame_sum(batting, 'homeRuns'))} HR  |  {int(self._frame_sum(batting, 'runsBattedIn'))} RBI  |  {int(walks)} BB  |  {int(self._frame_sum(batting, 'strikeOuts'))} SO  |  {int(self._frame_sum(batting, 'stolenBases'))} SB",
            f"{avg:.3f} AVG  |  {obp:.3f} OBP  |  {slg:.3f} SLG  |  {obp + slg:.3f} OPS",
            "",
            "PITCHING",
            f"{wins}-{losses}  |  {self._outs_to_innings(innings_outs)} IP  |  {era:.2f} ERA  |  {whip:.2f} WHIP  |  {int(self._frame_sum(pitching, 'saves'))} SV",
            f"{int(hits_allowed)} H  |  {int(self._frame_sum(pitching, 'runs'))} R  |  {int(earned_runs)} ER  |  {int(pitching_walks)} BB  |  {int(self._frame_sum(pitching, 'strikeOuts'))} SO  |  {int(self._frame_sum(pitching, 'homeRuns'))} HR",
            "",
            "FIELDING",
            f"{int(putouts)} PO  |  {int(assists)} A  |  {int(errors)} E  |  {fielding_percent:.3f} FLD%  |  {int(self._frame_sum(fielding, 'doublePlays'))} DP",
        ]
        self.team_totals_copy_text = "\n".join(output)

    def copy_team_totals(self):
        if not self.team_totals_copy_text:
            self.team_totals_status.set("Select a team with available totals")
            return
        self.root.clipboard_clear(); self.root.clipboard_append(self.team_totals_copy_text); self.root.update()
        self.team_totals_status.set("Team totals copied to clipboard")

    def _player_row(self, key, player_id):
        frame = self.db.get(key, pd.DataFrame())
        if frame.empty or "player_id" not in frame:
            return None
        rows = frame[pd.to_numeric(frame["player_id"], errors="coerce").eq(int(player_id))]
        return None if rows.empty else rows.iloc[0]

    def _projected_lineup(self, team, year):
        roster = self.db["roster"][self.db["roster"]["team"].eq(team)].copy()
        batting = self.db.get(f"batting_{year}", pd.DataFrame())
        candidates = []
        for _, player in roster.iterrows():
            stats = self._player_row(f"batting_{year}", player["player_id"])
            if stats is None:
                continue
            position = str(value(player, "position", "")).upper()
            candidates.append(
                {
                    "player": player,
                    "stats": stats,
                    "pitcher_only": position in {"P", "RHP", "LHP"},
                    "starts": self._stat_number(stats, "gamesStarted"),
                    "pa": self._stat_number(stats, "plateAppearances"),
                    "obp": self._stat_number(stats, "onBasePercentage"),
                    "ops": self._stat_number(stats, "opsPercentage"),
                    "sb": self._stat_number(stats, "stolenBases"),
                }
            )
        regulars = [item for item in candidates if not item["pitcher_only"] and item["pa"] > 0]
        regulars.sort(key=lambda item: (item["starts"], item["pa"], item["ops"]), reverse=True)
        selected = regulars[:9]
        if len(selected) < 9:
            extras = [item for item in candidates if item not in selected and item["pa"] > 0]
            extras.sort(key=lambda item: (item["starts"], item["pa"], item["ops"]), reverse=True)
            selected.extend(extras[: 9 - len(selected)])
        if not selected:
            return []
        leadoff = max(selected, key=lambda item: (item["obp"] + item["sb"] * 0.01, item["pa"]))
        remainder = [item for item in selected if item is not leadoff]
        remainder.sort(key=lambda item: (item["ops"], item["obp"], item["pa"]), reverse=True)
        return [leadoff, *remainder]

    def _projected_pitchers(self, team, year):
        roster = self.db["roster"][self.db["roster"]["team"].eq(team)]
        choices = []
        for _, player in roster.iterrows():
            stats = self._player_row(f"pitching_{year}", player["player_id"])
            if stats is None:
                continue
            choices.append(
                (
                    self._stat_number(stats, "gamesStarted"),
                    self._innings_to_outs(value(stats, "inningsPitched", 0)),
                    self._stat_number(stats, "appearances"),
                    player,
                    stats,
                )
            )
        return sorted(choices, key=lambda item: item[:3], reverse=True)

    def _packet_broadcast_note(self, player, year):
        player_id = player["player_id"]
        is_pitcher = str(value(player, "position", "")).upper() in {"P", "RHP", "LHP"}
        return self.build_broadcast_note(
            is_pitcher,
            self._player_row("career_batting", player_id),
            self._player_row("career_pitching", player_id),
            self._player_row(f"batting_{year}", player_id),
            self._player_row(f"pitching_{year}", player_id),
        )

    def _packet_team_snapshot(self, team, year):
        code = TEAM_TO_CODE.get(team, "")
        batting = self.db.get(f"batting_{year}", pd.DataFrame())
        pitching = self.db.get(f"pitching_{year}", pd.DataFrame())
        batting = batting[batting["team_code"].astype(str).eq(code)] if not batting.empty else batting
        pitching = pitching[pitching["team_code"].astype(str).eq(code)] if not pitching.empty else pitching
        ab = self._frame_sum(batting, "atBat")
        hits = self._frame_sum(batting, "hits")
        walks = self._frame_sum(batting, "baseonBalls")
        hbp = self._frame_sum(batting, "hitByPitch")
        sf = self._frame_sum(batting, "sacrificeFly")
        tb = self._frame_sum(batting, "totalBases")
        avg = hits / ab if ab else 0
        obp = (hits + walks + hbp) / (ab + walks + hbp + sf) if ab + walks + hbp + sf else 0
        slg = tb / ab if ab else 0
        wins, losses = int(self._frame_sum(pitching, "wins")), int(self._frame_sum(pitching, "losses"))
        outs = sum(self._innings_to_outs(item) for item in pitching.get("inningsPitched", pd.Series(dtype=float)))
        innings = outs / 3
        earned_runs = self._frame_sum(pitching, "earnedRuns")
        era = earned_runs * 7 / innings if innings else 0
        return [
            f"Record: {wins}-{losses}",
            f"Offense: {int(self._frame_sum(batting, 'runs'))} R | {int(hits)} H | {int(self._frame_sum(batting, 'homeRuns'))} HR | {int(self._frame_sum(batting, 'runsBattedIn'))} RBI",
            f"Slash line: {avg:.3f} AVG | {obp:.3f} OBP | {slg:.3f} SLG | {obp + slg:.3f} OPS",
            f"Pitching: {self._outs_to_innings(outs)} IP | {era:.2f} ERA | {int(self._frame_sum(pitching, 'strikeOuts'))} SO",
        ]

    def _team_frames(self, team, year):
        code = TEAM_TO_CODE.get(team, "")
        batting = self.db.get(f"batting_{year}", pd.DataFrame())
        pitching = self.db.get(f"pitching_{year}", pd.DataFrame())
        fielding = self.db.get(f"fielding_{year}", pd.DataFrame())
        if not batting.empty and "team_code" in batting:
            batting = batting[batting["team_code"].astype(str).eq(code)]
        if not pitching.empty and "team_code" in pitching:
            pitching = pitching[pitching["team_code"].astype(str).eq(code)]
        if not fielding.empty and "team_code" in fielding:
            fielding = fielding[fielding["team_code"].astype(str).eq(code)]
        return batting, pitching, fielding

    def _team_metrics(self, team, year):
        batting, pitching, fielding = self._team_frames(team, year)
        ab = self._frame_sum(batting, "atBat")
        hits = self._frame_sum(batting, "hits")
        walks = self._frame_sum(batting, "baseonBalls")
        hbp = self._frame_sum(batting, "hitByPitch")
        sf = self._frame_sum(batting, "sacrificeFly")
        total_bases = self._frame_sum(batting, "totalBases")
        outs = sum(self._innings_to_outs(item) for item in pitching.get("inningsPitched", pd.Series(dtype=float)))
        innings = outs / 3
        earned_runs = self._frame_sum(pitching, "earnedRuns")
        pitching_walks = self._frame_sum(pitching, "baseOnBalls")
        hits_allowed = self._frame_sum(pitching, "hitsAllowed")
        chances = self._frame_sum(fielding, "totalChances")
        return {
            "wins": int(self._frame_sum(pitching, "wins")),
            "losses": int(self._frame_sum(pitching, "losses")),
            "runs": int(self._frame_sum(batting, "runs")),
            "hits": int(hits),
            "home_runs": int(self._frame_sum(batting, "homeRuns")),
            "rbi": int(self._frame_sum(batting, "runsBattedIn")),
            "stolen_bases": int(self._frame_sum(batting, "stolenBases")),
            "avg": hits / ab if ab else 0,
            "obp": (hits + walks + hbp) / (ab + walks + hbp + sf) if ab + walks + hbp + sf else 0,
            "slg": total_bases / ab if ab else 0,
            "ops": ((hits + walks + hbp) / (ab + walks + hbp + sf) if ab + walks + hbp + sf else 0) + (total_bases / ab if ab else 0),
            "era": earned_runs * 7 / innings if innings else 0,
            "whip": (hits_allowed + pitching_walks) / innings if innings else 0,
            "strikeouts": int(self._frame_sum(pitching, "strikeOuts")),
            "errors": int(self._frame_sum(fielding, "errors")),
            "fielding_pct": (self._frame_sum(fielding, "putOuts") + self._frame_sum(fielding, "assists")) / chances if chances else 0,
        }

    def _standings_row(self, team, year=CURRENT_YEAR):
        standings = self.db.get("standings", pd.DataFrame())
        if standings.empty:
            return None
        code = TEAM_TO_CODE.get(team, "")
        rows = standings[
            standings.get("team_code", pd.Series(dtype=str)).astype(str).eq(code)
            & pd.to_numeric(standings.get("seasonId", pd.Series(dtype=float)), errors="coerce").eq(SEASONS.get(year, year))
            & standings.get("standingsTypeLk", pd.Series(dtype=str)).astype(str).ne("POST")
        ]
        if rows.empty:
            rows = standings[
                standings.get("team_code", pd.Series(dtype=str)).astype(str).eq(code)
                & pd.to_numeric(standings.get("seasonId", pd.Series(dtype=float)), errors="coerce").eq(SEASONS.get(year, year))
            ]
        return None if rows.empty else rows.iloc[0]

    def _team_context_line(self, team, year=CURRENT_YEAR):
        row = self._standings_row(team, year)
        if row is None:
            metrics = self._team_metrics(team, year)
            return f"{team}: {metrics['runs']} runs scored, {metrics['home_runs']} HR, {metrics['era']:.2f} team ERA from local AUSL totals."
        rank = whole(row, "rank")
        wins, losses = whole(row, "wins"), whole(row, "losses")
        pct = decimal(row, "winPercentage", 3)
        streak = value(row, "winStreak", "—")
        gb = value(row, "gamesBehind", "—")
        diff = whole(row, "runDifferential")
        return f"{team}: {wins}-{losses} ({pct}) | Streak {streak} | GB {gb} | Run diff {diff} | Standings rank {rank}"

    def _recent_matchup_line(self, away, home):
        schedule = self.db.get("schedule_results", pd.DataFrame())
        if schedule.empty:
            return "No schedule/rematch context imported yet."
        away_code, home_code = TEAM_TO_CODE.get(away, ""), TEAM_TO_CODE.get(home, "")
        mask = (
            schedule.get("away_team_code", pd.Series(dtype=str)).astype(str).isin([away_code, home_code])
            & schedule.get("home_team_code", pd.Series(dtype=str)).astype(str).isin([away_code, home_code])
            & schedule.get("status", pd.Series(dtype=str)).astype(str).str.lower().eq("completed")
        )
        rows = schedule[mask].sort_values("game_date", ascending=False, na_position="last")
        if rows.empty:
            return "No completed head-to-head meeting is in the imported schedule context."
        row = rows.iloc[0]
        return (
            f"Recent matchup: {value(row, 'away_team_code')} {whole(row, 'away_score')} at "
            f"{value(row, 'home_team_code')} {whole(row, 'home_score')} on {value(row, 'game_date')}."
        )

    def _why_game_matters(self, away, home, year):
        away_standings = self._standings_row(away, year)
        home_standings = self._standings_row(home, year)
        if away_standings is not None and home_standings is not None:
            return (
                f"{away} enters {whole(away_standings, 'wins')}-{whole(away_standings, 'losses')} "
                f"with a {value(away_standings, 'winStreak', '—')} streak, while {home} enters "
                f"{whole(home_standings, 'wins')}-{whole(home_standings, 'losses')} with a "
                f"{value(home_standings, 'winStreak', '—')} streak. {self._recent_matchup_line(away, home)} "
                "Use this for stakes/rematch context, then verify standings against the latest official notes before air."
            )
        away_metrics = self._team_metrics(away, year)
        home_metrics = self._team_metrics(home, year)
        away_power = away_metrics["home_runs"] >= home_metrics["home_runs"]
        offense = away if away_power else home
        pitching = home if away_power else away
        pitching_metrics = home_metrics if away_power else away_metrics
        return (
            f"{offense} brings the stronger power profile in the local database, while the {pitching} staff "
            f"enters with a {pitching_metrics['era']:.2f} team ERA. Build this as a matchup between run creation "
            "and run prevention, then verify any official standings or playoff context before air."
        )

    def _best_split_rows(self, player_id, is_pitcher, year=CURRENT_YEAR, limit=4):
        key = "pitching_splits" if is_pitcher else "batting_splits"
        frame = self.db.get(key, pd.DataFrame())
        if frame.empty or "player_id" not in frame:
            return []
        rows = frame[
            pd.to_numeric(frame["player_id"], errors="coerce").eq(int(player_id))
            & pd.to_numeric(frame.get("season", pd.Series(dtype=float)), errors="coerce").eq(year)
        ].copy()
        if rows.empty:
            return []
        if is_pitcher:
            rows["_score"] = (
                pd.to_numeric(rows.get("strikeOuts", 0), errors="coerce").fillna(0)
                - pd.to_numeric(rows.get("earnedRunAverage", 99), errors="coerce").fillna(99)
                - pd.to_numeric(rows.get("whip", 3), errors="coerce").fillna(3)
            )
            rows = rows[pd.to_numeric(rows.get("inningsPitched", 0), errors="coerce").fillna(0) > 0]
        else:
            rows["_score"] = pd.to_numeric(rows.get("opsPercentage", 0), errors="coerce").fillna(0) * 1000 + pd.to_numeric(rows.get("runsBattedIn", 0), errors="coerce").fillna(0)
            rows = rows[pd.to_numeric(rows.get("plateAppearances", 0), errors="coerce").fillna(0) >= 2]
        return [row for _, row in rows.sort_values("_score", ascending=False).head(limit).iterrows()]

    def _best_split_lines(self, player_id, is_pitcher, year=CURRENT_YEAR):
        rows = self._best_split_rows(player_id, is_pitcher, year)
        if not rows:
            return ["No official split rows imported yet. Run Update All Data to refresh splits."]
        lines = []
        for row in rows:
            if is_pitcher:
                lines.append(
                    f"- {value(row, 'split_label')}: {value(row, 'inningsPitched')} IP | "
                    f"{decimal(row, 'earnedRunAverage', 2)} ERA | {whole(row, 'strikeOuts')} SO | {decimal(row, 'whip', 2)} WHIP"
                )
            else:
                lines.append(
                    f"- {value(row, 'split_label')}: {whole(row, 'plateAppearances')} PA | "
                    f"{decimal(row, 'battingAverage')} AVG | {decimal(row, 'opsPercentage')} OPS | "
                    f"{whole(row, 'runsBattedIn')} RBI"
                )
        return lines

    def _manual_note_lines(self, player_id=None, team_code=None):
        notes = self.db.get("manual_notes", pd.DataFrame())
        if notes.empty:
            return []
        rows = notes
        if player_id is not None and "player_id" in rows:
            rows = rows[pd.to_numeric(rows["player_id"], errors="coerce").eq(int(player_id))]
        if team_code and "team_code" in rows and rows.empty:
            rows = notes[notes["team_code"].astype(str).eq(team_code)]
        lines = []
        for _, row in rows.head(6).iterrows():
            safe = "AIR SAFE" if str(value(row, "air_safe", "")).lower() in {"true", "1", "yes"} else "VERIFY"
            lines.append(f"- [{safe}] {value(row, 'note_text')} ({value(row, 'source', 'manual note')})")
        return lines

    def _storyline_lines(self, roster, is_pitcher):
        player_id = roster["player_id"]
        lines = [
            f"PLAYER STORY CARD - {roster['player_name'].upper()}",
            self.data_freshness_text,
            "",
            "STATUS / VERIFY",
            self.availability_warning(roster) or "Listed active in the current roster file.",
            "",
            "STAT ANGLE",
            self.current_broadcast_note,
            "",
            "SPLIT ANGLE",
            *(self._best_split_lines(player_id, is_pitcher)[:2]),
            "",
            "BIO / CONTEXT ANGLE",
            f"College: {value(roster, 'college')} | Hometown: {value(roster, 'hometown')} | B/T: {value(roster, 'bats_throws')}",
        ]
        draft = self.compact_draft_info(roster)
        if draft:
            lines.append(f"Draft/context note: {draft}")
        manual = self._manual_note_lines(player_id=player_id, team_code=value(roster, "team_code", ""))
        if manual:
            lines.extend(["", "MANUAL PRODUCER NOTES", *manual])
        lines.extend(
            [
                "",
                "GFX IDEA",
                f"{roster['player_name'].upper()} - {('Pitching Spotlight' if is_pitcher else 'Player to Watch')}",
                "",
                "BIG-SPOT NOTE",
                "Use this card if she comes up in a high-leverage spot or is central to the inning. Verify all generated notes before air.",
            ]
        )
        return lines

    def _players_to_watch(self, team, year):
        roster = self.db["roster"][self.db["roster"]["team"].eq(team)]
        hitters, pitchers = [], []
        for _, player in roster.iterrows():
            batting = self._player_row(f"batting_{year}", player["player_id"])
            pitching = self._player_row(f"pitching_{year}", player["player_id"])
            if batting is not None and self._stat_number(batting, "plateAppearances") > 0:
                score = self._stat_number(batting, "opsPercentage") * 1000 + self._stat_number(batting, "runsBattedIn") + self._stat_number(batting, "homeRuns") * 4
                hitters.append((score, player, batting))
            if pitching is not None and self._stat_number(pitching, "appearances") > 0:
                score = self._stat_number(pitching, "strikeOuts") + self._stat_number(pitching, "gamesStarted") * 5 - self._stat_number(pitching, "earnedRunAverage")
                pitchers.append((score, player, pitching))
        hitters.sort(key=lambda item: item[0], reverse=True)
        pitchers.sort(key=lambda item: item[0], reverse=True)

        watched = []
        for _score, player, stats in hitters[:2]:
            watched.append((player, f"{decimal(stats, 'battingAverage')} AVG | {whole(stats, 'homeRuns')} HR | {whole(stats, 'runsBattedIn')} RBI", self._packet_broadcast_note(player, year)))
        if pitchers:
            _score, player, stats = pitchers[0]
            watched.append((player, f"{whole(stats, 'wins')}-{whole(stats, 'losses')} | {decimal(stats, 'earnedRunAverage', 2)} ERA | {whole(stats, 'strikeOuts')} SO", self._packet_broadcast_note(player, year)))
        return watched

    def _milestone_watch(self, team, year, limit=6):
        roster = self.db["roster"][self.db["roster"]["team"].eq(team)]
        notes = []
        for _, player in roster.iterrows():
            note = self._packet_broadcast_note(player, year)
            if note.startswith("Needs"):
                warning = self.availability_warning(player)
                suffix = f" ({warning})" if warning else ""
                notes.append(f"- {player['player_name']}: {note}{suffix}")
        return notes[:limit]

    def _graphic_suggestions(self, away, home, year):
        suggestions = [
            ("Opening Matchup", f"{away} at {home} with data timestamp and verification note."),
            ("Team Offensive Comparison", "Runs, hits, home runs, RBI, AVG/OBP/SLG/OPS for both clubs."),
            ("Starting Pitcher Comparison", "Use projected pitchers until official starters are confirmed."),
            ("Players to Watch", "Two hitters plus one pitcher per team based on current AUSL stats."),
            ("Milestone Watch", "AUSL-only career milestones that are close enough to track during the game."),
            ("Lineup Card", "Build from official lineup once released; projected lineup is prep-only."),
            ("Verification Checklist", "Lineups, starting pitchers, availability, pronunciations, and milestones."),
        ]
        return [f"{index}. {title} - {reason}" for index, (title, reason) in enumerate(suggestions, 1)]

    def _producer_prep_lines(self, away, home, year):
        lines = [
            f"{away.upper()} at {home.upper()} - AUSL PRODUCER PREP",
            self.data_freshness_text,
            "",
            "WHY THIS GAME MATTERS",
            self._why_game_matters(away, home, year),
            "",
            "TEAM CONTEXT / STANDINGS",
            self._team_context_line(away, year),
            self._team_context_line(home, year),
            self._recent_matchup_line(away, home),
            "",
            "TOP STORYLINES",
        ]
        for team in (away, home):
            metrics = self._team_metrics(team, year)
            lines.extend(
                [
                    f"- {team} offense: {metrics['runs']} R | {metrics['home_runs']} HR | {metrics['rbi']} RBI | {metrics['ops']:.3f} OPS.",
                    f"- {team} pitching: {metrics['era']:.2f} ERA | {metrics['whip']:.2f} WHIP | {metrics['strikeouts']} SO.",
                ]
            )
        lines.extend(["", "PLAYERS TO WATCH"])
        for team in (away, home):
            lines.append(f"{team}:")
            for player, line, note in self._players_to_watch(team, year):
                warning = self.availability_warning(player)
                lines.append(f"- {player['player_name']} ({value(player, 'position')}): {line}. {note}")
                if warning:
                    lines.append(f"  {warning}")
            if not self._players_to_watch(team, year):
                lines.append("- No player-to-watch candidate found in the current database.")
        lines.extend(["", "MILESTONE WATCH"])
        milestone_lines = self._milestone_watch(away, year) + self._milestone_watch(home, year)
        lines.extend(milestone_lines or ["- No nearby standard AUSL-only milestone identified."])
        lines.extend(["", "READY-MADE GRAPHICS QUEUE", *self._graphic_suggestions(away, home, year), "", "VERIFICATION CHECKLIST"])
        lines.extend(
            [
                "- Confirm official lineups and DP/FLEX information.",
                "- Confirm starting pitchers.",
                "- Confirm player availability, injuries, reserve status, and excused absences.",
                "- Confirm pronunciations and any producer/talent notes.",
                "- Confirm AUSL-only milestones against official notes before air.",
            ]
        )
        return lines

    def render_producer_prep(self):
        if not self.db or not hasattr(self, "producer_prep_text"):
            return
        away, home = self.away_var.get(), self.home_var.get()
        if not away or not home or away == home:
            message = "Select two different game teams to build producer prep."
            self.producer_prep_copy_text = ""
            self._set_text(self.producer_prep_text, message)
            self.producer_prep_status.set(message)
            return
        lines = self._producer_prep_lines(away, home, CURRENT_YEAR)
        self.producer_prep_copy_text = "\n".join(self._graphic_suggestions(away, home, CURRENT_YEAR))
        self._set_text(self.producer_prep_text, "\n".join(lines))
        self.producer_prep_status.set("Producer prep refreshed")

    def copy_producer_prep(self):
        if not self.producer_prep_copy_text:
            self.render_producer_prep()
        if not self.producer_prep_copy_text:
            self.producer_prep_status.set("No graphic queue to copy yet")
            return
        text = f"READY-MADE GRAPHICS QUEUE\n{self.data_freshness_text}\n\n{self.producer_prep_copy_text}"
        self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
        self.producer_prep_status.set("Graphic queue copied to clipboard")

    def generate_pregame_report(self):
        if not self.db:
            messagebox.showwarning("AUSL Broadcast Stats", "The database is still loading.")
            return
        away, home = self.away_var.get(), self.home_var.get()
        if not away or not home or away == home:
            messagebox.showwarning("AUSL Broadcast Stats", "Select two different game teams first.")
            return
        year = CURRENT_YEAR
        away_code, home_code = TEAM_TO_CODE.get(away, "AWAY"), TEAM_TO_CODE.get(home, "HOME")
        lines = [
            f"{away.upper()} at {home.upper()} - AUSL PRODUCER PRE-GAME PACKET",
            f"Generated: {date.today().isoformat()}",
            self.data_freshness_text,
            "",
            "IMPORTANT",
            "Projected lineups and starting pitchers are stat-based estimates, not official submissions.",
            VERIFY_NOTE,
            "",
            "WHY THIS GAME MATTERS",
            self._why_game_matters(away, home, year),
            "",
            "TEAM CONTEXT / STANDINGS",
            self._team_context_line(away, year),
            self._team_context_line(home, year),
            self._recent_matchup_line(away, home),
            "",
            "TOP STORYLINES",
            *[line for line in self._producer_prep_lines(away, home, year) if line.startswith("- ")][:4],
            "",
            "READY-MADE GRAPHICS QUEUE",
            *self._graphic_suggestions(away, home, year),
            "",
        ]

        for label, team in (("AWAY", away), ("HOME", home)):
            lines.extend([f"{label} — {team.upper()}", "", "PROJECTED STARTING LINEUP (NOT OFFICIAL)"])
            lineup = self._projected_lineup(team, year)
            if lineup:
                for order, item in enumerate(lineup, 1):
                    player, stats = item["player"], item["stats"]
                    jersey = str(value(player, "jersey_number", "?")).replace(".0", "")
                    status = self.roster_status(player)
                    warning = " | VERIFY AVAILABILITY" if self.availability_warning(player) else ""
                    lines.append(
                        f"{order}. #{jersey} {player['player_name']} — {value(player, 'position')} | "
                        f"{decimal(stats, 'battingAverage')} AVG | {whole(stats, 'homeRuns')} HR | {whole(stats, 'runsBattedIn')} RBI | {status}{warning}"
                    )
            else:
                lines.append("No stat-based lineup projection is available.")

            lines.extend(["", "PROJECTED STARTING PITCHER (NOT OFFICIAL)"])
            pitchers = self._projected_pitchers(team, year)
            if pitchers:
                _starts, _outs, _apps, pitcher, pitching = pitchers[0]
                jersey = str(value(pitcher, "jersey_number", "?")).replace(".0", "")
                status = self.roster_status(pitcher)
                warning = " | VERIFY AVAILABILITY" if self.availability_warning(pitcher) else ""
                lines.append(
                    f"#{jersey} {pitcher['player_name']} — {whole(pitching, 'wins')}-{whole(pitching, 'losses')} | "
                    f"{value(pitching, 'inningsPitched')} IP | {decimal(pitching, 'earnedRunAverage', 2)} ERA | {whole(pitching, 'strikeOuts')} SO | {status}{warning}"
                )
                if len(pitchers) > 1:
                    alternatives = ", ".join(item[3]["player_name"] for item in pitchers[1:3])
                    lines.append(f"Other likely options by season usage: {alternatives}")
            else:
                lines.append("No stat-based pitcher projection is available.")

            lines.extend(["", "TEAM SNAPSHOT", *self._packet_team_snapshot(team, year), "", "MILESTONE / BROADCAST NOTES"])
            roster = self.db["roster"][self.db["roster"]["team"].eq(team)]
            notes = []
            for _, player in roster.iterrows():
                note = self._packet_broadcast_note(player, year)
                if note.startswith("Needs"):
                    warning = self.availability_warning(player)
                    suffix = f" ({warning})" if warning else ""
                    notes.append(f"- {player['player_name']}: {note}{suffix}")
            lines.extend(notes[:10] or ["- No nearby standard milestone identified."])

            lines.extend(["", "QUICK ROSTER / JERSEY LOOKUP"])
            ordered = roster.assign(_number=pd.to_numeric(roster["jersey_number"], errors="coerce")).sort_values(["_number", "player_name"], na_position="last")
            for _, player in ordered.iterrows():
                jersey = str(value(player, "jersey_number", "?")).replace(".0", "")
                status = self.roster_status(player)
                draft = self.compact_draft_info(player)
                extra = f" | Draft: {draft}" if draft else ""
                warning = " | VERIFY AVAILABILITY" if self.availability_warning(player) else ""
                lines.append(f"#{jersey} — {player['player_name']} — {value(player, 'position')} — {status}{warning}{extra}")
            lines.extend(["", "=" * 72, ""])

        lines.extend(
            [
                "SOFTBALL / AUSL CONTEXT",
                "- AUSL games are scheduled for seven innings unless extras are needed.",
                "- Confirm DP/FLEX and official batting order details before building lineup graphics.",
                "- Players may have college, AU, Team USA, or international history that is not included in AUSL Career totals.",
                "- Treat media guide, news, awards, and college notes as context layers, not live stat authority.",
                "",
                "SOURCE LINKS",
                "- Official AUSL stats/splits: https://theausl.com/stats/",
                "- Official AUSL standings: https://theausl.com/standings/",
                "- Official AUSL schedule/results: https://theausl.com/schedule/",
                "- 2026 AUSL Media Guide: https://theausl.com/wp-content/uploads/2026/06/2026-AUSL-Media-Guide.pdf",
                "",
                "BROADCAST CHECKLIST",
                "- Replace projected lineups when official batting orders are released.",
                "- Confirm the announced starting pitchers before air.",
                "- Confirm DP/FLEX, bench, and unavailable players.",
                "- Confirm pronunciations and any talent/producer notes.",
                "- Verify imported milestones and team totals against official game notes.",
                "- Use the Live Game tab once the official AUSL box-score feed begins.",
            ]
        )
        packet_dir = export_dir() / "game_packets"
        packet_dir.mkdir(parents=True, exist_ok=True)
        path = packet_dir / f"{date.today().isoformat()}_{away_code}_at_{home_code}_producer_packet.txt"
        path.write_text("\n".join(lines), encoding="utf-8")
        self.status_var.set(f"Producer packet saved: {path}")
        messagebox.showinfo("AUSL Broadcast Stats", f"Producer packet created:\n\n{path}")

    def render_player(self, roster):
        number = str(value(roster, "jersey_number", "")).replace(".0", "")
        self.player_title.set(f"{roster['player_name'].upper()}   #{number}   {roster['position']}   {roster['team_code']}")
        details = [f"Status: {value(roster,'roster_status')}", f"B/T: {value(roster,'bats_throws')}", f"College: {value(roster,'college')}", f"Hometown: {value(roster,'hometown')}", f"Height: {value(roster,'height')}"]
        draft_info = self.compact_draft_info(roster)
        if draft_info:
            details[-1] += f"  |  Draft: {draft_info}"
        warning = self.availability_warning(roster)
        if warning:
            details.append(warning)
        self.player_meta.set("  |  ".join(details))
        cb, cp, cf = self.stat_row("career_batting"), self.stat_row("career_pitching"), self.stat_row("career_fielding")
        yb, yp, yf = self.stat_row(f"batting_{CURRENT_YEAR}"), self.stat_row(f"pitching_{CURRENT_YEAR}"), self.stat_row(f"fielding_{CURRENT_YEAR}")
        pb, pp, pf = self.stat_row(f"batting_{CURRENT_YEAR-1}"), self.stat_row(f"pitching_{CURRENT_YEAR-1}"), self.stat_row(f"fielding_{CURRENT_YEAR-1}")
        is_pitcher = "P" in str(roster["position"]).upper()
        self.current_broadcast_note = self.build_broadcast_note(is_pitcher, cb, cp, yb, yp)
        quick = [self.data_freshness_text, "IMPORTED DATA - VERIFY BEFORE AIR"]
        if warning:
            quick.extend(["", "AVAILABILITY WARNING", warning])
        quick.extend(["", "BROADCAST NOTE", self.current_broadcast_note, "", CAREER_LABEL.upper(), self.pitching_line(cp) if is_pitcher else self.batting_line(cb), "", f"{CURRENT_YEAR} SEASON", self.pitching_line(yp) if is_pitcher else self.batting_line(yb)])
        if is_pitcher and cb is not None:
            quick.extend(["", "AT THE PLATE", self.batting_line(cb)])
        self._set_text(self.stat_texts["card"], "\n".join(quick))
        self._set_text(self.stat_texts["storylines"], "\n".join(self._storyline_lines(roster, is_pitcher)))
        self._set_text(self.stat_texts["splits"], "\n".join([f"OFFICIAL AUSL SPLITS - {CURRENT_YEAR}", self.data_freshness_text, "", *self._best_split_lines(roster["player_id"], is_pitcher)]))
        source_lines = [
            "SOURCE LINKS / PRODUCER VERIFICATION",
            self.data_freshness_text,
            "",
            "Primary stat source: Official AUSL JSON/API and local Excel exports.",
            "Official split source: https://theausl.com/stats/",
            "Official standings source: https://theausl.com/standings/",
            "Official schedule source: https://theausl.com/schedule/",
            "Media guide source: https://theausl.com/wp-content/uploads/2026/06/2026-AUSL-Media-Guide.pdf",
            "",
            "Manual producer notes:",
            *(self._manual_note_lines(player_id=roster["player_id"], team_code=value(roster, "team_code", "")) or ["- No manual notes entered yet."]),
        ]
        self._set_text(self.stat_texts["sources"], "\n".join(source_lines))
        self._set_text(self.stat_texts["career"], CAREER_LABEL.upper() + "\n\nBATTING\n" + self.batting_line(cb) + "\n\nPITCHING\n" + self.pitching_line(cp) + "\n\nFIELDING\n" + self.fielding_line(cf))
        self._set_text(self.stat_texts["current"], "BATTING\n" + self.batting_line(yb) + "\n\nPITCHING\n" + self.pitching_line(yp) + "\n\nFIELDING\n" + self.fielding_line(yf))
        self._set_text(self.stat_texts["previous"], "BATTING\n" + self.batting_line(pb) + "\n\nPITCHING\n" + self.pitching_line(pp) + "\n\nFIELDING\n" + self.fielding_line(pf))
        self._set_text(self.stat_texts["fielding"], CAREER_LABEL.upper() + "\n" + self.fielding_line(cf) + f"\n\n{CURRENT_YEAR}\n" + self.fielding_line(yf) + f"\n\n{CURRENT_YEAR-1}\n" + self.fielding_line(pf))

    def gfx_text(self, kind):
        if self.selected_player_id is None:
            return ""
        roster_rows = self.db["roster"][pd.to_numeric(self.db["roster"]["player_id"], errors="coerce").eq(self.selected_player_id)]
        if roster_rows.empty:
            return ""
        roster = roster_rows.iloc[0]
        number = str(value(roster, "jersey_number", "")).replace(".0", "")
        is_pitcher = "P" in str(roster["position"]).upper()
        stat = self.pitching_line(self.stat_row("career_pitching")) if is_pitcher else self.batting_line(self.stat_row("career_batting"))
        name = roster["player_name"].upper()
        if kind == "lower": return f"{name}\n{stat}"
        if kind == "full": return f"{name} — {CAREER_LABEL.upper()}\n{stat}\n\n{self.data_freshness_text}"
        if kind == "note": return f"{roster['player_name']}: {self.current_broadcast_note}\n\n{self.data_freshness_text}"
        return f"#{number} — {name} — {roster['position']} — {roster['team'].upper()}"

    @staticmethod
    def _stat_number(row, key):
        if row is None or key not in row or pd.isna(row[key]):
            return 0.0
        try:
            return float(row[key])
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def compact_draft_info(roster):
        """Keep useful draft context in the bio line without making it the note."""
        comments = str(value(roster, "status_comments", "")).strip()
        transaction = str(value(roster, "transaction_type", "")).strip()
        draft_terms = ("draft", "round", "pick", "protect")
        if comments and comments != "—" and any(term in comments.lower() for term in draft_terms):
            return comments.replace("[", "").replace("]", "")
        if transaction and transaction != "—" and any(term in transaction.lower() for term in draft_terms):
            return transaction
        return ""

    def build_broadcast_note(self, is_pitcher, career_batting, career_pitching, season_batting, season_pitching):
        """Create a concise milestone-first note for the selected player."""
        if is_pitcher:
            career = career_pitching
            milestones = [
                ("strikeOuts", 25, 7, "career strikeouts"),
                ("wins", 5, 2, "career wins"),
                ("appearances", 25, 5, "career appearances"),
            ]
        else:
            career = career_batting
            milestones = [
                ("hits", 25, 7, "career hits"),
                ("homeRuns", 5, 2, "career home runs"),
                ("runsBattedIn", 25, 7, "career RBI"),
                ("stolenBases", 10, 2, "career stolen bases"),
                ("gamesPlayed", 25, 5, "career games"),
            ]

        candidates = []
        for priority, (key, interval, max_gap, label) in enumerate(milestones):
            current = int(self._stat_number(career, key))
            if current <= 0:
                continue
            target = ((current // interval) + 1) * interval
            gap = target - current
            if gap <= max_gap:
                candidates.append((gap / interval, priority, gap, target, label))
        if candidates:
            _ratio, _priority, gap, target, label = min(candidates)
            verb = "Needs" if gap != 1 else "Needs just"
            unit = "more" if gap != 1 else "more"
            return f"{verb} {gap} {unit} to reach {target} AUSL {label}; this is a clean milestone to track if she appears."

        if is_pitcher and season_pitching is not None:
            strikeouts = int(self._stat_number(season_pitching, "strikeOuts"))
            innings = value(season_pitching, "inningsPitched")
            era = decimal(season_pitching, "earnedRunAverage", 2)
            return f"Has {strikeouts} strikeouts over {innings} innings with a {era} ERA this season, giving the booth a current-form pitching note."
        if season_batting is not None:
            hits = int(self._stat_number(season_batting, "hits"))
            home_runs = int(self._stat_number(season_batting, "homeRuns"))
            rbi = int(self._stat_number(season_batting, "runsBattedIn"))
            return f"Has {hits} hits, {home_runs} home runs and {rbi} RBI this season, making her an easy hitter spotlight if she comes up in a key spot."
        return "No milestone note is available yet; check the live game tab for an in-game angle."

    def copy_gfx(self, kind):
        text = self.gfx_text(kind)
        if not text:
            self.copy_status.set("Select a player first.")
            return
        self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
        self.copy_status.set("Copied to clipboard")

    def refresh_live(self):
        game_id = self.game_id_var.get().strip()
        self.live_status.set("Refreshing official game data...")
        def work():
            try:
                game, box = fetch_live_game(game_id)
                self.root.after(0, lambda: self._finish_live(game, box))
            except Exception as exc:
                self.root.after(0, lambda: self.live_status.set(f"Live feed error: {exc}"))
        threading.Thread(target=work, daemon=True).start()

    def _finish_live(self, game, box):
        self.live_game, self.live_box = game, box
        self.live_status.set(f"Loaded game {game.get('gameId')} — {game.get('recordStatus','Unknown status')}")
        self._set_text(self.live_summary, self.format_live_comparison())
        self.search_live_player()
        self._schedule_live()

    def _schedule_live(self):
        if self.auto_var.get() and self.game_id_var.get().strip():
            self.root.after(30000, self._auto_refresh)

    def _auto_refresh(self):
        if self.auto_var.get():
            self.refresh_live()

    def format_live_comparison(self):
        if not self.live_box:
            return "No live box score loaded."
        competitors = self.live_box.get("competitors") or []
        lines = self.live_box.get("lineScores") or []
        title = " vs ".join(c.get("name", "Team") for c in competitors)
        output = [title.upper(), "LIVE / OFFICIAL FEED — VERIFY BEFORE AIR", ""]
        team_by_id = {c.get("eventTeamId"): c for c in competitors}
        if lines:
            for line in lines:
                team = team_by_id.get(line.get("eventTeamId"), {})
                name = line.get("abbreviation") or line.get("teamName") or line.get("name") or team.get("abbreviation") or team.get("name") or str(line.get("eventTeamId", "TEAM"))
                runs = line.get("runs", line.get("score", "—")); hits = line.get("hits", "—"); errors = line.get("errors", "—")
                innings = line.get("innings") or line.get("lineScore") or []
                if isinstance(innings, list): innings = " ".join(str(x.get("runs", x)) if isinstance(x, dict) else str(x) for x in innings)
                output.append(f"{name:<12} R {runs}   H {hits}   E {errors}   | {innings}")
        else:
            output.append("The official box score is available, but play has not started or no line score has been posted.")
        for section in ("batting", "pitching"):
            count = sum(1 for category, _row in self._flatten_live_players() if category == section)
            output.append(f"\n{section.title()} player lines available: {count}")
        return "\n".join(output)

    def _flatten_live_players(self):
        if not self.live_box:
            return []
        found = []
        for category in ("batting", "pitching"):
            section = self.live_box.get(category) or {}
            groups = section.values() if isinstance(section, dict) else [section]
            for group in groups:
                if isinstance(group, dict):
                    group = group.get("players") or group.get("stats") or [group]
                if isinstance(group, list):
                    for row in group:
                        if isinstance(row, dict): found.append((category, row))
        return found

    def search_live_player(self):
        query = self.live_search_var.get().strip().lower()
        if not self.live_box:
            self._set_text(self.live_player, "Load a live game first.")
            return
        matches = []
        for category, row in self._flatten_live_players():
            name = row.get("playerName") or row.get("displayName") or row.get("statName") or row.get("name") or f"{row.get('firstName','')} {row.get('lastName','')}".strip()
            if not query or query in name.lower():
                matches.append((category, name, row))
        if not matches:
            self.live_player_copy_text = ""
            self._set_text(self.live_player, "No matching player line is currently in the official box score.")
            return
        team_by_id = {c.get("eventTeamId"): c for c in self.live_box.get("competitors", [])}
        chunks = []
        for category, name, row in matches[:20]:
            team = team_by_id.get(row.get("eventTeamId"), {})
            heading = f"{name.upper()} — {team.get('abbreviation', '')} {row.get('position', '')}".strip()
            if category == "batting":
                stat_line = " | ".join(
                    f"{label} {row.get(key, '—')}"
                    for label, key in [("AB", "ab"), ("R", "r"), ("H", "h"), ("RBI", "rbi"), ("BB", "bb"), ("SO", "so"), ("LOB", "lob")]
                )
            else:
                stat_line = " | ".join(
                    f"{label} {row.get(key, '—')}"
                    for label, key in [("IP", "ip"), ("H", "h"), ("R", "r"), ("ER", "er"), ("BB", "bb"), ("SO", "so"), ("HR", "hr"), ("NP", "np")]
                )
                if row.get("decision"):
                    stat_line += f" | {row['decision']} {row.get('record', '')}".rstrip()
            action_note = self.live_action_note(category, row)
            chunks.append(f"{heading}\n{stat_line}\nACTION NOTE: {action_note}")
        self.live_player_copy_text = "\n\n".join(chunks)
        self._set_text(self.live_player, self.live_player_copy_text)

    def copy_live_player(self):
        if not self.live_player_copy_text:
            self.live_status.set("Search for a live player first")
            return
        self.root.clipboard_clear(); self.root.clipboard_append(self.live_player_copy_text); self.root.update()
        self.live_status.set("Live player line copied to clipboard")

    @staticmethod
    def live_action_note(category, row):
        """Turn a box-score row into an action-focused broadcast angle."""
        def stat(key):
            try:
                return float(row.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0.0

        notes = []
        if category == "batting":
            hits, rbi, runs, home_runs = int(stat("h")), int(stat("rbi")), int(stat("r")), int(stat("hr"))
            if home_runs:
                notes.append(f"has homered{' twice' if home_runs == 2 else f' {home_runs} times' if home_runs > 2 else ''}")
            if hits >= 2:
                notes.append(f"has a {hits}-hit game")
            if rbi >= 2:
                notes.append(f"has driven in {rbi} runs")
            if runs >= 2:
                notes.append(f"has scored {runs} runs")
            if not notes:
                notes.append("game line is ready for the next plate appearance")
        else:
            innings, strikeouts, earned_runs = row.get("ip", "0.0"), int(stat("so")), int(stat("er"))
            if stat("ip") >= 3 and earned_runs == 0:
                notes.append(f"has worked {innings} scoreless innings")
            if strikeouts >= 4:
                notes.append(f"has struck out {strikeouts}")
            if row.get("decision"):
                notes.append(f"is credited with the {str(row['decision']).lower()}")
            if not notes:
                notes.append(f"has allowed {earned_runs} earned run{'s' if earned_runs != 1 else ''} over {innings} innings")
        sentence = "; ".join(notes)
        return sentence[:1].upper() + sentence[1:] + "."

    def copy_live_comparison(self):
        text = self.format_live_comparison()
        self.root.clipboard_clear(); self.root.clipboard_append(text); self.root.update()
        self.live_status.set("Game comparison copied to clipboard")

    @staticmethod
    def _set_text(widget, content):
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")


def main():
    root = tk.Tk()
    AUSLStatsApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
