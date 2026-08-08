"""
cogs/write.py
-------------
This file is a "Cog" - discord.py's way of grouping related commands
into their own file/class instead of dumping everything into bot.py.
This cog owns the entire /write flow:

  /write
    -> bot DMs a Stripe Checkout payment link
    -> [Stripe webhook confirms payment - see webhook_server.py]
    -> bot DMs a "Start Writing" button -> opens the modal
    -> Modal popup: Title / Category / Description
    -> Publish / Rewrite / Discard buttons

Publish posts an embed with a real, working Buy button (defined in
cogs/marketplace.py, imported below) into the #marketplace channel.
"""

import discord
from discord import app_commands
from discord.ext import commands

import checkout_flow
import database
from utils import PRICE as WRITE_SLOT_PRICE, make_preview
from cogs.marketplace import BuyButtonView


# ---------------------------------------------------------------------------
# Step 1: payment. /write sends a Stripe Checkout link; nothing is
# unlocked until webhook_server.py confirms the payment succeeded.
# ---------------------------------------------------------------------------

class StartWritingView(discord.ui.View):
    """
    DM'd to a writer once webhook_server.py confirms their write-slot
    payment. Clicking this is a FRESH Discord interaction, which is
    exactly what lets us open the modal - modals can only be sent as
    the first response to an interaction, and a webhook arriving
    minutes after payment isn't one.

    timeout=None + a fixed custom_id makes this a "persistent" view:
    it keeps working even if the bot restarts between payment and
    click, as long as bot.py registers it once via bot.add_view() on
    startup (see bot.py's on_ready).
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✏️ Start Writing", style=discord.ButtonStyle.green, custom_id="cliply_start_writing")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(WriteIdeaModal())


# ---------------------------------------------------------------------------
# Step 2: the idea submission modal
# ---------------------------------------------------------------------------

class WriteIdeaModal(discord.ui.Modal, title="Submit Your Idea"):
    """
    discord.ui.Modal describes a popup form. Each discord.ui.TextInput
    below becomes one field on that form. Discord handles all the
    rendering - we just declare what fields we want.
    """

    idea_title = discord.ui.TextInput(
        label="Title",
        placeholder="A short, catchy title for your idea",
        max_length=100,
    )
    category = discord.ui.TextInput(
        label="Category",
        placeholder="e.g. Comedy, Tutorial, Vlog, Gaming...",
        max_length=50,
    )
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,  # paragraph = multi-line box
        placeholder="Describe the full idea in detail - this is what the buyer receives.",
        min_length=50,
        max_length=1500,
    )

    async def on_submit(self, interaction: discord.Interaction):
        """Called automatically when the user clicks Submit on the modal."""
        preview = make_preview(str(self.description))

        idea_id = await database.create_idea(
            writer_id=interaction.user.id,
            title=str(self.idea_title),
            category=str(self.category),
            full_text=str(self.description),
            preview_text=preview,
        )

        await database.increment_user_stat(interaction.user.id, "ideas_written")

        embed = discord.Embed(
            title=str(self.idea_title),
            description=str(self.description),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Category", value=str(self.category), inline=True)
        embed.set_footer(text="Review your draft, then choose what to do next.")

        await interaction.response.send_message(
            content="Here's your draft. What would you like to do?",
            embed=embed,
            view=DraftDecisionView(idea_id=idea_id),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Step 3: Publish / Rewrite / Discard
# ---------------------------------------------------------------------------

class DraftDecisionView(discord.ui.View):
    """
    Holds the three buttons shown after a draft is created. We store
    idea_id on the view itself so every button's callback knows which
    idea it's acting on.
    """

    def __init__(self, idea_id: int):
        super().__init__(timeout=600)  # 10 minutes to decide
        self.idea_id = idea_id

    @discord.ui.button(label="Publish", style=discord.ButtonStyle.green, emoji="✅")
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        idea = await database.get_idea(self.idea_id)
        if idea is None:
            await interaction.response.send_message("That draft no longer exists.", ephemeral=True)
            return

        await database.set_idea_status(self.idea_id, "published", timestamp_field="published_at")
        await database.increment_user_stat(interaction.user.id, "ideas_published")

        marketplace_embed = discord.Embed(
            title=idea["title"],
            description=idea["preview_text"],
            color=discord.Color.gold(),
        )
        marketplace_embed.add_field(name="Category", value=idea["category"], inline=True)
        marketplace_embed.add_field(name="Price", value=f"${idea['price']}", inline=True)
        marketplace_embed.set_footer(text=f"Idea #{self.idea_id} · Full text revealed to the buyer only")

        channel = interaction.client.get_channel(interaction.client.marketplace_channel_id)
        if channel is None:
            await interaction.response.edit_message(
                content="⚠️ Published, but I couldn't find the #marketplace channel. "
                        "Check MARKETPLACE_CHANNEL_ID in your .env.",
                embed=None,
                view=None,
            )
            return

        await channel.send(embed=marketplace_embed, view=BuyButtonView(idea_id=self.idea_id))

        await interaction.response.edit_message(
            content=f"✅ Published to {channel.mention}!",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Rewrite", style=discord.ButtonStyle.blurple, emoji="✏️")
    async def rewrite(self, interaction: discord.Interaction, button: discord.ui.Button):
        # The write slot was already paid for, so rewriting just deletes
        # this draft and reopens a blank modal - no second charge.
        await database.delete_idea(self.idea_id)
        await interaction.response.send_modal(WriteIdeaModal())

    @discord.ui.button(label="Discard", style=discord.ButtonStyle.red, emoji="🗑️")
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button):
        await database.delete_idea(self.idea_id)
        await interaction.response.edit_message(
            content="🗑️ Draft discarded.",
            embed=None,
            view=None,
        )


# ---------------------------------------------------------------------------
# The Cog itself - this is what bot.py loads via load_extension
# ---------------------------------------------------------------------------

class WriteCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="write", description=f"Pay ${WRITE_SLOT_PRICE} for a slot to submit an idea")
    async def write(self, interaction: discord.Interaction):
        await database.ensure_user(interaction.user.id, interaction.user.display_name)
        await checkout_flow.send_checkout_link(
            interaction,
            tx_type="write_slot",
            product_name="Cliply Write Slot",
        )


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.write') runs."""
    await bot.add_cog(WriteCog(bot))