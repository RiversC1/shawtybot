"""
One-time offline data enrichment: fetches base stats and a curated move pool
for every species in data/pokemon.json from PokeAPI, and writes the result
back into the same file.

This is NOT run at bot/webapi runtime — it's a build-time script, run once
(and re-run only if the species list changes), so neither the bot nor the
web app carry a runtime dependency on PokeAPI's availability.

Run with: python scripts/enrich_pokemon_data.py
"""
import asyncio
import json
import os

import aiohttp

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")
CONCURRENCY = 12
MIN_MOVES = 4
MAX_MOVES = 8

STAT_KEY_MAP = {
    "hp": "hp",
    "attack": "attack",
    "defense": "defense",
    "special-attack": "sp_attack",
    "special-defense": "sp_defense",
    "speed": "speed",
}


async def fetch_json(session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        for attempt in range(3):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return await resp.json()
            except Exception:
                pass
            await asyncio.sleep(1 + attempt)
        raise RuntimeError(f"Failed to fetch {url}")


def pick_moves(pokemon_json: dict) -> list[tuple[str, int]]:
    """Returns [(move_name, level_learned_at)], deduped, best level-up moves first."""
    best_level = {}
    for entry in pokemon_json["moves"]:
        name = entry["move"]["name"]
        for vgd in entry["version_group_details"]:
            if vgd["move_learn_method"]["name"] != "level-up":
                continue
            level = vgd["level_learned_at"]
            if name not in best_level or level > best_level[name]:
                best_level[name] = level

    ranked = sorted(best_level.items(), key=lambda kv: kv[1], reverse=True)
    if len(ranked) >= MIN_MOVES:
        return ranked[:MAX_MOVES]

    # Too few level-up moves (some basic/baby species) — pad with any other
    # learn method (TM/tutor/egg) so every species has a usable move pool.
    seen = set(best_level.keys())
    extras = []
    for entry in pokemon_json["moves"]:
        name = entry["move"]["name"]
        if name in seen:
            continue
        seen.add(name)
        extras.append((name, 0))
    return (ranked + extras)[:MAX_MOVES]


async def enrich_one(session, sem, species, move_cache, move_cache_lock):
    dex_id = species["id"]
    pokemon_json = await fetch_json(session, f"https://pokeapi.co/api/v2/pokemon/{dex_id}", sem)

    base_stats = {}
    for stat in pokemon_json["stats"]:
        key = STAT_KEY_MAP.get(stat["stat"]["name"])
        if key:
            base_stats[key] = stat["base_stat"]
    species["base_stats"] = base_stats

    move_picks = pick_moves(pokemon_json)
    moves_out = []
    for name, _level in move_picks:
        async with move_cache_lock:
            cached = move_cache.get(name)
        if cached is None:
            move_json = await fetch_json(session, f"https://pokeapi.co/api/v2/move/{name}", sem)
            short_effect = next(
                (e["short_effect"] for e in move_json["effect_entries"] if e["language"]["name"] == "en"), ""
            )
            effect_chance = move_json.get("effect_chance")
            description = f"{short_effect} ({effect_chance}%)" if short_effect and effect_chance else short_effect
            cached = {
                "name": name.replace("-", " ").title(),
                "type": move_json["type"]["name"],
                "category": move_json["damage_class"]["name"] if move_json["damage_class"] else "status",
                "pp": move_json.get("pp"),
                "power": move_json.get("power"),
                "accuracy": move_json.get("accuracy"),
                "description": description,
            }
            async with move_cache_lock:
                move_cache[name] = cached
        moves_out.append(cached)
    species["moves"] = moves_out

    return dex_id


async def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        species_list = json.load(f)

    sem = asyncio.Semaphore(CONCURRENCY)
    move_cache: dict[str, dict] = {}
    move_cache_lock = asyncio.Lock()

    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as session:
        done = 0
        tasks = [
            enrich_one(session, sem, species, move_cache, move_cache_lock)
            for species in species_list
        ]
        for coro in asyncio.as_completed(tasks):
            dex_id = await coro
            done += 1
            if done % 25 == 0 or done == len(species_list):
                print(f"  {done}/{len(species_list)} species done (last: #{dex_id})", flush=True)

    species_list.sort(key=lambda p: p["id"])
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(species_list, f, ensure_ascii=False, indent=2)

    print(f"Done. {len(move_cache)} unique moves cached.")


if __name__ == "__main__":
    asyncio.run(main())
