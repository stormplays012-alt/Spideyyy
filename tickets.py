import asyncio
import io
import json
import os
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK
from moderation import fail

DATA_FILE = "tickets.json"
DEFAULT_CFG = {"category": None, "support_roles": [], "log_channel": None, "counter": 0, "max_open": 1}


# ------------------------------------------------------------------ modals
class OpenModal(discord.ui.Modal, title="Open a ticket 🎫"):
    subject = discord.ui.TextInput(label="What do you need help with?", max_length=100)
    details = discord.ui.TextInput(label="Details (optional)", style=discord.TextStyle.paragraph,
                                   required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Tickets")
        await cog.create_ticket(interaction, str(self.subject).strip(), str(self.details).strip())


class CloseModal(discord.ui.Modal, title="Close this ticket"):
    reason = discord.ui.TextInput(label="Reason (optional)", required=False, max_length=200)

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("Tickets")
        await cog.close_ticket(interaction, interaction.channel, str(self.reason).strip() or "No reason provided")


# ------------------------------------------------------------------ persistent views
class TicketPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Open Ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ticket:open")
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(OpenModal())


class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Claim", emoji="🙋", style=discord.ButtonStyle.success, custom_id="ticket:claim")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Tickets")
        await cog.claim_ticket(interaction)

    @discord.ui.button(label="Close", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket:close")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("Tickets")
        ticket = cog.data["tickets"].get(str(interaction.channel.id))
        if ticket is None:
            return await fail(interaction, "This isn't an active ticket.")
        if not (interaction.user.id == ticket["opener"] or cog.is_staff(interaction.user)):
            return await fail(interaction, "Only the ticket owner or staff can close this ticket. 🕷️")
        await interaction.response.send_modal(CloseModal())


# ------------------------------------------------------------------ cog
class Tickets(commands.Cog):
    ticket = app_commands.Group(name="ticket", description="Support tickets", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = {"guilds": {}, "tickets": {}}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data["guilds"] = loaded.get("guilds", {}) if isinstance(loaded.get("guilds"), dict) else {}
                    self.data["tickets"] = loaded.get("tickets", {}) if isinstance(loaded.get("tickets"), dict) else {}
            except (OSError, json.JSONDecodeError):
                pass

    async def cog_load(self):
        self.bot.add_view(TicketPanel())
        self.bot.add_view(TicketControls())

    def save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, DATA_FILE)

    def cfg(self, gid: int) -> dict:
        g = self.data["guilds"].setdefault(str(gid), {})
        for k, v in DEFAULT_CFG.items():
            g.setdefault(k, list(v) if isinstance(v, list) else v)
        return g

    def is_staff(self, member: discord.Member) -> bool:
        if member.guild_permissions.administrator:
            return True
        ids = self.cfg(member.guild.id)["support_roles"]
        return any(r.id in ids for r in member.roles)

    def current_ticket(self, interaction: discord.Interaction):
        return self.data["tickets"].get(str(interaction.channel.id))

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        if self.data["tickets"].pop(str(channel.id), None) is not None:
            self.save()

    # ================================================================ CREATING
    async def create_ticket(self, interaction: discord.Interaction, subject: str, details: str):
        guild, user = interaction.guild, interaction.user
        c = self.cfg(guild.id)
        category = guild.get_channel(c["category"]) if c["category"] else None
        if not isinstance(category, discord.CategoryChannel):
            return await fail(interaction, "Tickets aren't set up yet. Ask an admin to run `/ticket setup`.")
        mine = [t for t in self.data["tickets"].values() if t["guild"] == guild.id and t["opener"] == user.id]
        if len(mine) >= c["max_open"]:
            where = f" (<#{mine[0]['channel']}>)" if mine else ""
            return await fail(interaction, f"You already have {len(mine)} open ticket(s){where}. Close it before opening another.")

        await interaction.response.defer(ephemeral=True)
        c["counter"] += 1
        number = c["counter"]
        slug = re.sub(r"[^a-z0-9]+", "-", user.name.lower()).strip("-")[:20] or "user"

        see = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                          attach_files=True, embed_links=True)
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: see,
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True,
                                                  read_message_history=True, attach_files=True, embed_links=True),
        }
        staff_roles = [r for r in (guild.get_role(i) for i in c["support_roles"]) if r]
        for role in staff_roles:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                                           attach_files=True, embed_links=True, manage_messages=True)
        try:
            channel = await guild.create_text_channel(
                f"ticket-{number:04d}-{slug}", category=category, overwrites=overwrites,
                topic=f"Ticket #{number} • opened by {user} ({user.id})", reason=f"Ticket opened by {user}")
        except discord.HTTPException as e:
            c["counter"] -= 1
            return await fail(interaction, f"I couldn't create the ticket channel: {e.text}")

        self.data["tickets"][str(channel.id)] = {
            "guild": guild.id, "channel": channel.id, "opener": user.id, "number": number,
            "subject": subject, "claimed_by": None, "created": int(time.time()),
        }
        self.save()

        embed = web_embed(f"Ticket #{number}", f"**Subject:** {subject}\n" + (f"**Details:** {details}\n" if details else "")
                          + "\nA staff member will be with you soon. 🕷️", BLUE)
        embed.set_author(name=user.display_name, icon_url=user.display_avatar.url)
        mentions = " ".join([user.mention] + [r.mention for r in staff_roles])
        await channel.send(content=mentions, embed=embed, view=TicketControls(),
                           allowed_mentions=discord.AllowedMentions(users=True, roles=True))
        await interaction.followup.send(embed=web_embed(
            "Ticket Created!", f"Your ticket is ready: {channel.mention} 🎫", BLUE), ephemeral=True)

    # ================================================================ CLAIM / CLOSE
    async def claim_ticket(self, interaction: discord.Interaction):
        ticket = self.current_ticket(interaction)
        if ticket is None:
            return await fail(interaction, "This isn't an active ticket.")
        if not self.is_staff(interaction.user):
            return await fail(interaction, "Only staff can claim tickets. 🕷️")
        if ticket["claimed_by"]:
            return await fail(interaction, f"This ticket is already claimed by <@{ticket['claimed_by']}>.")
        ticket["claimed_by"] = interaction.user.id
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Ticket Claimed!", f"{interaction.user.mention} is handling this ticket. 🙋", BLUE))

    async def build_transcript(self, channel: discord.TextChannel) -> str:
        lines = []
        async for m in channel.history(limit=2000, oldest_first=True):
            stamp = m.created_at.strftime("%Y-%m-%d %H:%M:%S")
            text = m.content or ""
            if m.embeds:
                text += " [embed]"
            for a in m.attachments:
                text += f" [attachment: {a.url}]"
            lines.append(f"[{stamp}] {m.author} ({m.author.id}): {text}")
        return "\n".join(lines) or "(empty)"

    async def close_ticket(self, interaction: discord.Interaction, channel, reason: str):
        ticket = self.data["tickets"].get(str(channel.id))
        if ticket is None:
            return await fail(interaction, "This isn't an active ticket.")
        if not (interaction.user.id == ticket["opener"] or self.is_staff(interaction.user)):
            return await fail(interaction, "Only the ticket owner or staff can close this ticket. 🕷️")

        await interaction.response.send_message(embed=web_embed(
            "Closing Ticket…", f"Saving the transcript. This channel will be deleted in 5 seconds.\n**Reason:** {reason}", DARK))
        transcript = await self.build_transcript(channel)
        filename = f"ticket-{ticket['number']:04d}.txt"
        guild = interaction.guild
        c = self.cfg(guild.id)
        summary = (f"**Ticket #{ticket['number']}** • opened by <@{ticket['opener']}>\n"
                   f"**Subject:** {ticket['subject']}\n**Closed by:** {interaction.user.mention}\n**Reason:** {reason}"
                   + (f"\n**Claimed by:** <@{ticket['claimed_by']}>" if ticket["claimed_by"] else ""))

        log = guild.get_channel(c["log_channel"]) if c["log_channel"] else None
        if log:
            try:
                await log.send(embed=web_embed("Ticket Closed", summary, DARK),
                               file=discord.File(io.BytesIO(transcript.encode("utf-8")), filename=filename))
            except discord.HTTPException:
                pass
        opener = guild.get_member(ticket["opener"])
        if opener:
            try:
                await opener.send(embed=web_embed("Your Ticket Was Closed", summary, DARK),
                                  file=discord.File(io.BytesIO(transcript.encode("utf-8")), filename=filename))
            except discord.HTTPException:
                pass

        self.data["tickets"].pop(str(channel.id), None)
        self.save()
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}: {reason}")
        except discord.HTTPException:
            pass

    # ================================================================ COMMANDS
    @ticket.command(name="setup", description="Choose the ticket category, support role and log channel")
    @app_commands.describe(category="Category where ticket channels are created", support_role="Role that can see tickets",
                           log_channel="Where transcripts are saved (optional)",
                           max_open="Tickets one member can have open at once (1-5)")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_cmd(self, interaction: discord.Interaction, category: discord.CategoryChannel,
                        support_role: discord.Role, log_channel: Optional[discord.TextChannel] = None,
                        max_open: app_commands.Range[int, 1, 5] = 1):
        c = self.cfg(interaction.guild.id)
        c["category"] = category.id
        if support_role.id not in c["support_roles"]:
            c["support_roles"].append(support_role.id)
        c["log_channel"] = log_channel.id if log_channel else None
        c["max_open"] = max_open
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Tickets Ready!", f"📁 **Category:** {category.mention}\n🎧 **Support role:** {support_role.mention}\n"
                              f"📜 **Transcripts:** {log_channel.mention if log_channel else 'not saved to a channel'}\n"
                              f"🎫 **Max open per member:** {max_open}\n\nNext: `/ticket panel` to post the Open Ticket button.", BLUE))

    @ticket.command(name="panel", description="Post the Open Ticket button")
    @app_commands.describe(channel="Where to post the panel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        if not self.cfg(interaction.guild.id)["category"]:
            return await fail(interaction, "Run `/ticket setup` first.")
        embed = web_embed("Need Help?", "Press the button below to open a private ticket with our staff. 🕷️", RED)
        try:
            await channel.send(embed=embed, view=TicketPanel())
        except discord.HTTPException:
            return await fail(interaction, f"I can't post in {channel.mention}. Check my permissions there.")
        await interaction.response.send_message(embed=web_embed(
            "Panel Posted!", f"The Open Ticket button is now in {channel.mention}. 🎫", BLUE), ephemeral=True)

    @ticket.command(name="supportrole", description="Add or remove roles that can see tickets")
    @app_commands.describe(action="add / remove / list", role="The role (not needed for list)")
    @app_commands.choices(action=[app_commands.Choice(name=a, value=a) for a in ("add", "remove", "list")])
    @app_commands.checks.has_permissions(manage_guild=True)
    async def supportrole(self, interaction: discord.Interaction, action: app_commands.Choice[str],
                          role: Optional[discord.Role] = None):
        ids = self.cfg(interaction.guild.id)["support_roles"]
        if action.value == "list":
            text = ", ".join(f"<@&{i}>" for i in ids) or "No support roles yet."
            return await interaction.response.send_message(embed=web_embed("Support Roles", text, BLUE), ephemeral=True)
        if role is None:
            return await fail(interaction, "Tell me which role.")
        if action.value == "add":
            if role.id in ids:
                return await fail(interaction, "That role is already a support role.")
            ids.append(role.id)
        else:
            if role.id not in ids:
                return await fail(interaction, "That role isn't a support role.")
            ids.remove(role.id)
        self.save()
        await interaction.response.send_message(embed=web_embed(
            "Support Roles Updated!", f"{'Added' if action.value == 'add' else 'Removed'} {role.mention}. 🎧\n"
                                      "(New tickets use this. Existing tickets keep their old access.)", BLUE))

    @ticket.command(name="add", description="Add a member to this ticket")
    async def add(self, interaction: discord.Interaction, user: discord.Member):
        ticket = self.current_ticket(interaction)
        if ticket is None:
            return await fail(interaction, "Use this inside a ticket channel.")
        if not self.is_staff(interaction.user):
            return await fail(interaction, "Only staff can add people to a ticket. 🕷️")
        await interaction.channel.set_permissions(user, view_channel=True, send_messages=True,
                                                  read_message_history=True, attach_files=True)
        await interaction.response.send_message(embed=web_embed("Member Added!", f"{user.mention} can now see this ticket. 🕸️", BLUE))

    @ticket.command(name="remove", description="Remove a member from this ticket")
    async def remove(self, interaction: discord.Interaction, user: discord.Member):
        ticket = self.current_ticket(interaction)
        if ticket is None:
            return await fail(interaction, "Use this inside a ticket channel.")
        if not self.is_staff(interaction.user):
            return await fail(interaction, "Only staff can remove people from a ticket. 🕷️")
        if user.id == ticket["opener"]:
            return await fail(interaction, "You can't remove the ticket owner. Close the ticket instead.")
        await interaction.channel.set_permissions(user, overwrite=None)
        await interaction.response.send_message(embed=web_embed("Member Removed!", f"{user.mention} can no longer see this ticket.", DARK))

    @ticket.command(name="rename", description="Rename this ticket")
    async def rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 90]):
        ticket = self.current_ticket(interaction)
        if ticket is None:
            return await fail(interaction, "Use this inside a ticket channel.")
        if not self.is_staff(interaction.user):
            return await fail(interaction, "Only staff can rename tickets. 🕷️")
        clean = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-")
        if not clean:
            return await fail(interaction, "Use letters or numbers in the name.")
        try:
            await asyncio.wait_for(interaction.channel.edit(name=clean[:100]), timeout=10)
        except asyncio.TimeoutError:
            return await fail(interaction, "Discord is rate-limiting renames (2 per 10 minutes). Try again later.")
        except discord.HTTPException as e:
            return await fail(interaction, e.text)
        await interaction.response.send_message(embed=web_embed("Ticket Renamed!", f"This ticket is now **#{clean}**.", BLUE))

    @ticket.command(name="close", description="Close the ticket and save a transcript")
    @app_commands.describe(reason="Why is it being closed?")
    async def close(self, interaction: discord.Interaction, reason: str = "No reason provided"):
        await self.close_ticket(interaction, interaction.channel, reason)


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
