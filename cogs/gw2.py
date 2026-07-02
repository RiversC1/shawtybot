import discord
from discord.ext import commands, tasks
from discord import app_commands
import aiohttp
import sqlite3
import logging

log = logging.getLogger("bot")
DB_PATH = "gw2.db"
GW2_API = "https://api.guildwars2.com/v2"


class GW2(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._init_db()
        self.poll_games.start()

    def cog_unload(self):
        self.poll_games.cancel()

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gw2_users (
                    user_id INTEGER PRIMARY KEY,
                    guild_id INTEGER,
                    channel_id INTEGER,
                    api_key TEXT,
                    last_game_id TEXT
                )
            """)

    async def _get_latest_game(self, session: aiohttp.ClientSession, api_key: str):
        params = {"access_token": api_key}
        async with session.get(f"{GW2_API}/pvp/games", params=params) as resp:
            if resp.status != 200:
                return None, None
            game_ids = await resp.json()

        if not game_ids:
            return None, None

        latest_id = game_ids[0]
        async with session.get(f"{GW2_API}/pvp/games/{latest_id}", params=params) as resp:
            if resp.status != 200:
                return None, latest_id
            game = await resp.json()

        return game, latest_id

    async def _get_current_rating(self, session: aiohttp.ClientSession, api_key: str) -> int | None:
        async with session.get(f"{GW2_API}/pvp/standings", params={"access_token": api_key}) as resp:
            if resp.status != 200:
                return None
            standings = await resp.json()
        if standings:
            return standings[0].get("current", {}).get("rating")
        return None

    async def _get_map_name(self, session: aiohttp.ClientSession, map_id: int) -> str:
        async with session.get(f"{GW2_API}/maps/{map_id}") as resp:
            if resp.status == 200:
                data = await resp.json()
                return data.get("name", f"Map {map_id}")
        return f"Map {map_id}"

    @tasks.loop(seconds=60)
    async def poll_games(self):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, guild_id, channel_id, api_key, last_game_id FROM gw2_users"
            ).fetchall()

        if not rows:
            return

        async with aiohttp.ClientSession() as session:
            for user_id, guild_id, channel_id, api_key, last_game_id in rows:
                try:
                    game, game_id = await self._get_latest_game(session, api_key)

                    if game_id is None:
                        continue

                    if game_id == last_game_id:
                        continue

                    with sqlite3.connect(DB_PATH) as conn:
                        conn.execute(
                            "UPDATE gw2_users SET last_game_id = ? WHERE user_id = ?",
                            (game_id, user_id),
                        )

                    # Skip notification on first poll after setup
                    if last_game_id is None:
                        continue

                    if game is None:
                        continue

                    channel = self.bot.get_channel(channel_id)
                    if not channel:
                        continue

                    user = self.bot.get_user(user_id)
                    mention = user.mention if user else f"<@{user_id}>"

                    result = game.get("result", "Unknown")
                    rating_change = game.get("rating_change", 0)
                    profession = game.get("profession", "Unknown")
                    scores = game.get("scores", {})
                    map_id = game.get("map_id")
                    rating_type = game.get("rating_type", "")

                    map_name = await self._get_map_name(session, map_id) if map_id else "Unknown"

                    is_win = result == "Win"
                    color = discord.Color.green() if is_win else discord.Color.red()
                    result_emoji = "✅" if is_win else "❌"

                    red_score = scores.get("red", "?")
                    blue_score = scores.get("blue", "?")

                    embed = discord.Embed(
                        title=f"GW2 PvP — {result_emoji} {result}",
                        color=color,
                    )
                    embed.add_field(name="Profession", value=profession, inline=True)
                    embed.add_field(name="Map", value=map_name, inline=True)
                    embed.add_field(name="Type", value=rating_type or "Unknown", inline=True)
                    embed.add_field(name="Score", value=f"🔵 {blue_score} — 🔴 {red_score}", inline=True)

                    if rating_type == "Ranked":
                        change_str = f"+{rating_change}" if rating_change > 0 else str(rating_change)
                        embed.add_field(name="Rating Change", value=change_str, inline=True)
                        current_rating = await self._get_current_rating(session, api_key)
                        if current_rating is not None:
                            embed.add_field(name="Current Rating", value=str(current_rating), inline=True)

                    await channel.send(content=mention, embed=embed)

                except Exception as e:
                    log.error(f"Error polling GW2 for user {user_id}: {e}", exc_info=True)

    @poll_games.before_loop
    async def before_poll(self):
        await self.bot.wait_until_ready()

    gw2 = app_commands.Group(name="gw2", description="Guild Wars 2 commands")

    @gw2.command(name="setup", description="Link your GW2 API key to get PvP game notifications in this channel")
    @app_commands.describe(api_key="Your GW2 API key (needs PvP permission)")
    async def gw2_setup(self, interaction: discord.Interaction, api_key: str):
        await interaction.response.defer(ephemeral=True)

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{GW2_API}/pvp/games", params={"access_token": api_key}
            ) as resp:
                if resp.status != 200:
                    await interaction.followup.send(
                        "Invalid API key or missing PvP permission. Make sure your key has **PvP** access enabled.",
                        ephemeral=True,
                    )
                    return

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO gw2_users (user_id, guild_id, channel_id, api_key, last_game_id)
                VALUES (?, ?, ?, ?, NULL)
                """,
                (interaction.user.id, interaction.guild_id, interaction.channel_id, api_key),
            )

        await interaction.followup.send(
            f"✅ Set up! I'll post your PvP results in {interaction.channel.mention} after each game.",
            ephemeral=True,
        )

    @gw2.command(name="stop", description="Stop receiving GW2 PvP game notifications")
    async def gw2_stop(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM gw2_users WHERE user_id = ?", (interaction.user.id,))
        await interaction.response.send_message("✅ GW2 PvP notifications stopped.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(GW2(bot))
