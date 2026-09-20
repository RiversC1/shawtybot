"""
One-off script to build a local Gen 1-4 Pokemon dataset from PokeAPI.
Run manually: python scripts/fetch_pokemon_data.py
Writes data/pokemon.json — the bot reads this at runtime, no live API calls needed.
"""
import asyncio
import aiohttp
import json
import os

GEN_1_TO_4_MAX_ID = 493  # National dex: Bulbasaur (1) to Arceus (493)
CONCURRENCY = 15
OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")


async def fetch_one(session: aiohttp.ClientSession, sem: asyncio.Semaphore, dex_id: int) -> dict:
    async with sem:
        async with session.get(f"https://pokeapi.co/api/v2/pokemon/{dex_id}") as resp:
            mon = await resp.json()
        async with session.get(f"https://pokeapi.co/api/v2/pokemon-species/{dex_id}") as resp:
            species = await resp.json()

    types = [t["type"]["name"] for t in sorted(mon["types"], key=lambda t: t["slot"])]
    genus = next(
        (g["genus"] for g in species["genera"] if g["language"]["name"] == "en"),
        "Unknown Pokémon",
    )
    artwork = mon["sprites"]["other"]["official-artwork"]["front_default"]
    sprite = mon["sprites"]["front_default"]

    return {
        "id": dex_id,
        "name": mon["name"].capitalize(),
        "types": types,
        "category": genus,
        "artwork": artwork,
        "sprite": sprite,
    }


async def main():
    sem = asyncio.Semaphore(CONCURRENCY)
    results = [None] * GEN_1_TO_4_MAX_ID

    async with aiohttp.ClientSession() as session:
        async def worker(dex_id: int):
            for attempt in range(3):
                try:
                    results[dex_id - 1] = await fetch_one(session, sem, dex_id)
                    if dex_id % 25 == 0:
                        print(f"Fetched {dex_id}/{GEN_1_TO_4_MAX_ID}")
                    return
                except Exception as e:
                    print(f"Retry {dex_id} ({attempt+1}/3): {e}")
                    await asyncio.sleep(1)
            print(f"FAILED: {dex_id}")

        await asyncio.gather(*(worker(i) for i in range(1, GEN_1_TO_4_MAX_ID + 1)))

    missing = [i + 1 for i, r in enumerate(results) if r is None]
    if missing:
        print(f"WARNING: missing {len(missing)} entries: {missing}")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump([r for r in results if r], f, ensure_ascii=False, indent=2)

    print(f"Wrote {len([r for r in results if r])} entries to {OUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
