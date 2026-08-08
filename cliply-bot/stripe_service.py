"""
stripe_service.py
------------------
Wraps Stripe Checkout Session creation. A Checkout Session is Stripe's
hosted payment page - we create one, get back a URL, and send that URL
to the user. Stripe handles collecting card details; we never touch
raw card numbers ourselves, which is exactly how it should be.

We use `price_data` inline (rather than pre-creating a Stripe Product
and Price in the Stripe Dashboard) because every charge in Cliply is a
flat $10, whether it's a write slot or a purchase - inline pricing
keeps this simple and avoids a manual setup step in Stripe.

Everything we need to know once payment succeeds - who paid, what
for, and which idea if relevant - rides along in `metadata`. Stripe
stores this untouched and hands it back to us in the webhook event,
which is how webhook_server.py knows what to unlock.
"""

import asyncio
import os

import stripe

PRICE_CENTS = 1000  # $10.00 - Stripe wants amounts in the smallest currency unit (cents)

# Stripe requires success_url/cancel_url to be valid URLs, but for a
# Discord-only flow there's no real "success page" to send people to -
# we default to sending them straight back into the Discord app. Feel
# free to override these in .env with real pages if you build any.
SUCCESS_URL = os.getenv("STRIPE_SUCCESS_URL", "https://discord.com/channels/@me")
CANCEL_URL = os.getenv("STRIPE_CANCEL_URL", "https://discord.com/channels/@me")


def _configure():
    """
    Sets stripe.api_key fresh from the environment each time, rather
    than once at import time - same reasoning as ai_scorer.py's
    _get_client(): it makes a missing/bad key easier to diagnose.
    """
    api_key = os.getenv("STRIPE_SECRET_KEY")
    if not api_key:
        raise RuntimeError(
            "STRIPE_SECRET_KEY is not set. Check your .env file - it should "
            "start with 'sk_test_' while you're in Stripe test mode."
        )
    stripe.api_key = api_key


async def create_checkout_session(
    discord_user_id: int,
    tx_type: str,
    product_name: str,
    idea_id: int | None = None,
) -> str:
    """
    Creates a Stripe Checkout Session for a $10 charge and returns its
    payment URL.

    tx_type is one of 'write_slot', 'purchase', or 'random_purchase' -
    webhook_server.py reads this back out of the metadata to know what
    to do once payment succeeds.

    The actual Stripe API call is a blocking network request (the
    `stripe` library isn't async-native), so we run it in a background
    thread via asyncio.to_thread - otherwise it would freeze the bot's
    entire event loop for however long that request takes.
    """
    _configure()

    metadata = {"discord_user_id": str(discord_user_id), "type": tx_type}
    if idea_id is not None:
        metadata["idea_id"] = str(idea_id)

    def _create():
        return stripe.checkout.Session.create(
            mode="payment",
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "unit_amount": PRICE_CENTS,
                        "product_data": {"name": product_name},
                    },
                    "quantity": 1,
                }
            ],
            metadata=metadata,
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
        )

    session = await asyncio.to_thread(_create)
    return session.url


async def refund_payment_intent(payment_intent_id: str):
    """
    Issues a full refund. Used when a webhook confirms payment for
    something that's no longer available (e.g. a specific marketplace
    idea sold out to someone else while this payment was in progress).
    """
    _configure()

    def _refund():
        stripe.Refund.create(payment_intent=payment_intent_id)

    await asyncio.to_thread(_refund)