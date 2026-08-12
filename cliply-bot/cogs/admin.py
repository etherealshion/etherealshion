"""
cogs/admin.py
-------------
Four commands:
  /leaderboard        - public. Top writers (by published count) and
                         top editors (by purchase count).
  /admin-stats        - owner-only. Total revenue, active writers/editors,
                         and the written -> published -> sold funnel.
  /admin-list-ideas   - owner-only. Every idea's ID/title/category/status,
                         so you can find junk/test entries to remove.
  /admin-delete-idea  - owner-only. Permanently deletes one idea by ID.

Owner check: we compare interaction.user.id against OWNER_ID, which
you set in .env to your own Discord user ID. This is simpler and more
explicit for a single-owner bot than relying on Discord's app-team
"owner" concept, and it's easy to see exactly who's authorized just by
reading .env.
"""

import discord
from discord import app_commands
from discord.ext import commands

import database
from utils import OWNER_ID


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

    @app_commands.command(name="admin-list-ideas", description="Owner-only: list all ideas with their IDs and status")
    async def admin_list_ideas(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        ideas = await database.get_all_ideas(limit=50)
        if not ideas:
            await interaction.response.send_message("No ideas in the database.", ephemeral=True)
            return

        lines = [f"#{i['idea_id']} · {i['status']:<10} · {i['category']} · {i['title']}" for i in ideas]
        text = "\n".join(lines)
        # Discord caps a single message at 2000 characters - truncate
        # defensively so this can never fail to send outright.
        if len(text) > 1900:
            text = text[:1900] + "\n... (truncated - delete a few, then run this again)"

        await interaction.response.send_message(
            f"**All ideas (newest first):**\n```\n{text}\n```",
            ephemeral=True,
        )

    @app_commands.command(name="admin-delete-idea", description="Owner-only: permanently delete an idea by ID")
    @app_commands.describe(idea_id="The idea's ID number, shown in /admin-list-ideas or on its posted embed")
    async def admin_delete_idea(self, interaction: discord.Interaction, idea_id: int):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        deleted = await database.admin_delete_idea(idea_id)
        if deleted:
            await interaction.response.send_message(
                f"🗑️ Deleted idea #{idea_id} from the database. "
                "If it was already posted in a channel, that message will still be "
                "visible, but its Buy button will now automatically refund anyone who "
                "tries to buy it, since the idea no longer exists.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"No idea found with ID #{idea_id}.", ephemeral=True)


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.admin') runs."""
    await bot.add_cog(AdminCog(bot))