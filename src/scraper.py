import requests
import os


def download_player_page(player_name):

    print(f"\nSearching for {player_name}...")

    search_name = player_name.lower().replace(" ", "-")

    search_url = (
        f"https://www.pro-football-reference.com/search/search.fcgi?search={search_name}"
    )

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    response = requests.get(search_url, headers=headers)

    print(f"Status Code: {response.status_code}")

    os.makedirs("../cache", exist_ok=True)

    with open("../cache/search_result.html", "w", encoding="utf-8") as f:
        f.write(response.text)

    print("Search page saved to cache/search_result.html")