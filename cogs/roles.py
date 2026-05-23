import discord
from discord.ext import commands
from discord import app_commands
import logging

log = logging.getLogger("bot")

VISITOR_ROLE = "Visitor"
FRIENDO_ROLE = "Friendo"


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        role = discord.utils.get(member.guild.roles, name=VISITOR_ROLE)
        if not role:
            log.warning(f"'{VISITOR_ROLE}' role not found in {member.guild.name}")
            return
        await member.add_roles(role)
        log.info(f"Assigned {VISITOR_ROLE} to {member} on join")

    @app_commands.command(name="approve", description="Promote a Visitor to Friendo")
    @app_commands.describe(member="The member to approve")
    @app_commands.checks.has_role(FRIENDO_ROLE)
    async def approve(self, interaction: discord.Interaction, member: discord.Member):
        log.info(f"{interaction.user} ran /approve on {member}")
        await interaction.response.defer()

        try:
            friendo = discord.utils.get(interaction.guild.roles, name=FRIENDO_ROLE)
            visitor = discord.utils.get(interaction.guild.roles, name=VISITOR_ROLE)

            if not friendo:
                await interaction.followup.send(f"'{FRIENDO_ROLE}' role not found!", ephemeral=True)
                return

            if friendo in member.roles:
                await interaction.followup.send(f"{member.mention} is already a Friendo!", ephemeral=True)
                return

            await member.add_roles(friendo)
            if visitor and visitor in member.roles:
                await member.remove_roles(visitor)

            await interaction.followup.send(f"Welcome to the group, {member.mention}! You're now a Friendo.")
            log.info(f"{member} promoted to {FRIENDO_ROLE} by {interaction.user}")

        except Exception as e:
            log.error(f"Error in /approve: {e}", exc_info=True)
            await interaction.followup.send("Something went wrong, check the logs.", ephemeral=True)

    @approve.error
    async def approve_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole):
            await interaction.response.send_message("Only Friendos can approve people.", ephemeral=True)
        else:
            log.error(f"Approve command error: {error}", exc_info=True)
            await interaction.response.send_message("Something went wrong.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
