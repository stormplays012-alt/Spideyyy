"""Makes the bot obey the dashboard: command gate, welcome/leave, automod, leveling, live presence."""
import json, os, random, re, time, discord
from discord.ext import commands
from . import settings as st, server

ACT = {"playing": discord.ActivityType.playing, "listening": discord.ActivityType.listening,
       "watching": discord.ActivityType.watching, "competing": discord.ActivityType.competing}
INVITE, LINK = re.compile(r"(discord\.gg|discord\.com/invite)/", re.I), re.compile(r"https?://", re.I)
XP_FILE = os.path.join(os.path.dirname(st.FILE), "levels.json")

class DashboardCog(commands.Cog):
    def __init__(self, bot):
        self.bot, self.runner, self.recent, self.cool = bot, None, {}, {}
        try:
            with open(XP_FILE, encoding="utf8") as f: self.xp = json.load(f)
        except (FileNotFoundError, ValueError): self.xp = {}
        bot.add_check(self.gate)
        bot.tree.interaction_check = self.tree_gate

    async def cog_load(self): self.runner = await server.start(self.bot)
    async def cog_unload(self):
        self.bot.remove_check(self.gate)
        try: del self.bot.tree.interaction_check
        except AttributeError: pass
        if self.runner: await self.runner.cleanup()

    # ---- command on/off switches (prefix + slash) ----
    async def gate(self, ctx):
        if not ctx.guild or not ctx.command or st.command_enabled(ctx.guild.id, ctx.command.qualified_name): return True
        await ctx.send("This command is turned off on this server."); return False
    async def tree_gate(self, interaction: discord.Interaction):
        c = interaction.command
        if not interaction.guild_id or not c or st.command_enabled(interaction.guild_id, c.qualified_name): return True
        await interaction.response.send_message("This command is turned off on this server.", ephemeral=True); return False

    # ---- apply nickname / status the moment it's saved ----
    @commands.Cog.listener()
    async def on_dashboard_change(self, guild_id, s):
        g = self.bot.get_guild(guild_id)
        try: await g.me.edit(nick=s["general"]["nickname"] or None)
        except discord.HTTPException: pass
        gen = s["general"]
        act = discord.Activity(type=ACT.get(gen["activityType"], ACT["playing"]), name=gen["activityText"]) if gen["activityText"] else None
        await self.bot.change_presence(status=discord.Status[gen["status"]], activity=act)

    # ---- welcome / leave / auto-role ----
    @commands.Cog.listener()
    async def on_member_join(self, m):
        w = st.get(m.guild.id)["welcome"]
        if w["autoRole"] and (r := m.guild.get_role(int(w["autoRole"]))):
            try: await m.add_roles(r)
            except discord.HTTPException: pass
        if w["enabled"] and w["channel"] and (ch := m.guild.get_channel(int(w["channel"]))):
            text = st.fmt(w["message"], m)
            await ch.send(embed=discord.Embed(description=text, color=0x4DE3B0)) if w["embed"] else await ch.send(text)

    @commands.Cog.listener()
    async def on_member_remove(self, m):
        w = st.get(m.guild.id)["welcome"]
        if w["leaveEnabled"] and w["channel"] and (ch := m.guild.get_channel(int(w["channel"]))):
            await ch.send(st.fmt(w["leaveMessage"], m))

    # ---- automod + leveling ----
    @commands.Cog.listener()
    async def on_message(self, msg):
        if not msg.guild or msg.author.bot: return
        s = st.get(msg.guild.id); m, c = s["moderation"], msg.content
        if not msg.author.guild_permissions.manage_messages:
            why = None
            if m["antiInvites"] and INVITE.search(c): why = "invite link"
            elif m["antiLinks"] and LINK.search(c): why = "link"
            elif m["capsFilter"] and len(c) > 10 and sum(x.isupper() for x in c) / len(c) > 0.7: why = "excess caps"
            elif m["badWords"] and any(w.strip() and w.strip().lower() in c.lower() for w in m["badWordList"].split(",")): why = "blocked word"
            elif m["antiSpam"]:
                k, now = (msg.guild.id, msg.author.id), time.time()
                self.recent[k] = [t for t in self.recent.get(k, []) if now - t < 5] + [now]
                if len(self.recent[k]) > 5: why = "spam"
            if why:
                try: await msg.delete()
                except discord.HTTPException: pass
                if m["logChannel"] and (ch := msg.guild.get_channel(int(m["logChannel"]))):
                    await ch.send(f"Removed a message from {msg.author.mention}: {why}")
                return
        await self.level(msg, s["leveling"])

    async def level(self, msg, lv):
        if not lv["enabled"]: return
        k, now = (msg.guild.id, msg.author.id), time.time()
        if now - self.cool.get(k, 0) < 60: return
        self.cool[k] = now
        g = self.xp.setdefault(str(msg.guild.id), {}); old = g.get(str(msg.author.id), 0)
        new = old + round(random.randint(15, 25) * lv["xpRate"]); g[str(msg.author.id)] = new
        with open(XP_FILE, "w", encoding="utf8") as f: json.dump(self.xp, f)
        a, b = int((old / 100) ** .5), int((new / 100) ** .5)
        if b <= a: return
        if lv["announce"]:
            ch = msg.guild.get_channel(int(lv["announceChannel"])) if lv["announceChannel"] else msg.channel
            await ch.send(f"{msg.author.mention} reached level {b}!")
        for line in lv["roleRewards"].splitlines():          # format: 5=RoleName
            if "=" in line:
                n, name = line.split("=", 1)
                role = discord.utils.get(msg.guild.roles, name=name.strip())
                if role and n.strip().isdigit() and b >= int(n): 
                    try: await msg.author.add_roles(role)
                    except discord.HTTPException: pass
