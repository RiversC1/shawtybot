"""Trainer progression shared by the bot (cogs/pokemon.py) and the web API
(webapi.py): trainer characters, XP levels, bag items and achievements.
Plain sqlite3, no discord.py or FastAPI, so both processes import the one
copy instead of keeping their own in sync."""
import sqlite3
from datetime import datetime, timezone

# ---------- Trainer characters ----------
# Main protagonists, one male/female pair per generation 1-4 plus two custom
# ones, for the web trainer card and battle HUD. Self-hosted under
# web/static/trainers/ (Bulbagarden Archives intermittently 403s hotlinks).
TRAINER_SPRITE_BASE = "https://shawtypoke-web.duckdns.org/static/trainers"
_CHARACTERS = [
    ("red", "Red", "Kanto"), ("leaf", "Leaf", "Kanto"), ("gold", "Gold", "Johto"), ("kris", "Kris", "Johto"),
    ("brendan", "Brendan", "Hoenn"), ("may", "May", "Hoenn"), ("lucas", "Lucas", "Sinnoh"),
    ("dawn", "Dawn", "Sinnoh"), ("rivers", "Rivers", "Custom"), ("nowa", "Nowa", "Custom"),
]
TRAINER_CHARACTERS = {
    key: {"label": label, "generation": gen, "sprite": f"{TRAINER_SPRITE_BASE}/{key}.png"}
    for key, label, gen in _CHARACTERS
}
DEFAULT_CHARACTER = "red"


def character_info(key: str | None) -> dict:
    """A trainer's chosen character (the default one if unset or unknown)."""
    return TRAINER_CHARACTERS.get(key or DEFAULT_CHARACTER, TRAINER_CHARACTERS[DEFAULT_CHARACTER])

# ---------- XP / level ----------


def xp_for_level(level: int) -> int:
    """XP required to advance from `level` to `level + 1`."""
    return 50 + level * 25


def compute_level(total_xp: int) -> tuple[int, int, int]:
    """Returns (level, xp_into_current_level, xp_needed_for_next_level)."""
    level = 1
    remaining = total_xp
    while remaining >= xp_for_level(level):
        remaining -= xp_for_level(level)
        level += 1
    return level, remaining, xp_for_level(level)


# ---------- Bag ----------


def add_item_sql(conn: sqlite3.Connection, user_id: int, item: str, delta: int):
    """Adds `delta` (may be negative) of an item; quantities never go below 0."""
    conn.execute(
        "INSERT INTO poke_items (user_id, item, qty) VALUES (?, ?, MAX(?, 0)) "
        "ON CONFLICT(user_id, item) DO UPDATE SET qty = MAX(qty + ?, 0)",
        (user_id, item, delta, delta),
    )


def item_qty(conn: sqlite3.Connection, user_id: int, item: str) -> int:
    row = conn.execute("SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, item)).fetchone()
    return row[0] if row else 0


# ---------- Permanent achievements ----------
# Categories x 5 tiers. Each tier grants its rewards to the bag automatically,
# exactly once, the moment its threshold is crossed.
ACHIEVEMENT_TIERS = ["bronze", "silver", "gold", "platinum", "diamond"]

ACHIEVEMENTS = {
    "catches": {
        "label": "Catches",
        "description": "Catch Pokémon",
        "stat": "total_caught",
        "tiers": {
            "bronze": {"threshold": 10, "rewards": {"pokeball": 5}},
            "silver": {"threshold": 50, "rewards": {"pokeball": 10, "greatball": 5}},
            "gold": {"threshold": 150, "rewards": {"greatball": 10, "coin": 50}},
            "platinum": {"threshold": 300, "rewards": {"ultraball": 10, "coin": 100}},
            "diamond": {"threshold": 500, "rewards": {"masterball": 1, "coin": 200}},
        },
    },
    "species": {
        "label": "Pokédex Completion",
        "description": "Catch unique species",
        "stat": "unique_species",
        "tiers": {
            "bronze": {"threshold": 10, "rewards": {"greatball": 5}},
            "silver": {"threshold": 50, "rewards": {"greatball": 10, "coin": 50}},
            "gold": {"threshold": 150, "rewards": {"ultraball": 10, "coin": 100}},
            "platinum": {"threshold": 300, "rewards": {"masterball": 1, "coin": 150}},
            "diamond": {"threshold": 493, "rewards": {"masterball": 2, "coin": 500}},
        },
    },
    "shiny": {
        "label": "Shiny Hunter",
        "description": "Catch shiny Pokémon",
        "stat": "shiny_count",
        "tiers": {
            "bronze": {"threshold": 1, "rewards": {"coin": 50}},
            "silver": {"threshold": 3, "rewards": {"ultraball": 5, "coin": 100}},
            "gold": {"threshold": 5, "rewards": {"masterball": 1}},
            "platinum": {"threshold": 10, "rewards": {"masterball": 2, "coin": 200}},
            "diamond": {"threshold": 20, "rewards": {"masterball": 3, "coin": 500}},
        },
    },
    "evolutions": {
        "label": "Evolver",
        "description": "Evolve Pokémon",
        "stat": "evolution_count",
        "tiers": {
            "bronze": {"threshold": 1, "rewards": {"coin": 20}},
            "silver": {"threshold": 5, "rewards": {"greatball": 5, "coin": 50}},
            "gold": {"threshold": 15, "rewards": {"ultraball": 5, "coin": 100}},
            "platinum": {"threshold": 30, "rewards": {"masterball": 1, "coin": 200}},
            "diamond": {"threshold": 50, "rewards": {"masterball": 2, "coin": 300}},
        },
    },
    "level": {
        "label": "Trainer Level",
        "description": "Reach trainer levels",
        "stat": "level",
        "tiers": {
            "bronze": {"threshold": 5, "rewards": {"pokeball": 10}},
            "silver": {"threshold": 10, "rewards": {"greatball": 10}},
            "gold": {"threshold": 20, "rewards": {"ultraball": 10, "coin": 100}},
            "platinum": {"threshold": 35, "rewards": {"masterball": 1, "coin": 150}},
            "diamond": {"threshold": 50, "rewards": {"masterball": 2, "coin": 300}},
        },
    },
    "battles": {
        "label": "Battler",
        "description": "Win battles (PvP, gyms, or trainers)",
        "stat": "battle_wins",
        "tiers": {
            "bronze": {"threshold": 5, "rewards": {"coin": 50}},
            "silver": {"threshold": 20, "rewards": {"greatball": 10, "coin": 100}},
            "gold": {"threshold": 50, "rewards": {"ultraball": 10, "coin": 200}},
            "platinum": {"threshold": 100, "rewards": {"masterball": 1, "coin": 300}},
            "diamond": {"threshold": 250, "rewards": {"masterball": 2, "coin": 500}},
        },
    },
    "legends": {
        "label": "Legend Slayer", "description": "Defeat the Legends: Red, Silver, Steven, Cyrus and Ash",
        "stat": "legends_beaten",
        "tiers": {
            "bronze": {"threshold": 1, "rewards": {"coin": 500}},
            "silver": {"threshold": 2, "rewards": {"ultraball": 10, "coin": 1000}},
            "gold": {"threshold": 3, "rewards": {"masterball": 1, "coin": 1500}},
            "platinum": {"threshold": 4, "rewards": {"masterball": 2, "coin": 2000}},
            "diamond": {"threshold": 5, "rewards": {"masterball": 3, "coin": 5000}},
        },
    },
    "true_champion": {
        "label": "True Champion", "description": "Defeat True Champion Cynthia, the hardest battle in the game",
        "stat": "true_champion_wins",
        "tiers": {
            "bronze": {"threshold": 1, "rewards": {"masterball": 3, "coin": 10000}},
            "silver": {"threshold": 2, "rewards": {"masterball": 2, "coin": 5000}},
            "gold": {"threshold": 3, "rewards": {"masterball": 3, "coin": 7500}},
            "platinum": {"threshold": 5, "rewards": {"masterball": 3, "coin": 10000}},
            "diamond": {"threshold": 10, "rewards": {"masterball": 5, "coin": 25000}},
        },
    },
}


def battle_wins(conn: sqlite3.Connection, user_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM poke_battles WHERE status = 'finished' AND "
        "((side_a_user_id = ? AND winner_side = 'A') OR (side_b_user_id = ? AND winner_side = 'B'))",
        (user_id, user_id),
    ).fetchone()
    return row[0] if row else 0


def _legend_wins(conn: sqlite3.Connection, user_id: int, keys, column: str) -> int:
    import battle_store  # here, not at the top: battle_store imports this module
    battle_store.ensure_legend_table(conn)
    keys = list(keys)
    if not keys:
        return 0
    marks = ",".join("?" * len(keys))
    return conn.execute(f"SELECT COALESCE({column}, 0) FROM poke_legend_wins WHERE user_id = ? AND legend_key IN ({marks})",
                        (user_id, *keys)).fetchone()[0]


def legends_beaten(conn: sqlite3.Connection, user_id: int) -> int:
    """The five Legends beaten (the super-boss has its own achievement)."""
    import battle_store
    return _legend_wins(conn, user_id, battle_store.REGULAR_LEGEND_KEYS, "COUNT(*)")


def true_champion_wins(conn: sqlite3.Connection, user_id: int) -> int:
    import battle_store
    return _legend_wins(conn, user_id, battle_store.SUPERBOSS_KEYS, "SUM(wins)")


def achievement_stats(conn: sqlite3.Connection, user_id: int) -> dict:
    """The current value of every stat an achievement tracks."""
    return {
        "total_caught": conn.execute("SELECT COUNT(*) FROM poke_collection WHERE user_id = ?", (user_id,)).fetchone()[0],
        "unique_species": conn.execute("SELECT COUNT(DISTINCT dex_id) FROM poke_collection WHERE user_id = ?",
                                       (user_id,)).fetchone()[0],
        "shiny_count": conn.execute("SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND is_shiny = 1",
                                    (user_id,)).fetchone()[0],
        "evolution_count": item_qty(conn, user_id, "stat_evolutions"),
        "level": compute_level(item_qty(conn, user_id, "xp"))[0],
        "battle_wins": battle_wins(conn, user_id),
        "legends_beaten": legends_beaten(conn, user_id),
        "true_champion_wins": true_champion_wins(conn, user_id),
    }


def unlocked_achievements(conn: sqlite3.Connection, user_id: int) -> set[str]:
    return {r[0] for r in conn.execute("SELECT achievement_key FROM poke_achievements WHERE user_id = ?", (user_id,))}


def unlock_achievements(conn: sqlite3.Connection, user_id: int) -> list[dict]:
    """Unlocks every tier whose threshold is now met, granting its rewards
    exactly once, and returns the newly unlocked ones. The bot and the API
    may both check the same trainer at the same moment; only the insert that
    lands grants the rewards."""
    stats = achievement_stats(conn, user_id)
    unlocked = unlocked_achievements(conn, user_id)
    newly_unlocked = []
    for cat_key, cat in ACHIEVEMENTS.items():
        for tier_key in ACHIEVEMENT_TIERS:
            key = f"{cat_key}_{tier_key}"
            tier = cat["tiers"][tier_key]
            if key in unlocked or stats[cat["stat"]] < tier["threshold"]:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO poke_achievements (user_id, achievement_key, unlocked_at) VALUES (?, ?, ?)",
                (user_id, key, datetime.now(timezone.utc).isoformat()),
            )
            if cur.rowcount == 0:
                continue
            for item, qty in tier["rewards"].items():
                add_item_sql(conn, user_id, item, qty)
            newly_unlocked.append({"category": cat_key, "category_label": cat["label"],
                                   "tier": tier_key, "rewards": tier["rewards"]})
    return newly_unlocked


# ---------- Store ----------
# Every item the web store sells, and what the bot's bag and evolutions use.
import battle_engine as _be  # noqa: E402  (no project imports above: battle_store imports this module)

ITEM_SPRITE_BASE = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items"
BALL_SPRITES = {
    "pokeball": f"{ITEM_SPRITE_BASE}/poke-ball.png",
    "greatball": f"{ITEM_SPRITE_BASE}/great-ball.png",
    "ultraball": f"{ITEM_SPRITE_BASE}/ultra-ball.png",
    "masterball": f"{ITEM_SPRITE_BASE}/master-ball.png",
}
STORE_ITEMS = {
    "pokeball": {"label": "Poké Ball", "price": 5},
    "greatball": {"label": "Great Ball", "price": 15},
    "ultraball": {"label": "Ultra Ball", "price": 35},
    "masterball": {"label": "Master Ball", "price": 10000},
    "meteorite": {"label": "Meteorite", "price": 25000},
    "fire-stone": {"label": "Fire Stone", "price": 80},
    "water-stone": {"label": "Water Stone", "price": 80},
    "thunder-stone": {"label": "Thunder Stone", "price": 80},
    "leaf-stone": {"label": "Leaf Stone", "price": 80},
    "moon-stone": {"label": "Moon Stone", "price": 90},
    "sun-stone": {"label": "Sun Stone", "price": 90},
    "shiny-stone": {"label": "Shiny Stone", "price": 110},
    "dusk-stone": {"label": "Dusk Stone", "price": 110},
    "dawn-stone": {"label": "Dawn Stone", "price": 110},
}
# Held items (battle_engine.HELD_ITEMS): cheap on purpose.
HELD_ITEM_PRICES = {
    "leftovers": 300, "life-orb": 350, "choice-band": 350, "choice-specs": 350, "choice-scarf": 350,
    "focus-sash": 200, "expert-belt": 250, "muscle-band": 150, "wise-glasses": 150,
    "sitrus-berry": 60, "lum-berry": 80, "quick-claw": 150, "scope-lens": 150, "bright-powder": 150,
    "heat-rock": 120, "damp-rock": 120, "smooth-rock": 120, "icy-rock": 120,
}
STORE_ITEMS.update({key: {"label": _be.HELD_ITEMS[key]["label"], "price": price} for key, price in HELD_ITEM_PRICES.items()})

EVOLUTION_CANDY_COST = 20
XP_PER_EVOLUTION = 20
