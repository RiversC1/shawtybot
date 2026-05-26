import discord
from discord.ext import commands


class Fun(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
