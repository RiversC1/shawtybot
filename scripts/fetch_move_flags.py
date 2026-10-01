"""Adds the move flags some abilities need (contact, punch, sound, bite,
pulse, slicing) to every move in data/pokemon.json, from Pokémon Showdown's
move data. PokeAPI doesn't expose these. Safe to re-run.

Abilities that use them: Static / Flame Body / Poison Point / Rough Skin /
Effect Spore / Tough Claws / Poison Touch / Pickpocket / Aftermath (contact),
Iron Fist (punch), Soundproof (sound), Strong Jaw (bite), Mega Launcher
(pulse), Sharpness (slicing)."""
import json
import os
import re
import urllib.request

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")
SOURCE = "https://play.pokemonshowdown.com/data/moves.json"
WANTED = ("contact", "punch", "sound", "bite", "pulse", "slicing")


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


req = urllib.request.Request(SOURCE, headers={"User-Agent": "Mozilla/5.0"})
showdown = json.loads(urllib.request.urlopen(req, timeout=60).read())
flags_by_move = {key: {f for f in WANTED if data.get("flags", {}).get(f)} for key, data in showdown.items()}

with open(PATH, encoding="utf-8") as f:
    dex = json.load(f)

missing, tagged = set(), 0
for mon in dex:
    for move in mon.get("moves", []):
        extra = flags_by_move.get(norm(move["name"]))
        if extra is None:
            missing.add(move["name"])
            continue
        flags = [f for f in move.get("flags", []) if f not in WANTED] + sorted(extra)
        if extra:
            tagged += 1
        move["flags"] = flags

with open(PATH, "w", encoding="utf-8") as f:
    json.dump(dex, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"tagged {tagged} move entries; not in Showdown data: {sorted(missing) or 'none'}")
