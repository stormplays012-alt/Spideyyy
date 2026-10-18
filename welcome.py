import json
import os
import random
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
}

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
            embed = web_embed("New Arrival!", render(c["welcome_message"], member), BLUE)
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await channel.send(embed=embed)
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
        desc = (f"👋 **Welcome channel:** {ch(c['welcome_channel'])}\n"
                f"📝 **Welcome message:** {c['welcome_message'][:300]}\n\n"
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

    @welcome.command(name="test", description="Preview the welcome message")
    async def test(self, interaction: discord.Interaction):
        c = self.cfg(interaction.guild.id)
        embed = web_embed("New Arrival!", render(c["welcome_message"], interaction.user), BLUE)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.response.send_message(content="Here's how it looks:", embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Welcome(bot))
