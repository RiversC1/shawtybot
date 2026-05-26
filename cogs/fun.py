import discord
from discord.ext import commands


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        if "rivals" in message.content.lower():
            await message.channel.send("https://tenor.com/view/hop-on-marvel-rivals-marvel-rivals-hop-on-gif-10073567528215660129")


async def setup(bot: commands.Bot):
    await bot.add_cog(Fun(bot))
