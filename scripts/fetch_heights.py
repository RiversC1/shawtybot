"""Adds each Pokémon's official height (metres) and weight (kg) to
data/pokemon.json as "height" / "weight", from PokeAPI. The battle scene
sizes sprites by height so a Weavile (1.1 m) doesn't tower over a Charizard
(1.7 m); weight drives Low Kick and Heavy Slam. Megas and Formes use their
own PokeAPI entry (same ids). The placeholder reward Pokémon (#494/#495)
have no PokeAPI entry and keep no height (the client falls back to 1 m).
Idempotent; only fetches entries missing either value."""
import json
import os
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")
PLACEHOLDERS = {494, 495}


def fetch_height(dex_id: int) -> tuple[float, float] | None:
    req = urllib.request.Request(f"https://pokeapi.co/api/v2/pokemon/{dex_id}", headers={"User-Agent": "shawtybot"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
            return d["height"] / 10, d["weight"] / 10  # decimetres -> m, hectograms -> kg
    except Exception as e:  # noqa: BLE001 - report and skip
        print(f"  #{dex_id}: {e}")
        return None


with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

todo = [m for m in data if ("height" not in m or "weight" not in m) and m["id"] not in PLACEHOLDERS]
print(f"fetching {len(todo)} heights/weights...")
with ThreadPoolExecutor(max_workers=12) as pool:
    heights = dict(zip((m["id"] for m in todo), pool.map(fetch_height, (m["id"] for m in todo))))

for m in data:
    hw = heights.get(m["id"])
    if hw is not None:
        m["height"], m["weight"] = hw

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
missing = [m["id"] for m in data if "weight" not in m and m["id"] not in PLACEHOLDERS]
print("done; missing:", missing or "none")
