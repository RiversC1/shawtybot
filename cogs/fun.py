import discord
from discord.ext import commands
from discord import app_commands
import aiohttp


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="kiss", description="Kiss another user!")
    @app_commands.describe(user="The user you want to kiss")
    async def kiss(self, interaction: discord.Interaction, user: discord.Member):
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.waifu.pics/sfw/kiss") as resp:
                data = await resp.json()
        gif_url = data["url"]
        embed = discord.Embed(
            description=f"**{interaction.user.display_name}** kissed **{user.display_name}**! 💋",
            color=discord.Color.pink()
        )
        embed.set_image(url=gif_url)
        await interaction.response.send_message(embed=embed)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        content = message.content.lower()
        if "rivals" in content:
            await message.channel.send("https://tenor.com/view/hop-on-marvel-rivals-marvel-rivals-hop-on-gif-10073567528215660129")
        if "estupido" in content:
            await message.channel.send("https://tenor.com/view/howard-hamlin-jimmy-mcgill-saul-goodman-better-call-saul-breaking-bad-gif-22509687")


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
