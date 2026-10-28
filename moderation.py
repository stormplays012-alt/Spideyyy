import copy
import json
import os
import re
import time
from datetime import timedelta
from types import SimpleNamespace
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from theme import web_embed, RED, BLUE, DARK

DATA_FILE = "moderation.json"
DEFAULT_REASON = "No reason provided"

DURATION_RE = re.compile(r"(\d+)\s*([smhdw])")
UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


# ------------------------------------------------------------------ helpers
def parse_duration(text: str) -> Optional[int]:
    """'10m' / '2h' / '1d12h' -> seconds. Returns None if invalid."""
    text = text.strip().lower()
    matches = DURATION_RE.findall(text)
    if not matches or DURATION_RE.sub("", text).strip():
        return None
    return sum(int(n) * UNITS[u] for n, u in matches)


def fmt_duration(seconds: int) -> str:
    parts = []
    for name, size in (("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        if seconds >= size:
            parts.append(f"{seconds // size}{name}")
            seconds %= size
    return " ".join(parts) or "0s"


async def fail(interaction: discord.Interaction, text: str):
    embed = web_embed("Web Snagged!", text, DARK)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


def hierarchy_error(interaction: discord.Interaction, target: discord.Member) -> Optional[str]:
    guild = interaction.guild
    if target.id == interaction.user.id:
        return "You can't do that to yourself. 🕷️"
    if target.id == guild.me.id:
        return "Nice try, but you can't use me on myself! 🕸️"
    if target.id == guild.owner_id:
        return "You can't touch the server owner."
    if interaction.user.id != guild.owner_id and target.top_role >= interaction.user.top_role:
        return "That member's top role is equal to or higher than yours."
    if target.top_role >= guild.me.top_role:
        return "That member's top role is higher than mine. Move my role up in Server Settings → Roles."
    return None


def role_error(interaction: discord.Interaction, role: discord.Role) -> Optional[str]:
    guild = interaction.guild
    if role.is_default() or role.managed:
        return "I can't assign that role (it's @everyone or managed by an integration)."
    if interaction.user.id != guild.owner_id and role >= interaction.user.top_role:
        return "That role is equal to or higher than your top role."
    if role >= guild.me.top_role:
        return "That role is higher than mine. Move my role up in Server Settings → Roles."
    return None


def mod_perms(**perms):
    """Server-only + Discord-side visibility + runtime permission check."""
    def deco(func):
        func = app_commands.guild_only()(func)
        func = app_commands.default_permissions(**perms)(func)
        func = app_commands.checks.has_permissions(**perms)(func)
        return func
    return deco


# ------------------------------------------------------------------ storage
class ModStore:
    def __init__(self, path: str = DATA_FILE):
        self.path = path
        self.data = {"guilds": {}, "tempbans": []}
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

    def guild(self, gid) -> dict:
        g = self.data["guilds"].setdefault(str(gid), {})
        g.setdefault("log_channel", None)
        g.setdefault("next_case", 1)
        g.setdefault("cases", [])
        g.setdefault("lockdown", [])
        return g


# ------------------------------------------------------------------ cog
class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = ModStore()
        self.tempban_loop.start()

    def cog_unload(self):
        self.tempban_loop.cancel()

    # ---------- case system
    async def record(self, guild: discord.Guild, type_: str, user_id: int, mod_id: int,
                     reason: str, active: bool = False, extra: str = "") -> dict:
        g = self.store.guild(guild.id)
        case = {
            "id": g["next_case"], "type": type_, "user": user_id, "mod": mod_id,
            "reason": reason, "time": int(time.time()), "active": active,
        }
        g["next_case"] += 1
        g["cases"].append(case)
        self.store.save()

        ch = guild.get_channel(g["log_channel"]) if g["log_channel"] else None
        if ch:
            desc = (f"**User:** <@{user_id}> (`{user_id}`)\n"
                    f"**Moderator:** <@{mod_id}>\n"
                    f"**Reason:** {reason}\n{extra}")
            try:
                await ch.send(embed=web_embed(f"Case #{case['id']} • {type_.upper()}", desc, DARK))
            except discord.HTTPException:
                pass
        return case

    async def dm(self, user, title: str, guild: discord.Guild, reason: str, extra: str = ""):
        try:
            await user.send(embed=web_embed(
                title, f"**Server:** {guild.name}\n**Reason:** {reason}\n{extra}", DARK))
        except (discord.Forbidden, discord.HTTPException):
            pass

    @staticmethod
    def result(title, target, reason, case, extra="", color=RED):
        desc = (f"**User:** {target.mention} (`{target.id}`)\n"
                f"**Reason:** {reason}\n{extra}**Case:** #{case['id']}")
        return web_embed(title, desc, color)

    def warn_count(self, guild_id, user_id) -> int:
        return sum(1 for c in self.store.guild(guild_id)["cases"]
                   if c["type"] == "warn" and c["user"] == user_id and c["active"])

    # ---------- tempban expiry
    @tasks.loop(seconds=30)
    async def tempban_loop(self):
        now = time.time()
        for tb in list(self.store.data["tempbans"]):
            if tb["until"] > now:
                continue
            guild = self.bot.get_guild(tb["guild"])
            if guild:
                try:
                    await guild.unban(discord.Object(id=tb["user"]), reason="Tempban expired")
                    await self.record(guild, "unban", tb["user"], self.bot.user.id, "Tempban expired")
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    pass
            self.store.data["tempbans"].remove(tb)
            self.store.save()

    @tempban_loop.before_loop
    async def _before_tempban(self):
        await self.bot.wait_until_ready()

    # ================================================================ KICK / BAN
    @app_commands.command(name="kick", description="Kick a member from the server")
    @app_commands.describe(member="Who to kick", reason="Why?")
    @mod_perms(kick_members=True)
    async def kick(self, interaction: discord.Interaction, member: discord.Member,
                   reason: str = DEFAULT_REASON):
        if err := hierarchy_error(interaction, member):
            return await fail(interaction, err)
        await self.dm(member, "You were kicked 🥾", interaction.guild, reason)
        try:
            await member.kick(reason=f"{interaction.user}: {reason}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        case = await self.record(interaction.guild, "kick", member.id, interaction.user.id, reason)
        await interaction.response.send_message(embed=self.result("Yeeted from the Web!", member, reason, case))

    @app_commands.command(name="ban", description="Ban a member (or a user ID) from the server")
    @app_commands.describe(user="Who to ban", reason="Why?", delete_days="Delete their messages from the last X days (0-7)")
    @mod_perms(ban_members=True)
    async def ban(self, interaction: discord.Interaction, user: discord.User,
                  reason: str = DEFAULT_REASON, delete_days: app_commands.Range[int, 0, 7] = 0):
        member = interaction.guild.get_member(user.id)
        if member:
            if err := hierarchy_error(interaction, member):
                return await fail(interaction, err)
        elif user.id in (interaction.user.id, interaction.guild.me.id):
            return await fail(interaction, "Nope, can't do that. 🕷️")
        await self.dm(user, "You were banned 🔨", interaction.guild, reason)
        try:
            await interaction.guild.ban(user, reason=f"{interaction.user}: {reason}",
                                        delete_message_seconds=delete_days * 86400)
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        case = await self.record(interaction.guild, "ban", user.id, interaction.user.id, reason)
        await interaction.response.send_message(embed=self.result("Trapped in the Web Forever!", user, reason, case))

    @app_commands.command(name="tempban", description="Ban someone for a set time, then auto-unban")
    @app_commands.describe(user="Who to ban", duration="e.g. 30m, 12h, 7d, 1d12h", reason="Why?")
    @mod_perms(ban_members=True)
    async def tempban(self, interaction: discord.Interaction, user: discord.User,
                      duration: str, reason: str = DEFAULT_REASON):
        secs = parse_duration(duration)
        if not secs:
            return await fail(interaction, "Invalid duration. Try `30m`, `12h`, `7d` or `1d12h`.")
        member = interaction.guild.get_member(user.id)
        if member and (err := hierarchy_error(interaction, member)):
            return await fail(interaction, err)
        if user.id in (interaction.user.id, interaction.guild.me.id):
            return await fail(interaction, "Nope, can't do that. 🕷️")
        await self.dm(user, "You were temporarily banned ⏳", interaction.guild, reason,
                      f"**Duration:** {fmt_duration(secs)}")
        try:
            await interaction.guild.ban(user, reason=f"Tempban {fmt_duration(secs)} by {interaction.user}: {reason}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        self.store.data["tempbans"].append(
            {"guild": interaction.guild.id, "user": user.id, "until": time.time() + secs})
        self.store.save()
        case = await self.record(interaction.guild, "tempban", user.id, interaction.user.id, reason,
                                 extra=f"**Duration:** {fmt_duration(secs)}\n")
        await interaction.response.send_message(embed=self.result(
            "Webbed Up Temporarily!", user, reason, case, f"**Duration:** {fmt_duration(secs)}\n"))

    @app_commands.command(name="softban", description="Ban + instantly unban to wipe their recent messages")
    @app_commands.describe(member="Who to softban", reason="Why?")
    @mod_perms(ban_members=True)
    async def softban(self, interaction: discord.Interaction, member: discord.Member,
                      reason: str = DEFAULT_REASON):
        if err := hierarchy_error(interaction, member):
            return await fail(interaction, err)
        await self.dm(member, "You were softbanned (you can rejoin) 🧹", interaction.guild, reason)
        try:
            await interaction.guild.ban(member, reason=f"Softban by {interaction.user}: {reason}",
                                        delete_message_seconds=86400)
            await interaction.guild.unban(member, reason="Softban")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        case = await self.record(interaction.guild, "softban", member.id, interaction.user.id, reason)
        await interaction.response.send_message(embed=self.result("Web Cleaned!", member, reason, case))

    @app_commands.command(name="unban", description="Unban a user")
    @app_commands.describe(user="Pick from the ban list", reason="Why?")
    @mod_perms(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user: str, reason: str = DEFAULT_REASON):
        if not user.isdigit():
            return await fail(interaction, "Pick someone from the autocomplete list (or paste their user ID).")
        try:
            await interaction.guild.unban(discord.Object(id=int(user)), reason=f"{interaction.user}: {reason}")
        except discord.NotFound:
            return await fail(interaction, "That user isn't banned.")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        self.store.data["tempbans"] = [t for t in self.store.data["tempbans"]
                                       if not (t["guild"] == interaction.guild.id and t["user"] == int(user))]
        self.store.save()
        case = await self.record(interaction.guild, "unban", int(user), interaction.user.id, reason)
        target = SimpleNamespace(id=int(user), mention=f"<@{user}>")
        await interaction.response.send_message(embed=self.result("Freed from the Web!", target, reason, case, color=BLUE))

    @unban.autocomplete("user")
    async def unban_autocomplete(self, interaction: discord.Interaction, current: str):
        choices = []
        try:
            async for entry in interaction.guild.bans(limit=500):
                label = f"{entry.user} ({entry.user.id})"
                if current.lower() in label.lower():
                    choices.append(app_commands.Choice(name=label[:100], value=str(entry.user.id)))
                if len(choices) >= 25:
                    break
        except discord.HTTPException:
            pass
        return choices

    @app_commands.command(name="banlist", description="Show the first 20 banned users")
    @mod_perms(ban_members=True)
    async def banlist(self, interaction: discord.Interaction):
        lines = []
        async for entry in interaction.guild.bans(limit=20):
            lines.append(f"🕸️ **{entry.user}** (`{entry.user.id}`) — {entry.reason or 'no reason'}")
        await interaction.response.send_message(
            embed=web_embed("Banned Flies", "\n".join(lines) or "Nobody is banned. 🕷️", DARK), ephemeral=True)

    # ================================================================ MUTE / TIMEOUT
    @app_commands.command(name="mute", description="Timeout (mute) a member")
    @app_commands.describe(member="Who to mute", duration="e.g. 10m, 2h, 1d (max 28d)", reason="Why?")
    @mod_perms(moderate_members=True)
    async def mute(self, interaction: discord.Interaction, member: discord.Member,
                   duration: str = "10m", reason: str = DEFAULT_REASON):
        if err := hierarchy_error(interaction, member):
            return await fail(interaction, err)
        secs = parse_duration(duration)
        if not secs or secs > 28 * 86400:
            return await fail(interaction, "Invalid duration. Use something like `10m`, `2h`, `1d` (max 28d).")
        try:
            await member.timeout(timedelta(seconds=secs), reason=f"{interaction.user}: {reason}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await self.dm(member, "You were muted 🤐", interaction.guild, reason, f"**Duration:** {fmt_duration(secs)}")
        case = await self.record(interaction.guild, "mute", member.id, interaction.user.id, reason,
                                 extra=f"**Duration:** {fmt_duration(secs)}\n")
        await interaction.response.send_message(embed=self.result(
            "Silk Over the Mouth!", member, reason, case, f"**Duration:** {fmt_duration(secs)}\n"))

    @app_commands.command(name="unmute", description="Remove a member's timeout")
    @app_commands.describe(member="Who to unmute", reason="Why?")
    @mod_perms(moderate_members=True)
    async def unmute(self, interaction: discord.Interaction, member: discord.Member,
                     reason: str = DEFAULT_REASON):
        if not member.is_timed_out():
            return await fail(interaction, "That member isn't muted.")
        try:
            await member.timeout(None, reason=f"{interaction.user}: {reason}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        case = await self.record(interaction.guild, "unmute", member.id, interaction.user.id, reason)
        await interaction.response.send_message(embed=self.result("Web Removed!", member, reason, case, color=BLUE))

    # ================================================================ WARNINGS
    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.describe(member="Who to warn", reason="Why?")
    @mod_perms(moderate_members=True)
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str):
        if err := hierarchy_error(interaction, member):
            return await fail(interaction, err)
        case = await self.record(interaction.guild, "warn", member.id, interaction.user.id, reason, active=True)
        total = self.warn_count(interaction.guild.id, member.id)
        await self.dm(member, "You received a warning ⚠️", interaction.guild, reason,
                      f"**Total warnings:** {total}")
        await interaction.response.send_message(embed=self.result(
            "Warning Spun!", member, reason, case, f"**Total warnings:** {total}\n"))

    @app_commands.command(name="warnings", description="See a member's active warnings")
    @app_commands.describe(member="Whose warnings?")
    @mod_perms(moderate_members=True)
    async def warnings(self, interaction: discord.Interaction, member: discord.Member):
        warns = [c for c in self.store.guild(interaction.guild.id)["cases"]
                 if c["type"] == "warn" and c["user"] == member.id and c["active"]]
        lines = [f"**#{c['id']}** • {c['reason']} — <@{c['mod']}> <t:{c['time']}:R>" for c in warns[-15:]]
        await interaction.response.send_message(
            embed=web_embed(f"{member.display_name}'s Warnings",
                            "\n".join(lines) or "No active warnings. 🕸️", DARK), ephemeral=True)

    @app_commands.command(name="delwarn", description="Remove one warning by case number")
    @app_commands.describe(case_id="The case number of the warning")
    @mod_perms(moderate_members=True)
    async def delwarn(self, interaction: discord.Interaction, case_id: int):
        for c in self.store.guild(interaction.guild.id)["cases"]:
            if c["id"] == case_id and c["type"] == "warn" and c["active"]:
                c["active"] = False
                self.store.save()
                await self.record(interaction.guild, "delwarn", c["user"], interaction.user.id,
                                  f"Removed warning #{case_id}")
                return await interaction.response.send_message(embed=web_embed(
                    "Warning Removed!", f"Warning **#{case_id}** for <@{c['user']}> was cleared. 🕸️", BLUE))
        await fail(interaction, "No active warning with that case number.")

    @app_commands.command(name="clearwarnings", description="Clear all of a member's warnings")
    @app_commands.describe(member="Whose warnings to clear")
    @mod_perms(moderate_members=True)
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member):
        count = 0
        for c in self.store.guild(interaction.guild.id)["cases"]:
            if c["type"] == "warn" and c["user"] == member.id and c["active"]:
                c["active"] = False
                count += 1
        self.store.save()
        await self.record(interaction.guild, "clearwarns", member.id, interaction.user.id,
                          f"Cleared {count} warning(s)")
        await interaction.response.send_message(embed=web_embed(
            "Slate Wiped Clean!", f"Cleared **{count}** warning(s) for {member.mention}. 🕸️", BLUE))

    # ================================================================ CASES
    @app_commands.command(name="modlogs", description="Show a user's moderation history")
    @app_commands.describe(user="Whose history?")
    @mod_perms(moderate_members=True)
    async def modlogs(self, interaction: discord.Interaction, user: discord.User):
        cases = [c for c in self.store.guild(interaction.guild.id)["cases"] if c["user"] == user.id]
        lines = [f"**#{c['id']}** `{c['type'].upper()}` • {c['reason']} <t:{c['time']}:R>" for c in cases[-10:]]
        await interaction.response.send_message(
            embed=web_embed(f"Case File: {user}", "\n".join(lines) or "Clean record. 🕸️", DARK), ephemeral=True)

    @app_commands.command(name="case", description="Look up a case by number")
    @app_commands.describe(case_id="Case number")
    @mod_perms(moderate_members=True)
    async def case(self, interaction: discord.Interaction, case_id: int):
        for c in self.store.guild(interaction.guild.id)["cases"]:
            if c["id"] == case_id:
                desc = (f"**Type:** {c['type'].upper()}\n**User:** <@{c['user']}> (`{c['user']}`)\n"
                        f"**Moderator:** <@{c['mod']}>\n**Reason:** {c['reason']}\n**When:** <t:{c['time']}:F>")
                return await interaction.response.send_message(
                    embed=web_embed(f"Case #{case_id}", desc, DARK), ephemeral=True)
        await fail(interaction, "No case with that number.")

    @app_commands.command(name="setlogchannel", description="Set the channel where mod actions are logged")
    @app_commands.describe(channel="Log channel (leave empty to turn logging off)")
    @mod_perms(manage_guild=True)
    async def setlogchannel(self, interaction: discord.Interaction,
                            channel: Optional[discord.TextChannel] = None):
        g = self.store.guild(interaction.guild.id)
        g["log_channel"] = channel.id if channel else None
        self.store.save()
        text = f"Mod actions will be logged in {channel.mention} 🕸️" if channel else "Mod logging is off."
        await interaction.response.send_message(embed=web_embed("Log Web Set!", text, BLUE))

    # ================================================================ CHANNEL TOOLS
    @app_commands.command(name="purge", description="Bulk delete recent messages")
    @app_commands.describe(amount="How many recent messages to scan (1-100)",
                           member="Only delete this member's messages",
                           contains="Only delete messages containing this text")
    @mod_perms(manage_messages=True)
    async def purge(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100],
                    member: Optional[discord.Member] = None, contains: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)

        def check(m: discord.Message) -> bool:
            if m.pinned:
                return False
            if member and m.author.id != member.id:
                return False
            if contains and contains.lower() not in m.content.lower():
                return False
            return True

        try:
            deleted = await interaction.channel.purge(limit=amount, check=check)
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await self.record(interaction.guild, "purge", member.id if member else interaction.user.id,
                          interaction.user.id, f"Purged {len(deleted)} message(s) in #{interaction.channel}")
        await interaction.followup.send(embed=web_embed(
            "Web Swept!", f"Cleaned up **{len(deleted)}** message(s). 🧹🕸️\n"
                          "(Messages older than 14 days can't be bulk deleted.)"), ephemeral=True)

    @app_commands.command(name="slowmode", description="Set a channel's slowmode")
    @app_commands.describe(seconds="0 to turn off, max 21600", channel="Channel (defaults to this one)")
    @mod_perms(manage_channels=True)
    async def slowmode(self, interaction: discord.Interaction, seconds: app_commands.Range[int, 0, 21600],
                       channel: Optional[discord.TextChannel] = None):
        channel = channel or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await fail(interaction, "Pick a text channel.")
        try:
            await channel.edit(slowmode_delay=seconds, reason=f"Slowmode by {interaction.user}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        text = f"Slowmode in {channel.mention} is now **{seconds}s** 🐌" if seconds else \
               f"Slowmode in {channel.mention} is **off**."
        await interaction.response.send_message(embed=web_embed("Web Slowed!", text, BLUE))

    @app_commands.command(name="lock", description="Stop everyone from chatting in a channel")
    @app_commands.describe(channel="Channel (defaults to this one)", reason="Why?")
    @mod_perms(manage_channels=True)
    async def lock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None,
                   reason: str = DEFAULT_REASON):
        channel = channel or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await fail(interaction, "Pick a text channel.")
        everyone = interaction.guild.default_role
        ow = channel.overwrites_for(everyone)
        ow.send_messages = False
        try:
            await channel.set_permissions(everyone, overwrite=ow, reason=f"Lock by {interaction.user}: {reason}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message(embed=web_embed(
            "Web Sealed!", f"{channel.mention} is locked. 🔒\n**Reason:** {reason}"))

    @app_commands.command(name="unlock", description="Let everyone chat in a channel again")
    @app_commands.describe(channel="Channel (defaults to this one)")
    @mod_perms(manage_channels=True)
    async def unlock(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        channel = channel or interaction.channel
        if not isinstance(channel, discord.TextChannel):
            return await fail(interaction, "Pick a text channel.")
        everyone = interaction.guild.default_role
        ow = channel.overwrites_for(everyone)
        ow.send_messages = None
        try:
            await channel.set_permissions(everyone, overwrite=ow, reason=f"Unlock by {interaction.user}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message(embed=web_embed(
            "Web Reopened!", f"{channel.mention} is unlocked. 🔓", BLUE))

    @app_commands.command(name="lockdown", description="Lock EVERY text channel (emergency raid protection)")
    @app_commands.describe(reason="Why?")
    @mod_perms(administrator=True)
    async def lockdown(self, interaction: discord.Interaction, reason: str = DEFAULT_REASON):
        await interaction.response.defer()
        g = self.store.guild(interaction.guild.id)
        everyone = interaction.guild.default_role
        count = 0
        for ch in interaction.guild.text_channels:
            ow = ch.overwrites_for(everyone)
            if ow.send_messages is False:
                continue  # already locked, leave it alone when unlocking later
            ow.send_messages = False
            try:
                await ch.set_permissions(everyone, overwrite=ow, reason=f"Lockdown by {interaction.user}: {reason}")
                g["lockdown"].append(ch.id)
                count += 1
            except discord.HTTPException:
                pass
        self.store.save()
        await self.record(interaction.guild, "lockdown", interaction.user.id, interaction.user.id, reason)
        await interaction.followup.send(embed=web_embed(
            "WEB LOCKDOWN!", f"Locked **{count}** channel(s). 🚨🕸️\n**Reason:** {reason}"))

    @app_commands.command(name="unlockdown", description="Undo /lockdown")
    @mod_perms(administrator=True)
    async def unlockdown(self, interaction: discord.Interaction):
        await interaction.response.defer()
        g = self.store.guild(interaction.guild.id)
        everyone = interaction.guild.default_role
        count = 0
        for cid in list(g["lockdown"]):
            ch = interaction.guild.get_channel(cid)
            if ch:
                ow = ch.overwrites_for(everyone)
                ow.send_messages = None
                try:
                    await ch.set_permissions(everyone, overwrite=ow, reason=f"Lockdown lifted by {interaction.user}")
                    count += 1
                except discord.HTTPException:
                    pass
        g["lockdown"] = []
        self.store.save()
        await interaction.followup.send(embed=web_embed(
            "Lockdown Lifted!", f"Unlocked **{count}** channel(s). 🕸️", BLUE))

    # ================================================================ MEMBER TOOLS
    @app_commands.command(name="nick", description="Change or reset a member's nickname")
    @app_commands.describe(member="Whose nickname?", nickname="New nickname (empty = reset)")
    @mod_perms(manage_nicknames=True)
    async def nick(self, interaction: discord.Interaction, member: discord.Member,
                   nickname: Optional[str] = None):
        if err := hierarchy_error(interaction, member):
            return await fail(interaction, err)
        try:
            await member.edit(nick=nickname, reason=f"Nick change by {interaction.user}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        text = f"{member.mention} is now **{nickname}** 🕷️" if nickname else f"{member.mention}'s nickname was reset."
        await interaction.response.send_message(embed=web_embed("Name Respun!", text, BLUE))

    @app_commands.command(name="addrole", description="Give a role to a member")
    @app_commands.describe(member="Who?", role="Which role?")
    @mod_perms(manage_roles=True)
    async def addrole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if err := role_error(interaction, role):
            return await fail(interaction, err)
        try:
            await member.add_roles(role, reason=f"Role added by {interaction.user}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message(embed=web_embed(
            "Role Spun!", f"Gave {role.mention} to {member.mention} 🕸️", BLUE))

    @app_commands.command(name="removerole", description="Remove a role from a member")
    @app_commands.describe(member="Who?", role="Which role?")
    @mod_perms(manage_roles=True)
    async def removerole(self, interaction: discord.Interaction, member: discord.Member, role: discord.Role):
        if err := role_error(interaction, role):
            return await fail(interaction, err)
        try:
            await member.remove_roles(role, reason=f"Role removed by {interaction.user}")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message(embed=web_embed(
            "Role Snipped!", f"Removed {role.mention} from {member.mention} 🕸️", BLUE))

    @app_commands.command(name="userinfo", description="Show info about a member")
    @app_commands.describe(member="Who? (defaults to you)")
    @mod_perms(moderate_members=True)
    async def userinfo(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        roles = ", ".join(r.mention for r in member.roles[1:][-10:]) or "none"
        muted = (f"until {discord.utils.format_dt(member.timed_out_until, 'R')}"
                 if member.is_timed_out() else "no")
        embed = web_embed(f"{member.display_name}", color=BLUE)
        embed.add_field(name="ID", value=f"`{member.id}`")
        embed.add_field(name="Created", value=discord.utils.format_dt(member.created_at, "R"))
        embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "?")
        embed.add_field(name="Top role", value=member.top_role.mention)
        embed.add_field(name="Warnings", value=str(self.warn_count(interaction.guild.id, member.id)))
        embed.add_field(name="Muted", value=muted)
        embed.add_field(name="Roles", value=roles, inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ================================================================ VOICE MODERATION
    async def _voice_action(self, interaction, member: discord.Member, title: str, text: str, **edit):
        if err := hierarchy_error(interaction, member):
            return await fail(interaction, err)
        if not member.voice or not member.voice.channel:
            return await fail(interaction, "That member isn't in a voice channel.")
        try:
            await member.edit(reason=f"Voice action by {interaction.user}", **edit)
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message(embed=web_embed(title, text, BLUE))

    @app_commands.command(name="vcmute", description="Server-mute a member in voice")
    @mod_perms(mute_members=True)
    async def vcmute(self, interaction: discord.Interaction, member: discord.Member):
        await self._voice_action(interaction, member, "Voice Silenced!", f"{member.mention} is voice-muted 🔇", mute=True)

    @app_commands.command(name="vcunmute", description="Remove a member's server voice mute")
    @mod_perms(mute_members=True)
    async def vcunmute(self, interaction: discord.Interaction, member: discord.Member):
        await self._voice_action(interaction, member, "Voice Restored!", f"{member.mention} is voice-unmuted 🔊", mute=False)

    @app_commands.command(name="vcdeafen", description="Server-deafen a member in voice")
    @mod_perms(deafen_members=True)
    async def vcdeafen(self, interaction: discord.Interaction, member: discord.Member):
        await self._voice_action(interaction, member, "Ears Covered!", f"{member.mention} is deafened 🙉", deafen=True)

    @app_commands.command(name="vcundeafen", description="Remove a member's server deafen")
    @mod_perms(deafen_members=True)
    async def vcundeafen(self, interaction: discord.Interaction, member: discord.Member):
        await self._voice_action(interaction, member, "Ears Uncovered!", f"{member.mention} is undeafened 👂", deafen=False)

    @app_commands.command(name="vckick", description="Disconnect a member from voice")
    @mod_perms(move_members=True)
    async def vckick(self, interaction: discord.Interaction, member: discord.Member):
        await self._voice_action(interaction, member, "Swung Out of Voice!",
                                 f"{member.mention} was disconnected 🥾", voice_channel=None)

    @app_commands.command(name="vcmove", description="Move a member to another voice channel")
    @app_commands.describe(member="Who?", channel="Where to?")
    @mod_perms(move_members=True)
    async def vcmove(self, interaction: discord.Interaction, member: discord.Member,
                     channel: discord.VoiceChannel):
        await self._voice_action(interaction, member, "Web Swing!",
                                 f"Moved {member.mention} to {channel.mention} 🕷️", voice_channel=channel)


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
