"""
One-off script to add abilities + shiny sprite URLs to data/pokemon.json.
Run manually: python scripts/enrich_pokemon_extra.py
"""
import asyncio
import aiohttp
import json
import os

CONCURRENCY = 15
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")


async def fetch_extra(session: aiohttp.ClientSession, sem: asyncio.Semaphore, dex_id: int) -> dict:
    async with sem:
        for attempt in range(3):
            try:
                async with session.get(f"https://pokeapi.co/api/v2/pokemon/{dex_id}") as resp:
                    mon = await resp.json()
                abilities = [
                    {"name": a["ability"]["name"], "is_hidden": a["is_hidden"]}
                    for a in sorted(mon["abilities"], key=lambda a: a["slot"])
                ]
                return {
                    "id": dex_id,
                    "abilities": abilities,
                    "artwork_shiny": mon["sprites"]["other"]["official-artwork"].get("front_shiny"),
                    "sprite_shiny": mon["sprites"].get("front_shiny"),
                }
            except Exception as e:
                print(f"Retry {dex_id} ({attempt+1}/3): {e}")
                await asyncio.sleep(1)
        print(f"FAILED: {dex_id}")
        return {"id": dex_id, "abilities": [], "artwork_shiny": None, "sprite_shiny": None}


async def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        pokemon = json.load(f)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(
            *(fetch_extra(session, sem, p["id"]) for p in pokemon)
        )

    extra_by_id = {r["id"]: r for r in results}
    for p in pokemon:
        r = extra_by_id[p["id"]]
        p["abilities"] = r["abilities"]
        p["artwork_shiny"] = r["artwork_shiny"]
        p["sprite_shiny"] = r["sprite_shiny"]

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(pokemon, f, ensure_ascii=False, indent=2)

    print(f"Done. Enriched {len(pokemon)} entries with abilities + shiny sprites.")


if __name__ == "__main__":
    asyncio.run(main())
