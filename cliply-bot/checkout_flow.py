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
    
    # PayPal Sandbox Merchant Email (Replace with os.getenv("PAYPAL_EMAIL") when launching live)
    paypal_email = "sb-lamkv52263940@business.example.com"
    custom_data = f"{user_id}:{tx_type}:{idea_id or 0}"

    # Build query parameters cleanly so prices and spaces are properly encoded
    params = {
        "cmd": "_xclick",
        "business": paypal_email,
        "item_name": product_name,
        "amount": f"{PRICE:.2f}",  # Forces standard 10.00 currency format
        "currency_code": "USD",
        "custom": custom_data,
    }

    # Sandbox URL for testing (Change back to www.paypal.com when launching live)
    paypal_url = f"https://www.sandbox.paypal.com/cgi-bin/webscr?{urllib.parse.urlencode(params)}"

    link_view = PayPalChoiceView(paypal_url=paypal_url)

    message_text = (
        f"Complete your **${PRICE}** payment via PayPal below. "
        "I'll message you again the moment it's confirmed — usually just a few seconds after you pay."
    )

    try:
        await interaction.user.send(content=message_text, view=link_view)
        await interaction.response.send_message("📬 Check your DMs for the payment link!", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message(content=message_text, view=link_view, ephemeral=True)