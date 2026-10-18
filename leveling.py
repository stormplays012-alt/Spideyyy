import json
import math
import os
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from theme import web_embed, RED, BLUE, DARK
from moderation import fail

DATA_FILE = "leveling.json"

DEFAULT_CFG = {
    "enabled": True,
    "xp_min": 15,
    "xp_max": 25,
    "cooldown": 60,          # seconds between XP-earning messages
    "voice_xp": 10,          # XP per minute in voice (0 = off)
    "multiplier": 1.0,
    "announce": True,
    "announce_channel": None,  # None = same channel as the message
    "stack_rewards": True,     # keep old reward roles when a new one is earned
    "ignored_channels": [],
}

LEVEL_UP_LINES = [
    "swung up to **level {level}**! 🕷️⬆️",
    "spun a stronger web and hit **level {level}**! 🕸️",
    "just reached **level {level}**! My spider-sense is tingling! 🕷️",
]


# ------------------------------------------------------------------ level math
def xp_to_next(level: int) -> int:
    """XP needed to go from `level` to `level + 1`."""
    return 5 * level ** 2 + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    return sum(xp_to_next(i) for i in range(level))


def level_info(total_xp: int):
    """-> (level, xp into this level, xp needed for next level)"""
    level, rem = 0, total_xp
    while rem >= xp_to_next(level):
        rem -= xp_to_next(level)
        level += 1
    return level, rem, xp_to_next(level)


def progress_bar(cur: int, need: int, length: int = 12) -> str:
    filled = int(length * cur / need) if need else 0
    return "▰" * filled + "▱" * (length - filled)


# ------------------------------------------------------------------ leaderboard view
class LeaderboardView(discord.ui.View):
    PER_PAGE = 10

    def __init__(self, cog: "Leveling", guild: discord.Guild, requester_id: int):
        super().__init__(timeout=120)
        self.cog, self.guild, self.requester_id = cog, guild, requester_id
        self.page = 0
        self._refresh()

    def _rows(self):
        return self.cog.ranking(self.guild.id)

    def _pages(self) -> int:
        return max(1, math.ceil(len(self._rows()) / self.PER_PAGE))

    def _refresh(self):
        self.prev_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self._pages() - 1

    def build(self) -> discord.Embed:
        rows = self._rows()
        start = self.page * self.PER_PAGE
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, (uid, data) in enumerate(rows[start:start + self.PER_PAGE], start + 1):
            lvl = level_info(data["xp"])[0]
            badge = medals[i - 1] if i <= 3 else f"**{i}.**"
            lines.append(f"{badge} <@{uid}> — Level **{lvl}** • {data['xp']:,} XP")
        embed = web_embed("Web Leaderboard", "\n".join(lines) or "Nobody has earned XP yet. 🕸️", BLUE)
        embed.set_footer(text=f"Page {self.page + 1}/{self._pages()} • 🕸️ Spidey")
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("Run `/leaderboard` yourself to flip pages. 🕷️", ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._refresh()
        await interaction.response.edit_message(embed=self.build(), view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._refresh()
        await interaction.response.edit_message(embed=self.build(), view=self)


# ------------------------------------------------------------------ cog
class Leveling(commands.Cog):
    levelconfig = app_commands.Group(
        name="levelconfig", description="Configure leveling", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True))
    levelreward = app_commands.Group(
        name="levelreward", description="Role rewards for reaching levels", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True))

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = {"guilds": {}}
        self.dirty = False
        self.cooldowns: dict[tuple[int, int], float] = {}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    self.data.update(json.load(f))
            except (OSError, json.JSONDecodeError):
                pass
        self.save_loop.start()
        self.voice_loop.start()

    def cog_unload(self):
        self.save_loop.cancel()
        self.voice_loop.cancel()
        self.save()

    # ---------- storage
    def save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, DATA_FILE)
        self.dirty = False

    @tasks.loop(seconds=30)
    async def save_loop(self):
        if self.dirty:
            self.save()

    def guild(self, gid: int) -> dict:
        g = self.data["guilds"].setdefault(str(gid), {})
        g.setdefault("config", {})
        for k, v in DEFAULT_CFG.items():
            g["config"].setdefault(k, list(v) if isinstance(v, list) else v)
        g.setdefault("users", {})
        g.setdefault("rewards", {})   # {"5": role_id}
        return g

    def ranking(self, gid: int):
        return sorted(self.guild(gid)["users"].items(), key=lambda kv: kv[1]["xp"], reverse=True)

    # ---------- XP engine
    async def add_xp(self, member: discord.Member, amount: int, channel=None, message: bool = False):
        g = self.guild(member.guild.id)
        cfg = g["config"]
        user = g["users"].setdefault(str(member.id), {"xp": 0, "messages": 0})
        old_level = level_info(user["xp"])[0]
        user["xp"] += max(1, int(amount * cfg["multiplier"]))
        if message:
            user["messages"] += 1
        self.dirty = True
        new_level = level_info(user["xp"])[0]
        if new_level > old_level:
            await self.level_up(member, new_level, channel)

    async def level_up(self, member: discord.Member, level: int, channel):
        cfg = self.guild(member.guild.id)["config"]
        gained = await self.apply_rewards(member, level)
        if not cfg["announce"]:
            return
        dest = member.guild.get_channel(cfg["announce_channel"]) if cfg["announce_channel"] else None
        dest = dest or channel or member.guild.system_channel
        if not dest:
            return
        text = f"{member.mention} {random.choice(LEVEL_UP_LINES).format(level=level)}"
        if gained:
            text += "\n🎁 New reward: " + ", ".join(r.mention for r in gained)
        embed = web_embed("Level Up!", text, BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await dest.send(embed=embed)
        except discord.HTTPException:
            pass

    async def apply_rewards(self, member: discord.Member, level: int) -> list:
        g = self.guild(member.guild.id)
        guild = member.guild
        earned = []
        for lvl, rid in sorted((int(l), r) for l, r in g["rewards"].items()):
            role = guild.get_role(rid)
            if lvl <= level and role:
                earned.append(role)
        if not earned:
            return []
        stack = g["config"]["stack_rewards"]
        target = earned if stack else [earned[-1]]
        to_add = [r for r in target if r not in member.roles and not r.managed and r < guild.me.top_role]
        to_remove = [] if stack else [r for r in earned[:-1] if r in member.roles and r < guild.me.top_role]
        try:
            if to_add:
                await member.add_roles(*to_add, reason="Spidey leveling reward")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Spidey leveling reward (replaced)")
        except discord.HTTPException:
            return []
        return to_add

    # ---------- listeners
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.author, discord.Member):
            return
        cfg = self.guild(message.guild.id)["config"]
        if not cfg["enabled"] or message.channel.id in cfg["ignored_channels"]:
            return
        if len(message.content.strip()) < 3 and not message.attachments:
            return
        key = (message.guild.id, message.author.id)
        now = time.time()
        if now - self.cooldowns.get(key, 0) < cfg["cooldown"]:
            return
        self.cooldowns[key] = now
        await self.add_xp(message.author, random.randint(cfg["xp_min"], cfg["xp_max"]),
                          message.channel, message=True)

    @tasks.loop(seconds=60)
    async def voice_loop(self):
        for guild in self.bot.guilds:
            cfg = self.guild(guild.id)["config"]
            if not cfg["enabled"] or cfg["voice_xp"] <= 0:
                continue
            for vc in guild.voice_channels:
                if vc == guild.afk_channel:
                    continue
                humans = [m for m in vc.members if not m.bot]
                if len(humans) < 2:
                    continue
                for m in humans:
                    if m.voice and not (m.voice.self_deaf or m.voice.deaf):
                        await self.add_xp(m, cfg["voice_xp"])

    @voice_loop.before_loop
    @save_loop.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()

    # ================================================================ PLAYER COMMANDS
    @app_commands.command(name="rank", description="See your level and XP")
    @app_commands.describe(member="Whose rank? (defaults to you)")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        if member.bot:
            return await fail(interaction, "Bots don't earn XP. 🕷️")
        g = self.guild(interaction.guild.id)
        user = g["users"].get(str(member.id), {"xp": 0, "messages": 0})
        level, cur, need = level_info(user["xp"])
        ids = [uid for uid, _ in self.ranking(interaction.guild.id)]
        position = f"#{ids.index(str(member.id)) + 1}" if str(member.id) in ids else "unranked"
        desc = (f"**Level {level}** • Rank **{position}**\n"
                f"{progress_bar(cur, need)}  `{cur:,} / {need:,} XP`\n\n"
                f"✨ **Total XP:** {user['xp']:,}\n💬 **Messages:** {user['messages']:,}")
        embed = web_embed(f"{member.display_name}'s Web Rank", desc, BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="leaderboard", description="Show the top members by XP")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction):
        view = LeaderboardView(self, interaction.guild, interaction.user.id)
        await interaction.response.send_message(embed=view.build(), view=view)

    # ================================================================ ADMIN: CONFIG
    @levelconfig.command(name="status", description="Show leveling settings")
    async def cfg_status(self, interaction: discord.Interaction):
        g = self.guild(interaction.guild.id)
        c = g["config"]
        ann = f"<#{c['announce_channel']}>" if c["announce_channel"] else "same channel as the message"
        ign = ", ".join(f"<#{i}>" for i in c["ignored_channels"]) or "none"
        desc = (
            f"{'🟢' if c['enabled'] else '🔴'} **Leveling** is {'on' if c['enabled'] else 'off'}\n"
            f"✨ **Message XP:** {c['xp_min']}–{c['xp_max']} every {c['cooldown']}s\n"
            f"🎙️ **Voice XP:** {c['voice_xp']}/min {'(off)' if c['voice_xp'] <= 0 else ''}\n"
            f"✖️ **Multiplier:** {c['multiplier']}x\n"
            f"📣 **Level-up messages:** {'on' if c['announce'] else 'off'} → {ann}\n"
            f"🎁 **Stack rewards:** {'yes' if c['stack_rewards'] else 'no (only highest)'}\n"
            f"🙈 **Ignored channels:** {ign}\n"
            f"🏅 **Rewards set:** {len(g['rewards'])}"
        )
        await interaction.response.send_message(embed=web_embed("Leveling Web", desc, BLUE), ephemeral=True)

    @levelconfig.command(name="toggle", description="Turn leveling on or off")
    async def cfg_toggle(self, interaction: discord.Interaction, enabled: bool):
        self.guild(interaction.guild.id)["config"]["enabled"] = enabled
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Leveling Updated!", f"Leveling is now {'🟢 **on**' if enabled else '🔴 **off**'}.", BLUE))

    @levelconfig.command(name="xp", description="Set message XP range and cooldown")
    @app_commands.describe(minimum="Min XP per message", maximum="Max XP per message", cooldown="Seconds between XP messages")
    async def cfg_xp(self, interaction: discord.Interaction,
                     minimum: Optional[app_commands.Range[int, 1, 500]] = None,
                     maximum: Optional[app_commands.Range[int, 1, 500]] = None,
                     cooldown: Optional[app_commands.Range[int, 0, 600]] = None):
        c = self.guild(interaction.guild.id)["config"]
        if minimum is not None:
            c["xp_min"] = minimum
        if maximum is not None:
            c["xp_max"] = maximum
        if cooldown is not None:
            c["cooldown"] = cooldown
        if c["xp_min"] > c["xp_max"]:
            c["xp_min"], c["xp_max"] = c["xp_max"], c["xp_min"]
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "XP Updated!", f"Message XP: **{c['xp_min']}–{c['xp_max']}**, cooldown **{c['cooldown']}s**.", BLUE))

    @levelconfig.command(name="voicexp", description="XP per minute in voice (0 = off)")
    async def cfg_voicexp(self, interaction: discord.Interaction, amount: app_commands.Range[int, 0, 200]):
        self.guild(interaction.guild.id)["config"]["voice_xp"] = amount
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Voice XP Updated!", f"Voice XP is now **{amount}/min**" + (" (off)" if amount == 0 else "") +
            ".\nOnly members who aren't deafened, in a call with 2+ humans, count.", BLUE))

    @levelconfig.command(name="multiplier", description="Multiply all XP gains (e.g. 2 for double XP weekend)")
    async def cfg_multiplier(self, interaction: discord.Interaction, value: app_commands.Range[float, 0.1, 10.0]):
        self.guild(interaction.guild.id)["config"]["multiplier"] = round(value, 2)
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Multiplier Set!", f"XP multiplier is now **{round(value, 2)}x**.", BLUE))

    @levelconfig.command(name="announce", description="Level-up messages on/off and where to send them")
    @app_commands.describe(enabled="Send level-up messages?", channel="Channel (empty = where they chatted)")
    async def cfg_announce(self, interaction: discord.Interaction, enabled: bool,
                           channel: Optional[discord.TextChannel] = None):
        c = self.guild(interaction.guild.id)["config"]
        c["announce"] = enabled
        c["announce_channel"] = channel.id if channel else None
        self.dirty = True
        where = channel.mention if channel else "the channel where they chat"
        await interaction.response.send_message(embed=web_embed(
            "Announcements Updated!", f"Level-up messages: **{'on' if enabled else 'off'}** → {where}", BLUE))

    @levelconfig.command(name="stack", description="Keep old reward roles when earning new ones?")
    async def cfg_stack(self, interaction: discord.Interaction, stack: bool):
        self.guild(interaction.guild.id)["config"]["stack_rewards"] = stack
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Rewards Updated!", "Members " + ("**keep** all reward roles." if stack else "only keep the **highest** reward role."), BLUE))

    @levelconfig.command(name="ignorechannel", description="Toggle a channel that gives no XP")
    async def cfg_ignore(self, interaction: discord.Interaction, channel: discord.TextChannel):
        ids = self.guild(interaction.guild.id)["config"]["ignored_channels"]
        if channel.id in ids:
            ids.remove(channel.id)
            text = f"{channel.mention} gives XP again."
        else:
            ids.append(channel.id)
            text = f"{channel.mention} no longer gives XP. 🙈"
        self.dirty = True
        await interaction.response.send_message(embed=web_embed("Ignore List Updated!", text, BLUE))

    # ---------- admin: manage users
    @levelconfig.command(name="setxp", description="Set a member's total XP")
    async def cfg_setxp(self, interaction: discord.Interaction, member: discord.Member,
                        xp: app_commands.Range[int, 0, 100_000_000]):
        user = self.guild(interaction.guild.id)["users"].setdefault(str(member.id), {"xp": 0, "messages": 0})
        user["xp"] = xp
        self.dirty = True
        level = level_info(xp)[0]
        await self.apply_rewards(member, level)
        await interaction.response.send_message(embed=web_embed(
            "XP Set!", f"{member.mention} now has **{xp:,} XP** (level **{level}**).", BLUE))

    @levelconfig.command(name="setlevel", description="Set a member to a specific level")
    async def cfg_setlevel(self, interaction: discord.Interaction, member: discord.Member,
                           level: app_commands.Range[int, 0, 500]):
        user = self.guild(interaction.guild.id)["users"].setdefault(str(member.id), {"xp": 0, "messages": 0})
        user["xp"] = total_xp_for_level(level)
        self.dirty = True
        await self.apply_rewards(member, level)
        await interaction.response.send_message(embed=web_embed(
            "Level Set!", f"{member.mention} is now **level {level}**.", BLUE))

    @levelconfig.command(name="resetuser", description="Reset one member's XP")
    async def cfg_resetuser(self, interaction: discord.Interaction, member: discord.Member):
        self.guild(interaction.guild.id)["users"].pop(str(member.id), None)
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "XP Reset!", f"{member.mention}'s XP was wiped. 🕸️", DARK))

    @levelconfig.command(name="reset", description="Wipe EVERYONE's XP (cannot be undone)")
    @app_commands.describe(confirm="Set to True to confirm")
    async def cfg_reset(self, interaction: discord.Interaction, confirm: bool):
        if not confirm:
            return await fail(interaction, "Set `confirm` to **True** if you really want to wipe everyone's XP.")
        self.guild(interaction.guild.id)["users"] = {}
        self.dirty = True
        await interaction.response.send_message(embed=web_embed("Web Wiped!", "All XP in this server was reset.", DARK))

    # ================================================================ ADMIN: REWARDS
    @levelreward.command(name="add", description="Give a role when members reach a level")
    async def reward_add(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 500], role: discord.Role):
        if role.managed or role.is_default():
            return await fail(interaction, "I can't hand out that role.")
        if role >= interaction.guild.me.top_role:
            return await fail(interaction, "That role is above mine. Move my role higher in Server Settings → Roles.")
        self.guild(interaction.guild.id)["rewards"][str(level)] = role.id
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Reward Added!", f"Reaching **level {level}** now gives {role.mention}. 🎁", BLUE))

    @levelreward.command(name="remove", description="Remove the reward for a level")
    async def reward_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 500]):
        if self.guild(interaction.guild.id)["rewards"].pop(str(level), None) is None:
            return await fail(interaction, "There's no reward at that level.")
        self.dirty = True
        await interaction.response.send_message(embed=web_embed("Reward Removed!", f"Removed the level **{level}** reward.", DARK))

    @levelreward.command(name="list", description="Show all level rewards")
    async def reward_list(self, interaction: discord.Interaction):
        rewards = self.guild(interaction.guild.id)["rewards"]
        lines = [f"**Level {l}** → <@&{r}>" for l, r in sorted(rewards.items(), key=lambda kv: int(kv[0]))]
        await interaction.response.send_message(
            embed=web_embed("Level Rewards", "\n".join(lines) or "No rewards set. 🕸️", BLUE), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Leveling(bot))
