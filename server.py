import os, secrets, aiohttp
from urllib.parse import urlencode
from aiohttp import web
from . import modules as M

API = "https://discord.com/api/v10"
PUBLIC = os.path.join(os.path.dirname(__file__), "public")
SESSIONS: dict = {}
SEC = {s["id"]: s for s in M.SECTIONS}

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
    SECRET, REDIRECT, J = os.getenv("CLIENT_SECRET"), os.getenv("REDIRECT_URI"), web.json_response
    async def dreq(method, path, **kw):
        async with aiohttp.ClientSession() as s, s.request(method, API + path, **kw) as r:
            if r.status >= 400: raise RuntimeError(r.status)
            return await r.json()
    def deny(request, gid=None):
        if not request["s"].get("user"): return J({"error": "Not logged in"}, status=401)
        if gid and (gid not in request["s"]["guilds"] or not bot.get_guild(int(gid))):
            return J({"error": "You cannot manage this server, or Spidey is not in it"}, status=403)

    async def login(request):
        request["s"]["state"] = secrets.token_hex(16)
        return web.HTTPFound("https://discord.com/oauth2/authorize?" + urlencode({"client_id": bot.user.id, "redirect_uri": REDIRECT,
            "response_type": "code", "scope": "identify guilds", "state": request["s"]["state"]}))
    async def callback(request):
        s = request["s"]
        try:
            if not request.query.get("code") or request.query.get("state") != s.get("state"): return web.HTTPFound("/?error=state")
            tok = await dreq("POST", "/oauth2/token", data={"client_id": bot.user.id, "client_secret": SECRET, "grant_type": "authorization_code",
                                                            "code": request.query["code"], "redirect_uri": REDIRECT})
            h = {"Authorization": f"Bearer {tok['access_token']}"}
            s["user"] = await dreq("GET", "/users/@me", headers=h)
            gl = [g for g in await dreq("GET", "/users/@me/guilds", headers=h) if can_manage(g)]
            s["guild_list"], s["guilds"] = gl, [g["id"] for g in gl]
            return web.HTTPFound("/")
        except Exception: return web.HTTPFound("/?error=login")
    async def logout(request): request["s"].clear(); return web.HTTPFound("/")
    async def config(request): return J({"clientId": str(bot.user.id)})
    async def me(request): return J({"user": request["s"].get("user")})
    async def schema(request): return deny(request) or J(M.schema())
    async def guilds(request):
        return deny(request) or J([{"id": g["id"], "name": g["name"], "icon": g.get("icon"), "botIn": bot.get_guild(int(g["id"])) is not None} for g in request["s"]["guild_list"]])

    async def guild_get(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        g, data = bot.get_guild(int(gid)), {}
        for sec in M.SECTIONS:
            cog = bot.get_cog(sec["cog"])
            if cog: data[sec["id"]] = M.read(sec, cog, g)
        return J({"values": data,
            "channels": [{"id": str(c.id), "name": c.name} for c in g.text_channels],
            "categories": [{"id": str(c.id), "name": c.name} for c in g.categories],
            "roles": [{"id": str(r.id), "name": r.name} for r in g.roles if not r.is_default() and not r.managed]})

    async def section_put(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        sec = SEC.get(request.match_info["sec"]); g = bot.get_guild(int(gid))
        cog = bot.get_cog(sec["cog"]) if sec else None
        if not cog: return J({"error": "Unknown section, or its module isn't loaded"}, status=404)
        try: M.write(sec, cog, g, await request.json())
        except (ValueError, TypeError, KeyError) as ex: return J({"error": str(ex) or "Invalid value"}, status=400)
        return J({"ok": True})

    async def index(request): return web.FileResponse(os.path.join(PUBLIC, "index.html"))
    app = web.Application(middlewares=[session_mw])
    app.add_routes([web.get("/", index), web.get("/auth/login", login), web.get("/auth/callback", callback), web.get("/auth/logout", logout),
        web.get("/api/config", config), web.get("/api/me", me), web.get("/api/schema", schema), web.get("/api/guilds", guilds),
        web.get("/api/guilds/{id}", guild_get), web.put("/api/guilds/{id}/{sec}", section_put)])
    return app

async def start(bot):
    if not (os.getenv("CLIENT_SECRET") and os.getenv("REDIRECT_URI")):
        print("[dashboard] CLIENT_SECRET / REDIRECT_URI not set, dashboard not started."); return None
    runner = web.AppRunner(build(bot)); await runner.setup()
    port = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or 3000)
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"🕸️ Dashboard running on port {port}")
    return runner
