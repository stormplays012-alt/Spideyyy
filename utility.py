import ast
import base64
import colorsys
import json
import operator
import os
import platform
import random
import re
import secrets
import string
import time
from datetime import timedelta
from typing import Optional, Union

import discord
from discord import app_commands
from discord.ext import commands, tasks

from theme import web_embed, RED, BLUE, DARK
from moderation import parse_duration, fmt_duration, fail

DATA_FILE = "utility.json"
NUMBER_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


# ------------------------------------------------------------------ storage
class UtilStore:
    def __init__(self, path: str = DATA_FILE):
        self.path = path
        self.data = {
            "reminders": [], "next_reminder": 1,
            "afk": {}, "todos": {},
            "suggest_channel": {}, "giveaways": [],
        }
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


# ------------------------------------------------------------------ safe calculator
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos,
}


def safe_eval(expr: str):
    if len(expr) > 200:
        raise ValueError("Expression too long.")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow) and abs(right) > 100:
                raise ValueError("Exponent too large (max 100).")
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError("Only numbers and + - * / // % ** ( ) are allowed.")

    return ev(ast.parse(expr.replace("^", "**").replace("×", "*").replace("÷", "/"), mode="eval"))


def parse_hex(text: str) -> Optional[int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text.strip())
    return int(m.group(1), 16) if m else None


# ------------------------------------------------------------------ cog
class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.store = UtilStore()
        self.started = time.time()
        self.reminder_loop.start()
        self.giveaway_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()
        self.giveaway_loop.cancel()

    # ================================================================ INFO
    @app_commands.command(name="serverinfo", description="Show info about this server")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction):
        g = interaction.guild
        bots = sum(1 for m in g.members if m.bot)
        embed = web_embed(g.name, g.description or "", BLUE)
        embed.add_field(name="ID", value=f"`{g.id}`")
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>")
        embed.add_field(name="Created", value=discord.utils.format_dt(g.created_at, "R"))
        embed.add_field(name="Members", value=f"{g.member_count} ({g.member_count - bots} humans, {bots} bots)")
        embed.add_field(name="Channels", value=f"💬 {len(g.text_channels)} • 🔊 {len(g.voice_channels)} • 📁 {len(g.categories)}")
        embed.add_field(name="Roles", value=str(len(g.roles)))
        embed.add_field(name="Emojis", value=str(len(g.emojis)))
        embed.add_field(name="Boosts", value=f"{g.premium_subscription_count} (Tier {g.premium_tier})")
        embed.add_field(name="Verification", value=str(g.verification_level).title())
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="whois", description="Show info about a member")
    @app_commands.describe(member="Who? (defaults to you)")
    @app_commands.guild_only()
    async def whois(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        roles = ", ".join(r.mention for r in reversed(member.roles[1:])) or "none"
        embed = web_embed(str(member), color=member.color.value or BLUE)
        embed.add_field(name="ID", value=f"`{member.id}`")
        embed.add_field(name="Nickname", value=member.nick or "none")
        embed.add_field(name="Bot?", value="yes" if member.bot else "no")
        embed.add_field(name="Created", value=discord.utils.format_dt(member.created_at, "R"))
        embed.add_field(name="Joined", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "?")
        embed.add_field(name="Top role", value=member.top_role.mention)
        embed.add_field(name=f"Roles ({len(member.roles) - 1})", value=roles[:1000], inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="roleinfo", description="Show info about a role")
    @app_commands.guild_only()
    async def roleinfo(self, interaction: discord.Interaction, role: discord.Role):
        embed = web_embed(role.name, color=role.color.value or BLUE)
        embed.add_field(name="ID", value=f"`{role.id}`")
        embed.add_field(name="Color", value=str(role.color))
        embed.add_field(name="Members", value=str(len(role.members)))
        embed.add_field(name="Position", value=str(role.position))
        embed.add_field(name="Mentionable", value="yes" if role.mentionable else "no")
        embed.add_field(name="Shown separately", value="yes" if role.hoist else "no")
        embed.add_field(name="Created", value=discord.utils.format_dt(role.created_at, "R"))
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="channelinfo", description="Show info about a channel")
    @app_commands.describe(channel="Which channel? (defaults to this one)")
    @app_commands.guild_only()
    async def channelinfo(self, interaction: discord.Interaction,
                          channel: Optional[Union[discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel]] = None):
        ch = channel or interaction.channel
        embed = web_embed(ch.name, color=BLUE)
        embed.add_field(name="ID", value=f"`{ch.id}`")
        embed.add_field(name="Type", value=str(ch.type).replace("_", " ").title())
        embed.add_field(name="Created", value=discord.utils.format_dt(ch.created_at, "R"))
        if getattr(ch, "category", None):
            embed.add_field(name="Category", value=ch.category.name)
        if isinstance(ch, discord.TextChannel):
            embed.add_field(name="Topic", value=ch.topic or "none", inline=False)
            embed.add_field(name="Slowmode", value=f"{ch.slowmode_delay}s")
            embed.add_field(name="NSFW", value="yes" if ch.is_nsfw() else "no")
        if isinstance(ch, discord.VoiceChannel):
            embed.add_field(name="Limit", value=str(ch.user_limit or "unlimited"))
            embed.add_field(name="Bitrate", value=f"{ch.bitrate // 1000} kbps")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="membercount", description="How many members are in this server")
    @app_commands.guild_only()
    async def membercount(self, interaction: discord.Interaction):
        g = interaction.guild
        bots = sum(1 for m in g.members if m.bot)
        await interaction.response.send_message(embed=web_embed(
            "Web Headcount", f"👥 **{g.member_count}** total\n🧑 **{g.member_count - bots}** humans\n🤖 **{bots}** bots", BLUE))

    @app_commands.command(name="banner", description="Show someone's profile banner")
    async def banner(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        user = await self.bot.fetch_user((user or interaction.user).id)
        if not user.banner:
            return await fail(interaction, "That user has no banner.")
        embed = web_embed(f"{user.display_name}'s Banner", color=BLUE)
        embed.set_image(url=user.banner.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="servericon", description="Show this server's icon")
    @app_commands.guild_only()
    async def servericon(self, interaction: discord.Interaction):
        if not interaction.guild.icon:
            return await fail(interaction, "This server has no icon.")
        embed = web_embed(f"{interaction.guild.name}'s Icon", color=BLUE)
        embed.set_image(url=interaction.guild.icon.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="emojis", description="List this server's custom emojis")
    @app_commands.guild_only()
    async def emojis(self, interaction: discord.Interaction):
        text = " ".join(str(e) for e in interaction.guild.emojis)[:4000] or "No custom emojis."
        await interaction.response.send_message(embed=web_embed("Server Emojis", text, BLUE))

    @app_commands.command(name="roles", description="List all roles in this server")
    @app_commands.guild_only()
    async def roles(self, interaction: discord.Interaction):
        text = " ".join(r.mention for r in reversed(interaction.guild.roles[1:]))[:4000] or "No roles."
        await interaction.response.send_message(embed=web_embed("Server Roles", text, BLUE))

    @app_commands.command(name="botinfo", description="About Spidey")
    async def botinfo(self, interaction: discord.Interaction):
        embed = web_embed("About Spidey", "Your friendly neighborhood bot 🕷️", BLUE)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)))
        embed.add_field(name="Users", value=str(sum(g.member_count or 0 for g in self.bot.guilds)))
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)}ms")
        embed.add_field(name="Uptime", value=fmt_duration(int(time.time() - self.started)))
        embed.add_field(name="discord.py", value=discord.__version__)
        embed.add_field(name="Python", value=platform.python_version())
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="uptime", description="How long Spidey has been online")
    async def uptime(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=web_embed(
            "Web Uptime", f"I've been swinging for **{fmt_duration(int(time.time() - self.started))}** 🕷️", BLUE))

    @app_commands.command(name="invite", description="Get Spidey's invite link")
    async def invite(self, interaction: discord.Interaction):
        url = discord.utils.oauth_url(self.bot.user.id, permissions=discord.Permissions(administrator=True),
                                      scopes=("bot", "applications.commands"))
        await interaction.response.send_message(embed=web_embed("Invite Spidey", f"[Click here to add me to a server 🕸️]({url})", BLUE))

    # ================================================================ TOOLS
    @app_commands.command(name="calc", description="Calculate a math expression")
    @app_commands.describe(expression="e.g. (12 + 8) * 3 / 4")
    async def calc(self, interaction: discord.Interaction, expression: str):
        try:
            result = safe_eval(expression)
        except ZeroDivisionError:
            return await fail(interaction, "You can't divide by zero. 🕷️")
        except (ValueError, SyntaxError, OverflowError, TypeError) as e:
            return await fail(interaction, str(e) if isinstance(e, ValueError) else "I couldn't read that expression.")
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        text = f"`{expression}`\n= **{result}**"
        await interaction.response.send_message(embed=web_embed("Web Calculator", text[:4000], BLUE))

    @app_commands.command(name="choose", description="Let Spidey pick for you")
    @app_commands.describe(options="Separate options with commas, e.g. pizza, burger, tacos")
    async def choose(self, interaction: discord.Interaction, options: str):
        items = [o.strip() for o in options.split(",") if o.strip()]
        if len(items) < 2:
            return await fail(interaction, "Give me at least 2 options separated by commas.")
        await interaction.response.send_message(embed=web_embed("My Spider-Sense Says…", f"**{random.choice(items)}** 🕷️", BLUE))

    @app_commands.command(name="roll", description="Roll dice, e.g. 2d6")
    @app_commands.describe(dice="Format NdM, like 1d20 or 3d6")
    async def roll(self, interaction: discord.Interaction, dice: str = "1d6"):
        m = re.fullmatch(r"(\d{1,2})d(\d{1,4})", dice.strip().lower())
        if not m or int(m.group(1)) < 1 or int(m.group(2)) < 2:
            return await fail(interaction, "Use the format `NdM`, like `1d20` or `3d6`.")
        n, sides = int(m.group(1)), int(m.group(2))
        rolls = [random.randint(1, sides) for _ in range(n)]
        await interaction.response.send_message(embed=web_embed(
            "Dice Rolled!", f"🎲 {', '.join(map(str, rolls))}\n**Total:** {sum(rolls)}", BLUE))

    @app_commands.command(name="coinflip", description="Flip a coin")
    async def coinflip(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=web_embed(
            "Coin Flip!", f"🪙 **{random.choice(['Heads', 'Tails'])}**", BLUE))

    @app_commands.command(name="color", description="Preview a hex color")
    @app_commands.describe(hex_code="e.g. #E62429")
    async def color(self, interaction: discord.Interaction, hex_code: str):
        value = parse_hex(hex_code)
        if value is None:
            return await fail(interaction, "Give me a 6-digit hex code like `#E62429`.")
        r, g, b = (value >> 16) & 255, (value >> 8) & 255, value & 255
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        text = (f"**HEX:** `#{value:06X}`\n**RGB:** `{r}, {g}, {b}`\n"
                f"**HSV:** `{round(h * 360)}°, {round(s * 100)}%, {round(v * 100)}%`\n**Int:** `{value}`")
        await interaction.response.send_message(embed=web_embed("Color Web", text, value))

    @app_commands.command(name="timestamp", description="Make Discord timestamps for a time from now")
    @app_commands.describe(duration="Time from now, e.g. 2h, 1d12h (leave empty for now)")
    async def timestamp(self, interaction: discord.Interaction, duration: str = "0s"):
        secs = parse_duration(duration)
        if secs is None:
            return await fail(interaction, "Use something like `30m`, `2h`, `1d12h`.")
        ts = int(time.time()) + secs
        rows = [f"`<t:{ts}:{f}>` → <t:{ts}:{f}>" for f in ("t", "T", "d", "D", "f", "F", "R")]
        await interaction.response.send_message(embed=web_embed("Web Time", "\n".join(rows), BLUE), ephemeral=True)

    @app_commands.command(name="snowflake", description="Find out when a Discord ID was created")
    async def snowflake(self, interaction: discord.Interaction, id: str):
        if not id.isdigit():
            return await fail(interaction, "Give me a numeric Discord ID.")
        created = discord.utils.snowflake_time(int(id))
        await interaction.response.send_message(embed=web_embed(
            "Snowflake Decoded", f"Created {discord.utils.format_dt(created, 'F')} ({discord.utils.format_dt(created, 'R')})", BLUE),
            ephemeral=True)

    @app_commands.command(name="password", description="Generate a strong random password (only you see it)")
    @app_commands.describe(length="8-64 characters")
    async def password(self, interaction: discord.Interaction, length: app_commands.Range[int, 8, 64] = 16):
        chars = string.ascii_letters + string.digits + "!@#$%^&*-_"
        pw = "".join(secrets.choice(chars) for _ in range(length))
        await interaction.response.send_message(embed=web_embed("Secret Silk", f"`{pw}`", DARK), ephemeral=True)

    @app_commands.command(name="base64", description="Encode or decode base64")
    @app_commands.choices(mode=[app_commands.Choice(name="encode", value="encode"),
                                app_commands.Choice(name="decode", value="decode")])
    async def base64_cmd(self, interaction: discord.Interaction, mode: app_commands.Choice[str], text: str):
        try:
            if mode.value == "encode":
                out = base64.b64encode(text.encode()).decode()
            else:
                out = base64.b64decode(text.encode(), validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            return await fail(interaction, "That isn't valid base64.")
        await interaction.response.send_message(embed=web_embed(f"Base64 {mode.name.title()}d", f"```{out[:3900]}```", BLUE), ephemeral=True)

    # ================================================================ SENDING
    @app_commands.command(name="say", description="Make Spidey send a message")
    @app_commands.describe(message="What to say", channel="Where? (defaults to this channel)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def say(self, interaction: discord.Interaction, message: str,
                  channel: Optional[discord.TextChannel] = None):
        channel = channel or interaction.channel
        try:
            await channel.send(message, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message("Sent! 🕸️", ephemeral=True)

    @app_commands.command(name="embed", description="Send a custom themed embed")
    @app_commands.describe(title="Embed title", description="Embed text", color="Hex color (optional)",
                           channel="Where? (defaults to this channel)")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    async def embed_cmd(self, interaction: discord.Interaction, title: str, description: str,
                        color: Optional[str] = None, channel: Optional[discord.TextChannel] = None):
        value = parse_hex(color) if color else RED
        if value is None:
            return await fail(interaction, "Color must be a 6-digit hex code like `#E62429`.")
        channel = channel or interaction.channel
        try:
            await channel.send(embed=web_embed(title, description.replace("\\n", "\n"), value),
                               allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message("Sent! 🕸️", ephemeral=True)

    # ================================================================ POLLS & SUGGESTIONS
    @app_commands.command(name="poll", description="Create a poll (leave options empty for yes/no)")
    @app_commands.describe(question="What are you asking?")
    @app_commands.guild_only()
    async def poll(self, interaction: discord.Interaction, question: str,
                   option1: Optional[str] = None, option2: Optional[str] = None,
                   option3: Optional[str] = None, option4: Optional[str] = None,
                   option5: Optional[str] = None):
        options = [o for o in (option1, option2, option3, option4, option5) if o]
        if len(options) == 1:
            return await fail(interaction, "Give at least 2 options, or none for a yes/no poll.")
        if options:
            body = "\n".join(f"{NUMBER_EMOJIS[i]} {o}" for i, o in enumerate(options))
            reactions = NUMBER_EMOJIS[:len(options)]
        else:
            body, reactions = "React with ✅ or ❌", ["✅", "❌"]
        embed = web_embed("Web Poll", f"**{question}**\n\n{body}", BLUE)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        for r in reactions:
            await msg.add_reaction(r)

    @app_commands.command(name="setsuggestchannel", description="Set the channel for /suggest")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setsuggestchannel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        self.store.data["suggest_channel"][str(interaction.guild.id)] = channel.id
        self.store.save()
        await interaction.response.send_message(embed=web_embed(
            "Suggestion Web Set!", f"Suggestions will go to {channel.mention} 🕸️", BLUE))

    @app_commands.command(name="suggest", description="Send a suggestion to the server staff")
    @app_commands.guild_only()
    async def suggest(self, interaction: discord.Interaction, suggestion: str):
        cid = self.store.data["suggest_channel"].get(str(interaction.guild.id))
        channel = interaction.guild.get_channel(cid) if cid else None
        if not channel:
            return await fail(interaction, "No suggestion channel is set. An admin can use `/setsuggestchannel`.")
        embed = web_embed("New Suggestion", suggestion, BLUE)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        try:
            msg = await channel.send(embed=embed)
            await msg.add_reaction("👍")
            await msg.add_reaction("👎")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message("Your suggestion was sent! 🕸️", ephemeral=True)

    # ================================================================ REMINDERS
    @app_commands.command(name="remind", description="Remind yourself about something later")
    @app_commands.describe(duration="e.g. 10m, 2h, 1d12h", text="What to remind you about")
    async def remind(self, interaction: discord.Interaction, duration: str, text: str):
        secs = parse_duration(duration)
        if not secs or secs > 365 * 86400:
            return await fail(interaction, "Use a duration like `10m`, `2h` or `1d12h` (max 1 year).")
        d = self.store.data
        rid = d["next_reminder"]
        d["next_reminder"] += 1
        d["reminders"].append({
            "id": rid, "user": interaction.user.id, "channel": interaction.channel_id,
            "time": time.time() + secs, "text": text,
        })
        self.store.save()
        await interaction.response.send_message(embed=web_embed(
            "Reminder Spun!", f"I'll remind you {discord.utils.format_dt(discord.utils.utcnow() + timedelta(seconds=secs), 'R')}:\n**{text}**\n(ID `{rid}`)", BLUE))

    @app_commands.command(name="reminders", description="List your pending reminders")
    async def reminders(self, interaction: discord.Interaction):
        mine = [r for r in self.store.data["reminders"] if r["user"] == interaction.user.id]
        lines = [f"`{r['id']}` • <t:{int(r['time'])}:R> — {r['text']}" for r in mine[:15]]
        await interaction.response.send_message(
            embed=web_embed("Your Reminders", "\n".join(lines) or "No pending reminders. 🕸️", BLUE), ephemeral=True)

    @app_commands.command(name="delreminder", description="Delete one of your reminders")
    async def delreminder(self, interaction: discord.Interaction, reminder_id: int):
        for r in self.store.data["reminders"]:
            if r["id"] == reminder_id and r["user"] == interaction.user.id:
                self.store.data["reminders"].remove(r)
                self.store.save()
                return await interaction.response.send_message(
                    embed=web_embed("Reminder Cut!", f"Deleted reminder `{reminder_id}`.", BLUE), ephemeral=True)
        await fail(interaction, "You have no reminder with that ID.")

    @tasks.loop(seconds=15)
    async def reminder_loop(self):
        now = time.time()
        for r in list(self.store.data["reminders"]):
            if r["time"] > now:
                continue
            embed = web_embed("Reminder!", f"⏰ {r['text']}", RED)
            sent = False
            channel = self.bot.get_channel(r["channel"])
            if channel:
                try:
                    await channel.send(content=f"<@{r['user']}>", embed=embed)
                    sent = True
                except discord.HTTPException:
                    pass
            if not sent:
                try:
                    user = await self.bot.fetch_user(r["user"])
                    await user.send(embed=embed)
                except discord.HTTPException:
                    pass
            self.store.data["reminders"].remove(r)
            self.store.save()

    @reminder_loop.before_loop
    async def _before_reminders(self):
        await self.bot.wait_until_ready()

    # ================================================================ AFK
    @app_commands.command(name="afk", description="Set yourself as AFK (I'll tell people who ping you)")
    @app_commands.guild_only()
    async def afk(self, interaction: discord.Interaction, reason: str = "AFK"):
        afk = self.store.data["afk"].setdefault(str(interaction.guild.id), {})
        afk[str(interaction.user.id)] = {"reason": reason[:200], "since": time.time()}
        self.store.save()
        await interaction.response.send_message(embed=web_embed(
            "Gone Swinging!", f"{interaction.user.mention} is now AFK: **{reason[:200]}** 🕷️", BLUE))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        afk = self.store.data["afk"].get(str(message.guild.id), {})
        if not afk:
            return
        uid = str(message.author.id)
        if uid in afk and time.time() - afk[uid]["since"] > 10:
            del afk[uid]
            self.store.save()
            await message.channel.send(
                embed=web_embed("Welcome Back!", f"{message.author.mention}, I removed your AFK. 🕸️", BLUE),
                delete_after=10)
        for m in message.mentions[:3]:
            entry = afk.get(str(m.id))
            if entry and m.id != message.author.id:
                await message.channel.send(embed=web_embed(
                    "They're Away!", f"**{m.display_name}** is AFK: {entry['reason']}\n"
                                     f"(since <t:{int(entry['since'])}:R>)", DARK), delete_after=15)

    # ================================================================ TODO
    todo = app_commands.Group(name="todo", description="Your personal to-do list")

    @todo.command(name="add", description="Add an item to your to-do list")
    async def todo_add(self, interaction: discord.Interaction, item: str):
        items = self.store.data["todos"].setdefault(str(interaction.user.id), [])
        if len(items) >= 25:
            return await fail(interaction, "Your list is full (25 items). Finish some first! 🕷️")
        items.append(item[:200])
        self.store.save()
        await interaction.response.send_message(embed=web_embed("Task Spun!", f"Added: **{item[:200]}**", BLUE), ephemeral=True)

    @todo.command(name="list", description="Show your to-do list")
    async def todo_list(self, interaction: discord.Interaction):
        items = self.store.data["todos"].get(str(interaction.user.id), [])
        text = "\n".join(f"**{i}.** {t}" for i, t in enumerate(items, 1)) or "Nothing to do! 🕸️"
        await interaction.response.send_message(embed=web_embed("Your To-Do Web", text, BLUE), ephemeral=True)

    @todo.command(name="done", description="Complete (remove) an item by its number")
    async def todo_done(self, interaction: discord.Interaction, number: int):
        items = self.store.data["todos"].get(str(interaction.user.id), [])
        if not 1 <= number <= len(items):
            return await fail(interaction, "No item with that number.")
        done = items.pop(number - 1)
        self.store.save()
        await interaction.response.send_message(embed=web_embed("Task Done!", f"✅ ~~{done}~~", BLUE), ephemeral=True)

    @todo.command(name="clear", description="Clear your whole to-do list")
    async def todo_clear(self, interaction: discord.Interaction):
        self.store.data["todos"][str(interaction.user.id)] = []
        self.store.save()
        await interaction.response.send_message(embed=web_embed("Web Cleared!", "Your to-do list is empty. 🕸️", BLUE), ephemeral=True)

    # ================================================================ GIVEAWAYS
    giveaway = app_commands.Group(
        name="giveaway", description="Run giveaways", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True))

    @giveaway.command(name="start", description="Start a giveaway")
    @app_commands.describe(duration="e.g. 30m, 2h, 1d", prize="What are you giving away?", winners="How many winners (1-10)")
    async def giveaway_start(self, interaction: discord.Interaction, duration: str, prize: str,
                             winners: app_commands.Range[int, 1, 10] = 1):
        secs = parse_duration(duration)
        if not secs or secs > 30 * 86400:
            return await fail(interaction, "Use a duration like `30m`, `2h` or `1d` (max 30d).")
        end = int(time.time()) + secs
        embed = web_embed("GIVEAWAY!", f"🎁 **{prize}**\n\nReact with 🎉 to enter!\n"
                                       f"**Ends:** <t:{end}:R>\n**Winners:** {winners}\n**Host:** {interaction.user.mention}")
        await interaction.response.send_message(embed=embed)
        msg = await interaction.original_response()
        await msg.add_reaction("🎉")
        self.store.data["giveaways"].append({
            "message": msg.id, "channel": msg.channel.id, "guild": interaction.guild.id,
            "prize": prize, "winners": winners, "end": end, "ended": False,
        })
        self.store.save()

    async def _pick_winners(self, g: dict):
        channel = self.bot.get_channel(g["channel"])
        if not channel:
            return None, None
        try:
            msg = await channel.fetch_message(g["message"])
        except discord.HTTPException:
            return None, None
        reaction = discord.utils.get(msg.reactions, emoji="🎉")
        users = [u async for u in reaction.users() if not u.bot] if reaction else []
        return msg, random.sample(users, min(g["winners"], len(users)))

    async def _finish(self, g: dict):
        g["ended"] = True
        self.store.save()
        msg, winners = await self._pick_winners(g)
        if msg is None:
            return
        if winners:
            names = ", ".join(w.mention for w in winners)
            text = f"🎁 **{g['prize']}**\n\n**Winner(s):** {names}"
            announce = f"🎉 Congrats {names}! You won **{g['prize']}**!"
        else:
            text = f"🎁 **{g['prize']}**\n\nNobody entered. 🕸️"
            announce = "Nobody entered the giveaway. 🕸️"
        await msg.edit(embed=web_embed("GIVEAWAY ENDED", text, DARK))
        await msg.channel.send(announce, reference=msg)

    @giveaway.command(name="end", description="End a giveaway early")
    @app_commands.describe(message_id="The giveaway message ID")
    async def giveaway_end(self, interaction: discord.Interaction, message_id: str):
        for g in self.store.data["giveaways"]:
            if str(g["message"]) == message_id and g["guild"] == interaction.guild.id and not g["ended"]:
                await interaction.response.send_message("Ending giveaway… 🎉", ephemeral=True)
                return await self._finish(g)
        await fail(interaction, "No running giveaway with that message ID.")

    @giveaway.command(name="reroll", description="Pick new winner(s) for an ended giveaway")
    @app_commands.describe(message_id="The giveaway message ID")
    async def giveaway_reroll(self, interaction: discord.Interaction, message_id: str):
        for g in self.store.data["giveaways"]:
            if str(g["message"]) == message_id and g["guild"] == interaction.guild.id and g["ended"]:
                msg, winners = await self._pick_winners(g)
                if not winners:
                    return await fail(interaction, "No valid entries to pick from.")
                names = ", ".join(w.mention for w in winners)
                return await interaction.response.send_message(f"🎉 New winner(s) for **{g['prize']}**: {names}")
        await fail(interaction, "No ended giveaway with that message ID.")

    @tasks.loop(seconds=15)
    async def giveaway_loop(self):
        now = time.time()
        for g in list(self.store.data["giveaways"]):
            if not g["ended"] and g["end"] <= now:
                try:
                    await self._finish(g)
                except discord.HTTPException:
                    pass

    @giveaway_loop.before_loop
    async def _before_giveaways(self):
        await self.bot.wait_until_ready()

    # ================================================================ HELP
    @app_commands.command(name="utilhelp", description="List all utility commands")
    async def utilhelp(self, interaction: discord.Interaction):
        desc = (
            "**ℹ️ Info**\n`/serverinfo` `/whois` `/roleinfo` `/channelinfo` `/membercount`\n"
            "`/banner` `/servericon` `/emojis` `/roles` `/botinfo` `/uptime` `/invite`\n\n"
            "**🧰 Tools**\n`/calc` `/choose` `/roll` `/coinflip` `/color` `/timestamp`\n"
            "`/snowflake` `/password` `/base64`\n\n"
            "**📣 Sending** (mods)\n`/say` `/embed`\n\n"
            "**🗳️ Polls & ideas**\n`/poll` `/suggest` `/setsuggestchannel`\n\n"
            "**⏰ Personal**\n`/remind` `/reminders` `/delreminder` `/afk`\n`/todo add|list|done|clear`\n\n"
            "**🎉 Giveaways** (mods)\n`/giveaway start|end|reroll`"
        )
        await interaction.response.send_message(embed=web_embed("Spidey's Utility Web", desc, DARK), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Utility(bot))
