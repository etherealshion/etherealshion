"""
cogs/roles.py
--------------
Self-assignable "notify me" roles, one per idea category (see
categories.py). Editors can opt in to whichever categories interest
them, and get pinged in that category's channel whenever a new idea
publishes there (see the ping added in cogs/write.py's Publish handler).

/setup-roles (owner-only) creates any missing roles and posts a panel
of toggle buttons - run it once, in whatever channel you want editors
to see it (same pattern as /setup-tickets - no channel ID needed, it
just posts wherever you run the command).
"""

import discord
from discord import app_commands
from discord.ext import commands

from categories import CATEGORY_CHANNELS
from utils import OWNER_ID


def role_name_for_category(category: str) -> str:
    """The exact role name used for a category's 'notify me' role."""
    return f"🔔 {category} Fan"


class RoleToggleButton(discord.ui.Button):
    """
    One button per category. Clicking it adds the role if the member
    doesn't already have it, or removes it if they do - a simple on/off
    toggle. We look up the role BY NAME each time (rather than storing
    its ID anywhere) so a button never goes stale - it works correctly
    even if a role gets deleted and recreated, and needs no saved state
    to survive a bot restart, since custom_id alone identifies which
    category this button belongs to.
    """

    def __init__(self, category: str):
        role_name = role_name_for_category(category)
        super().__init__(
            label=role_name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"role_toggle_{category}",
        )
        self.category = category

    async def callback(self, interaction: discord.Interaction):
        role_name = role_name_for_category(self.category)
        role = discord.utils.get(interaction.guild.roles, name=role_name)
        if role is None:
            await interaction.response.send_message(
                f"⚠️ The '{role_name}' role doesn't exist yet - ask the owner to run /setup-roles.",
                ephemeral=True,
            )
            return

        member = interaction.user  # a discord.Member here, since this only runs inside a server
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Self-removed via role panel")
                await interaction.response.send_message(f"❌ Removed {role.mention}.", ephemeral=True)
            else:
                await member.add_roles(role, reason="Self-assigned via role panel")
                await interaction.response.send_message(f"✅ Added {role.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(
                "⚠️ I don't have permission to manage that role - my own role needs to sit "
                "ABOVE it in Server Settings -> Roles, and I need the 'Manage Roles' permission.",
                ephemeral=True,
            )


class RolePanelView(discord.ui.View):
    """
    Persistent (timeout=None) - stays clickable forever, across bot
    restarts, as long as bot.py registers it once via bot.add_view()
    on startup (see bot.py's on_ready). One button per category, built
    fresh from CATEGORY_CHANNELS every time the bot starts, so adding a
    new category to categories.py automatically gets a button here too
    the next time you run /setup-roles.
    """

    def __init__(self):
        super().__init__(timeout=None)
        for category in CATEGORY_CHANNELS.keys():
            self.add_item(RoleToggleButton(category))


class RolesCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="setup-roles", description="Owner-only: create category roles and post the role panel here")
    async def setup_roles(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        # Creating several roles is a handful of Discord API calls in a
        # row - deferring first protects against this taking longer
        # than the normal 3 second response window.
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        created = []
        for category in CATEGORY_CHANNELS.keys():
            role_name = role_name_for_category(category)
            existing = discord.utils.get(guild.roles, name=role_name)
            if existing is None:
                try:
                    await guild.create_role(name=role_name, mentionable=True, reason="Cliply category role setup")
                    created.append(role_name)
                except discord.Forbidden:
                    await interaction.followup.send(
                        "⚠️ I don't have permission to create roles - I need the "
                        "'Manage Roles' permission.",
                        ephemeral=True,
                    )
                    return

        embed = discord.Embed(
            title="🔔 Get notified about new ideas",
            description=(
                "Click a button below to get pinged whenever a new idea is published "
                "in that category. Click it again to remove it."
            ),
            color=discord.Color.blurple(),
        )

        # A new, permanent message - not tied to this interaction after
        # this point. Running /setup-roles again posts a second panel,
        # so only run it once per channel you want it in (it's still
        # safe to run repeatedly for the role-creation part - it skips
        # any role that already exists).
        await interaction.channel.send(embed=embed, view=RolePanelView())

        if created:
            summary = f"✅ Role panel posted. Created {len(created)} new role(s): {', '.join(created)}."
        else:
            summary = "✅ Role panel posted. All roles already existed."
        await interaction.followup.send(summary, ephemeral=True)


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.roles') runs."""
    await bot.add_cog(RolesCog(bot))