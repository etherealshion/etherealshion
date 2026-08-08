"""
cogs/admin.py
-------------
Two commands:
  /leaderboard   - public. Top writers (by published count) and
                   top editors (by purchase count).
  /admin-stats   - owner-only. Total revenue, active writers/editors,
                   and the written -> published -> sold funnel.

Owner check: we compare interaction.user.id against OWNER_ID, which
you set in .env to your own Discord user ID. This is simpler and more
explicit for a single-owner bot than relying on Discord's app-team
"owner" concept, and it's easy to see exactly who's authorized just by
reading .env.
"""

import os

import discord
from discord import app_commands
from discord.ext import commands

import database

OWNER_ID = int(os.getenv("OWNER_ID", "0"))


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Top writers and top editors")
    async def leaderboard(self, interaction: discord.Interaction):
        top_writers = await database.get_top_writers(limit=5)
        top_editors = await database.get_top_editors(limit=5)

        embed = discord.Embed(title="🏆 Cliply Leaderboard", color=discord.Color.gold())

        if top_writers:
            lines = [
                f"**{rank}.** {w['display_name']} — {w['ideas_published']} published"
                for rank, w in enumerate(top_writers, start=1)
            ]
            embed.add_field(name="✍️ Top Writers", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="✍️ Top Writers", value="No published ideas yet.", inline=False)

        if top_editors:
            lines = [
                f"**{rank}.** {e['display_name']} — {e['ideas_purchased']} purchased"
                for rank, e in enumerate(top_editors, start=1)
            ]
            embed.add_field(name="🛒 Top Editors", value="\n".join(lines), inline=False)
        else:
            embed.add_field(name="🛒 Top Editors", value="No purchases yet.", inline=False)

        # Not ephemeral on purpose - a leaderboard is meant to be seen by
        # everyone. Switch to ephemeral=True later if it feels too noisy.
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="admin-stats", description="Owner-only: full marketplace stats")
    async def admin_stats(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        stats = await database.get_admin_stats()

        embed = discord.Embed(title="📊 Cliply Admin Stats", color=discord.Color.dark_gold())
        embed.add_field(name="Total Revenue", value=f"${stats['total_revenue']}", inline=True)
        embed.add_field(name="Active Writers", value=str(stats["active_writers"]), inline=True)
        embed.add_field(name="Active Editors", value=str(stats["active_editors"]), inline=True)
        embed.add_field(
            name="Funnel (Written → Published → Sold)",
            value=f"{stats['total_written']} → {stats['total_published']} → {stats['total_sold']}",
            inline=False,
        )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.admin') runs."""
    await bot.add_cog(AdminCog(bot))