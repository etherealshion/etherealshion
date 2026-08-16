"""
cogs/marketplace.py
--------------------
This cog owns everything about BUYING an idea, and browsing/dashboard
commands for both writers and editors:

  BuyButton / BuyButtonView   - kicks off a Stripe Checkout for one
                                 specific idea, reused everywhere a Buy
                                 button appears
  deliver_purchase /
  deliver_random_purchase     - called by webhook_server.py once
                                 Stripe confirms payment; these do the
                                 actual unlocking and DM the buyer
  /marketplace  - browse currently published ideas and buy one directly
  /random       - pay $10 for a random published idea, no browsing
  /myideas      - a writer's dashboard: drafts / published / sold
  /mypurchases  - an editor's dashboard: full text of everything bought

Note: cogs/write.py imports BuyButtonView from this file (that's why
Publish can attach a real, working Buy button to its marketplace post).
This file does NOT import anything from write.py, which avoids a
circular import.
"""

import discord
from discord import app_commands
from discord.ext import commands

import checkout_flow
import database
import paypal_service
from utils import PRICE, is_free_publisher


# ---------------------------------------------------------------------------
# Delivery logic - called by webhook_server.py's /paypal/capture route
# once PayPal confirms a purchase payment actually went through. There's
# no live Discord interaction at this point (payment can be confirmed
# minutes after the button click, in a completely separate browser
# request), so delivery happens via DM instead of an interaction reply.
# ---------------------------------------------------------------------------

async def deliver_purchase(bot: discord.Client, idea_id: int, buyer_id: int, capture_id: str | None) -> bool:
    """
    Finishes buying ONE SPECIFIC idea (used for /marketplace purchases).

    Uses the same atomic database.try_buy_idea() as before - even
    though the buyer already paid, we still need this check: the idea
    could have sold out to someone else in the time between them
    clicking Buy and completing PayPal approval. If that happens, we
    automatically refund the capture rather than leaving them charged
    with nothing to show for it.
    """
    success = await database.try_buy_idea(idea_id, buyer_id)
    buyer = await bot.fetch_user(buyer_id)

    if not success:
        if capture_id:
            await paypal_service.refund_capture(capture_id)
        await buyer.send(
            "😕 Sorry — that idea sold out right as your payment went through. "
            "You've been automatically refunded. Try /marketplace or /random again!"
        )
        return False

    idea = await database.get_idea(idea_id)
    await database.increment_user_stat(idea["writer_id"], "ideas_sold")
    await database.increment_user_stat(buyer_id, "ideas_purchased")
    await database.log_transaction(
        user_id=buyer_id,
        tx_type="purchase",
        amount=PRICE,
        idea_id=idea_id,
        payment_status="confirmed",
    )

    embed = discord.Embed(title=idea["title"], description=idea["full_text"], color=discord.Color.green())
    embed.add_field(name="Category", value=idea["category"], inline=True)
    embed.set_footer(text="✅ Payment confirmed - this idea is now yours.")
    await buyer.send(embed=embed)
    return True


async def deliver_random_purchase(bot: discord.Client, buyer_id: int, capture_id: str | None):
    """
    Finishes a /random purchase. We deliberately wait until THIS moment
    (payment confirmed) to pick which idea the buyer gets, rather than
    picking one back when they first ran /random - that would require
    "reserving" it for however long checkout takes, adding a lot of
    complexity (reservation timeouts, releasing abandoned reservations,
    etc.) for little benefit. Picking fresh at confirm-time is simpler
    and just as fair.
    """
    idea = await database.get_random_published_idea()
    buyer = await bot.fetch_user(buyer_id)

    if idea is None:
        if capture_id:
            await paypal_service.refund_capture(capture_id)
        await buyer.send(
            "😕 Sorry — the marketplace emptied out right as your payment went through. "
            "You've been automatically refunded. Try again once more ideas are published!"
        )
        return

    await deliver_purchase(bot, idea["idea_id"], buyer_id, capture_id)


# ---------------------------------------------------------------------------
# Instant delivery for owner/mods - used ONLY by the free path below.
# Delivers via DM, same as a normal paid purchase does (deliver_purchase
# above) - the only difference is WHEN it happens: instantly on click,
# instead of waiting for a webhook to confirm a payment that never
# needs to occur for the owner/mods in the first place.
# ---------------------------------------------------------------------------

async def _deliver_purchase_instantly(interaction: discord.Interaction, idea_id: int) -> bool:
    success = await database.try_buy_idea(idea_id, interaction.user.id)
    if not success:
        await interaction.followup.send("😕 That idea is no longer available.", ephemeral=True)
        return False

    idea = await database.get_idea(idea_id)
    await database.increment_user_stat(idea["writer_id"], "ideas_sold")
    await database.increment_user_stat(interaction.user.id, "ideas_purchased")
    await database.log_transaction(
        user_id=interaction.user.id,
        tx_type="purchase",
        amount=0,
        idea_id=idea_id,
        payment_status="free_owner_mod",
    )

    embed = discord.Embed(title=idea["title"], description=idea["full_text"], color=discord.Color.green())
    embed.add_field(name="Category", value=idea["category"], inline=True)
    embed.set_footer(text="✅ Free purchase (owner/mod) - this idea is now yours.")

    try:
        await interaction.user.send(embed=embed)
        await interaction.followup.send("✅ Free purchase (owner/mod) - check your DMs!", ephemeral=True)
    except discord.Forbidden:
        # Their DMs are closed to server members - fall back to showing
        # it right here instead, same fallback used in checkout_flow.py.
        await interaction.followup.send(embed=embed, ephemeral=True)

    return True


# ---------------------------------------------------------------------------
# Buy buttons - these only START checkout. Nothing is unlocked here.
# ---------------------------------------------------------------------------

class BuyButton(discord.ui.Button):
    """
    A single reusable Buy button tied to one idea_id. We build this as
    its own class (rather than a @discord.ui.button-decorated method)
    so the exact same button logic can be dropped into different views:
    the persistent one-button view on a #marketplace channel post, AND
    the multi-button view /marketplace sends when someone browses.

    Clicking this does NOT buy the idea - it only sends a payment link
    (unless you're the owner or a mod - see is_free_publisher, in which
    case it reveals the idea immediately, right in this same click, no
    payment and no DM needed). For a normal paying buyer, the idea is
    actually marked sold later, by deliver_purchase() above, once
    webhook_server.py confirms the payment succeeded.
    """

    def __init__(self, idea_id: int):
        super().__init__(
            label=f"Buy #{idea_id} (${PRICE})",
            style=discord.ButtonStyle.green,
            emoji="🛒",
            # custom_id must be unique and stable so persistent views
            # (timeout=None) keep working correctly even after a bot restart.
            custom_id=f"buy_{idea_id}",
        )
        self.idea_id = idea_id

    async def callback(self, interaction: discord.Interaction):
        if is_free_publisher(interaction.user):
            # No payment, no DM - deliver right here, in this response.
            await interaction.response.defer(ephemeral=True, thinking=True)
            await _deliver_purchase_instantly(interaction, self.idea_id)
            return

        await checkout_flow.send_checkout_link(
            interaction,
            tx_type="purchase",
            product_name=f"Cliply Idea #{self.idea_id}",
            idea_id=self.idea_id,
        )


class BuyButtonView(discord.ui.View):
    """
    Wraps a single BuyButton. timeout=None makes it "persistent" - it
    keeps working even after the bot restarts, which matters because a
    marketplace post might sit around for days before someone buys it.
    """

    def __init__(self, idea_id: int):
        super().__init__(timeout=None)
        self.add_item(BuyButton(idea_id))


# ---------------------------------------------------------------------------
# /random - also just starts checkout; which idea you get is decided
# later, once payment is confirmed (see deliver_random_purchase above).
# ---------------------------------------------------------------------------

class ConfirmRandomPurchaseView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label=f"Get Payment Link (${PRICE})", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if is_free_publisher(interaction.user):
            await interaction.response.defer(ephemeral=True, thinking=True)
            idea = await database.get_random_published_idea()
            if idea is None:
                await interaction.followup.send("The marketplace is empty right now.", ephemeral=True)
                return
            await _deliver_purchase_instantly(interaction, idea["idea_id"])
            return

        await checkout_flow.send_checkout_link(
            interaction,
            tx_type="random_purchase",
            product_name="Cliply Random Idea",
        )


# ---------------------------------------------------------------------------
# The Cog
# ---------------------------------------------------------------------------

class MarketplaceCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="marketplace", description="Browse published ideas and buy one")
    async def marketplace(self, interaction: discord.Interaction):
        await database.ensure_user(interaction.user.id, interaction.user.display_name)

        ideas = await database.get_published_ideas(limit=10)
        if not ideas:
            await interaction.response.send_message(
                "The marketplace is empty right now - check back soon!",
                ephemeral=True,
            )
            return

        embeds = []
        view = discord.ui.View(timeout=180)
        for idea in ideas:
            embed = discord.Embed(
                title=idea["title"],
                description=idea["preview_text"],
                color=discord.Color.gold(),
            )
            embed.add_field(name="Category", value=idea["category"], inline=True)
            embed.add_field(name="Price", value=f"${idea['price']}", inline=True)
            embed.set_footer(text=f"Idea #{idea['idea_id']}")
            embeds.append(embed)
            view.add_item(BuyButton(idea_id=idea["idea_id"]))

        await interaction.response.send_message(embeds=embeds, view=view, ephemeral=True)

    @app_commands.command(name="random", description=f"Pay ${PRICE} for a random published idea")
    async def random_idea(self, interaction: discord.Interaction):
        await database.ensure_user(interaction.user.id, interaction.user.display_name)

        # This is just a courtesy check so we don't send someone a payment
        # link when the marketplace is obviously empty. It's not a
        # guarantee - the marketplace could still empty out between now
        # and payment confirming - which is exactly why
        # deliver_random_purchase() in this file re-checks and
        # auto-refunds if that happens.
        preview_idea = await database.get_random_published_idea()
        if preview_idea is None:
            await interaction.response.send_message(
                "The marketplace is empty right now - check back soon!",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            content=(
                f"Confirm ${PRICE} for a **random** published idea. "
                "Which one you get is decided the moment your payment is confirmed, "
                "so you won't know until then."
            ),
            view=ConfirmRandomPurchaseView(),
            ephemeral=True,
        )

    @app_commands.command(name="myideas", description="Your dashboard as a writer: drafts, published, and sold")
    async def myideas(self, interaction: discord.Interaction):
        await database.ensure_user(interaction.user.id, interaction.user.display_name)

        ideas = await database.get_ideas_by_writer(interaction.user.id)
        if not ideas:
            await interaction.response.send_message(
                "You haven't written anything yet - try /write!",
                ephemeral=True,
            )
            return

        drafts = [i for i in ideas if i["status"] == "draft"]
        published = [i for i in ideas if i["status"] == "published"]
        sold = [i for i in ideas if i["status"] == "sold"]

        embed = discord.Embed(title="📋 Your Writer Dashboard", color=discord.Color.blurple())

        if drafts:
            lines = [f"• {i['title']}" for i in drafts]
            embed.add_field(name=f"📝 Drafts ({len(drafts)})", value="\n".join(lines), inline=False)

        if published:
            lines = [f"• {i['title']} - ${i['price']} (#{i['idea_id']})" for i in published]
            embed.add_field(name=f"🟡 Published, unsold ({len(published)})", value="\n".join(lines), inline=False)

        if sold:
            lines = [f"• {i['title']} - sold for ${i['price']}" for i in sold]
            embed.add_field(name=f"✅ Sold ({len(sold)})", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"Total written: {len(ideas)}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="mypurchases", description="Everything you've bought, with full text")
    async def mypurchases(self, interaction: discord.Interaction):
        await database.ensure_user(interaction.user.id, interaction.user.display_name)

        purchases = await database.get_purchases_by_buyer(interaction.user.id)
        if not purchases:
            await interaction.response.send_message(
                "You haven't bought anything yet - try /marketplace or /random!",
                ephemeral=True,
            )
            return

        embeds = []
        for idea in purchases[:10]:  # Discord allows at most 10 embeds per message
            embed = discord.Embed(
                title=idea["title"],
                description=idea["full_text"],
                color=discord.Color.green(),
            )
            embed.add_field(name="Category", value=idea["category"], inline=True)
            embeds.append(embed)

        await interaction.response.send_message(embeds=embeds, ephemeral=True)


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.marketplace') runs."""
    await bot.add_cog(MarketplaceCog(bot))