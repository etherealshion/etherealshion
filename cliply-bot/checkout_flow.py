"""
checkout_flow.py
-----------------
One shared helper, used by both cogs/write.py (for write slots) and
cogs/marketplace.py (for purchases), so the "create a Stripe session
and get the payment link to the user" logic only exists in one place.

Lives at the project root (not inside cogs/) specifically so neither
cog needs to import it FROM the other - avoiding the circular-import
problem described in utils.py.
"""

import discord

import stripe_service
from utils import PRICE


async def send_checkout_link(
    interaction: discord.Interaction,
    tx_type: str,
    product_name: str,
    idea_id: int | None = None,
):
    """
    Creates a Stripe Checkout Session, then tries to DM the payment
    link to the user (matches the original spec: "bot DMs a payment
    link"). If their DMs are closed to server members, we fall back to
    an ephemeral in-channel reply instead - ephemeral messages are only
    visible to the user who triggered them, so it's just as private.

    Nothing is unlocked here - this only gets the payment link in
    front of the user. The actual write slot / purchase only unlocks
    once Stripe's webhook confirms the payment went through (see
    webhook_server.py).
    """
    url = await stripe_service.create_checkout_session(
        discord_user_id=interaction.user.id,
        tx_type=tx_type,
        product_name=product_name,
        idea_id=idea_id,
    )

    link_view = discord.ui.View(timeout=None)
    link_view.add_item(discord.ui.Button(label=f"Pay ${PRICE}", style=discord.ButtonStyle.link, url=url))

    message_text = (
        f"Complete your ${PRICE} payment below. I'll message you again the moment "
        "it's confirmed - usually just a few seconds after you pay."
    )

    try:
        await interaction.user.send(content=message_text, view=link_view)
        await interaction.response.send_message("📬 Check your DMs for the payment link!", ephemeral=True)
    except discord.Forbidden:
        # Their DM settings block messages from server members - fall back
        # to an ephemeral reply right here instead.
        await interaction.response.send_message(content=message_text, view=link_view, ephemeral=True)