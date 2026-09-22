"""
Shared trade DB/orchestration layer — pure Python + sqlite3, zero discord.py
and zero FastAPI imports. Mirrors battle_store.py's split: the bot only ever
creates a trade session (and posts a link), while the internal API is where
every real action (accept/decline/offer/confirm/cancel) is submitted from the
web trade UI and both processes share this one implementation.

A trade is between two specific individuals a player owns (poke_collection
rows, i.e. catch_ids) — never species — since two Pokémon of the same
species can now have completely different IVs (see battle_store.py's
ivs_from_collection_row), and trading is exactly the feature where that
individual identity matters most.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import battle_store

DB_PATH = "pokemon.db"

TRADE_PENDING_TIMEOUT_SECONDS = 5 * 60   # unanswered trade invite
TRADE_ABANDON_SECONDS = 30 * 60          # idle active trade, nobody confirming


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- Session CRUD ----------

def has_active_trade(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM poke_trades WHERE status IN ('pending', 'active') "
            "AND (side_a_user_id = ? OR side_b_user_id = ?) LIMIT 1",
            (user_id, user_id),
        ).fetchone()
    return row is not None


def create_trade(side_a_user_id: int, side_b_user_id: int, guild_id: int | None, channel_id: int | None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO poke_trades (status, side_a_user_id, side_b_user_id, guild_id, channel_id, "
            "created_at, updated_at) VALUES ('pending', ?, ?, ?, ?, ?, ?)",
            (side_a_user_id, side_b_user_id, guild_id or 0, channel_id or 0, now, now),
        )
        return cur.lastrowid


def get_trade_row(trade_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute("SELECT * FROM poke_trades WHERE trade_id = ?", (trade_id,)).fetchone()


def side_for_user(row: sqlite3.Row, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    if row["side_a_user_id"] == user_id:
        return "A"
    if row["side_b_user_id"] == user_id:
        return "B"
    return None


def accept_trade(trade_id: int) -> tuple[bool, str | None]:
    row = get_trade_row(trade_id)
    if not row or row["status"] != "pending":
        return False, "This trade invite is no longer available."
    with db() as conn:
        conn.execute(
            "UPDATE poke_trades SET status = 'active', updated_at = ? WHERE trade_id = ?",
            (datetime.now(timezone.utc).isoformat(), trade_id),
        )
    return True, None


def decline_trade(trade_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE poke_trades SET status = 'declined', updated_at = ? WHERE trade_id = ?",
            (datetime.now(timezone.utc).isoformat(), trade_id),
        )


def cancel_trade(trade_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE poke_trades SET status = 'cancelled', updated_at = ? WHERE trade_id = ?",
            (datetime.now(timezone.utc).isoformat(), trade_id),
        )


def set_offer(trade_id: int, side: str, catch_id: int) -> tuple[bool, str | None]:
    """Sets this side's offered individual. Resets BOTH sides' confirmation —
    changing either offer un-readies both trainers, so nobody can be tricked
    into confirming a swap they didn't actually agree to."""
    row = get_trade_row(trade_id)
    if not row or row["status"] != "active":
        return False, "This trade isn't active right now."
    user_id = row["side_a_user_id"] if side == "A" else row["side_b_user_id"]
    catch_col = "side_a_catch_id" if side == "A" else "side_b_catch_id"
    with db() as conn:
        owned = conn.execute(
            "SELECT 1 FROM poke_collection WHERE id = ? AND user_id = ?", (catch_id, user_id)
        ).fetchone()
        if not owned:
            return False, "You don't own that Pokémon."
        conn.execute(
            f"UPDATE poke_trades SET {catch_col} = ?, side_a_confirmed = 0, side_b_confirmed = 0, "
            "updated_at = ? WHERE trade_id = ?",
            (catch_id, datetime.now(timezone.utc).isoformat(), trade_id),
        )
    return True, None


def confirm_offer(trade_id: int, side: str) -> tuple[bool, str | None, bool]:
    """Marks this side confirmed; if both sides are now confirmed (and both
    have offered something), executes the trade atomically. Returns
    (ok, error_or_None, completed)."""
    row = get_trade_row(trade_id)
    if not row or row["status"] != "active":
        return False, "This trade isn't active right now.", False
    catch_col = "side_a_catch_id" if side == "A" else "side_b_catch_id"
    if not row[catch_col]:
        return False, "Choose a Pokémon to offer first.", False
    confirmed_col = "side_a_confirmed" if side == "A" else "side_b_confirmed"

    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            f"UPDATE poke_trades SET {confirmed_col} = 1, updated_at = ? WHERE trade_id = ?", (now, trade_id)
        )
        row = conn.execute("SELECT * FROM poke_trades WHERE trade_id = ?", (trade_id,)).fetchone()
        if not (row["side_a_confirmed"] and row["side_b_confirmed"]
                and row["side_a_catch_id"] and row["side_b_catch_id"]):
            return True, None, False

        a_catch = conn.execute(
            "SELECT id, dex_id FROM poke_collection WHERE id = ? AND user_id = ?",
            (row["side_a_catch_id"], row["side_a_user_id"]),
        ).fetchone()
        b_catch = conn.execute(
            "SELECT id, dex_id FROM poke_collection WHERE id = ? AND user_id = ?",
            (row["side_b_catch_id"], row["side_b_user_id"]),
        ).fetchone()
        if not a_catch or not b_catch:
            conn.execute(
                "UPDATE poke_trades SET status = 'cancelled', updated_at = ? WHERE trade_id = ?", (now, trade_id)
            )
            return False, "One of the offered Pokémon is no longer available.", False

        conn.execute("UPDATE poke_collection SET user_id = ? WHERE id = ?", (row["side_b_user_id"], a_catch["id"]))
        conn.execute("UPDATE poke_collection SET user_id = ? WHERE id = ?", (row["side_a_user_id"], b_catch["id"]))
        # Receiving a species for the first time registers it in the
        # recipient's permanent Pokédex, same as catching/evolving into it.
        conn.execute(
            "INSERT OR IGNORE INTO poke_dex_seen (user_id, dex_id, first_caught_at) VALUES (?, ?, ?)",
            (row["side_b_user_id"], a_catch["dex_id"], now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO poke_dex_seen (user_id, dex_id, first_caught_at) VALUES (?, ?, ?)",
            (row["side_a_user_id"], b_catch["dex_id"], now),
        )
        conn.execute(
            "UPDATE poke_trades SET status = 'completed', completed_at = ?, updated_at = ? WHERE trade_id = ?",
            (now, now, trade_id),
        )
    return True, None, True


def sweep_trades() -> list[int]:
    """Expires unanswered invites and abandons idle active trades — mirrors
    battle_store.sweep_battles()'s idle-timeout pattern."""
    now = datetime.now(timezone.utc)
    with db() as conn:
        rows = conn.execute("SELECT * FROM poke_trades WHERE status IN ('pending', 'active')").fetchall()

    changed = []
    for row in rows:
        idle_seconds = (now - datetime.fromisoformat(row["updated_at"])).total_seconds()
        threshold = TRADE_PENDING_TIMEOUT_SECONDS if row["status"] == "pending" else TRADE_ABANDON_SECONDS
        if idle_seconds <= threshold:
            continue
        new_status = "declined" if row["status"] == "pending" else "cancelled"
        with db() as conn:
            conn.execute(
                "UPDATE poke_trades SET status = ?, updated_at = ? WHERE trade_id = ?",
                (new_status, now.isoformat(), row["trade_id"]),
            )
        changed.append(row["trade_id"])
    return changed


# ---------- Serialization ----------

def mon_card(catch_id: int | None) -> dict | None:
    """Full card data for one specific individual — everything the trade
    room's 'click to open the card' modal needs, without a second request."""
    if not catch_id:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT id, dex_id, nickname, is_shiny, caught_at, "
            "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed "
            "FROM poke_collection WHERE id = ?",
            (catch_id,),
        ).fetchone()
    if not row:
        return None
    mon = battle_store.POKEDEX.get(row["dex_id"])
    if not mon:
        return None

    ivs = battle_store.ivs_from_collection_row(row)
    base_stats = mon.get("base_stats", {})
    labels = [
        ("hp", "HP", True), ("attack", "Attack", False), ("defense", "Defense", False),
        ("sp_attack", "Sp. Attack", False), ("sp_defense", "Sp. Defense", False), ("speed", "Speed", False),
    ]
    stats = [
        {
            "key": key, "label": label, "base": base_stats.get(key, 0), "iv": ivs.get(key, 31),
            "at_level_100": battle_store.be.stat_at_level_100(base_stats.get(key, 0), ivs.get(key, 31), is_hp),
        }
        for key, label, is_hp in labels
    ]
    return {
        "id": row["id"],
        "dex_id": row["dex_id"],
        "name": mon.get("name", f"#{row['dex_id']}"),
        "nickname": row["nickname"],
        "types": mon.get("types", []),
        "category": mon.get("category"),
        "artwork": (mon.get("artwork_shiny") or mon.get("artwork")) if row["is_shiny"] else mon.get("artwork"),
        "is_shiny": bool(row["is_shiny"]),
        "caught_at": row["caught_at"],
        "base_stats": stats,
        "iv_percent": round(sum(ivs.values()) / (31 * 6) * 100, 1),
    }


def summarize_trade(row: sqlite3.Row) -> dict:
    with db() as conn:
        name_a = battle_store.trainer_display_name(conn, row["side_a_user_id"])
        name_b = battle_store.trainer_display_name(conn, row["side_b_user_id"])
    return {
        "trade_id": row["trade_id"], "status": row["status"],
        "name_a": name_a, "name_b": name_b,
        "created_at": row["created_at"], "completed_at": row["completed_at"],
    }


def serialize_trade_detail(trade_id: int, viewer_user_id: int | None = None) -> dict | None:
    row = get_trade_row(trade_id)
    if not row:
        return None
    with db() as conn:
        name_a = battle_store.trainer_display_name(conn, row["side_a_user_id"])
        name_b = battle_store.trainer_display_name(conn, row["side_b_user_id"])

    side = side_for_user(row, viewer_user_id)
    payload = {
        "trade_id": trade_id, "status": row["status"],
        "name_a": name_a, "name_b": name_b,
        "mon_a": mon_card(row["side_a_catch_id"]), "mon_b": mon_card(row["side_b_catch_id"]),
        "confirmed_a": bool(row["side_a_confirmed"]), "confirmed_b": bool(row["side_b_confirmed"]),
        "created_at": row["created_at"], "completed_at": row["completed_at"],
    }
    payload["you"] = {
        "side": side,
        "is_pending_target": side == "B" and row["status"] == "pending",
        "can_act": side is not None and row["status"] == "active",
    }
    return payload
