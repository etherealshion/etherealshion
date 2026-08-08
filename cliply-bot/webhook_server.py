"""
webhook_server.py
------------------
A small FastAPI app that listens for Stripe webhook events. It runs
ALONGSIDE the bot, IN THE SAME PYTHON PROCESS (started as a background
task in bot.py's main()) rather than as a separate deployment. That's
a deliberate choice: it lets this file message Discord users directly
through the one existing bot connection, with no extra plumbing to
pass messages between two separate processes.

Why a webhook at all, instead of just trusting the browser? Because
the browser redirect after Stripe Checkout (success_url) is NOT proof
of payment - a user could close the tab, or the browser could fail to
load the redirect, even after paying successfully; conversely, someone
could visit success_url directly without ever paying. Stripe's webhook
is the one source of truth: it's Stripe's own server telling ours,
server-to-server, "this specific payment actually succeeded."

Signature verification (stripe.Webhook.construct_event) is what stops
someone from just POSTing a fake "payment succeeded" JSON body to this
endpoint and getting a free write slot or idea - only a request signed
with your actual STRIPE_WEBHOOK_SECRET will be accepted.
"""

import os
import traceback

import discord
import stripe
from fastapi import FastAPI, Request, HTTPException

import database
from cogs.marketplace import deliver_purchase, deliver_random_purchase
from cogs.write import StartWritingView
from utils import PRICE

app = FastAPI()

# Set by bot.py via attach_bot() right after the bot logs in. We need a
# live bot/Client reference here so this file can fetch users and DM
# them - there's no Discord "interaction" available in a webhook handler.
_bot: discord.Client | None = None


def attach_bot(bot: discord.Client):
    global _bot
    _bot = bot


@app.post("/webhook")
async def stripe_webhook(request: Request):
    # This confirms the request reached us AT ALL - if you never see
    # this line after paying, the problem is upstream of this file
    # entirely (most likely: `stripe listen` isn't running, or isn't
    # forwarding to the right port). See the troubleshooting checklist.
    print("[webhook_server] Webhook endpoint was hit - verifying signature...")

    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except (ValueError, stripe.error.SignatureVerificationError) as error:
        # By far the most common cause: `stripe listen` prints a NEW
        # whsec_... secret every single time you start it. If you
        # restarted `stripe listen` since you last copied it into
        # .env, the old secret is now stale and every event fails here.
        print(f"[webhook_server] REJECTED - signature verification failed: {error}")
        print(
            "[webhook_server] Most likely cause: STRIPE_WEBHOOK_SECRET in .env doesn't match "
            "your current `stripe listen` session. Copy the whsec_... it printed just now, "
            "update .env, and RESTART the bot (python bot.py) - .env is only read once at startup."
        )
        raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {error}")

    print(f"[webhook_server] Signature OK. Event type: {event['type']}")

    if event["type"] == "checkout.session.completed":
        try:
            await _handle_checkout_completed(event["data"]["object"])
        except Exception:
            # Never let a bug in here vanish silently - print the full
            # traceback so a broken write-slot/purchase delivery is
            # obvious in your terminal, not a mysterious "nothing happened."
            print("[webhook_server] Error while handling checkout.session.completed:")
            traceback.print_exc()

    # Stripe just wants a 200 response to know we received the event -
    # the body content doesn't matter to it.
    return {"status": "ok"}


async def _handle_checkout_completed(session):
    if _bot is None:
        print("[webhook_server] Received a payment but the bot isn't attached yet - ignoring.")
        return

    # `session.metadata` looks and prints like a plain dict but is
    # ACTUALLY the same kind of typed Stripe object as `session` itself
    # - I confirmed this by testing directly against the installed
    # `stripe` library. Calling .get() on it would crash the exact
    # same way. .to_dict() converts it into a real Python dict, on
    # which .get() works normally.
    metadata = session.metadata.to_dict() if session.metadata else {}
    tx_type = metadata.get("type")
    discord_user_id_raw = metadata.get("discord_user_id")
    payment_intent_id = session.payment_intent

    if not tx_type or not discord_user_id_raw:
        print(f"[webhook_server] Ignoring session with missing metadata: {metadata!r}")
        return

    discord_user_id = int(discord_user_id_raw)

    if tx_type == "write_slot":
        await _handle_write_slot_paid(discord_user_id)
    elif tx_type == "purchase":
        idea_id = int(metadata["idea_id"])
        await deliver_purchase(_bot, idea_id, discord_user_id, payment_intent_id)
    elif tx_type == "random_purchase":
        await deliver_random_purchase(_bot, discord_user_id, payment_intent_id)
    else:
        print(f"[webhook_server] Unknown transaction type: {tx_type!r}")


async def _handle_write_slot_paid(discord_user_id: int):
    """
    Confirms the write-slot payment, then DMs a "Start Writing" button.
    We DM the button rather than opening the modal directly because a
    modal can only be sent as the direct response to a fresh Discord
    interaction - and a webhook arriving here has no such interaction
    to respond to. The button click itself becomes that fresh interaction.
    """
    user = await _bot.fetch_user(discord_user_id)

    # We fetch the real user first so ensure_user() has their actual
    # display name - calling it with a blank string would have
    # overwritten a real name already on file for a returning user.
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