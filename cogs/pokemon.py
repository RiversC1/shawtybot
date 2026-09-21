import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import sqlite3
import json
import os
import random
import logging
import asyncio
import secrets
from datetime import datetime, timedelta, timezone

log = logging.getLogger("bot")

DB_PATH = "pokemon.db"
DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "pokemon.json")

# National dex order, one per evolution line's base stage
STARTERS = [
    "Bulbasaur", "Charmander", "Squirtle",
    "Chikorita", "Cyndaquil", "Totodile",
    "Treecko", "Torchic", "Mudkip",
    "Chimchar", "Turtwig", "Piplup",
]

# Main protagonist characters, one male/female pair per generation 1-4, for the
# web profile's trainer customization. Sprites are official game art (Bulbagarden
# Archives), used only for the web trainer-card feature, not sold/traded in-bot.
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

# ---------- XP / Level ----------

XP_PER_CATCH = 10
XP_SHINY_BONUS = 50
XP_LEGENDARY_BONUS = 30
XP_PER_EVOLUTION = 20
XP_PER_STARTER = 10
XP_PER_COFFER = {"silver": 5, "golden": 10, "diamond": 15}


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


# ---------- Permanent Achievements ----------
# 5 categories x 5 tiers = 25 total. Each tier grants its rewards to the bag
# automatically, exactly once, the moment its threshold is crossed.

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
}

TOTAL_ACHIEVEMENT_TIERS = sum(len(cat["tiers"]) for cat in ACHIEVEMENTS.values())

# Base catch rates before rarity/summoner adjustments. Master Ball always succeeds.
BALLS = {
    "pokeball": {"label": "Poké Ball", "catch_rate": 0.10},
    "greatball": {"label": "Great Ball", "catch_rate": 0.20},
    "ultraball": {"label": "Ultra Ball", "catch_rate": 0.35},
    "masterball": {"label": "Master Ball", "catch_rate": 1.00},
}

BALL_SPRITES = {
    "pokeball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/poke-ball.png",
    "greatball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/great-ball.png",
    "ultraball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/ultra-ball.png",
    "masterball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/master-ball.png",
}

COIN_EMOJI = "🪙"
CANDY_EMOJI = "🍬"
REWARD_FALLBACK_EMOJIS = {"candy": CANDY_EMOJI, "coin": COIN_EMOJI}

# Evolution stones — inventory-only for now, will be consumed once the evolution
# mechanic exists for the web app.
STORE_ITEMS = {
    "pokeball": {"label": "Poké Ball", "price": 5},
    "greatball": {"label": "Great Ball", "price": 15},
    "ultraball": {"label": "Ultra Ball", "price": 35},
    "masterball": {"label": "Master Ball", "price": 300},
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

ITEM_LABELS = {key: v["label"] for key, v in BALLS.items()}
ITEM_LABELS["candy"] = "Rare Candy"
ITEM_LABELS["coin"] = "Poké Coin"
ITEM_LABELS.update({key: cfg["label"] for key, cfg in STORE_ITEMS.items()})

TYPE_EMOJIS = {
    "normal": "⚪", "fire": "🔥", "water": "💧", "grass": "🌿", "electric": "⚡",
    "ice": "❄️", "fighting": "🥊", "poison": "☠️", "ground": "🌍", "flying": "🕊️",
    "psychic": "🔮", "bug": "🐛", "rock": "🪨", "ghost": "👻", "dragon": "🐉",
    "dark": "🌑", "steel": "⚙️", "fairy": "✨",
}

SPAWN_LIMIT = 10
SPAWN_WINDOW = timedelta(hours=5)
SPAWN_FLEE_AFTER = timedelta(minutes=10)
RANDOM_SPAWN_INTERVAL_SECONDS = 10 * 60  # 10 minutes

# Legendary/mythical species get this spawn weight vs. 1.0 for everything else,
# so they show up far less often (roughly ~1% of spawns instead of ~7%).
LEGENDARY_SPAWN_WEIGHT = 0.15
# Non-master balls are multiplied by this against legendary/mythical Pokémon,
# so a handful of Poké Balls won't realistically land one — Master Ball is by
# far the reliable way to catch one.
LEGENDARY_PENALTY = 0.08
# The trainer who summoned the spawn (via /poke spawn-daily) gets a slight edge.
SUMMONER_BONUS = 1.3
# Odds that any given spawn is shiny — intentionally very rare.
SHINY_CHANCE = 1 / 200
# Flat family-candy cost to evolve any Pokémon — a handful of catches, not a grind.
EVOLUTION_CANDY_COST = 20
# /poke web one-time login link validity.
WEB_TOKEN_EXPIRE_MINUTES = 10

# ---------- Coffers ----------

COFFER_EXPIRE_SECONDS = 15 * 60  # unclaimed coffers vanish after 15 minutes

COFFERS = {
    "silver": {
        "label": "Silver Coffer",
        "color": discord.Color.light_grey(),
        "image": "https://archives.bulbagarden.net/media/upload/a/a6/SugimoriPokeBall.png",
        "interval_seconds": 15 * 60,
        "rewards": {"pokeball": (3, 6), "candy": (1, 2), "coin": (5, 10)},
        "masterball_chance": 0.0,
    },
    "golden": {
        "label": "Golden Coffer",
        "color": discord.Color.gold(),
        "image": "https://archives.bulbagarden.net/media/upload/0/06/SugimoriGreatBall.png",
        "interval_seconds": 30 * 60,
        "rewards": {"pokeball": (5, 10), "greatball": (2, 4), "ultraball": (1, 2), "candy": (2, 4), "coin": (15, 25)},
        "masterball_chance": 0.0,
    },
    "diamond": {
        "label": "Diamond Coffer",
        "color": discord.Color.blue(),
        "image": "https://archives.bulbagarden.net/media/upload/2/26/SugimoriUltraBall.png",
        "interval_seconds": 60 * 60,
        "rewards": {"pokeball": (8, 15), "ultraball": (2, 4), "candy": (3, 6), "coin": (30, 50)},
        "masterball_chance": 0.15,
    },
}


def load_pokedex() -> dict[int, dict]:
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {p["id"]: p for p in data}


def find_by_name(pokedex: dict[int, dict], name: str) -> dict | None:
    name = name.lower()
    return next((p for p in pokedex.values() if p["name"].lower() == name), None)


def format_types(types: list[str]) -> str:
    return " / ".join(f"{TYPE_EMOJIS.get(t, '')} {t.capitalize()}".strip() for t in types)


def format_abilities(abilities: list[dict]) -> str:
    if not abilities:
        return "Unknown"
    parts = []
    for a in abilities:
        name = a["name"].replace("-", " ").title()
        if a.get("is_hidden"):
            name += " (Hidden Ability)"
        parts.append(name)
    return ", ".join(parts)


def is_rare(mon: dict) -> bool:
    return bool(mon.get("is_legendary") or mon.get("is_mythical"))


def format_evolution_line(mon: dict, pokedex: dict[int, dict]) -> str | None:
    """Returns a display string for what this Pokémon evolves into, or None if it doesn't."""
    options = mon.get("evolves_to") or []
    if not options:
        return None
    parts = []
    for opt in options:
        target = pokedex.get(opt["id"])
        if not target:
            continue
        if opt.get("item"):
            parts.append(f"{target['name']} ({ITEM_LABELS.get(opt['item'], opt['item'].replace('-', ' ').title())})")
        else:
            parts.append(target["name"])
    return ", ".join(parts) if parts else None


def roll_spawn_mon(pokedex: dict[int, dict]) -> dict:
    """Pick a random species (legendaries/mythicals are much rarer) and roll
    whether this particular spawn is shiny. Returns a shallow copy so the
    shared pokedex entries are never mutated."""
    population = list(pokedex.values())
    weights = [LEGENDARY_SPAWN_WEIGHT if is_rare(p) else 1.0 for p in population]
    base = random.choices(population, weights=weights, k=1)[0]
    mon = dict(base)
    mon["is_shiny"] = random.random() < SHINY_CHANCE
    return mon


def spawn_title_prefix(mon: dict) -> str:
    return "✨ " if mon.get("is_shiny") else ""


def spawn_image_url(mon: dict) -> str | None:
    if mon.get("is_shiny"):
        return mon.get("artwork_shiny") or mon.get("sprite_shiny") or mon.get("artwork") or mon.get("sprite")
    return mon.get("artwork") or mon.get("sprite")


def roll_coffer_rewards(coffer_key: str) -> dict[str, int]:
    config = COFFERS[coffer_key]
    rewards = {item: random.randint(lo, hi) for item, (lo, hi) in config["rewards"].items()}
    if config["masterball_chance"] > 0 and random.random() < config["masterball_chance"]:
        rewards["masterball"] = rewards.get("masterball", 0) + 1
    return rewards


def build_coffer_embed(coffer_key: str, expires_at: datetime) -> discord.Embed:
    cfg = COFFERS[coffer_key]
    embed = discord.Embed(
        title=f"A {cfg['label']} appeared!",
        description=(
            "Click the button below to claim the coffer before it despawns "
            f"<t:{int(expires_at.timestamp())}:R>."
        ),
        color=cfg["color"],
    )
    embed.set_image(url=cfg["image"])
    return embed


def build_coffer_reward_embed(cog: "Pokemon", coffer_key: str, rewards: dict[str, int]) -> discord.Embed:
    lines = []
    for item, qty in rewards.items():
        emoji = cog.ball_emojis.get(item)
        emoji_str = str(emoji) if emoji else REWARD_FALLBACK_EMOJIS.get(item, "•")
        label = ITEM_LABELS.get(item, item.title())
        lines.append(f"{emoji_str} **{qty}** {label}")

    embed = discord.Embed(
        title="Supplies received!",
        description="\n".join(lines),
        color=COFFERS[coffer_key]["color"],
    )
    return embed


def build_catch_panel_embed(cog: "Pokemon", mon: dict, items: dict[str, int], catcher_id: int,
                             spawner_id: int | None, expires_at: datetime, result_line: str | None = None) -> discord.Embed:
    rarity = "⭐ Legendary" if is_rare(mon) else "Standard"
    owned = cog.count_owned(catcher_id, mon["id"])
    collection_line = (
        f"✨ You already have this species (x{owned})" if owned
        else "✨ You don't have this species in your collection yet."
    )

    embed = discord.Embed(
        title=f"Catch {spawn_title_prefix(mon)}{mon['name']}",
        description=(
            "Choose a ball. Each valid throw consumes one, even if it misses.\n"
            f"Flees <t:{int(expires_at.timestamp())}:R>."
        ),
        color=discord.Color.red(),
    )
    embed.add_field(name="Rarity", value=rarity, inline=True)
    embed.add_field(name="Your Collection", value=collection_line, inline=False)
    embed.add_field(
        name="Your Balls",
        value="\n".join(f"{BALLS[k]['label']}: **{items.get(k, 0)}**" for k in BALLS),
        inline=False,
    )
    if spawner_id:
        note = (
            "You can catch it. As the summoner, you have a better chance of catching it than other trainers."
            if catcher_id == spawner_id
            else "This was summoned by another trainer, who has a slight catch advantage over you."
        )
        embed.add_field(name="Summoned Appearance", value=note, inline=False)
    if result_line:
        embed.add_field(name="Result", value=result_line, inline=False)
    embed.set_thumbnail(url=mon.get("sprite") or mon.get("artwork"))
    return embed


class CatchPanelView(discord.ui.View):
    def __init__(self, cog: "Pokemon", spawn_view: "SpawnView", catcher_id: int):
        super().__init__(timeout=300)
        self.cog = cog
        self.spawn_view = spawn_view
        self.catcher_id = catcher_id
        self._build_buttons()

    def _build_buttons(self):
        self.clear_items()
        items = self.cog.get_items(self.catcher_id)
        locked = self.spawn_view.caught or self.spawn_view.fled
        for key in BALLS:
            qty = items.get(key, 0)
            btn = discord.ui.Button(
                label=f"{BALLS[key]['label']} ({qty})",
                style=discord.ButtonStyle.secondary,
                disabled=(qty <= 0 or locked),
                emoji=self.cog.ball_emojis.get(key),
                row=0,
            )
            btn.callback = self._throw_callback(key)
            self.add_item(btn)

        refresh = discord.ui.Button(label="Refresh", style=discord.ButtonStyle.grey, emoji="🔄", row=1)
        refresh.callback = self._refresh
        self.add_item(refresh)

    def _throw_callback(self, ball_key: str):
        async def callback(interaction: discord.Interaction):
            await self._throw(interaction, ball_key)
        return callback

    async def _refresh(self, interaction: discord.Interaction):
        items = self.cog.get_items(self.catcher_id)
        self._build_buttons()
        embed = build_catch_panel_embed(
            self.cog, self.spawn_view.mon, items, self.catcher_id,
            self.spawn_view.spawner_id, self.spawn_view.expires_at,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def _throw(self, interaction: discord.Interaction, ball_key: str):
        if self.spawn_view.caught:
            await interaction.response.edit_message(
                embed=discord.Embed(description="Someone already caught this Pokémon!", color=discord.Color.dark_grey()),
                view=None,
            )
            return
        if self.spawn_view.fled:
            await interaction.response.edit_message(
                embed=discord.Embed(description="This Pokémon already fled!", color=discord.Color.dark_grey()),
                view=None,
            )
            return

        mon = self.spawn_view.mon
        self.cog.add_item(self.catcher_id, ball_key, -1)
        is_summoner = self.spawn_view.spawner_id == self.catcher_id
        rate = self.cog.compute_catch_rate(ball_key, mon, is_summoner)
        success = random.random() < rate

        if success:
            self.spawn_view.caught = True
            self.cog.add_to_collection(self.catcher_id, mon["id"], is_shiny=mon.get("is_shiny", False))
            family_id = mon.get("family_id", mon["id"])
            family_name = self.cog.pokedex.get(family_id, mon)["name"]
            candy_qty = random.randint(2, 5)
            self.cog.add_item(self.catcher_id, f"famcandy_{family_id}", candy_qty)

            xp_gain = XP_PER_CATCH
            if mon.get("is_shiny"):
                xp_gain += XP_SHINY_BONUS
            if is_rare(mon):
                xp_gain += XP_LEGENDARY_BONUS
            self.cog.add_xp(self.catcher_id, xp_gain)
            self.cog.check_achievements(self.catcher_id)

            await self.spawn_view.mark_caught(
                interaction.user.display_name, interaction.user.mention, BALLS[ball_key]["label"]
            )
            result = (
                f"🎉 Gotcha! **{mon['name']}** was caught with a {BALLS[ball_key]['label']}!\n"
                f"You also got **{candy_qty}x {family_name} Candy** and **{xp_gain} XP**."
            )
        else:
            result = f"The {mon['name']} broke free from the {BALLS[ball_key]['label']}!"

        items = self.cog.get_items(self.catcher_id)
        self._build_buttons()
        embed = build_catch_panel_embed(
            self.cog, mon, items, self.catcher_id, self.spawn_view.spawner_id,
            self.spawn_view.expires_at, result_line=result,
        )
        await interaction.response.edit_message(embed=embed, view=None if success else self)


class SpawnView(discord.ui.View):
    def __init__(self, cog: "Pokemon", mon: dict, spawner_id: int | None = None):
        super().__init__(timeout=SPAWN_FLEE_AFTER.total_seconds())
        self.cog = cog
        self.mon = mon
        self.spawner_id = spawner_id
        self.caught = False
        self.fled = False
        self.message: discord.Message | None = None
        self.expires_at = datetime.now(timezone.utc) + SPAWN_FLEE_AFTER

    async def on_timeout(self):
        if self.caught:
            return
        self.fled = True
        for child in self.children:
            child.disabled = True
        if self.message:
            embed = self.message.embeds[0]
            embed.title = f"The wild {spawn_title_prefix(self.mon)}{self.mon['name']} fled!"
            embed.color = discord.Color.dark_grey()
            try:
                await self.message.edit(embed=embed, view=self)
                self.cog.untrack_spawn(self.message.id)
            except discord.HTTPException as e:
                log.error(f"Failed to mark spawn as fled: {e}")

    async def mark_caught(self, catcher_name: str, catcher_mention: str, ball_label: str):
        self.caught = True
        for child in self.children:
            child.disabled = True
        if self.message:
            embed = self.message.embeds[0]
            embed.title = f"{spawn_title_prefix(self.mon)}{self.mon['name']} was caught!"
            embed.color = discord.Color.blurple()
            embed.set_footer(text=f"Caught by {catcher_name}")
            try:
                await self.message.edit(embed=embed, view=self)
                self.cog.untrack_spawn(self.message.id)
                await self.message.channel.send(
                    f"{catcher_mention} caught **{self.mon['name']}** with a {ball_label}!"
                )
            except discord.HTTPException as e:
                log.error(f"Failed to update caught spawn message: {e}")

    @discord.ui.button(label="Catch!", style=discord.ButtonStyle.success, emoji="🎯")
    async def catch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.caught:
            await interaction.response.send_message("This Pokémon has already been caught!", ephemeral=True)
            return
        if self.fled:
            await interaction.response.send_message("This Pokémon already fled!", ephemeral=True)
            return

        if not self.cog.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        items = self.cog.get_items(interaction.user.id)
        if not any(qty > 0 for qty in items.values()):
            await interaction.response.send_message(
                "You're out of balls! Catch more Pokémon to keep going.", ephemeral=True
            )
            return

        panel = CatchPanelView(self.cog, self, interaction.user.id)
        embed = build_catch_panel_embed(self.cog, self.mon, items, interaction.user.id, self.spawner_id, self.expires_at)
        await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)


class CofferView(discord.ui.View):
    def __init__(self, cog: "Pokemon", coffer_key: str):
        super().__init__(timeout=COFFER_EXPIRE_SECONDS)
        self.cog = cog
        self.coffer_key = coffer_key
        self.claimed_by: set[int] = set()
        self.message: discord.Message | None = None
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=COFFER_EXPIRE_SECONDS)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            embed = self.message.embeds[0]
            embed.title = f"The {COFFERS[self.coffer_key]['label']} vanished!"
            embed.color = discord.Color.dark_grey()
            try:
                await self.message.edit(embed=embed, view=self)
                self.cog.untrack_coffer(self.message.id)
            except discord.HTTPException as e:
                log.error(f"Failed to mark coffer as vanished: {e}")

    @discord.ui.button(label="Claim Coffer", style=discord.ButtonStyle.success, emoji="🗝️")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id in self.claimed_by:
            await interaction.response.send_message("You've already claimed this coffer!", ephemeral=True)
            return

        if not self.cog.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        self.claimed_by.add(interaction.user.id)
        rewards = roll_coffer_rewards(self.coffer_key)
        for item, qty in rewards.items():
            self.cog.add_item(interaction.user.id, item, qty)
        self.cog.add_xp(interaction.user.id, XP_PER_COFFER[self.coffer_key])
        self.cog.check_achievements(interaction.user.id)

        if self.message:
            embed = self.message.embeds[0]
            embed.set_footer(text=f"Claimed by {len(self.claimed_by)} trainer(s) so far")
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException as e:
                log.error(f"Failed to update coffer claim count: {e}")

        reward_embed = build_coffer_reward_embed(self.cog, self.coffer_key, rewards)
        await interaction.response.send_message(embed=reward_embed, ephemeral=True)


class StarterSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(placeholder="Pick your starter...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "StarterSelectView" = self.view
        await view.handle_choice(interaction, self.values[0])


class StarterSelectView(discord.ui.View):
    def __init__(self, cog: "Pokemon", user_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        options = [discord.SelectOption(label=name, value=name) for name in STARTERS]
        self.add_item(StarterSelect(options))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your starter selection!", ephemeral=True)
            return False
        return True

    async def handle_choice(self, interaction: discord.Interaction, name: str):
        if self.cog.get_trainer(self.user_id):
            await interaction.response.edit_message(content="You already have a starter Pokémon!", embed=None, view=None)
            return

        mon = find_by_name(self.cog.pokedex, name)
        self.cog.create_trainer(self.user_id, mon["id"])
        self.cog.add_item(self.user_id, "pokeball", 10)
        self.cog.add_to_collection(self.user_id, mon["id"])
        self.cog.add_xp(self.user_id, XP_PER_STARTER)
        self.cog.set_character(self.user_id, DEFAULT_CHARACTER)
        self.cog.set_favorite(self.user_id, mon["id"])

        embed = discord.Embed(
            title=f"You chose {mon['name']}!",
            description="You received **10 Poké Balls**. Good luck on your journey, trainer!",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=mon["artwork"])
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=None, embed=embed, view=self)


class QuantityModal(discord.ui.Modal):
    def __init__(self, cog: "Pokemon", user_id: int, item_key: str):
        super().__init__(title=f"Buy {STORE_ITEMS[item_key]['label']}")
        self.cog = cog
        self.user_id = user_id
        self.item_key = item_key
        self.quantity = discord.ui.TextInput(
            label="Quantity", placeholder="1", default="1", max_length=3, required=True
        )
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            qty = int(self.quantity.value)
        except ValueError:
            await interaction.response.send_message("Please enter a valid whole number.", ephemeral=True)
            return
        if qty <= 0:
            await interaction.response.send_message("Quantity must be at least 1.", ephemeral=True)
            return

        item_cfg = STORE_ITEMS[self.item_key]
        total_cost = item_cfg["price"] * qty
        balance = self.cog.get_coins(self.user_id)

        if balance < total_cost:
            await interaction.response.send_message(
                f"You need **{total_cost}** {COIN_EMOJI} for {qty}x {item_cfg['label']}, "
                f"but you only have **{balance}**.",
                ephemeral=True,
            )
            return

        self.cog.add_item(self.user_id, "coin", -total_cost)
        self.cog.add_item(self.user_id, self.item_key, qty)

        await interaction.response.send_message(
            f"✅ Bought **{qty}x {item_cfg['label']}** for **{total_cost}** {COIN_EMOJI}. "
            f"You have **{balance - total_cost}** {COIN_EMOJI} left.",
            ephemeral=True,
        )


class StoreSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=f"{cfg['label']} — {cfg['price']} coins", value=key)
            for key, cfg in STORE_ITEMS.items()
        ]
        super().__init__(placeholder="Choose an item to buy...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "StoreView" = self.view
        await interaction.response.send_modal(QuantityModal(view.cog, view.user_id, self.values[0]))


class StoreView(discord.ui.View):
    def __init__(self, cog: "Pokemon", user_id: int):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.add_item(StoreSelect())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your store session!", ephemeral=True)
            return False
        return True


class EvolutionChoiceSelect(discord.ui.Select):
    def __init__(self, options_data: list[dict]):
        options = [
            discord.SelectOption(label=o["target"]["name"], value=str(o["target"]["id"]))
            for o in options_data
        ]
        super().__init__(placeholder="Choose which evolution...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "EvolutionChoiceView" = self.view
        target_id = int(self.values[0])
        target = next(o["target"] for o in view.options_data if o["target"]["id"] == target_id)
        await interaction.response.edit_message(content="Evolving...", view=None)
        await view.cog.perform_evolution(interaction, view.from_mon, target, view.candy_key)


class EvolutionChoiceView(discord.ui.View):
    def __init__(self, cog: "Pokemon", user_id: int, from_mon: dict, options_data: list[dict], candy_key: str):
        super().__init__(timeout=60)
        self.cog = cog
        self.user_id = user_id
        self.from_mon = from_mon
        self.options_data = options_data
        self.candy_key = candy_key
        self.add_item(EvolutionChoiceSelect(options_data))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your evolution choice!", ephemeral=True)
            return False
        return True


class TeamSelect(discord.ui.Select):
    def __init__(self, owned_species: list[tuple[int, str]], current_team: list[int]):
        options = [
            discord.SelectOption(
                label=f"#{dex_id:03} {name}",
                value=str(dex_id),
                default=(dex_id in current_team),
            )
            for dex_id, name in owned_species
        ]
        super().__init__(
            placeholder="Choose up to 6 Pokémon for your team...",
            min_values=0,
            max_values=min(6, len(options)),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        view: "TeamView" = self.view
        dex_ids = [int(v) for v in self.values]
        view.cog.set_team(view.user_id, dex_ids)
        names = [view.cog.pokedex[d]["name"] for d in dex_ids] if dex_ids else ["(empty)"]
        await interaction.response.edit_message(
            content=f"✅ Your team is now: {', '.join(names)}", embed=None, view=None
        )


class TeamView(discord.ui.View):
    def __init__(self, cog: "Pokemon", user_id: int, owned_species: list[tuple[int, str]], current_team: list[int]):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.add_item(TeamSelect(owned_species, current_team))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("This isn't your team!", ephemeral=True)
            return False
        return True


class Pokemon(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pokedex = load_pokedex()
        self.ball_emojis: dict[str, discord.Emoji] = {}
        self._init_db()
        self.tasks = [
            self.bot.loop.create_task(self._startup()),
            self.bot.loop.create_task(self._sweep_loop()),
        ]
        for coffer_key, cfg in COFFERS.items():
            self.tasks.append(
                self.bot.loop.create_task(self._coffer_loop(coffer_key, cfg["interval_seconds"]))
            )

    def cog_unload(self):
        for task in self.tasks:
            task.cancel()

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_trainers (
                    user_id INTEGER PRIMARY KEY,
                    starter_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_items (
                    user_id INTEGER,
                    item TEXT,
                    qty INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, item)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_collection (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    dex_id INTEGER NOT NULL,
                    caught_at TEXT NOT NULL,
                    is_shiny INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_spawn_usage (
                    user_id INTEGER PRIMARY KEY,
                    count INTEGER NOT NULL DEFAULT 0,
                    window_start TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER
                )
            """)
            # Tracks currently-live spawn/coffer cards so a bot restart doesn't leave
            # a stale, still-clickable button behind — see _sweep_expired().
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_active_spawns (
                    message_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    dex_id INTEGER NOT NULL,
                    is_shiny INTEGER NOT NULL DEFAULT 0,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_active_coffers (
                    message_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL,
                    coffer_key TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_team (
                    user_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    dex_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, slot)
                )
            """)
            # One-time login tokens for /poke web — exchanged by the web API for a session.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_web_tokens (
                    token TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL
                )
            """)
            # Migration for DBs created before is_shiny existed
            cols = [r[1] for r in conn.execute("PRAGMA table_info(poke_collection)").fetchall()]
            if "is_shiny" not in cols:
                conn.execute("ALTER TABLE poke_collection ADD COLUMN is_shiny INTEGER NOT NULL DEFAULT 0")
            # Migration for DBs created before web profile caching existed
            cols = [r[1] for r in conn.execute("PRAGMA table_info(poke_trainers)").fetchall()]
            if "username" not in cols:
                conn.execute("ALTER TABLE poke_trainers ADD COLUMN username TEXT")
            if "avatar_url" not in cols:
                conn.execute("ALTER TABLE poke_trainers ADD COLUMN avatar_url TEXT")
            # Migration for DBs created before the trainer card feature existed
            if "character" not in cols:
                conn.execute("ALTER TABLE poke_trainers ADD COLUMN character TEXT")
            if "favorite_dex_id" not in cols:
                conn.execute("ALTER TABLE poke_trainers ADD COLUMN favorite_dex_id INTEGER")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS poke_achievements (
                    user_id INTEGER NOT NULL,
                    achievement_key TEXT NOT NULL,
                    unlocked_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, achievement_key)
                )
            """)

    # ---------- DB helpers ----------

    def get_trainer(self, user_id: int) -> sqlite3.Row | None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute("SELECT * FROM poke_trainers WHERE user_id = ?", (user_id,)).fetchone()

    def create_trainer(self, user_id: int, starter_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_trainers (user_id, starter_id, created_at) VALUES (?, ?, ?)",
                (user_id, starter_id, datetime.now(timezone.utc).isoformat()),
            )

    def get_items(self, user_id: int) -> dict[str, int]:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("SELECT item, qty FROM poke_items WHERE user_id = ?", (user_id,)).fetchall()
        owned = {item: qty for item, qty in rows}
        return {key: owned.get(key, 0) for key in BALLS}

    def get_candy(self, user_id: int) -> int:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'candy'", (user_id,)
            ).fetchone()
        return row[0] if row else 0

    def get_coins(self, user_id: int) -> int:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT qty FROM poke_items WHERE user_id = ? AND item = 'coin'", (user_id,)
            ).fetchone()
        return row[0] if row else 0

    def get_item_qty(self, user_id: int, item_key: str) -> int:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT qty FROM poke_items WHERE user_id = ? AND item = ?", (user_id, item_key)
            ).fetchone()
        return row[0] if row else 0

    def get_family_candies(self, user_id: int) -> list[tuple[int, int]]:
        """Returns [(family_dex_id, qty), ...] for family candies the user owns (qty > 0)."""
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT item, qty FROM poke_items WHERE user_id = ? AND item LIKE 'famcandy_%' AND qty > 0",
                (user_id,),
            ).fetchall()
        result = []
        for item, qty in rows:
            try:
                family_id = int(item.split("_", 1)[1])
            except (IndexError, ValueError):
                continue
            result.append((family_id, qty))
        return result

    def evolve_one(self, user_id: int, from_dex_id: int, to_dex_id: int) -> bool:
        """Converts one caught instance of from_dex_id into to_dex_id, preserving shininess.
        Returns False if the user doesn't own one."""
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id, is_shiny FROM poke_collection WHERE user_id = ? AND dex_id = ? ORDER BY id LIMIT 1",
                (user_id, from_dex_id),
            ).fetchone()
            if not row:
                return False
            row_id, is_shiny = row
            conn.execute("DELETE FROM poke_collection WHERE id = ?", (row_id,))
            conn.execute(
                "INSERT INTO poke_collection (user_id, dex_id, caught_at, is_shiny) VALUES (?, ?, ?, ?)",
                (user_id, to_dex_id, datetime.now(timezone.utc).isoformat(), is_shiny),
            )
        return True

    def get_team(self, user_id: int) -> list[int]:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT dex_id FROM poke_team WHERE user_id = ? ORDER BY slot", (user_id,)
            ).fetchall()
        return [r[0] for r in rows]

    def set_team(self, user_id: int, dex_ids: list[int]):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM poke_team WHERE user_id = ?", (user_id,))
            for slot, dex_id in enumerate(dex_ids[:6]):
                conn.execute(
                    "INSERT INTO poke_team (user_id, slot, dex_id) VALUES (?, ?, ?)",
                    (user_id, slot, dex_id),
                )

    def create_web_token(self, user_id: int, username: str, avatar_url: str | None) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=WEB_TOKEN_EXPIRE_MINUTES)
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_web_tokens (token, user_id, expires_at) VALUES (?, ?, ?)",
                (token, user_id, expires_at.isoformat()),
            )
            conn.execute(
                "UPDATE poke_trainers SET username = ?, avatar_url = ? WHERE user_id = ?",
                (username, avatar_url, user_id),
            )
        return token

    def get_xp(self, user_id: int) -> int:
        return self.get_item_qty(user_id, "xp")

    def add_xp(self, user_id: int, amount: int):
        self.add_item(user_id, "xp", amount)

    def set_character(self, user_id: int, character: str) -> bool:
        if character not in TRAINER_CHARACTERS:
            return False
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE poke_trainers SET character = ? WHERE user_id = ?", (character, user_id))
        return True

    def set_favorite(self, user_id: int, dex_id: int) -> bool:
        if self.count_owned(user_id, dex_id) < 1:
            return False
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE poke_trainers SET favorite_dex_id = ? WHERE user_id = ?", (dex_id, user_id))
        return True

    def clear_favorite(self, user_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("UPDATE poke_trainers SET favorite_dex_id = NULL WHERE user_id = ?", (user_id,))

    def get_collection_stats(self, user_id: int) -> dict:
        with sqlite3.connect(DB_PATH) as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM poke_collection WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
            unique = conn.execute(
                "SELECT COUNT(DISTINCT dex_id) FROM poke_collection WHERE user_id = ?", (user_id,)
            ).fetchone()[0]
        return {
            "total_caught": total,
            "unique_species": unique,
            "dex_total": len(self.pokedex),
            "dex_percent": round(unique / len(self.pokedex) * 100, 1) if self.pokedex else 0,
        }

    def get_shiny_count(self, user_id: int) -> int:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND is_shiny = 1", (user_id,)
            ).fetchone()
        return row[0] if row else 0

    def get_evolution_count(self, user_id: int) -> int:
        return self.get_item_qty(user_id, "stat_evolutions")

    def get_achievement_stats(self, user_id: int) -> dict:
        stats = self.get_collection_stats(user_id)
        level, _, _ = compute_level(self.get_xp(user_id))
        return {
            "total_caught": stats["total_caught"],
            "unique_species": stats["unique_species"],
            "shiny_count": self.get_shiny_count(user_id),
            "evolution_count": self.get_evolution_count(user_id),
            "level": level,
        }

    def get_unlocked_achievements(self, user_id: int) -> set[str]:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT achievement_key FROM poke_achievements WHERE user_id = ?", (user_id,)
            ).fetchall()
        return {r[0] for r in rows}

    def check_achievements(self, user_id: int) -> list[dict]:
        """Compares current stats against every achievement tier, unlocks any
        newly-earned ones (granting rewards exactly once), and returns them."""
        stats = self.get_achievement_stats(user_id)
        unlocked = self.get_unlocked_achievements(user_id)
        newly_unlocked = []

        for cat_key, cat in ACHIEVEMENTS.items():
            stat_value = stats[cat["stat"]]
            for tier_key in ACHIEVEMENT_TIERS:
                achievement_key = f"{cat_key}_{tier_key}"
                if achievement_key in unlocked:
                    continue
                tier = cat["tiers"][tier_key]
                if stat_value >= tier["threshold"]:
                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute(
                            "INSERT INTO poke_achievements (user_id, achievement_key, unlocked_at) VALUES (?, ?, ?)",
                            (user_id, achievement_key, datetime.now(timezone.utc).isoformat()),
                        )
                    for item, qty in tier["rewards"].items():
                        self.add_item(user_id, item, qty)
                    newly_unlocked.append({
                        "category": cat_key, "category_label": cat["label"],
                        "tier": tier_key, "rewards": tier["rewards"],
                    })

        return newly_unlocked

    def add_item(self, user_id: int, item: str, delta: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_items (user_id, item, qty) VALUES (?, ?, MAX(?, 0)) "
                "ON CONFLICT(user_id, item) DO UPDATE SET qty = MAX(qty + ?, 0)",
                (user_id, item, delta, delta),
            )

    def add_to_collection(self, user_id: int, dex_id: int, is_shiny: bool = False):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_collection (user_id, dex_id, caught_at, is_shiny) VALUES (?, ?, ?, ?)",
                (user_id, dex_id, datetime.now(timezone.utc).isoformat(), int(is_shiny)),
            )

    def count_owned(self, user_id: int, dex_id: int) -> int:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM poke_collection WHERE user_id = ? AND dex_id = ?",
                (user_id, dex_id),
            ).fetchone()
        return row[0] if row else 0

    def compute_catch_rate(self, ball_key: str, mon: dict, is_summoner: bool) -> float:
        if ball_key == "masterball":
            return 1.0
        rate = BALLS[ball_key]["catch_rate"]
        if is_rare(mon):
            rate *= LEGENDARY_PENALTY
        if is_summoner:
            rate *= SUMMONER_BONUS
        return min(rate, 1.0)

    def check_and_use_spawn(self, user_id: int) -> tuple[bool, int, datetime | None]:
        """Returns (allowed, remaining_after_use, reset_time_if_blocked)."""
        now = datetime.now(timezone.utc)
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT count, window_start FROM poke_spawn_usage WHERE user_id = ?", (user_id,)
            ).fetchone()

            if row is None or now - datetime.fromisoformat(row[1]) > SPAWN_WINDOW:
                conn.execute(
                    "INSERT INTO poke_spawn_usage (user_id, count, window_start) VALUES (?, 1, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET count = 1, window_start = excluded.window_start",
                    (user_id, now.isoformat()),
                )
                return True, SPAWN_LIMIT - 1, None

            count, window_start = row
            if count >= SPAWN_LIMIT:
                reset_at = datetime.fromisoformat(window_start) + SPAWN_WINDOW
                return False, 0, reset_at

            conn.execute("UPDATE poke_spawn_usage SET count = count + 1 WHERE user_id = ?", (user_id,))
            return True, SPAWN_LIMIT - count - 1, None

    def get_collection_summary(self, user_id: int) -> list[tuple[int, int, int]]:
        """Returns [(dex_id, count, has_shiny), ...] sorted by dex_id."""
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT dex_id, COUNT(*), MAX(is_shiny) FROM poke_collection "
                "WHERE user_id = ? GROUP BY dex_id ORDER BY dex_id",
                (user_id,),
            ).fetchall()
        return rows

    # ---------- Active spawn/coffer tracking (for restart recovery) ----------

    def track_spawn(self, message_id: int, channel_id: int, dex_id: int, is_shiny: bool, expires_at: datetime):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO poke_active_spawns "
                "(message_id, channel_id, dex_id, is_shiny, expires_at) VALUES (?, ?, ?, ?, ?)",
                (message_id, channel_id, dex_id, int(is_shiny), expires_at.isoformat()),
            )

    def untrack_spawn(self, message_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM poke_active_spawns WHERE message_id = ?", (message_id,))

    def track_coffer(self, message_id: int, channel_id: int, coffer_key: str, expires_at: datetime):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO poke_active_coffers "
                "(message_id, channel_id, coffer_key, expires_at) VALUES (?, ?, ?, ?)",
                (message_id, channel_id, coffer_key, expires_at.isoformat()),
            )

    def untrack_coffer(self, message_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM poke_active_coffers WHERE message_id = ?", (message_id,))

    # ---------- Emoji setup ----------

    async def _ensure_ball_emojis(self):
        guild_id = os.getenv("GUILD_ID")
        if not guild_id:
            log.warning("GUILD_ID not set — skipping ball emoji setup, buttons will show text only.")
            return
        guild = self.bot.get_guild(int(guild_id))
        if not guild:
            log.warning("Could not find guild for ball emoji setup.")
            return

        existing = {e.name: e for e in guild.emojis}
        async with aiohttp.ClientSession() as session:
            for key, url in BALL_SPRITES.items():
                if key in existing:
                    self.ball_emojis[key] = existing[key]
                    continue
                try:
                    async with session.get(url) as resp:
                        image_bytes = await resp.read()
                    emoji = await guild.create_custom_emoji(name=key, image=image_bytes)
                    self.ball_emojis[key] = emoji
                    log.info(f"Created ball emoji :{key}:")
                except discord.HTTPException as e:
                    log.warning(f"Could not create emoji '{key}' (check Manage Emojis permission): {e}")

    # ---------- Embeds ----------

    def build_spawn_embed(self, mon: dict, spawned_by: str | None, expires_at: datetime) -> discord.Embed:
        rare = is_rare(mon)
        shiny = mon.get("is_shiny", False)
        color = discord.Color.magenta() if shiny else (discord.Color.gold() if rare else discord.Color.green())
        description = "Spawned naturally 🍃" if spawned_by is None else f"Spawned by {spawned_by}"
        embed = discord.Embed(
            title=f"A wild {spawn_title_prefix(mon)}{mon['name']} appeared!",
            description=description,
            color=color,
        )
        embed.add_field(name="Pokédex #", value=f"#{mon['id']:03}", inline=True)
        embed.add_field(name="Type", value=format_types(mon["types"]), inline=True)
        embed.add_field(name="Category", value=mon["category"], inline=True)
        embed.add_field(name="Rarity", value="⭐ Legendary" if rare else "Standard", inline=True)
        embed.add_field(name="Abilities", value=format_abilities(mon.get("abilities", [])), inline=True)
        embed.add_field(name="Flees", value=f"<t:{int(expires_at.timestamp())}:R>", inline=True)
        evolution_line = format_evolution_line(mon, self.pokedex)
        if evolution_line:
            embed.add_field(name="Evolves Into", value=evolution_line, inline=True)
        embed.set_image(url=spawn_image_url(mon))
        if shiny:
            embed.add_field(name="Shiny!", value="✨ This is an extremely rare shiny Pokémon!", inline=False)
        return embed

    # ---------- Background tasks ----------

    async def _startup(self):
        await self.bot.wait_until_ready()
        await self._ensure_ball_emojis()
        try:
            await self._sweep_expired()
        except Exception as e:
            log.error(f"Startup sweep failed: {e}", exc_info=True)
        while not self.bot.is_closed():
            await asyncio.sleep(RANDOM_SPAWN_INTERVAL_SECONDS)
            try:
                await self._do_random_spawns()
            except Exception as e:
                log.error(f"Random Pokémon spawn failed: {e}", exc_info=True)

    async def _coffer_loop(self, coffer_key: str, interval_seconds: int):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(interval_seconds)
            try:
                await self._spawn_coffer(coffer_key)
            except Exception as e:
                log.error(f"Coffer spawn failed ({coffer_key}): {e}", exc_info=True)

    async def _sweep_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            await asyncio.sleep(60)
            try:
                await self._sweep_expired()
            except Exception as e:
                log.error(f"Sweep failed: {e}", exc_info=True)

    async def _sweep_expired(self):
        """Catches up on any spawn/coffer whose flee/expiry timer elapsed while the
        bot was offline (e.g. a redeploy) — discord.py's View.on_timeout only fires
        for views still alive in memory, so a restart otherwise leaves the button
        looking clickable forever."""
        now = datetime.now(timezone.utc)

        with sqlite3.connect(DB_PATH) as conn:
            spawn_rows = conn.execute(
                "SELECT message_id, channel_id, dex_id, is_shiny, expires_at FROM poke_active_spawns"
            ).fetchall()
            coffer_rows = conn.execute(
                "SELECT message_id, channel_id, coffer_key, expires_at FROM poke_active_coffers"
            ).fetchall()

        for message_id, channel_id, dex_id, is_shiny, expires_at_str in spawn_rows:
            if now < datetime.fromisoformat(expires_at_str):
                continue
            mon = dict(self.pokedex.get(dex_id, {"name": "Pokémon"}))
            mon["is_shiny"] = bool(is_shiny)
            await self._resolve_stale_spawn(channel_id, message_id, mon)
            self.untrack_spawn(message_id)

        for message_id, channel_id, coffer_key, expires_at_str in coffer_rows:
            if now < datetime.fromisoformat(expires_at_str):
                continue
            await self._resolve_stale_coffer(channel_id, message_id, coffer_key)
            self.untrack_coffer(message_id)

    async def _resolve_stale_spawn(self, channel_id: int, message_id: int, mon: dict):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return
        if not msg.embeds:
            return
        embed = msg.embeds[0]
        embed.title = f"The wild {spawn_title_prefix(mon)}{mon.get('name', 'Pokémon')} fled!"
        embed.color = discord.Color.dark_grey()
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Catch!", style=discord.ButtonStyle.secondary, disabled=True, emoji="🎯"))
        try:
            await msg.edit(embed=embed, view=view)
            log.info(f"Swept stale spawn {message_id} ({mon.get('name')})")
        except discord.HTTPException as e:
            log.error(f"Failed to sweep stale spawn {message_id}: {e}")

    async def _resolve_stale_coffer(self, channel_id: int, message_id: int, coffer_key: str):
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        try:
            msg = await channel.fetch_message(message_id)
        except discord.HTTPException:
            return
        if not msg.embeds:
            return
        embed = msg.embeds[0]
        embed.title = f"The {COFFERS[coffer_key]['label']} vanished!"
        embed.color = discord.Color.dark_grey()
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Claim Coffer", style=discord.ButtonStyle.secondary, disabled=True, emoji="🗝️"))
        try:
            await msg.edit(embed=embed, view=view)
            log.info(f"Swept stale coffer {message_id} ({coffer_key})")
        except discord.HTTPException as e:
            log.error(f"Failed to sweep stale coffer {message_id}: {e}")

    def _spawn_channels(self) -> list[int]:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT channel_id FROM poke_settings WHERE channel_id IS NOT NULL"
            ).fetchall()
        return [r[0] for r in rows]

    async def _do_random_spawns(self):
        for channel_id in self._spawn_channels():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            mon = roll_spawn_mon(self.pokedex)
            view = SpawnView(self, mon, spawner_id=None)
            embed = self.build_spawn_embed(mon, spawned_by=None, expires_at=view.expires_at)
            try:
                msg = await channel.send(embed=embed, view=view)
                view.message = msg
                self.track_spawn(msg.id, msg.channel.id, mon["id"], mon["is_shiny"], view.expires_at)
                log.info(f"Random spawn: {mon['name']}{' (shiny)' if mon['is_shiny'] else ''} in #{channel}")
            except discord.HTTPException as e:
                log.error(f"Failed to spawn Pokémon in channel {channel_id}: {e}")

    async def _spawn_coffer(self, coffer_key: str):
        for channel_id in self._spawn_channels():
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            view = CofferView(self, coffer_key)
            embed = build_coffer_embed(coffer_key, view.expires_at)
            try:
                msg = await channel.send(embed=embed, view=view)
                view.message = msg
                self.track_coffer(msg.id, msg.channel.id, coffer_key, view.expires_at)
                log.info(f"Spawned {coffer_key} coffer in #{channel}")
            except discord.HTTPException as e:
                log.error(f"Failed to spawn {coffer_key} coffer in channel {channel_id}: {e}")

    async def perform_evolution(self, interaction: discord.Interaction, from_mon: dict, to_mon: dict, candy_key: str):
        self.add_item(interaction.user.id, candy_key, -EVOLUTION_CANDY_COST)
        self.evolve_one(interaction.user.id, from_mon["id"], to_mon["id"])
        self.add_xp(interaction.user.id, XP_PER_EVOLUTION)
        self.add_item(interaction.user.id, "stat_evolutions", 1)
        self.check_achievements(interaction.user.id)

        embed = discord.Embed(
            title=f"{from_mon['name']} evolved into {to_mon['name']}!",
            color=discord.Color.gold(),
        )
        embed.set_image(url=to_mon.get("artwork") or to_mon.get("sprite"))
        embed.set_footer(text=f"{interaction.user.display_name}'s Pokémon")

        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

    async def evolve_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        current = (current or "").lower()
        summary = self.get_collection_summary(interaction.user.id)
        choices = []
        for dex_id, count, _ in summary:
            mon = self.pokedex.get(dex_id)
            if not mon or not mon.get("evolves_to"):
                continue
            if current and current not in mon["name"].lower():
                continue
            choices.append(app_commands.Choice(name=f"{mon['name']} (x{count})", value=mon["name"]))
        return choices[:25]

    # ---------- Commands ----------

    poke = app_commands.Group(name="poke", description="Pokémon commands")

    @poke.command(name="start", description="Choose your starter Pokémon and begin your journey")
    async def poke_start(self, interaction: discord.Interaction):
        if self.get_trainer(interaction.user.id):
            await interaction.response.send_message("You already have a starter Pokémon!", ephemeral=True)
            return

        view = StarterSelectView(self, interaction.user.id)
        await interaction.response.send_message("Choose your starter Pokémon!", view=view, ephemeral=True)

    @poke.command(name="spawn-daily", description="Spawn a wild Pokémon (up to 10 times every 5 hours)")
    async def poke_spawn_daily(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        allowed, remaining, reset_at = self.check_and_use_spawn(interaction.user.id)
        if not allowed:
            ts = int(reset_at.timestamp())
            await interaction.response.send_message(
                f"You've used all your spawns for now! Next one available <t:{ts}:R>.", ephemeral=True
            )
            return

        mon = roll_spawn_mon(self.pokedex)
        view = SpawnView(self, mon, spawner_id=interaction.user.id)
        embed = self.build_spawn_embed(mon, spawned_by=interaction.user.mention, expires_at=view.expires_at)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        self.track_spawn(view.message.id, view.message.channel.id, mon["id"], mon["is_shiny"], view.expires_at)
        await interaction.followup.send(f"({remaining} spawns left in this 5-hour window)", ephemeral=True)

    @poke.command(name="setchannel", description="Set the channel for random Pokémon and coffer spawns")
    @app_commands.describe(channel="The channel for random spawns")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def poke_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_settings (guild_id, channel_id) VALUES (?, ?) "
                "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
                (interaction.guild_id, channel.id),
            )
        await interaction.response.send_message(
            f"Wild Pokémon and coffers will now randomly spawn in {channel.mention}.", ephemeral=True
        )

    @poke.command(name="inventory", description="See how many balls and candies you have")
    async def poke_inventory(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return
        items = self.get_items(interaction.user.id)
        lines = [f"**{BALLS[key]['label']}**: {qty}" for key, qty in items.items()]
        lines.append(f"**Rare Candy**: {self.get_candy(interaction.user.id)}")
        lines.append(f"**Poké Coins**: {self.get_coins(interaction.user.id)} {COIN_EMOJI}")

        family_candies = self.get_family_candies(interaction.user.id)
        if family_candies:
            lines.append("")
            lines.append("**Evolution Candy:**")
            for family_id, qty in family_candies:
                name = self.pokedex.get(family_id, {}).get("name", f"#{family_id}")
                lines.append(f"{name} Candy: {qty}")

        embed = discord.Embed(title="Your Bag", description="\n".join(lines), color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @poke.command(name="store", description="Spend your Poké Coins on balls and evolution stones")
    async def poke_store(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        balance = self.get_coins(interaction.user.id)
        lines = [f"{cfg['label']}: **{cfg['price']}** {COIN_EMOJI}" for cfg in STORE_ITEMS.values()]
        embed = discord.Embed(
            title="Poké Mart",
            description="\n".join(lines),
            color=discord.Color.teal(),
        )
        embed.set_footer(text=f"Your balance: {balance} Poké Coins — pick an item below")
        view = StoreView(self, interaction.user.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @poke.command(name="pokedex", description="See the Pokémon you've caught")
    async def poke_pokedex(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        summary = self.get_collection_summary(interaction.user.id)
        if not summary:
            await interaction.response.send_message("You haven't caught any Pokémon yet!", ephemeral=True)
            return

        lines = [
            f"#{dex_id:03} **{self.pokedex[dex_id]['name']}**{' ✨' if has_shiny else ''} x{count}"
            for dex_id, count, has_shiny in summary[:25]
        ]
        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Pokédex",
            description="\n".join(lines),
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"{len(summary)} unique species caught")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @poke.command(name="evolve", description="Evolve one of your caught Pokémon using family candy")
    @app_commands.describe(pokemon="The Pokémon species you want to evolve")
    @app_commands.autocomplete(pokemon=evolve_autocomplete)
    async def poke_evolve(self, interaction: discord.Interaction, pokemon: str):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        mon = find_by_name(self.pokedex, pokemon)
        if not mon:
            await interaction.response.send_message("Couldn't find that Pokémon.", ephemeral=True)
            return

        if self.count_owned(interaction.user.id, mon["id"]) < 1:
            await interaction.response.send_message(f"You don't have a {mon['name']} to evolve.", ephemeral=True)
            return

        options = mon.get("evolves_to") or []
        if not options:
            await interaction.response.send_message(f"**{mon['name']}** doesn't evolve any further.", ephemeral=True)
            return

        family_id = mon.get("family_id", mon["id"])
        family_name = self.pokedex.get(family_id, mon)["name"]
        candy_key = f"famcandy_{family_id}"
        have_candy = self.get_item_qty(interaction.user.id, candy_key)

        candidates = []
        for opt in options:
            target = self.pokedex.get(opt["id"])
            if not target:
                continue
            item_needed = opt.get("item")
            has_item = item_needed is None or self.get_item_qty(interaction.user.id, item_needed) > 0
            candidates.append({"target": target, "item": item_needed, "has_item": has_item})

        if have_candy < EVOLUTION_CANDY_COST:
            options_text = ", ".join(
                c["target"]["name"] + (f" (needs {ITEM_LABELS.get(c['item'], c['item'])})" if c["item"] else "")
                for c in candidates
            )
            await interaction.response.send_message(
                f"**{mon['name']}** can evolve into: {options_text}\n"
                f"You need **{EVOLUTION_CANDY_COST}** {family_name} Candy — you have **{have_candy}**.",
                ephemeral=True,
            )
            return

        ready = [c for c in candidates if c["has_item"]]
        if not ready:
            missing_items = ", ".join(ITEM_LABELS.get(c["item"], c["item"]) for c in candidates if c["item"])
            await interaction.response.send_message(
                f"You have enough candy, but evolving **{mon['name']}** needs one of: {missing_items}. "
                f"Buy one from `/poke store`!",
                ephemeral=True,
            )
            return

        if len(ready) == 1:
            await self.perform_evolution(interaction, mon, ready[0]["target"], candy_key)
            return

        names = ", ".join(c["target"]["name"] for c in ready)
        view = EvolutionChoiceView(self, interaction.user.id, mon, ready, candy_key)
        await interaction.response.send_message(
            f"**{mon['name']}** can evolve into multiple forms: {names}. Pick one:",
            view=view,
            ephemeral=True,
        )

    @poke.command(name="team", description="Set your team of up to 6 Pokémon")
    async def poke_team(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        summary = self.get_collection_summary(interaction.user.id)
        if not summary:
            await interaction.response.send_message("You haven't caught any Pokémon yet!", ephemeral=True)
            return

        owned_species = [(dex_id, self.pokedex[dex_id]["name"]) for dex_id, _, _ in summary][:25]
        current_team = self.get_team(interaction.user.id)

        team_names = [self.pokedex[d]["name"] for d in current_team if d in self.pokedex]
        desc = f"Current team: {', '.join(team_names)}" if team_names else "Your team is empty."
        if len(summary) > 25:
            desc += "\n(Only your first 25 species are shown here for now.)"

        embed = discord.Embed(title="Your Pokémon Team", description=desc, color=discord.Color.purple())
        view = TeamView(self, interaction.user.id, owned_species, current_team)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @poke.command(name="web", description="Get a link to view your profile, Pokédex, team, and inventory on the web")
    async def poke_web(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need to pick your starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        token = self.create_web_token(
            interaction.user.id, interaction.user.name, interaction.user.display_avatar.url
        )
        base_url = os.getenv("WEB_BASE_URL", "http://localhost:8080")
        link = f"{base_url}/login?token={token}"

        embed = discord.Embed(
            title="Your Pokémon Web Profile",
            description=(
                f"[Click here to open your profile]({link})\n\n"
                f"This link expires in **{WEB_TOKEN_EXPIRE_MINUTES} minutes** and can only be used once."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Pokemon(bot))
