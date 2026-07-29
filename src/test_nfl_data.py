import nfl_data_py as nfl

print("Downloading 2023-2025 player stats...")

stats = nfl.import_weekly_data([2023, 2024, 2025])
rosters = nfl.import_seasonal_rosters([2025])

print(stats.head())
print(rosters.head())

print("Stats rows:", len(stats))
print("Roster rows:", len(rosters))