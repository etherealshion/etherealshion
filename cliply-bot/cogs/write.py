"""
cogs/write.py
-------------
This file is a "Cog" - discord.py's way of grouping related commands
into their own file/class instead of dumping everything into bot.py.
This cog owns the entire /write flow:

  /write
    -> bot DMs a Stripe Checkout payment link
    -> [Stripe webhook confirms payment - see webhook_server.py]
    -> bot DMs a "Start Writing" button
    -> a dropdown to pick a category
    -> THEN the modal popup: Title / Description
    -> Publish / Rewrite / Discard buttons

Why the category is picked BEFORE the modal, as a separate step:
Discord modals can only contain text-input fields - there's no way to
put a dropdown (select menu) inside one. So picking a category has to
happen as its own interaction first; the dropdown-selection click that
results from it is what's allowed to open the modal afterward.

Publish posts an embed with a real, working Buy button (defined in
cogs/marketplace.py, imported below) into whichever channel matches
the idea's category (see categories.py), instead of one shared
#marketplace channel.
"""

import discord
from discord import app_commands
from discord.ext import commands

import checkout_flow
import database
from categories import CATEGORY_CHANNELS
from utils import PRICE as WRITE_SLOT_PRICE, make_preview, is_free_publisher
from cogs.marketplace import BuyButtonView
from cogs.roles import role_name_for_category


# ---------------------------------------------------------------------------
# Step 1: payment. /write sends a Stripe Checkout link; nothing is
# unlocked until webhook_server.py confirms the payment succeeded.
# ---------------------------------------------------------------------------

class StartWritingView(discord.ui.View):
    """
    DM'd to a writer once webhook_server.py confirms their write-slot
    payment. Clicking this is a FRESH Discord interaction, which is
    exactly what lets us respond with something interactive next - a
    webhook arriving minutes after payment has no live interaction of
    its own to work with.

    timeout=None + a fixed custom_id makes this a "persistent" view:
    it keeps working even if the bot restarts between payment and
    click, as long as bot.py registers it once via bot.add_view() on
    startup (see bot.py's on_ready).
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="✏️ Start Writing", style=discord.ButtonStyle.green, custom_id="cliply_start_writing")
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            "Which category is your idea for?",
            view=CategorySelectView(),
        )


# ---------------------------------------------------------------------------
# Step 2: pick a category from a dropdown
# ---------------------------------------------------------------------------

class CategorySelect(discord.ui.Select):
    def __init__(self):
        options = [discord.SelectOption(label=name) for name in CATEGORY_CHANNELS.keys()]
        super().__init__(placeholder="Choose a category...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        # self.values[0] is whichever option the writer picked. Selecting
        # a dropdown option is itself a fresh interaction, so we're
        # allowed to respond to it by opening the modal directly.
        category = self.values[0]
        await interaction.response.send_modal(WriteIdeaModal(category=category))


class CategorySelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.add_item(CategorySelect())


# ---------------------------------------------------------------------------
# Step 3: the idea submission modal - title and description only now,
# category was already picked in the previous step.
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
    description = discord.ui.TextInput(
        label="Description",
        style=discord.TextStyle.paragraph,  # paragraph = multi-line box
        placeholder="Describe the full idea in detail - this is what the buyer receives.",
        min_length=50,
        max_length=1500,
    )

    def __init__(self, category: str):
        super().__init__()
        # Carried over from the dropdown step so on_submit knows which
        # category this idea belongs to, and so Rewrite (below) can
        # reopen the modal without asking for the category again.
        self.category = category

    async def on_submit(self, interaction: discord.Interaction):
        """Called automatically when the user clicks Submit on the modal."""
        preview = make_preview(str(self.description))

        idea_id = await database.create_idea(
            writer_id=interaction.user.id,
            title=str(self.idea_title),
            category=self.category,
            full_text=str(self.description),
            preview_text=preview,
        )

        await database.increment_user_stat(interaction.user.id, "ideas_written")

        embed = discord.Embed(
            title=str(self.idea_title),
            description=str(self.description),
            color=discord.Color.blurple(),
        )
        embed.add_field(name="Category", value=self.category, inline=True)
        embed.set_footer(text="Review your draft, then choose what to do next.")

        await interaction.response.send_message(
            content="Here's your draft. What would you like to do?",
            embed=embed,
            view=DraftDecisionView(idea_id=idea_id, category=self.category),
            ephemeral=True,
        )


# ---------------------------------------------------------------------------
# Step 4: Publish / Rewrite / Discard
# ---------------------------------------------------------------------------

class DraftDecisionView(discord.ui.View):
    """
    Holds the three buttons shown after a draft is created. We store
    idea_id AND category on the view itself - idea_id so every button
    knows which idea it's acting on, and category so Rewrite can reopen
    the modal without making the writer pick a category again.
    """

    def __init__(self, idea_id: int, category: str):
        super().__init__(timeout=600)  # 10 minutes to decide
        self.idea_id = idea_id
        self.category = category

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

        # Route to the channel that matches this idea's category, instead
        # of one shared #marketplace channel. Falls back to the bot's
        # general marketplace_channel_id if the category somehow isn't
        # in CATEGORY_CHANNELS (shouldn't normally happen, since the
        # category came from that exact dict's keys via the dropdown -
        # this only matters if categories.py was edited AFTER this idea
        # was drafted).
        target_channel_id = CATEGORY_CHANNELS.get(idea["category"], interaction.client.marketplace_channel_id)
        channel = interaction.client.get_channel(target_channel_id)
        if channel is None:
            await interaction.response.edit_message(
                content=(
                    f"⚠️ Published, but I couldn't find the channel for category "
                    f"'{idea['category']}'. Check categories.py - that channel ID "
                    "may be wrong, or the bot may not have access to it."
                ),
                embed=None,
                view=None,
            )
            return

        await channel.send(embed=marketplace_embed, view=BuyButtonView(idea_id=self.idea_id))

        # Ping the category's "notify me" role if it exists (created via
        # /setup-roles - see cogs/roles.py). Posted as a SEPARATE plain
        # message rather than the content of the embed message above, so
        # the embed itself stays clean and this ping can be safely
        # skipped entirely if the role doesn't exist yet.
        role = discord.utils.get(channel.guild.roles, name=role_name_for_category(idea["category"]))
        if role is not None:
            await channel.send(content=f"{role.mention} a new idea just dropped! 👆")

        await interaction.response.edit_message(
            content=f"✅ Published to {channel.mention}!",
            embed=None,
            view=None,
        )

    @discord.ui.button(label="Rewrite", style=discord.ButtonStyle.blurple, emoji="✏️")
    async def rewrite(self, interaction: discord.Interaction, button: discord.ui.Button):
        # The write slot was already paid for, so rewriting just deletes
        # this draft and reopens the modal - no second charge, and no
        # need to pick the category again since we kept it on self.
        await database.delete_idea(self.idea_id)
        await interaction.response.send_modal(WriteIdeaModal(category=self.category))

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

        if is_free_publisher(interaction.user):
            # Owner/mods skip Stripe entirely - straight to category
            # selection, same next step a paying writer reaches only
            # AFTER their payment webhook confirms (see StartWritingView).
            await interaction.response.send_message(
                "✅ Free write slot (owner/mod) - which category is your idea for?",
                view=CategorySelectView(),
                ephemeral=True,
            )
            return

        await checkout_flow.send_checkout_link(
            interaction,
            tx_type="write_slot",
            product_name="Cliply Write Slot",
        )


async def setup(bot: commands.Bot):
    """discord.py calls this automatically when bot.load_extension('cogs.write') runs."""
    await bot.add_cog(WriteCog(bot))