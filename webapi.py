"""
Internal Pokémon data API — runs on the same machine as the Discord bot,
reading the same pokemon.db SQLite file directly. The public-facing web
app (hosted separately, e.g. on EC2) talks to this over HTTPS instead of
touching the database itself.

Run with: uvicorn webapi:app --host 0.0.0.0 --port 8000

CD test marker: deploy-bot.yml pipeline
"""
import os
import sqlite3
import json
import logging
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

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
TRAINER_CHARACTERS = {
    "red": {"label": "Red", "generation": "Kanto", "sprite": "https://archives.bulbagarden.net/media/upload/e/e8/Spr_HGSS_Red.png"},
    "leaf": {"label": "Leaf", "generation": "Kanto", "sprite": "https://archives.bulbagarden.net/media/upload/2/2b/Spr_FRLG_Leaf.png"},
    "gold": {"label": "Gold", "generation": "Johto", "sprite": "https://archives.bulbagarden.net/media/upload/a/a5/Spr_HGSS_Ethan.png"},
    "kris": {"label": "Kris", "generation": "Johto", "sprite": "https://archives.bulbagarden.net/media/upload/9/9e/Spr_C_Kris.png"},
    "brendan": {"label": "Brendan", "generation": "Hoenn", "sprite": "https://archives.bulbagarden.net/media/upload/6/68/Spr_RS_Brendan.png"},
    "may": {"label": "May", "generation": "Hoenn", "sprite": "https://archives.bulbagarden.net/media/upload/3/38/Spr_RS_May.png"},
    "lucas": {"label": "Lucas", "generation": "Sinnoh", "sprite": "https://archives.bulbagarden.net/media/upload/6/6b/Spr_Pt_Lucas.png"},
    "dawn": {"label": "Dawn", "generation": "Sinnoh", "sprite": "https://archives.bulbagarden.net/media/upload/0/00/Spr_DP_Dawn.png"},
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


with open(DATA_PATH, encoding="utf-8") as f:
    POKEDEX: dict[int, dict] = {p["id"]: p for p in json.load(f)}

app = FastAPI(title="ShawtyBot Pokémon API")

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

@app.get("/api/me")
def get_me(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        trainer = conn.execute("SELECT * FROM poke_trainers WHERE user_id = ?", (user_id,)).fetchone()
        if not trainer:
            raise HTTPException(404, "Trainer not found")

        xp_row = conn.execute(
            "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'xp'", (user_id,)
        ).fetchone()
        total_xp = xp_row["qty"] if xp_row else 0

        total_caught = conn.execute(
            "SELECT COUNT(*) FROM poke_collection WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
        unique_species = conn.execute(
            "SELECT COUNT(DISTINCT dex_id) FROM poke_collection WHERE user_id = ?", (user_id,)
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

    dex_total = len(POKEDEX)

    return {
        "user_id": user_id,
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
        # Battling isn't built yet — always accurate at 0 until it is.
        "battle_record": {"wins": 0, "games": 0},
    }


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


# ---------- Inventory ----------

@app.get("/api/inventory")
def get_inventory(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        rows = conn.execute("SELECT item, qty FROM poke_items WHERE user_id = ?", (user_id,)).fetchall()

    balls, family_candies = {}, {}
    coins = rare_candy = 0

    for row in rows:
        item, qty = row["item"], row["qty"]
        if item == "coin":
            coins = qty
        elif item == "candy":
            rare_candy = qty
        elif item.startswith("famcandy_"):
            try:
                family_id = int(item.split("_", 1)[1])
            except ValueError:
                continue
            family_candies[POKEDEX.get(family_id, {}).get("name", item)] = qty
        else:
            balls[item] = qty

    return {"balls": balls, "coins": coins, "rare_candy": rare_candy, "family_candies": family_candies}


# ---------- Team ----------

class TeamRequest(BaseModel):
    dex_ids: list[int]


@app.get("/api/team")
def get_team(user_id: int = Depends(get_current_user_id)):
    with db() as conn:
        rows = conn.execute(
            "SELECT dex_id FROM poke_team WHERE user_id = ? ORDER BY slot", (user_id,)
        ).fetchall()
    return [
        {"dex_id": r["dex_id"], "name": POKEDEX.get(r["dex_id"], {}).get("name", f"#{r['dex_id']}")}
        for r in rows
    ]


@app.post("/api/team")
def set_team(body: TeamRequest, user_id: int = Depends(get_current_user_id)):
    if len(body.dex_ids) > 6:
        raise HTTPException(400, "A team can have at most 6 Pokémon")

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
