"""
One-off script: adds the non-legendary Mega Evolutions of Gen 1-4 Pokémon to
data/pokemon.json, from PokeAPI. Run manually: python scripts/fetch_mega_data.py

Megas are the Poké League completionist's pick-one reward (see
battle_store.MEGA_REWARD_DEX_IDS). They reuse PokeAPI's form ids (10000+),
so they never collide with the 1-493 national dex, and carry
"is_mega": true + "mega_of": <base dex id> so the game can keep them out of
wild spawns, the Pokédex and completion totals. Each Mega keeps its base
species' move pool, family and category; stats, types, ability and artwork
are the Mega form's own. Re-running replaces existing Mega entries.
"""
import json
import os
import urllib.request

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")

# Gen 1-4 species with a Mega Evolution, excluding legendaries (Mewtwo,
# Latias, Latios, Rayquaza) by design.
MEGA_FORMS = [
    "venusaur-mega", "charizard-mega-x", "charizard-mega-y", "blastoise-mega", "beedrill-mega",
    "pidgeot-mega", "alakazam-mega", "slowbro-mega", "gengar-mega", "kangaskhan-mega", "pinsir-mega",
    "gyarados-mega", "aerodactyl-mega", "ampharos-mega", "steelix-mega", "scizor-mega", "heracross-mega",
    "houndoom-mega", "tyranitar-mega", "sceptile-mega", "blaziken-mega", "swampert-mega", "gardevoir-mega",
    "sableye-mega", "mawile-mega", "aggron-mega", "medicham-mega", "manectric-mega", "sharpedo-mega",
    "camerupt-mega", "altaria-mega", "banette-mega", "absol-mega", "glalie-mega", "salamence-mega",
    "metagross-mega", "lopunny-mega", "garchomp-mega", "lucario-mega", "abomasnow-mega", "gallade-mega",
]

STAT_KEYS = {"hp": "hp", "attack": "attack", "defense": "defense", "special-attack": "sp_attack",
             "special-defense": "sp_defense", "speed": "speed"}


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "shawtybot-data-script"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def main():
    with open(OUT_PATH, encoding="utf-8") as f:
        dex = json.load(f)
    by_id = {m["id"]: m for m in dex}
    dex = [m for m in dex if not m.get("is_mega")]

    for form in MEGA_FORMS:
        mon = get(f"https://pokeapi.co/api/v2/pokemon/{form}")
        species = get(mon["species"]["url"])
        base = by_id[species["id"]]
        suffix = form.split("-mega")[1].replace("-", " ").upper().strip()
        name = f"Mega {base['name']}" + (f" {suffix}" if suffix else "")
        art = mon["sprites"]["other"]["official-artwork"]
        dex.append({
            "id": mon["id"],
            "name": name,
            "types": [t["type"]["name"] for t in sorted(mon["types"], key=lambda t: t["slot"])],
            "category": base["category"],
            "artwork": art["front_default"],
            "sprite": mon["sprites"]["front_default"] or art["front_default"],
            "is_legendary": False,
            "is_mythical": False,
            "abilities": [{"name": a["ability"]["name"], "is_hidden": False} for a in mon["abilities"]],
            "artwork_shiny": art.get("front_shiny") or art["front_default"],
            "sprite_shiny": mon["sprites"]["front_shiny"] or art.get("front_shiny") or art["front_default"],
            "evolves_to": [],
            "family_id": base["family_id"],
            "base_stats": {STAT_KEYS[s["stat"]["name"]]: s["base_stat"] for s in mon["stats"]},
            "moves": base["moves"],
            "is_mega": True,
            "mega_of": base["id"],
        })
        print(f"{mon['id']:>6} {name}")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dex, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"Wrote {len(MEGA_FORMS)} Mega entries.")


if __name__ == "__main__":
    main()
