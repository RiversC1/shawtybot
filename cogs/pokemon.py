import discord
from discord.ext import commands
from discord import app_commands
import sqlite3
import json
import os
import random
import logging
import asyncio
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

BALLS = {
    "pokeball": {"label": "Poké Ball", "catch_rate": 0.50},
    "greatball": {"label": "Great Ball", "catch_rate": 0.65},
    "ultraball": {"label": "Ultra Ball", "catch_rate": 0.80},
    "masterball": {"label": "Master Ball", "catch_rate": 1.00},
}

TYPE_EMOJIS = {
    "normal": "⚪", "fire": "🔥", "water": "💧", "grass": "🌿", "electric": "⚡",
    "ice": "❄️", "fighting": "🥊", "poison": "☠️", "ground": "🌍", "flying": "🕊️",
    "psychic": "🔮", "bug": "🐛", "rock": "🪨", "ghost": "👻", "dragon": "🐉",
    "dark": "🌑", "steel": "⚙️", "fairy": "✨",
}

SPAWN_LIMIT = 10
SPAWN_WINDOW = timedelta(hours=5)
RANDOM_SPAWN_MIN_SECONDS = 60 * 60       # 1 hour
RANDOM_SPAWN_MAX_SECONDS = 3 * 60 * 60   # 3 hours


def load_pokedex() -> dict[int, dict]:
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {p["id"]: p for p in data}


def find_by_name(pokedex: dict[int, dict], name: str) -> dict | None:
    name = name.lower()
    return next((p for p in pokedex.values() if p["name"].lower() == name), None)


def format_types(types: list[str]) -> str:
    return " / ".join(f"{TYPE_EMOJIS.get(t, '')} {t.capitalize()}".strip() for t in types)


class BallSelect(discord.ui.Select):
    def __init__(self, options: list[discord.SelectOption]):
        super().__init__(placeholder="Choose a ball to throw...", options=options)

    async def callback(self, interaction: discord.Interaction):
        view: "BallSelectView" = self.view
        await view.handle_choice(interaction, self.values[0])


class BallSelectView(discord.ui.View):
    def __init__(self, cog: "Pokemon", catcher_id: int, mon: dict, spawn_view: "SpawnView", items: dict[str, int]):
        super().__init__(timeout=60)
        self.cog = cog
        self.catcher_id = catcher_id
        self.mon = mon
        self.spawn_view = spawn_view

        options = [
            discord.SelectOption(label=f"{BALLS[key]['label']} ({qty})", value=key)
            for key, qty in items.items()
            if qty > 0 and key in BALLS
        ]
        self.add_item(BallSelect(options))

    async def handle_choice(self, interaction: discord.Interaction, ball: str):
        if self.spawn_view.caught:
            await interaction.response.edit_message(content="Someone already caught this Pokémon!", view=None)
            return

        self.cog.add_item(self.catcher_id, ball, -1)
        success = random.random() < BALLS[ball]["catch_rate"]

        if success:
            self.spawn_view.caught = True
            self.cog.add_to_collection(self.catcher_id, self.mon["id"])
            await self.spawn_view.mark_caught(interaction.user.display_name)
            await interaction.response.edit_message(
                content=f"Gotcha! **{self.mon['name']}** was caught with a {BALLS[ball]['label']}!",
                view=None,
            )
        else:
            await interaction.response.edit_message(
                content=f"Oh no! **{self.mon['name']}** broke free from the {BALLS[ball]['label']}. "
                        f"Try again if it's still up for grabs!",
                view=None,
            )


class SpawnView(discord.ui.View):
    def __init__(self, cog: "Pokemon", mon: dict):
        super().__init__(timeout=None)
        self.cog = cog
        self.mon = mon
        self.caught = False
        self.message: discord.Message | None = None

    async def mark_caught(self, catcher_name: str):
        self.caught = True
        for child in self.children:
            child.disabled = True
        if self.message:
            embed = self.message.embeds[0]
            embed.title = f"{self.mon['name']} was caught!"
            embed.color = discord.Color.blurple()
            embed.set_footer(text=f"Caught by {catcher_name}")
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException as e:
                log.error(f"Failed to update caught spawn message: {e}")

    @discord.ui.button(label="Catch!", style=discord.ButtonStyle.success, emoji="🎯")
    async def catch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.caught:
            await interaction.response.send_message("This Pokémon has already been caught!", ephemeral=True)
            return

        if not self.cog.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need a starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        items = self.cog.get_items(interaction.user.id)
        if not any(qty > 0 for qty in items.values()):
            await interaction.response.send_message(
                "You're out of balls! Catch more Pokémon to keep going.", ephemeral=True
            )
            return

        view = BallSelectView(self.cog, interaction.user.id, self.mon, self, items)
        await interaction.response.send_message("Which ball do you want to throw?", view=view, ephemeral=True)


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

        embed = discord.Embed(
            title=f"You chose {mon['name']}!",
            description="You received **10 Poké Balls**. Good luck on your journey, trainer!",
            color=discord.Color.gold(),
        )
        embed.set_thumbnail(url=mon["artwork"])
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=None, embed=embed, view=self)


class Pokemon(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.pokedex = load_pokedex()
        self._init_db()
        self.spawn_task = self.bot.loop.create_task(self._random_spawn_loop())

    def cog_unload(self):
        self.spawn_task.cancel()

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
                    caught_at TEXT NOT NULL
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

    def add_item(self, user_id: int, item: str, delta: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_items (user_id, item, qty) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, item) DO UPDATE SET qty = MAX(qty + excluded.qty, 0)",
                (user_id, item, max(delta, 0)),
            )

    def add_to_collection(self, user_id: int, dex_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_collection (user_id, dex_id, caught_at) VALUES (?, ?, ?)",
                (user_id, dex_id, datetime.now(timezone.utc).isoformat()),
            )

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

    def get_collection_summary(self, user_id: int) -> list[tuple[int, int]]:
        """Returns [(dex_id, count), ...] sorted by dex_id."""
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT dex_id, COUNT(*) FROM poke_collection WHERE user_id = ? GROUP BY dex_id ORDER BY dex_id",
                (user_id,),
            ).fetchall()
        return rows

    # ---------- Embeds ----------

    def build_spawn_embed(self, mon: dict, spawned_by: str) -> discord.Embed:
        embed = discord.Embed(
            title=f"A wild {mon['name']} appeared!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Pokédex #", value=f"#{mon['id']:03}", inline=True)
        embed.add_field(name="Type", value=format_types(mon["types"]), inline=True)
        embed.add_field(name="Category", value=mon["category"], inline=True)
        embed.set_image(url=mon["artwork"] or mon["sprite"])
        embed.set_footer(text=f"Spawned by {spawned_by}")
        return embed

    # ---------- Background random spawns ----------

    async def _random_spawn_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            wait_seconds = random.randint(RANDOM_SPAWN_MIN_SECONDS, RANDOM_SPAWN_MAX_SECONDS)
            await asyncio.sleep(wait_seconds)
            try:
                await self._do_random_spawns()
            except Exception as e:
                log.error(f"Random Pokémon spawn failed: {e}", exc_info=True)

    async def _do_random_spawns(self):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT channel_id FROM poke_settings WHERE channel_id IS NOT NULL"
            ).fetchall()

        for (channel_id,) in rows:
            channel = self.bot.get_channel(channel_id)
            if not channel:
                continue
            mon = random.choice(list(self.pokedex.values()))
            embed = self.build_spawn_embed(mon, spawned_by=self.bot.user.mention)
            view = SpawnView(self, mon)
            try:
                msg = await channel.send(embed=embed, view=view)
                view.message = msg
                log.info(f"Random spawn: {mon['name']} in #{channel}")
            except discord.HTTPException as e:
                log.error(f"Failed to spawn Pokémon in channel {channel_id}: {e}")

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
                "You need a starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        allowed, remaining, reset_at = self.check_and_use_spawn(interaction.user.id)
        if not allowed:
            ts = int(reset_at.timestamp())
            await interaction.response.send_message(
                f"You've used all your spawns for now! Next one available <t:{ts}:R>.", ephemeral=True
            )
            return

        mon = random.choice(list(self.pokedex.values()))
        embed = self.build_spawn_embed(mon, spawned_by=interaction.user.mention)
        view = SpawnView(self, mon)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()
        await interaction.followup.send(f"({remaining} spawns left in this 5-hour window)", ephemeral=True)

    @poke.command(name="setchannel", description="Set the channel where wild Pokémon spawn randomly")
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
            f"Wild Pokémon will now randomly spawn in {channel.mention}.", ephemeral=True
        )

    @poke.command(name="inventory", description="See how many balls you have")
    async def poke_inventory(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need a starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return
        items = self.get_items(interaction.user.id)
        lines = [f"**{BALLS[key]['label']}**: {qty}" for key, qty in items.items()]
        embed = discord.Embed(title="Your Bag", description="\n".join(lines), color=discord.Color.orange())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @poke.command(name="pokedex", description="See the Pokémon you've caught")
    async def poke_pokedex(self, interaction: discord.Interaction):
        if not self.get_trainer(interaction.user.id):
            await interaction.response.send_message(
                "You need a starter Pokémon first! Use `/poke start`.", ephemeral=True
            )
            return

        summary = self.get_collection_summary(interaction.user.id)
        if not summary:
            await interaction.response.send_message("You haven't caught any Pokémon yet!", ephemeral=True)
            return

        lines = [
            f"#{dex_id:03} **{self.pokedex[dex_id]['name']}** x{count}"
            for dex_id, count in summary[:25]
        ]
        embed = discord.Embed(
            title=f"{interaction.user.display_name}'s Pokédex",
            description="\n".join(lines),
            color=discord.Color.red(),
        )
        embed.set_footer(text=f"{len(summary)} unique species caught")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Pokemon(bot))
