import nflreadpy as nfl
import inspect

print("Testing nflreadpy load_player_stats")
print()

print("Function signature:")
print(inspect.signature(nfl.load_player_stats))
print()

print("Downloading player stats...")

try:
    stats = nfl.load_player_stats(seasons=[2024])
except TypeError:
    try:
        stats = nfl.load_player_stats([2024])
    except TypeError:
        stats = nfl.load_player_stats()

print("Download complete")
print()

# Some nflreadpy data may come back as a lazy table.
# If so, collect it into a real table.
if hasattr(stats, "collect"):
    stats = stats.collect()

print("Type:")
print(type(stats))
print()

print("Shape:")
print(stats.shape)
print()

print("Columns:")
for col in stats.columns:
    print("-", col)

print()
print("First 5 rows:")
print(stats.head())