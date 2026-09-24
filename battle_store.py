"""
Shared battle DB/orchestration layer — pure Python + sqlite3, zero discord.py
and zero FastAPI imports, so both the bot (cogs/pokemon.py, which only ever
*creates* battles now) and the internal API (webapi.py, which is where all
battle actions are actually submitted and resolved from the web battle UI)
can import the exact same code instead of maintaining two copies.

Split from battle_engine.py (pure combat math, no I/O at all) — this module
is the glue: SQLite persistence, roster-building from a trainer's team/web
loadout or from static gym/trainer data, reward granting, and the turn/
forced-switch/forfeit orchestration around battle_engine's resolve_turn.

Player-facing display names are resolved purely from poke_trainers.username
(populated whenever someone visits the web app) rather than a live Discord
bot object — by the time a human is actually submitting battle actions, they
are necessarily logged into the web app, so this is always available for
real participants.
"""
import json
import os
import random
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import battle_engine as be

SHOWDOWN_SPRITE_BASE = "https://play.pokemonshowdown.com/sprites"


def showdown_sprite_slug(name: str) -> str:
    """Pokémon Showdown's animated-sprite filenames are the species name
    lowercased with every non-alphanumeric character stripped (hyphens,
    apostrophes, spaces, periods all just vanish — 'Mr. Mime' -> 'mrmime',
    'Porygon-Z' -> 'porygonz', "Farfetch'd" -> 'farfetchd', 'Ho-oh' ->
    'hooh'), except the gender symbols on Nidoran, which become a literal
    f/m ('Nidoran♀' -> 'nidoranf'). Verified against the live CDN for a
    sample including every one of these edge cases in our Gen 1-4 dataset."""
    name = name.replace("♀", "f").replace("♂", "m")
    return re.sub(r"[^a-z0-9]", "", name.lower())


def animated_sprite_urls(name: str) -> tuple[str, str]:
    """Returns (front, back) animated sprite GIF URLs. Not every species has
    a back sprite on the CDN; the front URL is safe for all of them. The
    client falls back to the static artwork on a 404 either way."""
    slug = showdown_sprite_slug(name)
    return f"{SHOWDOWN_SPRITE_BASE}/ani/{slug}.gif", f"{SHOWDOWN_SPRITE_BASE}/ani-back/{slug}.gif"

DB_PATH = "pokemon.db"
DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "pokemon.json")
GYMS_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "gyms.json")
TRAINER_CLASSES_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "trainer_classes.json")
ELITE_FOUR_DATA_PATH = os.path.join(os.path.dirname(__file__), "data", "elite_four.json")

with open(DATA_PATH, encoding="utf-8") as f:
    POKEDEX: dict[int, dict] = {p["id"]: p for p in json.load(f)}

with open(GYMS_DATA_PATH, encoding="utf-8") as f:
    GYMS: dict[str, dict] = {g["gym_key"]: g for g in json.load(f)}
GYM_REGIONS: list[str] = ["kanto", "johto", "hoenn", "sinnoh"]


def gym_order(region: str) -> list[str]:
    """The ordered list of gym_keys for one region's 8 gyms, sorted by each
    entry's own `order` field (1-8, scoped to that region — not global)."""
    return [
        key for key, entry in sorted(GYMS.items(), key=lambda kv: kv[1]["order"])
        if entry["generation"] == region
    ]

with open(TRAINER_CLASSES_DATA_PATH, encoding="utf-8") as f:
    TRAINER_CLASSES: list[dict] = json.load(f)
TRAINER_CLASS_BY_KEY: dict[str, dict] = {c["class_key"]: c for c in TRAINER_CLASSES}

# Poké League: the Elite Four + Champion tier, unlocked once every gym
# badge across all 4 regions is earned. Keyed by "{generation}:{member_key}" (e.g.
# "kanto:lorelei") — that composite is what's stored in poke_battles'
# side_b_npc_key and in poke_league_progress' league_key, exactly the same
# role gym_key plays for gym battles.
with open(ELITE_FOUR_DATA_PATH, encoding="utf-8") as f:
    LEAGUE: dict[str, dict] = {f"{e['generation']}:{e['member_key']}": e for e in json.load(f)}
LEAGUE_GENERATIONS: list[str] = ["kanto", "johto", "hoenn", "sinnoh"]


def league_order(generation: str, role: str) -> list[str]:
    """The ordered list of league_keys for one generation's Elite Four
    (role='elite_four', 4 members) or Champion (role='champion', 1 member),
    sorted by each entry's own `order` field."""
    return [
        key for key, entry in sorted(LEAGUE.items(), key=lambda kv: kv[1]["order"])
        if entry["generation"] == generation and entry["role"] == role
    ]

# Reuses the existing trainer XP/coin systems for rewards — no new
# per-Pokémon stat-growth mechanic. "Getting stronger" means a stronger
# account (XP/coins/badges/moveset choices), not stat growth on any mon.
TRAINER_BATTLE_REWARDS = {
    "low": {"xp": 15, "coin": 20},
    "medium": {"xp": 30, "coin": 40},
    "high": {"xp": 55, "coin": 80},
}
GYM_BATTLE_XP = 100
GYM_BATTLE_COIN = 250
PVP_WIN_XP = 25
PVP_WIN_COIN = 30
LEAGUE_BATTLE_XP = 200
LEAGUE_BATTLE_COIN = 400
CHAMPION_BATTLE_XP = 400
CHAMPION_BATTLE_COIN = 800

BATTLE_TURN_TIMEOUT_SECONDS = 90
BATTLE_PENDING_TIMEOUT_SECONDS = 5 * 60   # unanswered PvP challenge
BATTLE_ABANDON_SECONDS = 30 * 60          # idle active/awaiting-switch battle


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------- Trainer/team helpers ----------

def get_team(user_id: int) -> list[int]:
    with db() as conn:
        rows = conn.execute("SELECT dex_id FROM poke_team WHERE user_id = ? ORDER BY slot", (user_id,)).fetchall()
    return [r["dex_id"] for r in rows]


def get_battle_pokemon_config(user_id: int, dex_id: int) -> tuple[list[str], str | None]:
    """Mirrors webapi.py's resolve_pokemon_config, trimmed to (moves, ability)."""
    mon = POKEDEX.get(dex_id, {})
    pool = mon.get("moves", [])
    abilities = mon.get("abilities", [])
    default_moves = [m["name"] for m in pool[:4]]
    default_ability = abilities[0]["name"] if abilities else None

    with db() as conn:
        row = conn.execute(
            "SELECT moves, ability FROM poke_pokemon_config WHERE user_id = ? AND dex_id = ?", (user_id, dex_id)
        ).fetchone()
    moves = json.loads(row["moves"]) if row and row["moves"] else default_moves
    ability = row["ability"] if row and row["ability"] else default_ability
    return moves, ability


IV_COLUMNS = ("iv_hp", "iv_attack", "iv_defense", "iv_sp_attack", "iv_sp_defense", "iv_speed")


def ivs_from_collection_row(row: sqlite3.Row) -> dict:
    """Reads the iv_* columns off a poke_collection row into the {hp, attack,
    ...} shape battle_engine expects. Existing rows caught before IVs existed
    were backfilled to max (31) so nobody's Pokémon got retroactively
    weaker — see the migration in cogs/pokemon.py's _init_db."""
    return {
        key: row[col] if row[col] is not None else 31
        for key, col in zip(be.IV_STAT_KEYS, IV_COLUMNS)
    }


def get_best_ivs_for_species(user_id: int, dex_id: int) -> dict | None:
    """The IVs of whichever individual of this species (by total IV) the
    player owns — used anywhere a species-keyed feature (the team, the
    per-species move/ability config) needs to show/use 'your' stats for
    that species, since poke_team/poke_pokemon_config aren't per-catch.
    Returns None if the player owns none of this species."""
    with db() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(IV_COLUMNS)} FROM poke_collection WHERE user_id = ? AND dex_id = ?",
            (user_id, dex_id),
        ).fetchall()
    if not rows:
        return None
    best = max(rows, key=lambda r: sum(ivs_from_collection_row(r).values()))
    return ivs_from_collection_row(best)


def trainer_display_name(conn: sqlite3.Connection, user_id: int | None) -> str:
    if not user_id:
        return "Trainer"
    row = conn.execute("SELECT username FROM poke_trainers WHERE user_id = ?", (user_id,)).fetchone()
    return row["username"] if row and row["username"] else "Trainer"


# ---------- Roster building ----------

def build_roster_for_player(user_id: int) -> list["be.BattlerState"] | None:
    team = get_team(user_id)
    if not team:
        return None
    roster = []
    for dex_id in team:
        mon = POKEDEX.get(dex_id)
        if not mon:
            continue
        moves, ability = get_battle_pokemon_config(user_id, dex_id)
        ivs = get_best_ivs_for_species(user_id, dex_id)
        roster.append(be.build_battler_state(mon, moves, ability, ivs=ivs))
    return roster or None


def build_roster_for_gym(gym_key: str) -> list["be.BattlerState"]:
    gym = GYMS[gym_key]
    return [
        be.build_battler_state(POKEDEX[entry["dex_id"]], entry["moves"], entry.get("ability"))
        for entry in gym["roster"]
    ]


def build_roster_for_league(league_key: str) -> list["be.BattlerState"]:
    entry = LEAGUE[league_key]
    return [
        be.build_battler_state(POKEDEX[m["dex_id"]], m["moves"], m.get("ability"))
        for m in entry["roster"]
    ]


def build_roster_for_random_trainer(class_key: str) -> list["be.BattlerState"]:
    tclass = TRAINER_CLASS_BY_KEY[class_key]
    lo, hi = tclass["team_size"]
    size = random.randint(lo, hi)
    candidates = [
        dex_id for dex_id, mon in POKEDEX.items()
        if not mon.get("is_legendary") and not mon.get("is_mythical")
        and any(t in tclass["preferred_types"] for t in mon.get("types", []))
    ]
    chosen = random.sample(candidates, min(size, len(candidates)))
    roster = []
    for dex_id in chosen:
        mon = POKEDEX[dex_id]
        pool = mon.get("moves", [])
        move_names = [m["name"] for m in random.sample(pool, min(4, len(pool)))]
        abilities = mon.get("abilities", [])
        ability = random.choice(abilities)["name"] if abilities else None
        roster.append(be.build_battler_state(mon, move_names, ability))
    return roster


# ---------- Battle session CRUD ----------

def has_active_battle(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM poke_battles WHERE status IN ('pending', 'active', 'awaiting_forced_switch') "
            "AND (side_a_user_id = ? OR side_b_user_id = ?) LIMIT 1",
            (user_id, user_id),
        ).fetchone()
    return row is not None


def create_battle(battle_type: str, side_a_user_id: int, side_b_user_id: int | None,
                   side_b_npc_key: str | None, guild_id: int | None, channel_id: int | None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO poke_battles (battle_type, status, side_a_user_id, side_b_user_id, side_b_npc_key, "
            "guild_id, channel_id, current_turn_number, created_at, updated_at) "
            "VALUES (?, 'pending', ?, ?, ?, ?, ?, 0, ?, ?)",
            (battle_type, side_a_user_id, side_b_user_id, side_b_npc_key, guild_id, channel_id, now, now),
        )
        return cur.lastrowid


def get_battle_row(battle_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute("SELECT * FROM poke_battles WHERE battle_id = ?", (battle_id,)).fetchone()


def touch_battle(battle_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE poke_battles SET updated_at = ? WHERE battle_id = ?",
            (datetime.now(timezone.utc).isoformat(), battle_id),
        )


def abandon_battle(battle_id: int):
    with db() as conn:
        conn.execute(
            "UPDATE poke_battles SET status = 'abandoned', updated_at = ? "
            "WHERE battle_id = ? AND status IN ('pending', 'active', 'awaiting_forced_switch')",
            (datetime.now(timezone.utc).isoformat(), battle_id),
        )


def start_battle_sides(battle_id: int, roster_a: list["be.BattlerState"], roster_b: list["be.BattlerState"]):
    now = datetime.now(timezone.utc).isoformat()
    deadline = (datetime.now(timezone.utc) + timedelta(seconds=BATTLE_TURN_TIMEOUT_SECONDS)).isoformat()
    with db() as conn:
        for side_label, roster in (("A", roster_a), ("B", roster_b)):
            for slot, b in enumerate(roster):
                fields = be.battler_state_to_row_fields(b)
                conn.execute(
                    "INSERT INTO poke_battle_sides (battle_id, side, slot, dex_id, ability, current_hp, max_hp, "
                    "status, status_counter, stat_stages, confusion_counter, moves, is_active, is_fainted, volatile, ivs) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        battle_id, side_label, slot, fields["dex_id"], b.ability, fields["current_hp"],
                        fields["max_hp"], fields["status"], fields["status_counter"],
                        json.dumps(fields["stat_stages"]), fields["confusion_counter"],
                        json.dumps(fields["moves"]), 1 if slot == 0 else 0,
                        1 if fields["is_fainted"] else 0, json.dumps(fields["volatile"]),
                        json.dumps(fields["ivs"]),
                    ),
                )
        conn.execute(
            "UPDATE poke_battles SET status = 'active', current_turn_number = 1, turn_deadline = ?, "
            "updated_at = ? WHERE battle_id = ?",
            (deadline, now, battle_id),
        )

    # The opening send-outs never went through resolve_turn (which is what
    # normally emits switch_in), so without this the battle log — and the
    # web room's send-out animation — would have nothing to show for how
    # the first two Pokémon got there.
    append_battle_events(battle_id, 1, [
        {"type": "switch_in", "side": "A", "dex_id": roster_a[0].dex_id, "name": roster_a[0].species_name},
        {"type": "switch_in", "side": "B", "dex_id": roster_b[0].dex_id, "name": roster_b[0].species_name},
    ])


def load_battle_state(battle_id: int) -> tuple["be.BattleState", sqlite3.Row] | tuple[None, None]:
    battle_row = get_battle_row(battle_id)
    if not battle_row:
        return None, None
    with db() as conn:
        side_rows = conn.execute(
            "SELECT * FROM poke_battle_sides WHERE battle_id = ? ORDER BY side, slot", (battle_id,)
        ).fetchall()

    roster_a, roster_b = [], []
    active_a, active_b = 0, 0
    for r in side_rows:
        mon = POKEDEX.get(r["dex_id"], {})
        row_dict = {
            "dex_id": r["dex_id"], "current_hp": r["current_hp"], "max_hp": r["max_hp"],
            "status": r["status"], "status_counter": r["status_counter"],
            "stat_stages": json.loads(r["stat_stages"]), "confusion_counter": r["confusion_counter"],
            "moves": json.loads(r["moves"]), "is_fainted": r["is_fainted"], "volatile": json.loads(r["volatile"]),
            "ivs": json.loads(r["ivs"]) if r["ivs"] else dict(be.MAX_IVS),
        }
        battler = be.battler_state_from_row(mon, row_dict, ability=r["ability"])
        if r["side"] == "A":
            if r["is_active"]:
                active_a = len(roster_a)
            roster_a.append(battler)
        else:
            if r["is_active"]:
                active_b = len(roster_b)
            roster_b.append(battler)

    side_a = be.BattleSide(side_id="A", controller=battle_row["side_a_user_id"], roster=roster_a, active_index=active_a)
    side_b = be.BattleSide(
        side_id="B", controller=(battle_row["side_b_user_id"] or "npc"), roster=roster_b, active_index=active_b
    )
    forced = battle_row["forced_switch_side"].split(",") if battle_row["forced_switch_side"] else []
    battle = be.BattleState(
        battle_id=battle_id, side_a=side_a, side_b=side_b,
        turn_number=battle_row["current_turn_number"], status=battle_row["status"],
        winner_side=battle_row["winner_side"], forced_switch_sides=forced,
    )
    return battle, battle_row


def persist_battle_state(battle_id: int, battle: "be.BattleState"):
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        for side_label, side in (("A", battle.side_a), ("B", battle.side_b)):
            for slot, b in enumerate(side.roster):
                fields = be.battler_state_to_row_fields(b)
                conn.execute(
                    "UPDATE poke_battle_sides SET current_hp = ?, max_hp = ?, status = ?, status_counter = ?, "
                    "stat_stages = ?, confusion_counter = ?, moves = ?, is_active = ?, is_fainted = ?, volatile = ? "
                    "WHERE battle_id = ? AND side = ? AND slot = ?",
                    (
                        fields["current_hp"], fields["max_hp"], fields["status"], fields["status_counter"],
                        json.dumps(fields["stat_stages"]), fields["confusion_counter"], json.dumps(fields["moves"]),
                        1 if slot == side.active_index else 0, 1 if fields["is_fainted"] else 0,
                        json.dumps(fields["volatile"]), battle_id, side_label, slot,
                    ),
                )
        deadline = (
            (datetime.now(timezone.utc) + timedelta(seconds=BATTLE_TURN_TIMEOUT_SECONDS)).isoformat()
            if battle.status == "active" else None
        )
        conn.execute(
            "UPDATE poke_battles SET status = ?, current_turn_number = ?, forced_switch_side = ?, "
            "winner_side = ?, turn_deadline = ?, updated_at = ?, "
            "finished_at = CASE WHEN ? = 'finished' THEN COALESCE(finished_at, ?) ELSE finished_at END "
            "WHERE battle_id = ?",
            (
                battle.status, battle.turn_number, ",".join(battle.forced_switch_sides) or None,
                battle.winner_side, deadline, now, battle.status, now, battle_id,
            ),
        )


def append_battle_events(battle_id: int, turn_number: int, events: list[dict]):
    if not events:
        return
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        next_seq = conn.execute(
            "SELECT COALESCE(MAX(seq), -1) + 1 FROM poke_battle_events WHERE battle_id = ? AND turn_number = ?",
            (battle_id, turn_number),
        ).fetchone()[0]
        for i, event in enumerate(events):
            conn.execute(
                "INSERT INTO poke_battle_events (battle_id, turn_number, seq, event_type, payload, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (battle_id, turn_number, next_seq + i, event["type"], json.dumps(event), now),
            )


def record_pending_action(battle_id: int, side: str, turn_number: int, action: "be.Action"):
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO poke_battle_pending_actions "
            "(battle_id, side, turn_number, action_kind, move_slot, switch_to_slot, submitted_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                battle_id, side, turn_number, action.kind, action.move_index, action.switch_to_index,
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_pending_action(battle_id: int, side: str, turn_number: int) -> "be.Action | None":
    with db() as conn:
        row = conn.execute(
            "SELECT action_kind, move_slot, switch_to_slot FROM poke_battle_pending_actions "
            "WHERE battle_id = ? AND side = ? AND turn_number = ?",
            (battle_id, side, turn_number),
        ).fetchone()
    if not row:
        return None
    return be.Action(kind=row["action_kind"], side=side, move_index=row["move_slot"], switch_to_index=row["switch_to_slot"])


def clear_pending_actions(battle_id: int, turn_number: int):
    with db() as conn:
        conn.execute(
            "DELETE FROM poke_battle_pending_actions WHERE battle_id = ? AND turn_number = ?",
            (battle_id, turn_number),
        )


def get_badges(user_id: int) -> set[str]:
    with db() as conn:
        rows = conn.execute("SELECT gym_key FROM poke_badges WHERE user_id = ?", (user_id,)).fetchall()
    return {r["gym_key"] for r in rows}


def award_badge(user_id: int, gym_key: str):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO poke_badges (user_id, gym_key, earned_at) VALUES (?, ?, ?)",
            (user_id, gym_key, datetime.now(timezone.utc).isoformat()),
        )


def get_gym_clearers(gym_key: str, limit: int = 100) -> list[dict]:
    """Trainers who've earned this gym's badge, most recent first — powers
    the gym card's "trainers who beat this gym" list."""
    with db() as conn:
        rows = conn.execute(
            "SELECT pb.earned_at, pt.username FROM poke_badges pb "
            "JOIN poke_trainers pt ON pt.user_id = pb.user_id "
            "WHERE pb.gym_key = ? ORDER BY pb.earned_at DESC LIMIT ?",
            (gym_key, limit),
        ).fetchall()
    return [{"username": r["username"] or "Trainer", "earned_at": r["earned_at"]} for r in rows]


def next_gym_key(user_id: int, region: str) -> str | None:
    """The next unbeaten gym in this region's fixed order, or None once all
    8 of that region's gyms are defeated."""
    earned = get_badges(user_id)
    for gym_key in gym_order(region):
        if gym_key not in earned:
            return gym_key
    return None


def all_badges_earned(user_id: int) -> bool:
    """Every gym across every region — the gate for the Poké League."""
    return len(get_badges(user_id) & set(GYMS.keys())) >= len(GYMS)


def league_unlocked(user_id: int) -> bool:
    """The Poké League opens once every gym badge, across all 4 regions, is earned."""
    return all_badges_earned(user_id)


def get_league_progress(user_id: int) -> set[str]:
    with db() as conn:
        rows = conn.execute(
            "SELECT league_key FROM poke_league_progress WHERE user_id = ?", (user_id,)
        ).fetchall()
    return {r["league_key"] for r in rows}


def award_league_progress(user_id: int, league_key: str):
    with db() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO poke_league_progress (user_id, league_key, earned_at) VALUES (?, ?, ?)",
            (user_id, league_key, datetime.now(timezone.utc).isoformat()),
        )


def next_elite_four_key(user_id: int, generation: str) -> str | None:
    """The next unbeaten Elite Four member in this generation's fixed order,
    or None once all 4 are defeated."""
    earned = get_league_progress(user_id)
    for key in league_order(generation, "elite_four"):
        if key not in earned:
            return key
    return None


def champion_key(generation: str) -> str | None:
    keys = league_order(generation, "champion")
    return keys[0] if keys else None


def champion_unlocked(user_id: int, generation: str) -> bool:
    """The Champion fight opens once that generation's 4 Elite Four members
    are all defeated."""
    return next_elite_four_key(user_id, generation) is None


# Poké League completionist reward: a one-off mystery Pokémon (real design
# TBD — a "???" placeholder in data/pokemon.json for now) granted the moment
# a user has defeated every Elite Four member and every Champion across all
# 4 generations. Excluded from normal wild spawns (see cogs/pokemon.py's
# roll_spawn_mon) so this is the only way to obtain it.
LEAGUE_REWARD_DEX_ID = 494


def league_completion(user_id: int) -> tuple[int, int]:
    """(earned, total) across every Elite Four member + Champion in the game."""
    earned = len(get_league_progress(user_id) & set(LEAGUE.keys()))
    return earned, len(LEAGUE)


def league_fully_completed(user_id: int) -> bool:
    earned, total = league_completion(user_id)
    return earned >= total


def has_league_reward(user_id: int) -> bool:
    with db() as conn:
        row = conn.execute(
            "SELECT 1 FROM poke_collection WHERE user_id = ? AND dex_id = ? LIMIT 1",
            (user_id, LEAGUE_REWARD_DEX_ID),
        ).fetchone()
    return row is not None


def award_league_reward_if_new(user_id: int) -> bool:
    """Grants the League completionist's mystery Pokémon exactly once — safe
    to call unconditionally any time (checks league_fully_completed() itself,
    on top of never granting a duplicate). Returns True only the one time it
    actually granted it."""
    if not league_fully_completed(user_id) or has_league_reward(user_id):
        return False
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute(
            "INSERT INTO poke_collection (user_id, dex_id, caught_at, is_shiny, "
            "iv_hp, iv_attack, iv_defense, iv_sp_attack, iv_sp_defense, iv_speed) "
            "VALUES (?, ?, ?, 0, 31, 31, 31, 31, 31, 31)",
            (user_id, LEAGUE_REWARD_DEX_ID, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO poke_dex_seen (user_id, dex_id, first_caught_at) VALUES (?, ?, ?)",
            (user_id, LEAGUE_REWARD_DEX_ID, now),
        )
    return True


def add_xp_and_coins(user_id: int, xp: int, coin: int):
    """Mirrors cogs/pokemon.py's add_xp/add_item so a win grants the exact
    same account progression regardless of which process resolved the
    winning turn (the bot for creation-time bookkeeping, or the API for the
    actual turn that ended the battle)."""
    with db() as conn:
        for item, delta in (("xp", xp), ("coin", coin)):
            conn.execute(
                "INSERT INTO poke_items (user_id, item, qty) VALUES (?, ?, MAX(?, 0)) "
                "ON CONFLICT(user_id, item) DO UPDATE SET qty = MAX(qty + ?, 0)",
                (user_id, item, delta, delta),
            )


# Keep in sync with cogs/pokemon.py's / webapi.py's TRAINER_CHARACTERS —
# duplicated here (this module has zero discord/fastapi deps) so a battle's
# HUD can show a human trainer's chosen character sprite without either
# process reaching into the other's constants.
TRAINER_CHARACTER_SPRITE_BASE = "https://shawtypoke-web.duckdns.org/static/trainers"
TRAINER_CHARACTER_KEYS = ["red", "leaf", "gold", "kris", "brendan", "may", "lucas", "dawn"]
DEFAULT_TRAINER_CHARACTER = "red"


def avatar_for_side(battle_row: sqlite3.Row, side: str) -> str | None:
    """A small portrait for the battle-room HUD: the human's chosen trainer
    character for a real user, the gym leader's artwork for a gym battle, or
    that trainer class's sprite for a random-trainer battle."""
    user_id = battle_row["side_a_user_id"] if side == "A" else battle_row["side_b_user_id"]
    if user_id:
        with db() as conn:
            row = conn.execute("SELECT character FROM poke_trainers WHERE user_id = ?", (user_id,)).fetchone()
        character = row["character"] if row and row["character"] in TRAINER_CHARACTER_KEYS else DEFAULT_TRAINER_CHARACTER
        return f"{TRAINER_CHARACTER_SPRITE_BASE}/{character}.png"
    npc_key = battle_row["side_b_npc_key"]
    if battle_row["battle_type"] == "gym":
        return GYMS.get(npc_key, {}).get("leader_image")
    if battle_row["battle_type"] in ("elite_four", "champion"):
        return LEAGUE.get(npc_key, {}).get("portrait")
    tclass = TRAINER_CLASS_BY_KEY.get(npc_key)
    return tclass.get("sprite") if tclass else None


def display_name_for_side(battle_row: sqlite3.Row, side: str) -> str:
    user_id = battle_row["side_a_user_id"] if side == "A" else battle_row["side_b_user_id"]
    if user_id:
        with db() as conn:
            return trainer_display_name(conn, user_id)
    npc_key = battle_row["side_b_npc_key"]
    if battle_row["battle_type"] == "gym":
        return GYMS.get(npc_key, {}).get("leader_name", "Gym Leader")
    if battle_row["battle_type"] in ("elite_four", "champion"):
        return LEAGUE.get(npc_key, {}).get("name", "League Trainer")
    tclass = TRAINER_CLASS_BY_KEY.get(npc_key)
    return tclass["display_name"] if tclass else "Wild Trainer"


def grant_battle_rewards(battle_row: sqlite3.Row, battle: "be.BattleState") -> str | None:
    """Only a human winner gets rewards; losing costs nothing."""
    if battle.winner_side not in ("A", "B"):
        return None
    winner_user_id = battle_row["side_a_user_id"] if battle.winner_side == "A" else battle_row["side_b_user_id"]
    if not winner_user_id:
        return None

    battle_type = battle_row["battle_type"]
    if battle_type == "pvp":
        add_xp_and_coins(winner_user_id, PVP_WIN_XP, PVP_WIN_COIN)
        summary = f"+{PVP_WIN_XP} XP, +{PVP_WIN_COIN} coins"
    elif battle_type == "gym":
        gym_key = battle_row["side_b_npc_key"]
        add_xp_and_coins(winner_user_id, GYM_BATTLE_XP, GYM_BATTLE_COIN)
        award_badge(winner_user_id, gym_key)
        badge_name = GYMS.get(gym_key, {}).get("badge_name", "Badge")
        summary = f"+{GYM_BATTLE_XP} XP, +{GYM_BATTLE_COIN} coins, and the {badge_name}!"
    elif battle_type == "elite_four":
        league_key = battle_row["side_b_npc_key"]
        add_xp_and_coins(winner_user_id, LEAGUE_BATTLE_XP, LEAGUE_BATTLE_COIN)
        award_league_progress(winner_user_id, league_key)
        member_name = LEAGUE.get(league_key, {}).get("name", "the Elite Four member")
        summary = f"+{LEAGUE_BATTLE_XP} XP, +{LEAGUE_BATTLE_COIN} coins! {member_name} has been defeated."
    elif battle_type == "champion":
        league_key = battle_row["side_b_npc_key"]
        add_xp_and_coins(winner_user_id, CHAMPION_BATTLE_XP, CHAMPION_BATTLE_COIN)
        award_league_progress(winner_user_id, league_key)
        champ_name = LEAGUE.get(league_key, {}).get("name", "the Champion")
        summary = f"+{CHAMPION_BATTLE_XP} XP, +{CHAMPION_BATTLE_COIN} coins! {champ_name} has fallen — Champion crowned!"
    else:
        tclass = TRAINER_CLASS_BY_KEY.get(battle_row["side_b_npc_key"])
        rewards = TRAINER_BATTLE_REWARDS[tclass["reward_tier"] if tclass else "low"]
        add_xp_and_coins(winner_user_id, rewards["xp"], rewards["coin"])
        summary = f"+{rewards['xp']} XP, +{rewards['coin']} coins"

    if battle_type in ("elite_four", "champion") and award_league_reward_if_new(winner_user_id):
        summary += " 🎁 You've conquered the entire Poké League and received a Mystery Pokémon! Check the Rewards page."

    return summary


# NPC battles the League tier: given every trainer already fields max-IV,
# level-100 Pokémon, a smarter opponent (battle_engine's "hard" AI) is the
# only real lever left to make these fights tougher than a gym.
HARD_AI_BATTLE_TYPES = {"elite_four", "champion"}


def npc_pick_action(battle: "be.BattleState", side_id: str, battle_row: sqlite3.Row) -> "be.Action":
    if battle_row["battle_type"] in HARD_AI_BATTLE_TYPES:
        return be.pick_npc_action_hard(battle, side_id)
    return be.pick_npc_action(battle, side_id)


def npc_pick_forced_switch(battle: "be.BattleState", side_id: str, battle_row: sqlite3.Row) -> int:
    if battle_row["battle_type"] in HARD_AI_BATTLE_TYPES:
        return be.pick_npc_forced_switch_hard(battle, side_id)
    return be.pick_npc_forced_switch(battle, side_id)


# ---------- Turn / switch / forfeit orchestration ----------

def resolve_battle_turn(battle_id: int, action_a: "be.Action", action_b: "be.Action"):
    """Resolves one turn, auto-cascading any NPC-side forced switch. Returns
    (battle, battle_row, all_events, reward_summary_or_None)."""
    battle, battle_row = load_battle_state(battle_id)
    if not battle or battle.status != "active":
        return battle, battle_row, [], None
    turn_number = battle.turn_number

    result = be.resolve_turn(battle, action_a, action_b)
    persist_battle_state(battle_id, battle)
    append_battle_events(battle_id, turn_number, result.events)
    clear_pending_actions(battle_id, turn_number)
    all_events = list(result.events)

    while battle.status == "awaiting_forced_switch":
        npc_side = next((s for s in battle.forced_switch_sides if battle.side(s).controller == "npc"), None)
        if not npc_side:
            break
        idx = npc_pick_forced_switch(battle, npc_side, battle_row)
        switch_events = be.apply_forced_switch(battle, npc_side, idx)
        persist_battle_state(battle_id, battle)
        append_battle_events(battle_id, turn_number, switch_events)
        all_events.extend(switch_events)

    battle_row = get_battle_row(battle_id)
    reward_summary = grant_battle_rewards(battle_row, battle) if battle.status == "finished" else None
    return battle, battle_row, all_events, reward_summary


def handle_forced_switch(battle_id: int, side: str, team_index: int):
    """Returns (battle, battle_row, all_events)."""
    battle, battle_row = load_battle_state(battle_id)
    if not battle or battle.status != "awaiting_forced_switch" or side not in battle.forced_switch_sides:
        return battle, battle_row, []
    turn_number = battle.turn_number

    events = be.apply_forced_switch(battle, side, team_index)
    persist_battle_state(battle_id, battle)
    append_battle_events(battle_id, turn_number, events)
    all_events = list(events)

    while battle.status == "awaiting_forced_switch":
        npc_side = next((s for s in battle.forced_switch_sides if battle.side(s).controller == "npc"), None)
        if not npc_side:
            break
        idx = npc_pick_forced_switch(battle, npc_side, battle_row)
        npc_events = be.apply_forced_switch(battle, npc_side, idx)
        persist_battle_state(battle_id, battle)
        append_battle_events(battle_id, turn_number, npc_events)
        all_events.extend(npc_events)

    battle_row = get_battle_row(battle_id)
    return battle, battle_row, all_events


def apply_forfeit(battle_id: int, forfeiting_side: str):
    """Ends the battle immediately; the other side wins and is rewarded
    normally. Returns (battle, battle_row, reward_summary_or_None)."""
    battle, battle_row = load_battle_state(battle_id)
    if not battle or battle.status not in ("active", "awaiting_forced_switch"):
        return battle, battle_row, None
    winner_side = "B" if forfeiting_side == "A" else "A"
    battle.status = "finished"
    battle.winner_side = winner_side
    battle.forced_switch_sides = []
    persist_battle_state(battle_id, battle)
    append_battle_events(battle_id, battle.turn_number, [
        {"type": "battle_end", "winner_side": winner_side, "reason": "forfeit"}
    ])
    battle_row = get_battle_row(battle_id)
    reward_summary = grant_battle_rewards(battle_row, battle)
    return battle, battle_row, reward_summary


def accept_challenge(battle_id: int):
    """Returns (ok, battle, battle_row, error_message)."""
    battle_row = get_battle_row(battle_id)
    if not battle_row or battle_row["status"] != "pending":
        return False, None, battle_row, "This challenge is no longer available."
    roster_a = build_roster_for_player(battle_row["side_a_user_id"])
    roster_b = build_roster_for_player(battle_row["side_b_user_id"])
    if not roster_a or not roster_b:
        abandon_battle(battle_id)
        return False, None, battle_row, "One of you doesn't have a team set (use the Team page first)."
    start_battle_sides(battle_id, roster_a, roster_b)
    battle, battle_row = load_battle_state(battle_id)
    return True, battle, battle_row, None


def decline_challenge(battle_id: int):
    abandon_battle(battle_id)


# ---------- Viewer-facing legal-action info ----------

def get_viewer_info(battle: "be.BattleState", battle_row: sqlite3.Row, user_id: int | None) -> dict:
    """What the requesting user is allowed to do right now, if anything —
    the single source of truth the web battle UI renders its action panel
    from."""
    side = None
    if user_id and battle_row["side_a_user_id"] == user_id:
        side = "A"
    elif user_id and battle_row["side_b_user_id"] == user_id:
        side = "B"

    info = {
        "side": side,
        "is_pending_target": side == "B" and battle_row["status"] == "pending",
        "can_act": False,
        "usable_move_indices": [],
        "can_switch": False,
        "switchable_indices": [],
        "needs_forced_switch": False,
        "already_locked_in": False,
        "waiting_on_opponent": False,
    }
    if not side or battle.status not in ("active", "awaiting_forced_switch"):
        return info

    if battle.status == "awaiting_forced_switch":
        if side in battle.forced_switch_sides:
            legal = be.legal_actions(battle, side)
            info["needs_forced_switch"] = True
            info["switchable_indices"] = legal["switchable_indices"]
        return info

    pending = get_pending_action(battle.battle_id, side, battle.turn_number)
    if pending is not None:
        info["already_locked_in"] = True
        other_side = "B" if side == "A" else "A"
        if battle.side(other_side).controller != "npc":
            info["waiting_on_opponent"] = get_pending_action(battle.battle_id, other_side, battle.turn_number) is None
        return info

    legal = be.legal_actions(battle, side)
    info["can_act"] = True
    info["usable_move_indices"] = legal["usable_move_indices"]
    info["can_switch"] = legal["can_switch"]
    info["switchable_indices"] = legal["switchable_indices"]
    return info


# ---------- Sweep (timeouts / abandonment) ----------

def sweep_battles() -> list[int]:
    """Auto-resolves any turn whose timer elapsed (auto-picks a random usable
    move for whichever human side didn't choose — never a forfeit) and
    abandons battles idle past their threshold. Returns the battle_ids that
    changed, so the caller can broadcast updates for them."""
    now = datetime.now(timezone.utc)
    changed: list[int] = []
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM poke_battles WHERE status IN ('pending', 'active', 'awaiting_forced_switch')"
        ).fetchall()

    for row in rows:
        idle_seconds = (now - datetime.fromisoformat(row["updated_at"])).total_seconds()
        threshold = BATTLE_PENDING_TIMEOUT_SECONDS if row["status"] == "pending" else BATTLE_ABANDON_SECONDS
        if idle_seconds > threshold:
            abandon_battle(row["battle_id"])
            changed.append(row["battle_id"])
            continue

        if row["status"] != "active" or not row["turn_deadline"]:
            continue
        if now < datetime.fromisoformat(row["turn_deadline"]):
            continue

        battle, _ = load_battle_state(row["battle_id"])
        if not battle or battle.status != "active":
            continue
        turn_number = battle.turn_number

        def resolve_side(side_id: str, controller) -> "be.Action":
            if controller == "npc":
                return npc_pick_action(battle, side_id, row)
            existing = get_pending_action(row["battle_id"], side_id, turn_number)
            if existing is not None:
                return existing
            legal = be.legal_actions(battle, side_id)
            if legal["usable_move_indices"]:
                return be.Action(kind="move", side=side_id, move_index=random.choice(legal["usable_move_indices"]))
            return be.Action(kind="move", side=side_id, move_index=None)

        action_a = resolve_side("A", battle.side_a.controller)
        action_b = resolve_side("B", battle.side_b.controller)
        resolve_battle_turn(row["battle_id"], action_a, action_b)
        changed.append(row["battle_id"])

    return changed


# ---------- Serialization (shared by the GET endpoint and WS broadcasts) ----------

def summarize_battle(conn: sqlite3.Connection, row: sqlite3.Row, viewer_user_id: int | None = None) -> dict:
    name_a = trainer_display_name(conn, row["side_a_user_id"])
    name_b = display_name_for_side(row, "B")
    winner_name = None
    if row["winner_side"] in ("A", "B"):
        winner_name = name_a if row["winner_side"] == "A" else name_b

    viewer_side = None
    if viewer_user_id is not None:
        if row["side_a_user_id"] == viewer_user_id:
            viewer_side = "A"
        elif row["side_b_user_id"] == viewer_user_id:
            viewer_side = "B"
    your_result = None
    if viewer_side and row["winner_side"] in ("A", "B"):
        your_result = "win" if row["winner_side"] == viewer_side else "loss"

    return {
        "battle_id": row["battle_id"], "battle_type": row["battle_type"], "status": row["status"],
        "name_a": name_a, "name_b": name_b,
        "avatar_a": avatar_for_side(row, "A"), "avatar_b": avatar_for_side(row, "B"),
        # Stringified — Discord snowflake IDs exceed JS's safe integer range,
        # so a plain JSON number would get silently corrupted by the browser.
        "side_a_user_id": str(row["side_a_user_id"]) if row["side_a_user_id"] else None,
        "side_b_user_id": str(row["side_b_user_id"]) if row["side_b_user_id"] else None,
        "is_participant": viewer_side is not None,
        "turn_number": row["current_turn_number"],
        "winner_name": winner_name, "your_result": your_result,
        "created_at": row["created_at"], "finished_at": row["finished_at"],
    }


def arena_type_for(battle_row: sqlite3.Row) -> str | None:
    """The Pokémon type an NPC battle's arena is themed around (a gym's or
    Elite Four member's specialty), or None for PvP, random trainers and
    Champions (who use mixed teams)."""
    npc_key = battle_row["side_b_npc_key"]
    if battle_row["battle_type"] == "gym":
        return GYMS.get(npc_key, {}).get("type_theme")
    if battle_row["battle_type"] in ("elite_four", "champion"):
        return LEAGUE.get(npc_key, {}).get("type_theme")
    return None


def serialize_battle_detail(battle_id: int, viewer_user_id: int | None = None) -> dict | None:
    battle, battle_row = load_battle_state(battle_id)
    if not battle_row:
        return None
    name_a = display_name_for_side(battle_row, "A")
    name_b = display_name_for_side(battle_row, "B")

    with db() as conn:
        side_rows = conn.execute(
            "SELECT * FROM poke_battle_sides WHERE battle_id = ? ORDER BY side, slot", (battle_id,)
        ).fetchall()
        event_rows = conn.execute(
            "SELECT id, payload FROM poke_battle_events WHERE battle_id = ? ORDER BY turn_number DESC, seq DESC LIMIT 60",
            (battle_id,),
        ).fetchall()

    # Battle-scene sprite orientation: YOUR OWN side is always shown from
    # behind (as in the real games), the other side faces you. A spectator
    # (no recognized side) gets a neutral default of A-back/B-front.
    viewer_side = None
    if viewer_user_id is not None:
        if battle_row["side_a_user_id"] == viewer_user_id:
            viewer_side = "A"
        elif battle_row["side_b_user_id"] == viewer_user_id:
            viewer_side = "B"
    back_side = viewer_side or "A"

    # The opposing active Pokémon's types, so revealed moves can say how
    # effective they'd be right now (the move tooltip on the battle page).
    active_types = {
        r["side"]: POKEDEX.get(r["dex_id"], {}).get("types", [])
        for r in side_rows if r["is_active"] and not r["is_fainted"]
    }
    move_detail_keys = (
        "category", "power", "accuracy", "priority", "description", "ailment", "ailment_chance",
        "stat_changes", "stat_chance", "crit_rate", "drain_percent", "recoil_percent", "healing_percent",
        "flinch_chance", "min_hits", "max_hits", "target", "flags",
    )

    def mon_summary(r: sqlite3.Row, reveal_moves: bool, side: str) -> dict:
        mon = POKEDEX.get(r["dex_id"], {})
        name = mon.get("name", f"#{r['dex_id']}")
        sprite_front, sprite_back = animated_sprite_urls(name)
        out = {
            "dex_id": r["dex_id"], "name": name, "artwork": mon.get("artwork"),
            "sprite": sprite_back if side == back_side else sprite_front,
            "types": mon.get("types", []), "current_hp": r["current_hp"], "max_hp": r["max_hp"],
            "status": r["status"], "is_active": bool(r["is_active"]), "is_fainted": bool(r["is_fainted"]),
            "stat_stages": {k: v for k, v in json.loads(r["stat_stages"] or "{}").items() if v},
            "confused": bool(r["confusion_counter"]),
        }
        if reveal_moves:
            moves = json.loads(r["moves"])
            pool_by_name = {m["name"]: m for m in mon.get("moves", [])}
            foe_types = active_types.get("B" if side == "A" else "A")
            out["moves"] = []
            for m in moves:
                info = pool_by_name.get(m["name"], {})
                move = {"name": m["name"], "pp": m["pp"], "max_pp": info.get("pp", m["pp"]), "type": info.get("type")}
                move.update({k: info.get(k) for k in move_detail_keys})
                move["stat_self"] = be.stat_changes_affect_user(info)
                if foe_types and info.get("category") != "status" and info.get("target") != "self":
                    move["effectiveness"] = be.effectiveness_label(be.type_effectiveness(info.get("type"), foe_types))
                out["moves"].append(move)
        return out

    reveal_a = viewer_user_id is not None and battle_row["side_a_user_id"] == viewer_user_id
    reveal_b = viewer_user_id is not None and battle_row["side_b_user_id"] == viewer_user_id
    roster_a = [mon_summary(r, reveal_a, "A") for r in side_rows if r["side"] == "A"]
    roster_b = [mon_summary(r, reveal_b, "B") for r in side_rows if r["side"] == "B"]
    # Only the latest 60 events are sent, so clients can't find "what's new"
    # by counting — each event carries its row id (insertion order, which
    # matches turn/seq order) for them to track instead.
    events = [{**json.loads(r["payload"]), "event_id": r["id"]} for r in reversed(event_rows)]

    winner_name = None
    if battle_row["winner_side"] in ("A", "B"):
        winner_name = name_a if battle_row["winner_side"] == "A" else name_b

    payload = {
        "battle_id": battle_id, "battle_type": battle_row["battle_type"], "status": battle_row["status"],
        "name_a": name_a, "name_b": name_b,
        "avatar_a": avatar_for_side(battle_row, "A"), "avatar_b": avatar_for_side(battle_row, "B"),
        "roster_a": roster_a, "roster_b": roster_b,
        "turn_number": battle_row["current_turn_number"], "winner_side": battle_row["winner_side"],
        "winner_name": winner_name, "events": events,
        "arena_type": arena_type_for(battle_row),
    }
    if viewer_user_id is not None:
        payload["you"] = get_viewer_info(battle, battle_row, viewer_user_id)
    return payload
