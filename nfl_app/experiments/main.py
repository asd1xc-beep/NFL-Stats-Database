# PARKED: CLI entry point for the abandoned PFR scraper below — see scraper.py.
# Not an active code path; the app's entry point is src/nfl_stats_app.py.

from scraper import download_player_page

player = input("Enter player name: ")

download_player_page(player)