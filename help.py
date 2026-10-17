import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK

# cog class name -> (emoji, label, short blurb). Order here = order in the dropdown.
# Cogs that aren't listed still show up automatically as "✨ <CogName>".
CATEGORIES = {
    "Basic":       ("🕸️", "Basic", "Fun starter commands"),
    "Moderation":  ("🛡️", "Moderation", "Kick, ban, mute, warn, purge and more"),
    "AutoMod":     ("🤖", "AutoMod", "Automatic spam, link and word protection"),
    "Utility":     ("🧰", "Utility", "Info, tools, polls, reminders, giveaways"),
    "Games":       ("🎮", "Games", "Play with Spidey"),
    "Economy":     ("💰", "Economy", "Webs, daily, give, shop, rob"),
    "Casino":      ("🎰", "Casino", "Bet webs: slots, mines, blackjack, more"),
    "VoiceMaster": ("🎙️", "VoiceMaster", "Join-to-create voice rooms"),
    "Leveling":    ("⭐", "Leveling", "XP, ranks and leaderboards"),
    "Welcome":     ("👋", "Welcome", "Welcome messages, auto-roles, verification"),
    "Tickets":     ("🎫", "Tickets", "Private support tickets"),
}

HOME = "__home__"
FIELD_LIMIT = 1000


def flatten(cmds):
    """Turn commands/groups into a flat list of (name, description, needs_perms)."""
    out = []
    for c in cmds:
        if isinstance(c, app_commands.Group):
            locked = c.default_permissions is not None
            for sub in c.walk_commands():
                if isinstance(sub, app_commands.Command):
                    out.append((sub.qualified_name, sub.description, locked or sub.default_permissions is not None))
        else:
            out.append((c.name, c.description, c.default_permissions is not None))
    return out


def collect(bot: commands.Bot) -> dict:
    """Group every registered slash command by the cog it lives in."""
    cats: dict[str, list] = {}
    cog_top_names = set()
    for name, cog in bot.cogs.items():
        cmds = cog.get_app_commands()
        if not cmds:
            continue
        cog_top_names.update(c.name for c in cmds)
        cats[name] = sorted(flatten(cmds))
    basic = [c for c in bot.tree.get_commands() if c.name not in cog_top_names]
    if basic:
        cats["Basic"] = sorted(flatten(basic))
    ordered = {k: cats[k] for k in CATEGORIES if k in cats}
    ordered.update({k: v for k, v in cats.items() if k not in ordered})  # unknown cogs last
    return ordered


def meta(key: str):
    return CATEGORIES.get(key, ("✨", key, f"{key} commands"))


class HelpView(discord.ui.View):
    def __init__(self, bot: commands.Bot, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.data = collect(bot)
        self.message: discord.Message | None = None

        options = [discord.SelectOption(label="Home", emoji="🏠", value=HOME, description="Overview of everything")]
        for key, cmds in self.data.items():
            emoji, label, _ = meta(key)
            options.append(discord.SelectOption(
                label=label, emoji=emoji, value=key,
                description=f"{len(cmds)} command{'s' if len(cmds) != 1 else ''}"))
        self.select = discord.ui.Select(placeholder="🕷️ Choose a category...", options=options[:25])
        self.select.callback = self.on_select
        self.add_item(self.select)

    # ---------- embeds
    def home_embed(self) -> discord.Embed:
        total = sum(len(v) for v in self.data.values())
        lines = []
        for key, cmds in self.data.items():
            emoji, label, blurb = meta(key)
            lines.append(f"{emoji} **{label}** — {blurb} (`{len(cmds)}`)")
        desc = (f"Welcome to Spidey's Web Guide! 🕷️\n"
                f"I have **{total}** commands. Pick a category from the dropdown below.\n\n"
                + "\n".join(lines))
        return web_embed("Spidey's Web Guide", desc, RED)

    def category_embed(self, key: str) -> discord.Embed:
        emoji, label, blurb = meta(key)
        rows = self.data.get(key, [])
        trim = 80
        while True:
            lines = [f"{'🔒 ' if locked else ''}`/{name}` — {desc[:trim]}" for name, desc, locked in rows]
            if sum(len(l) + 1 for l in lines) < 5200 or trim <= 30:
                break
            trim -= 15

        # split into fields (Discord caps a field at 1024 chars)
        chunks, current = [], ""
        for line in lines:
            if len(current) + len(line) + 1 > FIELD_LIMIT:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)

        embed = web_embed(f"{emoji} {label}", blurb, BLUE)
        for i, chunk in enumerate(chunks, 1):
            embed.add_field(name="Commands" if len(chunks) == 1 else f"Commands ({i}/{len(chunks)})",
                            value=chunk, inline=False)
        embed.set_footer(text="🔒 needs special permissions • 🕸️ Spidey")
        return embed

    # ---------- interaction
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Run `/help` yourself to browse. 🕷️", ephemeral=True)
            return False
        return True

    async def on_select(self, interaction: discord.Interaction):
        key = self.select.values[0]
        embed = self.home_embed() if key == HOME else self.category_embed(key)
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        self.select.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Browse everything Spidey can do")
    async def help(self, interaction: discord.Interaction):
        view = HelpView(self.bot, interaction.user.id)
        await interaction.response.send_message(embed=view.home_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
