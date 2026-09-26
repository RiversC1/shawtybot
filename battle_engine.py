"""
Pure-Python Pokémon battle engine — zero discord.py/FastAPI imports, so both
the bot (cogs/pokemon.py, which drives real turns) and, if ever needed, the
web API can import it safely.

Scope (see the approved battle-system plan): an accurate damage formula,
types/STAB/effectiveness, accuracy rolls, critical hits, priority + speed
turn order, stat-stage changes, and the main status conditions (burn,
paralysis, poison, toxic, sleep, freeze, confusion, flinch). Abilities/held
items/weather are NOT simulated in v1 — ability is carried as flavor text
only. Every battler is effectively level 100 (see webapi.py's
stat_at_level_100 — an owned Pokémon's stats are already fixed at that
level's value), which is why the damage formula below folds the level term
into a constant instead of taking a level parameter.

Singles format only: one active Pokémon per side, up to 6 per team.
"""
from __future__ import annotations

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


def stat_at_level_100(base: int, iv: int, is_hp: bool) -> int:
    """Mirrors webapi.py's stat_at_level_100 — no EVs, neutral nature, level
    100, real per-individual IV (0-31). Duplicated here (not imported) so
    this module stays free of any dependency on webapi.py's FastAPI app
    construction."""
    return 2 * base + iv + 110 if is_hp else 2 * base + iv + 5


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
    moves: list = field(default_factory=list)  # list[MoveSlot], up to 4
    stat_stages: dict = field(default_factory=default_stat_stages)
    status: Optional[str] = None  # burn|paralysis|poison|toxic|sleep|freeze
    status_counter: int = 0
    confusion_counter: int = 0
    volatile: dict = field(default_factory=default_volatile)
    is_fainted: bool = False


@dataclass
class BattleSide:
    side_id: str  # "A" | "B"
    controller: object  # Discord user id (int) or "npc"
    roster: list  # list[BattlerState], up to 6
    active_index: int = 0

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
                         ivs: dict | None = None) -> BattlerState:
    """mon: one entry from data/pokemon.json (POKEDEX[dex_id]). moves: up to 4
    move names to load from that species' own embedded move pool (falls back
    to its first 4 known moves if not given/found). ivs: that individual's
    real 0-31-per-stat values; defaults to max (31) for gym/trainer NPCs,
    which have no owned Pokémon row to draw real IVs from."""
    ivs = ivs or MAX_IVS
    base_stats = mon.get("base_stats", {})
    stats = {
        "hp": stat_at_level_100(base_stats.get("hp", 1), ivs.get("hp", 31), True),
        "attack": stat_at_level_100(base_stats.get("attack", 1), ivs.get("attack", 31), False),
        "defense": stat_at_level_100(base_stats.get("defense", 1), ivs.get("defense", 31), False),
        "sp_attack": stat_at_level_100(base_stats.get("sp_attack", 1), ivs.get("sp_attack", 31), False),
        "sp_defense": stat_at_level_100(base_stats.get("sp_defense", 1), ivs.get("sp_defense", 31), False),
        "speed": stat_at_level_100(base_stats.get("speed", 1), ivs.get("speed", 31), False),
    }
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
        ivs=dict(ivs), moves=move_slots,
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
    }


def battler_state_from_row(mon: dict, row: dict, ability: str | None = None) -> BattlerState:
    """Rebuild a BattlerState for one roster slot from a poke_battle_sides row
    (already JSON-decoded) plus the species' pokedex entry. IVs are written
    once at battle start and never change, so they're just read back here."""
    ivs = row.get("ivs") or MAX_IVS
    base_stats = mon.get("base_stats", {})
    stats = {
        "hp": stat_at_level_100(base_stats.get("hp", 1), ivs.get("hp", 31), True),
        "attack": stat_at_level_100(base_stats.get("attack", 1), ivs.get("attack", 31), False),
        "defense": stat_at_level_100(base_stats.get("defense", 1), ivs.get("defense", 31), False),
        "sp_attack": stat_at_level_100(base_stats.get("sp_attack", 1), ivs.get("sp_attack", 31), False),
        "sp_defense": stat_at_level_100(base_stats.get("sp_defense", 1), ivs.get("sp_defense", 31), False),
        "speed": stat_at_level_100(base_stats.get("speed", 1), ivs.get("speed", 31), False),
    }
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

    return BattlerState(
        dex_id=row["dex_id"], species_name=mon.get("name", f"#{row['dex_id']}"), types=list(mon.get("types", [])),
        stats=stats, ivs=dict(ivs), max_hp=row.get("max_hp", stats["hp"]), current_hp=row.get("current_hp", stats["hp"]),
        ability=ability, moves=move_slots, stat_stages=stages,
        status=row.get("status"), status_counter=row.get("status_counter", 0) or 0,
        confusion_counter=row.get("confusion_counter", 0) or 0, volatile=volatile,
        is_fainted=bool(row.get("is_fainted")),
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
    switchable = [i for i, b in enumerate(side.roster) if not b.is_fainted and i != side.active_index]
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
    for i in la["usable_move_indices"]:
        move = active.moves[i].move
        if move.category != "status" and move.power:
            eff = type_effectiveness(move.type, opp_active.types)
            stab = 1.5 if move.type in active.types else 1.0
            score = max(move.power * eff * stab, 1)
        else:
            score = 35  # modest baseline so status/utility moves get picked sometimes
        scored.append((i, score))

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

def estimate_damage(attacker: BattlerState, defender: BattlerState, move: MoveData) -> float:
    """Deterministic average-case damage estimate (no RNG consumed) — used
    only for NPC decision-making, never for real turn resolution, so it
    never perturbs the battle's own RNG stream."""
    if move.category == "status" or not move.power:
        return 0.0
    a_key, d_key = ("attack", "defense") if move.category == "physical" else ("sp_attack", "sp_defense")
    A = attacker.stats[a_key] * stat_stage_multiplier(attacker.stat_stages[a_key])
    D = defender.stats[d_key] * stat_stage_multiplier(defender.stat_stages[d_key])
    eff = type_effectiveness(move.type, defender.types)
    if eff == 0:
        return 0.0
    base = math.floor(LEVEL_100_STAGE_BASE * move.power * A / D / 50) + 2
    stab = 1.5 if move.type and move.type in attacker.types else 1.0
    burn_mult = 0.5 if (attacker.status == "burn" and move.category == "physical") else 1.0
    return base * stab * eff * 0.925 * burn_mult  # 0.925 ~= average of the real 0.85-1.00 roll


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
    for slot in attacker.moves:
        if slot.current_pp <= 0:
            continue
        m = slot.move
        dmg = estimate_damage(attacker, defender, m)
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
    my_dmg = _best_attack(me, foe)
    foe_dmg = _best_attack(foe, me) * foe_dmg_mult
    first = i_act_first if i_act_first is not None else _effective_speed(me) > _effective_speed(foe)
    speed_first = _effective_speed(me) > _effective_speed(foe)
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
    acc = _hit_chance(move)
    if move.priority != 0:
        first = move.priority > 0
    else:
        first = _effective_speed(me) > _effective_speed(foe)
    wasted = _race(me, foe, me.current_hp, foe.current_hp, my_first_hit=0.0, i_act_first=first)

    if move.category != "status" and move.power:
        dmg = estimate_damage(me, foe, move)
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


def _value_switch(battle: BattleState, side_id: str, index: int) -> float:
    """Switching in costs the new Pokémon a free hit from the foe."""
    cand = battle.side(side_id).roster[index]
    foe = battle.other(side_id).active
    hit = _best_attack(foe, cand)
    if hit >= cand.current_hp:
        return -1.0
    return _race(cand, foe, cand.current_hp - hit, foe.current_hp)


def pick_npc_action_hard(battle: BattleState, side_id: str) -> Action:
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


def pick_npc_forced_switch_hard(battle: BattleState, side_id: str) -> int:
    la = legal_actions(battle, side_id)
    if not la["switchable_indices"]:
        raise ValueError("No switchable Pokémon left")
    side = battle.side(side_id)
    foe = battle.other(side_id).active
    # A replacement after a faint comes in without taking a hit.
    return max(la["switchable_indices"], key=lambda i: _race(side.roster[i], foe, side.roster[i].current_hp, foe.current_hp))


# ---------------------------------------------------------------------------
# Damage
# ---------------------------------------------------------------------------

LEVEL_100_STAGE_BASE = math.floor(2 * 100 / 5 + 2)  # 42, constant since every mon is level 100


def compute_damage(attacker: BattlerState, defender: BattlerState, move: MoveData,
                    is_crit: bool, rng: random.Random) -> int:
    if move.category == "status" or not move.power:
        return 0

    if move.category == "physical":
        a_key, d_key = "attack", "defense"
    else:
        a_key, d_key = "sp_attack", "sp_defense"

    a_stage = 0 if is_crit and attacker.stat_stages[a_key] < 0 else attacker.stat_stages[a_key]
    d_stage = 0 if is_crit and defender.stat_stages[d_key] > 0 else defender.stat_stages[d_key]
    A = attacker.stats[a_key] * stat_stage_multiplier(a_stage)
    D = defender.stats[d_key] * stat_stage_multiplier(d_stage)

    eff = type_effectiveness(move.type, defender.types)
    if eff == 0:
        return 0

    base = math.floor(LEVEL_100_STAGE_BASE * move.power * A / D / 50) + 2
    stab = 1.5 if move.type and move.type in attacker.types else 1.0
    crit_mult = 2.0 if is_crit else 1.0
    random_roll = rng.uniform(0.85, 1.00)
    burn_mult = 0.5 if (attacker.status == "burn" and move.category == "physical") else 1.0

    damage = math.floor(base * stab * eff * crit_mult * random_roll * burn_mult)
    return max(1, damage)


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


def crit_chance(move: MoveData) -> float:
    return CRIT_TABLE.get(min(3, max(0, move.crit_rate)), CRIT_TABLE[0])


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
    return active.moves[action.move_index].move.priority


def _effective_speed(mon: BattlerState) -> float:
    spd = mon.stats["speed"] * stat_stage_multiplier(mon.stat_stages["speed"])
    if mon.status == "paralysis":
        spd *= 0.5
    return spd


def _order_actions(battle: BattleState, action_a: Action, action_b: Action) -> list:
    entries = [("A", action_a), ("B", action_b)]
    prio_a = _action_priority(battle, "A", action_a)
    prio_b = _action_priority(battle, "B", action_b)
    if prio_a != prio_b:
        return entries if prio_a > prio_b else [entries[1], entries[0]]
    spd_a = _effective_speed(battle.side_a.active)
    spd_b = _effective_speed(battle.side_b.active)
    if spd_a != spd_b:
        return entries if spd_a > spd_b else [entries[1], entries[0]]
    return entries if battle.rng.random() < 0.5 else [entries[1], entries[0]]


def do_switch(side: BattleSide, target_index: int) -> list:
    events = []
    outgoing = side.active
    events.append({"type": "switch_out", "side": side.side_id, "dex_id": outgoing.dex_id, "name": outgoing.species_name})
    outgoing.stat_stages = default_stat_stages()
    outgoing.volatile = default_volatile()
    outgoing.confusion_counter = 0
    side.active_index = target_index
    incoming = side.active
    events.append({"type": "switch_in", "side": side.side_id, "dex_id": incoming.dex_id, "name": incoming.species_name})
    return events


def apply_forced_switch(battle: BattleState, side_id: str, team_index: int) -> list:
    side = battle.side(side_id)
    events = do_switch(side, team_index)
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
                            move_name: str, is_crit: bool, move_type: Optional[str]) -> int:
    eff = type_effectiveness(move_type, defender.types) if move_type is not None else 1.0
    amount = max(0, int(amount))
    defender.current_hp = max(0, defender.current_hp - amount)
    events.append({
        "type": "damage", "side": defender_side_id, "move_name": move_name, "amount": amount,
        "new_hp": defender.current_hp, "max_hp": defender.max_hp,
        "effectiveness": effectiveness_label(eff), "is_crit": is_crit,
    })
    if defender.current_hp <= 0 and not defender.is_fainted:
        defender.is_fainted = True
        events.append({"type": "faint", "side": defender_side_id, "dex_id": defender.dex_id, "name": defender.species_name})
    return amount


def _check_and_emit_faint(mon: BattlerState, side_id: str, events: list) -> None:
    if mon.current_hp <= 0 and not mon.is_fainted:
        mon.is_fainted = True
        events.append({"type": "faint", "side": side_id, "dex_id": mon.dex_id, "name": mon.species_name})


def _maybe_apply_ailment(battle: BattleState, move: MoveData, target: BattlerState, events: list, target_side_id: str) -> None:
    if not move.ailment:
        return
    if move.ailment == "confusion":
        if target.confusion_counter > 0:
            return
        if battle.rng.random() * 100 < move.ailment_chance:
            target.confusion_counter = battle.rng.randint(2, 5)
            events.append({"type": "status_applied", "side": target_side_id, "status": "confusion"})
        return
    if target.status is not None:
        return  # already has a major status; secondary status effects don't stack/overwrite
    if battle.rng.random() * 100 < move.ailment_chance:
        target.status = move.ailment
        if move.ailment == "sleep":
            target.status_counter = battle.rng.randint(1, 3)
        elif move.ailment == "toxic":
            target.status_counter = 1
        else:
            target.status_counter = 0
        events.append({"type": "status_applied", "side": target_side_id, "status": move.ailment})


def _maybe_apply_stat_changes(battle: BattleState, move: MoveData, target: BattlerState, events: list, target_side_id: str) -> None:
    if not move.stat_changes:
        return
    if battle.rng.random() * 100 >= move.stat_chance:
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
        old = target.stat_stages[stat]
        new = max(-6, min(6, old + change))
        target.stat_stages[stat] = new
        if new == old:
            events.append({"type": "stat_change_fizzled", "side": target_side_id, "stat": stat})
        else:
            events.append({"type": "stat_changed", "side": target_side_id, "stat": stat, "change": new - old})


def _maybe_apply_flinch(battle: BattleState, move: MoveData, defender: BattlerState) -> None:
    if move.flinch_chance and battle.rng.random() * 100 < move.flinch_chance:
        defender.volatile["flinched"] = True


def _apply_end_of_turn_status(mon: BattlerState, side_id: str) -> list:
    events = []
    if mon.status in ("burn", "poison"):
        dmg = max(1, mon.max_hp // 16)
        mon.current_hp = max(0, mon.current_hp - dmg)
        events.append({"type": "status_damage", "side": side_id, "status": mon.status,
                       "amount": dmg, "new_hp": mon.current_hp, "max_hp": mon.max_hp})
        _check_and_emit_faint(mon, side_id, events)
    elif mon.status == "toxic":
        stacks = min(mon.status_counter, 15)
        dmg = max(1, (stacks * mon.max_hp) // 16)
        mon.current_hp = max(0, mon.current_hp - dmg)
        mon.status_counter += 1
        events.append({"type": "status_damage", "side": side_id, "status": "toxic",
                       "amount": dmg, "new_hp": mon.current_hp, "max_hp": mon.max_hp})
        _check_and_emit_faint(mon, side_id, events)
    return events


def _execute_move_action(battle: BattleState, side: BattleSide, opp: BattleSide, action: Action) -> list:
    events: list = []
    active = side.active
    defender = opp.active
    v = active.volatile

    if v.get("must_recharge"):
        v["must_recharge"] = False
        events.append({"type": "cannot_act", "side": side.side_id, "reason": "recharge"})
        return events

    if active.status == "sleep":
        active.status_counter -= 1
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
        move = move_slot.move

        if "charge" in move.flags:
            move_slot.current_pp = max(0, move_slot.current_pp - 1)
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

        move_slot.current_pp = max(0, move_slot.current_pp - 1)
    else:
        # Release turn of a charging move (Solar Beam etc.) — move_index was
        # already resolved above from volatile["charging_move"]; no new PP cost.
        move = active.moves[move_index].move

    events.append({"type": "move_used", "side": side.side_id, "move_name": move.name,
                   "move_type": move.type, "category": move.category, "target": move.target})

    if move.target != "self" and defender.volatile.get("invulnerable_until_turn") == battle.turn_number:
        events.append({"type": "move_missed", "side": side.side_id, "move_name": move.name,
                        "reason": "invulnerable", "target_name": defender.species_name})
        if "recharge" in move.flags:
            active.volatile["must_recharge"] = True
        return events

    if "always_hit" not in move.flags and move.accuracy is not None:
        acc_mult = stat_stage_multiplier(
            active.stat_stages["accuracy"] - defender.stat_stages["evasion"], is_accuracy_or_evasion=True
        )
        effective_acc = move.accuracy * acc_mult
        if battle.rng.random() * 100 >= effective_acc:
            events.append({"type": "move_missed", "side": side.side_id, "move_name": move.name})
            if "recharge" in move.flags:
                active.volatile["must_recharge"] = True
            return events

    dmg = 0
    total_dealt = 0
    is_crit = False

    if "ohko" in move.flags:
        # The generic accuracy check above already gated this on the move's
        # own accuracy field (PokeAPI encodes OHKO moves' ~30% hit chance
        # there directly) — reaching here means it already hit, so the only
        # remaining condition is mainline's "fails if the target is faster."
        if _effective_speed(defender) > _effective_speed(active):
            events.append({"type": "move_failed", "side": side.side_id, "move_name": move.name, "reason": "outsped"})
        else:
            total_dealt = _apply_damage_and_emit(defender, defender.current_hp, events, opp.side_id, move.name, False, move.type)
    elif "fixed_damage" in move.flags:
        amount = move.fixed_damage_amount if move.fixed_damage_amount is not None else 100
        total_dealt = _apply_damage_and_emit(defender, amount, events, opp.side_id, move.name, False, move.type)
    elif move.category == "status":
        pass
    elif "multi_hit" in move.flags:
        n = sample_multi_hit_count(move, battle.rng)
        hits = 0
        for _ in range(n):
            if defender.is_fainted:
                break
            hit_crit = battle.rng.random() < crit_chance(move)
            hit_dmg = compute_damage(active, defender, move, hit_crit, battle.rng)
            total_dealt += _apply_damage_and_emit(defender, hit_dmg, events, opp.side_id, move.name, hit_crit, move.type)
            hits += 1
        events.append({"type": "multi_hit_summary", "side": side.side_id, "hits": hits, "total_damage": total_dealt})
    else:
        is_crit = battle.rng.random() < crit_chance(move)
        dmg = compute_damage(active, defender, move, is_crit, battle.rng)
        total_dealt = _apply_damage_and_emit(defender, dmg, events, opp.side_id, move.name, is_crit, move.type)

    if move.drain_percent and total_dealt > 0:
        heal = max(1, math.floor(total_dealt * move.drain_percent / 100))
        active.current_hp = min(active.max_hp, active.current_hp + heal)
        events.append({"type": "drain", "side": side.side_id, "amount": heal,
                        "new_hp": active.current_hp, "max_hp": active.max_hp})

    if move.recoil_percent and total_dealt > 0:
        recoil = max(1, math.floor(total_dealt * move.recoil_percent / 100))
        active.current_hp = max(0, active.current_hp - recoil)
        events.append({"type": "recoil", "side": side.side_id, "amount": recoil,
                        "new_hp": active.current_hp, "max_hp": active.max_hp})
        _check_and_emit_faint(active, side.side_id, events)

    if move.healing_percent and not active.is_fainted:
        heal = max(1, math.floor(active.max_hp * move.healing_percent / 100))
        active.current_hp = min(active.max_hp, active.current_hp + heal)
        events.append({"type": "heal", "side": side.side_id, "amount": heal,
                        "new_hp": active.current_hp, "max_hp": active.max_hp})

    if move.category == "status":
        target_mon = active if move.target == "self" else defender
        target_side_id = side.side_id if move.target == "self" else opp.side_id
        _maybe_apply_ailment(battle, move, target_mon, events, target_side_id)
        _maybe_apply_stat_changes(battle, move, target_mon, events, target_side_id)
    else:
        if not defender.is_fainted:
            _maybe_apply_ailment(battle, move, defender, events, opp.side_id)
        if move.stat_self:
            if not active.is_fainted:
                _maybe_apply_stat_changes(battle, move, active, events, side.side_id)
        elif not defender.is_fainted:
            _maybe_apply_stat_changes(battle, move, defender, events, opp.side_id)
        _maybe_apply_flinch(battle, move, defender)

    if "multi_turn_lock" in move.flags:
        if not v.get("locked_move"):
            v["locked_move"] = move.name
            v["lock_turns_remaining"] = battle.rng.randint(2, 3) - 1
        else:
            v["lock_turns_remaining"] -= 1
            if v["lock_turns_remaining"] <= 0:
                v["locked_move"] = None
                if not active.is_fainted:
                    active.confusion_counter = battle.rng.randint(2, 3)
                    events.append({"type": "status_applied", "side": side.side_id, "status": "confusion", "reason": "fatigue"})

    if "recharge" in move.flags:
        active.volatile["must_recharge"] = True

    _check_and_emit_faint(active, side.side_id, events)
    return events


def resolve_turn(battle: BattleState, action_a: Action, action_b: Action) -> TurnResult:
    """Resolves one simultaneous turn (both sides already chose an Action)
    and mutates `battle` in place. The caller is responsible for persisting
    the mutated state and the returned events afterward."""
    events: list = [{"type": "turn_start", "turn_number": battle.turn_number}]
    order = _order_actions(battle, action_a, action_b)

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
            events.extend(do_switch(side, action.switch_to_index))
            continue

        events.extend(_execute_move_action(battle, side, opp, action))
        winner = apply_faint_and_check_winner(battle)
        if winner:
            battle_over = True
            events.append({"type": "battle_end", "winner_side": winner, "reason": "all_fainted"})
            break

    if not battle_over:
        for side_id, _ in order:
            side = battle.side(side_id)
            if side.active.is_fainted:
                continue
            events.extend(_apply_end_of_turn_status(side.active, side_id))
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
