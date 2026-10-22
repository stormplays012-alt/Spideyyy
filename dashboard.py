"""Spideyyy dashboard Cog. Integrates with the existing bot process."""
from __future__ import annotations

import os
import secrets
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import aiohttp
from aiohttp import web
from discord.ext import commands

DISCORD_API = "https://discord.com/api/v10"
BASE_DIR = Path(__file__).resolve().parent
HTML_FILE = BASE_DIR / "dashboard.html"

# Discord permission bits: ADMINISTRATOR=8, MANAGE_GUILD=32
MANAGE_GUILD_BITS = 8 | 32


def _int(value: Any, low: int, high: int, label: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise web.HTTPBadRequest(text=f"{label} must be a number")
    if not low <= number <= high:
        raise web.HTTPBadRequest(text=f"{label} must be between {low} and {high}")
    return number


class Dashboard(commands.Cog):
    """OAuth-protected dashboard running alongside the bot."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.client_id = os.getenv("DASHBOARD_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("DASHBOARD_CLIENT_SECRET", "").strip()
        self.redirect_uri = os.getenv("DASHBOARD_REDIRECT_URI", "").strip()
        self.sessions: dict[str, dict[str, Any]] = {}
        self.oauth_states: dict[str, float] = {}
        self.http: aiohttp.ClientSession | None = None
        self.runner: web.AppRunner | None = None
        self.app = web.Application(client_max_size=64 * 1024)
        self.app.add_routes([
            web.get("/", self.home),
            web.get("/login", self.login),
            web.get("/callback", self.callback),
            web.post("/logout", self.logout),
            web.get("/api/me", self.api_me),
            web.get("/api/guilds", self.api_guilds),
            web.get("/api/guild/{guild_id}/config", self.api_get_config),
            web.post("/api/guild/{guild_id}/config", self.api_save_config),
            web.post("/api/guild/{guild_id}/tickets", self.api_save_tickets),
            web.post("/api/guild/{guild_id}/tickets/panel", self.api_post_ticket_panel),
            web.post("/api/guild/{guild_id}/voicemaster", self.api_save_voicemaster),
        ])

    async def cog_load(self) -> None:
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15))
        host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
        port = _int(os.getenv("PORT", os.getenv("DASHBOARD_PORT", "8080")), 1, 65535, "PORT")
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, host=host, port=port)
        await site.start()
        print(f"[dashboard] Listening on {host}:{port}")
        if not (self.client_id and self.client_secret and self.redirect_uri):
            print("[dashboard] Set DASHBOARD_CLIENT_ID, DASHBOARD_CLIENT_SECRET, and DASHBOARD_REDIRECT_URI to enable login.")

    async def cog_unload(self) -> None:
        if self.runner:
            await self.runner.cleanup()
            self.runner = None
        if self.http and not self.http.closed:
            await self.http.close()

    def _oauth_ready(self) -> bool:
        return bool(self.client_id and self.client_secret and self.redirect_uri and self.http)

    def _session(self, request: web.Request) -> dict[str, Any] | None:
        sid = request.cookies.get("spideyyy_session", "")
        return self.sessions.get(sid)

    def _require_session(self, request: web.Request) -> dict[str, Any]:
        session = self._session(request)
        if not session:
            raise web.HTTPUnauthorized(text="Please log in with Discord first.")
        return session

    async def _discord_json(self, method: str, path: str, *, token: str | None = None, data=None):
        assert self.http is not None
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        async with self.http.request(method, DISCORD_API + path, headers=headers, data=data) as resp:
            payload = await resp.json(content_type=None)
            if resp.status >= 400:
                raise web.HTTPBadGateway(text="Discord API request failed.")
            return payload

    async def home(self, request: web.Request) -> web.Response:
        if not HTML_FILE.exists():
            return web.Response(text="dashboard.html is missing from the project folder.", status=500)
        return web.FileResponse(HTML_FILE, headers={"Cache-Control": "no-store"})

    async def login(self, request: web.Request) -> web.Response:
        if not self._oauth_ready():
            return web.Response(
                text="Dashboard login is not configured. Set DASHBOARD_CLIENT_ID, DASHBOARD_CLIENT_SECRET, and DASHBOARD_REDIRECT_URI in your host environment.",
                status=503,
            )
        now = time.time()
        self.oauth_states = {key: stamp for key, stamp in self.oauth_states.items() if now - stamp < 600}
        state = secrets.token_urlsafe(32)
        self.oauth_states[state] = now
        query = urlencode({
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
        })
        raise web.HTTPFound("https://discord.com/oauth2/authorize?" + query)

    async def callback(self, request: web.Request) -> web.Response:
        if not self._oauth_ready():
            raise web.HTTPServiceUnavailable(text="Discord OAuth is not configured.")
        state = request.query.get("state", "")
        stamp = self.oauth_states.pop(state, None)
        if not state or stamp is None or time.time() - stamp > 600:
            raise web.HTTPBadRequest(text="Invalid or expired login state. Please try again.")
        code = request.query.get("code", "")
        if not code:
            raise web.HTTPBadRequest(text="Discord login was cancelled or no code was returned.")
        assert self.http is not None
        async with self.http.post(
            DISCORD_API + "/oauth2/token",
            data={
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ) as resp:
            token_data = await resp.json(content_type=None)
            if resp.status >= 400 or not token_data.get("access_token"):
                raise web.HTTPBadGateway(text="Discord login could not be completed.")
        token = token_data["access_token"]
        user = await self._discord_json("GET", "/users/@me", token=token)
        sid = secrets.token_urlsafe(32)
        self.sessions[sid] = {"token": token, "user": user, "created": time.time()}
        response = web.HTTPFound("/")
        response.set_cookie(
            "spideyyy_session", sid, httponly=True, secure=self.redirect_uri.startswith("https://"),
            samesite="Lax", max_age=60 * 60 * 8, path="/",
        )
        raise response

    async def logout(self, request: web.Request) -> web.Response:
        sid = request.cookies.get("spideyyy_session", "")
        self.sessions.pop(sid, None)
        response = web.json_response({"ok": True})
        response.del_cookie("spideyyy_session", path="/")
        return response

    async def api_me(self, request: web.Request) -> web.Response:
        session = self._session(request)
        if not session:
            return web.json_response({"logged_in": False})
        user = session["user"]
        return web.json_response({
            "logged_in": True,
            "user": {"id": user.get("id"), "username": user.get("global_name") or user.get("username"), "avatar": user.get("avatar")},
        })

    async def _manageable_guild_ids(self, session: dict[str, Any]) -> set[int]:
        guilds = await self._discord_json("GET", "/users/@me/guilds", token=session["token"])
        ids = set()
        for guild in guilds if isinstance(guilds, list) else []:
            try:
                permissions = int(guild.get("permissions", "0"))
                gid = int(guild["id"])
            except (TypeError, ValueError, KeyError):
                continue
            if permissions & MANAGE_GUILD_BITS and self.bot.get_guild(gid):
                ids.add(gid)
        return ids

    async def _authorized_guild(self, request: web.Request):
        session = self._require_session(request)
        try:
            gid = int(request.match_info["guild_id"])
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid server ID.")
        if gid not in await self._manageable_guild_ids(session):
            raise web.HTTPForbidden(text="You need Manage Server permissions, and Spideyyy must be in that server.")
        guild = self.bot.get_guild(gid)
        if guild is None:
            raise web.HTTPNotFound(text="Spideyyy is not in that server.")
        return session, guild

    async def api_guilds(self, request: web.Request) -> web.Response:
        session = self._require_session(request)
        manageable = await self._manageable_guild_ids(session)
        guilds = []
        for gid in manageable:
            guild = self.bot.get_guild(gid)
            if guild:
                guilds.append({"id": str(gid), "name": guild.name, "icon": str(guild.icon.url) if guild.icon else None})
        guilds.sort(key=lambda item: item["name"].lower())
        return web.json_response({"guilds": guilds})

    async def api_get_config(self, request: web.Request) -> web.Response:
        _, guild = await self._authorized_guild(request)
        gid = guild.id
        automod_cog = self.bot.get_cog("AutoMod")
        welcome_cog = self.bot.get_cog("Welcome")
        leveling_cog = self.bot.get_cog("Leveling")
        config = {}
        if automod_cog:
            c = automod_cog.cfg(gid)
            config["automod"] = {
                "modules": dict(c["modules"]), "ignore_staff": bool(c["ignore_staff"]),
                "spam": dict(c.get("spam", {})), "duplicates": dict(c.get("duplicates", {})),
                "caps": dict(c.get("caps", {})), "mentions": dict(c.get("mentions", {})),
                "emojis": dict(c.get("emojis", {})), "badwords": list(c.get("badwords", [])),
                "allowed_domains": list(c.get("allowed_domains", [])),
                "ignored_channels": list(c.get("ignored_channels", [])),
                "ignored_roles": list(c.get("ignored_roles", [])),
                "punishment": dict(c.get("punishment", {})),
            }
        if welcome_cog:
            c = welcome_cog.cfg(gid)
            config["welcome"] = {
                "welcome_channel": c.get("welcome_channel"),
                "welcome_enabled": bool(c.get("welcome_enabled", True)),
                "goodbye_enabled": bool(c.get("goodbye_enabled", True)),
                "auto_roles_enabled": bool(c.get("auto_roles_enabled", True)),
                "auto_roles": list(c.get("auto_roles", [])),
                "welcome_title": c.get("welcome_title", "New Arrival!"),
                "welcome_message": c.get("welcome_message", ""),
                "welcome_text": c.get("welcome_text", ""),
                "goodbye_channel": c.get("goodbye_channel"),
                "goodbye_message": c.get("goodbye_message", ""),
            }
        if leveling_cog:
            c = leveling_cog.guild(gid)["config"]
            config["leveling"] = {
                "enabled": bool(c["enabled"]), "xp_min": int(c["xp_min"]), "xp_max": int(c["xp_max"]),
                "cooldown": int(c["cooldown"]), "voice_xp": int(c["voice_xp"]),
                "multiplier": float(c["multiplier"]), "announce": bool(c["announce"]),
            }
        ticket_cog = self.bot.get_cog("Tickets")
        if ticket_cog:
            tc = ticket_cog.cfg(gid)
            config["tickets"] = {"category": tc.get("category"), "support_roles": list(tc.get("support_roles", [])), "log_channel": tc.get("log_channel"), "max_open": int(tc.get("max_open", 1)), "counter": int(tc.get("counter", 0))}
        voice_cog = self.bot.get_cog("VoiceMaster")
        if voice_cog:
            vc = voice_cog.store.get_guild(gid) or {}
            config["voicemaster"] = {"category_id": vc.get("category_id"), "creator_id": vc.get("creator_id"), "enabled": bool(vc)}
        # Build dropdown data from Discord's live API rather than relying only on
        # the local cache. A partially populated cache can otherwise make all
        # ticket routing selects appear empty.
        try:
            fetched_channels = await guild.fetch_channels()
        except Exception:
            fetched_channels = list(guild.channels)
        try:
            fetched_roles = await guild.fetch_roles()
        except Exception:
            fetched_roles = list(guild.roles)
        discord = __import__("discord")
        channels = [{"id": str(ch.id), "name": ch.name} for ch in fetched_channels if isinstance(ch, discord.TextChannel)]
        categories = [{"id": str(ch.id), "name": ch.name} for ch in fetched_channels if isinstance(ch, discord.CategoryChannel)]
        voice_channels = [{"id": str(ch.id), "name": ch.name} for ch in fetched_channels if isinstance(ch, discord.VoiceChannel)]
        roles = [{"id": str(r.id), "name": r.name} for r in fetched_roles if not r.is_default()]
        channels.sort(key=lambda item: item["name"].lower())
        categories.sort(key=lambda item: item["name"].lower())
        roles.sort(key=lambda item: item["name"].lower())
        return web.json_response({"guild": {"id": str(gid), "name": guild.name}, "config": config, "channels": channels, "categories": categories, "voice_channels": voice_channels, "roles": roles})

    async def api_save_config(self, request: web.Request) -> web.Response:
        _, guild = await self._authorized_guild(request)
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Expected JSON.")
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Expected a JSON object.")
        gid = guild.id
        changed = []

        if "automod" in body:
            cog = self.bot.get_cog("AutoMod")
            payload = body["automod"]
            if not cog or not isinstance(payload, dict):
                raise web.HTTPBadRequest(text="AutoMod settings are unavailable.")
            c = cog.cfg(gid)
            modules = payload.get("modules", {})
            if not isinstance(modules, dict):
                raise web.HTTPBadRequest(text="AutoMod modules must be an object.")
            for key in c["modules"]:
                if key in modules:
                    if not isinstance(modules[key], bool):
                        raise web.HTTPBadRequest(text=f"AutoMod {key} must be true or false.")
                    c["modules"][key] = modules[key]
            if "ignore_staff" in payload:
                if not isinstance(payload["ignore_staff"], bool):
                    raise web.HTTPBadRequest(text="ignore_staff must be true or false.")
                c["ignore_staff"] = payload["ignore_staff"]
            numeric = {
                "spam": {"messages": (2, 20), "seconds": (1, 60)},
                "duplicates": {"count": (2, 10), "seconds": (5, 300)},
                "caps": {"percent": (30, 100), "min_length": (5, 100)},
                "mentions": {"limit": (2, 30)}, "emojis": {"limit": (3, 50)},
            }
            for section, fields in numeric.items():
                incoming = payload.get(section, {})
                if incoming is not None and not isinstance(incoming, dict):
                    raise web.HTTPBadRequest(text=f"{section} must be an object.")
                for key, bounds in fields.items():
                    if key in incoming:
                        c.setdefault(section, {})[key] = _int(incoming[key], bounds[0], bounds[1], f"{section}.{key}")
            for key, limit in (("badwords", 100), ("allowed_domains", 100)):
                if key in payload:
                    values = payload[key]
                    if not isinstance(values, list) or len(values) > limit or any(not isinstance(x, str) for x in values):
                        raise web.HTTPBadRequest(text=f"{key} must be a list of up to {limit} text items.")
                    cleaned = []
                    for item in values:
                        item = item.strip().lower()
                        if not item: continue
                        if len(item) > 100 or any(ch.isspace() for ch in item):
                            raise web.HTTPBadRequest(text=f"Invalid {key} item: {item[:40]}")
                        cleaned.append(item)
                    c[key] = list(dict.fromkeys(cleaned))
            for key, kind in (("ignored_channels", "channel"), ("ignored_roles", "role")):
                if key in payload:
                    values = payload[key]
                    if not isinstance(values, list) or len(values) > 100:
                        raise web.HTTPBadRequest(text=f"{key} must be a list of IDs.")
                    ids = []
                    for raw in values:
                        ident = _int(raw, 1, 2**63 - 1, key)
                        obj = guild.get_channel(ident) if kind == "channel" else guild.get_role(ident)
                        if kind == "channel" and not isinstance(obj, __import__("discord").TextChannel):
                            raise web.HTTPBadRequest(text=f"{ident} is not a text channel in this server.")
                        if obj is None:
                            raise web.HTTPBadRequest(text=f"Invalid ignored {kind} ID: {ident}")
                        ids.append(ident)
                    c[key] = list(dict.fromkeys(ids))
            if "punishment" in payload:
                incoming = payload["punishment"]
                if not isinstance(incoming, dict):
                    raise web.HTTPBadRequest(text="punishment must be an object.")
                punish = c.setdefault("punishment", {})
                if "action" in incoming:
                    if incoming["action"] not in ("none", "timeout", "kick", "ban"):
                        raise web.HTTPBadRequest(text="Punishment action must be none, timeout, kick, or ban.")
                    punish["action"] = incoming["action"]
                for key, lo, hi in (("strikes",1,10),("window",30,86400),("duration",60,2419200)):
                    if key in incoming: punish[key] = _int(incoming[key], lo, hi, f"punishment.{key}")
            cog.save()
            changed.append("AutoMod")

        if "welcome" in body:
            cog = self.bot.get_cog("Welcome")
            payload = body["welcome"]
            if not cog or not isinstance(payload, dict):
                raise web.HTTPBadRequest(text="Welcome settings are unavailable.")
            c = cog.cfg(gid)
            text_fields = {"welcome_title": 200, "welcome_message": 1500, "welcome_text": 1000, "goodbye_message": 1500}
            for key in ("welcome_enabled", "goodbye_enabled", "auto_roles_enabled"):
                if key in payload:
                    if not isinstance(payload[key], bool):
                        raise web.HTTPBadRequest(text=f"{key} must be true or false.")
                    c[key] = payload[key]
            if "auto_roles" in payload:
                values = payload["auto_roles"]
                if not isinstance(values, list) or len(values) > 25:
                    raise web.HTTPBadRequest(text="Choose up to 25 join roles.")
                roles = []
                for raw in values:
                    role_id = _int(raw, 1, 2**63 - 1, "auto_roles")
                    role = guild.get_role(role_id)
                    if role is None or role.is_default() or role.managed or role >= guild.me.top_role:
                        raise web.HTTPBadRequest(text=f"Spideyyy cannot assign role {role_id}. Check role hierarchy and managed roles.")
                    roles.append(role_id)
                c["auto_roles"] = list(dict.fromkeys(roles))
            for key, max_len in text_fields.items():
                if key in payload:
                    value = payload[key]
                    if not isinstance(value, str) or len(value) > max_len:
                        raise web.HTTPBadRequest(text=f"{key} must be text of at most {max_len} characters.")
                    c[key] = value
            for key in ("welcome_channel", "goodbye_channel"):
                if key in payload:
                    raw = payload[key]
                    if raw in (None, "", "0"):
                        c[key] = None
                    else:
                        channel_id = _int(raw, 1, 2**63 - 1, key)
                        if not isinstance(guild.get_channel(channel_id), __import__("discord").TextChannel):
                            raise web.HTTPBadRequest(text=f"{key} must be a text channel in this server.")
                        c[key] = channel_id
            cog.save()
            changed.append("Welcome")

        if "leveling" in body:
            cog = self.bot.get_cog("Leveling")
            payload = body["leveling"]
            if not cog or not isinstance(payload, dict):
                raise web.HTTPBadRequest(text="Leveling settings are unavailable.")
            c = cog.guild(gid)["config"]
            for key in ("enabled", "announce"):
                if key in payload:
                    if not isinstance(payload[key], bool):
                        raise web.HTTPBadRequest(text=f"{key} must be true or false.")
                    c[key] = payload[key]
            if "xp_min" in payload:
                c["xp_min"] = _int(payload["xp_min"], 1, 500, "xp_min")
            if "xp_max" in payload:
                c["xp_max"] = _int(payload["xp_max"], 1, 500, "xp_max")
            if c["xp_min"] > c["xp_max"]:
                raise web.HTTPBadRequest(text="Minimum XP cannot exceed maximum XP.")
            if "cooldown" in payload:
                c["cooldown"] = _int(payload["cooldown"], 0, 600, "cooldown")
            if "voice_xp" in payload:
                c["voice_xp"] = _int(payload["voice_xp"], 0, 200, "voice_xp")
            if "multiplier" in payload:
                try:
                    multiplier = float(payload["multiplier"])
                except (TypeError, ValueError):
                    raise web.HTTPBadRequest(text="multiplier must be a number.")
                if not 0.1 <= multiplier <= 10:
                    raise web.HTTPBadRequest(text="multiplier must be between 0.1 and 10.")
                c["multiplier"] = round(multiplier, 2)
            cog.dirty = True
            cog.save()
            changed.append("Leveling")

        if not changed:
            raise web.HTTPBadRequest(text="No supported settings were provided.")
        return web.json_response({"ok": True, "saved": changed})


    async def api_save_tickets(self, request: web.Request) -> web.Response:
        _, guild = await self._authorized_guild(request)
        cog = self.bot.get_cog("Tickets")
        if not cog:
            raise web.HTTPServiceUnavailable(text="Tickets module is not loaded.")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Expected JSON.")
        category_raw = body.get("category")
        role_values = body.get("support_roles")
        if role_values is None:
            role_values = [body.get("support_role")] if body.get("support_role") else []
        if not isinstance(role_values, list) or not 1 <= len(role_values) <= 25:
            raise web.HTTPBadRequest(text="Select between 1 and 25 support roles.")
        roles = []
        for raw_role in role_values:
            try:
                role = guild.get_role(int(raw_role))
            except (TypeError, ValueError):
                role = None
            if role is None:
                raise web.HTTPBadRequest(text="One of the selected support roles is invalid.")
            if role not in roles:
                roles.append(role)
        log_raw = body.get("log_channel")
        category = guild.get_channel(int(category_raw)) if category_raw else None
        if not isinstance(category, __import__("discord").CategoryChannel):
            raise web.HTTPBadRequest(text="Choose a valid ticket category.")
        log_channel = guild.get_channel(int(log_raw)) if log_raw else None
        if log_raw and not isinstance(log_channel, __import__("discord").TextChannel):
            raise web.HTTPBadRequest(text="Choose a valid transcript text channel.")
        max_open = _int(body.get("max_open", 1), 1, 5, "max_open")
        cfg = cog.cfg(guild.id)
        cfg["category"] = category.id
        cfg["support_roles"] = [role.id for role in roles]
        cfg["log_channel"] = log_channel.id if log_channel else None
        cfg["max_open"] = max_open
        cog.save()
        return web.json_response({"ok": True, "message": "Ticket settings saved."})

    async def api_post_ticket_panel(self, request: web.Request) -> web.Response:
        _, guild = await self._authorized_guild(request)
        cog = self.bot.get_cog("Tickets")
        if not cog:
            raise web.HTTPServiceUnavailable(text="Tickets module is not loaded.")
        try:
            body = await request.json()
            channel_id = _int(body.get("channel_id", 0), 1, 2**63 - 1, "channel_id")
        except Exception:
            raise web.HTTPBadRequest(text="Choose a text channel.")
        discord = __import__("discord")
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise web.HTTPBadRequest(text="Choose a valid text channel.")
        if not channel.permissions_for(guild.me).send_messages or not channel.permissions_for(guild.me).embed_links:
            raise web.HTTPForbidden(text="Spideyyy needs Send Messages and Embed Links in that channel.")
        cfg = cog.cfg(guild.id)
        category_raw = body.get("category") or cfg.get("category")
        category = guild.get_channel(int(category_raw)) if category_raw else None
        if not isinstance(category, discord.CategoryChannel):
            raise web.HTTPBadRequest(text="Choose a valid ticket category in the global settings or panel settings.")
        title = body.get("title", "Need Help?")
        description = body.get("description", "Press the button below to open a private ticket with our staff. 🕷️")
        button_label = body.get("button_label", "Open Ticket")
        button_emoji = body.get("button_emoji", "🎫")
        button_style = body.get("button_style", "primary")
        raw_buttons = body.get("buttons")
        if raw_buttons is None:
            raw_buttons = [{"label": button_label, "emoji": button_emoji, "style": button_style}]
        if not isinstance(raw_buttons, list) or not 1 <= len(raw_buttons) <= 25:
            raise web.HTTPBadRequest(text="Add between 1 and 25 buttons. Discord allows at most 5 buttons per row and 5 rows per message.")
        buttons = []
        for i, item in enumerate(raw_buttons):
            if not isinstance(item, dict):
                raise web.HTTPBadRequest(text=f"Button {i+1} is invalid.")
            label = str(item.get("label", "")).strip()
            emoji = str(item.get("emoji", ""))[:32]
            style = str(item.get("style", "primary"))
            if not label or len(label) > 80:
                raise web.HTTPBadRequest(text=f"Button {i+1} needs a label between 1 and 80 characters.")
            if style not in {"primary", "secondary", "success", "danger"}:
                raise web.HTTPBadRequest(text=f"Button {i+1} has an unsupported style.")
            ticket_message_item = item.get("ticket_message", "")
            if not isinstance(ticket_message_item, str) or len(ticket_message_item) > 4000:
                raise web.HTTPBadRequest(text=f"Button {i+1}: first ticket message must be at most 4000 characters.")
            button_form_enabled = item.get("form_enabled", body.get("form_enabled", True))
            if not isinstance(button_form_enabled, bool):
                raise web.HTTPBadRequest(text=f"Button {i+1}: form toggle must be on or off.")
            button_questions = item.get("questions", [])
            if not isinstance(button_questions, list) or len(button_questions) > 5:
                raise web.HTTPBadRequest(text=f"Button {i+1}: forms support up to five questions.")
            cleaned_questions = []
            for qi, question in enumerate(button_questions):
                if isinstance(question, str):
                    question = {"label": question}
                if not isinstance(question, dict):
                    raise web.HTTPBadRequest(text=f"Button {i+1}: invalid form question.")
                qlabel = str(question.get("label", "")).strip()
                if not qlabel or len(qlabel) > 45:
                    raise web.HTTPBadRequest(text=f"Button {i+1}: each form question label must be 1–45 characters.")
                cleaned_questions.append({"label": qlabel, "style": question.get("style", "paragraph" if any(w in qlabel.lower() for w in ("detail", "explain", "reason")) else "short"), "required": bool(question.get("required", "optional" not in qlabel.lower())), "max_length": min(4000, max(1, int(question.get("max_length", 1000 if "paragraph" in str(question.get("style", "")) else 100))))})
            buttons.append({"label": label, "emoji": emoji, "style": style, "ticket_message": ticket_message_item, "form_enabled": button_form_enabled, "form_title": str(item.get("form_title", body.get("form_title", "Open a ticket 🎫")) )[:45], "questions": cleaned_questions})
        form_enabled = bool(body.get("form_enabled", True))
        color_raw = str(body.get("color", "#E53935")).strip().lstrip("#")
        footer = body.get("footer", "Spideyyy Support")
        author = body.get("author", "")
        thumbnail = body.get("thumbnail", "")
        ticket_message = body.get("ticket_message", "A staff member will be with you soon. 🕷️")
        form_title = body.get("form_title", "Open a ticket 🎫")
        question_lines = body.get("questions", "What do you need help with?\nDetails (optional)")
        if not isinstance(title, str) or not title.strip() or len(title) > 256:
            raise web.HTTPBadRequest(text="Panel title must be 1–256 characters.")
        if not isinstance(description, str) or not description.strip() or len(description) > 4096:
            raise web.HTTPBadRequest(text="Panel description must be 1–4096 characters.")
        if len(color_raw) != 6 or any(c not in "0123456789abcdefABCDEF" for c in color_raw):
            raise web.HTTPBadRequest(text="Embed colour must be a six-digit hex colour, such as #E53935.")
        for name, value, limit in (("Footer", footer, 2048), ("Author", author, 256), ("Ticket message", ticket_message, 4000), ("Form title", form_title, 45)):
            if not isinstance(value, str) or len(value) > limit:
                raise web.HTTPBadRequest(text=f"{name} exceeds Discord's {limit}-character limit.")
        if thumbnail and (not isinstance(thumbnail, str) or len(thumbnail) > 2048 or not thumbnail.startswith(("https://", "http://"))):
            raise web.HTTPBadRequest(text="Thumbnail must be an HTTP(S) image URL under 2048 characters.")
        embed_text_total = len(title) + len(description) + len(footer) + len(author)
        if embed_text_total > 6000:
            raise web.HTTPBadRequest(text="Combined panel embed text exceeds Discord's 6000-character total limit.")
        if not isinstance(question_lines, str):
            raise web.HTTPBadRequest(text="Questions must be entered one per line.")
        questions_text = [line.strip() for line in question_lines.splitlines() if line.strip()]
        if form_enabled and (not questions_text or len(questions_text) > 5):
            raise web.HTTPBadRequest(text="When the form is enabled, enter between 1 and 5 questions.")
        if any(len(q) > 45 for q in questions_text):
            raise web.HTTPBadRequest(text="Each form question label must be 45 characters or fewer.")
        role_values = body.get("support_roles")
        if role_values is None:
            role_values = [body.get("support_role")] if body.get("support_role") else []
        support_roles = []
        if role_values:
            if not isinstance(role_values, list) or len(role_values) > 25:
                raise web.HTTPBadRequest(text="Select up to 25 support roles.")
            for raw_role in role_values:
                try:
                    role = guild.get_role(int(raw_role))
                except (TypeError, ValueError):
                    role = None
                if role is None:
                    raise web.HTTPBadRequest(text="One of the selected support roles is invalid.")
                if role.id not in support_roles:
                    support_roles.append(role.id)
        else:
            support_roles = list(cfg.get("support_roles", []))
        if not support_roles:
            raise web.HTTPBadRequest(text="Select at least one support role for this panel.")
        log_raw = body.get("log_channel")
        log_channel = guild.get_channel(int(log_raw)) if log_raw else None
        if log_raw and not isinstance(log_channel, discord.TextChannel):
            raise web.HTTPBadRequest(text="Choose a valid transcript log text channel.")
        max_open = _int(body.get("max_open", cfg.get("max_open", 1)), 1, 5, "max_open")
        default_questions = [{"label": q, "style": "paragraph" if len(q) > 32 or any(w in q.lower() for w in ("details", "explain", "reason")) else "short", "required": "optional" not in q.lower(), "max_length": 1000 if len(q) > 32 else 100} for q in questions_text]
        for button in buttons:
            if button.get("form_enabled") and not button.get("questions"):
                button["questions"] = default_questions
            if not button.get("ticket_message"):
                button["ticket_message"] = ticket_message
            if not button.get("form_title"):
                button["form_title"] = form_title or "Open a ticket"
        panel_id = secrets.token_hex(4)
        panel_cfg = {
            "panel_id": panel_id, "title": title.strip(), "description": description.strip(),
            "button_label": buttons[0]["label"], "button_emoji": buttons[0]["emoji"],
            "button_style": buttons[0]["style"], "buttons": buttons,
            "form_enabled": form_enabled, "color": int(color_raw, 16), "footer": footer,
            "author": author, "thumbnail": thumbnail, "ticket_message": ticket_message,
            "form_title": form_title or "Open a ticket", "questions": [
                {"label": q, "style": "paragraph" if len(q) > 32 or "details" in q.lower() or "explain" in q.lower() else "short",
                 "required": "optional" not in q.lower(), "max_length": 1000 if len(q) > 32 else 100}
                for q in questions_text
            ] if form_enabled else [], "category": category.id, "support_roles": support_roles,
            "log_channel": log_channel.id if log_channel else cfg.get("log_channel"), "max_open": max_open,
            "post_channel": channel.id,
        }
        embed = discord.Embed(title=panel_cfg["title"], description=panel_cfg["description"], color=panel_cfg["color"])
        if author.strip():
            embed.set_author(name=author.strip())
        if footer.strip():
            embed.set_footer(text=footer.strip())
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        from tickets import TicketPanel
        cfg.setdefault("panels", {})[panel_id] = panel_cfg
        cog.save()
        try:
            await channel.send(embed=embed, view=TicketPanel(button_label=panel_cfg["button_label"], panel_id=panel_id, config=panel_cfg))
        except Exception:
            cfg["panels"].pop(panel_id, None)
            cog.save()
            raise
        form_note = f"form with {len(questions_text)} question(s)" if form_enabled else "direct-open buttons (no form)"
        return web.json_response({"ok": True, "message": f"Ticket Studio panel posted in #{channel.name} with {len(buttons)} button(s) and {form_note}.", "panel_id": panel_id})

    async def api_save_voicemaster(self, request: web.Request) -> web.Response:
        _, guild = await self._authorized_guild(request)
        cog = self.bot.get_cog("VoiceMaster")
        if not cog:
            raise web.HTTPServiceUnavailable(text="VoiceMaster module is not loaded.")
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text="Expected JSON.")
        discord = __import__("discord")
        action = body.get("action", "save")
        if action == "disable":
            old = cog.store.get_guild(guild.id)
            if old:
                ch = guild.get_channel(old.get("creator_id"))
                if ch:
                    try:
                        await ch.delete(reason="VoiceMaster disabled from dashboard")
                    except discord.HTTPException:
                        pass
                cog.store.remove_guild(guild.id)
            return web.json_response({"ok": True, "message": "VoiceMaster disabled."})
        try:
            category_id = int(body.get("category_id", 0))
        except (TypeError, ValueError):
            raise web.HTTPBadRequest(text="Choose a category.")
        category = guild.get_channel(category_id)
        if not isinstance(category, discord.CategoryChannel):
            raise web.HTTPBadRequest(text="Choose a valid category.")
        old = cog.store.get_guild(guild.id)
        old_creator = guild.get_channel(old.get("creator_id")) if old else None
        if old_creator and isinstance(old_creator, discord.VoiceChannel) and old_creator.category_id == category.id:
            return web.json_response({"ok": True, "message": "VoiceMaster is already configured in this category."})
        try:
            creator = await guild.create_voice_channel("🕸️ Join to Create", category=category, user_limit=1, reason="VoiceMaster configured from dashboard")
        except discord.Forbidden:
            raise web.HTTPForbidden(text="Spideyyy needs Manage Channels permission in that category.")
        cog.store.set_guild(guild.id, category.id, creator.id)
        return web.json_response({"ok": True, "message": f"VoiceMaster configured. Join-to-create channel: {creator.name}."})


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dashboard(bot))
