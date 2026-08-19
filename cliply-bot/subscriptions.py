"""
subscriptions.py
-----------------
The "30-Day Pass" - a $5 one-time purchase (via the same card-friendly
PayPal Orders flow as everything else) that drops the price of writing
or buying an idea from $10 to $2 for 30 days.

WHY THIS ISN'T A REAL RECURRING PAYPAL SUBSCRIPTION: PayPal's actual
Subscriptions/billing-agreement API requires the buyer to have (or
create) a PayPal account - guest/card-only checkout explicitly does
NOT extend to recurring payments, only one-time ones. Since staying
card-only with no PayPal account required has been the priority for
this whole bot, a real subscription would break that specifically for
this one feature. A 30-day pass gets the same discount behavior
without ever needing PayPal login - the trade-off is no auto-renewal;
the buyer just buys another pass when it runs out.
"""

from datetime import datetime, timedelta, timezone

import database
from utils import PRICE, is_free_publisher

SUBSCRIPTION_PRICE = 5
DISCOUNT_PRICE = 2
PASS_DURATION_DAYS = 30


async def has_active_pass(discord_user_id: int) -> bool:
    """True if this user currently has an unexpired 30-Day Pass."""
    expires_at_raw = await database.get_subscription_expiry(discord_user_id)
    if not expires_at_raw:
        return False
    expires_at = datetime.fromisoformat(expires_at_raw)
    return expires_at > datetime.now(timezone.utc)


async def days_remaining(discord_user_id: int) -> int:
    """
    How many whole days are left on their pass (0 if expired/none).
    Used for messaging - e.g. "You already have 12 days left."
    """
    expires_at_raw = await database.get_subscription_expiry(discord_user_id)
    if not expires_at_raw:
        return 0
    expires_at = datetime.fromisoformat(expires_at_raw)
    remaining = expires_at - datetime.now(timezone.utc)
    return max(0, remaining.days)


async def get_effective_price(member) -> int:
    """
    The actual price this member should pay right now for writing or
    buying an idea: $0 for the owner/mods, $2 with an active pass,
    otherwise the normal $10. `member` needs a `.id` and (for the
    free-publisher check) a `.roles` list - a discord.Member, same as
    what is_free_publisher() already expects.
    """
    if is_free_publisher(member):
        return 0
    if await has_active_pass(member.id):
        return DISCOUNT_PRICE
    return PRICE


async def activate_pass(discord_user_id: int):
    """
    Grants (or extends) a 30-Day Pass. If they already have time left
    on an existing pass, this ADDS 30 days on top of their current
    expiry rather than resetting it - buying early to avoid a gap
    never costs them days they already paid for.
    """
    now = datetime.now(timezone.utc)
    current_expiry_raw = await database.get_subscription_expiry(discord_user_id)

    start_from = now
    if current_expiry_raw:
        current_expiry = datetime.fromisoformat(current_expiry_raw)
        if current_expiry > now:
            start_from = current_expiry

    new_expiry = start_from + timedelta(days=PASS_DURATION_DAYS)
    await database.set_subscription_expiry(discord_user_id, new_expiry.isoformat())
    return new_expiry
