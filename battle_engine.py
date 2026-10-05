"""
Pure-Python Pokémon battle engine — zero discord.py/FastAPI imports, so both
the bot (cogs/pokemon.py, which drives real turns) and, if ever needed, the
web API can import it safely.

Scope (see the approved battle-system plan): an accurate damage formula,
types/STAB/effectiveness, accuracy rolls, critical hits, priority + speed
turn order, stat-stage changes, the main status conditions (burn,
paralysis, poison, toxic, sleep, freeze, confusion, flinch), and weather
(sun, rain, sandstorm, hail: moves, weather-setting abilities, and the
abilities that depend on weather; see the Weather section), competitive held
items (see the Held items section), and self-KO moves (Explosion etc.).
Other abilities are not simulated; ability is flavor text for those. Every battler is effectively level 100 (see webapi.py's
stat_at_level_100 — an owned Pokémon's stats are already fixed at that
level's value), which is why the damage formula below folds the level term
into a constant instead of taking a level parameter.

Singles format only: one active Pokémon per side, up to 6 per team.
"""
from __future__ import annotations

import contextvars
import dataclasses
import math
import random
import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Type effectiveness (modern/Gen 6+ chart, all 18 types). Sparse: only
# non-1.0 multipliers are listed; anything absent defaults to neutral (1.0).
# ---------------------------------------------------------------------------

TYPE_CHART: dict[str, dict[str, float]] = {
    "normal": {"rock": 0.5, "ghost": 0.0, "steel": 0.5},
    "fire": {"fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 2.0, "bug": 2.0,
             "rock": 0.5, "dragon": 0.5, "steel": 2.0},
    "water": {"fire": 2.0, "water": 0.5, "grass": 0.5, "ground": 2.0, "rock": 2.0, "dragon": 0.5},
    "electric": {"water": 2.0, "electric": 0.5, "grass": 0.5, "ground": 0.0, "flying": 2.0, "dragon": 0.5},
    "grass": {"fire": 0.5, "water": 2.0, "grass": 0.5, "poison": 0.5, "ground": 2.0,
              "flying": 0.5, "bug": 0.5, "rock": 2.0, "dragon": 0.5, "steel": 0.5},
    "ice": {"fire": 0.5, "water": 0.5, "grass": 2.0, "ice": 0.5, "ground": 2.0,
            "flying": 2.0, "dragon": 2.0, "steel": 0.5},
    "fighting": {"normal": 2.0, "ice": 2.0, "poison": 0.5, "flying": 0.5, "psychic": 0.5,
                 "bug": 0.5, "rock": 2.0, "ghost": 0.0, "dark": 2.0, "steel": 2.0, "fairy": 0.5},
    "poison": {"grass": 2.0, "poison": 0.5, "ground": 0.5, "rock": 0.5, "ghost": 0.5,
               "steel": 0.0, "fairy": 2.0},
    "ground": {"fire": 2.0, "electric": 2.0, "grass": 0.5, "poison": 2.0, "flying": 0.0,
               "bug": 0.5, "rock": 2.0, "steel": 2.0},
    "flying": {"electric": 0.5, "grass": 2.0, "fighting": 2.0, "bug": 2.0, "rock": 0.5, "steel": 0.5},
    "psychic": {"fighting": 2.0, "poison": 2.0, "psychic": 0.5, "dark": 0.0, "steel": 0.5},
    "bug": {"fire": 0.5, "grass": 2.0, "fighting": 0.5, "poison": 0.5, "flying": 0.5,
            "psychic": 2.0, "ghost": 0.5, "dark": 2.0, "steel": 0.5, "fairy": 0.5},
    "rock": {"fire": 2.0, "ice": 2.0, "fighting": 0.5, "ground": 0.5, "flying": 2.0, "bug": 2.0, "steel": 0.5},
    "ghost": {"normal": 0.0, "psychic": 2.0, "ghost": 2.0, "dark": 0.5},
    "dragon": {"dragon": 2.0, "steel": 0.5, "fairy": 0.0},
    "dark": {"fighting": 0.5, "psychic": 2.0, "ghost": 2.0, "dark": 0.5, "fairy": 0.5},
    "steel": {"fire": 0.5, "water": 0.5, "electric": 0.5, "ice": 2.0, "rock": 2.0, "steel": 0.5, "fairy": 2.0},
    "fairy": {"fire": 0.5, "fighting": 2.0, "poison": 0.5, "dragon": 2.0, "dark": 2.0, "steel": 0.5},
}

CRIT_TABLE = {0: 1 / 16, 1: 1 / 8, 2: 1 / 2, 3: 1.0}
STAT_KEYS = ("attack", "defense", "sp_attack", "sp_defense", "speed", "accuracy", "evasion")
STAT_ALIASES = {"special_attack": "sp_attack", "special_defense": "sp_defense"}


IV_STAT_KEYS = ("hp", "attack", "defense", "sp_attack", "sp_defense", "speed")
MAX_IVS: dict[str, int] = {k: 31 for k in IV_STAT_KEYS}


def stat_at_level_100(base: int, iv: int, is_hp: bool, ev: int = 0) -> int:
    """Mirrors webapi.py's stat_at_level_100 — neutral nature, level 100,
    real per-individual IV (0-31). Players' Pokémon have no EVs; only boss
    trainers' do (0-252 per stat, +1 stat point per 4). Duplicated here (not
    imported) so this module stays free of any dependency on webapi.py's
    FastAPI app construction."""
    bonus = max(0, min(252, int(ev or 0))) // 4
    return 2 * base + iv + 110 + bonus if is_hp else 2 * base + iv + 5 + bonus


def stats_at_level_100(base_stats: dict, ivs: dict, evs: dict | None = None) -> dict:
    evs = evs or {}
    return {key: stat_at_level_100(base_stats.get(key, 1), ivs.get(key, 31), key == "hp", evs.get(key, 0))
            for key in IV_STAT_KEYS}


def type_effectiveness(move_type: str | None, defender_types: list[str]) -> float:
    if move_type is None:
        return 1.0
    mult = 1.0
    chart = TYPE_CHART.get(move_type, {})
    for t in defender_types:
        mult *= chart.get(t, 1.0)
    return mult


def stat_stage_multiplier(stage: int, is_accuracy_or_evasion: bool = False) -> float:
    stage = max(-6, min(6, stage))
    base = 3 if is_accuracy_or_evasion else 2
    if stage >= 0:
        return (base + stage) / base
    return base / (base - stage)


def effectiveness_label(mult: float) -> str:
    if mult == 0:
        return "no_effect"
    if mult < 1:
        return "not_very_effective"
    if mult > 1:
        return "super_effective"
    return "neutral"


# ---------------------------------------------------------------------------
# Move data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MoveData:
    name: str
    type: Optional[str]
    category: str  # "physical" | "special" | "status"
    power: Optional[int]
    accuracy: Optional[int]
    priority: int
    max_pp: int
    ailment: Optional[str]
    ailment_chance: int
    stat_changes: tuple
    stat_chance: int
    crit_rate: int
    drain_percent: Optional[int]
    recoil_percent: Optional[int]
    healing_percent: Optional[int]
    flinch_chance: int
    min_hits: Optional[int]
    max_hits: Optional[int]
    min_turns: Optional[int]
    max_turns: Optional[int]
    target: str  # "self" | "opponent"
    flags: frozenset
    fixed_damage_amount: Optional[int] = None
    # For damaging moves: do the stat changes hit the user (Close Combat,
    # Overheat, Metal Claw...) rather than the target? See stat_changes_affect_user.
    stat_self: bool = False


_LOWERS_USER = re.compile(r"\blowers?\s+(the\s+)?user", re.IGNORECASE)


def stat_changes_affect_user(d: dict) -> bool:
    """Whether a move's stat changes apply to its user instead of its target.

    The move data (from PokeAPI) doesn't record who a stat change applies to.
    Status moves already say it via `target` ("self" for Swords Dance etc.).
    For damaging moves: every stat *raise* in the data affects the user
    (Metal Claw, Flame Charge, Ancient Power...), and the drawbacks that
    lower the user's own stats (Close Combat, Overheat, Superpower...) say
    "lowers (the) user" in their description; any other drop hits the target.
    """
    changes = d.get("stat_changes") or []
    if not changes:
        return False
    if d.get("category") == "status":
        return d.get("target") == "self"
    if all(c.get("change", 0) > 0 for c in changes):
        return True
    return bool(_LOWERS_USER.search(d.get("description") or ""))


def move_data_from_dict(d: dict) -> MoveData:
    return MoveData(
        name=d["name"],
        type=d.get("type"),
        category=d.get("category", "status"),
        power=d.get("power"),
        accuracy=d.get("accuracy"),
        priority=d.get("priority", 0) or 0,
        max_pp=d.get("pp") or 1,
        ailment=d.get("ailment"),
        ailment_chance=d.get("ailment_chance", 0) or 0,
        stat_changes=tuple(d.get("stat_changes") or []),
        stat_chance=d.get("stat_chance", 0) or 0,
        crit_rate=d.get("crit_rate", 0) or 0,
        drain_percent=d.get("drain_percent"),
        recoil_percent=d.get("recoil_percent"),
        healing_percent=d.get("healing_percent"),
        flinch_chance=d.get("flinch_chance", 0) or 0,
        min_hits=d.get("min_hits"),
        max_hits=d.get("max_hits"),
        min_turns=d.get("min_turns"),
        max_turns=d.get("max_turns"),
        target=d.get("target", "opponent"),
        flags=frozenset(d.get("flags") or []),
        fixed_damage_amount=d.get("fixed_damage_amount"),
        stat_self=stat_changes_affect_user(d),
    )


STRUGGLE_MOVE = MoveData(
    name="Struggle", type=None, category="physical", power=50, accuracy=None, priority=0, max_pp=1,
    ailment=None, ailment_chance=0, stat_changes=(), stat_chance=0, crit_rate=0,
    drain_percent=None, recoil_percent=None, healing_percent=None, flinch_chance=0,
    min_hits=None, max_hits=None, min_turns=None, max_turns=None, target="opponent",
    flags=frozenset({"always_hit"}), fixed_damage_amount=None,
)


@dataclass
class MoveSlot:
    move: MoveData
    current_pp: int


# ---------------------------------------------------------------------------
# Battler / side / battle state
# ---------------------------------------------------------------------------

def default_stat_stages() -> dict:
    return {k: 0 for k in STAT_KEYS}


def default_volatile() -> dict:
    return {"must_recharge": False, "locked_move": None, "lock_turns_remaining": 0,
            "charging_move": None, "flinched": False, "invulnerable_until_turn": None}


@dataclass
class BattlerState:
    dex_id: int
    species_name: str
    types: list
    stats: dict  # hp/attack/defense/sp_attack/sp_defense/speed, resolved level-100 values
    max_hp: int
    current_hp: int
    ability: Optional[str] = None
    ivs: dict = field(default_factory=lambda: dict(MAX_IVS))  # same 6 keys, 0-31 each
    evs: dict = field(default_factory=dict)  # boss trainers only: stat -> 0-252
    moves: list = field(default_factory=list)  # list[MoveSlot], up to 4
    stat_stages: dict = field(default_factory=default_stat_stages)
    status: Optional[str] = None  # burn|paralysis|poison|toxic|sleep|freeze
    status_counter: int = 0
    confusion_counter: int = 0
    volatile: dict = field(default_factory=default_volatile)
    is_fainted: bool = False
    item: Optional[str] = None  # held item key (HELD_ITEMS); None once consumed
    weight: float = 100.0  # kg, for weight-based moves (Low Kick, Heavy Slam)


@dataclass
class BattleSide:
    side_id: str  # "A" | "B"
    controller: object  # Discord user id (int) or "npc"
    roster: list  # list[BattlerState], up to 6
    active_index: int = 0
    # Mega Evolutions an NPC side uses automatically: base dex_id -> the
    # Mega's pokedex entry (boss trainers, see battle_store.MEGA_TRAINERS).
    mega_forms: dict = field(default_factory=dict)
    # A player's unlocked Megas: base dex_id -> [Mega entries] (Charizard can
    # have X and Y). Used only when the player chooses to, once per battle.
    player_megas: dict = field(default_factory=dict)
    mega_used: bool = False
    # The boss AI keeps this Pokémon (its ace) for last.
    ace_index: Optional[int] = None

    @property
    def active(self) -> BattlerState:
        return self.roster[self.active_index]


@dataclass
class BattleState:
    battle_id: int
    side_a: BattleSide
    side_b: BattleSide
    turn_number: int = 1
    status: str = "active"  # active|awaiting_forced_switch|finished
    winner_side: Optional[str] = None
    forced_switch_sides: list = field(default_factory=list)
    rng: random.Random = field(default_factory=random.Random)
    weather: Optional[str] = None  # "sun" | "rain" | "sand" | "hail"
    weather_turns: int = 0         # turns left, counting the current one

    def side(self, side_id: str) -> BattleSide:
        return self.side_a if side_id == "A" else self.side_b

    def other(self, side_id: str) -> BattleSide:
        return self.side_b if side_id == "A" else self.side_a


@dataclass
class Action:
    kind: str  # "move" | "switch"
    side: str
    move_index: Optional[int] = None
    switch_to_index: Optional[int] = None
    mega: Optional[int] = None  # dex_id of the Mega a player evolves into this turn


@dataclass
class TurnResult:
    events: list
    side_a_needs_switch: bool
    side_b_needs_switch: bool
    battle_over: bool
    winner_side: Optional[str]


# ---------------------------------------------------------------------------
# Construction helpers
# ---------------------------------------------------------------------------

def build_battler_state(mon: dict, moves: list[str] | None, ability: str | None,
                         ivs: dict | None = None, item: str | None = None, evs: dict | None = None) -> BattlerState:
    """mon: one entry from data/pokemon.json (POKEDEX[dex_id]). moves: up to 4
    move names to load from that species' own embedded move pool (falls back
    to its first 4 known moves if not given/found). ivs: that individual's
    real 0-31-per-stat values; defaults to max (31) for gym/trainer NPCs,
    which have no owned Pokémon row to draw real IVs from."""
    ivs = ivs or MAX_IVS
    evs = dict(evs or {})
    stats = stats_at_level_100(mon.get("base_stats", {}), ivs, evs)
    pool_by_name = {m["name"]: m for m in mon.get("moves", [])}
    chosen = [n for n in (moves or []) if n in pool_by_name]
    if not chosen:
        chosen = [m["name"] for m in mon.get("moves", [])[:4]]

    move_slots = []
    for name in chosen[:4]:
        md = move_data_from_dict(pool_by_name[name])
        move_slots.append(MoveSlot(move=md, current_pp=md.max_pp))

    return BattlerState(
        dex_id=mon["id"], species_name=mon["name"], types=list(mon.get("types", [])),
        stats=stats, max_hp=stats["hp"], current_hp=stats["hp"], ability=ability,
        ivs=dict(ivs), evs=evs, moves=move_slots, item=item if item in HELD_ITEMS else None,
        weight=float(mon.get("weight") or 100.0),
    )


def build_battle_state(battle_id: int, side_a_controller, side_a_roster: list[BattlerState],
                        side_b_controller, side_b_roster: list[BattlerState],
                        rng: random.Random | None = None) -> BattleState:
    return BattleState(
        battle_id=battle_id,
        side_a=BattleSide(side_id="A", controller=side_a_controller, roster=side_a_roster),
        side_b=BattleSide(side_id="B", controller=side_b_controller, roster=side_b_roster),
        rng=rng or random.Random(),
    )


# ---------------------------------------------------------------------------
# DB (de)serialization helpers — the cog stores dex_id/current_hp/max_hp/
# status/status_counter/is_active/is_fainted as plain columns and
# stat_stages/moves/volatile as JSON blobs (see poke_battle_sides). Stats
# themselves are never stored — they're a pure function of base_stats, so
# they're recomputed fresh from the pokedex each time.
# ---------------------------------------------------------------------------

def battler_state_to_row_fields(b: BattlerState) -> dict:
    return {
        "dex_id": b.dex_id,
        "current_hp": b.current_hp,
        "max_hp": b.max_hp,
        "status": b.status,
        "status_counter": b.status_counter,
        "stat_stages": dict(b.stat_stages),
        "confusion_counter": b.confusion_counter,
        "moves": [{"name": ms.move.name, "pp": ms.current_pp} for ms in b.moves],
        "is_fainted": b.is_fainted,
        "volatile": dict(b.volatile),
        "ivs": dict(b.ivs),
        "evs": dict(b.evs),
        "item": b.item,
    }


def battler_state_from_row(mon: dict, row: dict, ability: str | None = None) -> BattlerState:
    """Rebuild a BattlerState for one roster slot from a poke_battle_sides row
    (already JSON-decoded) plus the species' pokedex entry. IVs are written
    once at battle start and never change, so they're just read back here."""
    ivs = row.get("ivs") or MAX_IVS
    evs = row.get("evs") or {}
    stats = stats_at_level_100(mon.get("base_stats", {}), ivs, evs)
    pool_by_name = {m["name"]: m for m in mon.get("moves", [])}
    move_slots = []
    for entry in row.get("moves", []):
        md_dict = pool_by_name.get(entry["name"])
        if md_dict is None:
            continue
        move_slots.append(MoveSlot(move=move_data_from_dict(md_dict), current_pp=entry.get("pp", 0)))

    stages = default_stat_stages()
    stages.update(row.get("stat_stages") or {})
    volatile = default_volatile()
    volatile.update(row.get("volatile") or {})
    types = list(volatile.get("types") or mon.get("types", []))

    return BattlerState(
        dex_id=row["dex_id"], species_name=mon.get("name", f"#{row['dex_id']}"), types=types,
        stats=stats, ivs=dict(ivs), evs=dict(evs), max_hp=row.get("max_hp", stats["hp"]), current_hp=row.get("current_hp", stats["hp"]),
        ability=ability, moves=move_slots, stat_stages=stages,
        status=row.get("status"), status_counter=row.get("status_counter", 0) or 0,
        confusion_counter=row.get("confusion_counter", 0) or 0, volatile=volatile,
        is_fainted=bool(row.get("is_fainted")), item=row.get("item"),
        weight=float(mon.get("weight") or 100.0),
    )


# ---------------------------------------------------------------------------
# Legal actions / NPC AI
# ---------------------------------------------------------------------------

def legal_actions(battle: BattleState, side_id: str) -> dict:
    side = battle.side(side_id)
    active = side.active
    if active.is_fainted:
        switchable = [i for i, b in enumerate(side.roster) if not b.is_fainted]
        return {"usable_move_indices": [], "can_switch": True, "switchable_indices": switchable, "forced": True}
    usable = [i for i, ms in enumerate(active.moves) if ms.current_pp > 0]
    lock = active.volatile.get("choice_lock")
    if lock and active.item in CHOICE_ITEMS:
        # A Choice item locks its holder into the first move it used.
        usable = [i for i in usable if active.moves[i].move.name == lock]
    disabled = (active.volatile.get("disabled") or {}).get("move")
    if disabled:
        usable = [i for i in usable if active.moves[i].move.name != disabled]
    switchable = [i for i, b in enumerate(side.roster) if not b.is_fainted and i != side.active_index]
    if traps(battle.other(side_id).active, active):
        switchable = []  # Shadow Tag / Arena Trap / Magnet Pull
    return {"usable_move_indices": usable, "can_switch": bool(switchable),
            "switchable_indices": switchable, "forced": False}


def pick_npc_action(battle: BattleState, side_id: str) -> Action:
    la = legal_actions(battle, side_id)
    side = battle.side(side_id)
    opp = battle.other(side_id)
    active = side.active
    opp_active = opp.active

    if not la["usable_move_indices"]:
        return Action(kind="move", side=side_id, move_index=None)

    scored = []
    low_hp = active.current_hp <= active.max_hp * 0.35 and la["can_switch"]
    for i in la["usable_move_indices"]:
        move = active.moves[i].move
        if move.name in SELF_KO_MOVES and not low_hp:
            continue  # only as a last-ditch move
        if move.category != "status" and move.power:
            eff = type_effectiveness(move.type, opp_active.types)
            stab = stab_multiplier(active, move)
            score = max(move.power * eff * stab, 1)
        else:
            score = 35  # modest baseline so status/utility moves get picked sometimes
        scored.append((i, score))
    if not scored:
        scored = [(i, 1) for i in la["usable_move_indices"]]

    total = sum(s for _, s in scored)
    roll = battle.rng.uniform(0, total)
    upto = 0.0
    chosen = scored[0][0]
    for i, s in scored:
        upto += s
        if roll <= upto:
            chosen = i
            break
    return Action(kind="move", side=side_id, move_index=chosen)


def pick_npc_forced_switch(battle: BattleState, side_id: str) -> int:
    la = legal_actions(battle, side_id)
    if not la["switchable_indices"]:
        raise ValueError("No switchable Pokémon left")
    return battle.rng.choice(la["switchable_indices"])


# ---------------------------------------------------------------------------
# "Hard" NPC AI — used for Elite Four / Champion battles instead of the
# heavily-randomized pick_npc_action above. Every trainer in this game
# already fields max-IV, level-100 Pokémon (see build_battler_state), so
# there's no stat-growth lever left to make the League tougher than a gym —
# the only honest way to raise the difficulty is to make the opponent
# actually play well: hit the highest-damage move (finishing off a weakened
# target when possible) instead of a coin flip, and proactively switch out
# of a bad type matchup instead of only doing so when forced by a faint.
# ---------------------------------------------------------------------------

def estimate_damage(attacker: BattlerState, defender: BattlerState, move: MoveData,
                    weather: Optional[str] = None) -> float:
    """Deterministic average-case damage estimate (no RNG consumed) — used
    only for NPC decision-making, never for real turn resolution, so it
    never perturbs the battle's own RNG stream."""
    move = ability_adjusted_move(attacker, weather_adjusted_move(move, weather))
    if ability_blocks_move(attacker, defender, move):
        return 0.0
    if move.category != "status" and not move.power:
        immune = move_effectiveness(attacker, move, defender) == 0
        if "fixed_damage" in move.flags:
            return 0.0 if immune else float(move.fixed_damage_amount or 100)
        if move.name == "Super Fang":
            return 0.0 if immune else defender.current_hp / 2
        if move.name == "Endeavor":
            return 0.0 if immune else float(max(0, defender.current_hp - attacker.current_hp))
        if move.name in VARIABLE_POWER_MOVES and move.name != "Present":
            move = variable_power(attacker, defender, move, weather=weather) or move
    if move.category == "status" or not move.power:
        return 0.0
    A, D = _attack_and_defense(attacker, defender, move, False, weather)
    eff = move_effectiveness(attacker, move, defender)
    if eff == 0:
        return 0.0
    base = math.floor(LEVEL_100_STAGE_BASE * move.power * A / D / 50) + 2
    hits = 2 if ability_key(attacker) == "parental-bond" and "multi_hit" not in move.flags else 1
    return base * _damage_multiplier(attacker, defender, move, eff, weather) * 0.925 * (1.25 if hits == 2 else 1)  # 0.925 ~= avg roll


def _best_move_damage(attacker: BattlerState, defender: BattlerState) -> float:
    best = 0.0
    for slot in attacker.moves:
        if slot.current_pp <= 0:
            continue
        best = max(best, estimate_damage(attacker, defender, slot.move))
    return best


def _matchup_score(candidate: BattlerState, opponent: BattlerState) -> float:
    """Higher = better for `candidate` to be on the field against
    `opponent`: the best damage candidate can deal back, minus the best
    damage opponent can deal to candidate, each as a fraction of the
    receiving side's current HP."""
    if candidate.is_fainted:
        return float("-inf")
    my_best = _best_move_damage(candidate, opponent)
    their_best = _best_move_damage(opponent, candidate)
    return my_best / max(opponent.current_hp, 1) - their_best / max(candidate.current_hp, 1)


# The League AI evaluates every option by simulating the current 1-on-1
# "race" a few turns ahead: after this turn's action (an attack, a boost, a
# heal, a status move, or a switch), both Pokémon keep using their best
# attack until one faints. An action's value is how that race ends, from
# +1 (win at full HP) to -1 (lose without denting the foe). It's an
# estimate (no randomness is consumed; the real turn is resolved normally)
# but it lets boosting, healing, crippling status and switching be weighed
# on the same scale as simply attacking.

_RACE_TURNS = 12

# The weather the AI is currently reasoning under. Set by the pick_* entry
# points for the duration of one decision (a ContextVar, so concurrent
# decisions on different server threads can't see each other's value), and
# read by the estimates below.
_AI_WEATHER: contextvars.ContextVar = contextvars.ContextVar("ai_weather", default=None)


def _hit_chance(move: MoveData) -> float:
    return 1.0 if move.accuracy is None or "always_hit" in move.flags else move.accuracy / 100


def _with_changes(mon: BattlerState, stages: dict | None = None, status: str | None = "__keep__"):
    """Context-free snapshot of a battler with hypothetical stat stages /
    status, used only for estimates (the real battler is untouched)."""
    clone = BattlerState.__new__(BattlerState)
    clone.__dict__.update(mon.__dict__)
    clone.stat_stages = dict(mon.stat_stages)
    if stages:
        for stat, change in stages.items():
            if stat in clone.stat_stages:
                clone.stat_stages[stat] = max(-6, min(6, clone.stat_stages[stat] + change))
    if status != "__keep__":
        clone.status = status
    return clone


def _best_attack(attacker: BattlerState, defender: BattlerState) -> float:
    """Expected per-turn damage of attacker's best usable damaging move
    (accuracy-weighted; recharge/charge moves count at their real rate)."""
    best = 0.0
    lock = attacker.volatile.get("choice_lock") if attacker.item in CHOICE_ITEMS else None
    for slot in attacker.moves:
        if slot.current_pp <= 0 or slot.move.name in SELF_KO_MOVES:
            continue
        if lock and slot.move.name != lock:
            continue
        m = weather_adjusted_move(slot.move, _AI_WEATHER.get())
        dmg = estimate_damage(attacker, defender, m, _AI_WEATHER.get())
        if dmg <= 0:
            continue
        if m.min_hits and m.max_hits:
            dmg *= (m.min_hits + m.max_hits) / 2
        dmg *= _hit_chance(m)
        if "recharge" in m.flags or ("charge" in m.flags and "semi_invulnerable" not in m.flags):
            dmg *= 0.5
        best = max(best, dmg)
    return best


def _race(me: BattlerState, foe: BattlerState, my_hp: float, foe_hp: float, *,
          my_first_hit: float | None = None, i_act_first: bool | None = None,
          foe_skips: float = 0.0, foe_chip: float = 0.0, foe_dmg_mult: float = 1.0,
          my_skips_after_first: int = 0) -> float:
    """Plays out the 1v1 from now. my_first_hit: damage of my move this turn
    (None = my action this turn deals no damage). Returns +(my HP left
    fraction) if I win, -(foe HP left fraction) if I lose."""
    w = _AI_WEATHER.get()
    my_dmg = _best_attack(me, foe)
    foe_dmg = _best_attack(foe, me) * foe_dmg_mult
    first = i_act_first if i_act_first is not None else _effective_speed(me, w) > _effective_speed(foe, w)
    speed_first = _effective_speed(me, w) > _effective_speed(foe, w)
    for turn in range(_RACE_TURNS):
        mine = (my_first_hit or 0.0) if turn == 0 else (0.0 if turn <= my_skips_after_first else my_dmg)
        theirs = 0.0 if turn < foe_skips else foe_dmg
        order_first = first if turn == 0 else speed_first
        if order_first:
            foe_hp -= mine
            if foe_hp <= 0:
                return max(my_hp, 0) / max(me.max_hp, 1)
            my_hp -= theirs
            if my_hp <= 0:
                return -max(foe_hp, 0) / max(foe.max_hp, 1)
        else:
            my_hp -= theirs
            if my_hp <= 0:
                return -max(foe_hp, 0) / max(foe.max_hp, 1)
            foe_hp -= mine
            if foe_hp <= 0:
                return max(my_hp, 0) / max(me.max_hp, 1)
        foe_hp -= foe_chip * foe.max_hp
        if foe_hp <= 0:
            return max(my_hp, 0) / max(me.max_hp, 1)
    return (my_hp / max(me.max_hp, 1)) - (foe_hp / max(foe.max_hp, 1))


def _value_move(battle: BattleState, side_id: str, move: MoveData) -> float:
    me = battle.side(side_id).active
    foe = battle.other(side_id).active
    weather = _AI_WEATHER.get()
    move = weather_adjusted_move(move, weather)
    acc = _hit_chance(move)
    if move.priority != 0:
        first = move.priority > 0
    else:
        first = _effective_speed(me, weather) > _effective_speed(foe, weather)
    wasted = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first)

    if move.name in SELF_KO_MOVES:
        return _value_sacrifice(battle, side_id, move)

    # Setting the weather: play the matchup out under the new weather.
    if move.name in WEATHER_MOVES:
        new = WEATHER_MOVES[move.name]
        suppressed = any(ability_key(m) in WEATHER_NEGATING_ABILITIES for m in (me, foe))
        if battle.weather == new or suppressed:
            return wasted - 0.05
        chip = 0.0
        if new == "sand" and not (set(foe.types) & SAND_IMMUNE_TYPES) and ability_key(foe) not in SAND_IMMUNE_ABILITIES:
            chip = 1 / 16
        if new == "hail" and "ice" not in foe.types and ability_key(foe) not in HAIL_IMMUNE_ABILITIES:
            chip = 1 / 16
        token = _AI_WEATHER.set(new)
        try:
            v = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first, foe_chip=chip)
        finally:
            _AI_WEATHER.reset(token)
        return v - 0.02

    if move.category != "status" and move.power:
        dmg = estimate_damage(me, foe, move, weather)
        if dmg <= 0:
            return wasted - 0.01
        if move.min_hits and move.max_hits:
            dmg *= (move.min_hits + move.max_hits) / 2
        if "charge" in move.flags and "semi_invulnerable" not in move.flags:
            # Nothing lands this turn; the hit comes next turn.
            hit = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first)
            return hit - 0.05
        my_hp = me.current_hp
        if move.recoil_percent:
            my_hp -= min(dmg, foe.current_hp) * move.recoil_percent / 100
        if move.drain_percent:
            my_hp = min(me.max_hp, my_hp + min(dmg, foe.current_hp) * move.drain_percent / 100)
        skips = 1 if "recharge" in move.flags and dmg < foe.current_hp else 0
        hit = _race(me, foe, my_hp, foe.current_hp, my_first_hit=dmg, i_act_first=first, my_skips_after_first=skips)
        if move.ailment and move.ailment_chance and foe.status is None:
            hit += 0.03 * move.ailment_chance / 100
        return acc * hit + (1 - acc) * wasted

    # Healing myself.
    if move.healing_percent and move.target == "self":
        heal = me.max_hp * move.healing_percent / 100
        if first:
            healed_hp = min(me.max_hp, me.current_hp + heal)
        else:
            foe_hit = _best_attack(foe, me)
            if foe_hit >= me.current_hp:
                return wasted - 0.01
            healed_hp = min(me.max_hp, me.current_hp - foe_hit + heal) + foe_hit  # race subtracts the hit
        return _race(me, foe, healed_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first) - 0.01

    # Boosting myself: stats change after my move, so the foe's hit this turn
    # lands on the old stats; the race approximates both with the new ones
    # except for this turn's order.
    if move.target == "self" and move.stat_changes:
        changes = {STAT_ALIASES.get(sc.get("stat"), sc.get("stat")): sc.get("change", 0) for sc in move.stat_changes}
        if all(v <= 0 for v in changes.values()):
            return wasted - 0.02
        boosted = _with_changes(me, changes)
        v = _race(boosted, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first)
        # Boosts outlast this foe: a small bonus for the rest of the fight,
        # but only if I'd survive to use it.
        if v > 0:
            v += 0.08 * sum(max(0, c) for c in changes.values())
        return acc * v + (1 - acc) * wasted - 0.02

    # Crippling the foe.
    if move.target != "self" and move.ailment and move.ailment_chance >= 50:
        if move.stat_changes and any(sc.get("change", 0) > 0 for sc in move.stat_changes):
            return wasted - 0.05  # Swagger/Flatter boost the foe
        p = acc * move.ailment_chance / 100
        a = move.ailment
        if a == "confusion":
            if foe.confusion_counter > 0:
                return wasted - 0.01
            v = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first, foe_dmg_mult=0.62)
        else:
            if foe.status is not None:
                return wasted - 0.01
            if a == "sleep":
                v = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first, foe_skips=3 if first else 2)
            elif a == "paralysis":
                crippled = _with_changes(foe, status="paralysis")
                v = _race(me, crippled, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first, foe_dmg_mult=0.75)
            elif a == "burn":
                phys = foe.stats["attack"] >= foe.stats["sp_attack"]
                v = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first,
                          foe_dmg_mult=0.55 if phys else 1.0, foe_chip=1 / 16)
            elif a == "toxic":
                v = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first, foe_chip=3 / 16)
            elif a == "poison":
                v = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first, foe_chip=1 / 8)
            elif a == "freeze":
                v = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first, foe_skips=3)
            else:
                return wasted - 0.01
        return p * v + (1 - p) * wasted - 0.01

    # Lowering the foe's stats.
    if move.target != "self" and move.stat_changes and all(sc.get("change", 0) < 0 for sc in move.stat_changes):
        changes = {STAT_ALIASES.get(sc.get("stat"), sc.get("stat")): sc.get("change", 0) for sc in move.stat_changes}
        weakened = _with_changes(foe, changes)
        p = acc * (move.stat_chance or 100) / 100
        v = _race(me, weakened, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first)
        return p * v + (1 - p) * wasted - 0.02

    return wasted - 0.05  # Protect, Heal Pulse, etc.: no effect here, or helps the foe


def _value_sacrifice(battle: BattleState, side_id: str, move: MoveData) -> float:
    """Explosion & co. cost the user itself: worth it only when it's low
    on HP (or losing anyway) and the hit (or the wish) buys a lot."""
    side = battle.side(side_id)
    me, foe = side.active, battle.other(side_id).active
    teammates = [b for i, b in enumerate(side.roster) if i != side.active_index and not b.is_fainted]
    my_frac = me.current_hp / max(me.max_hp, 1)
    if move.name in ("Healing Wish", "Lunar Dance"):
        if not teammates:
            return -2.0
        hurt = max(1 - b.current_hp / max(b.max_hp, 1) for b in teammates)
        return 0.5 * hurt - 0.9 * my_frac - 0.2
    if not teammates:
        return -1.5  # sacrificing the last Pokémon loses the battle
    if move.name == "Memento":
        return 0.25 - 0.9 * my_frac - 0.1
    if move.name == "Final Gambit":
        dmg = me.current_hp if type_effectiveness(move.type, foe.types) else 0.0
    else:
        dmg = estimate_damage(me, foe, move, _AI_WEATHER.get()) * _hit_chance(move)
    if dmg <= 0:
        return -1.0
    ko = dmg >= foe.current_hp
    return 0.9 * min(dmg, foe.current_hp) / max(foe.max_hp, 1) + (0.3 if ko else 0.0) - 0.9 * my_frac - 0.15


def _value_switch(battle: BattleState, side_id: str, index: int) -> float:
    """Switching in costs the new Pokémon a free hit from the foe."""
    cand = battle.side(side_id).roster[index]
    foe = battle.other(side_id).active
    hit = _best_attack(foe, cand)
    if hit >= cand.current_hp:
        return -1.0
    return _race(cand, foe, cand.current_hp - hit, foe.current_hp)


def pick_npc_action_hard(battle: BattleState, side_id: str) -> Action:
    token = _AI_WEATHER.set(current_weather(battle))
    try:
        return _pick_npc_action_hard(battle, side_id)
    finally:
        _AI_WEATHER.reset(token)


def _pick_npc_action_hard(battle: BattleState, side_id: str) -> Action:
    la = legal_actions(battle, side_id)
    side = battle.side(side_id)

    if not la["usable_move_indices"]:
        if la["can_switch"]:
            best_idx = max(la["switchable_indices"], key=lambda i: _value_switch(battle, side_id, i))
            return Action(kind="switch", side=side_id, switch_to_index=best_idx)
        return Action(kind="move", side=side_id, move_index=None)

    scored = [(i, _value_move(battle, side_id, side.active.moves[i].move)) for i in la["usable_move_indices"]]
    best_i, best_v = max(scored, key=lambda s: s[1])

    # Switch only when staying in loses and a teammate clearly does better,
    # even after taking a hit on the way in.
    if la["can_switch"] and best_v < 0:
        sw_i, sw_v = max(((i, _value_switch(battle, side_id, i)) for i in la["switchable_indices"]), key=lambda s: s[1])
        if sw_v > best_v + 0.25:
            return Action(kind="switch", side=side_id, switch_to_index=sw_i)

    # Pick among the near-best options so the League isn't fully predictable.
    top = [i for i, v in scored if v >= best_v - 0.03]
    return Action(kind="move", side=side_id, move_index=battle.rng.choice(top))


# ---------------------------------------------------------------------------
# Boss AI: Cynthia and the Legends. The League AI plus three habits of a
# strong human player:
#  - it reads switches: when the player's Pokémon is losing its matchup and
#    has a clearly better teammate to bring in, the boss expects the switch
#    and weighs each move against that teammate too (so it doesn't waste a
#    super-effective hit on a resist the player was always going to bring);
#  - it leaves a losing matchup sooner than the League AI does;
#  - it saves its ace (the last Pokémon) until its other Pokémon are down.
# ---------------------------------------------------------------------------

BOSS_SWITCH_MARGIN = 0.15
BOSS_ACE_PENALTY = 0.35
BOSS_MAX_READ = 0.55  # never bets more than this on a predicted switch


def _ace_penalty(side: BattleSide, index: int) -> float:
    if side.ace_index is None or index != side.ace_index:
        return 0.0
    others = [b for i, b in enumerate(side.roster)
              if i not in (index, side.active_index) and not b.is_fainted]
    return BOSS_ACE_PENALTY if others else 0.0


def _value_switch_in(battle: BattleState, side_id: str, index: int) -> float:
    """A switch, valued properly: the newcomer takes one free hit on the
    way in, then the two trade blows as usual. (_value_switch, the League
    AI's version, also skips the newcomer's first attack, which makes
    every switch look worse than it is.)"""
    cand = battle.side(side_id).roster[index]
    foe = battle.other(side_id).active
    hit = _best_attack(foe, cand)
    if hit >= cand.current_hp:
        return -1.0
    return _race(cand, foe, cand.current_hp - hit, foe.current_hp, my_first_hit=_best_attack(cand, foe))


def _with_foe_active(battle: BattleState, side_id: str, foe_index: int, fn):
    """Evaluates fn() as if the foe had `foe_index` on the field."""
    foe_side = battle.other(side_id)
    saved = foe_side.active_index
    foe_side.active_index = foe_index
    try:
        return fn()
    finally:
        foe_side.active_index = saved


def predict_switch(battle: BattleState, side_id: str) -> tuple[float, Optional[int]]:
    """(chance, team index): how likely `side_id`'s opponent is to switch
    out this turn, and to whom. Judged from the opponent's point of view:
    how badly their current Pokémon is losing, and how much better their
    best switch-in would do."""
    foe_id = battle.other(side_id).side_id
    la = legal_actions(battle, foe_id)
    if not la["can_switch"] or not la["usable_move_indices"]:
        return 0.0, None
    foe = battle.side(foe_id)
    stay = max(_value_move(battle, foe_id, foe.active.moves[i].move) for i in la["usable_move_indices"])
    if stay >= -0.1:
        return 0.0, None
    idx, val = max(((i, _value_switch_in(battle, foe_id, i)) for i in la["switchable_indices"]), key=lambda s: s[1])
    gain = val - stay
    if gain < 0.3:
        return 0.0, None
    return min(BOSS_MAX_READ, 0.2 + 0.35 * gain), idx


def pick_npc_action_boss(battle: BattleState, side_id: str) -> Action:
    token = _AI_WEATHER.set(current_weather(battle))
    try:
        return _pick_npc_action_boss(battle, side_id)
    finally:
        _AI_WEATHER.reset(token)


def _pick_npc_action_boss(battle: BattleState, side_id: str) -> Action:
    la = legal_actions(battle, side_id)
    side = battle.side(side_id)

    def switch_value(i):
        return _value_switch_in(battle, side_id, i) - _ace_penalty(side, i)

    if not la["usable_move_indices"]:
        if la["can_switch"]:
            return Action(kind="switch", side=side_id, switch_to_index=max(la["switchable_indices"], key=switch_value))
        return Action(kind="move", side=side_id, move_index=None)

    read, switch_in = predict_switch(battle, side_id)
    scored = []
    for i in la["usable_move_indices"]:
        move = side.active.moves[i].move
        v = _value_move(battle, side_id, move)
        if read:
            v_switch = _with_foe_active(battle, side_id, switch_in, lambda m=move: _value_move(battle, side_id, m))
            v = (1 - read) * v + read * v_switch
        scored.append((i, v))
    best_i, best_v = max(scored, key=lambda s: s[1])

    if la["can_switch"] and best_v < 0:
        sw_i, sw_v = max(((i, switch_value(i)) for i in la["switchable_indices"]), key=lambda s: s[1])
        if sw_v > best_v + BOSS_SWITCH_MARGIN:
            return Action(kind="switch", side=side_id, switch_to_index=sw_i)

    top = [i for i, v in scored if v >= best_v - 0.02]
    return Action(kind="move", side=side_id, move_index=battle.rng.choice(top))


def pick_npc_forced_switch_boss(battle: BattleState, side_id: str) -> int:
    la = legal_actions(battle, side_id)
    if not la["switchable_indices"]:
        raise ValueError("No switchable Pokémon left")
    side = battle.side(side_id)
    foe = battle.other(side_id).active
    token = _AI_WEATHER.set(current_weather(battle))
    try:
        return max(la["switchable_indices"],
                   key=lambda i: _race(side.roster[i], foe, side.roster[i].current_hp, foe.current_hp) - _ace_penalty(side, i))
    finally:
        _AI_WEATHER.reset(token)


MEDIUM_AI_SMART_SHARE = 0.65


def pick_npc_action_medium(battle: BattleState, side_id: str) -> Action:
    """Gym leaders: plays the League AI's move most turns and the loose,
    weighted-random pick the rest, so they're dangerous but beatable."""
    if battle.rng.random() < MEDIUM_AI_SMART_SHARE:
        return pick_npc_action_hard(battle, side_id)
    return pick_npc_action(battle, side_id)


def pick_npc_forced_switch_hard(battle: BattleState, side_id: str) -> int:
    la = legal_actions(battle, side_id)
    if not la["switchable_indices"]:
        raise ValueError("No switchable Pokémon left")
    side = battle.side(side_id)
    foe = battle.other(side_id).active
    token = _AI_WEATHER.set(current_weather(battle))
    try:
        # A replacement after a faint comes in without taking a hit.
        return max(la["switchable_indices"], key=lambda i: _race(side.roster[i], foe, side.roster[i].current_hp, foe.current_hp))
    finally:
        _AI_WEATHER.reset(token)


# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
# Sun, rain, sandstorm and hail, set by Sunny Day / Rain Dance / Sandstorm /
# Hail or by an ability when its Pokémon enters battle (Drought, Drizzle,
# Sand Stream, Snow Warning), lasting 5 turns either way (modern rules).
# Cloud Nine / Air Lock on the field suppress every weather effect.

WEATHER_TURNS = 5
WEATHER_MOVES = {"Sunny Day": "sun", "Rain Dance": "rain", "Sandstorm": "sand", "Hail": "hail"}
WEATHER_ABILITIES = {"drought": "sun", "drizzle": "rain", "sand-stream": "sand", "snow-warning": "hail"}
WEATHER_NEGATING_ABILITIES = {"cloud-nine", "air-lock"}
SPEED_DOUBLERS = {"swift-swim": "rain", "chlorophyll": "sun", "sand-rush": "sand"}
SAND_IMMUNE_TYPES = {"rock", "ground", "steel"}
SAND_IMMUNE_ABILITIES = {"sand-veil", "sand-rush", "sand-force", "overcoat", "magic-guard"}
HAIL_IMMUNE_ABILITIES = {"ice-body", "snow-cloak", "overcoat", "magic-guard"}
WEATHER_BALL_TYPES = {"sun": "fire", "rain": "water", "sand": "rock", "hail": "ice"}
SUN_HEAL_MOVES = {"Synthesis", "Morning Sun", "Moonlight"}

def ability_key(mon: BattlerState) -> str:
    """The Pokémon's ability right now (a traced one replaces its own until it
    switches out)."""
    override = mon.volatile.get("ability_override") if isinstance(mon.volatile, dict) else None
    return (override or mon.ability or "").strip().lower().replace(" ", "-").replace("_", "-")


def ability_label(mon: BattlerState) -> str:
    return ability_key(mon).replace("-", " ").title()


def current_weather(battle: BattleState) -> Optional[str]:
    """The weather in effect right now (None if clear or suppressed)."""
    if not battle.weather:
        return None
    for side in (battle.side_a, battle.side_b):
        mon = side.active
        if not mon.is_fainted and ability_key(mon) in WEATHER_NEGATING_ABILITIES:
            return None
    return battle.weather


def set_weather(battle: BattleState, weather: str, events: list, side_id: str, source: str,
                mon: BattlerState) -> bool:
    """Starts `weather` for WEATHER_TURNS turns. Returns False (no change) if
    that weather is already active."""
    if battle.weather == weather:
        return False
    turns = WEATHER_ROCK_TURNS if WEATHER_ROCKS.get(mon.item or "") == weather else WEATHER_TURNS
    battle.weather = weather
    battle.weather_turns = turns
    event = {"type": "weather_start", "weather": weather, "side": side_id, "source": source,
             "name": mon.species_name, "turns": turns}
    if turns != WEATHER_TURNS:
        event["item"] = HELD_ITEMS[mon.item]["label"]
    if source == "ability":
        event["ability"] = ability_label(mon)
    events.append(event)
    return True


def trigger_entry_ability(battle: BattleState, side_id: str, events: list, weather_only: bool = False) -> None:
    """Abilities that act as a Pokémon enters battle: weather, Intimidate,
    Trace, Download, the announcements (Pressure, Frisk...)."""
    mon = battle.side(side_id).active
    weather = WEATHER_ABILITIES.get(ability_key(mon))
    if weather and not mon.is_fainted:
        set_weather(battle, weather, events, side_id, "ability", mon)
    if not weather_only:
        trigger_ability_on_entry(battle, side_id, events)


def apply_opening_abilities(battle: BattleState) -> list:
    """Entry abilities of both leads at the start of a battle. The slower
    lead's activates first, so the faster lead's weather is the one that
    sticks (as in the games)."""
    events: list = []
    a, b = battle.side_a.active, battle.side_b.active
    order = ["A", "B"] if _effective_speed(a) <= _effective_speed(b) else ["B", "A"]
    for side_id in order:
        trigger_entry_ability(battle, side_id, events)
    return events


def weather_adjusted_move(move: MoveData, weather: Optional[str]) -> MoveData:
    """The move as it behaves in the current weather: Weather Ball's type and
    power, Solar Beam skipping its charge in sun (and halved in other
    weather), Thunder/Hurricane/Blizzard accuracy, sun-boosted healing moves,
    and Growth's doubled boost in sun."""
    if not weather:
        return move
    changes: dict = {}
    name = move.name
    if name == "Weather Ball":
        changes.update(type=WEATHER_BALL_TYPES[weather], power=(move.power or 50) * 2)
    elif name == "Solar Beam":
        if weather == "sun":
            changes["flags"] = frozenset(f for f in move.flags if f != "charge")
        else:
            changes["power"] = (move.power or 120) // 2
    elif name in ("Thunder", "Hurricane"):
        if weather == "rain":
            changes["accuracy"] = None
        elif weather == "sun":
            changes["accuracy"] = 50
    elif name == "Blizzard" and weather == "hail":
        changes["accuracy"] = None
    elif name in SUN_HEAL_MOVES:
        changes["healing_percent"] = 66 if weather == "sun" else 25
    elif name == "Growth" and weather == "sun":
        changes["stat_changes"] = tuple({**sc, "change": sc.get("change", 0) * 2} for sc in move.stat_changes)
    return dataclasses.replace(move, **changes) if changes else move


def weather_damage_multiplier(weather: Optional[str], move: MoveData, attacker: BattlerState) -> float:
    mult = 1.0
    if weather == "sun":
        mult *= {"fire": 1.5, "water": 0.5}.get(move.type, 1.0)
    elif weather == "rain":
        mult *= {"water": 1.5, "fire": 0.5}.get(move.type, 1.0)
    elif weather == "sand" and ability_key(attacker) == "sand-force" and move.type in SAND_IMMUNE_TYPES:
        mult *= 1.3
    return mult


def weather_stat_multiplier(weather: Optional[str], mon: BattlerState, stat: str) -> float:
    if weather == "sand" and stat == "sp_defense" and "rock" in mon.types:
        return 1.5
    if weather == "sun" and stat == "sp_attack" and ability_key(mon) == "solar-power":
        return 1.5
    return 1.0


def weather_evasion_multiplier(weather: Optional[str], defender: BattlerState) -> float:
    """Sand Veil / Snow Cloak: moves aimed at this Pokémon hit 20% less often."""
    ability = ability_key(defender)
    if (weather == "sand" and ability == "sand-veil") or (weather == "hail" and ability == "snow-cloak"):
        return 0.8
    return 1.0


def _apply_end_of_turn_weather(battle: BattleState, mon: BattlerState, side_id: str, weather: str) -> list:
    """Sandstorm/hail chip damage and weather-dependent abilities."""
    events: list = []
    ability = ability_key(mon)

    def hurt(amount: int, reason: str):
        mon.current_hp = max(0, mon.current_hp - amount)
        events.append({"type": "weather_damage", "side": side_id, "weather": weather, "reason": reason,
                       "amount": amount, "new_hp": mon.current_hp, "max_hp": mon.max_hp})
        _check_and_emit_faint(mon, side_id, events)
        _after_hp_loss(mon, side_id, events)

    def heal(amount: int, reason: str):
        if mon.current_hp >= mon.max_hp:
            return
        amount = min(amount, mon.max_hp - mon.current_hp)
        mon.current_hp += amount
        events.append({"type": "heal", "side": side_id, "amount": amount, "reason": reason,
                       "new_hp": mon.current_hp, "max_hp": mon.max_hp})

    if weather == "sand":
        if not (set(mon.types) & SAND_IMMUNE_TYPES) and ability not in SAND_IMMUNE_ABILITIES:
            hurt(max(1, mon.max_hp // 16), "sand")
    elif weather == "hail":
        if "ice" not in mon.types and ability not in HAIL_IMMUNE_ABILITIES:
            hurt(max(1, mon.max_hp // 16), "hail")
        elif ability == "ice-body":
            heal(max(1, mon.max_hp // 16), "Ice Body")
    elif weather == "rain":
        if ability == "rain-dish":
            heal(max(1, mon.max_hp // 16), "Rain Dish")
        elif ability == "dry-skin":
            heal(max(1, mon.max_hp // 8), "Dry Skin")
        elif ability == "hydration" and mon.status is not None:
            mon.status = None
            mon.status_counter = 0
            events.append({"type": "status_applied", "side": side_id, "status": "none", "reason": "hydration"})
    elif weather == "sun":
        if ability in ("dry-skin", "solar-power"):
            hurt(max(1, mon.max_hp // 8), ability_label(mon))
    return events


# ---------------------------------------------------------------------------
# Held items
# ---------------------------------------------------------------------------
# Competitive battle items (no healing items like Potions or Revives). Each
# Pokémon holds at most one; a consumed item (Focus Sash, berries) is gone
# for the rest of that battle only — the trainer's own copy isn't used up.

HELD_ITEMS = {
    "leftovers": {"label": "Leftovers", "description": "Restores 1/16 of the holder's max HP at the end of every turn."},
    "life-orb": {"label": "Life Orb", "description": "Boosts the power of the holder's attacks by 30%, but it loses 1/10 of its max HP each time it attacks."},
    "choice-band": {"label": "Choice Band", "description": "Boosts Attack by 50%, but the holder can only use the first move it picks until it switches out."},
    "choice-specs": {"label": "Choice Specs", "description": "Boosts Sp. Atk by 50%, but the holder can only use the first move it picks until it switches out."},
    "choice-scarf": {"label": "Choice Scarf", "description": "Boosts Speed by 50%, but the holder can only use the first move it picks until it switches out."},
    "focus-sash": {"label": "Focus Sash", "description": "If the holder is at full HP, it survives a hit that would knock it out with 1 HP. Single use per battle."},
    "expert-belt": {"label": "Expert Belt", "description": "Boosts the power of super-effective moves by 20%."},
    "muscle-band": {"label": "Muscle Band", "description": "Boosts the power of physical moves by 10%."},
    "wise-glasses": {"label": "Wise Glasses", "description": "Boosts the power of special moves by 10%."},
    "sitrus-berry": {"label": "Sitrus Berry", "description": "Restores 1/4 of max HP when the holder drops to half HP or less. Single use per battle."},
    "lum-berry": {"label": "Lum Berry", "description": "Cures any status condition or confusion the moment the holder gets it. Single use per battle."},
    "quick-claw": {"label": "Quick Claw", "description": "Gives the holder a 20% chance to move first among moves of the same priority."},
    "scope-lens": {"label": "Scope Lens", "description": "Raises the holder's critical-hit ratio by one stage."},
    "bright-powder": {"label": "Bright Powder", "description": "Makes moves aimed at the holder 10% less accurate."},
    "heat-rock": {"label": "Heat Rock", "description": "Harsh sunlight the holder starts lasts 8 turns instead of 5."},
    "damp-rock": {"label": "Damp Rock", "description": "Rain the holder starts lasts 8 turns instead of 5."},
    "smooth-rock": {"label": "Smooth Rock", "description": "A sandstorm the holder starts lasts 8 turns instead of 5."},
    "icy-rock": {"label": "Icy Rock", "description": "Hail the holder starts lasts 8 turns instead of 5."},
}
CHOICE_ITEMS = {"choice-band": "attack", "choice-specs": "sp_attack", "choice-scarf": "speed"}
WEATHER_ROCKS = {"heat-rock": "sun", "damp-rock": "rain", "smooth-rock": "sand", "icy-rock": "hail"}
WEATHER_ROCK_TURNS = 8
QUICK_CLAW_CHANCE = 0.2

# Moves that make their user faint (Explosion etc.). Healing Wish / Lunar
# Dance fully heal whichever Pokémon replaces the user.
SELF_KO_MOVES = {"Explosion", "Self Destruct", "Memento", "Healing Wish", "Lunar Dance", "Final Gambit"}


def item_label(mon: BattlerState) -> str:
    return HELD_ITEMS.get(mon.item or "", {}).get("label", "")


def item_stat_multiplier(mon: BattlerState, stat: str) -> float:
    return 1.5 if CHOICE_ITEMS.get(mon.item or "") == stat else 1.0


def item_damage_multiplier(attacker: BattlerState, move: MoveData, eff: float) -> float:
    item = attacker.item or ""
    if item == "life-orb":
        return 1.3
    if item == "expert-belt" and eff > 1:
        return 1.2
    if (item == "muscle-band" and move.category == "physical") or (item == "wise-glasses" and move.category == "special"):
        return 1.1
    return 1.0


def _consume_item(mon: BattlerState, side_id: str, events: list, **extra) -> None:
    events.append({"type": "item_used", "side": side_id, "name": mon.species_name,
                   "item": mon.item, "label": item_label(mon), **extra})
    if (mon.item or "").endswith("-berry"):
        mon.volatile["last_berry"] = mon.item  # Harvest can grow it back
    mon.item = None
    if ability_key(mon) == "unburden":
        mon.volatile["unburdened"] = True


def _after_hp_loss(mon: BattlerState, side_id: str, events: list) -> None:
    """Sitrus Berry: once, when the holder falls to half HP or below."""
    if (mon.item == "sitrus-berry" and not mon.is_fainted and 0 < mon.current_hp <= mon.max_hp // 2
            and not mon.volatile.get("unnerved")):
        heal = min(mon.max_hp // 4, mon.max_hp - mon.current_hp)
        mon.current_hp += heal
        _consume_item(mon, side_id, events, amount=heal, new_hp=mon.current_hp, max_hp=mon.max_hp)


def _after_status(mon: BattlerState, side_id: str, events: list) -> None:
    """Lum Berry: cures a major status or confusion as soon as it lands."""
    if mon.item != "lum-berry" or mon.is_fainted or mon.volatile.get("unnerved"):
        return
    cured = mon.status or ("confusion" if mon.confusion_counter else None)
    if not cured:
        return
    mon.status = None
    mon.status_counter = 0
    mon.confusion_counter = 0
    _consume_item(mon, side_id, events, cured=cured)


def _apply_end_of_turn_item(mon: BattlerState, side_id: str) -> list:
    events: list = []
    if mon.item == "leftovers" and not mon.is_fainted and mon.current_hp < mon.max_hp:
        heal = min(max(1, mon.max_hp // 16), mon.max_hp - mon.current_hp)
        mon.current_hp += heal
        events.append({"type": "heal", "side": side_id, "amount": heal, "reason": "Leftovers",
                       "new_hp": mon.current_hp, "max_hp": mon.max_hp})
    return events


# ---------------------------------------------------------------------------
# Moves with no fixed power
# ---------------------------------------------------------------------------
# Their damage depends on the situation: HP (Flail, Eruption, Wring Out),
# weight (Low Kick, Heavy Slam), speed (Gyro Ball, Electro Ball), the held
# item (Fling, Natural Gift), Stockpile (Spit Up), the damage just taken
# (Counter, Mirror Coat, Metal Burst, Bide) or a fraction of HP (Super Fang,
# Endeavor). Without this they all dealt 0 damage.

VARIABLE_POWER_MOVES = {
    "Flail", "Reversal", "Wring Out", "Crush Grip", "Eruption", "Water Spout", "Low Kick", "Grass Knot",
    "Heavy Slam", "Heat Crash", "Gyro Ball", "Electro Ball", "Punishment", "Trump Card", "Spit Up",
    "Natural Gift", "Fling", "Magnitude", "Present",
}
# name -> (category it answers, or None for either; damage multiplier)
COUNTER_MOVES = {"Counter": ("physical", 2.0), "Mirror Coat": ("special", 2.0), "Metal Burst": (None, 1.5)}
NATURAL_GIFT = {"sitrus-berry": ("psychic", 80), "lum-berry": ("flying", 80)}
FLING_POWER = {
    "quick-claw": 80, "heat-rock": 60, "damp-rock": 60, "icy-rock": 40, "life-orb": 30, "scope-lens": 30,
}  # every other held item flings for 10
MAGNITUDES = [(4, 10, 5), (5, 30, 10), (6, 50, 20), (7, 70, 30), (8, 90, 20), (9, 110, 10), (10, 150, 5)]
TRUMP_CARD_POWER = {0: 200, 1: 80, 2: 60, 3: 50}  # by PP left after use; 4+ -> 40
STOCKPILE_MAX = 3


def _hp_ratio(mon: BattlerState) -> float:
    return mon.current_hp / max(mon.max_hp, 1)


def variable_power(attacker: BattlerState, defender: BattlerState, move: MoveData, *,
                   rng: Optional[random.Random] = None, pp_left: Optional[int] = None,
                   weather: Optional[str] = None) -> Optional[MoveData]:
    """The move with its power (and, for Natural Gift, type) worked out for
    this use; None if it can't be used (no item to fling, nothing
    stockpiled). rng=None gives the average, for AI estimates."""
    name = move.name
    changes: dict = {}
    if name in ("Flail", "Reversal"):
        r = 48 * attacker.current_hp // max(attacker.max_hp, 1)
        power = 200 if r < 2 else 150 if r < 5 else 100 if r < 10 else 80 if r < 17 else 40 if r < 33 else 20
    elif name in ("Wring Out", "Crush Grip"):
        power = max(1, int(120 * _hp_ratio(defender)))
    elif name in ("Eruption", "Water Spout"):
        power = max(1, int(150 * _hp_ratio(attacker)))
    elif name in ("Low Kick", "Grass Knot"):
        w = _weight(defender)
        power = 20 if w < 10 else 40 if w < 25 else 60 if w < 50 else 80 if w < 100 else 100 if w < 200 else 120
    elif name in ("Heavy Slam", "Heat Crash"):
        ratio = _weight(attacker) / max(_weight(defender), 0.1)
        power = 120 if ratio >= 5 else 100 if ratio >= 4 else 80 if ratio >= 3 else 60 if ratio >= 2 else 40
    elif name == "Gyro Ball":
        power = min(150, int(25 * _effective_speed(defender, weather) / max(_effective_speed(attacker, weather), 1)) + 1)
    elif name == "Electro Ball":
        ratio = _effective_speed(attacker, weather) / max(_effective_speed(defender, weather), 1)
        power = 150 if ratio >= 4 else 120 if ratio >= 3 else 80 if ratio >= 2 else 60 if ratio >= 1 else 40
    elif name == "Punishment":
        power = min(200, 60 + 20 * sum(v for v in defender.stat_stages.values() if v > 0))
    elif name == "Trump Card":
        power = TRUMP_CARD_POWER.get(pp_left, 40) if pp_left is not None else 50
    elif name == "Spit Up":
        stock = attacker.volatile.get("stockpile", 0)
        if not stock:
            return None
        power = 100 * stock
    elif name == "Natural Gift":
        if attacker.item not in NATURAL_GIFT:
            return None
        changes["type"], power = NATURAL_GIFT[attacker.item]
    elif name == "Fling":
        if not attacker.item:
            return None
        power = FLING_POWER.get(attacker.item, 10)
    elif name == "Magnitude":
        if rng is None:
            power = 71
        else:
            roll, upto, power = rng.uniform(0, 100), 0, 70
            for _, p, chance in MAGNITUDES:
                upto += chance
                if roll <= upto:
                    power = p
                    break
    elif name == "Present":
        power = 52 if rng is None else rng.choice([40, 40, 40, 40, 80, 80, 80, 120])
    else:
        return move
    return dataclasses.replace(move, power=power, **changes)


def _stockpile_stat_changes(count: int) -> list:
    return [{"stat": "defense", "change": -count}, {"stat": "special_defense", "change": -count}]


# ---------------------------------------------------------------------------
# Damage
# ---------------------------------------------------------------------------

LEVEL_100_STAGE_BASE = math.floor(2 * 100 / 5 + 2)  # 42, constant since every mon is level 100


def compute_damage(attacker: BattlerState, defender: BattlerState, move: MoveData,
                    is_crit: bool, rng: random.Random, weather: Optional[str] = None) -> int:
    if move.category == "status" or not move.power:
        return 0

    A, D = _attack_and_defense(attacker, defender, move, is_crit, weather)
    eff = move_effectiveness(attacker, move, defender)
    if eff == 0:
        return 0

    base = math.floor(LEVEL_100_STAGE_BASE * move.power * A / D / 50) + 2
    crit_mult = (3.0 if ability_key(attacker) == "sniper" else 2.0) if is_crit else 1.0
    random_roll = rng.uniform(0.85, 1.00)
    damage = math.floor(base * _damage_multiplier(attacker, defender, move, eff, weather) * crit_mult * random_roll)
    return max(1, damage)


def _attack_and_defense(attacker: BattlerState, defender: BattlerState, move: MoveData, is_crit: bool,
                        weather: Optional[str]) -> tuple:
    """The attacking and defending stat for a hit, with stages (a crit ignores
    the bad ones; Unaware ignores the foe's), weather, items and abilities."""
    a_key, d_key = ("attack", "defense") if move.category == "physical" else ("sp_attack", "sp_defense")
    a_stage, d_stage = attacker.stat_stages[a_key], defender.stat_stages[d_key]
    if _foe_ability(defender, attacker) == "unaware":
        a_stage = 0
    if ability_key(attacker) == "unaware":
        d_stage = 0
    if is_crit:
        a_stage, d_stage = max(a_stage, 0), min(d_stage, 0)
    A = (attacker.stats[a_key] * stat_stage_multiplier(a_stage) * weather_stat_multiplier(weather, attacker, a_key)
         * item_stat_multiplier(attacker, a_key) * ability_stat_multiplier(attacker, a_key, weather))
    D = defender.stats[d_key] * stat_stage_multiplier(d_stage) * weather_stat_multiplier(weather, defender, d_key)
    if _foe_ability(defender, attacker):
        D *= ability_stat_multiplier(defender, d_key, weather)
    return A, D


def _damage_multiplier(attacker: BattlerState, defender: BattlerState, move: MoveData, eff: float,
                       weather: Optional[str]) -> float:
    """Everything that scales damage besides the stats and the random roll."""
    burn = 0.5 if (attacker.status == "burn" and move.category == "physical" and ability_key(attacker) != "guts") else 1.0
    return (stab_multiplier(attacker, move) * eff * burn * weather_damage_multiplier(weather, move, attacker)
            * item_damage_multiplier(attacker, move, eff) * ability_damage_multiplier(attacker, defender, move, eff))


def compute_confusion_damage(mon: BattlerState, rng: random.Random) -> int:
    A = mon.stats["attack"] * stat_stage_multiplier(mon.stat_stages["attack"])
    D = mon.stats["defense"] * stat_stage_multiplier(mon.stat_stages["defense"])
    base = math.floor(LEVEL_100_STAGE_BASE * 40 * A / D / 50) + 2
    return max(1, math.floor(base * rng.uniform(0.85, 1.00)))


def sample_multi_hit_count(move: MoveData, rng: random.Random) -> int:
    lo, hi = move.min_hits or 2, move.max_hits or 2
    if lo >= hi:
        return hi
    weights = {2: 0.375, 3: 0.375, 4: 0.125, 5: 0.125}
    candidates = [n for n in range(lo, hi + 1) if n in weights] or list(range(lo, hi + 1))
    total = sum(weights.get(n, 1.0) for n in candidates)
    roll = rng.uniform(0, total)
    upto = 0.0
    for n in candidates:
        upto += weights.get(n, 1.0)
        if roll <= upto:
            return n
    return candidates[-1]


def crit_chance(move: MoveData, attacker: Optional[BattlerState] = None,
                defender: Optional[BattlerState] = None) -> float:
    if defender is not None and _foe_ability(defender, attacker) in ("battle-armor", "shell-armor"):
        return 0.0
    stage = move.crit_rate
    if attacker is not None:
        stage += (1 if attacker.item == "scope-lens" else 0) + (1 if ability_key(attacker) == "super-luck" else 0)
    return CRIT_TABLE.get(min(3, max(0, stage)), CRIT_TABLE[0])


# ---------------------------------------------------------------------------
# Abilities
# ---------------------------------------------------------------------------
# What each ability does in a 1-on-1 battle. The weather ones live with the
# weather code above; these are everything else. Each hook below is called
# from the matching point of a turn (damage, accuracy, status, stat changes,
# switching, end of turn). An ability that changes something emits an
# "ability_activated" event (with a ready-to-show `message`) so the battle
# log and the animations can show it.

STATUS_IMMUNITIES = {
    "immunity": {"poison", "toxic"}, "limber": {"paralysis"}, "insomnia": {"sleep"},
    "vital-spirit": {"sleep"}, "water-veil": {"burn"}, "magma-armor": {"freeze"}, "own-tempo": {"confusion"},
}
ABSORB_HEAL = {"volt-absorb": "electric", "water-absorb": "water", "dry-skin": "water"}
ABSORB_BOOST = {"motor-drive": ("electric", "speed"), "lightning-rod": ("electric", "sp_attack"),
                "storm-drain": ("water", "sp_attack"), "sap-sipper": ("grass", "attack")}
PINCH_TYPES = {"blaze": "fire", "torrent": "water", "overgrow": "grass", "swarm": "bug"}
ATE_ABILITIES = {"aerilate": "flying", "pixilate": "fairy", "refrigerate": "ice"}
FLAG_BOOSTS = {"iron-fist": ("punch", 1.2), "strong-jaw": ("bite", 1.5), "mega-launcher": ("pulse", 1.5),
               "tough-claws": ("contact", 1.3), "sharpness": ("slicing", 1.5)}
# Blocks stat drops caused by the foe: None = every stat, else just that one.
STAT_DROP_BLOCKERS = {"clear-body": None, "white-smoke": None, "hyper-cutter": "attack",
                      "big-pecks": "defense", "keen-eye": "accuracy"}
INTIMIDATE_BLOCKERS = {"inner-focus", "oblivious", "own-tempo", "scrappy"}
CONTACT_STATUS = {"static": "paralysis", "flame-body": "burn", "poison-point": "poison"}
MOLD_BREAKERS = {"mold-breaker"}
UNTRACEABLE = {"trace", "multitype", "imposter", "forecast", "flower-gift", "wonder-guard"}
DAMP_BLOCKED = {"Explosion", "Self Destruct"}
MOODY_STATS = ("attack", "defense", "sp_attack", "sp_defense", "speed")
STAT_NAMES = {"attack": "Attack", "defense": "Defense", "sp_attack": "Sp. Atk", "sp_defense": "Sp. Def",
              "speed": "Speed", "accuracy": "accuracy", "evasion": "evasiveness"}

# Abilities with nothing to do in these battles: they only matter outside
# battle (Pickup, Run Away...) or in double battles (Plus, Healer...), or rely
# on mechanics this game doesn't have (genders, infatuation).
NO_BATTLE_EFFECT_ABILITIES = {
    "illuminate", "honey-gather", "pickup", "run-away", "plus", "minus", "friend-guard", "healer",
    "telepathy", "cute-charm", "rivalry", "gluttony", "infiltrator", "unknown", "suction-cups",
}
# Have a battle effect that isn't simulated yet.
UNSIMULATED_ABILITIES = {"klutz", "imposter", "multitype", "forecast", "neutralizing-gas", "wind-rider"}


def _foe_ability(defender: BattlerState, attacker: Optional[BattlerState]) -> str:
    """The defender's ability as it affects an attack: Mold Breaker ignores it."""
    if attacker is not None and ability_key(attacker) in MOLD_BREAKERS:
        return ""
    return ability_key(defender)


def _ability_event(events: list, mon: BattlerState, side_id: str, message: str, **extra) -> None:
    events.append({"type": "ability_activated", "side": side_id, "name": mon.species_name,
                   "ability": ability_label(mon), "message": message, **extra})


def _set_types(mon: BattlerState, types: list) -> None:
    """A type change (Protean, Color Change); kept in volatile so it lasts
    until the Pokémon switches out, and survives a save/reload."""
    mon.volatile.setdefault("base_types", list(mon.types))
    mon.types = list(types)
    mon.volatile["types"] = list(types)


def _weight(mon: BattlerState) -> float:
    abil = ability_key(mon)
    return mon.weight * (2 if abil == "heavy-metal" else 0.5 if abil == "light-metal" else 1)


def is_grounded(mon: BattlerState) -> bool:
    return "flying" not in mon.types and ability_key(mon) != "levitate"


def move_effectiveness(attacker: Optional[BattlerState], move: MoveData, defender: BattlerState) -> float:
    """Type effectiveness, with Scrappy letting Normal/Fighting moves hit Ghosts."""
    types = defender.types
    if attacker is not None and ability_key(attacker) == "scrappy" and move.type in ("normal", "fighting"):
        types = [t for t in types if t != "ghost"] or ["normal"]
    return type_effectiveness(move.type, types)


def ability_adjusted_move(attacker: BattlerState, move: MoveData) -> MoveData:
    """The move as the user's ability changes it: Normalize, the -ate
    abilities (Normal moves become their type, 1.2x), and Sheer Force
    (drops the move's secondary effects for 1.3x power)."""
    abil = ability_key(attacker)
    changes: dict = {}
    flags = set(move.flags)
    if abil == "normalize" and move.type != "normal" and move.category != "status":
        changes["type"] = "normal"
        flags.add("ability_boost")
    elif abil in ATE_ABILITIES and move.type == "normal" and move.category != "status":
        changes["type"] = ATE_ABILITIES[abil]
        flags.add("ability_boost")
    if abil == "sheer-force" and has_secondary_effect(move):
        changes.update(ailment=None, ailment_chance=0, flinch_chance=0)
        if not (move.stat_self and all(c.get("change", 0) < 0 for c in move.stat_changes)):
            changes.update(stat_changes=(), stat_chance=0)
        flags.add("sheer_force")
    if not changes and flags == set(move.flags):
        return move
    return dataclasses.replace(move, flags=frozenset(flags), **changes)


def has_secondary_effect(move: MoveData) -> bool:
    """A damaging move's extra chance effect (burn, flinch, stat change...),
    not counting drawbacks like Close Combat's own drops."""
    if move.category == "status" or not move.power:
        return False
    if move.ailment and move.ailment_chance:
        return True
    if move.flinch_chance:
        return True
    if move.stat_changes and move.stat_chance:
        drawback = move.stat_self and all(c.get("change", 0) < 0 for c in move.stat_changes)
        return not drawback
    return False


def ability_blocks_move(attacker: BattlerState, defender: BattlerState, move: MoveData) -> Optional[str]:
    """Why the defender's ability stops this move outright, or None: the
    absorbing abilities, Flash Fire, Levitate, Wonder Guard, Soundproof."""
    if move.target == "self":
        return None
    abil = _foe_ability(defender, attacker)
    if not abil:
        return None
    damaging = move.category != "status"
    if ABSORB_HEAL.get(abil) == move.type:
        return "absorb_heal"
    if abil in ABSORB_BOOST and ABSORB_BOOST[abil][0] == move.type:
        return "absorb_boost"
    if abil == "flash-fire" and move.type == "fire":
        return "flash_fire"
    if abil == "levitate" and move.type == "ground" and damaging:
        return "immune"
    if abil == "soundproof" and "sound" in move.flags:
        return "immune"
    if abil == "wonder-guard" and damaging and move_effectiveness(attacker, move, defender) <= 1:
        return "immune"
    return None


def ability_stat_multiplier(mon: BattlerState, stat: str, weather: Optional[str] = None) -> float:
    abil = ability_key(mon)
    mult = 1.0
    if stat == "attack":
        if abil in ("huge-power", "pure-power"):
            mult *= 2
        elif abil == "guts" and mon.status:
            mult *= 1.5
        elif abil == "hustle":
            mult *= 1.5
    if stat == "defense" and abil == "marvel-scale" and mon.status:
        mult *= 1.5
    if stat in ("attack", "speed") and abil == "slow-start" and mon.volatile.get("slow_start", 0) > 0:
        mult *= 0.5
    if stat in ("attack", "sp_defense") and abil == "flower-gift" and weather == "sun":
        mult *= 1.5
    return mult


def ability_damage_multiplier(attacker: BattlerState, defender: BattlerState, move: MoveData,
                              eff: float) -> float:
    """Power/damage changes from the attacker's and the defender's abilities."""
    abil = ability_key(attacker)
    mult = 1.0
    if abil == "technician" and move.power and move.power <= 60:
        mult *= 1.5
    if abil in FLAG_BOOSTS and FLAG_BOOSTS[abil][0] in move.flags:
        mult *= FLAG_BOOSTS[abil][1]
    if abil == "reckless" and (move.recoil_percent or "recoil" in move.flags):
        mult *= 1.2
    if "sheer_force" in move.flags:
        mult *= 1.3
    if "ability_boost" in move.flags:
        mult *= 1.2
    if abil == "tinted-lens" and 0 < eff < 1:
        mult *= 2
    if PINCH_TYPES.get(abil) == move.type and attacker.current_hp * 3 <= attacker.max_hp:
        mult *= 1.5
    if abil == "toxic-boost" and attacker.status in ("poison", "toxic") and move.category == "physical":
        mult *= 1.5
    if abil == "flare-boost" and attacker.status == "burn" and move.category == "special":
        mult *= 1.5
    if attacker.volatile.get("flash_fire") and move.type == "fire":
        mult *= 1.5
    if abil == "analytic" and attacker.volatile.get("moving_last"):
        mult *= 1.3
    d_abil = _foe_ability(defender, attacker)
    if d_abil == "thick-fat" and move.type in ("fire", "ice"):
        mult *= 0.5
    elif d_abil == "heatproof" and move.type == "fire":
        mult *= 0.5
    elif d_abil == "dry-skin" and move.type == "fire":
        mult *= 1.25
    if d_abil in ("filter", "solid-rock") and eff > 1:
        mult *= 0.75
    if d_abil == "multiscale" and defender.current_hp >= defender.max_hp:
        mult *= 0.5
    return mult


def ability_accuracy_multiplier(attacker: BattlerState, defender: BattlerState, move: MoveData) -> float:
    abil = ability_key(attacker)
    mult = 1.0
    if abil == "compound-eyes":
        mult *= 1.3
    if abil == "hustle" and move.category == "physical":
        mult *= 0.8
    d_abil = _foe_ability(defender, attacker)
    if d_abil == "tangled-feet" and defender.confusion_counter > 0:
        mult *= 0.5
    return mult


def ability_chance(attacker: Optional[BattlerState], chance: float) -> float:
    """Serene Grace doubles the odds of a move's added effects."""
    if attacker is not None and ability_key(attacker) == "serene-grace" and 0 < chance < 100:
        return min(100, chance * 2)
    return chance


def can_get_status(mon: BattlerState, status: str, attacker: Optional[BattlerState] = None,
                   weather: Optional[str] = None) -> bool:
    abil = _foe_ability(mon, attacker) if attacker is not None and attacker is not mon else ability_key(mon)
    if status in STATUS_IMMUNITIES.get(abil, ()):
        return False
    if abil == "leaf-guard" and weather == "sun" and status != "confusion":
        return False
    return True


def change_stat(mon: BattlerState, stat: str, change: int, events: list, side_id: str,
                by_foe: bool = False) -> int:
    """Raises/lowers one stat stage, with the abilities that affect that:
    Simple (doubled), Contrary (reversed), Clear Body & co. (no drops from
    the foe), and Defiant / Competitive (a drop from the foe sharply raises
    Attack / Sp. Atk). Returns the change actually made."""
    abil = ability_key(mon)
    if abil == "contrary":
        change = -change
    if abil == "simple":
        change *= 2
    if by_foe and change < 0 and abil in STAT_DROP_BLOCKERS and STAT_DROP_BLOCKERS[abil] in (None, stat):
        _ability_event(events, mon, side_id, f"prevents its {STAT_NAMES.get(stat, stat)} from being lowered!")
        return 0
    old = mon.stat_stages[stat]
    new = max(-6, min(6, old + change))
    mon.stat_stages[stat] = new
    if new == old:
        events.append({"type": "stat_change_fizzled", "side": side_id, "stat": stat})
    else:
        events.append({"type": "stat_changed", "side": side_id, "stat": stat, "change": new - old})
    if by_foe and new < old and abil in ("defiant", "competitive") and not mon.is_fainted:
        boost = "attack" if abil == "defiant" else "sp_attack"
        _ability_event(events, mon, side_id, "")
        change_stat(mon, boost, 2, events, side_id)
    return new - old


def apply_status(battle: BattleState, mon: BattlerState, side_id: str, status: str, events: list,
                 source: Optional[BattlerState] = None, source_side: Optional[str] = None,
                 reason: Optional[str] = None) -> bool:
    """Gives `mon` a major status (or confusion) if nothing prevents it.
    Synchronize passes burn / paralysis / poison back to the Pokémon that
    inflicted it."""
    if mon.is_fainted:
        return False
    weather = current_weather(battle)
    if not can_get_status(mon, status, source, weather):
        return False
    if status == "confusion":
        if mon.confusion_counter > 0:
            return False
        mon.confusion_counter = battle.rng.randint(2, 5)
        events.append({"type": "status_applied", "side": side_id, "status": "confusion"})
        _after_status(mon, side_id, events)
        return True
    if mon.status is not None:
        return False
    if weather == "sun" and status == "freeze":
        return False
    mon.status = status
    if status == "sleep":
        turns = battle.rng.randint(1, 3)
        mon.status_counter = max(1, (turns + 1) // 2) if ability_key(mon) == "early-bird" else turns
    elif status == "toxic":
        mon.status_counter = 1
    else:
        mon.status_counter = 0
    event = {"type": "status_applied", "side": side_id, "status": status}
    if reason:
        event["reason"] = reason
    events.append(event)
    if (ability_key(mon) == "synchronize" and source is not None and source is not mon and source_side
            and status in ("burn", "paralysis", "poison", "toxic") and source.status is None):
        _ability_event(events, mon, side_id, "")
        apply_status(battle, source, source_side, status, events)
    _after_status(mon, side_id, events)
    return True


def _ability_damage(mon: BattlerState, side_id: str, amount: int, events: list, message: str,
                    owner: BattlerState) -> None:
    """Damage dealt by an ability (Rough Skin, Aftermath, Liquid Ooze, Bad
    Dreams). Magic Guard prevents it."""
    if mon.is_fainted or ability_key(mon) == "magic-guard":
        return
    amount = max(1, min(amount, mon.current_hp))
    mon.current_hp -= amount
    events.append({"type": "ability_damage", "side": side_id, "name": mon.species_name,
                   "ability": ability_label(owner), "message": message, "amount": amount,
                   "new_hp": mon.current_hp, "max_hp": mon.max_hp})
    _check_and_emit_faint(mon, side_id, events)
    _after_hp_loss(mon, side_id, events)


def ability_absorb(battle: BattleState, attacker: BattlerState, defender: BattlerState, move: MoveData,
                   events: list, def_side: str, reason: str) -> None:
    """What happens when a move hits an absorbing / immune ability."""
    abil = ability_key(defender)
    if reason == "absorb_heal":
        if defender.current_hp < defender.max_hp:
            heal = min(max(1, defender.max_hp // 4), defender.max_hp - defender.current_hp)
            defender.current_hp += heal
            events.append({"type": "heal", "side": def_side, "amount": heal, "reason": ability_label(defender),
                           "new_hp": defender.current_hp, "max_hp": defender.max_hp})
        else:
            _ability_event(events, defender, def_side, "is unaffected!")
    elif reason == "absorb_boost":
        _ability_event(events, defender, def_side, "absorbed the move!")
        change_stat(defender, ABSORB_BOOST[abil][1], 1, events, def_side)
    elif reason == "flash_fire":
        defender.volatile["flash_fire"] = True
        _ability_event(events, defender, def_side, "powered up its Fire-type moves!")
    else:
        _ability_event(events, defender, def_side, f"isn't affected by {move.name}!")


def after_contact(battle: BattleState, attacker: BattlerState, defender: BattlerState, move: MoveData,
                  events: list, atk_side: str, def_side: str) -> None:
    """Abilities triggered by a contact move landing."""
    if "contact" not in move.flags:
        return
    d_abil = ability_key(defender)
    if d_abil in CONTACT_STATUS and battle.rng.random() < 0.3 and attacker.status is None:
        status = CONTACT_STATUS[d_abil]
        if can_get_status(attacker, status, weather=current_weather(battle)):
            _ability_event(events, defender, def_side, "")
            apply_status(battle, attacker, atk_side, status, events, source=defender, source_side=def_side)
    elif d_abil == "effect-spore" and battle.rng.random() < 0.3 and attacker.status is None:
        if "grass" not in attacker.types and ability_key(attacker) != "overcoat":
            status = battle.rng.choice(["poison", "paralysis", "sleep"])
            if can_get_status(attacker, status, weather=current_weather(battle)):
                _ability_event(events, defender, def_side, "")
                apply_status(battle, attacker, atk_side, status, events, source=defender, source_side=def_side)
    elif d_abil == "rough-skin":
        _ability_damage(attacker, atk_side, attacker.max_hp // 8, events, "was hurt by", defender)
    if d_abil == "aftermath" and defender.is_fainted and ability_key(attacker) != "damp":
        _ability_damage(attacker, atk_side, attacker.max_hp // 4, events, "was caught in the", defender)
    if (d_abil == "pickpocket" and not defender.is_fainted and not defender.item and attacker.item
            and ability_key(attacker) != "sticky-hold"):
        stolen = attacker.item
        label = HELD_ITEMS.get(stolen, {}).get("label", stolen)
        _consume_item(attacker, atk_side, events, reason="stolen")
        defender.item = stolen
        _ability_event(events, defender, def_side, f"stole {attacker.species_name}'s {label}!")
    if (ability_key(attacker) == "poison-touch" and not defender.is_fainted and defender.status is None
            and battle.rng.random() < 0.3 and can_get_status(defender, "poison", attacker, current_weather(battle))):
        _ability_event(events, attacker, atk_side, "")
        apply_status(battle, defender, def_side, "poison", events, source=attacker, source_side=atk_side)


def after_hit(battle: BattleState, attacker: BattlerState, defender: BattlerState, move: MoveData,
              events: list, atk_side: str, def_side: str, is_crit: bool, move_slot_name: Optional[str]) -> None:
    """The defender's abilities that react to taking a hit (it's still standing)."""
    if defender.is_fainted:
        return
    abil = ability_key(defender)
    if abil == "anger-point" and is_crit and defender.stat_stages["attack"] < 6:
        defender.stat_stages["attack"] = 6
        _ability_event(events, defender, def_side, "maxed its Attack!")
        events.append({"type": "stat_changed", "side": def_side, "stat": "attack", "change": 6})
    elif abil == "justified" and move.type == "dark":
        _ability_event(events, defender, def_side, "")
        change_stat(defender, "attack", 1, events, def_side)
    elif abil == "rattled" and move.type in ("bug", "ghost", "dark"):
        _ability_event(events, defender, def_side, "")
        change_stat(defender, "speed", 1, events, def_side)
    elif abil == "weak-armor" and move.category == "physical":
        _ability_event(events, defender, def_side, "")
        change_stat(defender, "defense", -1, events, def_side)
        change_stat(defender, "speed", 2, events, def_side)
    elif abil == "color-change" and move.type and defender.types != [move.type]:
        _set_types(defender, [move.type])
        _ability_event(events, defender, def_side, f"turned into the {move.type.title()} type!")
    elif (abil == "cursed-body" and move_slot_name and not attacker.is_fainted
          and not attacker.volatile.get("disabled") and battle.rng.random() < 0.3):
        attacker.volatile["disabled"] = {"move": move_slot_name, "turns": 4}
        _ability_event(events, defender, def_side, f"disabled {attacker.species_name}'s {move_slot_name}!")


def trigger_ability_on_entry(battle: BattleState, side_id: str, events: list, traced: bool = False) -> None:
    """Abilities that announce or act as their Pokémon enters battle."""
    mon = battle.side(side_id).active
    if mon.is_fainted:
        return
    foe_side = "B" if side_id == "A" else "A"
    foe = battle.side(foe_side).active
    abil = ability_key(mon)
    foe_up = not foe.is_fainted
    if abil == "intimidate" and foe_up:
        _ability_event(events, mon, side_id, f"intimidates {foe.species_name}!")
        if ability_key(foe) in INTIMIDATE_BLOCKERS:
            _ability_event(events, foe, foe_side, "isn't intimidated!")
        else:
            change_stat(foe, "attack", -1, events, foe_side, by_foe=True)
            if ability_key(foe) == "rattled":
                change_stat(foe, "speed", 1, events, foe_side)
    elif abil == "download" and foe_up:
        d = foe.stats["defense"] * stat_stage_multiplier(foe.stat_stages["defense"])
        sd = foe.stats["sp_defense"] * stat_stage_multiplier(foe.stat_stages["sp_defense"])
        _ability_event(events, mon, side_id, "")
        change_stat(mon, "attack" if d < sd else "sp_attack", 1, events, side_id)
    elif abil == "trace" and foe_up and not traced:
        target = ability_key(foe)
        if target and target not in UNTRACEABLE:
            mon.volatile["ability_override"] = target
            events.append({"type": "ability_activated", "side": side_id, "name": mon.species_name,
                           "ability": "Trace", "message": f"traced {foe.species_name}'s {ability_label(foe)}!"})
            trigger_ability_on_entry(battle, side_id, events, traced=True)
            trigger_entry_ability(battle, side_id, events, weather_only=True)
    elif abil == "pressure":
        _ability_event(events, mon, side_id, "is exerting its pressure!")
    elif abil == "mold-breaker":
        _ability_event(events, mon, side_id, "breaks the mold!")
    elif abil == "unnerve" and foe_up:
        _ability_event(events, mon, side_id, f"makes {foe.species_name} too nervous to eat Berries!")
    elif abil == "frisk" and foe_up and foe.item:
        _ability_event(events, mon, side_id, f"frisked {foe.species_name} and found its {item_label(foe)}!")
    elif abil == "forewarn" and foe_up and foe.moves:
        best = max(foe.moves, key=lambda s: s.move.power or 0).move
        _ability_event(events, mon, side_id, f"alerted it to {foe.species_name}'s {best.name}!")
    elif abil == "anticipation" and foe_up:
        if any(s.move.power and type_effectiveness(s.move.type, mon.types) > 1 for s in foe.moves):
            _ability_event(events, mon, side_id, "made it shudder!")
    elif abil == "slow-start":
        mon.volatile["slow_start"] = 5
        _ability_event(events, mon, side_id, "can't get it going!")
    for m, s in ((mon, side_id), (foe, foe_side)):
        m.volatile["unnerved"] = ability_key(battle.side("B" if s == "A" else "A").active) == "unnerve"


def ability_on_switch_out(mon: BattlerState, side_id: str, events: list) -> None:
    if mon.is_fainted:
        return
    abil = ability_key(mon)
    if abil == "natural-cure" and mon.status:
        mon.status = None
        mon.status_counter = 0
        _ability_event(events, mon, side_id, "cured its status!")
    elif abil == "regenerator" and mon.current_hp < mon.max_hp:
        heal = min(mon.max_hp // 3, mon.max_hp - mon.current_hp)
        mon.current_hp += heal
        events.append({"type": "heal", "side": side_id, "amount": heal, "reason": "Regenerator",
                       "new_hp": mon.current_hp, "max_hp": mon.max_hp})


def traps(foe: BattlerState, mon: BattlerState) -> bool:
    """Whether `foe`'s ability keeps `mon` from switching out (Ghosts escape)."""
    if foe.is_fainted or "ghost" in mon.types:
        return False
    abil = ability_key(foe)
    if abil == "shadow-tag":
        return ability_key(mon) != "shadow-tag"
    if abil == "arena-trap":
        return is_grounded(mon)
    if abil == "magnet-pull":
        return "steel" in mon.types
    return False


def apply_end_of_turn_ability(battle: BattleState, side_id: str) -> list:
    events: list = []
    mon = battle.side(side_id).active
    if mon.is_fainted:
        return events
    abil = ability_key(mon)
    v = mon.volatile
    if abil == "speed-boost" and mon.stat_stages["speed"] < 6:
        _ability_event(events, mon, side_id, "")
        change_stat(mon, "speed", 1, events, side_id)
    elif abil == "shed-skin" and mon.status and battle.rng.random() < 0.3:
        mon.status = None
        mon.status_counter = 0
        _ability_event(events, mon, side_id, "shed its status!")
    elif abil == "moody":
        ups = [s for s in MOODY_STATS if mon.stat_stages[s] < 6]
        if ups:
            up = battle.rng.choice(ups)
            downs = [s for s in MOODY_STATS if s != up and mon.stat_stages[s] > -6]
            _ability_event(events, mon, side_id, "")
            change_stat(mon, up, 2, events, side_id)
            if downs:
                change_stat(mon, battle.rng.choice(downs), -1, events, side_id)
    elif abil == "bad-dreams":
        foe_side = "B" if side_id == "A" else "A"
        foe = battle.side(foe_side).active
        if foe.status == "sleep":
            _ability_damage(foe, foe_side, foe.max_hp // 8, events, "is tormented by", mon)
    elif abil == "harvest" and not mon.item and v.get("last_berry"):
        if current_weather(battle) == "sun" or battle.rng.random() < 0.5:
            mon.item = v["last_berry"]
            v["last_berry"] = None
            _ability_event(events, mon, side_id, f"harvested its {item_label(mon)}!")
    if v.get("slow_start", 0) > 0:
        v["slow_start"] -= 1
        if v["slow_start"] == 0:
            _ability_event(events, mon, side_id, "finally got its act together!")
    disabled = v.get("disabled")
    if disabled:
        disabled["turns"] -= 1
        if disabled["turns"] <= 0:
            v["disabled"] = None
    return events


# Every ability these battles simulate (the team page marks them "active in
# battles"); see NO_BATTLE_EFFECT_ABILITIES / UNSIMULATED_ABILITIES for the rest.
BATTLE_ABILITIES = (
    set(WEATHER_ABILITIES) | WEATHER_NEGATING_ABILITIES | set(SPEED_DOUBLERS) | SAND_IMMUNE_ABILITIES
    | HAIL_IMMUNE_ABILITIES | {"rain-dish", "dry-skin", "hydration", "solar-power", "leaf-guard", "sand-force"}
    | set(STATUS_IMMUNITIES) | set(ABSORB_HEAL) | set(ABSORB_BOOST) | set(PINCH_TYPES) | set(ATE_ABILITIES)
    | set(FLAG_BOOSTS) | set(STAT_DROP_BLOCKERS) | set(CONTACT_STATUS) | {
        "adaptability", "flash-fire", "levitate", "soundproof", "wonder-guard", "huge-power", "pure-power",
        "guts", "hustle", "marvel-scale", "slow-start", "flower-gift", "technician", "reckless", "sheer-force",
        "normalize", "tinted-lens", "toxic-boost", "flare-boost", "analytic", "thick-fat", "heatproof",
        "filter", "solid-rock", "multiscale", "compound-eyes", "tangled-feet", "no-guard", "unaware",
        "wonder-skin", "serene-grace", "shield-dust", "stench", "inner-focus", "steadfast", "early-bird",
        "synchronize", "simple", "contrary", "defiant", "competitive", "effect-spore", "rough-skin",
        "aftermath", "pickpocket", "poison-touch", "sticky-hold", "anger-point", "justified", "rattled",
        "weak-armor", "color-change", "cursed-body", "intimidate", "download", "trace", "pressure",
        "mold-breaker", "unnerve", "frisk", "forewarn", "anticipation", "natural-cure", "regenerator",
        "shadow-tag", "arena-trap", "magnet-pull", "speed-boost", "shed-skin", "moody", "bad-dreams",
        "harvest", "quick-feet", "unburden", "sturdy", "battle-armor", "shell-armor", "super-luck",
        "sniper", "skill-link", "prankster", "stall", "truant", "protean", "parental-bond", "liquid-ooze",
        "magic-guard", "poison-heal", "damp", "magic-bounce", "heavy-metal", "light-metal", "scrappy",
        "oblivious", "rock-head", "overcoat", "moxie",
    }
)


# ---------------------------------------------------------------------------
# Turn resolution
# ---------------------------------------------------------------------------

def _action_priority(battle: BattleState, side_id: str, action: Action) -> int:
    if action.kind == "switch":
        return 6  # above every move priority bracket (-7..+5)
    side = battle.side(side_id)
    active = side.active
    if action.move_index is None or action.move_index >= len(active.moves):
        return 0  # Struggle
    move = active.moves[action.move_index].move
    if ability_key(active) == "prankster" and move.category == "status":
        return move.priority + 1
    return move.priority


def _effective_speed(mon: BattlerState, weather: Optional[str] = None) -> float:
    spd = mon.stats["speed"] * stat_stage_multiplier(mon.stat_stages["speed"])
    abil = ability_key(mon)
    if abil == "quick-feet" and mon.status:
        spd *= 1.5  # and paralysis doesn't slow it
    elif mon.status == "paralysis":
        spd *= 0.5
    if abil == "unburden" and mon.volatile.get("unburdened"):
        spd *= 2
    spd *= ability_stat_multiplier(mon, "speed", weather)
    if weather and SPEED_DOUBLERS.get(ability_key(mon)) == weather:
        spd *= 2
    return spd * item_stat_multiplier(mon, "speed")


def _order_actions(battle: BattleState, action_a: Action, action_b: Action, events: Optional[list] = None) -> list:
    entries = [("A", action_a), ("B", action_b)]
    prio_a = _action_priority(battle, "A", action_a)
    prio_b = _action_priority(battle, "B", action_b)
    if prio_a != prio_b:
        return entries if prio_a > prio_b else [entries[1], entries[0]]
    # Quick Claw: a 20% chance to jump ahead within the same priority.
    claws = [s for s, a in entries if a.kind == "move" and battle.side(s).active.item == "quick-claw"
             and battle.rng.random() < QUICK_CLAW_CHANCE]
    if len(claws) == 1:
        side_id = claws[0]
        if events is not None:
            mon = battle.side(side_id).active
            events.append({"type": "item_activated", "side": side_id, "name": mon.species_name,
                           "item": "quick-claw", "label": item_label(mon)})
        return entries if side_id == "A" else [entries[1], entries[0]]
    stall = [s for s, _ in entries if ability_key(battle.side(s).active) == "stall"]
    if len(stall) == 1:
        return [entries[1], entries[0]] if stall[0] == "A" else entries
    weather = current_weather(battle)
    spd_a = _effective_speed(battle.side_a.active, weather)
    spd_b = _effective_speed(battle.side_b.active, weather)
    if spd_a != spd_b:
        return entries if spd_a > spd_b else [entries[1], entries[0]]
    return entries if battle.rng.random() < 0.5 else [entries[1], entries[0]]


def do_switch(side: BattleSide, target_index: int, battle: Optional[BattleState] = None) -> list:
    events = []
    outgoing = side.active
    wish = outgoing.volatile.get("healing_wish") if outgoing.is_fainted else None
    ability_on_switch_out(outgoing, side.side_id, events)
    events.append({"type": "switch_out", "side": side.side_id, "dex_id": outgoing.dex_id, "name": outgoing.species_name})
    if outgoing.volatile.get("types"):
        outgoing.types = list(outgoing.volatile.get("base_types") or outgoing.types)
    outgoing.stat_stages = default_stat_stages()
    outgoing.volatile = default_volatile()
    outgoing.confusion_counter = 0
    side.active_index = target_index
    incoming = side.active
    events.append({"type": "switch_in", "side": side.side_id, "dex_id": incoming.dex_id, "name": incoming.species_name})
    if wish and not incoming.is_fainted:
        heal = incoming.max_hp - incoming.current_hp
        incoming.current_hp = incoming.max_hp
        incoming.status = None
        incoming.status_counter = 0
        events.append({"type": "heal", "side": side.side_id, "amount": heal, "reason": wish,
                       "new_hp": incoming.current_hp, "max_hp": incoming.max_hp})
    if battle is not None:
        trigger_entry_ability(battle, side.side_id, events)
    return events


def apply_forced_switch(battle: BattleState, side_id: str, team_index: int) -> list:
    side = battle.side(side_id)
    events = do_switch(side, team_index, battle)
    if side_id in battle.forced_switch_sides:
        battle.forced_switch_sides.remove(side_id)
    if not battle.forced_switch_sides:
        battle.status = "active"
    return events


def apply_faint_and_check_winner(battle: BattleState) -> Optional[str]:
    a_alive = any(not b.is_fainted for b in battle.side_a.roster)
    b_alive = any(not b.is_fainted for b in battle.side_b.roster)
    if not a_alive and not b_alive:
        return "draw"
    if not a_alive:
        return "B"
    if not b_alive:
        return "A"
    return None


def _apply_damage_and_emit(defender: BattlerState, amount: int, events: list, defender_side_id: str,
                            move_name: str, is_crit: bool, move_type: Optional[str],
                            attacker: Optional[BattlerState] = None) -> int:
    if move_type is None:
        eff = 1.0
    elif attacker is not None and ability_key(attacker) == "scrappy" and move_type in ("normal", "fighting"):
        eff = type_effectiveness(move_type, [t for t in defender.types if t != "ghost"] or ["normal"])
    else:
        eff = type_effectiveness(move_type, defender.types)
    amount = max(0, int(amount))
    full = defender.current_hp == defender.max_hp and amount >= defender.current_hp and defender.max_hp > 1
    sturdy = full and _foe_ability(defender, attacker) == "sturdy"
    sash = full and not sturdy and defender.item == "focus-sash"
    if sash or sturdy:
        amount = defender.current_hp - 1
    defender.current_hp = max(0, defender.current_hp - amount)
    events.append({
        "type": "damage", "side": defender_side_id, "move_name": move_name, "amount": amount,
        "new_hp": defender.current_hp, "max_hp": defender.max_hp,
        "effectiveness": effectiveness_label(eff), "is_crit": is_crit,
    })
    if sash:
        _consume_item(defender, defender_side_id, events)
    if sturdy:
        _ability_event(events, defender, defender_side_id, "endured the hit!")
    if defender.current_hp <= 0 and not defender.is_fainted:
        defender.is_fainted = True
        events.append({"type": "faint", "side": defender_side_id, "dex_id": defender.dex_id, "name": defender.species_name})
    _after_hp_loss(defender, defender_side_id, events)
    return amount


def _check_and_emit_faint(mon: BattlerState, side_id: str, events: list) -> None:
    if mon.current_hp <= 0 and not mon.is_fainted:
        mon.is_fainted = True
        events.append({"type": "faint", "side": side_id, "dex_id": mon.dex_id, "name": mon.species_name})


def _maybe_apply_ailment(battle: BattleState, move: MoveData, target: BattlerState, events: list, target_side_id: str,
                         user: Optional[BattlerState] = None, user_side: Optional[str] = None) -> None:
    if not move.ailment:
        return
    if move.ailment == "confusion" and target.confusion_counter > 0:
        return
    if move.ailment != "confusion" and target.status is not None:
        return  # already has a major status; secondary status effects don't stack/overwrite
    if battle.rng.random() * 100 < ability_chance(user, move.ailment_chance):
        source = user if user is not target else None
        apply_status(battle, target, target_side_id, move.ailment, events, source=source,
                     source_side=user_side if source else None)


def _maybe_apply_stat_changes(battle: BattleState, move: MoveData, target: BattlerState, events: list, target_side_id: str,
                              user: Optional[BattlerState] = None) -> None:
    if not move.stat_changes:
        return
    if battle.rng.random() * 100 >= ability_chance(user, move.stat_chance):
        return
    for sc in move.stat_changes:
        # Move data (PokeAPI) spells the special stats "special_attack"/
        # "special_defense"; the engine's stages use sp_attack/sp_defense.
        # Without this, every Sp. Atk/Sp. Def change (Calm Mind, Nasty Plot,
        # Overheat's drop, ...) was silently skipped below.
        stat = STAT_ALIASES.get(sc.get("stat"), sc.get("stat"))
        change = sc.get("change", 0)
        if stat not in target.stat_stages:
            continue
        change_stat(target, stat, change, events, target_side_id, by_foe=user is not None and user is not target)


def _maybe_apply_flinch(battle: BattleState, move: MoveData, defender: BattlerState,
                        attacker: Optional[BattlerState] = None) -> None:
    if _foe_ability(defender, attacker) == "inner-focus":
        return
    chance = ability_chance(attacker, move.flinch_chance)
    if not chance and attacker is not None and ability_key(attacker) == "stench" and move.power:
        chance = 10
    if chance and battle.rng.random() * 100 < chance:
        defender.volatile["flinched"] = True


def _apply_end_of_turn_status(mon: BattlerState, side_id: str) -> list:
    events = []
    abil = ability_key(mon)
    if abil == "poison-heal" and mon.status in ("poison", "toxic"):
        if mon.current_hp < mon.max_hp:
            heal = min(max(1, mon.max_hp // 8), mon.max_hp - mon.current_hp)
            mon.current_hp += heal
            events.append({"type": "heal", "side": side_id, "amount": heal, "reason": "Poison Heal",
                           "new_hp": mon.current_hp, "max_hp": mon.max_hp})
        return events
    if abil == "magic-guard":
        return events
    if mon.status in ("burn", "poison"):
        dmg = max(1, mon.max_hp // 16)
        mon.current_hp = max(0, mon.current_hp - dmg)
        events.append({"type": "status_damage", "side": side_id, "status": mon.status,
                       "amount": dmg, "new_hp": mon.current_hp, "max_hp": mon.max_hp})
        _check_and_emit_faint(mon, side_id, events)
        _after_hp_loss(mon, side_id, events)
    elif mon.status == "toxic":
        stacks = min(mon.status_counter, 15)
        dmg = max(1, (stacks * mon.max_hp) // 16)
        mon.current_hp = max(0, mon.current_hp - dmg)
        mon.status_counter += 1
        events.append({"type": "status_damage", "side": side_id, "status": "toxic",
                       "amount": dmg, "new_hp": mon.current_hp, "max_hp": mon.max_hp})
        _check_and_emit_faint(mon, side_id, events)
        _after_hp_loss(mon, side_id, events)
    return events


def _execute_move_action(battle: BattleState, side: BattleSide, opp: BattleSide, action: Action) -> list:
    events = _execute_move_core(battle, side, opp, action)
    active = side.active
    used = next((e for e in events if e["type"] == "move_used" and e["side"] == side.side_id), None)
    if used and used["move_name"] in SELF_KO_MOVES and not active.is_fainted:
        spared = any(e["type"] == "move_failed" and e.get("reason") in ("no_teammates", "immune", "damp") for e in events)
        if not spared:
            # Explosion & co.: the user faints whether or not the move landed.
            active.current_hp = 0
            if used["move_name"] in ("Healing Wish", "Lunar Dance"):
                active.volatile["healing_wish"] = used["move_name"]
            events.append({"type": "self_ko", "side": side.side_id, "name": active.species_name,
                           "move_name": used["move_name"]})
            _check_and_emit_faint(active, side.side_id, events)
    return events


def _execute_move_core(battle: BattleState, side: BattleSide, opp: BattleSide, action: Action) -> list:
    events: list = []
    active = side.active
    defender = opp.active
    v = active.volatile

    if v.get("must_recharge"):
        v["must_recharge"] = False
        events.append({"type": "cannot_act", "side": side.side_id, "reason": "recharge"})
        return events

    if ability_key(active) == "truant":
        if v.get("truant_loaf"):
            v["truant_loaf"] = False
            events.append({"type": "cannot_act", "side": side.side_id, "reason": "truant"})
            return events
        v["truant_loaf"] = True

    if active.status == "sleep":
        active.status_counter -= 2 if ability_key(active) == "early-bird" else 1
        if active.status_counter <= 0:
            active.status = None
            events.append({"type": "status_applied", "side": side.side_id, "status": "none", "reason": "woke_up"})
        else:
            events.append({"type": "cannot_act", "side": side.side_id, "reason": "asleep"})
            return events

    if active.status == "freeze":
        if battle.rng.random() < 0.20:
            active.status = None
            events.append({"type": "status_applied", "side": side.side_id, "status": "none", "reason": "thawed"})
        else:
            events.append({"type": "cannot_act", "side": side.side_id, "reason": "frozen"})
            return events

    if v.get("flinched"):
        v["flinched"] = False
        events.append({"type": "cannot_act", "side": side.side_id, "reason": "flinched"})
        if ability_key(active) == "steadfast":
            _ability_event(events, active, side.side_id, "")
            change_stat(active, "speed", 1, events, side.side_id)
        return events

    if active.confusion_counter > 0:
        active.confusion_counter -= 1
        if active.confusion_counter == 0:
            events.append({"type": "status_applied", "side": side.side_id, "status": "none", "reason": "confusion_ended"})
        if battle.rng.random() < 0.33:
            dmg = compute_confusion_damage(active, battle.rng)
            active.current_hp = max(0, active.current_hp - dmg)
            events.append({"type": "confusion_self_hit", "side": side.side_id, "amount": dmg,
                            "new_hp": active.current_hp, "max_hp": active.max_hp})
            _check_and_emit_faint(active, side.side_id, events)
            return events

    if active.status == "paralysis":
        if battle.rng.random() < 0.25:
            events.append({"type": "cannot_act", "side": side.side_id, "reason": "paralyzed"})
            return events

    move_index = action.move_index
    is_release_turn = False

    if v.get("locked_move"):
        for i, ms in enumerate(active.moves):
            if ms.move.name == v["locked_move"]:
                move_index = i
                break

    if v.get("charging_move"):
        for i, ms in enumerate(active.moves):
            if ms.move.name == v["charging_move"]:
                move_index = i
                break
        v["charging_move"] = None
        is_release_turn = True

    if not is_release_turn:
        no_pp = move_index is None or move_index >= len(active.moves) or active.moves[move_index].current_pp <= 0
        if no_pp:
            move = STRUGGLE_MOVE
            events.append({"type": "move_used", "side": side.side_id, "move_name": move.name,
                           "move_type": move.type, "category": move.category, "target": move.target})
            dmg = compute_damage(active, defender, move, False, battle.rng)
            _apply_damage_and_emit(defender, dmg, events, opp.side_id, move.name, False, move.type)
            recoil = max(1, active.max_hp // 4)
            active.current_hp = max(0, active.current_hp - recoil)
            events.append({"type": "recoil", "side": side.side_id, "amount": recoil,
                            "new_hp": active.current_hp, "max_hp": active.max_hp})
            _check_and_emit_faint(active, side.side_id, events)
            return events

        move_slot = active.moves[move_index]
        move = ability_adjusted_move(active, weather_adjusted_move(move_slot.move, current_weather(battle)))
        pp_cost = 2 if (ability_key(defender) == "pressure" and move.target != "self" and not defender.is_fainted) else 1

        if "charge" in move.flags:
            move_slot.current_pp = max(0, move_slot.current_pp - pp_cost)
            v["charging_move"] = move.name
            # Fly/Dig/Dive/Bounce make the user unhittable for the rest of
            # THIS turn only — tagging it with the current turn number (not a
            # plain bool cleared elsewhere) means the check below is correct
            # no matter which side acts first, on the charge turn or the
            # release turn that follows.
            if "semi_invulnerable" in move.flags:
                v["invulnerable_until_turn"] = battle.turn_number
            events.append({"type": "charge_start", "side": side.side_id, "move_name": move.name,
                            "move_type": move.type})
            return events

        move_slot.current_pp = max(0, move_slot.current_pp - pp_cost)
    else:
        # Release turn of a charging move (Solar Beam etc.) — move_index was
        # already resolved above from volatile["charging_move"]; no new PP cost.
        move = ability_adjusted_move(active, weather_adjusted_move(active.moves[move_index].move, current_weather(battle)))
    move_slot_name = active.moves[move_index].move.name if move_index is not None and move_index < len(active.moves) else None

    events.append({"type": "move_used", "side": side.side_id, "move_name": move.name,
                   "move_type": move.type, "category": move.category, "target": move.target})
    if active.item in CHOICE_ITEMS and not v.get("choice_lock"):
        v["choice_lock"] = move.name
    if ability_key(active) == "protean" and move.type and active.types != [move.type]:
        _set_types(active, [move.type])
        _ability_event(events, active, side.side_id, f"turned into the {move.type.title()} type!")
    if move.name in DAMP_BLOCKED and "damp" in (ability_key(active), ability_key(defender)):
        damp_mon, damp_side = (active, side.side_id) if ability_key(active) == "damp" else (defender, opp.side_id)
        _ability_event(events, damp_mon, damp_side, f"prevents {move.name}!")
        events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "damp"})
        return events

    if move.name in WEATHER_MOVES:
        if not set_weather(battle, WEATHER_MOVES[move.name], events, side.side_id, "move", active):
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "weather_active"})
        return events

    # Bide: stores the damage it takes for two turns, then hits back double.
    if move.name == "Bide":
        bide = v.get("bide")
        if not bide:
            v["bide"] = {"turns": 2, "stored": 0}
            v["locked_move"] = "Bide"
            events.append({"type": "bide", "side": side.side_id, "stage": "start"})
            return events
        bide["turns"] -= 1
        if bide["turns"] > 0:
            events.append({"type": "bide", "side": side.side_id, "stage": "storing"})
            return events
        v["bide"] = None
        v["locked_move"] = None
        events.append({"type": "bide", "side": side.side_id, "stage": "release"})
        if bide["stored"] <= 0 or type_effectiveness(move.type, defender.types) == 0:
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "nothing_stored"})
            return events
        _apply_damage_and_emit(defender, bide["stored"] * 2, events, opp.side_id, move.name, False, None)
        return events

    if move.name == "Stockpile":
        if v.get("stockpile", 0) >= STOCKPILE_MAX:
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "stockpile_full"})
            return events
        v["stockpile"] = v.get("stockpile", 0) + 1
        events.append({"type": "stockpile", "side": side.side_id, "count": v["stockpile"]})
    if move.name == "Swallow":
        stock = v.get("stockpile", 0)
        if not stock:
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "nothing_stockpiled"})
            return events
        move = dataclasses.replace(move, healing_percent={1: 25, 2: 50}.get(stock, 100),
                                   stat_changes=tuple(_stockpile_stat_changes(stock)), stat_chance=100)
        v["stockpile"] = 0

    if move.name == "Present" and battle.rng.random() < 0.2:
        # Present sometimes heals the target instead.
        heal = min(defender.max_hp // 4, defender.max_hp - defender.current_hp)
        defender.current_hp += heal
        events.append({"type": "heal", "side": opp.side_id, "amount": heal, "reason": "Present",
                       "new_hp": defender.current_hp, "max_hp": defender.max_hp})
        return events

    if move.name in VARIABLE_POWER_MOVES:
        pp_left = active.moves[move_index].current_pp if move_index is not None and move_index < len(active.moves) else None
        resolved = variable_power(active, defender, move, rng=battle.rng, pp_left=pp_left, weather=current_weather(battle))
        if resolved is None:
            reason = "nothing_stockpiled" if move.name == "Spit Up" else "no_item"
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": reason})
            return events
        if move.name in ("Fling", "Natural Gift"):
            _consume_item(active, side.side_id, events, reason=move.name)
        if move.name == "Magnitude":
            level = next((lv for lv, p, _ in MAGNITUDES if p == resolved.power), 7)
            events.append({"type": "magnitude", "side": side.side_id, "level": level})
        move = resolved

    if move.name in ("Healing Wish", "Lunar Dance"):
        if not any(not b.is_fainted for i, b in enumerate(side.roster) if i != side.active_index):
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "no_teammates"})
        return events

    if move.target != "self" and defender.volatile.get("invulnerable_until_turn") == battle.turn_number:
        events.append({"type": "move_missed", "side": side.side_id, "move_name": move.name,
                        "reason": "invulnerable", "target_name": defender.species_name})
        if "recharge" in move.flags:
            active.volatile["must_recharge"] = True
        return events

    no_guard = "no-guard" in (ability_key(active), ability_key(defender))
    if "always_hit" not in move.flags and move.accuracy is not None and not no_guard:
        acc_stage, eva_stage = active.stat_stages["accuracy"], defender.stat_stages["evasion"]
        if ability_key(active) in ("unaware", "keen-eye"):
            eva_stage = min(eva_stage, 0) if ability_key(active) == "keen-eye" else 0
        if _foe_ability(defender, active) == "unaware":
            acc_stage = 0
        acc_mult = stat_stage_multiplier(acc_stage - eva_stage, is_accuracy_or_evasion=True)
        accuracy = move.accuracy
        if move.category == "status" and _foe_ability(defender, active) == "wonder-skin" and move.target != "self":
            accuracy = min(accuracy, 50)
        effective_acc = (accuracy * acc_mult * weather_evasion_multiplier(current_weather(battle), defender)
                         * ability_accuracy_multiplier(active, defender, move))
        if defender.item == "bright-powder":
            effective_acc *= 0.9
        if battle.rng.random() * 100 >= effective_acc:
            events.append({"type": "move_missed", "side": side.side_id, "move_name": move.name})
            if "recharge" in move.flags:
                active.volatile["must_recharge"] = True
            return events

    blocked = ability_blocks_move(active, defender, move)
    if blocked:
        ability_absorb(battle, active, defender, move, events, opp.side_id, blocked)
        if "recharge" in move.flags:
            active.volatile["must_recharge"] = True
        return events

    dmg = 0
    total_dealt = 0
    is_crit = False

    immune = move_effectiveness(active, move, defender) == 0
    if move.name == "Final Gambit":
        if immune:
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "immune"})
            return events
        total_dealt = _apply_damage_and_emit(defender, active.current_hp, events, opp.side_id, move.name, False, move.type)
    elif move.name in COUNTER_MOVES:
        # Hits back for a multiple of the damage the user took from an attack
        # this turn (so Counter/Mirror Coat move last, Metal Burst must be slower).
        answers, mult = COUNTER_MOVES[move.name]
        hit = v.get("last_hit")
        if (not hit or hit.get("turn") != battle.turn_number or (answers and hit.get("category") != answers)
                or immune):
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "nothing_to_return"})
            return events
        total_dealt = _apply_damage_and_emit(defender, int(hit["amount"] * mult), events, opp.side_id, move.name, False, None)
    elif move.name == "Super Fang":
        if immune:
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "immune"})
            return events
        total_dealt = _apply_damage_and_emit(defender, max(1, defender.current_hp // 2), events, opp.side_id, move.name, False, None)
    elif move.name == "Endeavor":
        if immune or defender.current_hp <= active.current_hp:
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "immune" if immune else "no_effect"})
            return events
        total_dealt = _apply_damage_and_emit(defender, defender.current_hp - active.current_hp, events, opp.side_id, move.name, False, None)
    elif "ohko" in move.flags:
        # The generic accuracy check above already gated this on the move's
        # own accuracy field (PokeAPI encodes OHKO moves' ~30% hit chance
        # there directly) — reaching here means it already hit, so the only
        # remaining condition is mainline's "fails if the target is faster."
        if _foe_ability(defender, active) == "sturdy":
            _ability_event(events, defender, opp.side_id, "can't be knocked out in one hit!")
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "immune"})
        elif _effective_speed(defender, current_weather(battle)) > _effective_speed(active, current_weather(battle)):
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "outsped"})
        else:
            total_dealt = _apply_damage_and_emit(defender, defender.current_hp, events, opp.side_id, move.name, False, move.type)
    elif "fixed_damage" in move.flags:
        amount = move.fixed_damage_amount if move.fixed_damage_amount is not None else 100
        total_dealt = _apply_damage_and_emit(defender, 0 if immune else amount, events, opp.side_id, move.name, False, move.type)
    elif move.category == "status":
        pass
    elif "multi_hit" in move.flags:
        if move.name == "Beat Up":
            # One hit per healthy party member: power 5 + base Attack / 10
            # (base Attack recovered from the level-100 stat, ~2 x base + 36).
            powers = [5 + max(1, (b.stats["attack"] - 36) // 2) // 10
                      for b in side.roster if not b.is_fainted and b.status is None] or [10]
        else:
            count = (move.max_hits or 2) if ability_key(active) == "skill-link" else sample_multi_hit_count(move, battle.rng)
            powers = [move.power] * count
        hits = 0
        for hit_power in powers:
            if defender.is_fainted:
                break
            hit_move = move if hit_power == move.power else dataclasses.replace(move, power=hit_power)
            hit_crit = battle.rng.random() < crit_chance(move, active, defender)
            hit_dmg = compute_damage(active, defender, hit_move, hit_crit, battle.rng, current_weather(battle))
            total_dealt += _apply_damage_and_emit(defender, hit_dmg, events, opp.side_id, move.name, hit_crit, move.type, active)
            is_crit = is_crit or hit_crit
            hits += 1
        events.append({"type": "multi_hit_summary", "side": side.side_id, "hits": hits, "total_damage": total_dealt})
    else:
        is_crit = battle.rng.random() < crit_chance(move, active, defender)
        dmg = compute_damage(active, defender, move, is_crit, battle.rng, current_weather(battle))
        total_dealt = _apply_damage_and_emit(defender, dmg, events, opp.side_id, move.name, is_crit, move.type, active)
        if ability_key(active) == "parental-bond" and not defender.is_fainted and total_dealt > 0:
            # Parental Bond: a second hit at a quarter power.
            second = dataclasses.replace(move, power=max(1, move.power // 4))
            crit2 = battle.rng.random() < crit_chance(move, active, defender)
            dmg2 = compute_damage(active, defender, second, crit2, battle.rng, current_weather(battle))
            total_dealt += _apply_damage_and_emit(defender, dmg2, events, opp.side_id, move.name, crit2, move.type, active)
            events.append({"type": "multi_hit_summary", "side": side.side_id, "hits": 2, "total_damage": total_dealt})

    if total_dealt > 0 and move.category in ("physical", "special"):
        defender.volatile["last_hit"] = {"turn": battle.turn_number, "amount": total_dealt, "category": move.category}
        if defender.volatile.get("bide"):
            defender.volatile["bide"]["stored"] += total_dealt
        after_hit(battle, active, defender, move, events, side.side_id, opp.side_id, is_crit, move_slot_name)
        after_contact(battle, active, defender, move, events, side.side_id, opp.side_id)
        if defender.is_fainted and ability_key(active) == "moxie" and not active.is_fainted:
            _ability_event(events, active, side.side_id, "")
            change_stat(active, "attack", 1, events, side.side_id)
    if move.name == "Spit Up":
        stock = v.get("stockpile", 0)
        v["stockpile"] = 0
        if stock and not active.is_fainted:
            move = dataclasses.replace(move, stat_changes=tuple(_stockpile_stat_changes(stock)), stat_chance=100,
                                       stat_self=True)

    if move.drain_percent and total_dealt > 0:
        heal = max(1, math.floor(total_dealt * move.drain_percent / 100))
        if ability_key(defender) == "liquid-ooze":
            _ability_damage(active, side.side_id, heal, events, "sucked up the", defender)
        else:
            active.current_hp = min(active.max_hp, active.current_hp + heal)
            events.append({"type": "drain", "side": side.side_id, "amount": heal,
                            "new_hp": active.current_hp, "max_hp": active.max_hp})

    if move.recoil_percent and total_dealt > 0 and ability_key(active) not in ("rock-head", "magic-guard"):
        recoil = max(1, math.floor(total_dealt * move.recoil_percent / 100))
        active.current_hp = max(0, active.current_hp - recoil)
        events.append({"type": "recoil", "side": side.side_id, "amount": recoil,
                        "new_hp": active.current_hp, "max_hp": active.max_hp})
        _check_and_emit_faint(active, side.side_id, events)
        _after_hp_loss(active, side.side_id, events)

    if (active.item == "life-orb" and total_dealt > 0 and move.power and not active.is_fainted
            and move.name not in SELF_KO_MOVES and ability_key(active) != "magic-guard"):
        cost = max(1, active.max_hp // 10)
        active.current_hp = max(0, active.current_hp - cost)
        events.append({"type": "item_damage", "side": side.side_id, "name": active.species_name,
                       "item": "life-orb", "label": item_label(active), "amount": cost,
                       "new_hp": active.current_hp, "max_hp": active.max_hp})
        _check_and_emit_faint(active, side.side_id, events)
        _after_hp_loss(active, side.side_id, events)

    if move.healing_percent and not active.is_fainted:
        heal = max(1, math.floor(active.max_hp * move.healing_percent / 100))
        active.current_hp = min(active.max_hp, active.current_hp + heal)
        events.append({"type": "heal", "side": side.side_id, "amount": heal,
                        "new_hp": active.current_hp, "max_hp": active.max_hp})

    if move.category == "status":
        target_mon = active if move.target == "self" else defender
        target_side_id = side.side_id if move.target == "self" else opp.side_id
        user, user_side = active, side.side_id
        if move.target != "self" and _foe_ability(defender, active) == "magic-bounce" and (move.ailment or move.stat_changes):
            _ability_event(events, defender, opp.side_id, f"bounced the {move.name} back!")
            target_mon, target_side_id, user, user_side = active, side.side_id, defender, opp.side_id
        _maybe_apply_ailment(battle, move, target_mon, events, target_side_id, user, user_side)
        _maybe_apply_stat_changes(battle, move, target_mon, events, target_side_id, user)
    else:
        shielded = _foe_ability(defender, active) == "shield-dust"
        if not defender.is_fainted and not shielded:
            _maybe_apply_ailment(battle, move, defender, events, opp.side_id, active, side.side_id)
        if move.stat_self:
            if not active.is_fainted:
                _maybe_apply_stat_changes(battle, move, active, events, side.side_id, active)
        elif not defender.is_fainted and not shielded:
            _maybe_apply_stat_changes(battle, move, defender, events, opp.side_id, active)
        if not shielded:
            _maybe_apply_flinch(battle, move, defender, active)

    if "multi_turn_lock" in move.flags:
        if not v.get("locked_move"):
            v["locked_move"] = move.name
            v["lock_turns_remaining"] = battle.rng.randint(2, 3) - 1
        else:
            v["lock_turns_remaining"] -= 1
            if v["lock_turns_remaining"] <= 0:
                v["locked_move"] = None
                if not active.is_fainted and can_get_status(active, "confusion"):
                    active.confusion_counter = battle.rng.randint(2, 3)
                    events.append({"type": "status_applied", "side": side.side_id, "status": "confusion", "reason": "fatigue"})
                    _after_status(active, side.side_id, events)

    if "recharge" in move.flags:
        active.volatile["must_recharge"] = True

    _check_and_emit_faint(active, side.side_id, events)
    return events


def stab_multiplier(attacker: BattlerState, move: MoveData) -> float:
    """Same-type attack bonus: 1.5x, or 2x with Adaptability."""
    if not move.type or move.type not in attacker.types:
        return 1.0
    return 2.0 if ability_key(attacker) == "adaptability" else 1.5


def mega_options(side: BattleSide) -> list:
    """The Megas a player's active Pokémon can become right now: one Mega
    Evolution per battle, like in the games, and only species they've
    unlocked (Charizard can have both X and Y)."""
    if side.mega_used or not side.player_megas or side.active.is_fainted:
        return []
    return list(side.player_megas.get(side.active.dex_id, []))


def mega_evolve(battle: BattleState, side_id: str, mega: dict | None = None) -> list:
    """Mega Evolves `side_id`'s active Pokémon: into `mega` (a player's
    choice), or into its NPC side's Mega for it. Happens at the start of the
    turn, before anyone moves, so the new Speed already counts. It keeps its
    HP, moves, item, status and stat changes; its species, types, stats and
    ability become the Mega's."""
    side = battle.side(side_id)
    mon = side.active
    mega = mega or side.mega_forms.get(mon.dex_id)
    if not mega or mon.is_fainted:
        return []
    side.mega_used = True
    new_stats = stats_at_level_100(mega.get("base_stats", {}), mon.ivs, mon.evs)
    for key in ("attack", "defense", "sp_attack", "sp_defense", "speed"):
        mon.stats[key] = new_stats[key]
    old_name, old_dex = mon.species_name, mon.dex_id
    abilities = mega.get("abilities") or []
    mon.dex_id, mon.species_name = mega["id"], mega["name"]
    mon.types = list(mega.get("types", mon.types))
    mon.volatile.pop("types", None)
    mon.volatile.pop("ability_override", None)
    mon.ability = abilities[0]["name"] if abilities else mon.ability
    mon.weight = float(mega.get("weight") or mon.weight)
    events = [{"type": "mega_evolution", "side": side_id, "name": old_name, "dex_id": old_dex,
               "mega_name": mon.species_name, "mega_dex_id": mon.dex_id, "types": list(mon.types),
               "ability": ability_label(mon)}]
    trigger_entry_ability(battle, side_id, events)  # e.g. a Mega whose new ability sets weather
    return events


def resolve_turn(battle: BattleState, action_a: Action, action_b: Action) -> TurnResult:
    """Resolves one simultaneous turn (both sides already chose an Action)
    and mutates `battle` in place. The caller is responsible for persisting
    the mutated state and the returned events afterward."""
    events: list = [{"type": "turn_start", "turn_number": battle.turn_number}]
    for action in (action_a, action_b):
        if action.kind != "move":
            continue
        side = battle.side(action.side)
        if side.mega_forms:
            events.extend(mega_evolve(battle, action.side))
        elif action.mega:
            chosen = next((m for m in mega_options(side) if m.get("id") == action.mega), None)
            if chosen:
                events.extend(mega_evolve(battle, action.side, chosen))
    for s in ("A", "B"):
        battle.side(s).active.volatile["unnerved"] = ability_key(battle.other(s).active) == "unnerve"
    order = _order_actions(battle, action_a, action_b, events)
    for position, (side_id, _) in enumerate(order):
        battle.side(side_id).active.volatile["moving_last"] = position == 1

    battle_over = False
    winner = None

    for side_id, action in order:
        if battle_over:
            break
        side = battle.side(side_id)
        opp = battle.other(side_id)
        if side.active.is_fainted:
            continue

        if action.kind == "switch" and action.switch_to_index is not None:
            events.extend(do_switch(side, action.switch_to_index, battle))
            continue

        action_events = _execute_move_action(battle, side, opp, action)
        events.extend(action_events)
        winner = apply_faint_and_check_winner(battle)
        if winner == "draw" and any(e["type"] == "self_ko" and e["side"] == side_id for e in action_events):
            winner = opp.side_id  # the user of a self-KO move loses a double knockout
        if winner:
            battle_over = True
            events.append({"type": "battle_end", "winner_side": winner, "reason": "all_fainted"})
            break

    if not battle_over:
        weather = current_weather(battle)
        for side_id, _ in order:
            side = battle.side(side_id)
            if weather and not side.active.is_fainted:
                events.extend(_apply_end_of_turn_weather(battle, side.active, side_id, weather))
            if side.active.is_fainted:
                continue
            events.extend(_apply_end_of_turn_status(side.active, side_id))
            events.extend(_apply_end_of_turn_item(side.active, side_id))
            events.extend(apply_end_of_turn_ability(battle, side_id))
        if battle.weather:
            battle.weather_turns -= 1
            if battle.weather_turns <= 0:
                events.append({"type": "weather_end", "weather": battle.weather})
                battle.weather = None
                battle.weather_turns = 0
            else:
                events.append({"type": "weather_tick", "weather": battle.weather, "turns_left": battle.weather_turns})
        winner = apply_faint_and_check_winner(battle)
        if winner:
            battle_over = True
            events.append({"type": "battle_end", "winner_side": winner, "reason": "all_fainted"})

    side_a_needs_switch = (not battle_over and battle.side_a.active.is_fainted
                            and any(not b.is_fainted for b in battle.side_a.roster))
    side_b_needs_switch = (not battle_over and battle.side_b.active.is_fainted
                            and any(not b.is_fainted for b in battle.side_b.roster))

    if battle_over:
        battle.status = "finished"
        battle.winner_side = winner
        battle.forced_switch_sides = []
    elif side_a_needs_switch or side_b_needs_switch:
        battle.status = "awaiting_forced_switch"
        battle.forced_switch_sides = [s for s, need in (("A", side_a_needs_switch), ("B", side_b_needs_switch)) if need]
    else:
        battle.status = "active"
        battle.forced_switch_sides = []

    battle.turn_number += 1

    return TurnResult(
        events=events, side_a_needs_switch=side_a_needs_switch, side_b_needs_switch=side_b_needs_switch,
        battle_over=battle_over, winner_side=winner,
    )
