"""Adds each Pokémon's official height (metres) to data/pokemon.json as
"height", from PokeAPI. The battle scene sizes sprites by it so a Weavile
(1.1 m) doesn't tower over a Charizard (1.7 m). Megas and Formes use their
own PokeAPI entry (same ids). The placeholder reward Pokémon (#494/#495)
have no PokeAPI entry and keep no height (the client falls back to 1 m).
Idempotent; only fetches entries missing a height."""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")
PLACEHOLDERS = {494, 495}


def fetch_height(dex_id: int) -> float | None:
    req = urllib.request.Request(f"https://pokeapi.co/api/v2/pokemon/{dex_id}", headers={"User-Agent": "shawtybot"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["height"] / 10  # decimetres -> metres
    except Exception as e:  # noqa: BLE001 - report and skip
        print(f"  #{dex_id}: {e}")
        return None


with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

todo = [m for m in data if "height" not in m and m["id"] not in PLACEHOLDERS]
print(f"fetching {len(todo)} heights...")
with ThreadPoolExecutor(max_workers=12) as pool:
    heights = dict(zip((m["id"] for m in todo), pool.map(fetch_height, (m["id"] for m in todo))))

for m in data:
    h = heights.get(m["id"])
    if h is not None:
        m["height"] = h

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
missing = [m["id"] for m in data if "height" not in m and m["id"] not in PLACEHOLDERS]
print("done; missing:", missing or "none")
