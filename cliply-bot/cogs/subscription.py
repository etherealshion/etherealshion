"""
cogs/subscription.py
---------------------
/subscribe - buy (or extend) a 30-Day Pass: $5, drops the price of
writing or buying an idea from $10 to $2 for 30 days. See
subscriptions.py for why this is a plain one-time purchase rather than
a real recurring PayPal subscription.
"""

import discord
from discord import app_commands
from discord.ext import commands

import checkout_flow
import database
import subscriptions
from utils import is_free_publisher


class SubscriptionCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="subscribe", description=f"Get a 30-Day Pass - ${subscriptions.DISCOUNT_PRICE} per idea instead of $10")
    async def subscribe(self, interaction: discord.Interaction):
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


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.subscription') runs."""
    await bot.add_cog(SubscriptionCog(bot))
