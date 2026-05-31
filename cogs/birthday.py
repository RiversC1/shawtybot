import discord
from discord.ext import commands, tasks
from discord import app_commands
import sqlite3
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger("bot")

CT = ZoneInfo("America/Chicago")
DB_PATH = "birthdays.db"
ROLE_NAME = "Birthday"


class Birthday(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._init_db()
        self.check_birthdays.start()

    def cog_unload(self):
        self.check_birthdays.cancel()

    def _init_db(self):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS birthdays (
                    user_id INTEGER,
                    guild_id INTEGER,
                    month INTEGER,
                    day INTEGER,
                    PRIMARY KEY (user_id, guild_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    guild_id INTEGER PRIMARY KEY,
                    channel_id INTEGER
                )
            """)

    def _get_channel_id(self, guild_id: int) -> int | None:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute("SELECT channel_id FROM settings WHERE guild_id = ?", (guild_id,)).fetchone()
            return row[0] if row else None

    def _get_birthdays_today(self, guild_id: int) -> set[int]:
        now = datetime.now(CT)
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id FROM birthdays WHERE guild_id = ? AND month = ? AND day = ?",
                (guild_id, now.month, now.day),
            ).fetchall()
        return {row[0] for row in rows}

    @tasks.loop(hours=1)
    async def check_birthdays(self):
        for guild in self.bot.guilds:
            today_ids = self._get_birthdays_today(guild.id)
            birthday_role = discord.utils.get(guild.roles, name=ROLE_NAME)

            for user_id in today_ids:
                member = guild.get_member(user_id)
                if not member:
                    continue
                if birthday_role and birthday_role in member.roles:
                    continue

                if not birthday_role:
                    birthday_role = await guild.create_role(
                        name=ROLE_NAME, color=discord.Color.gold(), reason="Birthday role auto-created"
                    )

                await member.add_roles(birthday_role, reason="It's their birthday!")
                log.info(f"Added birthday role to {member} in {guild}")

                channel_id = self._get_channel_id(guild.id)
                if channel_id:
                    channel = guild.get_channel(channel_id)
                    if channel:
                        await channel.send(
                            f"SQUEAK Happy birthday {member.mention}! Have an amazing day. Best wishes from the rivers casita ❤️"
                        )

            if birthday_role:
                for member in list(birthday_role.members):
                    if member.id not in today_ids:
                        await member.remove_roles(birthday_role, reason="Birthday is over")
                        log.info(f"Removed birthday role from {member} in {guild}")

    @check_birthdays.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    birthday = app_commands.Group(name="birthday", description="Birthday management commands")

    @birthday.command(name="set", description="Set a member's birthday")
    @app_commands.describe(user="The member", month="Birth month (1–12)", day="Birth day (1–31)")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def birthday_set(self, interaction: discord.Interaction, user: discord.Member, month: int, day: int):
        if not (1 <= month <= 12) or not (1 <= day <= 31):
            await interaction.response.send_message("Invalid date.", ephemeral=True)
            return
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO birthdays (user_id, guild_id, month, day) VALUES (?, ?, ?, ?)",
                (user.id, interaction.guild_id, month, day),
            )
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        await interaction.response.send_message(
            f"Set {user.mention}'s birthday to **{months[month-1]} {day}**.", ephemeral=True
        )

    @birthday.command(name="remove", description="Remove a member's birthday")
    @app_commands.describe(user="The member")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def birthday_remove(self, interaction: discord.Interaction, user: discord.Member):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "DELETE FROM birthdays WHERE user_id = ? AND guild_id = ?",
                (user.id, interaction.guild_id),
            )
        await interaction.response.send_message(f"Removed {user.mention}'s birthday.", ephemeral=True)

    @birthday.command(name="list", description="List all saved birthdays")
    async def birthday_list(self, interaction: discord.Interaction):
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute(
                "SELECT user_id, month, day FROM birthdays WHERE guild_id = ? ORDER BY month, day",
                (interaction.guild_id,),
            ).fetchall()
        if not rows:
            await interaction.response.send_message("No birthdays saved yet.", ephemeral=True)
            return
        months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        lines = []
        for user_id, month, day in rows:
            member = interaction.guild.get_member(user_id)
            name = member.display_name if member else f"Unknown ({user_id})"
            lines.append(f"**{name}** — {months[month-1]} {day}")
        await interaction.response.send_message("\n".join(lines))

    @birthday.command(name="setchannel", description="Set the channel for birthday announcements")
    @app_commands.describe(channel="The announcement channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def birthday_setchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO settings (guild_id, channel_id) VALUES (?, ?)",
                (interaction.guild_id, channel.id),
            )
        await interaction.response.send_message(
            f"Birthday announcements will be sent in {channel.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Birthday(bot))
