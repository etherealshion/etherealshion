"""
checkout_flow.py
-----------------
One shared helper, used by cogs/write.py (write slots), cogs/marketplace.py
(purchases), and cogs/subscription.py (the 30-Day Pass itself), so the
"create a PayPal order and get the approval link in front of the buyer"
logic only exists in one place.

Lives at the project root (not inside cogs/) so no cog needs to import
it FROM another cog - same reasoning as utils.py.
"""

import os
import urllib.parse

import discord

import paypal_service
import subscriptions
from utils import PRICE

# Your app's public URL, e.g. https://your-app.up.railway.app - PayPal
# needs a real, public return_url to redirect the buyer's browser back
# to after they approve payment. Set this in your .env / Railway
# Variables to whatever domain Railway (or wherever you're hosted) gave you.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


async def _resolve_amount(interaction: discord.Interaction, tx_type: str) -> int:
    """
    The dollar amount to actually charge, in whole dollars:
      - "subscription" is always the flat $5 pass price.
      - write_slot / purchase / random_purchase depend on the buyer:
        $2 if they have an active 30-Day Pass, otherwise the normal $10.
        (Owner/mods never reach this function at all - see is_free_publisher
        checks in write.py/marketplace.py, which skip checkout entirely.)
    """
    if tx_type == "subscription":
        return subscriptions.SUBSCRIPTION_PRICE
    return await subscriptions.get_effective_price(interaction.user)


async def send_checkout_link(
    interaction: discord.Interaction,
    tx_type: str,
    product_name: str,
    idea_id: int | None = None,
):
    """
    Creates a PayPal order for the correct amount, then tries to DM the
    buyer the approval link (matches the original spec: "bot DMs a
    payment link"). Falls back to an ephemeral in-channel reply if
    their DMs are closed to server members.

    Nothing is unlocked here - only PayPal's redirect back to our
    /paypal/capture route (see webhook_server.py), which is what
    actually captures (charges) the payment.
    """
    if not PUBLIC_BASE_URL:
        await interaction.response.send_message(
            "⚠️ PUBLIC_BASE_URL isn't configured - let the bot owner know: it needs "
            "to be set to this app's public URL for payments to work at all.",
            ephemeral=True,
        )
        return

    amount = await _resolve_amount(interaction, tx_type)

    # Our own metadata rides along as query params on the return_url -
    # PayPal appends its own (?token=<order_id>) to whatever URL we give
    # it, so ours survive alongside theirs. This is what lets
    # /paypal/capture know WHO paid and WHAT FOR, without needing an
    # extra API call back to PayPal to look it up.
    return_params = {"discord_user_id": interaction.user.id, "type": tx_type}
    if idea_id is not None:
        return_params["idea_id"] = idea_id
    return_url = f"{PUBLIC_BASE_URL}/paypal/capture?{urllib.parse.urlencode(return_params)}"
    cancel_url = f"{PUBLIC_BASE_URL}/paypal/cancel"

    # Also stored directly on the PayPal order itself (as a backup / for
    # your own records if you ever look at orders in the PayPal dashboard).
    custom_id = f"{interaction.user.id}:{tx_type}:{idea_id or 0}"

    try:
        _order_id, approve_url = await paypal_service.create_order(
            return_url=return_url,
            cancel_url=cancel_url,
            product_name=product_name,
            custom_id=custom_id,
            amount_usd=f"{amount}.00",
        )
    except Exception:
        await interaction.response.send_message(
            "⚠️ Couldn't start a PayPal checkout right now - please try again in a moment.",
            ephemeral=True,
        )
        raise  # re-raised so it still shows up in your logs, not just swallowed silently

    link_view = discord.ui.View(timeout=None)
    link_view.add_item(
        discord.ui.Button(
            label=f"Pay ${amount} - Card or PayPal",
            style=discord.ButtonStyle.link,
            url=approve_url,
            emoji="💳",
        )
    )

    message_text = (
        f"Complete your ${amount} payment below - **no PayPal account needed**, you can "
        "pay with any credit or debit card directly on that page (or log in with PayPal "
        "if you prefer). I'll message you again the moment it's confirmed - usually just "
        "a few seconds after you pay."
    )

    # The upsell: only shown when paying the full $10 (not already
    # discounted, and not for the pass purchase itself) - this is the
    # "$10 (or $2 with subscription)" nudge, placed right at the moment
    # it's most persuasive: the instant they see the full price.
    if tx_type != "subscription" and amount == PRICE:
        message_text += (
            f"\n\n💡 **Tip:** Get a 30-Day Pass for ${subscriptions.SUBSCRIPTION_PRICE} and pay only "
            f"${subscriptions.DISCOUNT_PRICE} per idea instead of ${PRICE} - use `/subscribe`."
        )

    # If they're buying a pass while already having days left, let them
    # know upfront that this just extends it rather than resetting it.
    if tx_type == "subscription":
        remaining = await subscriptions.days_remaining(interaction.user.id)
        if remaining > 0:
            message_text += (
                f"\n\nYou already have **{remaining} day(s)** left on your current pass - "
                "this will add 30 more days on top of that, not replace it."
            )

    try:
        await interaction.user.send(content=message_text, view=link_view)
        await interaction.response.send_message("📬 Check your DMs for the payment link!", ephemeral=True)
    except discord.Forbidden:
        # Their DM settings block messages from server members - fall back
        # to an ephemeral reply right here instead.
        await interaction.response.send_message(content=message_text, view=link_view, ephemeral=True)