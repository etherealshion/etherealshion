"""
cogs/support.py
----------------
Two ways to open a ticket:
  - /ticket           - directly opens the modal, for anyone who prefers typing a command
  - the "Open a Ticket" button - posted once via /setup-tickets (owner-only),
    it sits in a channel permanently and opens the same modal on click

Both end up creating a support ticket as a NEW PRIVATE THREAD inside
your support channel.

Why a private thread rather than just posting a message in the support
channel: a private thread is only visible to people explicitly added
to it, plus anyone with access to the parent channel (your staff). We
explicitly add the ticket opener with thread.add_user() - that's what
lets them see and reply in the thread even if they don't otherwise
have permission to see the support channel itself. Nobody else in the
server can see the ticket at all.
"""

import os

import discord
from discord import app_commands
from discord.ext import commands

from utils import OWNER_ID

SUPPORT_CHANNEL_ID = int(os.getenv("SUPPORT_CHANNEL_ID", "0"))


class TicketModal(discord.ui.Modal, title="Open a Support Ticket"):
    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="A short summary of your issue",
        max_length=100,
    )
    details = discord.ui.TextInput(
        label="Details",
        style=discord.TextStyle.paragraph,
        placeholder="Describe your issue in as much detail as you can.",
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction):
        # Creating a thread, sending to it, and adding a user is three
        # separate Discord API calls in a row - deferring first protects
        # against this occasionally taking longer than the normal 3
        # second response window.
        await interaction.response.defer(ephemeral=True, thinking=True)

        channel = interaction.client.get_channel(SUPPORT_CHANNEL_ID)
        if channel is None:
            await interaction.followup.send(
                "⚠️ Couldn't find the support channel - let the bot owner know "
                "SUPPORT_CHANNEL_ID needs checking.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"🎫 {self.subject}",
            description=str(self.details),
            color=discord.Color.orange(),
        )
        embed.set_author(name=str(interaction.user), icon_url=interaction.user.display_avatar.url)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        try:
            thread = await channel.create_thread(
                name=f"Ticket - {interaction.user.display_name}"[:100],
                type=discord.ChannelType.private_thread,
            )
            await thread.send(embed=embed)
            await thread.add_user(interaction.user)
        except discord.Forbidden:
            await interaction.followup.send(
                "⚠️ I don't have permission to create threads in the support channel. "
                "The bot needs 'Create Private Threads' and 'Send Messages in Threads' "
                "permission there.",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Your ticket has been opened: {thread.mention}. Staff will get back to you there.",
            ephemeral=True,
        )


class TicketButtonView(discord.ui.View):
    """
    The button posted by /setup-tickets. timeout=None + a fixed
    custom_id makes this "persistent" - it keeps working forever, even
    across bot restarts, as long as bot.py registers it once via
    bot.add_view() on startup (see bot.py's on_ready) - otherwise a
    click on it after a restart would silently do nothing.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open a Ticket", style=discord.ButtonStyle.blurple, emoji="🎫", custom_id="cliply_open_ticket")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketModal())


class SupportCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ticket", description="Open a private support ticket")
    async def ticket(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TicketModal())

    @app_commands.command(name="setup-tickets", description="Owner-only: post the 'Open a Ticket' panel in this channel")
    async def setup_tickets(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🎫 Need help?",
            description=(
                "Click the button below to open a private ticket with our support team.\n\n"
                "Only you and staff will be able to see it."
            ),
            color=discord.Color.blurple(),
        )

        # This posts a NEW, permanent message with the button - it isn't
        # ephemeral, and isn't tied to this /setup-tickets interaction at
        # all after this point. Running /setup-tickets again will post a
        # second panel, so only run it once per channel you want it in.
        await interaction.channel.send(embed=embed, view=TicketButtonView())
        await interaction.response.send_message("✅ Ticket panel posted.", ephemeral=True)


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.support') runs."""
    await bot.add_cog(SupportCog(bot))