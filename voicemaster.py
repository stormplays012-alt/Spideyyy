import asyncio
import copy
import json
import os

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK
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

    e = web_embed(
        "Web Control Panel",
        "Use the buttons below to run your web. 🕷️\n"
        "Only the **owner** can change settings • anyone in the room can press **Info**.",
        color,
    )
    e.set_author(name=f"{owner.display_name}'s Web", icon_url=owner.display_avatar.url)
    e.set_thumbnail(url=owner.display_avatar.url)
    e.add_field(name="👑 Owner", value=f"{owner.mention}{'' if present else ' • *away*'}", inline=True)
    e.add_field(name="🔐 Access", value="🔒 Locked" if locked else "🔓 Open", inline=True)
    e.add_field(name="👁️ Visibility", value="👻 Hidden" if hidden else "👁️ Visible", inline=True)
    e.add_field(name="👥 Capacity", value=f"{count}/{limit}" if limit else f"{count} • unlimited", inline=True)
    e.add_field(name="🎙️ Status", value=(st["status"] or "—")[:100], inline=True)
    e.add_field(name="🎟️ Guest list",
                value=f"✅ {len(st['permitted'])} permitted • 🚫 {len(st['rejected'])} blocked", inline=True)
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


# ------------------------------------------------------------------ user picker
class PickView(discord.ui.View):
    """Ephemeral dropdown to choose a user for permit/reject/kick/transfer."""

    def __init__(self, handler, channel_id, owner_id, placeholder):
        super().__init__(timeout=60)
        self.handler, self.channel_id, self.owner_id = handler, channel_id, owner_id
        self.select = discord.ui.UserSelect(placeholder=placeholder, min_values=1, max_values=1)
        self.select.callback = self.picked
        self.add_item(self.select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id

    async def picked(self, interaction: discord.Interaction):
        member = interaction.guild.get_member(self.select.values[0].id)
        if member is None:
            return await _reply(interaction, "Web Snagged!", "I can't find that member.", DARK)
        self.stop()
        await self.handler(interaction, self.channel_id, member)


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

    handlers = {
        "permit": (cog.permit, "Who can join?"),
        "reject": (cog.reject, "Who should be blocked?"),
        "kick": (cog.kick, "Who should be removed?"),
        "transfer": (cog.transfer, "Choose the new owner."),
    }
    if action in handlers:
        handler, placeholder = handlers[action]
        return await interaction.response.send_message(
            embed=web_embed("Pick a Member", "Choose someone from the dropdown below. 🕸️", DARK),
            view=PickView(handler, channel_id, interaction.user.id, placeholder),
            ephemeral=True,
        )


class _PanelBase(discord.ui.View):
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        cog = interaction.client.get_cog("VoiceMaster")
        channel = interaction.channel
        room = cog.store.get_room(channel.id) if cog and channel else None
        if room is None:
            await _reply(interaction, "Dead Web", "This isn't an active VoiceMaster room.", DARK)
            return False
        return True


B = discord.ButtonStyle


class ControlPanel(_PanelBase):
    """Button panel. Pass the room state to get the live look (Lock/Unlock, Hide/Show, Claim enabled)."""

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
    def schedule_refresh(self, cid):
        cid = int(cid)
        task = self._refresh_tasks.get(cid)
        if task and not task.done():
            return                      # one already queued: it will read the latest state
        self._refresh_tasks[cid] = asyncio.create_task(self._refresh_later(cid))

    async def _refresh_later(self, cid: int):
        try:
            await asyncio.sleep(2)      # batch rapid changes / let Discord's cache catch up
            self._refresh_tasks.pop(cid, None)
            await self.refresh_panel(cid)
        except Exception:
            self._refresh_tasks.pop(cid, None)

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

    async def permit(self, interaction, channel_id, target: discord.Member):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        if target.id == room["owner"] or target.id == self.bot.user.id:
            return await _reply(interaction, "Web Snagged!", "Pick someone else. 🕷️", DARK)
        st = room["state"]
        try:
            if target.id in st["permitted"]:
                await channel.set_permissions(target, overwrite=None)
                st["permitted"].remove(target.id)
                msg = f"{target.mention} is off the guest list."
            else:
                await channel.set_permissions(target, connect=True, view_channel=True)
                st["permitted"].append(target.id)
                if target.id in st["rejected"]:
                    st["rejected"].remove(target.id)
                msg = f"{target.mention} can always swing into your web. ✅"
        except discord.HTTPException as e:
            return await _reply(interaction, "Web Snagged!", e.text, DARK)
        self._commit(room)
        await _reply(interaction, "Guest List Updated!", msg)

    async def reject(self, interaction, channel_id, target: discord.Member):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        if target.id == room["owner"] or target.id == self.bot.user.id:
            return await _reply(interaction, "Web Snagged!", "Pick someone else. 🕷️", DARK)
        st = room["state"]
        try:
            if target.id in st["rejected"]:
                await channel.set_permissions(target, overwrite=None)
                st["rejected"].remove(target.id)
                msg = f"{target.mention} is unblocked."
            else:
                await channel.set_permissions(target, connect=False, view_channel=False)
                st["rejected"].append(target.id)
                if target.id in st["permitted"]:
                    st["permitted"].remove(target.id)
                if target.voice and target.voice.channel and target.voice.channel.id == channel.id:
                    await target.move_to(None)
                msg = f"{target.mention} is stuck outside the web. 🚫"
        except discord.HTTPException as e:
            return await _reply(interaction, "Web Snagged!", e.text, DARK)
        self._commit(room)
        await _reply(interaction, "Block List Updated!", msg)

    async def kick(self, interaction, channel_id, target: discord.Member):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        if target.id == room["owner"]:
            return await _reply(interaction, "Web Snagged!", "You can't kick yourself. 🕷️", DARK)
        if not (target.voice and target.voice.channel and target.voice.channel.id == channel.id):
            return await _reply(interaction, "Web Snagged!", "That member isn't in your room.", DARK)
        try:
            await target.move_to(None)
        except discord.HTTPException as e:
            return await _reply(interaction, "Web Snagged!", e.text, DARK)
        await _reply(interaction, "Yeeted!", f"{target.mention} was swung out of the web. 🥾")

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

    async def transfer(self, interaction, channel_id, target: discord.Member):
        await interaction.response.defer(ephemeral=True)
        room, channel = self._room(interaction, channel_id)
        if not room:
            return await self._not_owner(interaction)
        if target.id == room["owner"] or target.bot:
            return await _reply(interaction, "Web Snagged!", "Pick a different (human) member.", DARK)
        if target not in channel.members:
            return await _reply(interaction, "Web Snagged!", "They need to be in the room first.", DARK)
        try:
            await self._do_transfer(interaction, room, channel, target)
        except discord.HTTPException as e:
            return await _reply(interaction, "Web Snagged!", e.text, DARK)
        await _reply(interaction, "Ownership Transferred!", f"{target.mention} is the new owner. 👑")

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

    async def info(self, interaction, channel_id):
        room = self.store.get_room(channel_id)
        if not room:
            return await _reply(interaction, "Dead Web", "This room isn't active.", DARK)
        st = room["state"]
        fmt = lambda ids: ", ".join(f"<@{i}>" for i in ids) or "nobody"
        desc = (
            f"👑 **Owner:** <@{room['owner']}>\n"
            f"👥 **Limit:** {st['limit'] or 'unlimited'}\n"
            f"🔒 **Locked:** {'yes' if st['locked'] else 'no'}\n"
            f"👻 **Hidden:** {'yes' if st['hidden'] else 'no'}\n"
            f"📝 **Status:** {st['status'] or 'none'}\n"
            f"✅ **Permitted:** {fmt(st['permitted'])}\n"
            f"🚫 **Rejected:** {fmt(st['rejected'])}"
        )
        await _reply(interaction, "Web Info", desc, BLUE)


async def setup(bot: commands.Bot):
    await bot.add_cog(VoiceMaster(bot))
