import asyncio
import copy
import json
import os
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK, report_ui_error
from vm_banner import banner_file, BANNER_NAME

DATA_FILE = "voicemaster.json"

DEFAULT_STATE = {
    "name": "",        # empty = use "<member>'s Web"
    "limit": 0,        # 0 = unlimited
    "status": "",
    "locked": False,
    "hidden": False,
    "permitted": [],
    "rejected": [],
    "bitrate": 0,      # 0 = server default
    "region": "",      # "" = automatic
}


# ------------------------------------------------------------------ storage
class Store:
    """Tiny JSON file store: guild config, live rooms, and per-user saved settings."""

    def __init__(self, path: str = DATA_FILE):
        self.path = path
        self.data = {"guilds": {}, "rooms": {}, "profiles": {}}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    # guild config
    def get_guild(self, gid):
        return self.data["guilds"].get(str(gid))

    def set_guild(self, gid, category_id, creator_id):
        self.data["guilds"][str(gid)] = {"category_id": category_id, "creator_id": creator_id}
        self.save()

    def remove_guild(self, gid):
        self.data["guilds"].pop(str(gid), None)
        self.save()

    # live rooms
    def get_room(self, cid):
        return self.data["rooms"].get(str(cid))

    def add_room(self, cid, gid, owner_id, state):
        self.data["rooms"][str(cid)] = {"guild": gid, "owner": owner_id, "state": state}
        self.save()

    def remove_room(self, cid):
        if self.data["rooms"].pop(str(cid), None) is not None:
            self.save()

    # saved per-user settings (what makes your room come back the same)
    def get_profile(self, gid, uid):
        state = copy.deepcopy(DEFAULT_STATE)
        state.update(self.data["profiles"].get(f"{gid}:{uid}", {}))
        return state

    def save_profile(self, gid, uid, state):
        self.data["profiles"][f"{gid}:{uid}"] = copy.deepcopy(state)
        self.save()


# ------------------------------------------------------------------ helpers
async def _reply(interaction: discord.Interaction, title: str, text: str, color: int = RED):
    embed = web_embed(title, text, color)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


def result_embed(result) -> discord.Embed:
    """(title, text, color[, fields]) -> a clean embed. fields = [(name, value, inline), ...]"""
    title, text, color, *rest = result
    e = web_embed(title, text, color)
    for name, value, inline in (rest[0] if rest else []):
        e.add_field(name=name, value=(value or "—")[:1024], inline=inline)
    return e


NOT_OWNER = ("Hands off my web!", "Only the room owner can do that. 🕷️", DARK)


def _mentions(ids) -> str:
    return ", ".join(f"<@{i}>" for i in ids) or "nobody"


async def _edit_original(interaction: discord.Interaction, embed: discord.Embed, view=None):
    """Edit the message a component sits on. Returns the message (or None if Discord refused)."""
    try:
        return await interaction.edit_original_response(embed=embed, view=view)
    except discord.HTTPException:
        return None


async def _send_menu(interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View):
    """Send an ephemeral menu and remember its message so it can be closed neatly when it times out."""
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    try:
        view.message = await interaction.original_response()
    except discord.HTTPException:
        pass


async def safe_edit(channel: discord.VoiceChannel, **kwargs):
    """Edit a channel. Returns None on success, or an error string."""
    try:
        await asyncio.wait_for(channel.edit(**kwargs), timeout=10)
        return None
    except asyncio.TimeoutError:
        return ("Discord is rate-limiting channel edits (renames are limited to "
                "2 per 10 minutes). Try again in a bit.")
    except TypeError:
        return "Your discord.py is too old for this. Run `pip install -U discord.py` (2.5+)."
    except discord.HTTPException as e:
        return f"Discord said no: {e.text}"


def build_overwrites(guild: discord.Guild, owner: discord.Member, state: dict):
    ow = {
        guild.default_role: discord.PermissionOverwrite(
            connect=False if state["locked"] else None,
            view_channel=False if state["hidden"] else None,
        )
    }
    for uid in state["permitted"]:
        m = guild.get_member(uid)
        if m and m != owner:
            ow[m] = discord.PermissionOverwrite(connect=True, view_channel=True)
    for uid in state["rejected"]:
        m = guild.get_member(uid)
        if m and m != owner:
            ow[m] = discord.PermissionOverwrite(connect=False, view_channel=False)
    ow[owner] = discord.PermissionOverwrite(connect=True, view_channel=True)
    ow[guild.me] = discord.PermissionOverwrite(
        view_channel=True, connect=True, manage_channels=True,
        move_members=True, send_messages=True, embed_links=True,
    )
    return ow


def panel_embed(owner: discord.Member, room: dict = None, channel=None,
                present: bool = None, count: int = None) -> discord.Embed:
    """Live control-panel embed. Reads the room's current state so it can be refreshed after every change."""
    st = (room or {}).get("state") or DEFAULT_STATE
    locked, hidden = st["locked"], st["hidden"]
    color = RED if locked else (DARK if hidden else BLUE)     # red = locked, grey = hidden, blue = open

    members = list(channel.members) if channel is not None else []
    if present is None:
        present = owner in members
    if count is None:
        count = len(members)
    limit = st["limit"]

    kbps = (channel.bitrate // 1000) if channel is not None else 64
    region = (getattr(channel, "rtc_region", None) if channel is not None else None) or "auto"
    region = str(region)

    e = web_embed(
        "Web Control Panel",
        "Run your web from the two menus below. 🕷️\n"
        "⚙️ **Room Settings** • rename, limit, status, bitrate, region, invite, reset\n"
        "🔐 **Privacy & Members** • lock, hide, permit, reject, kick, transfer\n"
        "*Only the owner can change things • anyone can use Info.*",
        color,
    )
    e.set_author(name=f"{owner.display_name}'s Web", icon_url=owner.display_avatar.url)
    e.set_thumbnail(url=owner.display_avatar.url)
    e.add_field(name="👑 Owner", value=f"{owner.mention}{'' if present else ' • *away*'}", inline=True)
    e.add_field(name="🔐 Access", value="🔒 Locked" if locked else "🔓 Open", inline=True)
    e.add_field(name="👁️ Visibility", value="👻 Hidden" if hidden else "👁️ Visible", inline=True)
    e.add_field(name="👥 Capacity", value=f"{count}/{limit}" if limit else f"{count} • unlimited", inline=True)
    e.add_field(name="🎚️ Bitrate", value=f"{kbps} kbps", inline=True)
    e.add_field(name="🌍 Region", value=REGION_NAMES.get(region, region.title()), inline=True)
    def _short(ids):
        shown = ", ".join(f"<@{i}>" for i in ids[:3])
        return shown + (f" +{len(ids) - 3}" if len(ids) > 3 else "") if ids else "—"
    e.add_field(name="✅ Permitted", value=_short(st["permitted"]), inline=True)
    e.add_field(name="🚫 Blocked", value=_short(st["rejected"]), inline=True)
    created = f"<t:{int(channel.created_at.timestamp())}:R>" if channel is not None else "just now"
    e.add_field(name="🕐 Created", value=created, inline=True)
    e.add_field(name="🎙️ Status", value=(st["status"] or "—")[:100], inline=False)
    e.set_image(url=f"attachment://{BANNER_NAME}")
    e.set_footer(text=f"🕸️ Spidey • {channel.name}" if channel is not None else "🕸️ Spidey • Your friendly neighborhood bot")
    return e


# ------------------------------------------------------------------ modals
class RenameModal(discord.ui.Modal, title="Rename your web 🕸️"):
    new_name = discord.ui.TextInput(label="New room name", min_length=1, max_length=100)

    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.set_name(interaction, self.channel_id, str(self.new_name).strip())


class LimitModal(discord.ui.Modal, title="Set member limit 🕷️"):
    amount = discord.ui.TextInput(label="Limit (0 = unlimited, max 99)", min_length=1, max_length=2)

    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.set_limit(interaction, self.channel_id, str(self.amount).strip())


class StatusModal(discord.ui.Modal, title="Set room status 🕸️"):
    text = discord.ui.TextInput(label="Status (leave empty to clear)", required=False, max_length=500)

    def __init__(self, cog, channel_id):
        super().__init__()
        self.cog, self.channel_id = cog, channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await self.cog.set_status(interaction, self.channel_id, str(self.text).strip())


# ------------------------------------------------------------------ pickers
class _Menu(discord.ui.View):
    """Base for the small owner-only menus.

    Why this exists: the old pickers stopped themselves after one use but stayed on screen, so using the same
    dropdown a second time showed 'Spidey isn't responding'. These menus (a) answer Discord immediately,
    (b) show the result in the same message, (c) re-arm themselves when you may pick again, and
    (d) close with a friendly message when they time out.
    """

    def __init__(self, owner_id: int, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await _reply(interaction, "Hands off my web!", "This menu belongs to the room owner. 🕷️", DARK)
            return False
        return True

    async def on_timeout(self):
        if self.message is None:
            return
        try:
            await self.message.edit(
                embed=web_embed("Menu Closed", "That menu timed out. Open it again from the panel. 🕸️", DARK), view=None)
        except discord.HTTPException:
            pass

    async def _run(self, interaction, *args):
        """Run the handler; a crash becomes a friendly message instead of a dead menu."""
        try:
            return await self.handler(interaction, self.channel_id, *args)
        except Exception as e:
            traceback.print_exception(type(e), e, e.__traceback__)
            return ("Web Snagged!", "Something went wrong on my side. Try again. 🕸️", DARK)


class PickView(_Menu):
    """Dropdown to choose people for permit / reject / kick / transfer.

    `options=None` -> any server member (user picker, can choose several). Otherwise a list of room members.
    `keep_open=True` -> the menu re-arms itself after every pick so you can keep going.
    """

    def __init__(self, handler, channel_id, owner_id, placeholder, *, options=None, multi=False, keep_open=True):
        super().__init__(owner_id)
        self.handler, self.channel_id = handler, channel_id
        self.placeholder, self.options, self.multi, self.keep_open = placeholder, options, multi, keep_open
        if options is None:
            self.select = discord.ui.UserSelect(placeholder=placeholder, min_values=1, max_values=5 if multi else 1)
        else:
            self.select = discord.ui.Select(placeholder=placeholder, min_values=1, options=options,
                                            max_values=min(5, len(options)) if multi else 1)
        self.select.callback = self.picked
        self.add_item(self.select)

    def fresh(self):
        return PickView(self.handler, self.channel_id, self.owner_id, self.placeholder,
                        options=self.options, multi=self.multi, keep_open=self.keep_open)

    async def picked(self, interaction: discord.Interaction):
        await interaction.response.defer()                    # answer Discord first (3-second rule)
        members = []
        for v in self.select.values:
            uid = v.id if hasattr(v, "id") else int(v)
            m = interaction.guild.get_member(uid)
            if m is None:
                try:
                    m = await interaction.guild.fetch_member(uid)
                except discord.HTTPException:
                    m = None
            if m is not None:
                members.append(m)

        if members:
            result = await self._run(interaction, members)
        else:
            result = ("Web Snagged!", "I can't find that member in this server.", DARK)

        self.stop()
        new = self.fresh() if self.keep_open else None
        msg = await _edit_original(interaction, result_embed(result), new)
        if new is not None:
            new.message = msg


REGIONS_PLACEHOLDER = None  # (regions are defined just below)
REGIONS = [
    ("auto", "Automatic", "🌐"), ("brazil", "Brazil", "🇧🇷"), ("hongkong", "Hong Kong", "🇭🇰"),
    ("india", "India", "🇮🇳"), ("japan", "Japan", "🇯🇵"), ("rotterdam", "Rotterdam", "🇳🇱"),
    ("russia", "Russia", "🇷🇺"), ("singapore", "Singapore", "🇸🇬"), ("southafrica", "South Africa", "🇿🇦"),
    ("sydney", "Sydney", "🇦🇺"), ("us-central", "US Central", "🇺🇸"), ("us-east", "US East", "🇺🇸"),
    ("us-south", "US South", "🇺🇸"), ("us-west", "US West", "🇺🇸"),
]
REGION_NAMES = {k: n for k, n, _ in REGIONS}
BITRATES = [(8, "Tiny"), (16, "Low"), (32, "Light"), (64, "Standard"), (96, "Good"),
            (128, "High (Boost level 1)"), (256, "Very high (Boost level 2)"), (384, "Max (Boost level 3)")]


class ChoiceView(_Menu):
    """Single-choice dropdown (bitrate / region). Shows the result in the same message and closes."""

    def __init__(self, handler, channel_id, owner_id, options, placeholder):
        super().__init__(owner_id)
        self.handler, self.channel_id = handler, channel_id
        self.select = discord.ui.Select(placeholder=placeholder, min_values=1, max_values=1, options=options)
        self.select.callback = self.picked
        self.add_item(self.select)

    async def picked(self, interaction: discord.Interaction):
        await interaction.response.defer()
        result = await self._run(interaction, self.select.values[0])
        self.stop()
        await _edit_original(interaction, result_embed(result), None)


class ConfirmView(_Menu):
    def __init__(self, handler, channel_id, owner_id):
        super().__init__(owner_id, timeout=120)
        self.handler, self.channel_id = handler, channel_id

    @discord.ui.button(label="Yes, reset it", emoji="♻️", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        result = await self._run(interaction)
        self.stop()
        await _edit_original(interaction, result_embed(result), None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=web_embed("Cancelled", "Nothing was changed. 🕸️", DARK), view=None)


# ------------------------------------------------------------------ control panel
async def run_action(interaction: discord.Interaction, action: str):
    """Shared by the button panel and the old dropdown panel."""
    cog = interaction.client.get_cog("VoiceMaster")
    channel_id = interaction.channel.id
    room = cog.store.get_room(channel_id)

    if room is None:
        return await _reply(interaction, "Dead Web", "This room isn't active.", DARK)

    # Info and Claim are open to everyone (claim has its own checks: owner must have left).
    if action == "info":
        return await cog.info(interaction, channel_id)
    if action == "claim":
        return await cog.claim(interaction, channel_id)

    # Everything else needs the current owner.
    if room["owner"] != interaction.user.id:
        return await _reply(interaction, "Hands off my web!",
                            "Only the room owner can use this control. 🕷️", DARK)

    # Modals must be the initial interaction response.
    if action == "rename":
        return await interaction.response.send_modal(RenameModal(cog, channel_id))
    if action == "limit":
        return await interaction.response.send_modal(LimitModal(cog, channel_id))
    if action == "status":
        return await interaction.response.send_modal(StatusModal(cog, channel_id))

    if action == "lock":
        return await cog.toggle_lock(interaction, channel_id)
    if action == "hide":
        return await cog.toggle_hide(interaction, channel_id)
    if action == "invite":
        return await cog.invite_link(interaction, channel_id)
    if action == "clear":
        return await cog.clear_lists(interaction, channel_id)

    if action == "bitrate":
        limit_k = int(interaction.guild.bitrate_limit // 1000)
        opts = [discord.SelectOption(label=f"{k} kbps", description=d, value=str(k))
                for k, d in BITRATES if k <= limit_k][:25]
        return await _send_menu(
            interaction,
            web_embed("Voice Quality", "Higher bitrate = clearer audio. The max depends on this "
                                       f"server's Boost level (up to **{limit_k} kbps** here). 🎚️", DARK),
            ChoiceView(cog.set_bitrate, channel_id, interaction.user.id, opts, "🎚️ Choose a bitrate..."))
    if action == "region":
        opts = [discord.SelectOption(label=n, value=k, emoji=e) for k, n, e in REGIONS]
        return await _send_menu(
            interaction,
            web_embed("Voice Region", "Pick the server closest to everyone for the lowest lag. 🌍", DARK),
            ChoiceView(cog.set_region, channel_id, interaction.user.id, opts, "🌍 Choose a region..."))
    if action == "reset":
        return await _send_menu(
            interaction,
            web_embed("Reset Room?", "This restores the default name, limit, status, bitrate and region, "
                                     "unlocks and unhides the room, and clears the permit/block lists. ♻️", DARK),
            ConfirmView(cog.reset_room, channel_id, interaction.user.id))

    st = room["state"]
    if action == "permit":
        e = web_embed("Permit Members",
                      "Pick up to **5** people who may **always join**, even when your room is locked or hidden. ✅\n"
                      "*Pick someone who's already on the list to take them off.*", DARK)
        e.add_field(name=f"🎟️ Guest list ({len(st['permitted'])})", value=_mentions(st["permitted"]), inline=False)
        return await _send_menu(
            interaction, e,
            PickView(cog.permit, channel_id, interaction.user.id, "✅ Choose who can join...", multi=True))
    if action == "reject":
        e = web_embed("Block Members",
                      "Pick up to **5** people to **keep out**. They can't see or join your room, and they're removed "
                      "if they're inside. 🚫\n*Pick someone who's already blocked to let them back.*", DARK)
        e.add_field(name=f"⛔ Block list ({len(st['rejected'])})", value=_mentions(st["rejected"]), inline=False)
        return await _send_menu(
            interaction, e,
            PickView(cog.reject, channel_id, interaction.user.id, "🚫 Choose who to block...", multi=True))
    if action in ("kick", "transfer"):
        channel = interaction.guild.get_channel(channel_id)
        people = [m for m in (channel.members if channel else []) if not m.bot and m.id != room["owner"]]
        if not people:
            return await _reply(interaction, "Empty Web", "Nobody else is in your room right now. 🕸️", DARK)
        opts = [discord.SelectOption(label=m.display_name[:100], value=str(m.id), description=f"@{m.name}"[:100])
                for m in people[:25]]
        if action == "kick":
            return await _send_menu(
                interaction, web_embed("Kick Members", "Pick up to **5** people to remove from the room. 🥾", DARK),
                PickView(cog.kick, channel_id, interaction.user.id, "🥾 Choose who to kick...",
                         options=opts, multi=True, keep_open=False))
        return await _send_menu(
            interaction, web_embed("Transfer Ownership", "Choose who becomes the new owner. 👑", DARK),
            PickView(cog.transfer, channel_id, interaction.user.id, "👑 Choose the new owner...",
                     options=opts, keep_open=False))


class _PanelBase(discord.ui.View):
    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        await report_ui_error(interaction, error)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cog = interaction.client.get_cog("VoiceMaster")
        channel = interaction.channel
        room = cog.store.get_room(channel.id) if cog and channel else None
        if room is None:
            await _reply(interaction, "Dead Web", "This isn't an active VoiceMaster room.", DARK)
            return False
        return True


B = discord.ButtonStyle


class ButtonControlPanel(_PanelBase):
    """Previous button panel. Only registered so panels already posted in live rooms keep working."""

    def __init__(self, state: dict = None, owner_present: bool = True):
        super().__init__(timeout=None)
        if state is None:
            return
        for child in self.children:
            cid = getattr(child, "custom_id", None)
            if cid == "vm:lock":
                child.label = "Unlock" if state["locked"] else "Lock"
                child.style = B.success if state["locked"] else B.danger
            elif cid == "vm:hide":
                child.label = "Show" if state["hidden"] else "Hide"
                child.style = B.success if state["hidden"] else B.secondary
            elif cid == "vm:claim":
                child.disabled = owner_present

    # row 0 - room settings
    @discord.ui.button(label="Rename", emoji="📝", style=B.primary, custom_id="vm:rename", row=0)
    async def b_rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "rename")

    @discord.ui.button(label="Limit", emoji="👥", style=B.primary, custom_id="vm:limit", row=0)
    async def b_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "limit")

    @discord.ui.button(label="Status", emoji="🎙️", style=B.primary, custom_id="vm:status", row=0)
    async def b_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "status")

    # row 1 - privacy
    @discord.ui.button(label="Lock", emoji="🔒", style=B.danger, custom_id="vm:lock", row=1)
    async def b_lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "lock")

    @discord.ui.button(label="Hide", emoji="👻", style=B.secondary, custom_id="vm:hide", row=1)
    async def b_hide(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "hide")

    @discord.ui.button(label="Info", emoji="ℹ️", style=B.secondary, custom_id="vm:info", row=1)
    async def b_info(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "info")

    # row 2 - members
    @discord.ui.button(label="Permit", emoji="✅", style=B.success, custom_id="vm:permit", row=2)
    async def b_permit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "permit")

    @discord.ui.button(label="Reject", emoji="🚫", style=B.danger, custom_id="vm:reject", row=2)
    async def b_reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "reject")

    @discord.ui.button(label="Kick", emoji="🥾", style=B.secondary, custom_id="vm:kick", row=2)
    async def b_kick(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "kick")

    # row 3 - ownership
    @discord.ui.button(label="Transfer", emoji="👑", style=B.secondary, custom_id="vm:transfer", row=3)
    async def b_transfer(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "transfer")

    @discord.ui.button(label="Claim", emoji="🕷️", style=B.secondary, custom_id="vm:claim", row=3)
    async def b_claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await run_action(interaction, "claim")


class LegacyControlPanel(_PanelBase):
    """The old dropdown panel. Only registered so panels already posted in live rooms keep working."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="🕷️ Choose a VoiceMaster action...",
        custom_id="vm:menu", min_values=1, max_values=1,
        options=[
            discord.SelectOption(label="Rename Room", emoji="📝", value="rename"),
            discord.SelectOption(label="Member Limit", emoji="👥", value="limit"),
            discord.SelectOption(label="Room Status", emoji="🎙️", value="status"),
            discord.SelectOption(label="Lock / Unlock", emoji="🔒", value="lock"),
            discord.SelectOption(label="Hide / Show", emoji="👻", value="hide"),
            discord.SelectOption(label="Permit Member", emoji="✅", value="permit"),
            discord.SelectOption(label="Reject Member", emoji="🚫", value="reject"),
            discord.SelectOption(label="Kick Member", emoji="👢", value="kick"),
            discord.SelectOption(label="Transfer Ownership", emoji="👑", value="transfer"),
            discord.SelectOption(label="Claim Room", emoji="🕷️", value="claim"),
            discord.SelectOption(label="Room Information", emoji="ℹ️", value="info"),
        ],
        row=0,
    )
    async def action_menu(self, interaction: discord.Interaction, select: discord.ui.Select):
        await run_action(interaction, select.values[0])


class ControlPanel(_PanelBase):
    """Two-dropdown panel: Room Settings + Privacy & Members. Pass the room state for live labels."""

    def __init__(self, state: dict = None, owner_present: bool = True):
        super().__init__(timeout=None)
        # options are shared between instances by discord.py, so copy before editing labels
        self.members_menu.options = [
            discord.SelectOption(label=o.label, value=o.value, emoji=o.emoji, description=o.description)
            for o in self.members_menu.options
        ]
        if state is None:
            return
        for o in self.members_menu.options:
            if o.value == "lock":
                o.label = "Unlock Room" if state["locked"] else "Lock Room"
                o.description = ("Currently locked - let everyone join again" if state["locked"]
                                 else "Stop new people from joining")
            elif o.value == "hide":
                o.label = "Show Room" if state["hidden"] else "Hide Room"
                o.description = ("Currently hidden - make it visible again" if state["hidden"]
                                 else "Hide the room from everyone else")
            elif o.value == "claim":
                o.description = ("Owner is away - take over this room!" if not owner_present
                                 else "Only works after the owner has left")

    async def _go(self, interaction: discord.Interaction, action: str):
        try:
            await run_action(interaction, action)
        finally:    # re-sending the panel also resets the dropdown so the same option can be picked again
            try:
                cog = interaction.client.get_cog("VoiceMaster")
                if cog:
                    cog.schedule_refresh(interaction.channel.id)
            except Exception:
                pass

    @discord.ui.select(
        placeholder="⚙️ Room Settings...", custom_id="vm:settings", min_values=1, max_values=1, row=0,
        options=[
            discord.SelectOption(label="Rename Room", emoji="📝", value="rename",
                                 description="Change the room's name"),
            discord.SelectOption(label="Member Limit", emoji="👥", value="limit",
                                 description="Set how many people can join (0 = unlimited)"),
            discord.SelectOption(label="Room Status", emoji="🎙️", value="status",
                                 description="Show a short status under the channel name"),
            discord.SelectOption(label="Bitrate", emoji="🎚️", value="bitrate",
                                 description="Change the audio quality"),
            discord.SelectOption(label="Voice Region", emoji="🌍", value="region",
                                 description="Pick the closest voice server for less lag"),
            discord.SelectOption(label="Invite Link", emoji="🔗", value="invite",
                                 description="Create a 1-hour invite straight to this room"),
            discord.SelectOption(label="Reset Room", emoji="♻️", value="reset",
                                 description="Restore every setting to default"),
        ],
    )
    async def settings_menu(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self._go(interaction, select.values[0])

    @discord.ui.select(
        placeholder="🔐 Privacy & Members...", custom_id="vm:members", min_values=1, max_values=1, row=1,
        options=[
            discord.SelectOption(label="Lock Room", emoji="🔒", value="lock",
                                 description="Stop new people from joining"),
            discord.SelectOption(label="Hide Room", emoji="👻", value="hide",
                                 description="Hide the room from everyone else"),
            discord.SelectOption(label="Permit Member", emoji="✅", value="permit",
                                 description="Let someone in even when locked or hidden"),
            discord.SelectOption(label="Reject Member", emoji="🚫", value="reject",
                                 description="Block someone from joining or seeing the room"),
            discord.SelectOption(label="Kick Member", emoji="🥾", value="kick",
                                 description="Remove someone from the room right now"),
            discord.SelectOption(label="Clear Lists", emoji="🧹", value="clear",
                                 description="Empty the permit and block lists"),
            discord.SelectOption(label="Transfer Ownership", emoji="👑", value="transfer",
                                 description="Hand the room over to someone inside"),
            discord.SelectOption(label="Claim Room", emoji="🕷️", value="claim",
                                 description="Only works after the owner has left"),
            discord.SelectOption(label="Room Information", emoji="ℹ️", value="info",
                                 description="See every setting (anyone can use this)"),
        ],
    )
    async def members_menu(self, interaction: discord.Interaction, select: discord.ui.Select):
        await self._go(interaction, select.values[0])


# ------------------------------------------------------------------ the cog
class VoiceMaster(commands.Cog):
    voicemaster = app_commands.Group(
        name="voicemaster",
        description="Spidey VoiceMaster setup",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = Store()
        self.creating: set[int] = set()
        self._refresh_tasks: dict = {}

    async def cog_load(self):
        self.bot.add_view(ControlPanel())
        self.bot.add_view(ButtonControlPanel())
        self.bot.add_view(LegacyControlPanel())

    # ---------- admin commands
    @voicemaster.command(name="setup", description="Set up VoiceMaster in a category")
    @app_commands.describe(category="Category where the Join-to-Create channel and rooms will live")
    async def setup(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        guild = interaction.guild
        await interaction.response.defer()

        old = self.store.get_guild(guild.id)
        if old:
            old_ch = guild.get_channel(old["creator_id"])
            if old_ch:
                try:
                    await old_ch.delete(reason="Spidey VoiceMaster re-setup")
                except discord.HTTPException:
                    pass
        try:
            creator = await guild.create_voice_channel(
                "🕸️ Join to Create", category=category, user_limit=1,
                reason="Spidey VoiceMaster setup",
            )
        except discord.Forbidden:
            return await interaction.followup.send(
                embed=web_embed("Web Tangled!",
                                "I need **Manage Channels**, **Move Members** and **Connect** "
                                "permissions in that category.", DARK),
                ephemeral=True,
            )

        self.store.set_guild(guild.id, category.id, creator.id)
        await interaction.followup.send(embed=web_embed(
            "VoiceMaster Configured!",
            f"VoiceMaster is now configured in **{category.name}** 🕸️\n\n"
            f"Join {creator.mention} (limit 1) and I'll spin a private web just for you, "
            "move you into it, and drop a control panel in its chat. 🕷️",
        ))

    @voicemaster.command(name="disable", description="Turn off VoiceMaster in this server")
    async def disable(self, interaction: discord.Interaction):
        cfg = self.store.get_guild(interaction.guild.id)
        if not cfg:
            return await _reply(interaction, "Nothing to Remove", "VoiceMaster isn't set up here.", DARK)
        ch = interaction.guild.get_channel(cfg["creator_id"])
        if ch:
            try:
                await ch.delete(reason="Spidey VoiceMaster disabled")
            except discord.HTTPException:
                pass
        self.store.remove_guild(interaction.guild.id)
        await _reply(interaction, "Web Cut!", "VoiceMaster is disabled. Existing rooms vanish when empty. 🕸️")

    # ---------- listeners
    @commands.Cog.listener()
    async def on_ready(self):
        # clean up rooms that vanished or emptied while the bot was offline
        for cid in list(self.store.data["rooms"].keys()):
            ch = self.bot.get_channel(int(cid))
            if ch is None:
                self.store.remove_room(cid)
            elif not ch.members:
                await self.delete_room(ch)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return

        if after.channel:
            cfg = self.store.get_guild(member.guild.id)
            if cfg and after.channel.id == cfg["creator_id"]:
                await self.handle_join_creator(member, cfg)

        if (before.channel and self.store.get_room(before.channel.id)
                and len(before.channel.members) == 0):
            await self.delete_room(before.channel)

        try:
            for ch in (before.channel, after.channel):      # refresh capacity / owner-away on the panel
                if ch and self.store.get_room(ch.id):
                    self.schedule_refresh(ch.id)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        self.store.remove_room(channel.id)

    # ---------- room lifecycle
    async def handle_join_creator(self, member: discord.Member, cfg: dict):
        if member.bot or member.id in self.creating:
            return
        self.creating.add(member.id)
        try:
            await self.create_room(member, cfg)
        except discord.HTTPException:
            pass
        finally:
            self.creating.discard(member.id)

    async def create_room(self, member: discord.Member, cfg: dict):
        guild = member.guild
        state = self.store.get_profile(guild.id, member.id)  # their saved settings
        category = guild.get_channel(cfg["category_id"])
        name = (state["name"] or f"🕷️ {member.display_name}'s Web")[:100]

        channel = await guild.create_voice_channel(
            name=name,
            category=category if isinstance(category, discord.CategoryChannel) else None,
            user_limit=state["limit"],
            overwrites=build_overwrites(guild, member, state),
            reason=f"Spidey VoiceMaster room for {member}",
        )
        self.store.add_room(channel.id, guild.id, member.id, state)

        try:
            await member.move_to(channel)
        except discord.HTTPException:
            return await self.delete_room(channel)

        if state["status"]:
            await safe_edit(channel, status=state["status"])
        extra = {}
        if state.get("bitrate"):
            extra["bitrate"] = min(int(state["bitrate"]), int(guild.bitrate_limit))
        if state.get("region"):
            extra["rtc_region"] = state["region"]
        if extra:
            await safe_edit(channel, **extra)

        room = self.store.get_room(channel.id)
        embed = panel_embed(member, room, channel, present=True, count=max(1, len(channel.members)))
        view = ControlPanel(room["state"], True)
        try:
            msg = await channel.send(content=member.mention, embed=embed, view=view, file=banner_file())
        except discord.HTTPException:
            embed.set_image(url=None)        # no Attach Files permission? still post the panel
            msg = await channel.send(content=member.mention, embed=embed, view=view)
        room["panel"] = msg.id
        self.store.save()

    async def delete_room(self, channel):
        self.store.remove_room(channel.id)
        try:
            await channel.delete(reason="Spidey VoiceMaster: room is empty")
        except discord.HTTPException:
            pass

    # ---------- shared checks
    def _room(self, interaction, channel_id):
        room = self.store.get_room(channel_id)
        channel = interaction.guild.get_channel(channel_id)
        if not room or channel is None or room["owner"] != interaction.user.id:
            return None, None
        return room, channel

    def _commit(self, room):
        """Save the room's settings to the owner's profile so their next room matches."""
        self.store.save_profile(room["guild"], room["owner"], room["state"])
        for cid, r in self.store.data["rooms"].items():     # keep the panel embed in sync
            if r is room:
                self.schedule_refresh(cid)
                break

    # ---------- live panel refresh
    def schedule_refresh(self, cid, delay: float = 0.8):
        cid = int(cid)
        task = self._refresh_tasks.get(cid)
        if task and not task.done():
            return                      # one already queued: it will read the latest state
        self._refresh_tasks[cid] = asyncio.create_task(self._refresh_later(cid, delay))

    async def _refresh_later(self, cid: int, delay: float = 0.8):
        try:
            await asyncio.sleep(delay)  # batch rapid changes / let Discord's cache catch up
            self._refresh_tasks.pop(cid, None)
            await self.refresh_panel(cid)
        except Exception as e:
            self._refresh_tasks.pop(cid, None)
            print(f"VoiceMaster: could not refresh panel {cid}: {e!r}")

    async def refresh_panel(self, cid: int):
        room = self.store.get_room(cid)
        channel = self.bot.get_channel(cid)
        if not room or not room.get("panel") or channel is None:
            return                      # old-style room (dropdown panel) or already gone
        owner = channel.guild.get_member(room["owner"])
        if owner is None:
            return
        present = owner in channel.members
        embed = panel_embed(owner, room, channel, present=present)
        view = ControlPanel(room["state"], present)
        msg = channel.get_partial_message(room["panel"])
        try:
            await msg.edit(embed=embed, view=view)
        except discord.NotFound:
            room.pop("panel", None)
            self.store.save()
        except discord.HTTPException:
            try:
                embed.set_image(url=None)
                await msg.edit(embed=embed, view=view)
            except discord.HTTPException:
                pass

    async def _not_owner(self, interaction):
        await _reply(interaction, "Hands off my web!", "Only the room owner can do that. 🕷️", DARK)

    # ---------- actions
    async def set_name(self, interaction, channel_id, name):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        err = await safe_edit(channel, name=name)
        if err:
            return await _reply(interaction, "Web Snagged!", err, DARK)
        room["state"]["name"] = name
        self._commit(room)
        await _reply(interaction, "Web Renamed!", f"Your room is now **{name}** 🕷️")

    async def set_limit(self, interaction, channel_id, raw):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        if not raw.isdigit() or not 0 <= int(raw) <= 99:
            return await _reply(interaction, "Web Snagged!", "Enter a number from 0 to 99.", DARK)
        amount = int(raw)
        err = await safe_edit(channel, user_limit=amount)
        if err:
            return await _reply(interaction, "Web Snagged!", err, DARK)
        room["state"]["limit"] = amount
        self._commit(room)
        await _reply(interaction, "Limit Set!",
                     f"Capacity is now **{amount if amount else 'unlimited'}** 🕸️")

    async def set_status(self, interaction, channel_id, text):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        err = await safe_edit(channel, status=text or None)
        if err:
            return await _reply(interaction, "Web Snagged!", err, DARK)
        room["state"]["status"] = text
        self._commit(room)
        await _reply(interaction, "Status Updated!", f"Status: **{text}**" if text else "Status cleared. 🕸️")

    async def toggle_lock(self, interaction, channel_id):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        locked = not room["state"]["locked"]
        try:
            await channel.set_permissions(interaction.guild.default_role,
                                          connect=False if locked else None)
        except discord.HTTPException as e:
            return await _reply(interaction, "Web Snagged!", e.text, DARK)
        room["state"]["locked"] = locked
        self._commit(room)
        await _reply(interaction, "Web Locked!" if locked else "Web Unlocked!",
                     "Nobody new can join unless permitted. 🔒" if locked else "Anyone can swing in again. 🕸️")

    async def toggle_hide(self, interaction, channel_id):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        hidden = not room["state"]["hidden"]
        try:
            await channel.set_permissions(interaction.guild.default_role,
                                          view_channel=False if hidden else None)
        except discord.HTTPException as e:
            return await _reply(interaction, "Web Snagged!", e.text, DARK)
        room["state"]["hidden"] = hidden
        self._commit(room)
        await _reply(interaction, "Web Hidden!" if hidden else "Web Visible!",
                     "Only you and permitted members can see this room. 👻" if hidden
                     else "Everyone can see the room again. 🕸️")

    async def permit(self, interaction, channel_id, targets):
        """Toggle people on the guest list. Returns (title, text, color) for the menu to show."""
        room, channel = self._room(interaction, channel_id)
        if not room:
            return NOT_OWNER
        st = room["state"]
        added, removed, skipped, problem = [], [], [], None
        for t in targets:
            if t.id == room["owner"] or t.id == self.bot.user.id:
                skipped.append(t)
                continue
            try:
                if t.id in st["permitted"]:
                    await channel.set_permissions(t, overwrite=None)
                    st["permitted"].remove(t.id)
                    removed.append(t)
                else:
                    await channel.set_permissions(t, connect=True, view_channel=True)
                    st["permitted"].append(t.id)
                    if t.id in st["rejected"]:
                        st["rejected"].remove(t.id)
                    added.append(t)
            except discord.HTTPException as e:
                problem = e.text or "Discord said no."
                break
        if added or removed:
            self._commit(room)
        fields = []
        if added:
            fields.append(("✅ Can always join now", ", ".join(m.mention for m in added), False))
        if removed:
            fields.append(("➖ Taken off the list", ", ".join(m.mention for m in removed), False))
        if skipped:
            fields.append(("⚠️ Skipped", "That's you or me, no need! " + ", ".join(m.mention for m in skipped), False))
        if problem:
            fields.append(("❌ Problem", problem, False))
        fields.append((f"🎟️ Guest list ({len(st['permitted'])})", _mentions(st["permitted"]), False))
        ok = bool(added or removed) and not problem
        return ("Guest List Updated!" if ok else "Web Snagged!"), \
               "Pick more people below, or dismiss this message when you're done. 🕸️", (RED if ok else DARK), fields

    async def reject(self, interaction, channel_id, targets):
        """Toggle people on the block list (and remove them from the room). Returns (title, text, color)."""
        room, channel = self._room(interaction, channel_id)
        if not room:
            return NOT_OWNER
        st = room["state"]
        blocked, unblocked, skipped, problem = [], [], [], None
        for t in targets:
            if t.id == room["owner"] or t.id == self.bot.user.id:
                skipped.append(t)
                continue
            try:
                if t.id in st["rejected"]:
                    await channel.set_permissions(t, overwrite=None)
                    st["rejected"].remove(t.id)
                    unblocked.append(t)
                else:
                    await channel.set_permissions(t, connect=False, view_channel=False)
                    st["rejected"].append(t.id)
                    if t.id in st["permitted"]:
                        st["permitted"].remove(t.id)
                    if t.voice and t.voice.channel and t.voice.channel.id == channel.id:
                        await t.move_to(None)
                    blocked.append(t)
            except discord.HTTPException as e:
                problem = e.text or "Discord said no."
                break
        if blocked or unblocked:
            self._commit(room)
        fields = []
        if blocked:
            fields.append(("🚫 Stuck outside the web", ", ".join(m.mention for m in blocked), False))
        if unblocked:
            fields.append(("➖ Unblocked", ", ".join(m.mention for m in unblocked), False))
        if skipped:
            fields.append(("⚠️ Skipped", "That's you or me, no need! " + ", ".join(m.mention for m in skipped), False))
        if problem:
            fields.append(("❌ Problem", problem, False))
        fields.append((f"⛔ Block list ({len(st['rejected'])})", _mentions(st["rejected"]), False))
        ok = bool(blocked or unblocked) and not problem
        return ("Block List Updated!" if ok else "Web Snagged!"), \
               "Pick more people below, or dismiss this message when you're done. 🕸️", (RED if ok else DARK), fields

    async def kick(self, interaction, channel_id, targets):
        room, channel = self._room(interaction, channel_id)
        if not room:
            return NOT_OWNER
        kicked, skipped, problem = [], [], None
        for t in targets:
            inside = bool(t.voice and t.voice.channel and t.voice.channel.id == channel.id)
            if t.id == room["owner"] or not inside:
                skipped.append(t)
                continue
            try:
                await t.move_to(None)
                kicked.append(t)
            except discord.HTTPException as e:
                problem = e.text or "Discord said no."
                break
        fields = []
        if kicked:
            fields.append(("🥾 Swung out of the web", ", ".join(m.mention for m in kicked), False))
        if skipped:
            fields.append(("⚠️ Skipped", "Not in your room, or it's you: " + ", ".join(m.mention for m in skipped), False))
        if problem:
            fields.append(("❌ Problem", problem, False))
        ok = bool(kicked) and not problem
        return ("Yeeted!" if ok else "Web Snagged!"), \
               ("They've been removed from the room. 🕸️" if ok else "Nobody was removed."), (RED if ok else DARK), fields

    async def _do_transfer(self, interaction, room, channel, new_owner: discord.Member):
        old_id = room["owner"]
        st = room["state"]
        for key in ("permitted", "rejected"):
            if new_owner.id in st[key]:
                st[key].remove(new_owner.id)
        if old_id not in st["permitted"] and old_id not in st["rejected"]:
            st["permitted"].append(old_id)  # old owner stays welcome
        await channel.set_permissions(new_owner, connect=True, view_channel=True)
        room["owner"] = new_owner.id
        self._commit(room)
        await channel.send(embed=web_embed(
            "New Web Master!", f"{new_owner.mention} now owns this web. 👑🕷️", BLUE))

    async def transfer(self, interaction, channel_id, targets):
        room, channel = self._room(interaction, channel_id)
        if not room:
            return NOT_OWNER
        target = targets[0]
        if target.id == room["owner"] or target.bot:
            return "Web Snagged!", "Pick a different (human) member.", DARK
        if target not in channel.members:
            return "Web Snagged!", "They need to be in the room first.", DARK
        try:
            await self._do_transfer(interaction, room, channel, target)
        except discord.HTTPException as e:
            return "Web Snagged!", e.text or "Discord said no.", DARK
        return "Ownership Transferred!", f"{target.mention} is the new owner. 👑", RED

    async def claim(self, interaction, channel_id):
        await interaction.response.defer(ephemeral=True)
        room = self.store.get_room(channel_id)
        channel = interaction.guild.get_channel(channel_id)
        if not room or channel is None:
            return await _reply(interaction, "Dead Web", "This room isn't active.", DARK)
        if interaction.user not in channel.members:
            return await _reply(interaction, "Web Snagged!", "Join the room first. 🕷️", DARK)
        owner = interaction.guild.get_member(room["owner"])
        if owner is not None and owner in channel.members:
            return await _reply(interaction, "Web Snagged!", "The owner is still here! 🕸️", DARK)
        try:
            await self._do_transfer(interaction, room, channel, interaction.user)
        except discord.HTTPException as e:
            return await _reply(interaction, "Web Snagged!", e.text, DARK)
        await _reply(interaction, "Web Claimed!", "You're the new owner now. 👑")

    async def set_bitrate(self, interaction, channel_id, value):
        room, channel = self._room(interaction, channel_id)
        if not room:
            return NOT_OWNER
        bps = int(value) * 1000
        err = await safe_edit(channel, bitrate=bps)
        if err:
            return "Web Snagged!", err, DARK
        room["state"]["bitrate"] = bps
        self._commit(room)
        return "Bitrate Updated!", f"Audio quality is now **{value} kbps** 🎚️", RED

    async def set_region(self, interaction, channel_id, value):
        room, channel = self._room(interaction, channel_id)
        if not room:
            return NOT_OWNER
        err = await safe_edit(channel, rtc_region=None if value == "auto" else value)
        if err:
            return "Web Snagged!", err, DARK
        room["state"]["region"] = "" if value == "auto" else value
        self._commit(room)
        return ("Region Updated!",
                f"Voice region is now **{REGION_NAMES.get(value, value)}** 🌍\n"
                "Members may briefly reconnect while it switches.", RED)

    async def invite_link(self, interaction, channel_id):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        try:
            inv = await channel.create_invite(max_age=3600, max_uses=0, unique=True,
                                              reason=f"Spidey VoiceMaster invite by {interaction.user}")
        except discord.HTTPException:
            return await _reply(interaction, "Web Snagged!",
                                "I couldn't create an invite. I need the **Create Invitation** permission.", DARK)
        note = ""
        if room["state"]["locked"] or room["state"]["hidden"]:
            note = "\n⚠️ The room is locked/hidden, so people still need to be **permitted** to get in."
        await _reply(interaction, "Invite Ready!", f"{inv.url}\nValid for **1 hour**. 🔗{note}", BLUE)

    async def clear_lists(self, interaction, channel_id):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        st = room["state"]
        ids = list(dict.fromkeys(st["permitted"] + st["rejected"]))
        for uid in ids:
            m = interaction.guild.get_member(uid)
            if m:
                try:
                    await channel.set_permissions(m, overwrite=None)
                except discord.HTTPException:
                    pass
        st["permitted"], st["rejected"] = [], []
        self._commit(room)
        await _reply(interaction, "Lists Cleared!",
                     f"Removed **{len(ids)}** member(s) from the permit and block lists. 🧹")

    async def reset_room(self, interaction, channel_id):
        room, channel = self._room(interaction, channel_id)
        if not room:
            return NOT_OWNER
        owner = interaction.user          # only the owner can get here (checked by _room)
        fresh = copy.deepcopy(DEFAULT_STATE)
        err = await safe_edit(
            channel,
            name=f"🕷️ {owner.display_name}'s Web"[:100],
            user_limit=0,
            overwrites=build_overwrites(interaction.guild, owner, fresh),
            bitrate=min(64000, int(interaction.guild.bitrate_limit)),
            rtc_region=None,
        )
        if err:
            return "Web Snagged!", err, DARK
        await safe_edit(channel, status=None)
        st = room["state"]
        st.clear()
        st.update(fresh)
        self._commit(room)
        return "Room Reset!", "Everything is back to default. ♻️", RED

    async def info(self, interaction, channel_id):
        room = self.store.get_room(channel_id)
        if not room:
            return await _reply(interaction, "Dead Web", "This room isn't active.", DARK)
        st = room["state"]
        channel = interaction.guild.get_channel(channel_id)
        embed = web_embed("Web Info", color=BLUE)
        embed.add_field(name="👑 Owner", value=f"<@{room['owner']}>")
        embed.add_field(name="🔐 Access", value="🔒 Locked" if st["locked"] else "🔓 Open")
        embed.add_field(name="👁️ Visibility", value="👻 Hidden" if st["hidden"] else "👁️ Visible")
        embed.add_field(name="👥 Limit", value=str(st["limit"] or "unlimited"))
        kbps = (st.get("bitrate") or 0) // 1000
        embed.add_field(name="🎚️ Bitrate", value=f"{kbps} kbps" if kbps else "default")
        embed.add_field(name="🌍 Region", value=REGION_NAMES.get(st.get("region") or "auto", st.get("region") or "Automatic"))
        embed.add_field(name="🎙️ Status", value=st["status"] or "none", inline=False)
        embed.add_field(name="✅ Permitted", value=_mentions(st["permitted"])[:1000], inline=False)
        embed.add_field(name="🚫 Blocked", value=_mentions(st["rejected"])[:1000], inline=False)
        if channel is not None:
            embed.set_footer(text=f"🕸️ Spidey • {channel.name} • {len(channel.members)} inside")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceMaster(bot))
