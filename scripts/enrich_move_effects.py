"""
One-time offline data enrichment (battle-system prep): backfills structured
mechanical effect data onto every move already embedded in data/pokemon.json,
so the battle engine (battle_engine.py) can dispatch on real fields instead of
parsing the free-text `description` at runtime.

Like scripts/enrich_pokemon_data.py, this is a build-time script only — not a
runtime dependency of the bot or web app. Only the ~488 distinct move names
already present in data/pokemon.json are fetched (cached per name), so this
is a quick one-time run (or re-run if the move list changes).

Adds to each move object:
  - ailment (str|null): one of "paralysis","poison","toxic","burn","sleep",
    "freeze","confusion","flinch" if the move causes one of the statuses the
    battle engine understands, else null. (PokeAPI ailments outside this set
    — trap, disable, yawn, leech-seed, etc. — are intentionally dropped; not
    modeled in v1.)
  - ailment_chance (int 0-100): normalized so 0-from-PokeAPI-on-a-status-move
    (meaning "always, it's the move's whole point") becomes 100.
  - stat_changes (list[{stat, change}]), stat_chance (int 0-100, same
    normalization as ailment_chance)
  - crit_rate (int 0-3): additional crit stages (e.g. Slash/Razor Leaf = 1)
  - drain_percent (int|null), recoil_percent (int|null): PokeAPI encodes both
    as a single signed `drain` field; split here into two unambiguous fields.
  - healing_percent (int|null): self-heal moves like Recover/Roost
  - flinch_chance (int 0-100)
  - min_hits/max_hits (int|null): multi-hit moves (Double Slap, etc.)
  - min_turns/max_turns (int|null): charge/recharge/multi-turn-lock moves
  - target (str): collapsed to "self" or "opponent" (singles battle format)
  - flags (list[str]): controlled vocabulary the engine dispatches on —
    charge, recharge, multi_turn_lock, multi_hit, drain, recoil, ohko,
    fixed_damage, always_hit

Run with: python scripts/enrich_move_effects.py
"""
import asyncio
import json
import os

import aiohttp

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")
CONCURRENCY = 12

# Moves whose PokeAPI slug can't be derived by "lowercase, spaces -> dashes".
SLUG_OVERRIDES = {
    "King's Shield": "kings-shield",
    "Land's Wrath": "lands-wrath",
    "Nature's Madness": "natures-madness",
    "Forest's Curse": "forests-curse",
    "Multi-Attack": "multi-attack",
}

# Ailments the battle engine actually models. Anything else PokeAPI reports
# (trap, disable, yawn, leech-seed, nightmare, torment, ...) is dropped.
AILMENT_MAP = {
    "paralysis": "paralysis",
    "poison": "poison",
    "burn": "burn",
    "sleep": "sleep",
    "freeze": "freeze",
    "confusion": "confusion",
    "flinch": "flinch",
}

# Moves PokeAPI's `meta`/`flags` don't map cleanly onto our vocabulary, or
# where we want to force a specific classification.
MOVE_FLAG_OVERRIDES: dict[str, list[str]] = {
    "Toxic": ["always_hit"],  # ailment override below turns its ailment into "toxic"
    "Fissure": ["ohko"],
    "Horn Drill": ["ohko"],
    "Guillotine": ["ohko"],
    "Sheer Cold": ["ohko"],
    "Sonic Boom": ["fixed_damage"],
    "Dragon Rage": ["fixed_damage"],
    "Night Shade": ["fixed_damage"],
    "Seismic Toss": ["fixed_damage"],
    "Hyper Beam": ["recharge"],
    "Giga Impact": ["recharge"],
    "Rock Wrecker": ["recharge"],
    "Roar Of Time": ["recharge"],
    "Frenzy Plant": ["recharge"],
    "Blast Burn": ["recharge"],
    "Hydro Cannon": ["recharge"],
    "Solar Beam": ["charge"],
    "Sky Attack": ["charge"],
    "Razor Wind": ["charge"],
    "Skull Bash": ["charge"],
    "Fly": ["charge"],
    "Dig": ["charge"],
    "Dive": ["charge"],
    "Bounce": ["charge"],
    "Freeze Shock": ["charge"],
    "Ice Burn": ["charge"],
    "Thrash": ["multi_turn_lock"],
    "Outrage": ["multi_turn_lock"],
    "Petal Dance": ["multi_turn_lock"],
    "Uproar": ["multi_turn_lock"],
}

# Fixed-damage moves the engine looks up directly rather than deriving from
# power (power is null for these in PokeAPI). Night Shade/Seismic Toss are
# handled specially by the engine (always exactly the target's level, and
# every battler in this bot is effectively level 100 -> flat 100) so they
# don't need an entry here.
MOVE_FIXED_DAMAGE_OVERRIDES: dict[str, int] = {
    "Sonic Boom": 20,
    "Dragon Rage": 40,
}

# Ailment overrides where PokeAPI's ailment slug doesn't distinguish a move
# we want to treat specially.
MOVE_AILMENT_OVERRIDES: dict[str, str] = {
    "Toxic": "toxic",
    "Toxic Spikes": "toxic",
}


def slugify(name: str) -> str:
    if name in SLUG_OVERRIDES:
        return SLUG_OVERRIDES[name]
    return name.lower().replace(" ", "-").replace("'", "")


async def fetch_json(session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore) -> dict | None:
    async with sem:
        for attempt in range(3):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 404:
                        return None
            except Exception:
                pass
            await asyncio.sleep(1 + attempt)
        return None


def normalize_chance(raw_chance: int, category_name: str, has_effect: bool) -> int:
    """PokeAPI reports 0 for 'ailment_chance'/'stat_chance' when the effect is
    the move's guaranteed primary effect (e.g. Thunder Wave's paralysis) —
    only secondary effects on damaging moves carry a real percentage."""
    if not has_effect:
        return 0
    if raw_chance == 0:
        return 100
    return raw_chance


def derive_flags(move_name: str, category: str, power: int | None, accuracy: int | None,
                  meta: dict, min_hits, max_hits, min_turns, max_turns,
                  pokeapi_flags: set[str], drain: int) -> list[str]:
    flags: set[str] = set()

    if min_hits and max_hits and max_hits > 1:
        flags.add("multi_hit")
    if drain and drain > 0:
        flags.add("drain")
    elif drain and drain < 0:
        flags.add("recoil")
    if "charge" in pokeapi_flags:
        flags.add("charge")
    if "recharge" in pokeapi_flags:
        flags.add("recharge")
    if (
        category != "status"
        and min_turns and max_turns and max_turns > 1
        and "charge" not in flags and "recharge" not in flags
    ):
        flags.add("multi_turn_lock")
    if accuracy is None and category != "status":
        flags.add("always_hit")

    flags.update(MOVE_FLAG_OVERRIDES.get(move_name, []))
    return sorted(flags)


async def enrich_move(session, sem, name: str) -> dict:
    slug = slugify(name)
    move_json = await fetch_json(session, f"https://pokeapi.co/api/v2/move/{slug}", sem)
    if move_json is None:
        print(f"  WARNING: could not fetch move data for {name!r} (slug={slug!r}); leaving effect fields empty")
        return {
            "ailment": None, "ailment_chance": 0, "stat_changes": [], "stat_chance": 0,
            "crit_rate": 0, "drain_percent": None, "recoil_percent": None, "healing_percent": None,
            "flinch_chance": 0, "min_hits": None, "max_hits": None, "min_turns": None, "max_turns": None,
            "target": "opponent", "flags": MOVE_FLAG_OVERRIDES.get(name, []),
        }

    meta = move_json.get("meta") or {}
    category = move_json["damage_class"]["name"] if move_json.get("damage_class") else "status"
    power = move_json.get("power")
    accuracy = move_json.get("accuracy")
    effect_chance = move_json.get("effect_chance")

    ailment_name = (meta.get("ailment") or {}).get("name", "none")
    ailment = AILMENT_MAP.get(ailment_name)
    ailment = MOVE_AILMENT_OVERRIDES.get(name, ailment)
    raw_ailment_chance = meta.get("ailment_chance", 0) or effect_chance or 0
    ailment_chance = normalize_chance(raw_ailment_chance, category, ailment is not None)

    stat_changes = [
        {"stat": sc["stat"]["name"].replace("-", "_"), "change": sc["change"]}
        for sc in move_json.get("stat_changes", [])
    ]
    raw_stat_chance = meta.get("stat_chance", 0) or effect_chance or 0
    stat_chance = normalize_chance(raw_stat_chance, category, bool(stat_changes))

    drain = meta.get("drain", 0) or 0
    drain_percent = drain if drain > 0 else None
    recoil_percent = abs(drain) if drain < 0 else None
    healing = meta.get("healing", 0) or 0
    healing_percent = healing if healing > 0 else None

    pokeapi_flags = {f["name"] for f in move_json.get("flags", []) if f.get("name")}

    min_hits, max_hits = meta.get("min_hits"), meta.get("max_hits")
    min_turns, max_turns = meta.get("min_turns"), meta.get("max_turns")

    target_name = (move_json.get("target") or {}).get("name", "selected-pokemon")
    target = "self" if target_name in ("user", "users-field") else "opponent"

    flags = derive_flags(
        name, category, power, accuracy, meta, min_hits, max_hits, min_turns, max_turns, pokeapi_flags, drain
    )

    return {
        "ailment": ailment,
        "ailment_chance": ailment_chance,
        "stat_changes": stat_changes,
        "stat_chance": stat_chance,
        "crit_rate": meta.get("crit_rate", 0) or 0,
        "drain_percent": drain_percent,
        "recoil_percent": recoil_percent,
        "healing_percent": healing_percent,
        "flinch_chance": meta.get("flinch_chance", 0) or 0,
        "min_hits": min_hits,
        "max_hits": max_hits,
        "min_turns": min_turns,
        "max_turns": max_turns,
        "target": target,
        "flags": flags,
    }


async def main():
    with open(DATA_PATH, encoding="utf-8") as f:
        species_list = json.load(f)

    unique_names: set[str] = set()
    for species in species_list:
        for move in species.get("moves", []):
            unique_names.add(move["name"])

    print(f"{len(unique_names)} unique moves to enrich.")

    sem = asyncio.Semaphore(CONCURRENCY)
    move_effects: dict[str, dict] = {}
    connector = aiohttp.TCPConnector(limit=CONCURRENCY)
    async with aiohttp.ClientSession(connector=connector) as session:
        names = sorted(unique_names)
        tasks = [enrich_move(session, sem, name) for name in names]
        results = await asyncio.gather(*tasks)
        for name, effect in zip(names, results):
            move_effects[name] = effect

    # Apply fixed-damage overrides directly onto the cached effect dict so
    # they ride along with every species that knows the move.
    for name, amount in MOVE_FIXED_DAMAGE_OVERRIDES.items():
        if name in move_effects:
            move_effects[name]["fixed_damage_amount"] = amount

    updated = 0
    for species in species_list:
        for move in species.get("moves", []):
            effect = move_effects.get(move["name"])
            if effect is not None:
                move.update(effect)
                updated += 1

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(species_list, f, ensure_ascii=False, indent=2)

    print(f"Done. {len(move_effects)} unique moves enriched, {updated} move entries updated across all species.")


if __name__ == "__main__":
    asyncio.run(main())
