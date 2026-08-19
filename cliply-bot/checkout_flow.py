"""
checkout_flow.py
-----------------
One shared helper, used by both cogs/write.py (write slots) and
cogs/marketplace.py (purchases), so the "create a PayPal order and get
the approval link in front of the buyer" logic only exists in one place.

Lives at the project root (not inside cogs/) so neither cog needs to
import it FROM the other - same reasoning as utils.py.
"""

import os
import urllib.parse

import discord

import paypal_service
from utils import PRICE

# Your app's public URL, e.g. https://your-app.up.railway.app - PayPal
# needs a real, public return_url to redirect the buyer's browser back
# to after they approve payment. Set this in your .env / Railway
# Variables to whatever domain Railway (or wherever you're hosted) gave you.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")


async def send_checkout_link(
    interaction: discord.Interaction,
    tx_type: str,
    product_name: str,
    idea_id: int | None = None,
):
    """
    Creates a PayPal order, then tries to DM the buyer the approval
    link (matches the original spec: "bot DMs a payment link"). Falls
    back to an ephemeral in-channel reply if their DMs are closed to
    server members.

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
            label=f"Pay ${PRICE} - Card or PayPal",
            style=discord.ButtonStyle.link,
            url=approve_url,
            emoji="💳",
        )
    )

    message_text = (
        f"Complete your ${PRICE} payment below - **no PayPal account needed**, you can "
        "pay with any credit or debit card directly on that page (or log in with PayPal "
        "if you prefer). I'll message you again the moment it's confirmed - usually just "
        "a few seconds after you pay."
    )

    try:
        await interaction.user.send(content=message_text, view=link_view)
        await interaction.response.send_message("📬 Check your DMs for the payment link!", ephemeral=True)
    except discord.Forbidden:
        # Their DM settings block messages from server members - fall back
        # to an ephemeral reply right here instead.
        await interaction.response.send_message(content=message_text, view=link_view, ephemeral=True)