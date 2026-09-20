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

# Base catch rates before rarity/summoner adjustments. Master Ball always succeeds.
BALLS = {
    "pokeball": {"label": "Poké Ball", "catch_rate": 0.35},
    "greatball": {"label": "Great Ball", "catch_rate": 0.55},
    "ultraball": {"label": "Ultra Ball", "catch_rate": 0.75},
    "masterball": {"label": "Master Ball", "catch_rate": 1.00},
}

BALL_SPRITES = {
    "pokeball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/poke-ball.png",
    "greatball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/great-ball.png",
    "ultraball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/ultra-ball.png",
    "masterball": "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/items/master-ball.png",
}

TYPE_EMOJIS = {
    "normal": "⚪", "fire": "🔥", "water": "💧", "grass": "🌿", "electric": "⚡",
    "ice": "❄️", "fighting": "🥊", "poison": "☠️", "ground": "🌍", "flying": "🕊️",
    "psychic": "🔮", "bug": "🐛", "rock": "🪨", "ghost": "👻", "dragon": "🐉",
    "dark": "🌑", "steel": "⚙️", "fairy": "✨",
}

SPAWN_LIMIT = 10
SPAWN_WINDOW = timedelta(hours=5)
SPAWN_FLEE_AFTER = timedelta(minutes=10)
RANDOM_SPAWN_MIN_SECONDS = 60 * 60       # 1 hour
RANDOM_SPAWN_MAX_SECONDS = 3 * 60 * 60   # 3 hours

# Non-master balls are multiplied by this against legendary/mythical Pokémon,
# so a handful of Poké Balls won't realistically land one.
LEGENDARY_PENALTY = 0.15
# The trainer who summoned the spawn (via /poke spawn-daily) gets a slight edge.
SUMMONER_BONUS = 1.3


def load_pokedex() -> dict[int, dict]:
    with open(DATA_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return {p["id"]: p for p in data}


def find_by_name(pokedex: dict[int, dict], name: str) -> dict | None:
    name = name.lower()
    return next((p for p in pokedex.values() if p["name"].lower() == name), None)


def format_types(types: list[str]) -> str:
    return " / ".join(f"{TYPE_EMOJIS.get(t, '')} {t.capitalize()}".strip() for t in types)


def is_rare(mon: dict) -> bool:
    return bool(mon.get("is_legendary") or mon.get("is_mythical"))


def build_catch_panel_embed(cog: "Pokemon", mon: dict, items: dict[str, int], catcher_id: int,
                             spawner_id: int | None, expires_at: datetime, result_line: str | None = None) -> discord.Embed:
    rarity = "⭐ Legendary" if is_rare(mon) else "Standard"
    owned = cog.count_owned(catcher_id, mon["id"])
    collection_line = (
        f"✨ You already have this species (x{owned})" if owned
        else "✨ You don't have this species in your collection yet."
    )

    embed = discord.Embed(
        title=f"Catch {mon['name']}",
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
            self.cog.add_to_collection(self.catcher_id, mon["id"])
            await self.spawn_view.mark_caught(
                interaction.user.display_name, interaction.user.mention, BALLS[ball_key]["label"]
            )
            result = f"🎉 Gotcha! **{mon['name']}** was caught with a {BALLS[ball_key]['label']}!"
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
            embed.title = f"The wild {self.mon['name']} fled!"
            embed.color = discord.Color.dark_grey()
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException as e:
                log.error(f"Failed to mark spawn as fled: {e}")

    async def mark_caught(self, catcher_name: str, catcher_mention: str, ball_label: str):
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
                "You need a starter Pokémon first! Use `/poke start`.", ephemeral=True
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
        self.ball_emojis: dict[str, discord.Emoji] = {}
        self._init_db()
        self.startup_task = self.bot.loop.create_task(self._startup())

    def cog_unload(self):
        self.startup_task.cancel()

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
                "INSERT INTO poke_items (user_id, item, qty) VALUES (?, ?, MAX(?, 0)) "
                "ON CONFLICT(user_id, item) DO UPDATE SET qty = MAX(qty + ?, 0)",
                (user_id, item, delta, delta),
            )

    def add_to_collection(self, user_id: int, dex_id: int):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO poke_collection (user_id, dex_id, caught_at) VALUES (?, ?, ?)",
                (user_id, dex_id, datetime.now(timezone.utc).isoformat()),
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

    def get_collection_summary(self, user_id: int) -> list[tuple[int, int]]:
        """Returns [(dex_id, count), ...] sorted by dex_id."""
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT dex_id, COUNT(*) FROM poke_collection WHERE user_id = ? GROUP BY dex_id ORDER BY dex_id",
                (user_id,),
            ).fetchall()
        return rows

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

    def build_spawn_embed(self, mon: dict, spawned_by: str, expires_at: datetime) -> discord.Embed:
        rare = is_rare(mon)
        embed = discord.Embed(
            title=f"A wild {mon['name']} appeared!",
            description=f"Spawned by {spawned_by}",
            color=discord.Color.gold() if rare else discord.Color.green(),
        )
        embed.add_field(name="Pokédex #", value=f"#{mon['id']:03}", inline=True)
        embed.add_field(name="Type", value=format_types(mon["types"]), inline=True)
        embed.add_field(name="Category", value=mon["category"], inline=True)
        embed.add_field(name="Rarity", value="⭐ Legendary" if rare else "Standard", inline=True)
        embed.add_field(name="Flees", value=f"<t:{int(expires_at.timestamp())}:R>", inline=True)
        embed.set_image(url=mon["artwork"] or mon["sprite"])
        return embed

    # ---------- Background tasks ----------

    async def _startup(self):
        await self.bot.wait_until_ready()
        await self._ensure_ball_emojis()
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
            view = SpawnView(self, mon, spawner_id=None)
            embed = self.build_spawn_embed(mon, spawned_by=self.bot.user.mention, expires_at=view.expires_at)
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
        view = SpawnView(self, mon, spawner_id=interaction.user.id)
        embed = self.build_spawn_embed(mon, spawned_by=interaction.user.mention, expires_at=view.expires_at)
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
