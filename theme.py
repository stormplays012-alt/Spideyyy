import traceback

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


async def safe_dm(user, title: str, description: str = "", color: int = BLUE):
    """DM a user. Never raises: closed DMs, bots and missing users are silently ignored."""
    try:
        if user is None or getattr(user, "bot", False):
            return False
        await user.send(embed=web_embed(title, description, color))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- never fail silently
async def report_ui_error(interaction: discord.Interaction, error: Exception):
    """Print the real error to the console AND tell the user, so a broken button never shows
    Discord's 'The application did not respond' without any explanation."""
    traceback.print_exception(type(error), error, error.__traceback__)
    embed = web_embed("Stuck in the Web!", "Something went wrong on my side. Please try again. 🕸️", DARK)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except discord.HTTPException:
        pass  # the interaction already expired, nothing more we can do


def install_error_handlers():
    """Make every View and Modal in the bot report errors instead of failing silently."""
    async def view_on_error(self, interaction: discord.Interaction, error: Exception, item):
        await report_ui_error(interaction, error)

    async def modal_on_error(self, interaction: discord.Interaction, error: Exception):
        await report_ui_error(interaction, error)

    discord.ui.View.on_error = view_on_error
    discord.ui.Modal.on_error = modal_on_error
