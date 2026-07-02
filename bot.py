import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import logging
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("bot")

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    guild = discord.Object(id=int(os.getenv("GUILD_ID")))
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        log.info(f"Synced {len(synced)} slash command(s): {[c.name for c in synced]}")
    except Exception as e:
        log.error(f"Failed to sync commands: {e}")
    log.info(f"Logged in as {bot.user}")

@bot.tree.command(name="hi", description="Say hi to the bot")
async def hi(interaction: discord.Interaction):
    log.info(f"{interaction.user} ran /hi in #{interaction.channel}")
    await interaction.response.send_message(f"Hey {interaction.user.mention}! 👋")

async def main():
    async with bot:
        for cog in ["cogs.music", "cogs.roles", "cogs.fun", "cogs.birthday", "cogs.gw2"]:
            try:
                await bot.load_extension(cog)
                log.info(f"Loaded {cog}")
            except Exception as e:
                log.error(f"Failed to load {cog}: {e}", exc_info=True)
        await bot.start(os.getenv("DISCORD_TOKEN"))



asyncio.run(main())
