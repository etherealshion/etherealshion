"""
webhook_server.py
------------------
A FastAPI app that listens for BOTH Stripe and PayPal webhook events (JSON & IPN).
Runs alongside the bot in the same Python process.
"""

import os
import traceback
import httpx

import discord
import stripe
from fastapi import FastAPI, Request, HTTPException

import database
from cogs.marketplace import deliver_purchase, deliver_random_purchase
from cogs.write import StartWritingView
from utils import PRICE

app = FastAPI()

_bot: discord.Client | None = None


def attach_bot(bot: discord.Client):
    global _bot
    _bot = bot


async def _verify_paypal_signature(request: Request, body_bytes: bytes) -> bool:
    """
    Verifies PayPal webhook signature via PayPal's REST API.
    Bypasses verification when running in sandbox mode for simulator testing.
    """
    paypal_mode = os.getenv("PAYPAL_MODE", "sandbox").lower()
    if paypal_mode == "sandbox":
        print("[webhook_server] Sandbox mode active: Skipping PayPal signature verification.")
        return True

    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID")
    client_id = os.getenv("PAYPAL_CLIENT_ID")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET")

    if not all([webhook_id, client_id, client_secret]):
        print("[webhook_server] Missing PayPal env vars for signature verification.")
        return False

    headers = request.headers
    auth_url = "https://api-m.paypal.com/v1/oauth2/token"
    verify_url = "https://api-m.paypal.com/v1/notifications/verify-webhook-signature"

    async with httpx.AsyncClient() as client:
        auth_resp = await client.post(
            auth_url,
            data={"grant_type": "client_credentials"},
            auth=(client_id, client_secret),
        )
        if auth_resp.status_code != 200:
            print("[webhook_server] Failed to get PayPal OAuth token.")
            return False

        access_token = auth_resp.json().get("access_token")

        verify_payload = {
            "transmission_id": headers.get("paypal-transmission-id"),
            "transmission_time": headers.get("paypal-transmission-time"),
            "cert_url": headers.get("paypal-cert-url"),
            "auth_algo": headers.get("paypal-auth-algo"),
            "transmission_sig": headers.get("paypal-transmission-sig"),
            "webhook_id": webhook_id,
            "webhook_event": await request.json(),
        }

        verify_resp = await client.post(
            verify_url,
            json=verify_payload,
            headers={"Authorization": f"Bearer {access_token}"},
        )

        if verify_resp.status_code == 200:
            status = verify_resp.json().get("verification_status")
            return status == "SUCCESS"

    return False


@app.post("/webhook")
async def combined_webhook(request: Request):
    print("[webhook_server] Webhook endpoint hit...")
    
    # ------------------------------------------------------------------
    # 1. PAYPAL IPN (FORM DATA) HANDLING
    # ------------------------------------------------------------------
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        print("[webhook_server] Processing PayPal IPN Form Data...")
        form_data = await request.form()
        payload = dict(form_data)

        payment_status = payload.get("payment_status", "")
        if payment_status in ["Completed", "Processed"]:
            try:
                await _handle_paypal_completed({
                    "custom_id": payload.get("custom"),
                    "id": payload.get("txn_id"),
                })
            except Exception:
                print("[webhook_server] Error while handling PayPal IPN event:")
                traceback.print_exc()

        return {"status": "ok"}

    raw_body = await request.body()

    # ------------------------------------------------------------------
    # 2. PAYPAL REST JSON WEBHOOK HANDLING
    # ------------------------------------------------------------------
    if "paypal-transmission-id" in request.headers:
        print("[webhook_server] Processing PayPal JSON Webhook...")
        
        try:
            is_valid = await _verify_paypal_signature(request, raw_body)
            if not is_valid:
                print("[webhook_server] REJECTED - PayPal signature verification failed.")
                raise HTTPException(status_code=400, detail="Invalid PayPal signature")
        except Exception as e:
            print(f"[webhook_server] Error verifying PayPal signature: {e}")
            raise HTTPException(status_code=400, detail="PayPal signature verification error")

        payload = await request.json()
        event_type = payload.get("event_type")
        print(f"[webhook_server] PayPal Signature OK. Event type: {event_type}")

        if event_type in ["CHECKOUT.ORDER.APPROVED", "PAYMENT.SALE.COMPLETED", "PAYMENT.CAPTURE.COMPLETED"]:
            try:
                await _handle_paypal_completed(payload.get("resource", {}))
            except Exception:
                print("[webhook_server] Error while handling PayPal event:")
                traceback.print_exc()

        return {"status": "ok"}

    # ------------------------------------------------------------------
    # 3. STRIPE WEBHOOK HANDLING
    # ------------------------------------------------------------------
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    if sig_header:
        print("[webhook_server] Processing Stripe Webhook...")
        try:
            event = stripe.Webhook.construct_event(raw_body, sig_header, webhook_secret)
        except (ValueError, stripe.error.SignatureVerificationError) as error:
            print(f"[webhook_server] REJECTED - Stripe signature verification failed: {error}")
            raise HTTPException(status_code=400, detail=f"Invalid webhook signature: {error}")

        print(f"[webhook_server] Stripe Signature OK. Event type: {event['type']}")

        if event["type"] == "checkout.session.completed":
            try:
                await _handle_checkout_completed(event["data"]["object"])
            except Exception:
                print("[webhook_server] Error while handling Stripe checkout.session.completed:")
                traceback.print_exc()

        return {"status": "ok"}

    raise HTTPException(status_code=400, detail="Unknown or unsupported webhook provider")


async def _handle_paypal_completed(resource: dict):
    if _bot is None:
        print("[webhook_server] Received PayPal payment but bot isn't attached yet.")
        return

    discord_user_id_raw = resource.get("custom_id") or resource.get("custom")
    payment_intent_id = resource.get("id")

    if not discord_user_id_raw:
        print(f"[webhook_server] Ignoring PayPal resource with missing custom_id: {resource!r}")
        return

    parts = str(discord_user_id_raw).split(":")
    discord_user_id = int(parts[0])
    tx_type = parts[1] if len(parts) > 1 else "write_slot"

    if tx_type == "write_slot":
        await _handle_write_slot_paid(discord_user_id)
    elif tx_type == "purchase" and len(parts) > 2:
        idea_id = int(parts[2])
        await deliver_purchase(_bot, idea_id, discord_user_id, payment_intent_id)
    elif tx_type == "random_purchase":
        await deliver_random_purchase(_bot, discord_user_id, payment_intent_id)
    else:
        await _handle_write_slot_paid(discord_user_id)


async def _handle_checkout_completed(session):
    if _bot is None:
        print("[webhook_server] Received a payment but the bot isn't attached yet - ignoring.")
        return

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
    user = await _bot.fetch_user(discord_user_id)

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