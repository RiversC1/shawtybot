"""
Internal Pokémon data API — runs on the same machine as the Discord bot,
reading the same pokemon.db SQLite file directly. The public-facing web
app (hosted separately, e.g. on EC2) talks to this over HTTPS instead of
touching the database itself.

Run with: uvicorn webapi:app --host 0.0.0.0 --port 8000

CD test marker: deploy-bot.yml pipeline
"""
import asyncio
import os
import re
import sqlite3
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

import battle_engine as be
import battle_store
import trade_store

log = logging.getLogger("webapi")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

DB_PATH = "pokemon.db"
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "pokemon.json")

JWT_SECRET = os.getenv("WEB_JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("WEB_JWT_SECRET environment variable must be set")
JWT_ALGORITHM = "HS256"
SESSION_DAYS = 7

BALL_KEYS = {"pokeball", "greatball", "ultraball", "masterball"}

# Keep in sync with cogs/pokemon.py — duplicated here so this API has no
# discord.py dependency and can be deployed/restarted independently of the bot.
# Self-hosted (web/static/trainers/) since Bulbagarden Archives intermittently
# 403s hotlinked requests for some of these files — not worth depending on.
WEB_STATIC_BASE = "https://shawtypoke-web.duckdns.org/static/trainers"
TRAINER_CHARACTERS = {
    "red": {"label": "Red", "generation": "Kanto", "sprite": f"{WEB_STATIC_BASE}/red.png"},
    "leaf": {"label": "Leaf", "generation": "Kanto", "sprite": f"{WEB_STATIC_BASE}/leaf.png"},
    "gold": {"label": "Gold", "generation": "Johto", "sprite": f"{WEB_STATIC_BASE}/gold.png"},
    "kris": {"label": "Kris", "generation": "Johto", "sprite": f"{WEB_STATIC_BASE}/kris.png"},
    "brendan": {"label": "Brendan", "generation": "Hoenn", "sprite": f"{WEB_STATIC_BASE}/brendan.png"},
    "may": {"label": "May", "generation": "Hoenn", "sprite": f"{WEB_STATIC_BASE}/may.png"},
    "lucas": {"label": "Lucas", "generation": "Sinnoh", "sprite": f"{WEB_STATIC_BASE}/lucas.png"},
    "dawn": {"label": "Dawn", "generation": "Sinnoh", "sprite": f"{WEB_STATIC_BASE}/dawn.png"},
    "rivers": {"label": "Rivers", "generation": "Custom", "sprite": f"{WEB_STATIC_BASE}/rivers.png"},
    "nowa": {"label": "Nowa", "generation": "Custom", "sprite": f"{WEB_STATIC_BASE}/nowa.png"},
}
DEFAULT_CHARACTER = "red"


def xp_for_level(level: int) -> int:
    return 50 + level * 25


def compute_level(total_xp: int) -> tuple[int, int, int]:
    level = 1
    remaining = total_xp
    while remaining >= xp_for_level(level):
        remaining -= xp_for_level(level)
        level += 1
    return level, remaining, xp_for_level(level)


# Keep in sync with cogs/pokemon.py's ACHIEVEMENTS.
ACHIEVEMENT_TIERS = ["bronze", "silver", "gold", "platinum", "diamond"]

ACHIEVEMENTS = {
    "catches": {
        "label": "Catches", "description": "Catch Pokémon", "stat": "total_caught",
        "tiers": {
            "bronze": {"threshold": 10, "rewards": {"pokeball": 5}},
            "silver": {"threshold": 50, "rewards": {"pokeball": 10, "greatball": 5}},
            "gold": {"threshold": 150, "rewards": {"greatball": 10, "coin": 50}},
            "platinum": {"threshold": 300, "rewards": {"ultraball": 10, "coin": 100}},
            "diamond": {"threshold": 500, "rewards": {"masterball": 1, "coin": 200}},
        },
    },
    "species": {
        "label": "Pokédex Completion", "description": "Catch unique species", "stat": "unique_species",
        "tiers": {
            "bronze": {"threshold": 10, "rewards": {"greatball": 5}},
            "silver": {"threshold": 50, "rewards": {"greatball": 10, "coin": 50}},
            "gold": {"threshold": 150, "rewards": {"ultraball": 10, "coin": 100}},
            "platinum": {"threshold": 300, "rewards": {"masterball": 1, "coin": 150}},
            "diamond": {"threshold": 493, "rewards": {"masterball": 2, "coin": 500}},
        },
    },
    "shiny": {
        "label": "Shiny Hunter", "description": "Catch shiny Pokémon", "stat": "shiny_count",
        "tiers": {
            "bronze": {"threshold": 1, "rewards": {"coin": 50}},
            "silver": {"threshold": 3, "rewards": {"ultraball": 5, "coin": 100}},
            "gold": {"threshold": 5, "rewards": {"masterball": 1}},
            "platinum": {"threshold": 10, "rewards": {"masterball": 2, "coin": 200}},
            "diamond": {"threshold": 20, "rewards": {"masterball": 3, "coin": 500}},
        },
    },
    "evolutions": {
        "label": "Evolver", "description": "Evolve Pokémon", "stat": "evolution_count",
        "tiers": {
            "bronze": {"threshold": 1, "rewards": {"coin": 20}},
            "silver": {"threshold": 5, "rewards": {"greatball": 5, "coin": 50}},
            "gold": {"threshold": 15, "rewards": {"ultraball": 5, "coin": 100}},
            "platinum": {"threshold": 30, "rewards": {"masterball": 1, "coin": 200}},
            "diamond": {"threshold": 50, "rewards": {"masterball": 2, "coin": 300}},
        },
    },
    "level": {
        "label": "Trainer Level", "description": "Reach trainer levels", "stat": "level",
        "tiers": {
            "bronze": {"threshold": 5, "rewards": {"pokeball": 10}},
            "silver": {"threshold": 10, "rewards": {"greatball": 10}},
            "gold": {"threshold": 20, "rewards": {"ultraball": 10, "coin": 100}},
            "platinum": {"threshold": 35, "rewards": {"masterball": 1, "coin": 150}},
            "diamond": {"threshold": 50, "rewards": {"masterball": 2, "coin": 300}},
        },
    },
    "battles": {
        "label": "Battler", "description": "Win battles (PvP, gyms, or trainers)", "stat": "battle_wins",
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
}

TOTAL_ACHIEVEMENT_TIERS = sum(len(cat["tiers"]) for cat in ACHIEVEMENTS.values())

# Keep in sync with cogs/pokemon.py's BALLS/STORE_ITEMS/item sprites.
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

# Held items (battle_engine.HELD_ITEMS) — cheap on purpose. Keep in sync
# with cogs/pokemon.py's STORE_ITEMS.
HELD_ITEM_PRICES = {
    "leftovers": 300, "life-orb": 350, "choice-band": 350, "choice-specs": 350, "choice-scarf": 350,
    "focus-sash": 200, "expert-belt": 250, "muscle-band": 150, "wise-glasses": 150,
    "sitrus-berry": 60, "lum-berry": 80, "quick-claw": 150, "scope-lens": 150, "bright-powder": 150,
    "heat-rock": 120, "damp-rock": 120, "smooth-rock": 120, "icy-rock": 120,
}
STORE_ITEMS.update({
    key: {"label": be.HELD_ITEMS[key]["label"], "price": price} for key, price in HELD_ITEM_PRICES.items()
})

HELD_ITEM_KEYS = set(HELD_ITEM_PRICES)


# The 30x30 bag sprites look blurry scaled up; the 90px Dream World art is
# used wherever it exists (every store item but the Meteorite).
NO_DREAM_WORLD_ART = {"meteorite"}
BALL_SLUGS = {"pokeball": "poke-ball", "greatball": "great-ball", "ultraball": "ultra-ball", "masterball": "master-ball"}


def item_icon(key: str) -> str:
    if key in NO_DREAM_WORLD_ART:
        return BALL_SPRITES.get(key) or f"{ITEM_SPRITE_BASE}/{key}.png"
    return f"{ITEM_SPRITE_BASE}/dream-world/{BALL_SLUGS.get(key, key)}.png"


# Keep in sync with cogs/pokemon.py.
EVOLUTION_CANDY_COST = 20
XP_PER_EVOLUTION = 20
ITEM_LABELS = {key: cfg["label"] for key, cfg in STORE_ITEMS.items()}
ITEM_LABELS["candy"] = "Rare Candy"
ITEM_LABELS["coin"] = "Poké Coin"


def format_ability_name(raw: str) -> str:
    return raw.replace("-", " ").title()


def stat_at_level_100(base: int, iv: int, is_hp: bool) -> int:
    """A stat at level 100 (no EV investment, neutral nature) using the
    standard stat formula, with a real per-individual IV (0-31, see
    poke_collection's iv_* columns). Mirrors battle_engine.py's copy."""
    if is_hp:
        return 2 * base + iv + 110
    return 2 * base + iv + 5


def add_item_sql(conn: sqlite3.Connection, user_id: int, item: str, delta: int):
    conn.execute(
        "INSERT INTO poke_items (user_id, item, qty) VALUES (?, ?, MAX(?, 0)) "
        "ON CONFLICT(user_id, item) DO UPDATE SET qty = MAX(qty + ?, 0)",
        (user_id, item, delta, delta),
    )


def get_battle_wins(conn: sqlite3.Connection, target_id: int) -> int:
    row = conn.execute(
        "SELECT COUNT(*) FROM poke_battles WHERE status = 'finished' AND "
        "((side_a_user_id = ? AND winner_side = 'A') OR (side_b_user_id = ? AND winner_side = 'B'))",
        (target_id, target_id),
    ).fetchone()
    return row[0] if row else 0


def legends_beaten(conn: sqlite3.Connection, target_id: int) -> int:
    battle_store.ensure_legend_table(conn)
    return conn.execute("SELECT COUNT(*) FROM poke_legend_wins WHERE user_id = ?", (target_id,)).fetchone()[0]


def unlock_achievements_for_battle_winner(battle: "be.BattleState", battle_row: sqlite3.Row):
    """Called right when a battle ends so the Battler achievement (and its
    rewards) lands immediately, instead of waiting for the winner's next
    profile view to backfill it."""
    if battle.winner_side not in ("A", "B"):
        return
    winner_user_id = battle_row["side_a_user_id"] if battle.winner_side == "A" else battle_row["side_b_user_id"]
    if not winner_user_id:
        return
    with db() as conn:
        check_and_unlock_achievements(conn, winner_user_id)


def check_and_unlock_achievements(conn: sqlite3.Connection, target_id: int) -> list[dict]:
    """Compares current stats against every achievement tier and unlocks any
    newly-earned ones, granting rewards exactly once. Mirrors
    cogs/pokemon.py's check_achievements — called here too so achievements
    (including ones earned before this feature existed) get backfilled the
    moment a profile is viewed, not only on the next live gameplay action."""
    total_caught = conn.execute(
        "SELECT COUNT(*) FROM poke_collection WHERE user_id = ?", (target_id,)
    ).fetchone()[0]
    unique_species = conn.execute(
        "SELECT COUNT(DISTINCT dex_id) FROM poke_collection WHERE user_id = ?", (target_id,)
    ).fetchone()[0]
    shiny_count = conn.execute(
        "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND is_shiny = 1", (target_id,)
    ).fetchone()[0]
    evo_row = conn.execute(
        "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'stat_evolutions'", (target_id,)
    ).fetchone()
    evolution_count = evo_row["qty"] if evo_row else 0
    xp_row = conn.execute(
        "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'xp'", (target_id,)
    ).fetchone()
    level, _, _ = compute_level(xp_row["qty"] if xp_row else 0)

    stats = {
        "total_caught": total_caught,
        "unique_species": unique_species,
        "shiny_count": shiny_count,
        "evolution_count": evolution_count,
        "level": level,
        "battle_wins": get_battle_wins(conn, target_id),
        "legends_beaten": legends_beaten(conn, target_id),
    }

    unlocked = {
        r["achievement_key"]
        for r in conn.execute(
            "SELECT achievement_key FROM poke_achievements WHERE user_id = ?", (target_id,)
        ).fetchall()
    }

    newly_unlocked = []
    for cat_key, cat in ACHIEVEMENTS.items():
        stat_value = stats[cat["stat"]]
        for tier_key in ACHIEVEMENT_TIERS:
            achievement_key = f"{cat_key}_{tier_key}"
            if achievement_key in unlocked:
                continue
            tier = cat["tiers"][tier_key]
            if stat_value >= tier["threshold"]:
                # Another request (e.g. the profile page's parallel calls, or
                # the bot) may unlock the same tier at the same moment; only
                # the one whose insert lands grants the rewards.
                cur = conn.execute(
                    "INSERT OR IGNORE INTO poke_achievements (user_id, achievement_key, unlocked_at) VALUES (?, ?, ?)",
                    (target_id, achievement_key, datetime.now(timezone.utc).isoformat()),
                )
                if cur.rowcount == 0:
                    continue
                for item, qty in tier["rewards"].items():
                    add_item_sql(conn, target_id, item, qty)
                newly_unlocked.append({"category": cat_key, "tier": tier_key, "rewards": tier["rewards"]})

    return newly_unlocked


with open(DATA_PATH, encoding="utf-8") as f:
    POKEDEX: dict[int, dict] = {p["id"]: p for p in json.load(f)}

# Gym/trainer-class data and every battle DB/orchestration function live in
# battle_store.py — shared with the bot, which only ever creates a battle and
# posts a link; all actual play (accepting, choosing moves, forfeiting) is
# submitted here from the web battle UI, since this is the process with a
# persistent event loop that can hold WebSocket connections open.

BATTLE_SWEEP_INTERVAL_SECONDS = 20
TRADE_SWEEP_INTERVAL_SECONDS = 20


class ConnectionManager:
    """Tracks live WebSocket viewers per session id (a battle_id or trade_id)
    so a state change can be pushed to everyone watching instantly instead of
    them polling for it. In-memory only — fine as long as this runs as a
    single uvicorn process (no multi-worker), which is how it's deployed."""

    def __init__(self, serialize_fn):
        self._serialize_fn = serialize_fn
        self._connections: dict[int, set[tuple[WebSocket, int | None]]] = {}

    async def connect(self, session_id: int, websocket: WebSocket, viewer_user_id: int | None):
        await websocket.accept()
        self._connections.setdefault(session_id, set()).add((websocket, viewer_user_id))

    def disconnect(self, session_id: int, websocket: WebSocket, viewer_user_id: int | None):
        conns = self._connections.get(session_id)
        if conns:
            conns.discard((websocket, viewer_user_id))
            if not conns:
                self._connections.pop(session_id, None)

    async def broadcast(self, session_id: int):
        conns = self._connections.get(session_id)
        if not conns:
            return
        for websocket, viewer_user_id in list(conns):
            try:
                payload = await asyncio.to_thread(self._serialize_fn, session_id, viewer_user_id)
                await websocket.send_json(payload)
            except Exception:
                self.disconnect(session_id, websocket, viewer_user_id)


battle_connections = ConnectionManager(battle_store.serialize_battle_detail)
trade_connections = ConnectionManager(trade_store.serialize_trade_detail)


async def _battle_sweep_loop():
    while True:
        await asyncio.sleep(BATTLE_SWEEP_INTERVAL_SECONDS)
        try:
            changed_ids = await asyncio.to_thread(battle_store.sweep_battles)
            for battle_id in changed_ids:
                await battle_connections.broadcast(battle_id)
        except Exception as e:
            log.error(f"Battle sweep failed: {e}", exc_info=True)


async def _trade_sweep_loop():
    while True:
        await asyncio.sleep(TRADE_SWEEP_INTERVAL_SECONDS)
        try:
            changed_ids = await asyncio.to_thread(trade_store.sweep_trades)
            for trade_id in changed_ids:
                await trade_connections.broadcast(trade_id)
        except Exception as e:
            log.error(f"Trade sweep failed: {e}", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    def _migrate():
        with battle_store.db() as conn:
            battle_store.ensure_mega_unlocks(conn)  # old reward Megas -> unlocks, once

    try:
        await asyncio.to_thread(_migrate)
    except Exception as e:
        log.error(f"Mega unlock migration failed: {e}", exc_info=True)
    tasks = [asyncio.create_task(_battle_sweep_loop()), asyncio.create_task(_trade_sweep_loop())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="ShawtyBot Pokémon API", lifespan=lifespan)

app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(
    CORSMiddleware,
    # Comma-separated list of allowed web-app origins, e.g. "https://poke.example.com"
    allow_origins=os.getenv("WEB_FRONTEND_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def create_session_jwt(user_id: int) -> str:
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def get_current_user_id(authorization: str | None = Header(default=None)) -> int:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing or invalid Authorization header")
    token = authorization.removeprefix("Bearer ")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, "Invalid or expired session")
    return int(payload["sub"])


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Auth ----------

class ExchangeRequest(BaseModel):
    token: str


@app.post("/api/auth/exchange")
def exchange_token(body: ExchangeRequest):
    with db() as conn:
        row = conn.execute(
            "SELECT user_id, expires_at FROM poke_web_tokens WHERE token = ?", (body.token,)
        ).fetchone()
        if row:
            conn.execute("DELETE FROM poke_web_tokens WHERE token = ?", (body.token,))

    if not row:
        raise HTTPException(400, "Invalid or already-used link")
    if datetime.now(timezone.utc) > datetime.fromisoformat(row["expires_at"]):
        raise HTTPException(400, "This link has expired — run /poke web again")

    return {"session": create_session_jwt(row["user_id"]), "user_id": row["user_id"]}


# ---------- Profile ----------

def build_profile_payload(target_id: int, conn: sqlite3.Connection) -> dict | None:
    trainer = conn.execute("SELECT * FROM poke_trainers WHERE user_id = ?", (target_id,)).fetchone()
    if not trainer:
        return None

    check_and_unlock_achievements(conn, target_id)

    xp_row = conn.execute(
        "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'xp'", (target_id,)
    ).fetchone()
    total_xp = xp_row["qty"] if xp_row else 0

    total_caught = conn.execute(
        "SELECT COUNT(*) FROM poke_collection WHERE user_id = ?", (target_id,)
    ).fetchone()[0]
    unique_species = conn.execute(
        "SELECT COUNT(DISTINCT dex_id) FROM poke_collection WHERE user_id = ?", (target_id,)
    ).fetchone()[0]
    unlocked_count = conn.execute(
        "SELECT COUNT(*) FROM poke_achievements WHERE user_id = ?", (target_id,)
    ).fetchone()[0]

    starter = POKEDEX.get(trainer["starter_id"])
    level, xp_into_level, xp_needed = compute_level(total_xp)

    character_key = trainer["character"] or DEFAULT_CHARACTER
    character = TRAINER_CHARACTERS.get(character_key, TRAINER_CHARACTERS[DEFAULT_CHARACTER])

    favorite = None
    if trainer["favorite_dex_id"] is not None:
        fav_mon = POKEDEX.get(trainer["favorite_dex_id"])
        if fav_mon:
            favorite = {
                "dex_id": fav_mon["id"],
                "name": fav_mon["name"],
                "types": fav_mon["types"],
                "artwork": fav_mon.get("artwork"),
                "sprite": fav_mon.get("sprite"),
            }

    dex_total = sum(1 for d in POKEDEX if battle_store.counts_toward_pokedex(d))

    earned_badges = battle_store.get_badges(target_id)
    badges_by_region = [
        {
            "region": region,
            "badges": [
                {
                    "gym_key": k,
                    "leader_name": battle_store.GYMS[k]["leader_name"],
                    "badge_name": battle_store.GYMS[k]["badge_name"],
                    "badge_image": battle_store.GYMS[k].get("badge_image"),
                    "type_theme": battle_store.GYMS[k].get("type_theme"),
                    "earned": k in earned_badges,
                }
                for k in battle_store.gym_order(region)
            ],
        }
        for region in battle_store.GYM_REGIONS
    ]
    badges_total_earned = len(earned_badges & set(battle_store.GYMS.keys()))
    badges_total = len(battle_store.GYMS)
    legend_wins = battle_store.get_legend_wins(target_id)

    return {
        "user_id": str(target_id),  # Discord snowflake — see the note in list_trainers()
        "username": trainer["username"],
        "avatar_url": trainer["avatar_url"],
        "starter": starter["name"] if starter else None,
        "created_at": trainer["created_at"],
        "character": {"key": character_key, **character},
        "favorite": favorite,
        "level": level,
        "total_xp": total_xp,
        "xp_into_level": xp_into_level,
        "xp_needed_for_level": xp_needed,
        "collection": {
            "total_caught": total_caught,
            "unique_species": unique_species,
            "dex_total": dex_total,
            "dex_percent": round(unique_species / dex_total * 100, 1) if dex_total else 0,
        },
        "battle_record": {
            "wins": get_battle_wins(conn, target_id),
            "games": conn.execute(
                "SELECT COUNT(*) FROM poke_battles WHERE status = 'finished' "
                "AND (side_a_user_id = ? OR side_b_user_id = ?)",
                (target_id, target_id),
            ).fetchone()[0],
        },
        "badges_by_region": badges_by_region,
        "custom_badges": custom_badges_for(target_id),
        "badges_total_earned": badges_total_earned,
        "badges_total": badges_total,
        "achievements": {"unlocked": unlocked_count, "total": TOTAL_ACHIEVEMENT_TIERS},
        "legend_trophies": [_legend_card(k, legend_wins) for k in battle_store.LEGENDS],
    }


@app.get("/api/me")
def get_me(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        payload = build_profile_payload(user_id, conn)
    if not payload:
        raise HTTPException(404, "Trainer not found")
    return payload


@app.get("/api/trainers")
def list_trainers(user_id: int = Depends(get_current_user_id)):
    """A public directory of every trainer, for browsing other people's profiles."""
    with db() as conn:
        rows = conn.execute(
            "SELECT user_id, username, avatar_url, character FROM poke_trainers"
        ).fetchall()
        results = []
        for row in rows:
            xp_row = conn.execute(
                "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'xp'", (row["user_id"],)
            ).fetchone()
            level, _, _ = compute_level(xp_row["qty"] if xp_row else 0)
            unique_species = conn.execute(
                "SELECT COUNT(DISTINCT dex_id) FROM poke_collection WHERE user_id = ?", (row["user_id"],)
            ).fetchone()[0]
            character_key = row["character"] or DEFAULT_CHARACTER
            character = TRAINER_CHARACTERS.get(character_key, TRAINER_CHARACTERS[DEFAULT_CHARACTER])
            results.append({
                # Discord snowflake IDs (18-19 digits) exceed JavaScript's safe
                # integer range (2^53) — serialize as a string so a JS client
                # round-tripping this through JSON doesn't silently corrupt it
                # (which would send trade proposals/collection lookups to the
                # wrong, or a nonexistent, user).
                "user_id": str(row["user_id"]),
                "username": row["username"] or "Trainer",
                "avatar_url": row["avatar_url"],
                "level": level,
                "unique_species": unique_species,
                "character_sprite": character["sprite"],
                "is_you": row["user_id"] == user_id,
            })

    results.sort(key=lambda t: (-t["level"], t["username"].lower()))
    return results


RANKING_MIN_BATTLES_FOR_WIN_RATE = 5


@app.get("/api/rankings")
def get_rankings(user_id: int = Depends(get_current_user_id)):
    """Every trainer's leaderboard stats in one payload; the page sorts it per
    category client-side. Each stat is one aggregate query over its table
    (not a query per trainer), so this stays cheap as the server grows."""
    gym_keys = set(battle_store.GYMS.keys())
    with db() as conn:
        trainers = conn.execute("SELECT user_id, username, avatar_url, character FROM poke_trainers").fetchall()

        # Finished battles only: abandoned ones never produced a result.
        battle_rows = conn.execute(
            "SELECT side_a_user_id AS uid, winner_side = 'A' AS won, battle_type FROM poke_battles "
            "WHERE status = 'finished' AND side_a_user_id IS NOT NULL "
            "UNION ALL "
            "SELECT side_b_user_id AS uid, winner_side = 'B' AS won, battle_type FROM poke_battles "
            "WHERE status = 'finished' AND side_b_user_id IS NOT NULL"
        ).fetchall()

        def per_user(sql: str) -> dict[int, int]:
            return {r[0]: r[1] for r in conn.execute(sql).fetchall()}

        xp = per_user("SELECT user_id, qty FROM poke_items WHERE item = 'xp'")
        dex = per_user("SELECT user_id, COUNT(*) FROM poke_dex_seen GROUP BY user_id")
        caught = per_user("SELECT user_id, COUNT(*) FROM poke_collection GROUP BY user_id")
        shinies = per_user("SELECT user_id, COUNT(*) FROM poke_collection WHERE is_shiny = 1 GROUP BY user_id")
        league = per_user("SELECT user_id, COUNT(*) FROM poke_league_progress GROUP BY user_id")
        badge_rows = conn.execute("SELECT user_id, gym_key FROM poke_badges").fetchall()

    wins: dict[int, int] = {}
    games: dict[int, int] = {}
    pvp_wins: dict[int, int] = {}
    for r in battle_rows:
        uid = r["uid"]
        games[uid] = games.get(uid, 0) + 1
        if r["won"]:
            wins[uid] = wins.get(uid, 0) + 1
            if r["battle_type"] == "pvp":
                pvp_wins[uid] = pvp_wins.get(uid, 0) + 1
    badges: dict[int, int] = {}
    for r in badge_rows:
        if r["gym_key"] in gym_keys:
            badges[r["user_id"]] = badges.get(r["user_id"], 0) + 1

    results = []
    for t in trainers:
        uid = t["user_id"]
        character = TRAINER_CHARACTERS.get(t["character"] or DEFAULT_CHARACTER, TRAINER_CHARACTERS[DEFAULT_CHARACTER])
        level, _, _ = compute_level(xp.get(uid, 0))
        g = games.get(uid, 0)
        w = wins.get(uid, 0)
        results.append({
            "user_id": str(uid),  # snowflake: see list_trainers()
            "username": t["username"] or "Trainer",
            "avatar_url": t["avatar_url"],
            "character_sprite": character["sprite"],
            "is_you": uid == user_id,
            "wins": w,
            "losses": g - w,
            "battles": g,
            "win_rate": round(w / g * 100, 1) if g >= RANKING_MIN_BATTLES_FOR_WIN_RATE else None,
            "pvp_wins": pvp_wins.get(uid, 0),
            "badges": badges.get(uid, 0),
            "league": league.get(uid, 0),
            "pokedex": dex.get(uid, 0),
            "caught": caught.get(uid, 0),
            "shinies": shinies.get(uid, 0),
            "level": level,
            "xp": xp.get(uid, 0),
        })

    return {
        "trainers": results,
        "totals": {
            "badges": len(gym_keys),
            "league": len(battle_store.LEAGUE),
            "pokedex": len([d for d in POKEDEX if battle_store.counts_toward_pokedex(d) and d != battle_store.CUSTOM_GYM_REWARD_DEX_ID]),
            "win_rate_min_battles": RANKING_MIN_BATTLES_FOR_WIN_RATE,
        },
    }


@app.get("/api/trainer/{target_id}")
def get_trainer(target_id: int, user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        payload = build_profile_payload(target_id, conn)
    if not payload:
        raise HTTPException(404, "Trainer not found")
    return payload


# ---------- Achievements ----------

def build_achievements_payload(target_id: int, conn: sqlite3.Connection) -> dict:
    check_and_unlock_achievements(conn, target_id)

    total_caught = conn.execute(
        "SELECT COUNT(*) FROM poke_collection WHERE user_id = ?", (target_id,)
    ).fetchone()[0]
    unique_species = conn.execute(
        "SELECT COUNT(DISTINCT dex_id) FROM poke_collection WHERE user_id = ?", (target_id,)
    ).fetchone()[0]
    shiny_count = conn.execute(
        "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND is_shiny = 1", (target_id,)
    ).fetchone()[0]
    evo_row = conn.execute(
        "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'stat_evolutions'", (target_id,)
    ).fetchone()
    evolution_count = evo_row["qty"] if evo_row else 0
    xp_row = conn.execute(
        "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'xp'", (target_id,)
    ).fetchone()
    level, _, _ = compute_level(xp_row["qty"] if xp_row else 0)

    unlocked = {
        r["achievement_key"]
        for r in conn.execute(
            "SELECT achievement_key FROM poke_achievements WHERE user_id = ?", (target_id,)
        ).fetchall()
    }

    stats = {
        "total_caught": total_caught,
        "unique_species": unique_species,
        "shiny_count": shiny_count,
        "evolution_count": evolution_count,
        "level": level,
        "battle_wins": get_battle_wins(conn, target_id),
        "legends_beaten": legends_beaten(conn, target_id),
    }

    categories = []
    for cat_key, cat in ACHIEVEMENTS.items():
        stat_value = stats[cat["stat"]]
        tiers = []
        for tier_key in ACHIEVEMENT_TIERS:
            tier = cat["tiers"][tier_key]
            tiers.append({
                "tier": tier_key,
                "threshold": tier["threshold"],
                "rewards": tier["rewards"],
                "unlocked": f"{cat_key}_{tier_key}" in unlocked,
            })
        next_threshold = next((t["threshold"] for t in tiers if not t["unlocked"]), None)
        categories.append({
            "key": cat_key,
            "label": cat["label"],
            "description": cat["description"],
            "current_value": stat_value,
            "next_threshold": next_threshold,  # None once all 5 tiers are unlocked
            "tiers": tiers,
        })

    return {"categories": categories, "total": TOTAL_ACHIEVEMENT_TIERS}


@app.get("/api/achievements")
def get_achievements(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        return build_achievements_payload(user_id, conn)


@app.get("/api/trainer/{target_id}/achievements")
def get_trainer_achievements(target_id: int, user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        trainer = conn.execute("SELECT 1 FROM poke_trainers WHERE user_id = ?", (target_id,)).fetchone()
        if not trainer:
            raise HTTPException(404, "Trainer not found")
        return build_achievements_payload(target_id, conn)


# ---------- Trainer character ----------

@app.get("/api/characters")
def list_characters():
    return [{"key": key, **cfg} for key, cfg in TRAINER_CHARACTERS.items()]


class CharacterRequest(BaseModel):
    character: str


@app.post("/api/character")
def set_character(body: CharacterRequest, user_id: int = Depends(get_current_user_id)):
    if body.character not in TRAINER_CHARACTERS:
        raise HTTPException(400, "Unknown character")
    with db() as conn:
        conn.execute("UPDATE poke_trainers SET character = ? WHERE user_id = ?", (body.character, user_id))
    return {"ok": True}


# ---------- Favorite Pokémon ----------

class FavoriteRequest(BaseModel):
    dex_id: int | None  # null clears the favorite


@app.post("/api/favorite")
def set_favorite(body: FavoriteRequest, user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        if body.dex_id is not None:
            owned = conn.execute(
                "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?",
                (user_id, body.dex_id),
            ).fetchone()[0]
            if owned < 1:
                name = POKEDEX.get(body.dex_id, {}).get("name", f"#{body.dex_id}")
                raise HTTPException(400, f"You don't own a {name}")
        conn.execute(
            "UPDATE poke_trainers SET favorite_dex_id = ? WHERE user_id = ?", (body.dex_id, user_id)
        )
    return {"ok": True}


# ---------- Pokédex ----------

@app.get("/api/pokedex")
def get_pokedex(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        rows = conn.execute(
            "SELECT dex_id, COUNT(*) as count, MAX(is_shiny) as has_shiny FROM poke_collection "
            "WHERE user_id = ? GROUP BY dex_id ORDER BY dex_id",
            (user_id,),
        ).fetchall()

    return [
        {
            "dex_id": r["dex_id"],
            "name": POKEDEX.get(r["dex_id"], {}).get("name", f"#{r['dex_id']}"),
            "count": r["count"],
            "has_shiny": bool(r["has_shiny"]),
            "types": POKEDEX.get(r["dex_id"], {}).get("types", []),
            "category": POKEDEX.get(r["dex_id"], {}).get("category"),
            "artwork": POKEDEX.get(r["dex_id"], {}).get("artwork"),
        }
        for r in rows
    ]


def dex_generation(dex_id: int) -> int:
    if dex_id <= 151:
        return 1
    if dex_id <= 251:
        return 2
    if dex_id <= 386:
        return 3
    return 4


@app.get("/api/pokedex/full")
def get_pokedex_full(user_id: int = Depends(get_current_user_id)):
    """Every species in the game. "owned" reflects the permanent Pokédex
    registration (poke_dex_seen) — once caught, a species stays colored-in
    forever, even if you no longer currently hold one (evolved it away,
    traded it, etc). "count" is how many you currently hold, separately."""
    with db() as conn:
        seen_rows = conn.execute(
            "SELECT dex_id FROM poke_dex_seen WHERE user_id = ?", (user_id,)
        ).fetchall()
        held_rows = conn.execute(
            "SELECT dex_id, COUNT(*) as count, MAX(is_shiny) as has_shiny FROM poke_collection "
            "WHERE user_id = ? GROUP BY dex_id",
            (user_id,),
        ).fetchall()

    seen = {r["dex_id"] for r in seen_rows}
    held = {r["dex_id"]: {"count": r["count"], "has_shiny": bool(r["has_shiny"])} for r in held_rows}

    result = []
    for dex_id in sorted(d for d in POKEDEX if battle_store.counts_toward_pokedex(d)):
        mon = POKEDEX[dex_id]
        h = held.get(dex_id)
        if mon.get("is_mythical"):
            rarity = "mythical"
        elif mon.get("is_legendary"):
            rarity = "legendary"
        else:
            rarity = "standard"
        result.append({
            "dex_id": dex_id,
            "name": mon.get("name", f"#{dex_id}"),
            "types": mon.get("types", []),
            "category": mon.get("category"),
            "artwork": mon.get("artwork"),
            "generation": dex_generation(dex_id),
            "rarity": rarity,
            "owned": dex_id in seen,
            "count": h["count"] if h else 0,
            "has_shiny": h["has_shiny"] if h else False,
        })
    return result


@app.get("/api/species/{dex_id}")
def get_species_detail(dex_id: int, user_id: int = Depends(get_current_user_id)):
    """Species-level detail for the Pokédex click-through card — works even
    for species you haven't caught, unlike /api/collection/{catch_id} which
    is about one specific individual you own."""
    mon = POKEDEX.get(dex_id)
    if not mon:
        raise HTTPException(404, "Unknown species")

    with db() as conn:
        seen_row = conn.execute(
            "SELECT 1 FROM poke_dex_seen WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
        ).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
        ).fetchone()[0]
        evolution = build_evolution_info(dex_id, user_id, conn)
        ivs = battle_store.get_best_ivs_for_species(user_id, dex_id) if count > 0 else None
        if count > 0:
            config = resolve_pokemon_config(conn, user_id, dex_id)
        else:
            pool = mon.get("moves", [])
            abilities = mon.get("abilities", [])
            config = {
                "moves": pool[:4],
                "move_pool": pool,
                "ability": format_ability_name(abilities[0]["name"]) if abilities else None,
                "ability_raw": abilities[0]["name"] if abilities else None,
                "abilities": [{"name": a["name"], "label": format_ability_name(a["name"])} for a in abilities],
            }

    return {
        "dex_id": dex_id,
        "name": mon["name"],
        "types": mon.get("types", []),
        "category": mon.get("category"),
        "artwork": mon.get("artwork"),
        "owned": bool(seen_row),
        "count": count,
        "base_stats": resolved_base_stats(mon, ivs),
        "evolution": evolution,
        **config,
    }


# ---------- Inventory ----------

@app.get("/api/inventory")
def get_inventory(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        rows = conn.execute("SELECT item, qty FROM poke_items WHERE user_id = ?", (user_id,)).fetchall()

    raw = {row["item"]: row["qty"] for row in rows}

    balls, stones, key_items, held_items = [], [], [], []
    for key, cfg in STORE_ITEMS.items():
        if not raw.get(key):
            continue
        entry = {"key": key, "label": cfg["label"], "icon": item_icon(key), "qty": raw[key]}
        if key in BALL_KEYS:
            balls.append(entry)
        elif key in battle_store.KEY_ITEMS:
            key_items.append(entry)
        elif key in HELD_ITEM_KEYS:
            held_items.append({**entry, "description": be.HELD_ITEMS[key]["description"]})
        else:
            stones.append(entry)

    family_candies = []
    for key, qty in raw.items():
        if key.startswith("famcandy_"):
            try:
                family_id = int(key.split("_", 1)[1])
            except ValueError:
                continue
            family_candies.append({"name": POKEDEX.get(family_id, {}).get("name", key), "qty": qty})

    return {
        "balls": balls,
        "stones": stones,
        "key_items": key_items,
        "held_items": held_items,
        "coins": raw.get("coin", 0),
        "rare_candy": raw.get("candy", 0),
        "family_candies": family_candies,
    }


# ---------- Store ----------

@app.get("/api/store")
def get_store(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        coin_row = conn.execute(
            "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'coin'", (user_id,)
        ).fetchone()
        owned_keys = {
            r["item"] for r in conn.execute(
                "SELECT item FROM poke_items WHERE user_id = ? AND qty > 0", (user_id,)
            ) if r["item"] in battle_store.KEY_ITEMS
        }
    return {
        "items": [
            {"key": key, "label": cfg["label"], "price": cfg["price"], "icon": item_icon(key),
             "key_item": key in battle_store.KEY_ITEMS, "owned": key in owned_keys,
             "held_item": key in HELD_ITEM_KEYS,
             "description": be.HELD_ITEMS[key]["description"] if key in HELD_ITEM_KEYS else None}
            for key, cfg in STORE_ITEMS.items()
        ],
        "coins": coin_row["qty"] if coin_row else 0,
    }


class BuyRequest(BaseModel):
    item: str
    quantity: int = 1


@app.post("/api/store/buy")
def buy_item(body: BuyRequest, user_id: int = Depends(get_current_user_id)):
    if body.item not in STORE_ITEMS:
        raise HTTPException(400, "Unknown item")
    if body.quantity <= 0:
        raise HTTPException(400, "Quantity must be at least 1")

    item_cfg = STORE_ITEMS[body.item]
    is_key_item = body.item in battle_store.KEY_ITEMS
    if is_key_item and body.quantity != 1:
        raise HTTPException(400, f"The {item_cfg['label']} is a key item — you only ever need one.")
    total_cost = item_cfg["price"] * body.quantity

    with db() as conn:
        if is_key_item:
            have = conn.execute(
                "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, body.item)
            ).fetchone()
            if have and have["qty"] > 0:
                raise HTTPException(400, f"You already have the {item_cfg['label']} — it's a key item you keep forever.")
        coin_row = conn.execute(
            "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'coin'", (user_id,)
        ).fetchone()
        balance = coin_row["qty"] if coin_row else 0
        if balance < total_cost:
            raise HTTPException(
                400,
                f"You need {total_cost} coins for {body.quantity}x {item_cfg['label']}, "
                f"but you only have {balance}.",
            )
        add_item_sql(conn, user_id, "coin", -total_cost)
        add_item_sql(conn, user_id, body.item, body.quantity)

    return {"ok": True, "coins_left": balance - total_cost}


# ---------- Per-species moveset/ability loadout ----------
# Keyed by (user_id, dex_id) rather than per-catch: every individual of a
# species you own currently shares one loadout, since there's no battle
# system yet to make per-individual loadouts matter.

# Ability descriptions for the team page's tooltips (scripts/fetch_ability_data.py).
with open(os.path.join(os.path.dirname(__file__), "data", "abilities.json"), encoding="utf-8") as _f:
    ABILITY_DESCRIPTIONS: dict[str, str] = json.load(_f)


def ability_info(name: str) -> dict:
    return {
        "name": name, "label": format_ability_name(name),
        "description": ABILITY_DESCRIPTIONS.get(name),
        "in_battle": name in be.BATTLE_ABILITIES,
        "no_battle_effect": name in be.NO_BATTLE_EFFECT_ABILITIES,
    }


def resolve_pokemon_config(conn: sqlite3.Connection, user_id: int, dex_id: int) -> dict:
    mon = POKEDEX.get(dex_id, {})
    pool = mon.get("moves", [])
    abilities = mon.get("abilities", [])
    default_moves = [m["name"] for m in pool[:4]]
    default_ability = abilities[0]["name"] if abilities else None

    row = conn.execute(
        "SELECT moves, ability FROM poke_pokemon_config WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
    ).fetchone()
    moves = json.loads(row["moves"]) if row and row["moves"] else default_moves
    ability_raw = row["ability"] if row and row["ability"] else default_ability

    pool_by_name = {m["name"]: m for m in pool}
    resolved_moves = [pool_by_name[name] for name in moves if name in pool_by_name]

    return {
        "moves": resolved_moves,
        "move_pool": pool,
        "ability": format_ability_name(ability_raw) if ability_raw else None,
        "ability_raw": ability_raw,
        "abilities": [ability_info(a["name"]) for a in abilities],
        "ability_info": ability_info(ability_raw) if ability_raw else None,
        **resolve_forme(conn, user_id, dex_id),
        **resolve_held_item(conn, user_id, dex_id),
    }


def held_item_entry(key: str) -> dict:
    return {"key": key, "label": be.HELD_ITEMS[key]["label"], "description": be.HELD_ITEMS[key]["description"],
            "icon": item_icon(key)}


def resolve_held_item(conn: sqlite3.Connection, user_id: int, dex_id: int) -> dict:
    """The species' held item plus the held items this trainer owns, each
    with how many copies are still free (not held by another species)."""
    current = battle_store.get_held_item(conn, user_id, dex_id)
    owned = {r["item"]: r["qty"] for r in conn.execute(
        "SELECT item, qty FROM poke_items WHERE user_id = ? AND qty > 0", (user_id,)) if r["item"] in be.HELD_ITEMS}
    held_elsewhere: dict[str, int] = {}
    for r in conn.execute(
            "SELECT held_item FROM poke_pokemon_config WHERE user_id = ? AND dex_id != ? AND held_item IS NOT NULL",
            (user_id, dex_id)):
        held_elsewhere[r[0]] = held_elsewhere.get(r[0], 0) + 1
    return {
        "held_item": held_item_entry(current) if current else None,
        "held_item_options": [
            {**held_item_entry(k), "qty": q, "free": q - held_elsewhere.get(k, 0)}
            for k, q in owned.items()
        ],
    }


def resolve_forme(conn: sqlite3.Connection, user_id: int, dex_id: int) -> dict:
    """Alternate-Forme info for species that have them (Deoxys): the Forme
    it battles in (whose art/stats the team pages show) and the choices."""
    forms = battle_store.forms_for(dex_id)
    if not forms:
        return {"forms": []}
    current = battle_store.form_dex_id_sql(conn, user_id, dex_id)
    item_key = battle_store.FORM_CHANGE_ITEMS[dex_id]
    have = conn.execute(
        "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, item_key)
    ).fetchone()
    return {
        "forms": [
            {**f, "artwork": POKEDEX[f["dex_id"]]["artwork"], "base_stats": POKEDEX[f["dex_id"]]["base_stats"]}
            for f in forms
        ],
        "form": next(f["key"] for f in forms if f["dex_id"] == current),
        "form_label": next(f["label"] for f in forms if f["dex_id"] == current),
        "artwork": POKEDEX[current]["artwork"],
        "form_item": {
            "key": item_key, "label": STORE_ITEMS[item_key]["label"], "price": STORE_ITEMS[item_key]["price"],
            "icon": item_icon(item_key), "owned": bool(have and have["qty"] > 0),
        },
    }


def resolved_base_stats(mon: dict, ivs: dict | None = None) -> list[dict]:
    """ivs: that specific individual's real 0-31-per-stat values. Omit to show
    the species' max-potential (31-IV) stats — used for a species you don't
    own yet, where no individual exists to draw real IVs from."""
    ivs = ivs or be.MAX_IVS
    base_stats = mon.get("base_stats", {})
    labels = [
        ("hp", "HP", True), ("attack", "Attack", False), ("defense", "Defense", False),
        ("sp_attack", "Sp. Attack", False), ("sp_defense", "Sp. Defense", False), ("speed", "Speed", False),
    ]
    return [
        {
            "key": key, "label": label,
            "base": base_stats.get(key, 0),
            "iv": ivs.get(key, 31),
            "at_level_100": stat_at_level_100(base_stats.get(key, 0), ivs.get(key, 31), is_hp),
        }
        for key, label, is_hp in labels
    ]


@app.get("/api/pokemon-config/{dex_id}")
def get_pokemon_config(dex_id: int, user_id: int = Depends(get_current_user_id)):
    mon = POKEDEX.get(dex_id)
    if not mon:
        raise HTTPException(404, "Unknown species")
    with db() as conn:
        owned = conn.execute(
            "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
        ).fetchone()[0]
        if owned < 1:
            raise HTTPException(404, "You don't own this Pokémon")
        config = resolve_pokemon_config(conn, user_id, dex_id)
        battle_mon = POKEDEX[battle_store.form_dex_id_sql(conn, user_id, dex_id)]
    ivs = battle_store.get_best_ivs_for_species(user_id, dex_id)
    return {
        "dex_id": dex_id,
        "name": mon["name"],
        "base_stats": resolved_base_stats(battle_mon, ivs),
        **config,
    }


class HeldItemRequest(BaseModel):
    item: str | None = None


@app.post("/api/pokemon-config/{dex_id}/item")
def set_pokemon_held_item(dex_id: int, body: HeldItemRequest, user_id: int = Depends(get_current_user_id)):
    if dex_id not in POKEDEX:
        raise HTTPException(404, "Unknown species")
    with db() as conn:
        owned = conn.execute(
            "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
        ).fetchone()[0]
        if owned < 1:
            raise HTTPException(404, "You don't own this Pokémon")
        problem = battle_store.held_item_problem(conn, user_id, dex_id, body.item)
    if problem:
        raise HTTPException(400, problem)
    battle_store.set_held_item(user_id, dex_id, body.item)
    return {"ok": True}


class FormeRequest(BaseModel):
    form: str


@app.post("/api/pokemon-config/{dex_id}/form")
def set_pokemon_forme(dex_id: int, body: FormeRequest, user_id: int = Depends(get_current_user_id)):
    forms = battle_store.forms_for(dex_id)
    if not forms:
        raise HTTPException(400, "This Pokémon doesn't have other Formes")
    if body.form not in {f["key"] for f in forms}:
        raise HTTPException(400, "Unknown Forme")
    item_key = battle_store.FORM_CHANGE_ITEMS[dex_id]
    with db() as conn:
        owned = conn.execute(
            "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
        ).fetchone()[0]
        if owned < 1:
            raise HTTPException(404, "You don't own this Pokémon")
        have = conn.execute(
            "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, item_key)
        ).fetchone()
    if not have or have["qty"] < 1:
        raise HTTPException(400, f"You need a {STORE_ITEMS[item_key]['label']} to change Formes — get one at the Store.")
    battle_store.set_form(user_id, dex_id, body.form)
    return {"ok": True}


class PokemonConfigRequest(BaseModel):
    moves: list[str]
    ability: str


@app.post("/api/pokemon-config/{dex_id}")
def set_pokemon_config(dex_id: int, body: PokemonConfigRequest, user_id: int = Depends(get_current_user_id)):
    mon = POKEDEX.get(dex_id)
    if not mon:
        raise HTTPException(404, "Unknown species")
    if len(body.moves) > 4:
        raise HTTPException(400, "You can only pick up to 4 moves")

    valid_moves = {m["name"] for m in mon.get("moves", [])}
    for name in body.moves:
        if name not in valid_moves:
            raise HTTPException(400, f"{name} isn't in this Pokémon's move pool")

    valid_abilities = {a["name"] for a in mon.get("abilities", [])}
    if body.ability not in valid_abilities:
        raise HTTPException(400, "Unknown ability for this species")

    with db() as conn:
        owned = conn.execute(
            "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
        ).fetchone()[0]
        if owned < 1:
            raise HTTPException(404, "You don't own this Pokémon")
        conn.execute(
            "INSERT INTO poke_pokemon_config (user_id, dex_id, moves, ability) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, dex_id) DO UPDATE SET moves = excluded.moves, ability = excluded.ability",
            (user_id, dex_id, json.dumps(body.moves), body.ability),
        )
    return {"ok": True}


# ---------- Collection (individual catches) ----------

def build_evolution_info(dex_id: int, user_id: int, conn: sqlite3.Connection) -> dict | None:
    mon = POKEDEX.get(dex_id)
    if not mon:
        return None
    options = mon.get("evolves_to") or []
    if not options:
        return None

    family_id = mon.get("family_id", dex_id)
    family_name = POKEDEX.get(family_id, mon)["name"]
    candy_key = f"famcandy_{family_id}"
    candy_row = conn.execute(
        "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, candy_key)
    ).fetchone()
    have_candy = candy_row["qty"] if candy_row else 0

    candidates = []
    for opt in options:
        target = POKEDEX.get(opt["id"])
        if not target:
            continue
        item_needed = opt.get("item")
        has_item = True
        if item_needed is not None:
            item_row = conn.execute(
                "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, item_needed)
            ).fetchone()
            has_item = (item_row["qty"] if item_row else 0) > 0
        candidates.append({
            "dex_id": target["id"],
            "name": target["name"],
            "artwork": target.get("artwork"),
            "item_needed": ITEM_LABELS.get(item_needed, item_needed) if item_needed else None,
            "has_item": has_item,
        })

    return {
        "family_candy_label": f"{family_name} Candy",
        "have_candy": have_candy,
        "needed_candy": EVOLUTION_CANDY_COST,
        "ready": have_candy >= EVOLUTION_CANDY_COST and any(c["has_item"] for c in candidates),
        "candidates": candidates,
    }


def build_collection_summary(conn: sqlite3.Connection, target_id: int) -> list[dict]:
    """One card per species a trainer owns, not one per individual catch — a
    nicknamed instance is shown preferentially since it's the one they've
    personalized, otherwise the most recently caught one represents the group."""
    rows = conn.execute(
        "SELECT id, dex_id, nickname, is_shiny, caught_at, "
        "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed "
        "FROM poke_collection WHERE user_id = ? ORDER BY caught_at DESC",
        (target_id,),
    ).fetchall()

    groups: dict[int, list] = {}
    for r in rows:
        groups.setdefault(r["dex_id"], []).append(r)

    result = []
    for dex_id, group in groups.items():
        mon = POKEDEX.get(dex_id, {})
        representative = next((r for r in group if r["nickname"]), group[0])
        best_iv_total = max(sum(battle_store.ivs_from_collection_row(r).values()) for r in group)
        result.append({
            "id": representative["id"],
            "dex_id": dex_id,
            "name": mon.get("name", f"#{dex_id}"),
            "nickname": representative["nickname"],
            "types": mon.get("types", []),
            "artwork": (
                (mon.get("artwork_shiny") or mon.get("artwork")) if representative["is_shiny"] else mon.get("artwork")
            ),
            "is_shiny": bool(representative["is_shiny"]),
            "caught_at": representative["caught_at"],
            "count": len(group),
            "best_iv_percent": round(best_iv_total / (31 * 6) * 100, 1),
        })

    result.sort(key=lambda m: m["caught_at"], reverse=True)
    return result


def build_collection_by_species(conn: sqlite3.Connection, target_id: int, dex_id: int) -> list[dict]:
    """Every individual a trainer owns of one species — unlike the summary
    (species-deduped), this is how a picker (e.g. trading) lets you choose
    exactly which of, say, three Charmander to act on, since IVs make them
    meaningfully different now."""
    rows = conn.execute(
        "SELECT id, dex_id, nickname, is_shiny, caught_at, "
        "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed "
        "FROM poke_collection WHERE user_id = ? AND dex_id = ? ORDER BY caught_at DESC",
        (target_id, dex_id),
    ).fetchall()
    mon = POKEDEX.get(dex_id, {})
    result = []
    for r in rows:
        ivs = battle_store.ivs_from_collection_row(r)
        result.append({
            "id": r["id"],
            "dex_id": dex_id,
            "name": mon.get("name", f"#{dex_id}"),
            "nickname": r["nickname"],
            "artwork": (mon.get("artwork_shiny") or mon.get("artwork")) if r["is_shiny"] else mon.get("artwork"),
            "is_shiny": bool(r["is_shiny"]),
            "caught_at": r["caught_at"],
            "iv_percent": round(sum(ivs.values()) / (31 * 6) * 100, 1),
        })
    return result


def build_collection_individuals(conn: sqlite3.Connection, target_id: int) -> list[dict]:
    """Every Pokémon a trainer owns, one entry per catch (two Charmander are
    two cards: their IVs, shininess and nicknames differ), newest first."""
    rows = conn.execute(
        "SELECT id, dex_id, nickname, is_shiny, caught_at, "
        "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed "
        "FROM poke_collection WHERE user_id = ? ORDER BY caught_at DESC, id DESC",
        (target_id,),
    ).fetchall()
    out = []
    for r in rows:
        mon = POKEDEX.get(r["dex_id"], {})
        ivs = battle_store.ivs_from_collection_row(r)
        out.append({
            "id": r["id"],
            "dex_id": r["dex_id"],
            "name": mon.get("name", f"#{r['dex_id']}"),
            "nickname": r["nickname"],
            "types": mon.get("types", []),
            "artwork": (mon.get("artwork_shiny") or mon.get("artwork")) if r["is_shiny"] else mon.get("artwork"),
            "is_shiny": bool(r["is_shiny"]),
            "caught_at": r["caught_at"],
            "iv_percent": round(sum(ivs.values()) / (31 * 6) * 100, 1),
            "tradeable": not battle_store.is_mega(r["dex_id"]),
        })
    return out


@app.get("/api/collection-individuals")
def get_collection_individuals(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        return build_collection_individuals(conn, user_id)


@app.get("/api/collection")
def get_collection(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        return build_collection_summary(conn, user_id)


@app.get("/api/collection/by-species/{dex_id}")
def get_collection_by_species(dex_id: int, user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        return build_collection_by_species(conn, user_id, dex_id)


@app.get("/api/trainer/{target_id}/collection")
def get_trainer_collection(target_id: int, user_id: int = Depends(get_current_user_id)):
    """Lets one trainer browse another's species — used by the trade
    proposal builder's 'what do they have' picker."""
    with db() as conn:
        trainer = conn.execute("SELECT 1 FROM poke_trainers WHERE user_id = ?", (target_id,)).fetchone()
        if not trainer:
            raise HTTPException(404, "Trainer not found")
        return build_collection_summary(conn, target_id)


@app.get("/api/trainer/{target_id}/collection-individuals")
def get_trainer_collection_individuals(target_id: int, user_id: int = Depends(get_current_user_id)):
    """Another trainer's Pokémon one per catch, for the trade builder's
    'what do they have' picker (duplicates differ by IVs)."""
    with db() as conn:
        trainer = conn.execute("SELECT 1 FROM poke_trainers WHERE user_id = ?", (target_id,)).fetchone()
        if not trainer:
            raise HTTPException(404, "Trainer not found")
        return build_collection_individuals(conn, target_id)


@app.get("/api/trainer/{target_id}/collection/by-species/{dex_id}")
def get_trainer_collection_by_species(target_id: int, dex_id: int, user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        trainer = conn.execute("SELECT 1 FROM poke_trainers WHERE user_id = ?", (target_id,)).fetchone()
        if not trainer:
            raise HTTPException(404, "Trainer not found")
        return build_collection_by_species(conn, target_id, dex_id)


@app.get("/api/collection/{catch_id}")
def get_collection_detail(catch_id: int, user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        row = conn.execute(
            "SELECT id, dex_id, nickname, is_shiny, caught_at, "
            "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed "
            "FROM poke_collection WHERE id = ? AND user_id = ?",
            (catch_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Pokémon not found")
        mon = POKEDEX.get(row["dex_id"])
        if not mon:
            raise HTTPException(404, "Unknown species")
        count = conn.execute(
            "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, row["dex_id"])
        ).fetchone()[0]
        evolution = build_evolution_info(row["dex_id"], user_id, conn)
        config = resolve_pokemon_config(conn, user_id, row["dex_id"])

    ivs = battle_store.ivs_from_collection_row(row)
    return {
        "id": row["id"],
        "dex_id": row["dex_id"],
        "name": mon["name"],
        "nickname": row["nickname"],
        "types": mon.get("types", []),
        "category": mon.get("category"),
        "artwork": (mon.get("artwork_shiny") or mon.get("artwork")) if row["is_shiny"] else mon.get("artwork"),
        "is_shiny": bool(row["is_shiny"]),
        "caught_at": row["caught_at"],
        "count": count,
        "base_stats": resolved_base_stats(mon, ivs),
        "iv_percent": round(sum(ivs.values()) / (31 * 6) * 100, 1),
        "evolution": evolution,
        "transfer_candy": transfer_candy_for(row["dex_id"], bool(row["is_shiny"])),
        "family_name": POKEDEX.get(mon.get("family_id", row["dex_id"]), mon)["name"],
        "can_transfer": not battle_store.is_mega(row["dex_id"]) and row["dex_id"] not in REWARD_ONLY_DEX_IDS,
        **config,
    }


class NicknameRequest(BaseModel):
    nickname: str | None


@app.post("/api/collection/{catch_id}/nickname")
def set_nickname(catch_id: int, body: NicknameRequest, user_id: int = Depends(get_current_user_id)):
    nickname = (body.nickname or "").strip()[:32] or None
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM poke_collection WHERE id = ? AND user_id = ?", (catch_id, user_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Pokémon not found")
        conn.execute("UPDATE poke_collection SET nickname = ? WHERE id = ?", (nickname, catch_id))
    return {"ok": True, "nickname": nickname}


# ---------- Transferring Pokémon for candy ----------
# Send away Pokémon you don't want (e.g. duplicates) for their family's
# candy. Your Pokédex keeps the species registered either way.
TRANSFER_CANDY_BASE = 3
TRANSFER_CANDY_EVOLVED = 5  # anything past its family's first stage
TRANSFER_SHINY_MULTIPLIER = 2
TRANSFER_MAX_PER_REQUEST = 200


def transfer_candy_for(dex_id: int, is_shiny: bool) -> int:
    mon = POKEDEX.get(dex_id, {})
    base = TRANSFER_CANDY_EVOLVED if mon.get("family_id", dex_id) != dex_id else TRANSFER_CANDY_BASE
    return base * (TRANSFER_SHINY_MULTIPLIER if is_shiny else 1)


class TransferRequest(BaseModel):
    catch_ids: list[int]


@app.post("/api/collection/transfer")
def transfer_pokemon(body: TransferRequest, user_id: int = Depends(get_current_user_id)):
    ids = list(dict.fromkeys(body.catch_ids))
    if not ids:
        raise HTTPException(400, "Pick at least one Pokémon to transfer")
    if len(ids) > TRANSFER_MAX_PER_REQUEST:
        raise HTTPException(400, f"You can transfer up to {TRANSFER_MAX_PER_REQUEST} at a time")
    marks = ",".join("?" * len(ids))
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, dex_id, is_shiny, nickname FROM poke_collection WHERE user_id = ? AND id IN ({marks})",
            (user_id, *ids),
        ).fetchall()
        if len(rows) != len(ids):
            raise HTTPException(404, "Some of those Pokémon aren't in your collection")
        name = lambda r: r["nickname"] or POKEDEX.get(r["dex_id"], {}).get("name", f"#{r['dex_id']}")  # noqa: E731

        for r in rows:
            if battle_store.is_mega(r["dex_id"]) or r["dex_id"] in REWARD_ONLY_DEX_IDS:
                raise HTTPException(400, f"{name(r)} is a special reward and can't be transferred")
        traded = {x[0] for x in conn.execute(
            f"SELECT side_a_catch_id FROM poke_trades WHERE status IN ('pending', 'active') AND side_a_catch_id IN ({marks}) "
            f"UNION SELECT side_b_catch_id FROM poke_trades WHERE status IN ('pending', 'active') AND side_b_catch_id IN ({marks})",
            (*ids, *ids))}
        for r in rows:
            if r["id"] in traded:
                raise HTTPException(400, f"{name(r)} is offered in an open trade — cancel that first")

        # Species you'd have none left of can't be on your team or your favorite.
        removing: dict[int, int] = {}
        for r in rows:
            removing[r["dex_id"]] = removing.get(r["dex_id"], 0) + 1
        team = {x[0] for x in conn.execute("SELECT dex_id FROM poke_team WHERE user_id = ?", (user_id,))}
        fav = conn.execute("SELECT favorite_dex_id FROM poke_trainers WHERE user_id = ?", (user_id,)).fetchone()
        fav = fav[0] if fav else None
        emptied = []
        for dex_id, n in removing.items():
            owned = conn.execute(
                "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
            ).fetchone()[0]
            if owned - n > 0:
                continue
            species = POKEDEX.get(dex_id, {}).get("name", f"#{dex_id}")
            if dex_id in team:
                raise HTTPException(400, f"That's your last {species} and it's on your team — take it off your team first")
            if dex_id == fav:
                raise HTTPException(400, f"That's your last {species} and it's your favorite — change your favorite first")
            emptied.append(dex_id)

        candy: dict[int, int] = {}
        for r in rows:
            family_id = POKEDEX.get(r["dex_id"], {}).get("family_id", r["dex_id"])
            candy[family_id] = candy.get(family_id, 0) + transfer_candy_for(r["dex_id"], bool(r["is_shiny"]))
        conn.execute(f"DELETE FROM poke_collection WHERE user_id = ? AND id IN ({marks})", (user_id, *ids))
        for family_id, qty in candy.items():
            add_item_sql(conn, user_id, f"famcandy_{family_id}", qty)
        if emptied:
            # A held item on a species you no longer own goes back in the bag.
            battle_store.ensure_form_column(conn)
            conn.execute(
                f"UPDATE poke_pokemon_config SET held_item = NULL WHERE user_id = ? AND dex_id IN ({','.join('?' * len(emptied))})",
                (user_id, *emptied),
            )
    return {
        "ok": True,
        "transferred": len(rows),
        "candy": [{"family": POKEDEX.get(f, {}).get("name", f"#{f}"), "qty": q} for f, q in candy.items()],
    }


class ConvertCandyRequest(BaseModel):
    amount: int


@app.post("/api/collection/{catch_id}/convert-candy")
def convert_candy(catch_id: int, body: ConvertCandyRequest, user_id: int = Depends(get_current_user_id)):
    """Converts generic Rare Candy into this Pokémon's family-specific candy, 1:1."""
    if body.amount <= 0:
        raise HTTPException(400, "Amount must be at least 1")

    with db() as conn:
        row = conn.execute(
            "SELECT dex_id FROM poke_collection WHERE id = ? AND user_id = ?", (catch_id, user_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Pokémon not found")
        mon = POKEDEX.get(row["dex_id"], {})
        family_id = mon.get("family_id", row["dex_id"])

        rare_row = conn.execute(
            "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'candy'", (user_id,)
        ).fetchone()
        have_rare = rare_row["qty"] if rare_row else 0
        if have_rare < body.amount:
            raise HTTPException(400, f"You only have {have_rare} Rare Candy")

        add_item_sql(conn, user_id, "candy", -body.amount)
        add_item_sql(conn, user_id, f"famcandy_{family_id}", body.amount)

    return {"ok": True}


class EvolveRequest(BaseModel):
    target_dex_id: int | None = None  # required only when multiple evolutions are available


@app.post("/api/collection/{catch_id}/evolve")
def evolve_pokemon(catch_id: int, body: EvolveRequest, user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        row = conn.execute(
            "SELECT id, dex_id FROM poke_collection WHERE id = ? AND user_id = ?", (catch_id, user_id)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Pokémon not found")
        mon = POKEDEX.get(row["dex_id"])
        if not mon:
            raise HTTPException(404, "Unknown species")

        options = mon.get("evolves_to") or []
        if not options:
            raise HTTPException(400, f"{mon['name']} doesn't evolve any further")

        family_id = mon.get("family_id", row["dex_id"])
        candy_key = f"famcandy_{family_id}"
        candy_row = conn.execute(
            "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, candy_key)
        ).fetchone()
        have_candy = candy_row["qty"] if candy_row else 0
        if have_candy < EVOLUTION_CANDY_COST:
            raise HTTPException(
                400, f"You need {EVOLUTION_CANDY_COST} {mon['name']} Candy — you have {have_candy}"
            )

        candidates = []
        for opt in options:
            target = POKEDEX.get(opt["id"])
            if not target:
                continue
            item_needed = opt.get("item")
            has_item = True
            if item_needed:
                item_row = conn.execute(
                    "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, item_needed)
                ).fetchone()
                has_item = (item_row["qty"] if item_row else 0) > 0
            candidates.append({"target": target, "item": item_needed, "has_item": has_item})

        ready = [c for c in candidates if c["has_item"]]
        if not ready:
            raise HTTPException(400, "You don't have the item needed to evolve into any available form")

        if body.target_dex_id is not None:
            chosen = next((c for c in ready if c["target"]["id"] == body.target_dex_id), None)
            if not chosen:
                raise HTTPException(400, "That evolution isn't available right now")
        elif len(ready) == 1:
            chosen = ready[0]
        else:
            raise HTTPException(400, "Multiple evolutions are available — specify target_dex_id")

        add_item_sql(conn, user_id, candy_key, -EVOLUTION_CANDY_COST)
        if chosen["item"]:
            add_item_sql(conn, user_id, chosen["item"], -1)
        conn.execute("UPDATE poke_collection SET dex_id = ? WHERE id = ?", (chosen["target"]["id"], catch_id))
        # Evolving into a species registers it in the Pokédex permanently, same
        # as catching it would — the species evolved FROM stays registered too
        # (that row was already written when it was originally caught).
        conn.execute(
            "INSERT OR IGNORE INTO poke_dex_seen (user_id, dex_id, first_caught_at) VALUES (?, ?, ?)",
            (user_id, chosen["target"]["id"], datetime.now(timezone.utc).isoformat()),
        )
        add_item_sql(conn, user_id, "stat_evolutions", 1)
        add_item_sql(conn, user_id, "xp", XP_PER_EVOLUTION)
        check_and_unlock_achievements(conn, user_id)

    return {"ok": True, "new_dex_id": chosen["target"]["id"], "new_name": chosen["target"]["name"]}


# ---------- Team ----------

class TeamRequest(BaseModel):
    dex_ids: list[int]


@app.get("/api/team")
def get_team(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        rows = conn.execute(
            "SELECT dex_id FROM poke_team WHERE user_id = ? ORDER BY slot", (user_id,)
        ).fetchall()
        result = []
        for r in rows:
            dex_id = r["dex_id"]
            mon = POKEDEX.get(dex_id, {})
            battle_mon = POKEDEX.get(battle_store.form_dex_id_sql(conn, user_id, dex_id), mon)
            ivs = battle_store.get_best_ivs_for_species(user_id, dex_id)
            result.append({
                "dex_id": dex_id,
                "name": mon.get("name", f"#{dex_id}"),
                "artwork": mon.get("artwork"),
                "types": mon.get("types", []),
                "base_stats": resolved_base_stats(battle_mon, ivs),
                **resolve_pokemon_config(conn, user_id, dex_id),
            })
    return result


@app.post("/api/team")
def set_team(body: TeamRequest, user_id: int = Depends(get_current_user_id)):
    if len(body.dex_ids) > 6:
        raise HTTPException(400, "A team can have at most 6 Pokémon")
    if len(set(body.dex_ids)) != len(body.dex_ids):
        raise HTTPException(400, "Each Pokémon can only be on your team once")

    with db() as conn:
        for dex_id in body.dex_ids:
            owned = conn.execute(
                "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
            ).fetchone()[0]
            if owned < 1:
                name = POKEDEX.get(dex_id, {}).get("name", f"#{dex_id}")
                raise HTTPException(400, f"You don't own a {name}")

        conn.execute("DELETE FROM poke_team WHERE user_id = ?", (user_id,))
        for slot, dex_id in enumerate(body.dex_ids):
            conn.execute(
                "INSERT INTO poke_team (user_id, slot, dex_id) VALUES (?, ?, ?)", (user_id, slot, dex_id)
            )

    return {"ok": True}


# ---------- Battles ----------
# All actual battling (choosing moves, switching, forfeiting) is submitted
# here from the web battle UI — the bot only ever creates a battle (and, for
# gym/trainer battles, its NPC side) then posts a link. Every mutation below
# broadcasts the fresh state to that battle's connected WebSocket viewers;
# battle_room.js also polls as a fallback for as long as no socket is open
# (e.g. before the reverse proxy in front of this API is configured to pass
# WebSocket upgrades through).

def decode_token_user_id(token: str | None) -> int | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return int(payload["sub"])
    except jwt.PyJWTError:
        return None


@app.get("/api/battles")
def list_battles(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        live_rows = conn.execute(
            "SELECT * FROM poke_battles WHERE status IN ('pending', 'active', 'awaiting_forced_switch') "
            "ORDER BY updated_at DESC"
        ).fetchall()
        recent_rows = conn.execute(
            "SELECT * FROM poke_battles WHERE status = 'finished' ORDER BY finished_at DESC LIMIT 20"
        ).fetchall()
        mine_rows = conn.execute(
            "SELECT * FROM poke_battles WHERE side_a_user_id = ? OR side_b_user_id = ? "
            "ORDER BY updated_at DESC LIMIT 30",
            (user_id, user_id),
        ).fetchall()
        return {
            "live": [battle_store.summarize_battle(conn, r, user_id) for r in live_rows],
            "recent": [battle_store.summarize_battle(conn, r, user_id) for r in recent_rows],
            "mine": [battle_store.summarize_battle(conn, r, user_id) for r in mine_rows],
        }


@app.get("/api/battles/{battle_id}")
def get_battle_detail(battle_id: int, user_id: int = Depends(get_current_user_id)):
    payload = battle_store.serialize_battle_detail(battle_id, viewer_user_id=user_id)
    if not payload:
        raise HTTPException(404, "Battle not found")
    return payload


@app.post("/api/battles/{battle_id}/accept")
async def accept_battle(battle_id: int, user_id: int = Depends(get_current_user_id)):
    row = battle_store.get_battle_row(battle_id)
    if not row:
        raise HTTPException(404, "Battle not found")
    if row["side_b_user_id"] != user_id:
        raise HTTPException(403, "This challenge isn't yours to accept")
    ok, battle, row, error = await asyncio.to_thread(battle_store.accept_challenge, battle_id)
    if not ok:
        raise HTTPException(400, error or "Couldn't accept this challenge")
    await battle_connections.broadcast(battle_id)
    return battle_store.serialize_battle_detail(battle_id, viewer_user_id=user_id)


@app.post("/api/battles/{battle_id}/decline")
async def decline_battle(battle_id: int, user_id: int = Depends(get_current_user_id)):
    row = battle_store.get_battle_row(battle_id)
    if not row:
        raise HTTPException(404, "Battle not found")
    if user_id not in (row["side_a_user_id"], row["side_b_user_id"]):
        raise HTTPException(403, "This challenge isn't yours to decline")
    await asyncio.to_thread(battle_store.decline_challenge, battle_id)
    await battle_connections.broadcast(battle_id)
    return {"ok": True}


class BattleActionRequest(BaseModel):
    kind: str  # "move" | "switch"
    move_index: int | None = None
    switch_to_index: int | None = None
    mega: int | None = None  # dex id of the Mega to evolve into before moving


@app.post("/api/battles/{battle_id}/action")
async def submit_battle_action(battle_id: int, body: BattleActionRequest, user_id: int = Depends(get_current_user_id)):
    row = battle_store.get_battle_row(battle_id)
    if not row:
        raise HTTPException(404, "Battle not found")
    side = "A" if row["side_a_user_id"] == user_id else ("B" if row["side_b_user_id"] == user_id else None)
    if side is None:
        raise HTTPException(403, "You're not a participant in this battle")

    def _do():
        battle, battle_row = battle_store.load_battle_state(battle_id)
        if not battle or battle.status != "active":
            return "This battle isn't active right now.", None
        if battle_store.get_pending_action(battle_id, side, battle.turn_number) is not None:
            return "You've already locked in your move this turn.", None

        mega = body.mega if body.kind == "move" else None
        if mega and mega not in {m["id"] for m in be.mega_options(battle.side(side))}:
            return "That Pokémon can't Mega Evolve right now.", None
        action = be.Action(kind=body.kind, side=side, move_index=body.move_index, switch_to_index=body.switch_to_index,
                           mega=mega)
        legal = be.legal_actions(battle, side)
        if body.kind == "move" and legal["usable_move_indices"] and body.move_index not in legal["usable_move_indices"]:
            return "That move can't be used right now.", None
        if body.kind == "switch" and body.switch_to_index not in legal["switchable_indices"]:
            return "That Pokémon can't be switched in.", None
        battle_store.record_pending_action(battle_id, side, battle.turn_number, action)

        other_side = "B" if side == "A" else "A"
        other_controller = battle.side(other_side).controller
        if other_controller == "npc":
            other_action = battle_store.npc_pick_action(battle, other_side, row)
        else:
            other_action = battle_store.get_pending_action(battle_id, other_side, battle.turn_number)
            if other_action is None:
                return None, None  # recorded, waiting on the opponent

        action_a = action if side == "A" else other_action
        action_b = other_action if side == "A" else action
        battle, battle_row, _events, _reward = battle_store.resolve_battle_turn(battle_id, action_a, action_b)
        return None, (battle, battle_row) if battle.status == "finished" else None

    error, finished = await asyncio.to_thread(_do)
    if error:
        raise HTTPException(400, error)
    if finished:
        unlock_achievements_for_battle_winner(*finished)
    await battle_connections.broadcast(battle_id)
    return battle_store.serialize_battle_detail(battle_id, viewer_user_id=user_id)


class ForcedSwitchRequest(BaseModel):
    team_index: int


@app.post("/api/battles/{battle_id}/forced-switch")
async def submit_forced_switch(battle_id: int, body: ForcedSwitchRequest, user_id: int = Depends(get_current_user_id)):
    row = battle_store.get_battle_row(battle_id)
    if not row:
        raise HTTPException(404, "Battle not found")
    side = "A" if row["side_a_user_id"] == user_id else ("B" if row["side_b_user_id"] == user_id else None)
    if side is None:
        raise HTTPException(403, "You're not a participant in this battle")

    battle, battle_row, events = await asyncio.to_thread(battle_store.handle_forced_switch, battle_id, side, body.team_index)
    if not events:
        raise HTTPException(400, "You don't need to switch right now")
    await battle_connections.broadcast(battle_id)
    return battle_store.serialize_battle_detail(battle_id, viewer_user_id=user_id)


@app.post("/api/battles/{battle_id}/forfeit")
async def forfeit_battle(battle_id: int, user_id: int = Depends(get_current_user_id)):
    row = battle_store.get_battle_row(battle_id)
    if not row:
        raise HTTPException(404, "Battle not found")
    side = "A" if row["side_a_user_id"] == user_id else ("B" if row["side_b_user_id"] == user_id else None)
    if side is None:
        raise HTTPException(403, "You're not a participant in this battle")

    battle, battle_row, _reward = await asyncio.to_thread(battle_store.apply_forfeit, battle_id, side)
    if battle and battle.status == "finished":
        unlock_achievements_for_battle_winner(battle, battle_row)
    await battle_connections.broadcast(battle_id)
    return battle_store.serialize_battle_detail(battle_id, viewer_user_id=user_id)


class CheerRequest(BaseModel):
    side: str


@app.post("/api/battles/{battle_id}/cheer")
async def cheer_battle(battle_id: int, body: CheerRequest, user_id: int = Depends(get_current_user_id)):
    """A spectator cheers for side A or B; everyone watching sees it live."""
    problem = await asyncio.to_thread(battle_store.add_cheer, battle_id, user_id, body.side)
    if problem:
        raise HTTPException(429 if "moment" in problem else 400, problem)
    await battle_connections.broadcast(battle_id)
    return {"ok": True}


@app.websocket("/ws/battles/{battle_id}")
async def battle_websocket(websocket: WebSocket, battle_id: int):
    viewer_user_id = decode_token_user_id(websocket.query_params.get("token"))
    await battle_connections.connect(battle_id, websocket, viewer_user_id)
    try:
        payload = await asyncio.to_thread(battle_store.serialize_battle_detail, battle_id, viewer_user_id)
        if payload is None:
            await websocket.close(code=4404)
            return
        await websocket.send_json(payload)
        while True:
            await websocket.receive_text()  # only used to detect disconnect; actions go via POST
    except WebSocketDisconnect:
        pass
    finally:
        battle_connections.disconnect(battle_id, websocket, viewer_user_id)


# ---------- Trading ----------
# Like battles, all actual trade actions are submitted here from the web
# trade UI; the bot only ever creates the session (or, for a web-initiated
# trade, this API creates it directly) and posts/redirects to a link.

@app.get("/api/trades")
def list_trades(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM poke_trades WHERE (side_a_user_id = ? OR side_b_user_id = ?) "
            "AND status IN ('pending', 'active') ORDER BY updated_at DESC",
            (user_id, user_id),
        ).fetchall()
        recent_rows = conn.execute(
            "SELECT * FROM poke_trades WHERE (side_a_user_id = ? OR side_b_user_id = ?) "
            "AND status NOT IN ('pending', 'active') ORDER BY updated_at DESC LIMIT 20",
            (user_id, user_id),
        ).fetchall()
    return {
        "live": [trade_store.summarize_trade(r, user_id) for r in rows],
        "recent": [trade_store.summarize_trade(r, user_id) for r in recent_rows],
    }


@app.post("/api/trades/start/{target_user_id}")
async def start_trade(target_user_id: int, user_id: int = Depends(get_current_user_id)):
    if target_user_id == user_id:
        raise HTTPException(400, "You can't trade with yourself.")
    with db() as conn:
        target = conn.execute("SELECT 1 FROM poke_trainers WHERE user_id = ?", (target_user_id,)).fetchone()
    if not target:
        raise HTTPException(404, "That trainer doesn't exist.")
    if await asyncio.to_thread(trade_store.has_active_trade, user_id):
        raise HTTPException(400, "You're already in a trade!")
    if await asyncio.to_thread(trade_store.has_active_trade, target_user_id):
        raise HTTPException(400, "That trainer is already in a trade!")
    trade_id = await asyncio.to_thread(trade_store.create_trade, user_id, target_user_id, None, None)
    return {"trade_id": trade_id}


class TradeProposalRequest(BaseModel):
    target_user_id: int
    offer_catch_id: int
    request_catch_id: int


@app.post("/api/trades/propose")
async def propose_trade(body: TradeProposalRequest, user_id: int = Depends(get_current_user_id)):
    if body.target_user_id == user_id:
        raise HTTPException(400, "You can't trade with yourself.")
    with db() as conn:
        target = conn.execute("SELECT 1 FROM poke_trainers WHERE user_id = ?", (body.target_user_id,)).fetchone()
    if not target:
        raise HTTPException(404, "That trainer doesn't exist.")
    if await asyncio.to_thread(trade_store.has_active_trade, user_id):
        raise HTTPException(400, "You're already in a trade!")
    if await asyncio.to_thread(trade_store.has_active_trade, body.target_user_id):
        raise HTTPException(400, "That trainer is already in a trade!")
    trade_id, error = await asyncio.to_thread(
        trade_store.propose_trade, user_id, body.target_user_id,
        body.offer_catch_id, body.request_catch_id, None, None,
    )
    if error:
        raise HTTPException(400, error)
    return {"trade_id": trade_id}


@app.get("/api/trades/{trade_id}")
def get_trade_detail(trade_id: int, user_id: int = Depends(get_current_user_id)):
    payload = trade_store.serialize_trade_detail(trade_id, viewer_user_id=user_id)
    if not payload:
        raise HTTPException(404, "Trade not found")
    return payload


@app.post("/api/trades/{trade_id}/accept")
async def accept_trade(trade_id: int, user_id: int = Depends(get_current_user_id)):
    row = trade_store.get_trade_row(trade_id)
    if not row:
        raise HTTPException(404, "Trade not found")
    if row["side_b_user_id"] != user_id:
        raise HTTPException(403, "This trade invite isn't yours to accept")
    ok, error, _completed = await asyncio.to_thread(trade_store.accept_trade, trade_id)
    if not ok:
        raise HTTPException(400, error or "Couldn't accept this trade")
    await trade_connections.broadcast(trade_id)
    return trade_store.serialize_trade_detail(trade_id, viewer_user_id=user_id)


@app.post("/api/trades/{trade_id}/decline")
async def decline_trade(trade_id: int, user_id: int = Depends(get_current_user_id)):
    row = trade_store.get_trade_row(trade_id)
    if not row:
        raise HTTPException(404, "Trade not found")
    if user_id not in (row["side_a_user_id"], row["side_b_user_id"]):
        raise HTTPException(403, "This trade isn't yours to decline")
    await asyncio.to_thread(trade_store.decline_trade, trade_id)
    await trade_connections.broadcast(trade_id)
    return {"ok": True}


@app.post("/api/trades/{trade_id}/cancel")
async def cancel_trade(trade_id: int, user_id: int = Depends(get_current_user_id)):
    row = trade_store.get_trade_row(trade_id)
    if not row:
        raise HTTPException(404, "Trade not found")
    if user_id not in (row["side_a_user_id"], row["side_b_user_id"]):
        raise HTTPException(403, "This trade isn't yours to cancel")
    await asyncio.to_thread(trade_store.cancel_trade, trade_id)
    await trade_connections.broadcast(trade_id)
    return trade_store.serialize_trade_detail(trade_id, viewer_user_id=user_id)


class TradeOfferRequest(BaseModel):
    catch_id: int


@app.post("/api/trades/{trade_id}/offer")
async def offer_trade(trade_id: int, body: TradeOfferRequest, user_id: int = Depends(get_current_user_id)):
    row = trade_store.get_trade_row(trade_id)
    if not row:
        raise HTTPException(404, "Trade not found")
    side = trade_store.side_for_user(row, user_id)
    if side is None:
        raise HTTPException(403, "You're not a participant in this trade")
    ok, error = await asyncio.to_thread(trade_store.set_offer, trade_id, side, body.catch_id)
    if not ok:
        raise HTTPException(400, error or "Couldn't offer that Pokémon")
    await trade_connections.broadcast(trade_id)
    return trade_store.serialize_trade_detail(trade_id, viewer_user_id=user_id)


@app.post("/api/trades/{trade_id}/confirm")
async def confirm_trade(trade_id: int, user_id: int = Depends(get_current_user_id)):
    row = trade_store.get_trade_row(trade_id)
    if not row:
        raise HTTPException(404, "Trade not found")
    side = trade_store.side_for_user(row, user_id)
    if side is None:
        raise HTTPException(403, "You're not a participant in this trade")
    ok, error, _completed = await asyncio.to_thread(trade_store.confirm_offer, trade_id, side)
    if not ok:
        raise HTTPException(400, error or "Couldn't confirm this trade")
    await trade_connections.broadcast(trade_id)
    return trade_store.serialize_trade_detail(trade_id, viewer_user_id=user_id)


@app.websocket("/ws/trades/{trade_id}")
async def trade_websocket(websocket: WebSocket, trade_id: int):
    viewer_user_id = decode_token_user_id(websocket.query_params.get("token"))
    await trade_connections.connect(trade_id, websocket, viewer_user_id)
    try:
        payload = await asyncio.to_thread(trade_store.serialize_trade_detail, trade_id, viewer_user_id)
        if payload is None:
            await websocket.close(code=4404)
            return
        await websocket.send_json(payload)
        while True:
            await websocket.receive_text()  # only used to detect disconnect; actions go via POST
    except WebSocketDisconnect:
        pass
    finally:
        trade_connections.disconnect(trade_id, websocket, viewer_user_id)


# ---------- Gyms ----------

@app.get("/api/gyms")
def list_gyms(user_id: int = Depends(get_current_user_id)):
    earned = battle_store.get_badges(user_id)
    regions = []
    for region in battle_store.GYM_REGIONS:
        next_key = battle_store.next_gym_key(user_id, region)
        gyms = [
            {
                "gym_key": k,
                "order": battle_store.GYMS[k]["order"],
                "leader_name": battle_store.GYMS[k]["leader_name"],
                "type_theme": battle_store.GYMS[k]["type_theme"],
                "location": battle_store.GYMS[k]["location"],
                "badge_name": battle_store.GYMS[k]["badge_name"],
                "flavor": battle_store.GYMS[k]["flavor"],
                "leader_image": battle_store.GYMS[k].get("leader_image"),
                "badge_image": battle_store.GYMS[k].get("badge_image"),
                "earned": k in earned,
                "is_next": k == next_key,
            }
            for k in battle_store.gym_order(region)
        ]
        regions.append({"region": region, "gyms": gyms})
    return {"regions": regions}


@app.get("/api/gyms/{gym_key}")
def get_gym_detail(gym_key: str, user_id: int = Depends(get_current_user_id)):
    gym = battle_store.GYMS.get(gym_key)
    if not gym:
        raise HTTPException(404, "Unknown gym")
    earned = gym_key in battle_store.get_badges(user_id)
    is_next = gym_key == battle_store.next_gym_key(user_id, gym["generation"])
    cleared_by = battle_store.get_gym_clearers(gym_key)

    roster = []
    for entry in gym["roster"]:
        mon = POKEDEX.get(entry["dex_id"], {})
        roster.append({
            "dex_id": entry["dex_id"],
            "name": mon.get("name", f"#{entry['dex_id']}"),
            "artwork": mon.get("artwork"),
            "types": mon.get("types", []),
            "moves": entry["moves"],
            "ability": format_ability_name(entry["ability"]) if entry.get("ability") else None,
        })

    return {
        "gym_key": gym_key,
        "generation": gym["generation"],
        "order": gym["order"],
        "leader_name": gym["leader_name"],
        "type_theme": gym["type_theme"],
        "location": gym["location"],
        "badge_name": gym["badge_name"],
        "flavor": gym["flavor"],
        "leader_image": gym.get("leader_image"),
        "badge_image": gym.get("badge_image"),
        "earned": earned,
        "is_next": is_next,
        "roster": roster,
        "cleared_by": cleared_by,
    }


@app.post("/api/battles/gym/{gym_key}")
async def start_gym_battle(gym_key: str, user_id: int = Depends(get_current_user_id)):
    gym = battle_store.GYMS.get(gym_key)
    if not gym:
        raise HTTPException(404, "Unknown gym")
    if battle_store.has_active_battle(user_id):
        raise HTTPException(400, "You're already in a battle!")
    roster_a = battle_store.build_roster_for_player(user_id)
    if not roster_a:
        raise HTTPException(400, "You need to set your team first — use the Team page.")
    next_key = battle_store.next_gym_key(user_id, gym["generation"])
    if next_key is None:
        raise HTTPException(400, f"You've already earned all of {gym['generation'].title()}'s badges!")
    if gym_key != next_key:
        raise HTTPException(400, "You need to beat the earlier gyms in this region first, in order.")

    def _do():
        roster_b = battle_store.build_roster_for_gym(gym_key)
        battle_id = battle_store.create_battle("gym", user_id, None, gym_key, 0, 0)
        battle_store.start_battle_sides(battle_id, roster_a, roster_b)
        return battle_id

    battle_id = await asyncio.to_thread(_do)
    return {"battle_id": battle_id}


# ---------- Custom (player-run) gyms ----------

CUSTOM_GYM_SHAPES = {"circle", "hexagon", "octagon", "diamond", "shield", "star"}
CUSTOM_GYM_EMBLEMS = {"star", "bolt", "flame", "drop", "leaf", "snowflake", "heart", "moon", "crown", "gem", "mountain", "wing"}
POKEMON_TYPES = sorted(be.TYPE_CHART.keys())
REWARD_ONLY_DEX_IDS = {battle_store.LEAGUE_REWARD_DEX_ID, battle_store.CUSTOM_GYM_REWARD_DEX_ID}


def _clean_text(value: str, field: str, min_len: int, max_len: int) -> str:
    value = " ".join((value or "").split())  # collapse whitespace/newlines
    if any(ord(c) < 32 for c in value):
        raise HTTPException(400, f"{field} contains invalid characters.")
    if not (min_len <= len(value) <= max_len):
        raise HTTPException(400, f"{field} must be {min_len}-{max_len} characters.")
    return value


def _custom_gym_summary(gym: dict, viewer_id: int, earned: set[int], clear_counts: dict[int, int],
                        names: dict[int, str]) -> dict:
    owner = gym["owner_user_id"]
    return {
        "owner_user_id": str(owner),  # snowflake: see list_trainers()
        "owner_name": names.get(owner, "Trainer"),
        "leader_image": battle_store.trainer_character_sprite(owner),
        "gym_name": gym["gym_name"],
        "type_theme": gym["type_theme"],
        "badge_name": gym["badge_name"],
        "flavor": gym["flavor"],
        "badge_version": gym["updated_at"],
        "team_size": len(gym["roster"]),
        "cleared_count": clear_counts.get(owner, 0),
        "earned": owner in earned,
        "is_yours": owner == viewer_id,
    }


def _trainer_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["user_id"]: r["username"] or "Trainer" for r in conn.execute("SELECT user_id, username FROM poke_trainers")}


def custom_badges_for(user_id: int) -> list[dict]:
    earned = battle_store.get_custom_badges(user_id)
    out = []
    for owner in earned:
        gym = battle_store.get_custom_gym(owner)
        if gym:
            out.append({"owner_user_id": str(owner), "gym_name": gym["gym_name"],
                        "badge_name": gym["badge_name"], "badge_version": gym["updated_at"]})
    return sorted(out, key=lambda b: b["badge_name"].lower())


@app.get("/api/custom-gyms")
def list_custom_gyms(user_id: int = Depends(get_current_user_id)):
    earned = battle_store.get_custom_badges(user_id)
    counts = battle_store.custom_gym_clear_counts()
    with db() as conn:
        names = _trainer_names(conn)
    gyms = [_custom_gym_summary(g, user_id, earned, counts, names) for g in battle_store.list_custom_gyms()]
    gyms.sort(key=lambda g: (not g["is_yours"], -g["cleared_count"], g["gym_name"].lower()))
    league_earned, league_total = battle_store.league_completion(user_id)
    return {
        "gyms": gyms,
        "can_create": league_earned >= league_total,
        "has_gym": any(g["is_yours"] for g in gyms),
        "league_earned": league_earned,
        "league_total": league_total,
    }


@app.get("/api/custom-gyms/{owner_id}")
def get_custom_gym_detail(owner_id: int, user_id: int = Depends(get_current_user_id)):
    gym = battle_store.get_custom_gym(owner_id)
    if not gym:
        raise HTTPException(404, "That gym doesn't exist.")
    earned = battle_store.get_custom_badges(user_id)
    with db() as conn:
        names = _trainer_names(conn)
    summary = _custom_gym_summary(gym, user_id, earned, battle_store.custom_gym_clear_counts(), names)
    megas = battle_store.custom_gym_mega_forms(owner_id, {m["dex_id"] for m in gym["roster"]})
    roster = []
    for entry in gym["roster"]:
        mon = POKEDEX.get(entry["dex_id"], {})
        roster.append({
            "dex_id": entry["dex_id"],
            "name": mon.get("name", f"#{entry['dex_id']}"),
            "artwork": mon.get("artwork"),
            "types": mon.get("types", []),
            "moves": entry["moves"],
            "ability": format_ability_name(entry["ability"]) if entry.get("ability") else None,
            "mega": megas[entry["dex_id"]]["name"] if entry["dex_id"] in megas else None,
        })
    return {**summary, "badge_design": gym["badge_design"], "roster": roster,
            "cleared_by": battle_store.get_custom_gym_clearers(owner_id)}


@app.get("/api/my-gym")
def get_my_gym(user_id: int = Depends(get_current_user_id)):
    league_earned, league_total = battle_store.league_completion(user_id)
    gym = battle_store.get_custom_gym(user_id)
    return {
        "unlocked": league_earned >= league_total,
        "league_earned": league_earned,
        "league_total": league_total,
        "types": POKEMON_TYPES,
        "shapes": sorted(CUSTOM_GYM_SHAPES),
        "emblems": sorted(CUSTOM_GYM_EMBLEMS),
        "min_team": battle_store.CUSTOM_GYM_MIN_TEAM,
        "max_team": battle_store.CUSTOM_GYM_MAX_TEAM,
        "gym": None if not gym else {
            "gym_name": gym["gym_name"], "type_theme": gym["type_theme"], "badge_name": gym["badge_name"],
            "flavor": gym["flavor"], "badge_design": gym["badge_design"],
            "dex_ids": [m["dex_id"] for m in gym["roster"]],
            "cleared_count": battle_store.custom_gym_clear_counts().get(user_id, 0),
        },
    }


class BadgeDesign(BaseModel):
    shape: str
    primary: str
    secondary: str
    emblem: str


class CustomGymRequest(BaseModel):
    gym_name: str
    type_theme: str
    badge_name: str
    flavor: str = ""
    badge_design: BadgeDesign
    dex_ids: list[int]


@app.post("/api/my-gym")
def save_my_gym(body: CustomGymRequest, user_id: int = Depends(get_current_user_id)):
    if not battle_store.league_fully_completed(user_id):
        raise HTTPException(403, "Complete the entire Poké League to open your own gym.")

    gym_name = _clean_text(body.gym_name, "Gym name", 3, 30)
    badge_name = _clean_text(body.badge_name, "Badge name", 3, 24)
    flavor = _clean_text(body.flavor, "Description", 0, 160)
    if body.type_theme not in POKEMON_TYPES:
        raise HTTPException(400, "Pick a valid gym type.")
    design = body.badge_design
    if design.shape not in CUSTOM_GYM_SHAPES or design.emblem not in CUSTOM_GYM_EMBLEMS:
        raise HTTPException(400, "Invalid badge shape or emblem.")
    for color in (design.primary, design.secondary):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or ""):
            raise HTTPException(400, "Badge colors must be hex colors like #ff0000.")

    dex_ids = body.dex_ids
    if not (battle_store.CUSTOM_GYM_MIN_TEAM <= len(dex_ids) <= battle_store.CUSTOM_GYM_MAX_TEAM):
        raise HTTPException(400, f"Your gym team needs {battle_store.CUSTOM_GYM_MIN_TEAM}-{battle_store.CUSTOM_GYM_MAX_TEAM} Pokémon.")
    if len(set(dex_ids)) != len(dex_ids):
        raise HTTPException(400, "Each Pokémon can only be on your gym team once.")
    with db() as conn:
        owned = {r[0] for r in conn.execute("SELECT DISTINCT dex_id FROM poke_collection WHERE user_id = ?", (user_id,))}
    for dex_id in dex_ids:
        mon = POKEDEX.get(dex_id)
        if not mon or dex_id in REWARD_ONLY_DEX_IDS:
            raise HTTPException(400, "That Pokémon can't be used in a gym.")
        if dex_id not in owned:
            raise HTTPException(400, f"You don't own a {mon['name']}.")
        if body.type_theme not in mon.get("types", []):
            raise HTTPException(400, f"{mon['name']} isn't {body.type_theme.title()}-type — gym teams must match the gym's type.")

    roster = battle_store.snapshot_custom_gym_roster(user_id, dex_ids)
    battle_store.save_custom_gym(
        user_id, gym_name, body.type_theme, badge_name, flavor,
        {"shape": design.shape, "primary": design.primary.lower(), "secondary": design.secondary.lower(), "emblem": design.emblem},
        roster,
    )
    return {"ok": True}


@app.post("/api/battles/custom-gym/{owner_id}")
async def start_custom_gym_battle(owner_id: int, user_id: int = Depends(get_current_user_id)):
    gym = battle_store.get_custom_gym(owner_id)
    if not gym:
        raise HTTPException(404, "That gym doesn't exist.")
    if owner_id == user_id:
        raise HTTPException(400, "You can't challenge your own gym.")
    if owner_id in battle_store.get_custom_badges(user_id):
        raise HTTPException(400, "You've already earned this gym's badge!")
    if battle_store.has_active_battle(user_id):
        raise HTTPException(400, "You're already in a battle!")
    roster_a = battle_store.build_roster_for_player(user_id)
    if not roster_a:
        raise HTTPException(400, "You need to set your team first — use the Team page.")

    def _do():
        roster_b = battle_store.build_roster_for_custom_gym(owner_id)
        battle_id = battle_store.create_battle(battle_store.CUSTOM_GYM_BATTLE_TYPE, user_id, None, str(owner_id), 0, 0)
        battle_store.start_battle_sides(battle_id, roster_a, roster_b)
        return battle_id

    battle_id = await asyncio.to_thread(_do)
    return {"battle_id": battle_id}


# ---------- Poké League (Elite Four + Champions) ----------

@app.get("/api/league")
def get_league(user_id: int = Depends(get_current_user_id)):
    unlocked = battle_store.league_unlocked(user_id)
    earned = battle_store.get_league_progress(user_id)
    generations = []
    for generation in battle_store.LEAGUE_GENERATIONS:
        next_e4_key = battle_store.next_elite_four_key(user_id, generation)
        elite_four = []
        for league_key in battle_store.league_order(generation, "elite_four"):
            member = battle_store.LEAGUE[league_key]
            elite_four.append({
                "league_key": league_key,
                "member_key": member["member_key"],
                "order": member["order"],
                "name": member["name"],
                "type_theme": member["type_theme"],
                "flavor": member["flavor"],
                "portrait": member.get("portrait"),
                "earned": league_key in earned,
                "is_next": league_key == next_e4_key,
            })
        champ_key = battle_store.champion_key(generation)
        champion = None
        if champ_key:
            champ = battle_store.LEAGUE[champ_key]
            champion = {
                "league_key": champ_key,
                "member_key": champ["member_key"],
                "name": champ["name"],
                "flavor": champ["flavor"],
                "portrait": champ.get("portrait"),
                "earned": champ_key in earned,
                "unlocked": battle_store.champion_unlocked(user_id, generation),
            }
        generations.append({"generation": generation, "elite_four": elite_four, "champion": champion})

    return {
        "unlocked": unlocked,
        "generations": generations,
        "badges_earned": len(battle_store.get_badges(user_id) & set(battle_store.GYMS.keys())),
        "max_legendaries": battle_store.LEAGUE_MAX_LEGENDARIES,
        "team_legendaries": [POKEDEX[d]["name"] for d in battle_store.get_team(user_id) if battle_store.is_legendary_species(d)],
        "badges_total": len(battle_store.GYMS),
    }


@app.get("/api/league/{generation}/{member_key}")
def get_league_member_detail(generation: str, member_key: str, user_id: int = Depends(get_current_user_id)):
    league_key = f"{generation}:{member_key}"
    member = battle_store.LEAGUE.get(league_key)
    if not member:
        raise HTTPException(404, "Unknown League member")

    earned = battle_store.get_league_progress(user_id)
    if member["role"] == "champion":
        is_next = battle_store.champion_unlocked(user_id, generation) and league_key not in earned
    else:
        is_next = league_key == battle_store.next_elite_four_key(user_id, generation)

    roster = []
    for entry in member["roster"]:
        mon = POKEDEX.get(entry["dex_id"], {})
        roster.append({
            "dex_id": entry["dex_id"],
            "name": mon.get("name", f"#{entry['dex_id']}"),
            "artwork": mon.get("artwork"),
            "types": mon.get("types", []),
            "moves": entry["moves"],
            "ability": format_ability_name(entry["ability"]) if entry.get("ability") else None,
        })

    return {
        "league_key": league_key,
        "generation": generation,
        "member_key": member["member_key"],
        "role": member["role"],
        "order": member["order"],
        "name": member["name"],
        "type_theme": member["type_theme"],
        "flavor": member["flavor"],
        "portrait": member.get("portrait"),
        "earned": league_key in earned,
        "is_next": is_next,
        "roster": roster,
    }


@app.post("/api/battles/elite4/{generation}/{member_key}")
async def start_elite_four_battle(generation: str, member_key: str, user_id: int = Depends(get_current_user_id)):
    league_key = f"{generation}:{member_key}"
    member = battle_store.LEAGUE.get(league_key)
    if not member or member["role"] != "elite_four":
        raise HTTPException(404, "Unknown Elite Four member")
    if battle_store.has_active_battle(user_id):
        raise HTTPException(400, "You're already in a battle!")
    if not battle_store.league_unlocked(user_id):
        raise HTTPException(400, "Earn every Gym Badge across all 4 regions first to unlock the Poké League.")
    roster_a = battle_store.build_roster_for_player(user_id)
    if not roster_a:
        raise HTTPException(400, "You need to set your team first — use the Team page.")
    problem = battle_store.league_team_problem(user_id)
    if problem:
        raise HTTPException(400, problem)
    next_key = battle_store.next_elite_four_key(user_id, generation)
    if next_key is None:
        raise HTTPException(400, f"You've already defeated {generation.title()}'s entire Elite Four!")
    if league_key != next_key:
        raise HTTPException(400, "You need to beat the earlier Elite Four members first, in order.")

    def _do():
        roster_b = battle_store.build_roster_for_league(league_key)
        battle_id = battle_store.create_battle("elite_four", user_id, None, league_key, 0, 0)
        battle_store.start_battle_sides(battle_id, roster_a, roster_b)
        return battle_id

    battle_id = await asyncio.to_thread(_do)
    return {"battle_id": battle_id}


@app.post("/api/battles/champion/{generation}")
async def start_champion_battle(generation: str, user_id: int = Depends(get_current_user_id)):
    league_key = battle_store.champion_key(generation)
    member = battle_store.LEAGUE.get(league_key) if league_key else None
    if not member:
        raise HTTPException(404, "Unknown region")
    if battle_store.has_active_battle(user_id):
        raise HTTPException(400, "You're already in a battle!")
    roster_a = battle_store.build_roster_for_player(user_id)
    if not roster_a:
        raise HTTPException(400, "You need to set your team first — use the Team page.")
    problem = battle_store.league_team_problem(user_id)
    if problem:
        raise HTTPException(400, problem)
    if not battle_store.champion_unlocked(user_id, generation):
        raise HTTPException(400, f"Defeat all 4 of {generation.title()}'s Elite Four first.")
    earned = battle_store.get_league_progress(user_id)
    if league_key in earned:
        raise HTTPException(400, f"You've already defeated {generation.title()}'s Champion!")

    def _do():
        roster_b = battle_store.build_roster_for_league(league_key)
        battle_id = battle_store.create_battle("champion", user_id, None, league_key, 0, 0)
        battle_store.start_battle_sides(battle_id, roster_a, roster_b)
        return battle_id

    battle_id = await asyncio.to_thread(_do)
    return {"battle_id": battle_id}


# ---------- Legends ----------

def _legend_card(key: str, wins: dict) -> dict:
    entry = battle_store.LEGENDS[key]
    reward = POKEDEX.get(entry["reward_mega"], {})
    reward_base = POKEDEX.get(reward.get("mega_of"), {})
    megas = [POKEDEX[m["mega"]]["name"] for m in entry["roster"] if m.get("mega") in POKEDEX]
    return {
        "key": key, "name": entry["name"], "title": entry["title"], "region": entry["region"],
        "order": entry["order"], "flavor": entry["flavor"], "portrait": entry.get("portrait"),
        "megas": megas,
        "reward": {"dex_id": reward.get("id"), "name": reward.get("name"), "artwork": reward.get("artwork"),
                   "base_name": reward_base.get("name")},
        "beaten": key in wins, "wins": wins.get(key, {}).get("wins", 0),
        "first_won_at": wins.get(key, {}).get("first_won_at"),
    }


@app.get("/api/legends")
def get_legends(user_id: int = Depends(get_current_user_id)):
    wins = battle_store.get_legend_wins(user_id)
    champions = [k for k, e in battle_store.LEAGUE.items() if e["role"] == "champion"]
    earned = battle_store.get_league_progress(user_id)
    return {
        "unlocked": battle_store.legends_unlocked(user_id),
        "champions": [{"name": battle_store.LEAGUE[k]["name"], "region": battle_store.LEAGUE[k]["generation"],
                       "beaten": k in earned} for k in champions],
        "legends": [_legend_card(k, wins) for k in battle_store.LEGENDS],
        "first_win": battle_store.LEGEND_FIRST_WIN,
        "rematch": battle_store.LEGEND_REMATCH,
        "max_legendaries": battle_store.LEAGUE_MAX_LEGENDARIES,
        "team_legendaries": [POKEDEX[d]["name"] for d in battle_store.get_team(user_id) if battle_store.is_legendary_species(d)],
    }


@app.get("/api/legends/{legend_key}")
def get_legend_detail(legend_key: str, user_id: int = Depends(get_current_user_id)):
    entry = battle_store.LEGENDS.get(legend_key)
    if not entry:
        raise HTTPException(404, "Unknown Legend")
    card = _legend_card(legend_key, battle_store.get_legend_wins(user_id))
    roster = []
    for m in entry["roster"]:
        mon = POKEDEX.get(m["dex_id"], {})
        mega = POKEDEX.get(m.get("mega")) if m.get("mega") else None
        roster.append({
            "dex_id": m["dex_id"], "name": mon.get("name"), "artwork": mon.get("artwork"),
            "types": mon.get("types", []),
            "mega": {"name": mega["name"], "artwork": mega.get("artwork"), "types": mega.get("types", [])} if mega else None,
        })
    return {**card, "roster": roster, "unlocked": battle_store.legends_unlocked(user_id)}


@app.post("/api/battles/legend/{legend_key}")
async def start_legend_battle(legend_key: str, user_id: int = Depends(get_current_user_id)):
    if legend_key not in battle_store.LEGENDS:
        raise HTTPException(404, "Unknown Legend")
    if battle_store.has_active_battle(user_id):
        raise HTTPException(400, "You're already in a battle!")
    if not battle_store.legends_unlocked(user_id):
        raise HTTPException(400, "Defeat all 4 regional Champions to challenge the Legends.")
    roster_a = battle_store.build_roster_for_player(user_id)
    if not roster_a:
        raise HTTPException(400, "You need to set your team first — use the Team page.")
    problem = battle_store.league_team_problem(user_id)
    if problem:
        raise HTTPException(400, problem.replace("The Poké League allows", "Legend battles allow"))

    def _do():
        roster_b = battle_store.build_roster_for_legend(legend_key)
        battle_id = battle_store.create_battle(battle_store.LEGEND_BATTLE_TYPE, user_id, None, legend_key, 0, 0)
        battle_store.start_battle_sides(battle_id, roster_a, roster_b)
        return battle_id

    battle_id = await asyncio.to_thread(_do)
    return {"battle_id": battle_id}


# ---------- Rewards ----------

@app.get("/api/rewards")
def get_rewards(user_id: int = Depends(get_current_user_id)):
    earned, total = battle_store.league_completion(user_id)
    legend_names = {f"legend:{k}": e["name"] for k, e in battle_store.LEGENDS.items()}
    unlocked = []
    for u in battle_store.get_mega_unlock_details(user_id):
        card = _mega_card(u["dex_id"])
        card["source"] = ("Poké League reward" if u["source"] == "league"
                          else f"Beat {legend_names[u['source']]}" if u["source"] in legend_names else "Reward")
        unlocked.append(card)
    return {
        "league_earned": earned,
        "league_total": total,
        "league_complete": earned >= total,
        "mega": {
            "chosen": _mega_card(battle_store.get_mega_choice(user_id)),
            "options": [_mega_card(d) for d in battle_store.MEGA_REWARD_DEX_IDS],
            "unlocked": unlocked,
            "can_mega_evolve": battle_store.mega_evolution_unlocked(user_id),
        },
    }


def _mega_card(dex_id: int | None) -> dict | None:
    if dex_id is None or dex_id not in POKEDEX:
        return None
    mon = POKEDEX[dex_id]
    base = POKEDEX.get(mon.get("mega_of"), {})
    return {
        "dex_id": dex_id,
        "name": mon["name"],
        "base_name": base.get("name"),
        "base_artwork": base.get("artwork"),
        "types": mon["types"],
        "artwork": mon["artwork"],
        "ability": format_ability_name(mon["abilities"][0]["name"]) if mon.get("abilities") else None,
        "base_stats": mon["base_stats"],
        "stat_total": sum(mon["base_stats"].values()),
    }


class MegaChoiceRequest(BaseModel):
    dex_id: int


@app.post("/api/rewards/mega")
def claim_mega(body: MegaChoiceRequest, user_id: int = Depends(get_current_user_id)):
    error = battle_store.claim_mega_reward(user_id, body.dex_id)
    if error:
        raise HTTPException(400, error)
    return {"ok": True, "chosen": _mega_card(body.dex_id)}
