"""
webhook_server.py
------------------
A small FastAPI app with two GET routes that PayPal redirects the
buyer's browser to after they approve or cancel a payment:

  GET /paypal/capture - PayPal sends the buyer here after approval,
                         with ?token=<order_id> appended (plus whatever
                         of our own metadata we put on the return_url -
                         see checkout_flow.py). THIS is where the buyer
                         actually gets charged: we call PayPal's
                         Capture API for that order_id ourselves.
  GET /paypal/cancel   - PayPal sends the buyer here if they back out
                         without paying.

It runs ALONGSIDE the bot, IN THE SAME PYTHON PROCESS (started as a
background task in bot.py's main()), so it can message Discord users
directly through the one existing bot connection, with no extra
plumbing to pass messages between two separate processes.

Note there's no webhook signature verification anywhere in this file,
and that's intentional (see paypal_service.py's module docstring for
why): we never trust an inbound "payment succeeded" request from
anyone. Every payment is confirmed by US calling PayPal's API
directly, authenticated with our own credentials.
"""

import discord
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

import database
import paypal_service
import subscriptions
from cogs.marketplace import deliver_purchase, deliver_random_purchase
from cogs.write import StartWritingView
from utils import PRICE

app = FastAPI()

# Set by bot.py via attach_bot() right after the bot logs in. We need a
# live bot/Client reference here so this file can fetch users and DM
# them - there's no Discord "interaction" available in a plain HTTP route.
_bot: discord.Client | None = None


def attach_bot(bot: discord.Client):
    global _bot
    _bot = bot


def _page(title: str, message: str) -> HTMLResponse:
    """A tiny, dependency-free HTML page - this is what the buyer's browser shows."""
    return HTMLResponse(
        f"""
        <html>
          <head><title>{title}</title></head>
          <body style="font-family: sans-serif; text-align: center; padding: 60px;">
            <h1>{title}</h1>
            <p>{message}</p>
            <p>You can close this tab and go back to Discord.</p>
          </body>
        </html>
        """
    )


@app.get("/paypal/capture")
async def paypal_capture(request: Request):
    order_id = request.query_params.get("token")
    discord_user_id_raw = request.query_params.get("discord_user_id")
    tx_type = request.query_params.get("type")
    idea_id_raw = request.query_params.get("idea_id")

    if not order_id or not discord_user_id_raw or not tx_type:
        return _page(
            "Something went wrong",
            "This payment link is missing information. Please go back to Discord and try again.",
        )

    if _bot is None:
        return _page("One moment", "The bot is still starting up - please try again in a few seconds.")

    discord_user_id = int(discord_user_id_raw)
    idea_id = int(idea_id_raw) if idea_id_raw else None

    try:
        capture = await paypal_service.capture_order(order_id)
    except Exception as error:
        # Most commonly this means the order was ALREADY captured -
        # e.g. the buyer refreshed this page, or went back and forward
        # in their browser. We can't cleanly tell PayPal's specific
        # error apart here, so we play it safe: never re-deliver, just
        # reassure them instead.
        print(f"[webhook_server] PayPal capture failed for order {order_id}: {error}")
        return _page(
            "Already processed",
            "This payment was already completed - check Discord, your confirmation should already be there.",
        )

    if capture.get("status") != "COMPLETED":
        return _page("Payment not completed", f"PayPal reported status: {capture.get('status')}. No charge was made.")

    # Used only for refunds if delivery fails below (e.g. an idea sold
    # out to someone else in the meantime) - see deliver_purchase().
    capture_id = None
    try:
        capture_id = capture["purchase_units"][0]["payments"]["captures"][0]["id"]
    except (KeyError, IndexError):
        pass

    if tx_type == "write_slot":
        await _handle_write_slot_paid(discord_user_id)
    elif tx_type == "purchase" and idea_id is not None:
        await deliver_purchase(_bot, idea_id, discord_user_id, capture_id)
    elif tx_type == "random_purchase":
        await deliver_random_purchase(_bot, discord_user_id, capture_id)
    elif tx_type == "subscription":
        await _handle_subscription_paid(discord_user_id)
    else:
        print(f"[webhook_server] Unknown/incomplete tx_type on capture: {tx_type!r}")
        return _page(
            "Something went wrong",
            "Your payment succeeded, but I couldn't tell what it was for. Please open a support ticket.",
        )

    return _page("Payment successful! ✅", "Check Discord - your confirmation should already be there.")


@app.get("/paypal/cancel")
async def paypal_cancel():
    return _page("Payment cancelled", "No charge was made. Head back to Discord to try again anytime.")


async def _handle_write_slot_paid(discord_user_id: int):
    """
    Confirms the write-slot payment, then DMs a "Start Writing" button.
    We DM the button rather than opening a modal directly because a
    modal can only be sent as the direct response to a fresh Discord
    interaction - this HTTP route has no such interaction to respond
    to. The button click itself becomes that fresh interaction.
    """
    user = await _bot.fetch_user(discord_user_id)

    # Fetch the real user first so ensure_user() gets their actual
    # display name, rather than overwriting a real one on file with a
    # blank string for a returning user.
    await database.ensure_user(discord_user_id, display_name=user.display_name)
    await database.log_transaction(
        user_id=discord_user_id,
        tx_type="write_slot",
        amount=PRICE,
        idea_id=None,
        payment_status="confirmed",
    )

    await user.send(
        content="✅ Payment confirmed! Click below when you're ready to submit your idea:",
        view=StartWritingView(),
    )


async def _handle_subscription_paid(discord_user_id: int):
    """
    Activates (or extends) the buyer's 30-Day Pass, then DMs a
    confirmation with the exact date it expires.
    """
    user = await _bot.fetch_user(discord_user_id)

    await database.ensure_user(discord_user_id, display_name=user.display_name)
    await database.log_transaction(
        user_id=discord_user_id,
        tx_type="subscription",
        amount=subscriptions.SUBSCRIPTION_PRICE,
        idea_id=None,
        payment_status="confirmed",
    )

    new_expiry = await subscriptions.activate_pass(discord_user_id)

    await user.send(
        content=(
            f"✅ Your 30-Day Pass is active! Writing and buying ideas is now "
            f"${subscriptions.DISCOUNT_PRICE} instead of ${PRICE}, until "
            f"**{new_expiry.strftime('%B %d, %Y')}**."
        )
    )