"""
Actions: /hug /slap /punch /dance as quick commands, plus /action for 40+ more.

GIFs come from the free nekos.best API (waifu.pics is the backup), fetched live, so no
files are stored and nothing needs an API key. If both are down the action still
posts, just without a GIF.

Why one /action command instead of 40 separate ones: Discord allows only 100 global
slash commands per bot and Spidey is already close to that limit.
"""
import random
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK

NEKOS = "https://nekos.best/api/v2/{}"
WAIFU = "https://api.waifu.pics/sfw/{}"

# key: (emoji, label, verb used with a target, text used when alone or None, nekos.best category, waifu.pics category)
# `alone` None means the action needs a target.
ACTIONS: dict[str, tuple] = {
    # --- affection
    "hug":       ("🤗", "Hug",         "hugs",                None, "hug",       "hug"),
    "cuddle":    ("🥰", "Cuddle",      "cuddles",             None, "cuddle",    "cuddle"),
    "kiss":      ("😘", "Kiss",        "kisses",              None, "kiss",      "kiss"),
    "peck":      ("😚", "Peck",        "gives a little peck to", None, "peck",    None),
    "pat":       ("👋", "Pat",         "pats",                None, "pat",       "pat"),
    "handhold":  ("🤝", "Hold Hands",  "holds hands with",    None, "handhold",  "handhold"),
    "handshake": ("🤝", "Handshake",   "shakes hands with",   None, "handshake", None),
    "highfive":  ("🙌", "High Five",   "high-fives",          None, "highfive",  "highfive"),
    "feed":      ("🍙", "Feed",        "feeds",               None, "feed",      None),
    "nom":       ("😋", "Nom",         "nom-noms",            None, "nom",       "nom"),
    # --- playful / teasing
    "poke":      ("👉", "Poke",        "pokes",               None, "poke",      "poke"),
    "tickle":    ("🤭", "Tickle",      "tickles",             None, "tickle",    None),
    "bite":      ("😬", "Bite",        "bites",               None, "bite",      "bite"),
    "stare":     ("👀", "Stare",       "stares at",           None, "stare",     None),
    "lurk":      ("🫣", "Lurk",        "lurks behind",        None, "lurk",      None),
    "baka":      ("💢", "Baka",        "calls a baka:",       None, "baka",      None),
    "bonk":      ("🔨", "Bonk",        "bonks",               None, None,        "bonk"),
    # --- violence (cartoon)
    "slap":      ("✋", "Slap",        "slaps",               None, "slap",      "slap"),
    "punch":     ("👊", "Punch",       "punches",             None, "punch",     None),
    "kick":      ("🦵", "Kick",        "kicks",               None, "kick",      "kick"),
    "shoot":     ("🔫", "Shoot",       "shoots",              None, "shoot",     None),
    "yeet":      ("🚀", "Yeet",        "yeets",               None, "yeet",      "yeet"),
    # --- can be done with or without someone
    "wave":      ("👋", "Wave",        "waves at",            "waves hello to everyone", "wave",  "wave"),
    "wink":      ("😉", "Wink",        "winks at",            "winks",  "wink",   "wink"),
    "dance":     ("💃", "Dance",       "dances with",         "dances",  "dance",  "dance"),
    "laugh":     ("😂", "Laugh",       "laughs at",           "laughs",  "laugh",  None),
    "blush":     ("😊", "Blush",       "blushes at",          "blushes", "blush",  "blush"),
    "smile":     ("😄", "Smile",       "smiles at",           "smiles",  "smile",  "smile"),
    "smug":      ("😏", "Smug",        "smirks at",           "looks smug", "smug", "smug"),
    "happy":     ("🥳", "Happy",       "is happy with",       "is happy", "happy", "happy"),
    "nod":       ("🙂", "Nod",         "nods at",             "nods",    "nod",    None),
    "thumbsup":  ("👍", "Thumbs Up",   "gives a thumbs up to", "gives a thumbs up", "thumbsup", None),
    "shrug":     ("🤷", "Shrug",       "shrugs at",           "shrugs",  "shrug",  None),
    "facepalm":  ("🤦", "Facepalm",    "facepalms at",        "facepalms", "facepalm", None),
    "cry":       ("😢", "Cry",         "cries on",            "cries",   "cry",    "cry"),
    "pout":      ("😤", "Pout",        "pouts at",            "pouts",   "pout",   None),
    "nope":      ("🙅", "Nope",        "says nope to",        "says nope", "nope", None),
    "think":     ("🤔", "Think",       "thinks about",        "is thinking", "think", None),
    "bored":     ("🥱", "Bored",       "is bored of",         "is bored", "bored", None),
    "yawn":      ("😪", "Yawn",        "yawns at",            "yawns",   "yawn",   None),
    "sleep":     ("😴", "Sleep",       "falls asleep on",     "falls asleep", "sleep", None),
    "run":       ("🏃", "Run",         "runs away from",      "runs away", "run",  None),
}
ACTION_COUNT = len(ACTIONS)

# slap, punch, etc. get a sassier colour
HOSTILE = {"slap", "punch", "kick", "shoot", "yeet", "bonk", "bite", "baka"}
SOFT = {"hug", "cuddle", "kiss", "peck", "pat", "handhold", "handshake", "highfive", "feed", "nom", "tickle"}


class ReturnView(discord.ui.View):
    """Lets the person who got the action send one back with a single press."""

    def __init__(self, cog: "Actions", key: str, giver: discord.abc.User, receiver: discord.abc.User):
        super().__init__(timeout=180)
        self.cog, self.key, self.giver, self.receiver = cog, key, giver, receiver
        self.message: Optional[discord.Message] = None
        emoji, label = ACTIONS[key][0], ACTIONS[key][1]
        button = discord.ui.Button(label=f"{label} back", emoji=emoji, style=discord.ButtonStyle.secondary)
        button.callback = self.press
        self.add_item(button)

    async def press(self, interaction: discord.Interaction):
        if interaction.user.id != self.receiver.id:
            return await interaction.response.send_message(
                f"Only {self.receiver.display_name} can do that. Use `/{self.key}` yourself! 🕷️", ephemeral=True)
        self.stop()
        for item in self.children:
            item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except discord.HTTPException:
            pass
        await interaction.response.defer()
        embed = await self.cog.build(self.key, self.receiver, self.giver)
        await interaction.followup.send(embed=embed)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Actions(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: Optional[aiohttp.ClientSession] = None

    async def cog_load(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6))

    async def cog_unload(self):
        if self.session is not None:
            await self.session.close()

    # ---------- GIF fetching
    async def fetch_gif(self, key: str) -> Optional[str]:
        _, _, _, _, nekos_cat, waifu_cat = ACTIONS[key]
        if self.session is None:
            return None
        if nekos_cat:
            try:
                async with self.session.get(NEKOS.format(nekos_cat)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return data["results"][0]["url"]
            except (aiohttp.ClientError, KeyError, IndexError, ValueError, TimeoutError):
                pass
        if waifu_cat:
            try:
                async with self.session.get(WAIFU.format(waifu_cat)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return data["url"]
            except (aiohttp.ClientError, KeyError, ValueError, TimeoutError):
                pass
        return None

    # ---------- embed builder
    async def build(self, key: str, author: discord.abc.User, target: Optional[discord.abc.User]) -> discord.Embed:
        emoji, label, verb, alone, _, _ = ACTIONS[key]
        if target is None:
            text = f"{emoji} {author.mention} {alone}!"
        elif target.id == author.id:
            text = f"{emoji} {author.mention} {verb} themselves... okay then. 🕸️"
        elif target.id == self.bot.user.id:
            text = f"{emoji} {author.mention} {verb} **Spidey**! My spider sense was not ready for that. 🕷️"
        else:
            text = f"{emoji} {author.mention} {verb} {target.mention}!"
        color = RED if key in HOSTILE else BLUE if key in SOFT else DARK
        embed = web_embed(label, text, color)
        gif = await self.fetch_gif(key)
        if gif:
            embed.set_image(url=gif)
            embed.set_footer(text="🕸️ Spidey • GIFs by nekos.best")
        return embed

    # ---------- shared command logic
    async def run(self, interaction: discord.Interaction, key: str, target: Optional[discord.abc.User]):
        alone = ACTIONS[key][3]
        if target is None and alone is None:
            return await interaction.response.send_message(
                embed=web_embed("Who's The Target?", f"Pick someone: `/{key} @user` 🕷️", DARK), ephemeral=True)
        await interaction.response.defer()          # the GIF lookup can take a moment
        embed = await self.build(key, interaction.user, target)
        view = None
        if target is not None and not target.bot and target.id != interaction.user.id:
            view = ReturnView(self, key, interaction.user, target)
        if view is not None:
            message = await interaction.followup.send(embed=embed, view=view, wait=True)
            view.message = message
        else:
            await interaction.followup.send(embed=embed)

    # ---------- the big one
    async def action_autocomplete(self, interaction: discord.Interaction, current: str):
        current = current.lower().strip()
        matches = [k for k in ACTIONS if current in k or current in ACTIONS[k][1].lower()]
        return [app_commands.Choice(name=f"{ACTIONS[k][0]} {ACTIONS[k][1]}", value=k) for k in matches[:25]]

    @app_commands.command(name="action", description=f"Do one of {ACTION_COUNT} actions with a GIF (hug, dance, punch, cry...)")
    @app_commands.describe(type="Start typing to search the actions", user="Who is it for? (optional for some)")
    @app_commands.autocomplete(type=action_autocomplete)
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 3.0)
    async def action(self, interaction: discord.Interaction, type: str, user: Optional[discord.Member] = None):
        key = type.lower().strip()
        if key not in ACTIONS:
            return await interaction.response.send_message(
                embed=web_embed("Unknown Action", "Pick one from the list that pops up while you type. 🕸️", DARK),
                ephemeral=True)
        await self.run(interaction, key, user)

    # ---------- quick commands for the favourites
    @app_commands.command(name="hug", description="Give someone a warm hug 🤗")
    @app_commands.describe(user="Who needs a hug?")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 3.0)
    async def hug(self, interaction: discord.Interaction, user: discord.Member):
        await self.run(interaction, "hug", user)

    @app_commands.command(name="slap", description="Slap someone ✋")
    @app_commands.describe(user="Who gets slapped?")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 3.0)
    async def slap(self, interaction: discord.Interaction, user: discord.Member):
        await self.run(interaction, "slap", user)

    @app_commands.command(name="punch", description="Punch someone 👊")
    @app_commands.describe(user="Who gets punched?")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 3.0)
    async def punch(self, interaction: discord.Interaction, user: discord.Member):
        await self.run(interaction, "punch", user)

    @app_commands.command(name="dance", description="Dance, alone or with someone 💃")
    @app_commands.describe(user="Dance with someone (optional)")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 3.0)
    async def dance(self, interaction: discord.Interaction, user: Optional[discord.Member] = None):
        await self.run(interaction, "dance", user)


async def setup(bot: commands.Bot):
    await bot.add_cog(Actions(bot))
