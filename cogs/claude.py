import discord
from discord.ext import commands
from discord import app_commands
import anthropic
import os
import logging

log = logging.getLogger("bot")

SYSTEM_PROMPT = (
    "You are a helpful assistant inside a Discord server. "
    "Keep your answers concise and friendly. "
    "Use plain text — no markdown headers or excessive formatting since Discord renders it differently."
)


class Claude(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    @app_commands.command(name="claude", description="Ask Claude a question")
    @app_commands.describe(prompt="Your question or prompt")
    async def claude_ask(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer()

        try:
            response = await self.client.messages.create(
                model="claude-opus-5",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )

            answer = next(
                (block.text for block in response.content if block.type == "text"), ""
            )

            # Discord message limit is 2000 chars
            if len(answer) > 1990:
                answer = answer[:1990] + "…"

            await interaction.followup.send(answer)

        except anthropic.AuthenticationError:
            log.error("Anthropic API key is invalid or missing")
            await interaction.followup.send(
                "Bot configuration error: invalid API key.", ephemeral=True
            )
        except anthropic.RateLimitError:
            await interaction.followup.send(
                "Rate limited right now, try again in a moment.", ephemeral=True
            )
        except Exception as e:
            log.error(f"Claude command error: {e}", exc_info=True)
            await interaction.followup.send(
                "Something went wrong. Try again later.", ephemeral=True
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(Claude(bot))
