"""
AI Server Builder  -  /build <prompt>

  1. An admin describes the server ("anime server with manga + art + gaming, Mod/Helper roles ...").
  2. The AI writes a PLAN (roles, categories, channels, permissions) as JSON.
  3. The bot VALIDATES the plan (dangerous permissions are stripped, sizes are capped) and shows a PREVIEW.
  4. Only after the admin presses Confirm does the bot create anything.

Safety:
  * Administrator only.
  * It only ADDS roles/channels. Nothing existing is edited or deleted.
  * The AI can never hand out Administrator, Manage Server/Roles/Channels/Webhooks or @everyone pings.
  * Everything it created can be removed again with the Undo button or "@Spidey undo build".
"""
import asyncio
import json
import os
import re
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK

DATA_FILE = "builder.json"

MAX_ROLES = 20
MAX_CATEGORIES = 12
MAX_CHANNELS = 50
GUILD_COOLDOWN = 20  # seconds between plan requests per server (protects the free AI limits)

# ----------------------------------------------------------------------------- permissions
# Only names on these lists can ever be used. Anything else the AI writes is thrown away.
CHANNEL_PERMS = {
    "view_channel", "send_messages", "send_messages_in_threads", "create_public_threads",
    "create_private_threads", "embed_links", "attach_files", "add_reactions", "use_external_emojis",
    "use_external_stickers", "read_message_history", "manage_messages", "manage_threads",
    "use_application_commands", "connect", "speak", "stream", "use_voice_activation",
    "priority_speaker", "create_instant_invite", "request_to_speak", "mute_members",
    "deafen_members", "move_members",
}
ROLE_ONLY_PERMS = {"kick_members", "ban_members", "moderate_members", "manage_nicknames", "change_nickname"}
ROLE_PERMS = CHANNEL_PERMS | ROLE_ONLY_PERMS

try:  # keep only names this discord.py version really knows
    _valid = {name for name, _ in discord.Permissions.none()}
    CHANNEL_PERMS &= _valid
    ROLE_PERMS &= _valid
except Exception:
    pass

CHANNEL_TYPES = {"text", "voice", "announcement", "forum", "stage"}
TYPE_ALIASES = {"news": "announcement", "announcements": "announcement", "chat": "text",
                "stage_channel": "stage", "voice_channel": "voice", "text_channel": "text"}
TYPE_ICON = {"text": "💬", "voice": "🔊", "announcement": "📢", "forum": "🗂️", "stage": "🎙️"}


# ----------------------------------------------------------------------------- tiny helpers
def load_data() -> dict:
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_data(data: dict):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        print(f"builder: could not save {DATA_FILE}: {e!r}")


def clean_name(value, limit: int = 100) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def channel_slug(value) -> str:
    """Text-style channel names: lowercase, dashes, no odd ASCII symbols (emoji and unicode are kept)."""
    s = clean_name(value).lower().replace(" ", "-")
    s = re.sub(r"[^\w\-\u0080-\U0010ffff]", "", s)
    return re.sub(r"-{2,}", "-", s).strip("-")[:100]


def parse_color(value) -> Optional[int]:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(value or "").strip())
    return int(m.group(1), 16) if m else None


def extract_json(text: str) -> dict:
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("no JSON object found")
    data = json.loads(t[a:b + 1])
    if not isinstance(data, dict):
        raise ValueError("JSON is not an object")
    return data


def as_list(value) -> list:
    return value if isinstance(value, list) else []


# ----------------------------------------------------------------------------- plan validation
def sanitize_plan(raw: dict, guild: discord.Guild, community: bool):
    """Turn whatever the AI wrote into a safe plan. Returns (plan, notes)."""
    notes: list[str] = []
    existing = {r.name.lower(): r for r in guild.roles}

    # ---- roles
    roles, seen = [], set()
    for r in as_list(raw.get("roles")):
        if not isinstance(r, dict):
            continue
        name = clean_name(r.get("name"))
        key = name.lower()
        if not name or key in ("everyone", "@everyone") or key in seen:
            continue
        if len(roles) >= MAX_ROLES:
            notes.append(f"Only the first {MAX_ROLES} roles are used.")
            break
        seen.add(key)
        asked = [p for p in as_list(r.get("permissions")) if isinstance(p, str)]
        perms = [p for p in dict.fromkeys(asked) if p in ROLE_PERMS]
        dropped = [p for p in dict.fromkeys(asked) if p not in ROLE_PERMS]
        if dropped:
            notes.append(f"Blocked risky/unknown permission(s) for **{name}**: `{', '.join(dropped[:6])}`")
        roles.append({
            "name": name, "color": parse_color(r.get("color")), "hoist": bool(r.get("hoist")),
            "mentionable": bool(r.get("mentionable")), "permissions": perms, "exists": key in existing,
        })

    new_roles = sum(1 for r in roles if not r["exists"])
    if len(guild.roles) + new_roles > 245:
        keep = max(0, 245 - len(guild.roles))
        trimmed, count = [], 0
        for r in roles:
            if r["exists"] or count < keep:
                trimmed.append(r)
                count += 0 if r["exists"] else 1
        roles = trimmed
        notes.append("Your server is close to Discord's 250 role limit, so some roles were skipped.")

    known = {r["name"].lower() for r in roles} | set(existing) | {"everyone", "@everyone"}

    def refs(value, where: str) -> list:
        out = []
        for n in as_list(value):
            n = clean_name(n)
            if not n:
                continue
            if n.lower() in known:
                out.append(n)
            else:
                notes.append(f"Ignored unknown role `{n}` in {where}.")
        return out

    def overrides(value, where: str) -> list:
        out = []
        for o in as_list(value):
            if not isinstance(o, dict):
                continue
            role = clean_name(o.get("role"))
            if role.lower() not in known:
                if role:
                    notes.append(f"Ignored unknown role `{role}` in {where}.")
                continue
            allow = [p for p in as_list(o.get("allow")) if p in CHANNEL_PERMS]
            deny = [p for p in as_list(o.get("deny")) if p in CHANNEL_PERMS and p not in allow]
            if allow or deny:
                out.append({"role": role, "allow": allow, "deny": deny})
        return out

    # ---- categories + channels
    cats, total, downgraded = [], 0, False
    room = max(0, 495 - len(guild.channels))  # Discord caps a server at 500 channels
    limit = min(MAX_CHANNELS, room)
    for c in as_list(raw.get("categories")):
        if not isinstance(c, dict):
            continue
        cname = clean_name(c.get("name"))
        if not cname:
            continue
        if len(cats) >= MAX_CATEGORIES or len(cats) + total >= limit:
            notes.append("Some categories were skipped (size limit).")
            break
        cat = {"name": cname, "private": bool(c.get("private")),
               "visible_to": refs(c.get("visible_to"), f"category {cname}"),
               "overrides": overrides(c.get("overrides"), f"category {cname}"), "channels": []}
        for ch in as_list(c.get("channels")):
            if not isinstance(ch, dict):
                continue
            if total + len(cats) + 1 >= limit:
                notes.append("Some channels were skipped (size limit).")
                break
            ctype = str(ch.get("type") or "text").lower().strip()
            ctype = TYPE_ALIASES.get(ctype, ctype)
            if ctype not in CHANNEL_TYPES:
                ctype = "text"
            if not community and ctype in ("announcement", "forum", "stage"):
                ctype = "voice" if ctype == "stage" else "text"
                downgraded = True
            name = clean_name(ch.get("name")) if ctype in ("voice", "stage") else channel_slug(ch.get("name"))
            if not name:
                continue
            try:
                slow = max(0, min(21600, int(ch.get("slowmode") or 0)))
            except (TypeError, ValueError):
                slow = 0
            cat["channels"].append({
                "name": name, "type": ctype, "topic": clean_name(ch.get("topic"), 300),
                "readonly": bool(ch.get("readonly")), "writers": refs(ch.get("writers"), f"#{name}"),
                "private": bool(ch.get("private")), "visible_to": refs(ch.get("visible_to"), f"#{name}"),
                "slowmode": slow, "overrides": overrides(ch.get("overrides"), f"#{name}"),
            })
            total += 1
        cats.append(cat)

    if downgraded:
        notes.append("Announcement/Forum/Stage channels need *Community* enabled, so they became text/voice channels.")
    return {"roles": roles, "categories": cats}, notes


def plan_counts(plan: dict):
    channels = sum(len(c["channels"]) for c in plan["categories"])
    new_roles = sum(1 for r in plan["roles"] if not r["exists"])
    return new_roles, len(plan["categories"]), channels


def preview_embed(plan: dict, notes: list, prompt: str) -> discord.Embed:
    new_roles, n_cats, n_chans = plan_counts(plan)
    embed = web_embed("Server Build Preview",
                      f"**Your request:** {prompt[:300]}\n\nWill create **{new_roles}** roles, **{n_cats}** categories "
                      f"and **{n_chans}** channels.\n*Nothing existing is changed or deleted.*", BLUE)

    lines = []
    for r in plan["roles"]:
        extra = ", ".join(r["permissions"][:4]) + ("…" if len(r["permissions"]) > 4 else "")
        tag = " *(already exists, will be reused)*" if r["exists"] else ""
        lines.append(f"• **{r['name']}**{tag}" + (f" — `{extra}`" if extra else ""))
    if lines:
        text = "\n".join(lines)
        embed.add_field(name=f"🎭 Roles ({len(plan['roles'])})", value=(text[:1000] + "…") if len(text) > 1000 else text,
                        inline=False)

    for cat in plan["categories"][:10]:
        head = f"📁 {cat['name']}" + ("  🔒" if cat["private"] else "")
        rows = []
        for ch in cat["channels"]:
            flags = ("🔒" if ch["private"] else "") + ("📖" if ch["readonly"] else "")
            rows.append(f"{TYPE_ICON[ch['type']]} {ch['name'][:40]} {flags}".rstrip())
        text = "\n".join(rows) or "*(empty)*"
        embed.add_field(name=head[:250], value=(text[:1000] + "…") if len(text) > 1000 else text, inline=True)
    if len(plan["categories"]) > 10:
        embed.add_field(name="…", value=f"and {len(plan['categories']) - 10} more categories", inline=False)

    if notes:
        text = "\n".join(f"• {n}" for n in notes[:8])
        embed.add_field(name="⚠️ Notes", value=text[:1000], inline=False)
    embed.add_field(name="Legend", value="🔒 private  •  📖 read-only", inline=False)
    return embed


# ----------------------------------------------------------------------------- AI prompt
def system_prompt(community: bool) -> str:
    chan_perms = ", ".join(sorted(CHANNEL_PERMS))
    role_perms = ", ".join(sorted(ROLE_PERMS))
    types = "text, voice, announcement, forum, stage" if community else "text, voice"
    return f"""You design Discord server layouts. Reply with ONE JSON object and nothing else (no markdown, no explanation).

Schema:
{{"roles":[{{"name":"Moderator","color":"#E74C3C","hoist":true,"mentionable":true,"permissions":["kick_members","manage_messages"]}}],
 "categories":[{{"name":"INFORMATION","private":false,"visible_to":[],"overrides":[],
   "channels":[{{"name":"rules","type":"text","topic":"Read these first","readonly":true,"writers":["Moderator"],
                 "private":false,"visible_to":[],"slowmode":0,
                 "overrides":[{{"role":"Member","allow":["add_reactions"],"deny":[]}}]}}]}}]}}

Rules:
- List roles from HIGHEST rank to LOWEST. Max {MAX_ROLES} roles, {MAX_CATEGORIES} categories, {MAX_CHANNELS} channels in total.
- Channel "type" must be one of: {types}.
- Text-style channel names are lowercase with dashes and may start with an emoji, e.g. "🎨┃fan-art" or "fan-art".
- "private": true hides a category/channel from everyone except the roles listed in "visible_to".
- "readonly": true stops @everyone from sending messages; "writers" are roles that can still send.
- "overrides" give a role extra allow/deny on that category/channel. Only these names may be used: {chan_perms}.
- Role "permissions" may only use these names: {role_perms}. Give moderators sensible powers, members almost nothing extra.
- Refer to roles ONLY by names from your "roles" list or from the existing roles you are given.
- NEVER use administrator, manage_guild, manage_roles, manage_channels, manage_webhooks or mention_everyone.
- If the user mentions staff/mods/admins, add a private staff category visible only to those roles.
- Typical servers include: information (rules, announcements), general chat, topic channels from the request, voice channels.
- Write short channel topics. Do not include NSFW channels."""


# ----------------------------------------------------------------------------- confirm / undo UI
class ConfirmView(discord.ui.View):
    """Confirm / Cancel buttons that only the person who asked can press."""

    def __init__(self, user_id: int, on_confirm, confirm_label: str = "Confirm & Build",
                 danger: bool = False, timeout: float = 240):
        super().__init__(timeout=timeout)
        self.user_id = user_id
        self.on_confirm = on_confirm
        self.message = None
        self.done = False
        self.confirm_button.label = confirm_label
        if danger:
            self.confirm_button.style = discord.ButtonStyle.danger

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Only the person who started this can press these buttons.",
                                                    ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.done:
            return await interaction.response.send_message("Already used.", ephemeral=True)
        self.done = True
        self.stop()
        await self.on_confirm(interaction)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖️")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.done = True
        self.stop()
        await interaction.response.edit_message(embed=web_embed("Cancelled", "Nothing was changed. 🕸️", DARK), view=None)

    async def on_timeout(self):
        if self.done or self.message is None:
            return
        try:
            await self.message.edit(embed=web_embed("Expired", "No answer in time, so nothing was changed. 🕸️", DARK),
                                    view=None)
        except discord.HTTPException:
            pass


class UndoView(discord.ui.View):
    def __init__(self, cog, user_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id = user_id

    @discord.ui.button(label="Undo this build", style=discord.ButtonStyle.danger, emoji="↩️")
    async def undo(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message("Only the admin who built this can undo it.", ephemeral=True)
        await self.cog.ask_undo(interaction)


# ----------------------------------------------------------------------------- the cog
class ServerBuilder(commands.Cog):
    """AI Server Builder: /build <prompt>"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.busy: set[int] = set()
        self.last_plan: dict[int, float] = {}

    # ---------- overwrites
    @staticmethod
    def _copy(ow: dict) -> dict:
        return {t: discord.PermissionOverwrite.from_pair(*o.pair()) for t, o in ow.items()}

    def _overwrites(self, guild: discord.Guild, role_map: dict, base: dict, spec: dict) -> dict:
        ow = self._copy(base)

        def get(target):
            return ow.setdefault(target, discord.PermissionOverwrite())

        if spec.get("private"):
            get(guild.default_role).view_channel = False
            for n in spec.get("visible_to", []):
                role = role_map.get(n.lower())
                if role:
                    get(role).view_channel = True
            get(guild.me).view_channel = True  # never lock the bot out of its own build
        if spec.get("readonly"):
            e = get(guild.default_role)
            e.send_messages = False
            e.send_messages_in_threads = False
            e.create_public_threads = False
            for n in spec.get("writers", []):
                role = role_map.get(n.lower())
                if role:
                    get(role).send_messages = True
        for o in spec.get("overrides", []):
            role = role_map.get(o["role"].lower())
            if not role:
                continue
            po = get(role)
            for p in o["allow"]:
                setattr(po, p, True)
            for p in o["deny"]:
                setattr(po, p, False)
        return ow

    # ---------- /build
    @app_commands.command(name="build", description="🏗️ Let AI build roles, channels & permissions from your prompt")
    @app_commands.describe(prompt="Describe the server, e.g. 'anime server: manga, art, gaming + Mod/Helper roles, private staff area'")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def build(self, interaction: discord.Interaction, prompt: app_commands.Range[str, 10, 1000]):
        guild = interaction.guild

        async def refuse(text: str):
            await interaction.response.send_message(embed=web_embed("Can't build", text, RED), ephemeral=True)

        if not interaction.user.guild_permissions.administrator:
            return await refuse("Only server **Administrators** can use `/build`.")
        me = guild.me
        if not (me.guild_permissions.manage_roles and me.guild_permissions.manage_channels):
            return await refuse("I need the **Manage Roles** and **Manage Channels** permissions. "
                                "Give me those (or Administrator) and try again.")
        ai = self.bot.get_cog("AIChat")
        if ai is None or not ai.targets:
            return await refuse("AI isn't set up. Add `GROQ_API_KEY` or `GEMINI_API_KEY` to the bot's variables.")
        if guild.id in self.busy:
            return await refuse("A build is already running in this server. Wait for it to finish.")
        wait = GUILD_COOLDOWN - (time.time() - self.last_plan.get(guild.id, 0))
        if wait > 0:
            return await refuse(f"Slow down a little, try again in **{int(wait) + 1}s**.")
        self.last_plan[guild.id] = time.time()

        await interaction.response.defer(ephemeral=True, thinking=True)
        community = "COMMUNITY" in guild.features

        context = (f"Server request: {prompt}\n"
                   f"Existing roles (do not recreate): {', '.join(r.name for r in guild.roles[1:][:40]) or 'none'}\n"
                   f"Existing categories: {', '.join(c.name for c in guild.categories[:25]) or 'none'}")
        messages = [{"role": "system", "content": system_prompt(community)},
                    {"role": "user", "content": context}]

        raw = None
        for attempt in range(2):
            text, busy = await ai._complete(messages, max_tokens=5000, deadline_s=90, req_timeout=60)
            if not text:
                msg = ("The AI is busy right now, try again in a minute. 🕸️" if busy
                       else "The AI couldn't answer. Check the console for the error.")
                return await interaction.edit_original_response(embed=web_embed("AI problem", msg, RED))
            try:
                raw = extract_json(text)
                break
            except (ValueError, json.JSONDecodeError):
                messages = messages + [{"role": "assistant", "content": text[:1500]},
                                       {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."}]
        if raw is None:
            return await interaction.edit_original_response(embed=web_embed(
                "AI problem", "The AI didn't return a usable plan. Try again, maybe with a clearer prompt.", RED))

        plan, notes = sanitize_plan(raw, guild, community)
        new_roles, n_cats, n_chans = plan_counts(plan)
        if not n_cats and not new_roles:
            return await interaction.edit_original_response(embed=web_embed(
                "Nothing to build", "The plan was empty. Try describing your server in more detail.", RED))

        async def on_confirm(btn_interaction: discord.Interaction):
            await self.run_build(btn_interaction, plan)

        view = ConfirmView(interaction.user.id, on_confirm)
        await interaction.edit_original_response(embed=preview_embed(plan, notes, prompt), view=view)
        view.message = await interaction.original_response()

    # ---------- the actual building
    async def run_build(self, interaction: discord.Interaction, plan: dict):
        guild = interaction.guild
        if guild.id in self.busy:
            return await interaction.response.send_message("A build is already running.", ephemeral=True)
        self.busy.add(guild.id)
        try:
            await self._build(interaction, plan)
        except Exception as e:  # never leave the lock stuck
            print(f"builder: unexpected error: {e!r}")
            try:
                await interaction.edit_original_response(embed=web_embed("Build stopped", f"Something went wrong: `{e}`", RED), view=None)
            except discord.HTTPException:
                pass
        finally:
            self.busy.discard(guild.id)

    async def _build(self, interaction: discord.Interaction, plan: dict):
        guild = interaction.guild
        reason = f"Spidey /build by {interaction.user} ({interaction.user.id})"
        new_roles, n_cats, n_chans = plan_counts(plan)
        total = new_roles + n_cats + n_chans
        done, failures = 0, []
        created = {"roles": [], "channels": []}

        await interaction.response.edit_message(
            embed=web_embed("Building…", f"Starting… 0/{total} 🕸️", BLUE), view=None)

        async def progress(force: bool = False):
            if force or done % 6 == 0:
                try:
                    await interaction.edit_original_response(embed=web_embed(
                        "Building…", f"Created **{done}/{total}** 🕸️\nThis can take a minute, Discord limits how fast I can build.", BLUE))
                except discord.HTTPException:
                    pass

        role_map = {"everyone": guild.default_role, "@everyone": guild.default_role}
        for r in guild.roles:
            role_map.setdefault(r.name.lower(), r)

        # ---- roles
        for r in plan["roles"]:
            if r["exists"]:
                continue
            kwargs = dict(name=r["name"], hoist=r["hoist"], mentionable=r["mentionable"], reason=reason,
                          colour=discord.Colour(r["color"]) if r["color"] is not None else discord.Colour.default())
            try:
                try:
                    role = await guild.create_role(permissions=discord.Permissions(**{p: True for p in r["permissions"]}), **kwargs)
                except discord.Forbidden:  # the bot lacks one of those permissions itself: make the role without them
                    role = await guild.create_role(**kwargs)
                    failures.append(f"Role **{r['name']}** was made without extra permissions (I don't have them myself).")
                role_map[r["name"].lower()] = role
                created["roles"].append(role.id)
            except discord.HTTPException as e:
                failures.append(f"Role **{r['name']}**: {e.text or e}")
            done += 1
            await progress()
            await asyncio.sleep(0.3)

        # ---- categories + channels
        for cat in plan["categories"]:
            cat_ow = self._overwrites(guild, role_map, {}, cat)
            try:
                category = await guild.create_category(cat["name"], overwrites=cat_ow, reason=reason)
                created["channels"].append(category.id)
            except discord.HTTPException as e:
                failures.append(f"Category **{cat['name']}**: {e.text or e}")
                done += 1 + len(cat["channels"])
                await progress(True)
                continue
            done += 1
            await progress()
            await asyncio.sleep(0.3)

            for ch in cat["channels"]:
                ow = self._overwrites(guild, role_map, cat_ow, ch)
                common = dict(category=category, overwrites=ow, reason=reason)
                try:
                    channel = await self._make_channel(guild, ch, common)
                    created["channels"].append(channel.id)
                except discord.HTTPException as e:
                    failures.append(f"Channel **{ch['name']}**: {e.text or e}")
                done += 1
                await progress()
                await asyncio.sleep(0.3)

        data = load_data()
        data[str(guild.id)] = {"roles": created["roles"], "channels": created["channels"], "at": int(time.time())}
        save_data(data)

        made_roles, made_chans = len(created["roles"]), len(created["channels"])
        desc = (f"✅ Created **{made_roles}** roles and **{made_chans}** categories/channels.\n"
                "Check the channel list, then move my role up/down or tweak anything you like.\n"
                "Not happy? Press **Undo** (works for 15 minutes) or later say `@Spidey undo build`.")
        embed = web_embed("Server Built!", desc, RED)
        if failures:
            embed.add_field(name=f"⚠️ {len(failures)} problem(s)", value="\n".join(f"• {f}" for f in failures[:6])[:1000],
                            inline=False)
        await interaction.edit_original_response(embed=embed, view=UndoView(self, interaction.user.id))

    async def _make_channel(self, guild, ch: dict, common: dict):
        t = ch["type"]
        if t == "voice":
            return await guild.create_voice_channel(ch["name"], **common)
        if t == "stage":
            return await guild.create_stage_channel(ch["name"], **common)
        if t == "forum":
            return await guild.create_forum(ch["name"], topic=ch["topic"] or None, **common)
        # text / announcement
        try:
            return await guild.create_text_channel(
                ch["name"], topic=ch["topic"] or None, slowmode_delay=ch["slowmode"],
                news=(t == "announcement"), **common)
        except discord.HTTPException:
            if t != "announcement":
                raise
            return await guild.create_text_channel(ch["name"], topic=ch["topic"] or None,
                                                   slowmode_delay=ch["slowmode"], **common)

    # ---------- undo
    async def ask_undo(self, interaction: discord.Interaction):
        entry = load_data().get(str(interaction.guild.id))
        if not entry:
            return await interaction.response.send_message("There's no build to undo.", ephemeral=True)

        async def go(btn: discord.Interaction):
            await self.do_undo(btn)

        view = ConfirmView(interaction.user.id, go, confirm_label="Yes, delete it all", danger=True)
        await interaction.response.edit_message(embed=web_embed(
            "Undo last build?",
            f"This **deletes** the {len(entry['roles'])} roles and {len(entry['channels'])} channels/categories "
            "created by the last build, **including any messages in them**.", RED), view=view)
        view.message = await interaction.original_response()

    async def do_undo(self, interaction: discord.Interaction):
        guild = interaction.guild
        data = load_data()
        entry = data.get(str(guild.id))
        if not entry:
            return await interaction.response.edit_message(embed=web_embed("Nothing to undo", "", DARK), view=None)
        await interaction.response.edit_message(embed=web_embed("Undoing…", "Deleting what I built 🕸️", BLUE), view=None)
        reason = f"Spidey /build undo by {interaction.user}"
        removed = 0
        for cid in reversed(entry["channels"]):  # children first, categories last
            ch = guild.get_channel(cid)
            if ch:
                try:
                    await ch.delete(reason=reason)
                    removed += 1
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.3)
        for rid in entry["roles"]:
            role = guild.get_role(rid)
            if role:
                try:
                    await role.delete(reason=reason)
                    removed += 1
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.3)
        data.pop(str(guild.id), None)
        save_data(data)
        await interaction.edit_original_response(embed=web_embed("Undone", f"Removed **{removed}** things. 🕸️", RED))

    # "@Spidey undo build"  (for when the 15-minute Undo button is gone)
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        me = self.bot.user
        if message.author.bot or not message.guild or me is None:
            return
        if f"<@{me.id}>" not in message.content and f"<@!{me.id}>" not in message.content:
            return
        text = message.content.replace(f"<@{me.id}>", "").replace(f"<@!{me.id}>", "").strip().lower().strip(" !.")
        if text not in ("undo build", "undo last build"):
            return
        if not message.author.guild_permissions.administrator:
            return await message.reply("Only Administrators can undo a build.", mention_author=False)
        entry = load_data().get(str(message.guild.id))
        if not entry:
            return await message.reply("There's no build to undo.", mention_author=False)

        async def go(btn: discord.Interaction):
            await self.do_undo(btn)

        view = ConfirmView(message.author.id, go, confirm_label="Yes, delete it all", danger=True)
        view.message = await message.reply(embed=web_embed(
            "Undo last build?",
            f"This **deletes** the {len(entry['roles'])} roles and {len(entry['channels'])} channels/categories "
            "created by the last build, **including any messages in them**.", RED), view=view, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(ServerBuilder(bot))
