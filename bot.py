
import os
import random
import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, DARK, BLUE

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing!")

# keep this secret!

# ---------- THEME ----------
SPIDER_FACTS = [
    "Spider silk is stronger than steel of the same thickness. 🕸️",
    "Most spiders have 8 eyes, but some have 6, 4, 2, or even none. 🕷️",
    "Spiders aren't insects. They're arachnids with 8 legs. 🕷️",
    "Some spiders can 'fly' by ballooning on silk threads in the wind. 🌬️",
    "A spider's web can be recycled: many spiders eat their old web. 🕸️",
]

# ---------- BOT SETUP ----------
intents = discord.Intents.default()
intents.members = True  # needed for welcome messages (enable in Dev Portal too)
intents.message_content = True  # needed for AutoMod (enable "Message Content Intent" in Dev Portal too)

class Spidey(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)

    async def setup_hook(self):
        await self.load_extension("voicemaster")  # 🕷️ VoiceMaster
        await self.load_extension("moderation")   # 🛡️ Moderation
        await self.load_extension("utility")      # 🧰 Utility
        await self.load_extension("automod")      # 🤖 AutoMod
        await self.load_extension("help")         # 📖 Help (dropdown)
        await self.load_extension("economy")      # 💰 Economy (webs, shop)
        await self.load_extension("casino")       # 🎰 Casino (games)
        await self.load_extension("leveling")     # ⭐ Leveling (XP, ranks)
        await self.load_extension("welcome")      # 👋 Welcome & verification
        await self.load_extension("tickets")      # 🎫 Tickets
        await self.load_extension("minigames")    # 🎲 Mini-games (/game, /guess)
        await self.load_extension("actions")      # 🤗 Actions (/hug /slap /punch /dance, /interact, /react)
        await self.load_extension("ai_chat")      # 🧠 AI Chat (@Spidey <question>)
        await self.load_extension("dashboard")    # 🖥️ Web dashboard (load LAST)
        
        await self.tree.sync()

client = Spidey()
tree = client.tree


@client.event
async def on_ready():
    await client.change_presence(
        status=discord.Status.dnd,
        activity=discord.Activity(
            type=discord.ActivityType.watching, name="the web 🕸️"
        )
    )
    print(f"🕷️ Spidey is swinging as {client.user}")


# ---------- COMMANDS ----------
@tree.command(name="ping", description="Check how fast the web is vibrating")
async def ping(interaction: discord.Interaction):
    ms = ("2")
    await interaction.response.send_message(
        embed=web_embed("Web Vibration", f"My web is vibrating at **{ms}ms** 🕸️⚡")
    )


@tree.command(name="fact", description="Get a random spider fact")
async def fact(interaction: discord.Interaction):
    await interaction.response.send_message(
        embed=web_embed("Spider Fact", random.choice(SPIDER_FACTS), DARK)
    )


@tree.command(name="swing", description="Web-swing over to someone")
@app_commands.describe(user="Who do you want to swing to?")
async def swing(interaction: discord.Interaction, user: discord.Member):
    await interaction.response.send_message(
        embed=web_embed(
            "Thwip!",
            f"{interaction.user.mention} shot a web and swung to {user.mention}! 🕸️🕷️",
        )
    )


@tree.command(name="avatar", description="Show someone's avatar in a web frame")
async def avatar(interaction: discord.Interaction, user: discord.Member = None):
    user = user or interaction.user
    embed = web_embed(f"{user.display_name}'s Avatar", color=BLUE)
    embed.set_image(url=user.display_avatar.url)
    await interaction.response.send_message(embed=embed)


# ---------- THEMED ERRORS ----------
@tree.error
async def on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        text = "You don't have the permissions for that. 🕷️"
    elif isinstance(error, app_commands.BotMissingPermissions):
        text = "I'm missing permissions: " + ", ".join(error.missing_permissions)
    elif isinstance(error, app_commands.CommandOnCooldown):
        text = f"Slow down! Try again in {error.retry_after:.0f}s. 🕸️"
    elif isinstance(error, app_commands.CheckFailure):
        text = "You can't use that here. 🕸️"
    else:
        text = "Something went wrong. Try again in a sec 🕸️"
        print(f"Command error: {error!r}")
    embed = web_embed("Stuck in the Web!", text, DARK)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)

client.run(TOKEN)
