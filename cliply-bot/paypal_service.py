"""
paypal_service.py
------------------
Wraps PayPal's REST API (Orders v2): creating an order (the "$10
charge") and capturing it once the buyer approves.

PayPal's redirect flow works like this:
  1. We create an Order via PayPal's API - this returns an "approve"
     link (a URL on paypal.com).
  2. We send the buyer that link, same as we did with Stripe Checkout.
  3. The buyer approves the payment on PayPal's site.
  4. PayPal redirects their browser back to OUR return_url (a route in
     webhook_server.py), appending ?token=<order_id> (plus whatever of
     our own metadata we put on the return_url - see checkout_flow.py).
  5. THAT'S when the buyer actually gets charged - by us calling
     PayPal's Capture endpoint for that order_id. Approval alone
     doesn't move any money; capture is the step that does.

WHY THIS IS SAFE WITHOUT WEBHOOK SIGNATURE VERIFICATION: we are the
one calling PayPal's API to capture the order, authenticated with our
own client secret. There's no inbound "trust me, this happened"
request whose authenticity we need to verify - we're asking PayPal
directly, with our own credentials, whether this specific order is
real and what its status is. That's inherently more trustworthy than
verifying a signature on a request someone else sent us, and it's
what the previous IPN-based implementation was missing entirely.
"""

import base64
import os

import httpx

CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
MODE = os.getenv("PAYPAL_MODE", "sandbox").lower()  # "sandbox" or "live"

BASE_URL = "https://api-m.sandbox.paypal.com" if MODE == "sandbox" else "https://api-m.paypal.com"


def _require_credentials():
    if not CLIENT_ID or not CLIENT_SECRET:
        raise RuntimeError(
            "PAYPAL_CLIENT_ID / PAYPAL_CLIENT_SECRET are not set. Get them from "
            "developer.paypal.com/dashboard/applications - make sure you copy the "
            "ones for whichever mode (Sandbox or Live) PAYPAL_MODE is set to; "
            "sandbox and live apps have completely different credentials."
        )


async def _get_access_token() -> str:
    """
    PayPal's REST API requires an OAuth2 access token on every call,
    obtained via HTTP Basic Auth with your client ID/secret. Tokens
    expire after a few hours, so we fetch a fresh one on every request
    rather than caching - simpler, and the extra round trip is
    negligible next to the rest of a checkout flow.
    """
    _require_credentials()
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
        )
        response.raise_for_status()
        return response.json()["access_token"]


async def create_order(
    return_url: str,
    cancel_url: str,
    product_name: str,
    custom_id: str,
    amount_usd: str,
) -> tuple[str, str]:
    """
    Creates a PayPal Order for the given dollar amount. Returns
    (order_id, approve_url) - send the buyer to approve_url. PayPal
    redirects them to return_url (with ?token=<order_id> appended) once
    they approve, or to cancel_url if they back out without paying.

    amount_usd must be a string like "10.00" - PayPal's API wants the
    exact decimal string, not a float (floats can introduce rounding
    errors like 9.999999999 that PayPal will reject).
    """
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/v2/checkout/orders",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "custom_id": custom_id,
                        "description": product_name,
                        "amount": {"currency_code": "USD", "value": amount_usd},
                    }
                ],
                "application_context": {
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                    "user_action": "PAY_NOW",
                    "brand_name": "Cliply",
                    # This is the actual fix for buyers landing on a
                    # login screen: without it, PayPal defaults to
                    # LOGIN. "BILLING" tells it to open straight to the
                    # guest card-entry screen instead - completely
                    # separate from (and not dependent on) the legacy
                    # "PayPal account optional" toggle in classic
                    # Website Payments Preferences, which only applies
                    # to old-style _xclick buttons, not this API.
                    "landing_page": "BILLING",
                },
            },
        )
        response.raise_for_status()
        data = response.json()

    order_id = data["id"]
    approve_url = next(link["href"] for link in data["links"] if link["rel"] == "approve")
    return order_id, approve_url


async def capture_order(order_id: str) -> dict:
    """
    Actually charges the buyer for a previously-approved order. Raises
    httpx.HTTPStatusError if it can't be captured - most commonly
    because it was ALREADY captured before (e.g. the buyer reloaded the
    return_url page). Callers should treat that as a safe, expected
    case rather than an error to alarm over - PayPal itself is the one
    refusing to charge the same order twice.
    """
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/v2/checkout/orders/{order_id}/capture",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response.raise_for_status()
        return response.json()


async def refund_capture(capture_id: str):
    """
    Issues a full refund for a completed capture. Used when a purchase
    gets confirmed for something that's no longer available (e.g. a
    specific idea sold out to someone else while this payment was in
    progress) - see cogs/marketplace.py's deliver_purchase().
    """
    token = await _get_access_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{BASE_URL}/v2/payments/captures/{capture_id}/refund",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={},
        )
        response.raise_for_status()
        return response.json()