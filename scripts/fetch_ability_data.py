"""Writes data/abilities.json: {ability_key: description} for every ability
that appears in data/pokemon.json, from PokeAPI's English short effect (with
its "$effect_chance" placeholder filled in). Shown as tooltips when picking
an ability on the team page. Idempotent; re-fetches everything."""
import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.join(os.path.dirname(__file__), "..", "data")


def fetch(name: str) -> tuple[str, str | None]:
    req = urllib.request.Request(f"https://pokeapi.co/api/v2/ability/{name}", headers={"User-Agent": "shawtybot"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
    except Exception as e:  # noqa: BLE001 - report and skip
        print(f"  {name}: {e}")
        return name, None
    text = next((e["short_effect"] for e in d.get("effect_entries", []) if e["language"]["name"] == "en"), None)
    if not text:
        # Newer abilities only have in-game flavor text.
        text = next((e["flavor_text"] for e in reversed(d.get("flavor_text_entries", []))
                     if e["language"]["name"] == "en"), None)
    if text:
        chance = d.get("effect_chance")
        text = text.replace("$effect_chance", str(chance) if chance is not None else "some")
        text = re.sub(r"\s+", " ", text).strip()
        # PokeAPI's text has U+FFFD in place of some characters.
        text = text.replace("Pok\ufffdmon", "Pok\u00e9mon").replace("\ufffd", "'")
    return name, OVERRIDES.get(name, text)


# Where this game's battles differ from PokeAPI's (Gen 3-4) wording: weather
# from an ability lasts 5 turns, as in modern games.
OVERRIDES = {
    "drought": "Summons harsh sunlight for 5 turns upon entering battle.",
    "drizzle": "Summons rain for 5 turns upon entering battle.",
    "sand-stream": "Summons a sandstorm for 5 turns upon entering battle.",
    "snow-warning": "Summons hail for 5 turns upon entering battle.",
}


with open(os.path.join(ROOT, "pokemon.json"), encoding="utf-8") as f:
    names = sorted({a["name"] for m in json.load(f) for a in m.get("abilities", [])})
print(f"fetching {len(names)} abilities...")
with ThreadPoolExecutor(max_workers=12) as pool:
    results = dict(pool.map(fetch, names))

out = {k: v for k, v in sorted(results.items()) if v}
with open(os.path.join(ROOT, "abilities.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(f"wrote {len(out)} descriptions; missing: {[k for k, v in results.items() if not v] or 'none'}")
