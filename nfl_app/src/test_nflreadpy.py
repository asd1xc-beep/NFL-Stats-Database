import nflreadpy as nfl

print("nflreadpy imported successfully")
print()

print("Available nflreadpy functions:")
for item in dir(nfl):
    if not item.startswith("_"):
        print("-", item)