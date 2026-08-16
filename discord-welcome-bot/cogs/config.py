import discord
from discord import app_commands
from discord.ext import commands
import database

class Config(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="setwelcome", description="Set the channel for welcome messages")
    @app_commands.checks.has_permissions(administrator=True)
    async def setwelcome(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await database.set_welcome_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"✅ Welcome messages will be sent to {channel.mention}", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Config(bot))