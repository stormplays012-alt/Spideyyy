import glob
import json
import os
import random
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK
from moderation import fail

DATA_FILE = "welcome.json"

DEFAULTS = {
    "welcome_channel": None,
    "welcome_message": "{line}\n\nWelcome {user} to **{server}**! You're member **#{count}**. 🕷️",
    "goodbye_channel": None,
    "goodbye_message": "**{name}** swung out of the web. 🕸️ We're now **{count}** members.",
    "auto_roles": [],
    "min_age_days": 0,
    "verify_role": None,
    # ---- look of the welcome message (all editable with /welcome ...)
    "welcome_text": "",          # text ABOVE the embed, the only part that can ping the new member
    "welcome_title": "New Arrival!",
    "welcome_footer": None,      # None = Spidey's default footer, "__off__" = no footer, anything else = custom
    "welcome_color": BLUE,
    "thumbnail_mode": "avatar",  # avatar | server | custom | none
    "thumbnail_url": None,
    "thumbnail_file": None,      # an uploaded picture (kept on disk)
    "image_url": None,           # big picture at the bottom of the embed
    "image_file": None,
}

IMAGE_DIR = "welcome_images"
FOOTER_OFF = "__off__"
IMAGE_TYPES = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}
MAX_IMAGE_BYTES = 8 * 1024 * 1024

LINES = [
    "A new friend got caught in the web! 🕸️",
    "My spider-sense was tingling… you've arrived! 🕷️",
    "Welcome to the neighborhood, web-slinger! 🕷️",
]


def render(template: str, member: discord.Member) -> str:
    """Fill in {user} {name} {server} {count} {line}."""
    return (template.replace("{user}", member.mention)
                    .replace("{name}", member.display_name)
                    .replace("{server}", member.guild.name)
                    .replace("{count}", str(member.guild.member_count))
                    .replace("{line}", random.choice(LINES))
                    .replace("\\n", "\n"))


def parse_hex(text: str) -> Optional[int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text.strip())
    return int(m.group(1), 16) if m else None


def valid_url(url: str) -> bool:
    u = url.strip()
    return u.startswith("https://") and " " not in u and 10 <= len(u) <= 512


def image_path(c: dict, kind: str) -> Optional[str]:
    """Path of an uploaded picture ('thumbnail' or 'image') if it exists on disk."""
    name = c.get(f"{kind}_file")
    path = os.path.join(IMAGE_DIR, name) if name else None
    return path if path and os.path.exists(path) else None


def build_welcome(member: discord.Member, c: dict):
    """Returns (text above the embed or None, embed, [files to attach])."""
    content = render(c["welcome_text"], member)[:2000] if c["welcome_text"] else None
    embed = web_embed(render(c["welcome_title"], member)[:256] or "Welcome!",
                      render(c["welcome_message"], member), c["welcome_color"])
    if c["welcome_footer"] == FOOTER_OFF:
        embed.remove_footer()
    elif c["welcome_footer"]:
        embed.set_footer(text=render(c["welcome_footer"], member)[:2048])

    files = []
    mode = c["thumbnail_mode"]
    if mode == "avatar":
        embed.set_thumbnail(url=member.display_avatar.url)
    elif mode == "server" and member.guild.icon:
        embed.set_thumbnail(url=member.guild.icon.url)
    elif mode == "custom":
        path = image_path(c, "thumbnail")
        if path:
            ext = os.path.splitext(path)[1]
            files.append(discord.File(path, filename=f"welcome_thumb{ext}"))
            embed.set_thumbnail(url=f"attachment://welcome_thumb{ext}")
        elif c["thumbnail_url"]:
            embed.set_thumbnail(url=c["thumbnail_url"])

    path = image_path(c, "image")
    if path:
        ext = os.path.splitext(path)[1]
        files.append(discord.File(path, filename=f"welcome_image{ext}"))
        embed.set_image(url=f"attachment://welcome_image{ext}")
    elif c["image_url"]:
        embed.set_image(url=c["image_url"])
    return content, embed, files


# ------------------------------------------------------------------ verify button (survives restarts)
class VerifyView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Verify", emoji="🕸️", style=discord.ButtonStyle.success, custom_id="welcome:verify")
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Welcome")
        role_id = cog.cfg(interaction.guild.id)["verify_role"] if cog else None
        role = interaction.guild.get_role(role_id) if role_id else None
        if role is None:
            return await fail(interaction, "Verification isn't set up properly. Tell an admin. 🕷️")
        if role in interaction.user.roles:
            return await interaction.response.send_message(
                embed=web_embed("Already Verified!", "You're already in the web. 🕸️", BLUE), ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="Passed Spidey verification")
        except discord.HTTPException:
            return await fail(interaction, "I couldn't give you the role. Tell an admin to move my role higher. 🕷️")
        await interaction.response.send_message(
            embed=web_embed("Verified!", f"Welcome in! You now have {role.mention}. 🕸️", BLUE), ephemeral=True)


# ------------------------------------------------------------------ cog
class Welcome(commands.Cog):
    welcome = app_commands.Group(
        name="welcome", description="Welcome messages, auto-roles and verification", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = {"guilds": {}}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict) and isinstance(loaded.get("guilds"), dict):
                    self.data = loaded
            except (OSError, json.JSONDecodeError):
                pass

    async def cog_load(self):
        self.bot.add_view(VerifyView())

    def save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, DATA_FILE)

    def cfg(self, gid: int) -> dict:
        g = self.data["guilds"].setdefault(str(gid), {})
        for k, v in DEFAULTS.items():
            g.setdefault(k, list(v) if isinstance(v, list) else v)
        return g

    # ================================================================ EVENTS
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.bot:
            return
        c = self.cfg(member.guild.id)

        # raid protection: too-new accounts
        if c["min_age_days"] > 0:
            age = (discord.utils.utcnow() - member.created_at).days
            if age < c["min_age_days"]:
                reason = f"Account is {age} day(s) old; this server requires {c['min_age_days']}+"
                try:
                    await member.send(embed=web_embed(
                        "Account Too New", f"**{member.guild.name}** only allows accounts older than "
                                           f"**{c['min_age_days']}** days. Come back later! 🕸️", DARK))
                except discord.HTTPException:
                    pass
                try:
                    await member.kick(reason=f"Spidey anti-raid: {reason}")
                except discord.HTTPException:
                    return
                mod = self.bot.get_cog("Moderation")
                if mod:
                    await mod.record(member.guild, "kick", member.id, self.bot.user.id, f"Anti-raid: {reason}")
                return

        # auto-roles
        roles = [r for r in (member.guild.get_role(i) for i in c["auto_roles"])
                 if r and not r.managed and r < member.guild.me.top_role]
        if roles:
            try:
                await member.add_roles(*roles, reason="Spidey auto-role")
            except discord.HTTPException:
                pass

        # welcome message
        channel = member.guild.get_channel(c["welcome_channel"]) if c["welcome_channel"] else None
        if channel:
            content, embed, files = build_welcome(member, c)
            try:      # only the new member can be pinged by the text above the embed
                await channel.send(content=content, embed=embed, files=files,
                                   allowed_mentions=discord.AllowedMentions(users=[member], roles=False, everyone=False))
            except discord.HTTPException:
                pass

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        if member.bot:
            return
        c = self.cfg(member.guild.id)
        channel = member.guild.get_channel(c["goodbye_channel"]) if c["goodbye_channel"] else None
        if channel:
            try:
                await channel.send(embed=web_embed("Goodbye!", render(c["goodbye_message"], member), DARK))
            except discord.HTTPException:
                pass

    # ================================================================ COMMANDS
    @welcome.command(name="status", description="See welcome, goodbye and verification settings")
    async def status(self, interaction: discord.Interaction):
        c = self.cfg(interaction.guild.id)
        ch = lambda i: f"<#{i}>" if i else "off"
        roles = ", ".join(f"<@&{i}>" for i in c["auto_roles"]) or "none"
        age = f"{c['min_age_days']} days" if c["min_age_days"] else "off"
        verify = f"<@&{c['verify_role']}>" if c["verify_role"] else "not set up"
        footer = {None: "Spidey default", FOOTER_OFF: "off"}.get(c["welcome_footer"], c["welcome_footer"])
        thumb = c["thumbnail_mode"] + (" (uploaded picture)" if c["thumbnail_mode"] == "custom" and image_path(c, "thumbnail")
                                       else " (link)" if c["thumbnail_mode"] == "custom" and c["thumbnail_url"] else "")
        image = "uploaded picture" if image_path(c, "image") else ("link" if c["image_url"] else "none")
        desc = (f"👋 **Welcome channel:** {ch(c['welcome_channel'])}\n"
                f"💬 **Text above the embed:** {c['welcome_text'][:200] or 'none'}\n"
                f"🏷️ **Title:** {c['welcome_title'][:100]}\n"
                f"📝 **Message:** {c['welcome_message'][:300]}\n"
                f"🔻 **Footer:** {str(footer)[:100]}\n"
                f"🎨 **Color:** `#{c['welcome_color']:06X}`\n"
                f"🖼️ **Thumbnail:** {thumb}\n"
                f"🌄 **Image:** {image}\n\n"
                f"🚪 **Goodbye channel:** {ch(c['goodbye_channel'])}\n"
                f"📝 **Goodbye message:** {c['goodbye_message'][:300]}\n\n"
                f"🎭 **Auto-roles:** {roles}\n"
                f"🛡️ **Minimum account age:** {age}\n"
                f"✅ **Verify role:** {verify}\n\n"
                "Placeholders: `{user}` `{name}` `{server}` `{count}`")
        await interaction.response.send_message(embed=web_embed("Welcome Settings", desc, BLUE), ephemeral=True)

    @welcome.command(name="channel", description="Pick where welcome messages are sent (empty = turn off)")
    async def channel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        self.cfg(interaction.guild.id)["welcome_channel"] = channel.id if channel else None
        self.save()
        text = f"Welcome messages will be sent to {channel.mention} 🕸️" if channel else "Welcome messages are **off**."
        await interaction.response.send_message(embed=web_embed("Welcome Updated!", text, BLUE))

    @welcome.command(name="message", description="Write the welcome message ({user}, {name}, {server}, {count})")
    @app_commands.describe(text="Use {user} {name} {server} {count}. Type \\n for a new line.")
    async def message(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 1500]):
        self.cfg(interaction.guild.id)["welcome_message"] = text
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Welcome Message Saved!", "Try `/welcome test` to preview it. 🕸️", BLUE))

    @welcome.command(name="goodbyechannel", description="Pick where goodbye messages are sent (empty = turn off)")
    async def goodbyechannel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        self.cfg(interaction.guild.id)["goodbye_channel"] = channel.id if channel else None
        self.save()
        text = f"Goodbye messages will be sent to {channel.mention} 🕸️" if channel else "Goodbye messages are **off**."
        await interaction.response.send_message(embed=web_embed("Goodbye Updated!", text, BLUE))

    @welcome.command(name="goodbyemessage", description="Write the goodbye message")
    @app_commands.describe(text="Use {user} {name} {server} {count}. Type \\n for a new line.")
    async def goodbyemessage(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 1500]):
        self.cfg(interaction.guild.id)["goodbye_message"] = text
        self.save()
        await interaction.response.send_message(embed=web_embed("Goodbye Message Saved!", "It's set. 🕸️", BLUE))

    @welcome.command(name="autorole", description="Give new members a role automatically")
    @app_commands.describe(action="add / remove / list", role="The role (not needed for list)")
    @app_commands.choices(action=[app_commands.Choice(name=a, value=a) for a in ("add", "remove", "list")])
    async def autorole(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                       role: Optional[discord.Role] = None):
        ids = self.cfg(interaction.guild.id)["auto_roles"]
        if action.value == "list":
            text = ", ".join(f"<@&{i}>" for i in ids) or "No auto-roles set."
            return await interaction.response.send_message(embed=web_embed("Auto-Roles", text, BLUE), ephemeral=True)
        if role is None:
            return await fail(interaction, "Tell me which role.")
        if action.value == "add":
            if role.managed or role.is_default():
                return await fail(interaction, "I can't hand out that role.")
            if role >= interaction.guild.me.top_role:
                return await fail(interaction, "That role is above mine. Move my role higher in Server Settings → Roles.")
            if role.id in ids:
                return await fail(interaction, "That role is already an auto-role.")
            ids.append(role.id)
        else:
            if role.id not in ids:
                return await fail(interaction, "That role isn't an auto-role.")
            ids.remove(role.id)
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Auto-Roles Updated!", f"{'Added' if action.value == 'add' else 'Removed'} {role.mention}. 🎭", BLUE))

    @welcome.command(name="minage", description="Kick brand-new accounts to stop raids (0 = off)")
    @app_commands.describe(days="Minimum account age in days (0 turns it off)")
    async def minage(self, interaction: discord.Interaction, days: app_commands.Range[int, 0, 365]):
        self.cfg(interaction.guild.id)["min_age_days"] = days
        self.save()
        text = (f"Accounts younger than **{days}** days will be kicked when they join. 🛡️" if days
                else "Account-age check is **off**.")
        await interaction.response.send_message(embed=web_embed("Anti-Raid Updated!", text, BLUE))

    @welcome.command(name="verify", description="Post a Verify button that gives a role")
    @app_commands.describe(channel="Where to post the Verify button", role="Role members get after verifying")
    async def verify(self, interaction: discord.Interaction, channel: discord.TextChannel, role: discord.Role):
        if role.managed or role.is_default():
            return await fail(interaction, "I can't hand out that role.")
        if role >= interaction.guild.me.top_role:
            return await fail(interaction, "That role is above mine. Move my role higher in Server Settings → Roles.")
        embed = web_embed("Verification", "Press the button below to verify and unlock the server. 🕸️", RED)
        try:
            await channel.send(embed=embed, view=VerifyView())
        except discord.HTTPException:
            return await fail(interaction, f"I can't post in {channel.mention}. Check my permissions there.")
        self.cfg(interaction.guild.id)["verify_role"] = role.id
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Verification Ready!", f"Posted the Verify button in {channel.mention}. Verified members get {role.mention}.\n"
                                   "Tip: hide your other channels from @everyone and show them to this role.", BLUE))

    # ================================================================ LOOK OF THE WELCOME MESSAGE
    @welcome.command(name="text", description="Text shown ABOVE the welcome embed (empty = none)")
    @app_commands.describe(text="e.g. Welcome {user}! Use {user} {name} {server} {count}. Type \\n for a new line.")
    async def text(self, interaction: discord.Interaction, text: Optional[app_commands.Range[str, 1, 1000]] = None):
        self.cfg(interaction.guild.id)["welcome_text"] = text or ""
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Welcome Text Saved!" if text else "Welcome Text Removed!",
            "Try `/welcome test` to preview it. 🕸️\n({user} in this text pings the new member.)" if text
            else "There's no text above the embed now.", BLUE))

    @welcome.command(name="title", description="The title of the welcome embed")
    @app_commands.describe(text="Use {user} {name} {server} {count}")
    async def title(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 200]):
        self.cfg(interaction.guild.id)["welcome_title"] = text
        self.save()
        await interaction.response.send_message(embed=web_embed("Welcome Title Saved!", "Try `/welcome test`. 🕸️", BLUE))

    @welcome.command(name="footer", description="The small text at the bottom of the welcome embed")
    @app_commands.describe(text="Your text (placeholders work). Empty = Spidey's default footer, 'off' = no footer.")
    async def footer(self, interaction: discord.Interaction, text: Optional[app_commands.Range[str, 1, 500]] = None):
        c = self.cfg(interaction.guild.id)
        if text is None:
            c["welcome_footer"], note = None, "The footer is back to Spidey's default."
        elif text.strip().lower() == "off":
            c["welcome_footer"], note = FOOTER_OFF, "The footer is now **off**."
        else:
            c["welcome_footer"], note = text, "Footer saved."
        self.save()
        await interaction.response.send_message(embed=web_embed("Welcome Footer Updated!", f"{note} Try `/welcome test`. 🕸️", BLUE))

    @welcome.command(name="color", description="The color strip on the left of the welcome embed")
    @app_commands.describe(hex_code="e.g. #E62429")
    async def color(self, interaction: discord.Interaction, hex_code: str):
        value = parse_hex(hex_code)
        if value is None:
            return await fail(interaction, "Give me a 6-digit hex color like `#E62429`.")
        self.cfg(interaction.guild.id)["welcome_color"] = value
        self.save()
        await interaction.response.send_message(embed=web_embed("Welcome Color Saved!", f"Color is now `#{value:06X}`.", value))

    async def _save_upload(self, guild_id: int, kind: str, attachment: discord.Attachment) -> str:
        ctype = (attachment.content_type or "").split(";")[0].lower()
        if ctype not in IMAGE_TYPES:
            raise ValueError("That file isn't a PNG, JPG, GIF or WEBP image.")
        if attachment.size > MAX_IMAGE_BYTES:
            raise ValueError("That image is too big (max 8 MB).")
        self._delete_files(guild_id, kind)
        os.makedirs(IMAGE_DIR, exist_ok=True)
        name = f"{guild_id}_{kind}{IMAGE_TYPES[ctype]}"
        await attachment.save(os.path.join(IMAGE_DIR, name))
        return name

    @staticmethod
    def _delete_files(guild_id: int, kind: str):
        for path in glob.glob(os.path.join(IMAGE_DIR, f"{guild_id}_{kind}.*")):
            try:
                os.remove(path)
            except OSError:
                pass

    @welcome.command(name="thumbnail", description="The small picture at the top right of the welcome embed")
    @app_commands.describe(mode="avatar = the new member's picture, server = your server icon, custom = your own, none = nothing",
                           url="For custom: a https:// link to a picture", file="For custom: or upload a picture")
    @app_commands.choices(mode=[app_commands.Choice(name="Member's avatar", value="avatar"),
                                app_commands.Choice(name="Server icon", value="server"),
                                app_commands.Choice(name="Custom picture", value="custom"),
                                app_commands.Choice(name="None", value="none")])
    async def thumbnail(self, interaction: discord.Interaction, mode: app_commands.Choice[str],
                        url: Optional[str] = None, file: Optional[discord.Attachment] = None):
        c = self.cfg(interaction.guild.id)
        gid = interaction.guild.id
        if mode.value == "custom":
            if file is None and not url:
                return await fail(interaction, "For **custom**, give a `url` (https link) or upload a `file`.")
            if file is not None:
                try:
                    c["thumbnail_file"] = await self._save_upload(gid, "thumbnail", file)
                except ValueError as e:
                    return await fail(interaction, str(e))
                c["thumbnail_url"] = None
            else:
                if not valid_url(url):
                    return await fail(interaction, "The link must start with `https://`.")
                self._delete_files(gid, "thumbnail")
                c["thumbnail_file"], c["thumbnail_url"] = None, url.strip()
        c["thumbnail_mode"] = mode.value
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Thumbnail Updated!", f"Thumbnail: **{mode.name}**. Try `/welcome test`. 🕸️", BLUE))

    @welcome.command(name="image", description="A big picture at the bottom of the welcome embed (empty = remove)")
    @app_commands.describe(url="A https:// link to a picture", file="Or upload a picture (PNG, JPG, GIF, WEBP)")
    async def image(self, interaction: discord.Interaction, url: Optional[str] = None,
                    file: Optional[discord.Attachment] = None):
        c = self.cfg(interaction.guild.id)
        gid = interaction.guild.id
        if file is None and not url:
            self._delete_files(gid, "image")
            c["image_file"] = c["image_url"] = None
            self.save()
            return await interaction.response.send_message(embed=web_embed(
                "Welcome Image Removed!", "There's no big picture on the welcome message now.", BLUE))
        if file is not None:
            try:
                c["image_file"] = await self._save_upload(gid, "image", file)
            except ValueError as e:
                return await fail(interaction, str(e))
            c["image_url"] = None
        else:
            if not valid_url(url):
                return await fail(interaction, "The link must start with `https://`.")
            self._delete_files(gid, "image")
            c["image_file"], c["image_url"] = None, url.strip()
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Welcome Image Saved!", "Try `/welcome test` to see it. 🌄", BLUE))

    @welcome.command(name="resetstyle", description="Put the welcome look back to Spidey's default")
    async def resetstyle(self, interaction: discord.Interaction):
        c = self.cfg(interaction.guild.id)
        for kind in ("thumbnail", "image"):
            self._delete_files(interaction.guild.id, kind)
        for key in ("welcome_text", "welcome_title", "welcome_footer", "welcome_color", "thumbnail_mode",
                    "thumbnail_url", "thumbnail_file", "image_url", "image_file"):
            c[key] = DEFAULTS[key]
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Welcome Look Reset!", "Text, title, footer, color, thumbnail and image are back to the default. (Your message stays.)", DARK))

    @welcome.command(name="test", description="Preview the welcome message exactly as new members see it")
    async def test(self, interaction: discord.Interaction):
        content, embed, files = build_welcome(interaction.user, self.cfg(interaction.guild.id))
        header = "**Preview** (this is how it looks):"
        await interaction.response.send_message(content=f"{header}\n{content}" if content else header, embed=embed,
                                                files=files, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
