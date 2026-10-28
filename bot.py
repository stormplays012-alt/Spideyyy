
import os
import random
import discord
from discord import app_commands
from discord.ext import commands

from theme import cozy_embed, PEACH, NIGHT, LAVENDER

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing!")

# keep this secret!

# ---------- THEME ----------
COZY_FACTS = [
    "\"Hamsafar\" means \"fellow traveler\" in Urdu, Hindi and Persian. 🧳",
    "Moonlight is just sunlight bouncing off the Moon. The Moon makes no light of its own. 🌙",
    "The smell of rain on dry earth has a name: petrichor. 🌧️",
    "Tea is the most-drunk beverage in the world after water. 🍵",
    "Sea otters sometimes hold hands while they sleep so they don't drift apart. 🦦",
    "Fireflies glow with almost no heat. It's called bioluminescence. ✨",
    "A group of owls is called a parliament. 🦉",
    "Sunlight takes about 8 minutes to travel to Earth. ☀️",
    "Honey found in ancient Egyptian tombs was still edible after thousands of years. 🍯",
    "Masala chai is simmered with milk, sugar and warm spices like cardamom and ginger. ☕",
]

# ---------- BOT SETUP ----------
intents = discord.Intents.default()
intents.members = True  # needed for welcome messages (enable in Dev Portal too)
intents.message_content = True  # needed for AutoMod (enable "Message Content Intent" in Dev Portal too)

class Hamsafar(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self):
        await self.load_extension("voicemaster")  # 🕯️ VoiceMaster (cozy nooks)
        await self.load_extension("moderation")   # 🛡️ Moderation
        await self.load_extension("utility")      # 🧰 Utility
        await self.load_extension("automod")      # 🤖 AutoMod
        await self.load_extension("help")         # 📖 Help (dropdown)
        await self.load_extension("economy")      # ✨ Economy (stardust, shop)
        await self.load_extension("casino")       # 🎰 Casino (games)
        await self.load_extension("leveling")     # ⭐ Leveling (XP, ranks)
        await self.load_extension("welcome")      # 👋 Welcome & verification
        await self.load_extension("tickets")      # 🎫 Tickets
        await self.load_extension("minigames")    # 🎲 Mini-games (/game, /guess)
        await self.load_extension("actions")      # 🤗 Actions (/hug /slap /punch /dance, /interact, /react)
        await self.load_extension("dashboard")    # 🖥️ Web dashboard (load LAST)

        await self.tree.sync()

client = Hamsafar()
tree = client.tree


@client.event
async def on_ready():
    # "idle" shows Discord's little crescent-moon status, which fits the vibe
    await client.change_presence(
        status=discord.Status.idle,
        activity=discord.Activity(
            type=discord.ActivityType.listening, name="lofi beats ☕"
        )
    )
    print(f"🌙 Hamsafar is online as {client.user}")


# ---------- COMMANDS ----------
@tree.command(name="ping", description="Check how gentle the breeze is blowing")
async def ping(interaction: discord.Interaction):
    ms = round(client.latency * 1000)
    await interaction.response.send_message(
        embed=cozy_embed("Gentle Breeze", f"The breeze is moving at **{ms}ms** 🍃")
    )


@tree.command(name="fact", description="Get a random cozy fact")
async def fact(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=cozy_embed("Cozy Fact", random.choice(COZY_FACTS), NIGHT)
    )


@tree.command(name="chai", description="Share a warm cup of chai with someone")
@app_commands.describe(user="Who do you want to share a cup with?")
async def chai(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.send_message(
        embed=cozy_embed(
            "Chai Time!",
            f"{interaction.user.mention} handed {user.mention} a steaming cup of chai ☕✨",
        )
    )


@tree.command(name="avatar", description="Show someone's avatar in a cozy frame")
async def avatar(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = cozy_embed(f"{user.display_name}'s Avatar", color=LAVENDER)
    embed.set_image(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


# ---------- THEMED ERRORS ----------
@tree.error
async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        text = "You don't have the permissions for that, friend. 🌙"
    elif isinstance(error, app_commands.BotMissingPermissions):
        text = "I'm missing permissions: " + ", ".join(error.missing_permissions)
    elif isinstance(error, app_commands.CommandOnCooldown):
        text = f"Take a breath, then try again in {error.retry_after:.0f}s. 🍵"
    elif isinstance(error, app_commands.CheckFailure):
        text = "You can't use that here. ☁️"
    else:
        text = "Something went a little wrong. Try again in a sec 🍂"
        print(f"Command error: {error!r}")
    embed = cozy_embed("A Little Detour", text, NIGHT)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

client.run(TOKEN)
