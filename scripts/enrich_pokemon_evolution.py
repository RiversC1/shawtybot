"""
One-off script to add evolution data to data/pokemon.json.
Adds, per species: "evolves_to" (list of {id, item}) and "family_id"
(the dex id of the root of its evolution chain, used for candy grouping).
Targets outside the gen 1-4 dataset (id > 493) are dropped.

Run manually: python scripts/enrich_pokemon_evolution.py
"""
import asyncio
import aiohttp
import json
import os

CONCURRENCY = 15
MAX_DEX_ID = 493
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")


def extract_id(url: str) -> int:
    return int(url.rstrip("/").split("/")[-1])


async def fetch_json(session: aiohttp.ClientSession, sem: asyncio.Semaphore, url: str) -> dict:
    async with sem:
        for attempt in range(3):
            try:
                async with session.get(url) as resp:
                    return await resp.json()
            except Exception as e:
                print(f"Retry {url} ({attempt+1}/3): {e}")
                await asyncio.sleep(1)
        print(f"FAILED: {url}")
        return {}


def pick_item(details: list) -> str | None:
    """Species can list multiple valid triggers for the same evolution edge
    (different game versions, trade, level-up, item, ...). Prefer any trigger
    that needs no item — only fall back to requiring one if that's the only way."""
    if any(not d.get("item") for d in details):
        return None
    for d in details:
        if d.get("item"):
            return d["item"]["name"]
    return None


def walk_chain(node: dict, family_id: int, edges: dict):
    species_id = extract_id(node["species"]["url"])
    edges.setdefault(species_id, [])  # ensure every node has an entry, even leaves
    for child in node.get("evolves_to", []):
        child_id = extract_id(child["species"]["url"])
        if child_id > MAX_DEX_ID:
            continue
        item = pick_item(child.get("evolution_details", []))
        edges[species_id].append({"id": child_id, "item": item})
        walk_chain(child, family_id, edges)


async def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        pokemon = json.load(f)

    sem = asyncio.Semaphore(CONCURRENCY)
    async with aiohttp.ClientSession() as session:
        species_list = await asyncio.gather(
            *(fetch_json(session, sem, f"https://pokeapi.co/api/v2/pokemon-species/{p['id']}") for p in pokemon)
        )

        chain_urls = sorted({s["evolution_chain"]["url"] for s in species_list if s.get("evolution_chain")})
        print(f"Fetching {len(chain_urls)} unique evolution chains...")
        chains = await asyncio.gather(*(fetch_json(session, sem, url) for url in chain_urls))

    evolves_to_by_id: dict[int, list[dict]] = {}
    family_by_id: dict[int, int] = {}

    for chain_data in chains:
        if not chain_data.get("chain"):
            continue
        root = chain_data["chain"]
        family_id = extract_id(root["species"]["url"])
        edges: dict[int, list[dict]] = {}
        walk_chain(root, family_id, edges)
        for species_id, targets in edges.items():
            evolves_to_by_id[species_id] = targets
            family_by_id[species_id] = family_id

    missing = 0
    for p in pokemon:
        dex_id = p["id"]
        p["evolves_to"] = evolves_to_by_id.get(dex_id, [])
        p["family_id"] = family_by_id.get(dex_id, dex_id)
        if dex_id not in family_by_id:
            missing += 1

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(pokemon, f, ensure_ascii=False, indent=2)

    evolvable = sum(1 for p in pokemon if p["evolves_to"])
    print(f"Done. {evolvable}/{len(pokemon)} species have a further evolution. Missing chain data: {missing}")


if __name__ == "__main__":
    asyncio.run(main())
