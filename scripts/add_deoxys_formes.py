"""Adds Deoxys' Attack/Defense/Speed Formes to data/pokemon.json as their own
entries (PokeAPI form ids 10001-10003), flagged form_of=386. Nobody owns or
catches these directly: a trainer owns #386 and picks which Forme it battles
in with a Meteorite (see battle_store's alternate-forme section). Stats are
the games' official Forme base stats. Idempotent."""
import json
import os

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")
ART = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon"

FORMES = [
    (10001, "attack", "Attack Forme", {"hp": 50, "attack": 180, "defense": 20, "sp_attack": 180, "sp_defense": 20, "speed": 150}),
    (10002, "defense", "Defense Forme", {"hp": 50, "attack": 70, "defense": 160, "sp_attack": 70, "sp_defense": 160, "speed": 90}),
    (10003, "speed", "Speed Forme", {"hp": 50, "attack": 95, "defense": 90, "sp_attack": 95, "sp_defense": 90, "speed": 180}),
]

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
data = [m for m in data if m.get("form_of") != 386]
base = next(m for m in data if m["id"] == 386)
for dex_id, key, label, stats in FORMES:
    data.append({
        **base,
        "id": dex_id,
        "name": f"Deoxys-{key.title()}",
        "artwork": f"{ART}/other/official-artwork/{dex_id}.png",
        "sprite": f"{ART}/{dex_id}.png",
        "artwork_shiny": f"{ART}/other/official-artwork/shiny/{dex_id}.png",
        "sprite_shiny": f"{ART}/shiny/{dex_id}.png",
        "evolves_to": [],
        "base_stats": stats,
        "form_of": 386,
        "form_key": key,
        "form_label": label,
    })

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print("added", len(FORMES), "Deoxys Formes")
