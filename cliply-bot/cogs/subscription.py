"""
cogs/subscription.py
---------------------
Two ways to buy (or extend) a 30-Day Pass: $5, drops the price of
writing or buying an idea from $10 to $2 for 30 days. See
subscriptions.py for why this is a plain one-time purchase rather than
a real recurring PayPal subscription.

  - /subscribe               - directly starts checkout, for anyone who
                                prefers typing a command
  - the "Get 30-Day Pass" button - posted once via /setup-subscription
    (owner-only), it sits in a channel permanently and starts the same
    checkout on click

Same pattern as cogs/support.py's ticket button: a persistent view
with a fixed custom_id, registered once in bot.py's on_ready so it
keeps working across restarts, plus an owner-only command that posts
the panel wherever it's run.
"""

import discord
from discord import app_commands
from discord.ext import commands

import checkout_flow
import database
import subscriptions
from utils import OWNER_ID, is_free_publisher


async def _start_subscription_checkout(interaction: discord.Interaction):
    """
    Shared by both /subscribe and the button - ensures the user exists
    in the DB, skips checkout entirely for owner/mods (already free),
    and otherwise sends the PayPal checkout link the normal way.
    """
    await database.ensure_user(interaction.user.id, interaction.user.display_name)

    if is_free_publisher(interaction.user):
        await interaction.response.send_message(
            "You're an owner/mod - writing and buying ideas is already free for you, "
            "no pass needed!",
            ephemeral=True,
        )
        return

    # Not a hard stop if they already have days left - buying again
    # just extends it (see subscriptions.activate_pass), and the
    # post-payment confirmation DM will show their new expiry date
    # either way. We don't need a separate message here - sending
    # two responses to the same interaction isn't allowed anyway.
    await checkout_flow.send_checkout_link(
        interaction,
        tx_type="subscription",
        product_name="Cliply 30-Day Pass",
    )


class SubscribeButtonView(discord.ui.View):
    """
    The button posted by /setup-subscription. timeout=None + a fixed
    custom_id makes this "persistent" - it keeps working forever, even
    across bot restarts, as long as bot.py registers it once via
    bot.add_view() on startup (see bot.py's on_ready) - otherwise a
    click on it after a restart would silently do nothing.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label=f"Get 30-Day Pass - ${subscriptions.SUBSCRIPTION_PRICE}",
        style=discord.ButtonStyle.blurple,
        emoji="⭐",
        custom_id="cliply_subscribe_panel",
    )
    async def subscribe_click(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_subscription_checkout(interaction)


class SubscriptionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="subscribe", description=f"Get a 30-Day Pass - ${subscriptions.DISCOUNT_PRICE} per idea instead of $10")
    async def subscribe(self, interaction: discord.Interaction):
        await _start_subscription_checkout(interaction)

    @app_commands.command(
        name="setup-subscription",
        description="Owner-only: post the '30-Day Pass' panel in this channel",
    )
    async def setup_subscription(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="⭐ 30-Day Pass",
            description=(
                f"Pay **${subscriptions.SUBSCRIPTION_PRICE}** once and every write slot or "
                f"idea purchase drops from ${subscriptions.PRICE} to just "
                f"**${subscriptions.DISCOUNT_PRICE}** for the next **{subscriptions.PASS_DURATION_DAYS} days**.\n\n"
                "Click below to check out - card or PayPal, no PayPal account needed. "
                "Already have a pass? Buying again just adds 30 more days on top."
            ),
            color=discord.Color.blurple(),
        )

        # This posts a NEW, permanent message with the button - it isn't
        # ephemeral, and isn't tied to this /setup-subscription interaction
        # at all after this point. Running it again will post a second
        # panel, so only run it once per channel you want it in (e.g. the
        # #subscription channel, ID 1539589897831063552).
        await interaction.channel.send(embed=embed, view=SubscribeButtonView())
        await interaction.response.send_message("✅ Subscription panel posted.", ephemeral=True)


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.subscription') runs."""
    await bot.add_cog(SubscriptionCog(bot))