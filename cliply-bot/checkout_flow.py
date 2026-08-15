"""
checkout_flow.py
-----------------
One shared helper, used by both cogs/write.py (for write slots) and
cogs/marketplace.py (for purchases).

Creates payment links for BOTH Stripe and PayPal, then DMs the user
(or falls back to an ephemeral reply if DMs are closed).
"""

import os
import discord

import stripe_service
from utils import PRICE


class PaymentChoiceView(discord.ui.View):
    """View containing link buttons for both Stripe and PayPal checkout."""
    def __init__(self, stripe_url: str, paypal_url: str):
        super().__init__(timeout=None)

        # Stripe (Card) Link Button
        self.add_item(
            discord.ui.Button(
                label=f"Pay ${PRICE} with Card (Stripe)",
                style=discord.ButtonStyle.link,
                url=stripe_url,
                emoji="💳",
            )
        )

        # PayPal Link Button
        self.add_item(
            discord.ui.Button(
                label=f"Pay ${PRICE} with PayPal",
                style=discord.ButtonStyle.link,
                url=paypal_url,
                emoji="🅿️",
            )
        )


async def send_checkout_link(
    interaction: discord.Interaction,
    tx_type: str,
    product_name: str,
    idea_id: int | None = None,
):
    """
    Creates a Stripe Checkout Session AND a PayPal payment link, then
    sends both options via DM (or ephemeral message if DMs are blocked).
    """
    # 1. Defer the interaction so Discord gives us time to talk to Stripe
    await interaction.response.defer(ephemeral=True)

    user_id = interaction.user.id

    # 2. Create the Stripe Checkout URL
    stripe_url = await stripe_service.create_checkout_session(
        discord_user_id=user_id,
        tx_type=tx_type,
        product_name=product_name,
        idea_id=idea_id,
    )

    # 3. Construct the PayPal Checkout URL with custom metadata
    paypal_email = os.getenv("PAYPAL_EMAIL", "your-paypal-email@example.com")
    custom_data = f"{tx_type}:{user_id}:{idea_id or ''}"

    paypal_url = (
        f"https://www.paypal.com/cgi-bin/webscr?"
        f"cmd=_xclick&"
        f"business={paypal_email}&"
        f"item_name={product_name.replace(' ', '+')}&"
        f"amount={PRICE}&"
        f"currency_code=USD&"
        f"custom={custom_data}"
    )

    # 4. Attach both links to the view
    link_view = PaymentChoiceView(stripe_url=stripe_url, paypal_url=paypal_url)

    message_text = (
        f"Complete your **${PRICE}** payment below using Stripe or PayPal. "
        "I'll message you again the moment it's confirmed — usually just a few seconds after you pay."
    )

    # 5. Try DMing the user; fall back to an ephemeral followup if DMs are closed
    try:
        await interaction.user.send(content=message_text, view=link_view)
        await interaction.followup.send("📬 Check your DMs for the payment link!", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send(content=message_text, view=link_view, ephemeral=True)