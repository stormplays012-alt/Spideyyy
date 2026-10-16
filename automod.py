import copy
import json
import os
import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK
from moderation import parse_duration, fmt_duration, fail

DATA_FILE = "automod.json"

# ------------------------------------------------------------------ config
MODULES = {
    "spam":       "Spam (too many messages too fast)",
    "duplicates": "Duplicate messages (repeating yourself)",
    "links":      "Links (with an allowlist)",
    "invites":    "Discord invites",
    "badwords":   "Bad words (your own list)",
    "caps":       "Excessive CAPS",
    "mentions":   "Mass mentions",
    "emojis":     "Emoji spam",
    "zalgo":      "Zalgo / glitchy text",
}

DEFAULTS = {
    "modules": {name: False for name in MODULES},
    "spam": {"messages": 5, "seconds": 5},
    "duplicates": {"count": 3, "seconds": 30},
    "caps": {"percent": 70, "min_length": 10},
    "mentions": {"limit": 5},
    "emojis": {"limit": 10},
    "badwords": [],
    "allowed_domains": [],
    "ignored_channels": [],
    "ignored_roles": [],
    "ignore_staff": True,
    "punishment": {"action": "timeout", "strikes": 3, "window": 600, "duration": 600},
}

# /automod set <setting> <value>
SETTINGS = {
    "spam_messages":     ("spam", "messages", 2, 20, "Messages allowed in the spam window"),
    "spam_seconds":      ("spam", "seconds", 1, 60, "Spam window length (seconds)"),
    "duplicate_count":   ("duplicates", "count", 2, 10, "Same message this many times = violation"),
    "duplicate_seconds": ("duplicates", "seconds", 5, 300, "Duplicate window length (seconds)"),
    "caps_percent":      ("caps", "percent", 30, 100, "% of letters in CAPS that triggers it"),
    "caps_min_length":   ("caps", "min_length", 5, 100, "Ignore messages with fewer letters than this"),
    "mention_limit":     ("mentions", "limit", 2, 30, "Max mentions (users + roles) per message"),
    "emoji_limit":       ("emojis", "limit", 3, 50, "Max emojis per message"),
}

URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>]+"
    r"|\b[a-z0-9-]+\.(?:com|net|org|io|gg|co|xyz|me|tv|ly|link|info|ru|app|dev|ml|tk)\b(?:/[^\s<>]*)?",
    re.I)
INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[\w-]+", re.I)
CUSTOM_EMOJI_RE = re.compile(r"<a?:\w+:\d+>")
UNICODE_EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF]")


def fill_defaults(target: dict, defaults: dict):
    for k, v in defaults.items():
        if k not in target:
            target[k] = copy.deepcopy(v)
        elif isinstance(v, dict) and isinstance(target[k], dict):
            fill_defaults(target[k], v)


def domain_of(url: str) -> str:
    url = re.sub(r"^https?://", "", url, flags=re.I)
    host = re.split(r"[/?#:]", url)[0].lower()
    return host[4:] if host.startswith("www.") else host


def compile_badword(word: str) -> re.Pattern:
    if "*" in word:
        pattern = re.escape(word).replace(r"\*", r"\w*")
    else:
        pattern = rf"\b{re.escape(word)}\b"
    return re.compile(pattern, re.I)


# ------------------------------------------------------------------ cog
class AutoMod(commands.Cog):
    automod = app_commands.Group(
        name="automod", description="Configure Spidey's AutoMod", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = {"guilds": {}}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass
        # in-memory trackers (reset on restart)
        self.history = defaultdict(lambda: deque(maxlen=30))   # (gid, uid) -> (t, message)
        self.dups = defaultdict(lambda: deque(maxlen=15))      # (gid, uid) -> (t, text, message)
        self.strikes = defaultdict(deque)                      # (gid, uid) -> timestamps
        self.last_notice = {}

    def save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, DATA_FILE)

    def cfg(self, gid: int) -> dict:
        g = self.data["guilds"].setdefault(str(gid), {})
        fill_defaults(g, DEFAULTS)
        return g

    # ================================================================ DETECTION
    def exempt(self, message: discord.Message, c: dict) -> bool:
        member = message.author
        if message.channel.id in c["ignored_channels"]:
            return True
        if any(r.id in c["ignored_roles"] for r in member.roles):
            return True
        perms = member.guild_permissions
        if c["ignore_staff"] and (perms.administrator or perms.manage_messages):
            return True
        return False

    def detect(self, message: discord.Message, c: dict, edited: bool = False):
        """Return (module, reason, [extra messages to delete]) or None."""
        mods, text = c["modules"], message.content
        now = time.monotonic()
        key = (message.guild.id, message.author.id)

        if not edited and mods["spam"]:
            hist = self.history[key]
            hist.append((now, message))
            s = c["spam"]
            recent = [m for t, m in hist if now - t <= s["seconds"]]
            if len(recent) >= s["messages"]:
                return "spam", "you're sending messages too fast. Slow down! 🕷️", recent

        if not edited and mods["duplicates"] and text.strip():
            norm = text.strip().lower()
            dq = self.dups[key]
            dq.append((now, norm, message))
            d = c["duplicates"]
            same = [m for t, n, m in dq if now - t <= d["seconds"] and n == norm]
            if len(same) >= d["count"]:
                return "duplicates", "please don't repeat the same message.", same

        if not text:
            return None

        if mods["invites"] and INVITE_RE.search(text):
            return "invites", "Discord invites aren't allowed here.", []

        if mods["links"]:
            for match in URL_RE.findall(text):
                host = domain_of(match)
                if not any(host == d or host.endswith("." + d) for d in c["allowed_domains"]):
                    return "links", "links aren't allowed here.", []

        if mods["badwords"]:
            for word in c["badwords"]:
                if compile_badword(word).search(text):
                    return "badwords", "watch your language! 🕸️", []

        if mods["caps"]:
            letters = [ch for ch in text if ch.isalpha()]
            if len(letters) >= c["caps"]["min_length"]:
                ratio = sum(1 for ch in letters if ch.isupper()) / len(letters) * 100
                if ratio >= c["caps"]["percent"]:
                    return "caps", "please don't shout (too many CAPS).", []

        if mods["mentions"]:
            count = len({u.id for u in message.mentions}) + len(message.role_mentions)
            if count >= c["mentions"]["limit"]:
                return "mentions", f"too many mentions in one message ({count}).", []

        if mods["emojis"]:
            count = len(CUSTOM_EMOJI_RE.findall(text)) + len(UNICODE_EMOJI_RE.findall(text))
            if count >= c["emojis"]["limit"]:
                return "emojis", f"too many emojis in one message ({count}).", []

        if mods["zalgo"]:
            if sum(1 for ch in text if unicodedata.category(ch) == "Mn") >= 10:
                return "zalgo", "glitchy/zalgo text isn't allowed.", []

        return None

    # ================================================================ ENFORCEMENT
    async def scan(self, message: discord.Message, edited: bool = False):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return
        c = self.cfg(message.guild.id)
        if not any(c["modules"].values()) or self.exempt(message, c):
            return
        result = self.detect(message, c, edited)
        if result:
            await self.handle(message, c, *result)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        await self.scan(message)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.content != after.content:
            await self.scan(after, edited=True)

    async def handle(self, message: discord.Message, c: dict, module: str, reason: str, extras: list):
        member, guild = message.author, message.guild
        key = (guild.id, member.id)

        # delete the offending message(s)
        to_delete = {m.id: m for m in extras}
        to_delete[message.id] = message
        for m in to_delete.values():
            try:
                await m.delete()
            except discord.HTTPException:
                pass
        self.history[key].clear()
        self.dups[key].clear()

        # strikes
        p = c["punishment"]
        now = time.time()
        strikes = self.strikes[key]
        strikes.append(now)
        while strikes and now - strikes[0] > p["window"]:
            strikes.popleft()
        count = len(strikes)

        # cases (shows up in /modlogs and the mod log channel)
        mod = self.bot.get_cog("Moderation")
        if mod:
            await mod.record(guild, "automod", member.id, self.bot.user.id, f"[{module}] {reason}")

        # notice (not spammed: max 1 per 5 seconds per user)
        if now - self.last_notice.get(key, 0) > 5:
            self.last_notice[key] = now
            try:
                await message.channel.send(embed=web_embed(
                    "Caught in the Web!", f"{member.mention}, {reason}\n"
                                          f"**Strike {min(count, p['strikes'])}/{p['strikes']}** • module: `{module}`", DARK),
                    delete_after=8)
            except discord.HTTPException:
                pass

        if count >= p["strikes"] and p["action"] != "none":
            strikes.clear()
            await self.punish(message, member, p, module)

    async def punish(self, message: discord.Message, member: discord.Member, p: dict, module: str):
        guild, action = message.guild, p["action"]
        why = f"AutoMod: reached {p['strikes']} strikes (last: {module})"
        try:
            if action == "timeout":
                secs = min(p["duration"], 28 * 86400)
                await member.timeout(timedelta(seconds=secs), reason=why)
                label, extra = "muted", f" for **{fmt_duration(secs)}**"
            elif action == "kick":
                await member.kick(reason=why)
                label, extra = "kicked", ""
            elif action == "ban":
                await member.ban(reason=why, delete_message_seconds=0)
                label, extra = "banned", ""
            else:
                return
        except discord.HTTPException:
            try:
                await message.channel.send(embed=web_embed(
                    "Web Snagged!", f"I tried to {action} {member.mention} but I don't have the permission "
                                    "(is my role above theirs?).", DARK), delete_after=10)
            except discord.HTTPException:
                pass
            return

        mod = self.bot.get_cog("Moderation")
        if mod:
            await mod.record(guild, {"timeout": "mute"}.get(action, action), member.id,
                             self.bot.user.id, why)
        try:
            await message.channel.send(embed=web_embed(
                "Web Justice!", f"{member.mention} was **{label}**{extra} for repeated violations. 🕷️", RED))
        except discord.HTTPException:
            pass

    # ================================================================ COMMANDS
    MODULE_CHOICES = [app_commands.Choice(name="ALL modules", value="all")] + \
                     [app_commands.Choice(name=v[:100], value=k) for k, v in MODULES.items()]

    def describe_module(self, name: str, c: dict) -> str:
        return {
            "spam": f"{c['spam']['messages']} msgs / {c['spam']['seconds']}s",
            "duplicates": f"{c['duplicates']['count']}× same msg / {c['duplicates']['seconds']}s",
            "links": f"{len(c['allowed_domains'])} allowed domain(s)",
            "invites": "blocks discord.gg invites",
            "badwords": f"{len(c['badwords'])} word(s) listed",
            "caps": f"≥{c['caps']['percent']}% caps, min {c['caps']['min_length']} letters",
            "mentions": f"limit {c['mentions']['limit']} per message",
            "emojis": f"limit {c['emojis']['limit']} per message",
            "zalgo": "10+ combining marks",
        }[name]

    @automod.command(name="status", description="See every AutoMod module and its settings")
    async def status(self, interaction: discord.Interaction):
        c = self.cfg(interaction.guild.id)
        lines = [f"{'🟢' if c['modules'][n] else '🔴'} **{n.title()}** — {self.describe_module(n, c)}"
                 for n in MODULES]
        p = c["punishment"]
        pun = "none (just delete + warn)" if p["action"] == "none" else (
            f"**{p['action']}** after **{p['strikes']}** strikes in {fmt_duration(p['window'])}"
            + (f" ({fmt_duration(p['duration'])})" if p["action"] == "timeout" else ""))
        ch = ", ".join(f"<#{i}>" for i in c["ignored_channels"]) or "none"
        ro = ", ".join(f"<@&{i}>" for i in c["ignored_roles"]) or "none"
        lines += [
            "", f"⚖️ **Punishment:** {pun}",
            f"🛡️ **Ignore staff:** {'yes' if c['ignore_staff'] else 'no'}",
            f"🙈 **Ignored channels:** {ch}", f"🙈 **Ignored roles:** {ro}",
        ]
        await interaction.response.send_message(embed=web_embed("AutoMod Web", "\n".join(lines), BLUE), ephemeral=True)

    @automod.command(name="toggle", description="Turn a module (or all) on or off")
    @app_commands.describe(module="Which protection?", enabled="On or off?")
    @app_commands.choices(module=MODULE_CHOICES)
    async def toggle(self, interaction: discord.Interaction, module: app_commands.Choice[str], enabled: bool):
        c = self.cfg(interaction.guild.id)
        names = list(MODULES) if module.value == "all" else [module.value]
        for n in names:
            c["modules"][n] = enabled
        self.save()
        label = "all modules" if module.value == "all" else f"**{module.value}**"
        await interaction.response.send_message(embed=web_embed(
            "AutoMod Updated!", f"{'🟢 Enabled' if enabled else '🔴 Disabled'} {label}.", BLUE))

    @automod.command(name="set", description="Change a numeric setting")
    @app_commands.describe(setting="Which setting?", value="The new value")
    @app_commands.choices(setting=[app_commands.Choice(name=f"{k} — {v[4]}"[:100], value=k) for k, v in SETTINGS.items()])
    async def set_setting(self, interaction: discord.Interaction, setting: app_commands.Choice[str], value: int):
        section, key, lo, hi, label = SETTINGS[setting.value]
        if not lo <= value <= hi:
            return await fail(interaction, f"**{setting.value}** must be between {lo} and {hi}.")
        self.cfg(interaction.guild.id)[section][key] = value
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "AutoMod Updated!", f"`{setting.value}` is now **{value}**.\n_{label}_", BLUE))

    @automod.command(name="punishment", description="What happens when someone collects too many strikes")
    @app_commands.describe(action="Punishment", strikes="Strikes needed (1-10)",
                           window="Strikes expire after this long, e.g. 10m",
                           duration="Mute length if action is timeout, e.g. 1h")
    @app_commands.choices(action=[app_commands.Choice(name=a, value=a) for a in ("none", "timeout", "kick", "ban")])
    async def punishment(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                         strikes: Optional[app_commands.Range[int, 1, 10]] = None,
                         window: Optional[str] = None, duration: Optional[str] = None):
        p = self.cfg(interaction.guild.id)["punishment"]
        if window:
            secs = parse_duration(window)
            if not secs:
                return await fail(interaction, "Invalid `window`. Try `10m` or `1h`.")
            p["window"] = secs
        if duration:
            secs = parse_duration(duration)
            if not secs or secs > 28 * 86400:
                return await fail(interaction, "Invalid `duration`. Try `10m`, `1h` (max 28d).")
            p["duration"] = secs
        if strikes:
            p["strikes"] = strikes
        p["action"] = action.value
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Punishment Set!", f"**{p['action']}** after **{p['strikes']}** strikes within {fmt_duration(p['window'])}"
                               + (f", mute length {fmt_duration(p['duration'])}" if p["action"] == "timeout" else ""), BLUE))

    @automod.command(name="badwords", description="Manage the bad word list (use * as a wildcard, e.g. spam*)")
    @app_commands.describe(action="add / remove / list", word="The word (not needed for list)")
    @app_commands.choices(action=[app_commands.Choice(name=a, value=a) for a in ("add", "remove", "list")])
    async def badwords(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                       word: Optional[str] = None):
        words = self.cfg(interaction.guild.id)["badwords"]
        if action.value == "list":
            text = ", ".join(f"`{w}`" for w in words) or "The list is empty."
            return await interaction.response.send_message(
                embed=web_embed("Bad Word List", text[:4000], DARK), ephemeral=True)
        if not word:
            return await fail(interaction, "Tell me which word.")
        word = word.strip().lower()
        if action.value == "add":
            if word in words:
                return await fail(interaction, "That word is already on the list.")
            words.append(word)
        else:
            if word not in words:
                return await fail(interaction, "That word isn't on the list.")
            words.remove(word)
        self.save()
        # ephemeral so the word isn't shown to everyone
        await interaction.response.send_message(embed=web_embed(
            "Bad Word List Updated!", f"{'Added' if action.value == 'add' else 'Removed'} the word. "
                                      f"({len(words)} total)", BLUE), ephemeral=True)

    @automod.command(name="allowlist", description="Manage domains that the links module allows")
    @app_commands.describe(action="add / remove / list", domain="e.g. youtube.com")
    @app_commands.choices(action=[app_commands.Choice(name=a, value=a) for a in ("add", "remove", "list")])
    async def allowlist(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                        domain: Optional[str] = None):
        domains = self.cfg(interaction.guild.id)["allowed_domains"]
        if action.value == "list":
            text = ", ".join(f"`{d}`" for d in domains) or "No allowed domains."
            return await interaction.response.send_message(
                embed=web_embed("Allowed Domains", text, BLUE), ephemeral=True)
        if not domain:
            return await fail(interaction, "Tell me which domain.")
        domain = domain_of(domain.strip())
        if action.value == "add":
            if domain in domains:
                return await fail(interaction, "That domain is already allowed.")
            domains.append(domain)
        else:
            if domain not in domains:
                return await fail(interaction, "That domain isn't on the list.")
            domains.remove(domain)
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Allowlist Updated!", f"{'Added' if action.value == 'add' else 'Removed'} `{domain}` "
                                  "(subdomains are included).", BLUE))

    @automod.command(name="ignorechannel", description="Toggle a channel AutoMod should ignore")
    async def ignorechannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        ids = self.cfg(interaction.guild.id)["ignored_channels"]
        if channel.id in ids:
            ids.remove(channel.id)
            text = f"AutoMod will watch {channel.mention} again."
        else:
            ids.append(channel.id)
            text = f"AutoMod will now ignore {channel.mention}. 🙈"
        self.save()
        await interaction.response.send_message(embed=web_embed("Ignore List Updated!", text, BLUE))

    @automod.command(name="ignorerole", description="Toggle a role AutoMod should ignore")
    async def ignorerole(self, interaction: discord.Interaction, role: discord.Role):
        ids = self.cfg(interaction.guild.id)["ignored_roles"]
        if role.id in ids:
            ids.remove(role.id)
            text = f"AutoMod will watch {role.mention} again."
        else:
            ids.append(role.id)
            text = f"AutoMod will now ignore {role.mention}. 🙈"
        self.save()
        await interaction.response.send_message(embed=web_embed("Ignore List Updated!", text, BLUE))

    @automod.command(name="staff", description="Should AutoMod ignore staff (Manage Messages / Admin)?")
    async def staff(self, interaction: discord.Interaction, ignore_staff: bool):
        self.cfg(interaction.guild.id)["ignore_staff"] = ignore_staff
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "AutoMod Updated!", f"Staff are now **{'ignored' if ignore_staff else 'checked like everyone else'}**. "
                                "(Turn this off to test AutoMod on yourself.)", BLUE))

    @automod.command(name="strikes", description="See a member's current AutoMod strikes")
    async def strikes_cmd(self, interaction: discord.Interaction, member: discord.Member):
        p = self.cfg(interaction.guild.id)["punishment"]
        dq = self.strikes[(interaction.guild.id, member.id)]
        now = time.time()
        count = sum(1 for t in dq if now - t <= p["window"])
        await interaction.response.send_message(embed=web_embed(
            "AutoMod Strikes", f"{member.mention} has **{count}/{p['strikes']}** active strikes.", DARK), ephemeral=True)

    @automod.command(name="clearstrikes", description="Reset a member's AutoMod strikes")
    async def clearstrikes(self, interaction: discord.Interaction, member: discord.Member):
        self.strikes[(interaction.guild.id, member.id)].clear()
        await interaction.response.send_message(embed=web_embed(
            "Strikes Cleared!", f"{member.mention}'s strikes were reset. 🕸️", BLUE))

    @automod.command(name="reset", description="Reset ALL AutoMod settings to defaults (everything off)")
    async def reset(self, interaction: discord.Interaction):
        self.data["guilds"][str(interaction.guild.id)] = {}
        self.save()
        self.cfg(interaction.guild.id)
        await interaction.response.send_message(embed=web_embed(
            "AutoMod Reset!", "All settings are back to defaults and every module is **off**.", DARK))


async def setup(bot: commands.Bot):
    await bot.add_cog(AutoMod(bot))
