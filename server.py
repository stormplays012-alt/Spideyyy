import os, secrets, aiohttp
from urllib.parse import urlencode
from aiohttp import web
from . import settings as st

API = "https://discord.com/api/v10"
PUBLIC = os.path.join(os.path.dirname(__file__), "public")
SESSIONS: dict = {}

def can_manage(g):
    p = int(g["permissions"]); return bool(g.get("owner") or p & 0x8 or p & 0x20)

@web.middleware
async def session_mw(request, handler):
    sid = request.cookies.get("sid")
    if sid not in SESSIONS: sid = secrets.token_hex(24); SESSIONS[sid] = {}
    request["s"] = SESSIONS[sid]
    resp = await handler(request)
    resp.set_cookie("sid", sid, httponly=True, samesite="Lax", max_age=7 * 86400)
    return resp

def build(bot) -> web.Application:
    CLIENT_SECRET, REDIRECT = os.getenv("CLIENT_SECRET"), os.getenv("REDIRECT_URI")
    J = web.json_response
    async def discord_req(method, path, **kw):
        async with aiohttp.ClientSession() as s, s.request(method, API + path, **kw) as r:
            if r.status >= 400: raise RuntimeError(r.status)
            return await r.json()

    def need_login(request):
        return None if request["s"].get("user") else J({"error": "Not logged in"}, status=401)
    def guild_for(request):
        gid = request.match_info["id"]
        if gid not in request["s"].get("guilds", []) or not bot.get_guild(int(gid)): return None
        return bot.get_guild(int(gid))

    async def login(request):
        request["s"]["state"] = secrets.token_hex(16)
        q = {"client_id": bot.user.id, "redirect_uri": REDIRECT, "response_type": "code", "scope": "identify guilds", "state": request["s"]["state"]}
        return web.HTTPFound("https://discord.com/oauth2/authorize?" + urlencode(q))

    async def callback(request):
        s = request["s"]
        try:
            if not request.query.get("code") or request.query.get("state") != s.get("state"): return web.HTTPFound("/?error=state")
            tok = await discord_req("POST", "/oauth2/token", data={"client_id": bot.user.id, "client_secret": CLIENT_SECRET, "grant_type": "authorization_code", "code": request.query["code"], "redirect_uri": REDIRECT})
            h = {"Authorization": f"Bearer {tok['access_token']}"}
            s["user"] = await discord_req("GET", "/users/@me", headers=h)
            gl = [g for g in await discord_req("GET", "/users/@me/guilds", headers=h) if can_manage(g)]
            s["guild_list"], s["guilds"] = gl, [g["id"] for g in gl]
            return web.HTTPFound("/")
        except Exception:
            return web.HTTPFound("/?error=login")

    async def logout(request): request["s"].clear(); return web.HTTPFound("/")
    async def config(request): return J({"clientId": str(bot.user.id)})
    async def me(request): return J({"user": request["s"].get("user")})

    async def guilds(request):
        if (e := need_login(request)): return e
        return J([{"id": g["id"], "name": g["name"], "icon": g.get("icon"), "botIn": bot.get_guild(int(g["id"])) is not None} for g in request["s"]["guild_list"]])

    async def guild_get(request):
        if (e := need_login(request)): return e
        g = guild_for(request)
        if not g: return J({"error": "You cannot manage this server, or the bot is not in it"}, status=403)
        return J({"settings": st.get(g.id),
                  "channels": [{"id": str(c.id), "name": c.name} for c in g.text_channels],
                  "roles": [{"id": str(r.id), "name": r.name} for r in g.roles if not r.is_default() and not r.managed]})

    async def guild_put(request):
        if (e := need_login(request)): return e
        g = guild_for(request)
        if not g: return J({"error": "You cannot manage this server, or the bot is not in it"}, status=403)
        clean = st.save(g.id, await request.json())
        bot.dispatch("dashboard_change", g.id, clean)   # cogs apply it instantly
        return J({"ok": True})

    async def index(request): return web.FileResponse(os.path.join(PUBLIC, "index.html"))

    app = web.Application(middlewares=[session_mw])
    app.add_routes([web.get("/", index), web.get("/auth/login", login), web.get("/auth/callback", callback), web.get("/auth/logout", logout),
                    web.get("/api/config", config), web.get("/api/me", me), web.get("/api/guilds", guilds),
                    web.get("/api/guilds/{id}", guild_get), web.put("/api/guilds/{id}/settings", guild_put)])
    return app

async def start(bot):
    if not (os.getenv("CLIENT_SECRET") and os.getenv("REDIRECT_URI")):
        print("[dashboard] Set CLIENT_SECRET and REDIRECT_URI in your environment, dashboard not started."); return None
    runner = web.AppRunner(build(bot)); await runner.setup()
    port = int(os.getenv("DASHBOARD_PORT", 3000))
    await web.TCPSite(runner, os.getenv("DASHBOARD_HOST", "0.0.0.0"), port).start()
    print(f"[dashboard] running on port {port}")
    return runner
