"""
bot.py
------
This is the entry point - the file you run with `python bot.py`.

What it does, in order:
  1. Loads secrets (token, channel ID, Stripe keys) from .env
  2. Creates the bot object
  3. Defines a simple /ping command
  4. Loads our cogs (write, marketplace, admin)
  5. Registers the persistent "Start Writing" button so it survives restarts
  6. Starts the Stripe webhook server (webhook_server.py) in the background
  7. Logs in and syncs slash commands with Discord
"""

import os
import asyncio

import discord
import uvicorn
from discord.ext import commands
from dotenv import load_dotenv

import database
import webhook_server
from cogs.write import StartWritingView

# load_dotenv() reads your .env file and makes its lines available
# through os.getenv(), just like real environment variables.
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
MARKETPLACE_CHANNEL_ID = int(os.getenv("MARKETPLACE_CHANNEL_ID"))
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8000"))

# TEST_GUILD_ID is optional. If you set it in .env, your slash commands
# will update INSTANTLY in that one server - great for development.
# Without it, commands still work, but can take up to an hour to show
# up everywhere (Discord's global command cache).
_test_guild_id = os.getenv("TEST_GUILD_ID")
TEST_GUILD = discord.Object(id=int(_test_guild_id)) if _test_guild_id else None

# "Intents" tell Discord which categories of events you want to receive.
# Since we're slash-commands-only, we don't need to read message content,
# so the defaults are enough.
intents = discord.Intents.default()

# commands.Bot is what gives us Cog support. command_prefix is required
# by the library but we'll never actually use "!" style commands.
bot = commands.Bot(command_prefix="!unused-", intents=intents)

# Stash the marketplace channel ID on the bot object itself, so any cog
# can reach it via `interaction.client.marketplace_channel_id`.
bot.marketplace_channel_id = MARKETPLACE_CHANNEL_ID


@bot.event
async def on_ready():
    """
    discord.py calls this automatically once, the moment the bot
    finishes connecting to Discord. This is where we do startup work.
    """
    print(f"Logged in as {bot.user} (id: {bot.user.id})")

    await database.init_db()
    print("Database ready.")

    # Registers the "Start Writing" button's custom_id globally so
    # clicking it keeps working even if the bot restarted between
    # payment and the user clicking it. Without this, a persistent
    # view (timeout=None) still SHOWS as clickable in Discord after a
    # restart, but clicking it would silently do nothing.
    bot.add_view(StartWritingView())

    if TEST_GUILD:
        # Copy our globally-registered commands into this one guild's
        # command list, then sync just that guild - this is what makes
        # updates appear instantly while you're developing.
        bot.tree.copy_global_to(guild=TEST_GUILD)
        synced = await bot.tree.sync(guild=TEST_GUILD)
        print(f"Synced {len(synced)} slash command(s) to test guild {TEST_GUILD.id}.")
    else:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s) globally (may take up to an hour to appear).")


@bot.tree.command(name="ping", description="Check if Cliply is online")
async def ping(interaction: discord.Interaction):
    """
    The simplest possible slash command. `ephemeral=True` means only
    the person who ran the command can see the reply - it won't clutter
    the channel for everyone else.
    """
    await interaction.response.send_message("🏓 Pong! Cliply is online.", ephemeral=True)


async def main():
    async with bot:
        # Both cogs need to be loaded so their setup() functions run and
        # register their slash commands with the bot. write.py imports
        # BuyButtonView from marketplace.py at the top of its file, but
        # that's a separate, ordinary Python import - it doesn't require
        # marketplace.py to be loaded as an extension first.
        await bot.load_extension("cogs.marketplace")
        await bot.load_extension("cogs.write")
        await bot.load_extension("cogs.admin")

        # Give webhook_server.py a reference to this bot so it can DM
        # users once Stripe confirms a payment.
        webhook_server.attach_bot(bot)

        # Run the FastAPI webhook app with uvicorn's Server class
        # directly (instead of the usual `uvicorn app:app` command line)
        # so it runs as a background task INSIDE this same asyncio event
        # loop, alongside the bot, instead of as a separate process.
        #
        # host="0.0.0.0" (not "127.0.0.1"!) is important: 127.0.0.1 only
        # accepts connections from inside the exact same container/machine.
        # On a platform like Railway, the public proxy connects from
        # OUTSIDE the container over the network - with host="127.0.0.1"
        # it would never be able to reach this server at all, even
        # though everything looks "Active" and healthy. 0.0.0.0 means
        # "accept connections on any network interface," which works
        # correctly both locally and in production.
        config = uvicorn.Config(webhook_server.app, host="0.0.0.0", port=WEBHOOK_PORT, log_level="info")
        server = uvicorn.Server(config)
        asyncio.create_task(server.serve())
        print(f"Webhook server listening on http://0.0.0.0:{WEBHOOK_PORT}/webhook")

        await bot.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())