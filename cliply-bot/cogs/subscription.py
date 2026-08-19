"""
cogs/subscription.py
---------------------
Two ways to buy a 30-Day Pass:
  - /subscribe          - directly starts checkout, for anyone who prefers typing a command
  - the "Get 30-Day Pass" button - posted once via /setup-subscription
    (owner-only), it sits in a channel permanently and starts the same
    checkout on click

Both call the exact same underlying logic (_start_subscription_checkout
below) so there's only one place that actually knows how to buy a pass -
no duplicated logic between the command and the button to keep in sync.

See subscriptions.py for why this is a plain one-time $5 purchase
rather than a real recurring PayPal subscription.
"""

import discord
from discord import app_commands
from discord.ext import commands

import checkout_flow
import database
import subscriptions
from utils import PRICE, OWNER_ID, is_free_publisher


async def _start_subscription_checkout(interaction: discord.Interaction):
    await database.ensure_user(interaction.user.id, interaction.user.display_name)

    if is_free_publisher(interaction.user):
        await interaction.response.send_message(
            "You're an owner/mod - writing and buying ideas is already free for you, "
            "no pass needed!",
            ephemeral=True,
        )
        return

    # Not a hard stop if they already have days left - buying again just
    # extends it (see subscriptions.activate_pass), and the post-payment
    # confirmation DM shows their new expiry date either way. That's
    # also mentioned directly in the checkout DM itself - see
    # checkout_flow.py - so there's no need for a separate message here.
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

    @discord.ui.button(label="Get 30-Day Pass", style=discord.ButtonStyle.blurple, emoji="🌟", custom_id="cliply_subscribe")
    async def subscribe(self, interaction: discord.Interaction, button: discord.ui.Button):
        await _start_subscription_checkout(interaction)


class SubscriptionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="subscribe", description=f"Get a 30-Day Pass - ${subscriptions.DISCOUNT_PRICE} per idea instead of ${PRICE}")
    async def subscribe(self, interaction: discord.Interaction):
        await _start_subscription_checkout(interaction)

    @app_commands.command(name="setup-subscription", description="Owner-only: post the 30-Day Pass panel in this channel")
    async def setup_subscription(self, interaction: discord.Interaction):
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "This command is restricted to the bot owner.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🌟 30-Day Pass",
            description=(
                "Unlock discounted pricing across the marketplace for 30 days.\n\n"
                f"**${subscriptions.DISCOUNT_PRICE}** per idea instead of **${PRICE}** — "
                "whether you're writing or buying.\n\n"
                "No PayPal account required. Pay by any credit or debit card, "
                "directly and securely."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Write ideas", value=f"${subscriptions.DISCOUNT_PRICE} instead of ${PRICE}", inline=True)
        embed.add_field(name="Buy ideas", value=f"${subscriptions.DISCOUNT_PRICE} instead of ${PRICE}", inline=True)
        embed.add_field(name="Pass price", value=f"${subscriptions.SUBSCRIPTION_PRICE} / 30 days", inline=True)
        embed.set_footer(text="Buying another pass while one is active extends it - it never resets your remaining time.")

        # A new, permanent message - not tied to this interaction after
        # this point. Running /setup-subscription again posts a second
        # panel, so only run it once per channel you want it in.
        await interaction.channel.send(embed=embed, view=SubscribeButtonView())
        await interaction.response.send_message("✅ Subscription panel posted.", ephemeral=True)


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.subscription') runs."""
    await bot.add_cog(SubscriptionCog(bot))