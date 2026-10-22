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
# ------------------------------------------------------------------ the control panel (/welcome)
LOOK_KEYS = ("welcome_text", "welcome_title", "welcome_footer", "welcome_color", "thumbnail_mode",
             "thumbnail_url", "thumbnail_file", "image_url", "image_file")


def summary_embed(c: dict, notes: Optional[list] = None) -> discord.Embed:
    ch = lambda i: f"<#{i}>" if i else "off"
    roles = ", ".join(f"<@&{i}>" for i in c["auto_roles"]) or "none"
    age = f"{c['min_age_days']} days" if c["min_age_days"] else "off"
    verify = f"<@&{c['verify_role']}>" if c["verify_role"] else "not set up"
    footer = {None: "Spidey default", FOOTER_OFF: "off"}.get(c["welcome_footer"], c["welcome_footer"])
    thumb = c["thumbnail_mode"]
    if thumb == "custom":
        thumb += " (uploaded picture)" if image_path(c, "thumbnail") else " (link)" if c["thumbnail_url"] else " (nothing set yet)"
    image = "uploaded picture" if image_path(c, "image") else ("link" if c["image_url"] else "none")
    lines = [f"⚠️ {n}" for n in (notes or [])]
    if lines:
        lines.append("")
    lines += [
        f"📣 **Welcome channel:** {ch(c['welcome_channel'])}   🚪 **Goodbye channel:** {ch(c['goodbye_channel'])}",
        "",
        f"💬 **Text above embed:** {c['welcome_text'][:150] or 'none'}",
        f"🏷️ **Title:** {c['welcome_title'][:100]}",
        f"📝 **Message:** {c['welcome_message'][:250]}",
        f"🔻 **Footer:** {str(footer)[:100]}   🎨 **Color:** `#{c['welcome_color']:06X}`",
        f"🖼️ **Thumbnail:** {thumb}   🌄 **Image:** {image}",
        "",
        f"📝 **Goodbye message:** {c['goodbye_message'][:200]}",
        f"🎭 **Auto-roles:** {roles}",
        f"🛡️ **Minimum account age:** {age}   ✅ **Verify role:** {verify}",
        "",
        "Press the buttons below to change things. Placeholders: `{user}` `{name}` `{server}` `{count}`",
    ]
    return web_embed("Welcome Panel", "\n".join(lines)[:4000], BLUE)


class OwnedView(discord.ui.View):
    """Base for every panel screen: only the staff member who opened it can use it."""

    def __init__(self, cog: "Welcome", interaction: discord.Interaction):
        super().__init__(timeout=600)
        self.cog, self.origin = cog, interaction
        self.owner_id, self.guild = interaction.user.id, interaction.guild

    @property
    def c(self) -> dict:
        return self.cog.cfg(self.guild.id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("This panel isn't yours. Run `/welcome` to open your own. 🕷️", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        try:
            await self.origin.edit_original_response(view=self)
        except discord.HTTPException:
            pass


async def show_main(cog: "Welcome", interaction: discord.Interaction, notes: Optional[list] = None, old=None):
    if old is not None:
        old.stop()          # the screen we leave must not keep its own timeout running
    view = PanelView(cog, interaction)
    await interaction.response.edit_message(embed=summary_embed(view.c, notes), view=view)


class BackButton(discord.ui.Button):
    def __init__(self, row: int = 4):
        super().__init__(label="Back", emoji="⬅️", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        await show_main(self.view.cog, interaction, old=self.view)


def _field(label, value, *, paragraph=False, required=True, max_length=200, placeholder=None):
    kwargs = {"label": label, "required": required, "max_length": max_length}
    if paragraph:
        kwargs["style"] = discord.TextStyle.paragraph
    if value:
        kwargs["default"] = str(value)[:max_length]
    if placeholder:
        kwargs["placeholder"] = placeholder
    return discord.ui.TextInput(**kwargs)


# ---------------- pop-up forms
class MessagesModal(discord.ui.Modal, title="Welcome & goodbye messages"):
    def __init__(self, view: "PanelView"):
        super().__init__()
        self.panel = view
        c = view.c
        self.text = _field("Text above the embed (optional)", c["welcome_text"], paragraph=True, required=False,
                           max_length=1000, placeholder="Welcome {user}!  ({user} pings the new member)")
        self.message = _field("Welcome message (inside the embed)", c["welcome_message"], paragraph=True, max_length=1500)
        self.goodbye = _field("Goodbye message", c["goodbye_message"], paragraph=True, max_length=1500)
        for item in (self.text, self.message, self.goodbye):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        c = self.panel.c
        c["welcome_text"] = str(self.text.value or "").strip()
        c["welcome_message"] = str(self.message.value or "").strip() or DEFAULTS["welcome_message"]
        c["goodbye_message"] = str(self.goodbye.value or "").strip() or DEFAULTS["goodbye_message"]
        self.panel.cog.save()
        await show_main(self.panel.cog, interaction, ["Messages saved."], old=self.panel)


class LookModal(discord.ui.Modal, title="Look of the welcome message"):
    def __init__(self, view: "PanelView"):
        super().__init__()
        self.panel = view
        c = view.c
        footer = "off" if c["welcome_footer"] == FOOTER_OFF else (c["welcome_footer"] or "")
        self.title_in = _field("Title", c["welcome_title"], max_length=200)
        self.footer = _field("Footer (empty = default, off = none)", footer, required=False, max_length=500)
        self.color = _field("Color (hex, like #E62429)", f"#{c['welcome_color']:06X}", required=False, max_length=7)
        for item in (self.title_in, self.footer, self.color):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction):
        c, notes = self.panel.c, ["Look saved."]
        c["welcome_title"] = str(self.title_in.value or "").strip() or DEFAULTS["welcome_title"]
        footer = str(self.footer.value or "").strip()
        c["welcome_footer"] = None if not footer else (FOOTER_OFF if footer.lower() == "off" else footer)
        color = str(self.color.value or "").strip()
        if color:
            value = parse_hex(color)
            if value is None:
                notes.append("The color wasn't changed: use a 6-digit hex like #E62429.")
            else:
                c["welcome_color"] = value
        self.panel.cog.save()
        await show_main(self.panel.cog, interaction, notes, old=self.panel)


class AntiRaidModal(discord.ui.Modal, title="Anti-raid: minimum account age"):
    def __init__(self, view: "PanelView"):
        super().__init__()
        self.panel = view
        self.days = _field("Minimum account age in days (0 = off)", str(view.c["min_age_days"]), max_length=3)
        self.add_item(self.days)

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.days.value or "").strip()
        if not raw.isdigit() or not 0 <= int(raw) <= 365:
            return await show_main(self.panel.cog, interaction, ["Account age wasn't changed: enter a number from 0 to 365."], old=self.panel)
        self.panel.c["min_age_days"] = int(raw)
        self.panel.cog.save()
        text = f"New accounts younger than {raw} days will be kicked." if int(raw) else "Account-age check is off."
        await show_main(self.panel.cog, interaction, [text], old=self.panel)


class LinkModal(discord.ui.Modal, title="Picture link"):
    def __init__(self, view: "PicturesView", kind: str):
        super().__init__()
        self.pictures, self.kind = view, kind
        self.url = _field("https:// link to a picture", view.c.get(f"{kind}_url"), max_length=512)
        self.add_item(self.url)

    async def on_submit(self, interaction: discord.Interaction):
        url = str(self.url.value or "").strip()
        notes = []
        c, gid = self.pictures.c, self.pictures.guild.id
        if not valid_url(url):
            notes.append("That link wasn't saved: it must start with https://")
        else:
            self.pictures.cog._delete_files(gid, self.kind)
            c[f"{self.kind}_file"], c[f"{self.kind}_url"] = None, url
            if self.kind == "thumbnail":
                c["thumbnail_mode"] = "custom"
            self.pictures.cog.save()
            notes.append(f"{self.kind.title()} link saved.")
        await self.pictures.refresh(interaction, notes)


# ---------------- screens
class PanelView(OwnedView):
    def embed(self, notes=None):
        return summary_embed(self.c, notes)

    @discord.ui.button(label="Messages", emoji="📝", style=discord.ButtonStyle.primary, row=0)
    async def messages(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MessagesModal(self))

    @discord.ui.button(label="Look", emoji="🎨", style=discord.ButtonStyle.primary, row=0)
    async def look(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LookModal(self))

    @discord.ui.button(label="Pictures", emoji="🖼️", style=discord.ButtonStyle.primary, row=0)
    async def pictures(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        view = PicturesView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Preview", emoji="👁️", style=discord.ButtonStyle.success, row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        content, embed, files = build_welcome(interaction.user, self.c)
        header = "**Preview** (this is how new members see it):"
        await interaction.response.send_message(content=f"{header}\n{content}" if content else header,
                                                embed=embed, files=files, ephemeral=True)

    @discord.ui.button(label="Channels", emoji="📣", style=discord.ButtonStyle.secondary, row=1)
    async def channels(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        view = ChannelsView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Auto-roles", emoji="🎭", style=discord.ButtonStyle.secondary, row=1)
    async def autoroles(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        view = AutoRolesView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Anti-raid", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def antiraid(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AntiRaidModal(self))

    @discord.ui.button(label="Verify button", emoji="✅", style=discord.ButtonStyle.secondary, row=1)
    async def verify_setup(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        view = VerifySetupView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(), view=view)

    @discord.ui.button(label="Reset look", emoji="♻️", style=discord.ButtonStyle.danger, row=2)
    async def reset_look(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        view = ConfirmResetView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(), view=view)


class PicturesView(OwnedView):
    def __init__(self, cog, interaction):
        super().__init__(cog, interaction)
        mode = self.c["thumbnail_mode"]
        options = [discord.SelectOption(label=label, value=value, emoji=emoji, default=(mode == value))
                   for label, value, emoji in (("Member's avatar", "avatar", "🧑"), ("Server icon", "server", "🏠"),
                                               ("Custom picture", "custom", "🖼️"), ("No thumbnail", "none", "🚫"))]
        self.select = discord.ui.Select(placeholder="Thumbnail (small picture, top right)", options=options, row=0)
        self.select.callback = self.on_mode
        self.add_item(self.select)
        self.add_item(BackButton(row=2))

    def embed(self, notes=None):
        c = self.c
        thumb = c["thumbnail_mode"]
        if thumb == "custom":
            thumb += " (uploaded picture)" if image_path(c, "thumbnail") else " (link)" if c["thumbnail_url"] else " (nothing set yet: add a link)"
        image = "uploaded picture" if image_path(c, "image") else ("link" if c["image_url"] else "none")
        lines = [f"⚠️ {n}" for n in (notes or [])] + ([""] if notes else []) + [
            f"🖼️ **Thumbnail:** {thumb}",
            f"🌄 **Big image:** {image}",
            "",
            "Pick the thumbnail type in the dropdown. Use the buttons to paste a picture link.",
            "💡 To **upload** a picture file instead, run `/welcome` and fill in the `image` or `thumbnail` option.",
        ]
        return web_embed("Welcome Pictures", "\n".join(lines), BLUE)

    async def refresh(self, interaction, notes=None):
        self.stop()
        view = PicturesView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(notes), view=view)

    async def on_mode(self, interaction: discord.Interaction):
        self.c["thumbnail_mode"] = self.select.values[0]
        self.cog.save()
        notes = []
        if self.c["thumbnail_mode"] == "custom" and not (image_path(self.c, "thumbnail") or self.c["thumbnail_url"]):
            notes.append("Custom needs a picture: press 'Thumbnail link'.")
        await self.refresh(interaction, notes)

    @discord.ui.button(label="Thumbnail link", emoji="🔗", style=discord.ButtonStyle.primary, row=1)
    async def thumb_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LinkModal(self, "thumbnail"))

    @discord.ui.button(label="Image link", emoji="🔗", style=discord.ButtonStyle.primary, row=1)
    async def image_link(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(LinkModal(self, "image"))

    @discord.ui.button(label="Remove image", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def remove_image(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog._delete_files(self.guild.id, "image")
        self.c["image_file"] = self.c["image_url"] = None
        self.cog.save()
        await self.refresh(interaction, ["Big image removed."])


class ChannelsView(OwnedView):
    def __init__(self, cog, interaction):
        super().__init__(cog, interaction)
        c = self.c
        for attr, key, placeholder, row in (("welcome_sel", "welcome_channel", "Send welcome messages to…", 0),
                                            ("goodbye_sel", "goodbye_channel", "Send goodbye messages to…", 1)):
            kwargs = {}
            current = self.guild.get_channel(c[key]) if c[key] else None
            if current is not None:
                kwargs["default_values"] = [current]
            sel = discord.ui.ChannelSelect(channel_types=[discord.ChannelType.text], placeholder=placeholder,
                                           min_values=1, max_values=1, row=row, **kwargs)
            sel.callback = self._make_callback(sel, key)
            setattr(self, attr, sel)
            self.add_item(sel)
        self.add_item(BackButton(row=2))

    def embed(self, notes=None):
        c = self.c
        ch = lambda i: f"<#{i}>" if i else "off"
        lines = [f"⚠️ {n}" for n in (notes or [])] + ([""] if notes else []) + [
            f"📣 **Welcome channel:** {ch(c['welcome_channel'])}",
            f"🚪 **Goodbye channel:** {ch(c['goodbye_channel'])}",
            "",
            "Pick a channel from each dropdown. Use the red buttons to turn a message off.",
        ]
        return web_embed("Welcome Channels", "\n".join(lines), BLUE)

    def _make_callback(self, select, key):
        async def callback(interaction: discord.Interaction):
            picked = self.guild.get_channel(select.values[0].id)
            notes = []
            if picked is None:
                notes.append("I can't see that channel.")
            else:
                self.c[key] = picked.id
                self.cog.save()
                if not picked.permissions_for(self.guild.me).send_messages:
                    notes.append(f"I can't send messages in {picked.mention} yet. Give me that permission there.")
            await self.refresh(interaction, notes)
        return callback

    async def refresh(self, interaction, notes=None):
        self.stop()
        view = ChannelsView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(notes), view=view)

    @discord.ui.button(label="Turn off welcome", emoji="🔕", style=discord.ButtonStyle.danger, row=2)
    async def off_welcome(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.c["welcome_channel"] = None
        self.cog.save()
        await self.refresh(interaction, ["Welcome messages are off."])

    @discord.ui.button(label="Turn off goodbye", emoji="🔕", style=discord.ButtonStyle.danger, row=2)
    async def off_goodbye(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.c["goodbye_channel"] = None
        self.cog.save()
        await self.refresh(interaction, ["Goodbye messages are off."])


class AutoRolesView(OwnedView):
    def __init__(self, cog, interaction):
        super().__init__(cog, interaction)
        current = [r for r in (self.guild.get_role(i) for i in self.c["auto_roles"]) if r]
        kwargs = {"default_values": current} if current else {}
        self.select = discord.ui.RoleSelect(placeholder="Roles every new member gets (pick none to clear)",
                                            min_values=0, max_values=10, row=0, **kwargs)
        self.select.callback = self.on_pick
        self.add_item(self.select)
        self.add_item(BackButton(row=1))

    def embed(self, notes=None):
        roles = ", ".join(f"<@&{i}>" for i in self.c["auto_roles"]) or "none"
        lines = [f"⚠️ {n}" for n in (notes or [])] + ([""] if notes else []) + [
            f"🎭 **Auto-roles:** {roles}", "",
            "New members get these roles the moment they join. My role must be above them in Server Settings → Roles."]
        return web_embed("Welcome Auto-Roles", "\n".join(lines), BLUE)

    async def on_pick(self, interaction: discord.Interaction):
        good, notes = [], []
        for role in self.select.values:
            if role.is_default() or role.managed:
                notes.append(f"Skipped {role.mention} (I can't hand that role out).")
            elif role >= self.guild.me.top_role:
                notes.append(f"Skipped {role.mention} (it's above my role).")
            else:
                good.append(role.id)
        self.c["auto_roles"] = good
        self.cog.save()
        self.stop()
        view = AutoRolesView(self.cog, interaction)
        await interaction.response.edit_message(embed=view.embed(notes), view=view)


class VerifySetupView(OwnedView):
    def __init__(self, cog, interaction):
        super().__init__(cog, interaction)
        self.role = self.channel = None
        self.role_sel = discord.ui.RoleSelect(placeholder="Role members get after verifying", min_values=1, max_values=1, row=0)
        self.chan_sel = discord.ui.ChannelSelect(channel_types=[discord.ChannelType.text],
                                                 placeholder="Channel to post the Verify button in", min_values=1, max_values=1, row=1)
        self.role_sel.callback, self.chan_sel.callback = self.on_role, self.on_channel
        self.add_item(self.role_sel)
        self.add_item(self.chan_sel)
        self.post_btn = discord.ui.Button(label="Post Verify button", emoji="✅", style=discord.ButtonStyle.success, row=2, disabled=True)
        self.post_btn.callback = self.post
        self.add_item(self.post_btn)
        self.add_item(BackButton(row=2))

    def embed(self, notes=None):
        c = self.c
        current = f"<@&{c['verify_role']}>" if c["verify_role"] else "not set up"
        chosen_role = self.role.mention if self.role else "—"
        chosen_chan = self.channel.mention if self.channel else "—"
        lines = [f"⚠️ {n}" for n in (notes or [])] + ([""] if notes else []) + [
            f"✅ **Current verify role:** {current}", "",
            f"Role chosen: {chosen_role}", f"Channel chosen: {chosen_chan}", "",
            "Pick a role and a channel, then press **Post Verify button**. Hide your other channels from @everyone "
            "and show them to this role, so members must verify first."]
        return web_embed("Verification Button", "\n".join(lines), BLUE)

    def _update(self):
        self.post_btn.disabled = not (self.role and self.channel)

    async def on_role(self, interaction: discord.Interaction):
        self.role = self.select_value(self.role_sel)
        self._update()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_channel(self, interaction: discord.Interaction):
        self.channel = self.guild.get_channel(self.chan_sel.values[0].id)
        self._update()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @staticmethod
    def select_value(sel):
        return sel.values[0] if sel.values else None

    async def post(self, interaction: discord.Interaction):
        role, channel = self.role, self.channel
        if role is None or channel is None:
            return await interaction.response.send_message("Pick a role and a channel first.", ephemeral=True)
        if role.is_default() or role.managed or role >= self.guild.me.top_role:
            return await self._note(interaction, f"I can't hand out {role.mention} (managed, @everyone, or above my role).")
        try:
            await channel.send(embed=web_embed("Verification", "Press the button below to verify and unlock the server. 🕸️", RED),
                               view=VerifyView())
        except discord.HTTPException:
            return await self._note(interaction, f"I can't post in {channel.mention}. Check my permissions there.")
        self.c["verify_role"] = role.id
        self.cog.save()
        await show_main(self.cog, interaction, [f"Verify button posted in {channel.mention}. Members get {role.mention}."], old=self)

    async def _note(self, interaction, text):
        await interaction.response.edit_message(embed=self.embed([text]), view=self)


class ConfirmResetView(OwnedView):
    def embed(self):
        return web_embed("Reset the Look?", "This restores the default text, title, footer, color, thumbnail and image, "
                                            "and deletes uploaded pictures. Your messages, channels and roles stay.", DARK)

    @discord.ui.button(label="Yes, reset it", emoji="♻️", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cog.reset_look(self.guild.id)
        await show_main(self.cog, interaction, ["The look was reset to the default."], old=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await show_main(self.cog, interaction, old=self)


class Welcome(commands.Cog):
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

    # ================================================================ HELPERS
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

    def reset_look(self, guild_id: int):
        c = self.cfg(guild_id)
        for kind in ("thumbnail", "image"):
            self._delete_files(guild_id, kind)
        for key in LOOK_KEYS:
            c[key] = DEFAULTS[key]
        self.save()

    # ================================================================ THE ONE COMMAND
    @app_commands.command(name="welcome", description="Set up welcome messages, look, pictures, auto-roles and verification")
    @app_commands.describe(image="Optional: upload a big picture for the welcome message",
                           thumbnail="Optional: upload a small picture (thumbnail)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def welcome(self, interaction: discord.Interaction, image: Optional[discord.Attachment] = None,
                      thumbnail: Optional[discord.Attachment] = None):
        c = self.cfg(interaction.guild.id)
        notes = []
        for kind, attachment in (("image", image), ("thumbnail", thumbnail)):
            if attachment is None:
                continue
            try:
                c[f"{kind}_file"] = await self._save_upload(interaction.guild.id, kind, attachment)
            except ValueError as e:
                notes.append(f"{kind.title()} not saved: {e}")
                continue
            c[f"{kind}_url"] = None
            if kind == "thumbnail":
                c["thumbnail_mode"] = "custom"
            notes.append(f"{kind.title()} uploaded.")
        if image is not None or thumbnail is not None:
            self.save()
        view = PanelView(self, interaction)
        await interaction.response.send_message(embed=summary_embed(c, notes), view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
