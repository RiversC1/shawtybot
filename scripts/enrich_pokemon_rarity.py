"""
One-off script to add is_legendary/is_mythical flags to the existing data/pokemon.json.
Run manually: python scripts/enrich_pokemon_rarity.py
"""
import asyncio
import aiohttp
import json
import os

CONCURRENCY = 15
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")


async def fetch_rarity(session: aiohttp.ClientSession, sem: asyncio.Semaphore, dex_id: int) -> dict:
    async with sem:
        for attempt in range(3):
            try:
                async with session.get(f"https://pokeapi.co/api/v2/pokemon-species/{dex_id}") as resp:
                    species = await resp.json()
                return {
                    "id": dex_id,
                    "is_legendary": species["is_legendary"],
                    "is_mythical": species["is_mythical"],
                }
            except Exception as e:
                print(f"Retry {dex_id} ({attempt+1}/3): {e}")
                await asyncio.sleep(1)
        print(f"FAILED: {dex_id}")
        return {"id": dex_id, "is_legendary": False, "is_mythical": False}


async def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        pokemon = json.load(f)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(fetch_rarity(session, sem, p["id"]) for p in pokemon)
        )

    rarity_by_id = {r["id"]: r for r in results}
    for p in pokemon:
        r = rarity_by_id[p["id"]]
        p["is_legendary"] = r["is_legendary"]
        p["is_mythical"] = r["is_mythical"]

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(pokemon, f, ensure_ascii=False, indent=2)

    legendary_count = sum(1 for p in pokemon if p["is_legendary"] or p["is_mythical"])
    print(f"Done. {legendary_count} legendary/mythical Pokémon flagged.")


if __name__ == "__main__":
    asyncio.run(main())
