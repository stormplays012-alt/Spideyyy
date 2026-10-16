import discord

RED = 0xE62429
DARK = 0x1B1B2F
BLUE = 0x2B4EA2
FOOTER = "🕸️ Spidey • Your friendly neighborhood bot"


def web_embed(title: str, description: str = "", color: int = RED) -> discord.Embed:
    """Every embed goes through here so the theme stays consistent."""
    embed = discord.Embed(
        title=f"🕷️ {title} 🕸️",
        description=description,
        color=color,
    )
    embed.set_footer(text=FOOTER)
    return embed
