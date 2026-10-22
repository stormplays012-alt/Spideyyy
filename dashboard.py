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
            config["automod"] = {"modules": dict(c["modules"]), "ignore_staff": bool(c["ignore_staff"])}
        if welcome_cog:
            c = welcome_cog.cfg(gid)
            config["welcome"] = {
                "welcome_channel": c.get("welcome_channel"),
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
        channels = [{"id": str(ch.id), "name": ch.name} for ch in guild.text_channels]
        return web.json_response({"guild": {"id": str(gid), "name": guild.name}, "config": config, "channels": channels})

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
            cog.save()
            changed.append("AutoMod")

        if "welcome" in body:
            cog = self.bot.get_cog("Welcome")
            payload = body["welcome"]
            if not cog or not isinstance(payload, dict):
                raise web.HTTPBadRequest(text="Welcome settings are unavailable.")
            c = cog.cfg(gid)
            text_fields = {"welcome_title": 200, "welcome_message": 1500, "welcome_text": 1000, "goodbye_message": 1500}
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dashboard(bot))
