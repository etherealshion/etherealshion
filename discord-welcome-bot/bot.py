import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import database
import image_gen

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True  # REQUIRED for join events - must also enable in Discord Developer Portal

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    await database.init_db()
    await bot.load_extension("cogs.config")
    synced = await bot.tree.sync()
    print(f"Logged in as {bot.user} | Synced {len(synced)} commands")

@bot.tree.command(name="ping", description="Check if the bot is alive")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! {round(bot.latency * 1000)}ms")

@bot.event
async def on_member_join(member: discord.Member):
    config = await database.get_config(member.guild.id)
    channel_id = config["welcome_channel_id"]
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if not channel:
        return

    card = await image_gen.generate_card(member, member.guild.member_count, kind="welcome")
    file = discord.File(card, filename="welcome.png")
    embed = discord.Embed(color=discord.Color.green())
    embed.set_image(url="attachment://welcome.png")
    await channel.send(content=f"{member.mention}", embed=embed, file=file)

bot.run(TOKEN)