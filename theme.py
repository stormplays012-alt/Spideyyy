import discord

# 🌙 Hamsafar palette - a warm dusk: apricot glow, soft lavender, midnight indigo
PEACH = 0xF2A47E      # main accent (warm apricot)
LAVENDER = 0xB8A9D9   # soft secondary
NIGHT = 0x4A3F6B      # quiet, muted indigo for gentle / serious embeds
SAGE = 0xA8C3A0       # soft green
ROSE = 0xE5989B       # soft rose
CREAM = 0xFFF1E0

FOOTER = "🌙 Hamsafar • travel together, stay cozy"


def cozy_embed(title: str, description: str = "", color: int = PEACH) -> discord.Embed:
    """Every embed goes through here so the theme stays consistent."""
    embed = discord.Embed(
        title=f"🌙 {title} ✨",
        description=description,
        color=color,
    )
    embed.set_footer(text=FOOTER)
    return embed
