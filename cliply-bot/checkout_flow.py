"""
checkout_flow.py
-----------------
Generates a PayPal-only payment link for write slots and marketplace purchases.
"""

import os
import urllib.parse
import discord

from utils import PRICE


class PayPalChoiceView(discord.ui.View):
    """View containing only the PayPal checkout link button."""

    def __init__(self, paypal_url: str):
        super().__init__(timeout=None)

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
    Creates a PayPal-only checkout URL and DMs it to the user.
    """
    user_id = interaction.user.id

    # Toggle environment using env variable (defaults to sandbox)
    paypal_mode = os.getenv("PAYPAL_MODE", "sandbox").lower()
    paypal_email = os.getenv("PAYPAL_EMAIL", "sb-lamkv52263940@business.example.com")

    base_url = (
        "https://www.sandbox.paypal.com/cgi-bin/webscr"
        if paypal_mode == "sandbox"
        else "https://www.paypal.com/cgi-bin/webscr"
    )

    custom_data = f"{user_id}:{tx_type}:{idea_id or 0}"

    # Build standard _xclick parameters with explicit checkout options
    params = {
        "cmd": "_xclick",
        "business": paypal_email,
        "item_name": str(product_name),
        "amount": f"{float(PRICE):.2f}",
        "currency_code": "USD",
        "custom": str(custom_data),
        "no_shipping": "1",  # Disables physical address prompt for digital delivery
        "no_note": "1",  # Prevents note inputs that interfere with redirect flows
        "bn": "PP-BuyNowBF",  # Standard PayPal build notation
    }

    paypal_url = f"{base_url}?{urllib.parse.urlencode(params)}"

    link_view = PayPalChoiceView(paypal_url=paypal_url)

    message_text = (
        f"Complete your **${PRICE}** payment via PayPal below. "
        "I'll message you again the moment it's confirmed — usually just a few seconds after you pay."
    )

    try:
        await interaction.user.send(content=message_text, view=link_view)
        await interaction.response.send_message(
            "📬 Check your DMs for the payment link!", ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            content=message_text, view=link_view, ephemeral=True
        )