"""Spidey web dashboard (single file). Put this next to bot.py, then in bot.py: await self.load_extension("dashboard")
Env needed: CLIENT_SECRET, REDIRECT_URI (optional DASHBOARD_PORT). Edits your cogs' live settings."""
import os, sys, secrets, aiohttp
from urllib.parse import urlencode
from aiohttp import web
from discord.ext import commands

# ================= settings <-> your cogs =================
from automod import MODULES   # your automod.py

def F(key, label, type, hint="", path=None, group="", **kw):
    return dict(key=key, label=label, type=type, hint=hint, group=group, path=tuple(path or (key,)), **kw)

SECTIONS = [
 dict(id="automod", title="AutoMod", cog="AutoMod",
      container=lambda cog, g: cog.cfg(g.id), save=lambda cog, g: cog.save(),
      fields=[F("m_" + k, v, "bool", path=("modules", k), group="Rules") for k, v in MODULES.items()] + [
        F("spam_messages", "Spam: messages allowed", "number", path=("spam", "messages"), min=2, max=20, group="Limits"),
        F("spam_seconds", "Spam: window (seconds)", "number", path=("spam", "seconds"), min=1, max=60, group="Limits"),
        F("dup_count", "Duplicates: same message count", "number", path=("duplicates", "count"), min=2, max=10, group="Limits"),
        F("dup_seconds", "Duplicates: window (seconds)", "number", path=("duplicates", "seconds"), min=5, max=300, group="Limits"),
        F("caps_percent", "Caps: % that triggers", "number", path=("caps", "percent"), min=30, max=100, group="Limits"),
        F("caps_min", "Caps: minimum letters", "number", path=("caps", "min_length"), min=5, max=100, group="Limits"),
        F("mention_limit", "Max mentions per message", "number", path=("mentions", "limit"), min=2, max=30, group="Limits"),
        F("emoji_limit", "Max emojis per message", "number", path=("emojis", "limit"), min=3, max=50, group="Limits"),
        F("badwords", "Bad words", "lines", "One per line. Use * as a wildcard", path=("badwords",), group="Lists"),
        F("allowed_domains", "Allowed link domains", "lines", "One per line, like youtube.com", path=("allowed_domains",), group="Lists"),
        F("ignored_channels", "Ignored channels", "multichannel", path=("ignored_channels",), group="Exceptions"),
        F("ignored_roles", "Ignored roles", "multirole", path=("ignored_roles",), group="Exceptions"),
        F("ignore_staff", "Ignore staff (Admin / Manage Messages)", "bool", path=("ignore_staff",), group="Exceptions"),
        F("pun_action", "Punishment", "select", "After enough strikes", path=("punishment", "action"), group="Punishment",
          options=[["none", "None (delete + warn)"], ["timeout", "Timeout"], ["kick", "Kick"], ["ban", "Ban"]]),
        F("pun_strikes", "Strikes before punishment", "number", path=("punishment", "strikes"), min=1, max=20, group="Punishment"),
        F("pun_window", "Strike window (seconds)", "number", path=("punishment", "window"), min=30, max=3600, group="Punishment"),
        F("pun_duration", "Timeout length (seconds)", "number", path=("punishment", "duration"), min=60, max=2419200, group="Punishment")]),

 dict(id="welcome", title="Welcome", cog="Welcome",
      container=lambda cog, g: cog.cfg(g.id), save=lambda cog, g: cog.save(),
      fields=[
        F("welcome_enabled", "Send welcome messages", "bool", group="Join"),
        F("welcome_channel", "Welcome channel", "channel", group="Join"),
        F("welcome_text", "Text above the embed", "text", "The only part that can ping the new member", max=500, group="Join"),
        F("welcome_title", "Embed title", "text", max=200, group="Join"),
        F("welcome_message", "Embed message", "area", "{user} {name} {server} {count} {line}", max=1800, group="Join"),
        F("welcome_footer", "Footer", "text", "Blank = Spidey default, __off__ = no footer", nullable=True, max=200, group="Join"),
        F("welcome_color", "Embed color", "color", group="Look"),
        F("thumbnail_mode", "Thumbnail", "select", path=("thumbnail_mode",), group="Look",
          options=[["avatar", "Member avatar"], ["server", "Server icon"], ["custom", "Custom URL"], ["none", "None"]]),
        F("thumbnail_url", "Custom thumbnail URL", "text", "https:// link", nullable=True, https=True, group="Look"),
        F("image_url", "Big image URL", "text", "https:// link", nullable=True, https=True, group="Look"),
        F("goodbye_enabled", "Send goodbye messages", "bool", group="Leave"),
        F("goodbye_channel", "Goodbye channel", "channel", group="Leave"),
        F("goodbye_message", "Goodbye message", "area", "{name} {server} {count}", max=1800, group="Leave"),
        F("auto_roles_enabled", "Give roles on join", "bool", group="Safety"),
        F("auto_roles", "Roles given on join", "multirole", group="Safety"),
        F("verify_role", "Verified role", "role", group="Safety"),
        F("min_age_days", "Minimum account age (days)", "number", min=0, max=365, group="Safety")]),

 dict(id="leveling", title="Leveling", cog="Leveling",
      container=lambda cog, g: cog.guild(g.id)["config"], save=lambda cog, g: (_fix_xp(cog, g), cog.save()),
      fields=[
        F("enabled", "Enable leveling", "bool"),
        F("multiplier", "XP multiplier", "number", min=0.1, max=10, float=True),
        F("xp_min", "XP per message (min)", "number", min=1, max=1000),
        F("xp_max", "XP per message (max)", "number", min=1, max=1000),
        F("cooldown", "Seconds between XP messages", "number", min=0, max=3600),
        F("voice_xp", "Voice XP per minute (0 = off)", "number", min=0, max=1000),
        F("announce", "Announce level-ups", "bool"),
        F("announce_channel", "Level-up channel", "channel", "Blank = same channel as the message"),
        F("stack_rewards", "Keep old reward roles", "bool"),
        F("ignored_channels", "No-XP channels", "multichannel"),
        F("rewards", "Role rewards", "lines", "One per line: level=Role name  (example: 5=Regular)",
          get=lambda cog, g: [f"{l}={g.get_role(r).name}" for l, r in sorted(cog.guild(g.id)["rewards"].items(), key=lambda kv: int(kv[0])) if g.get_role(r)],
          set=lambda cog, g, v: _set_rewards(cog, g, v))]),

 dict(id="moderation", title="Moderation", cog="Moderation",
      container=lambda cog, g: cog.store.guild(g.id), save=lambda cog, g: cog.store.save(),
      fields=[F("log_channel", "Mod log channel", "channel", "Every mod action is logged here as a case")]),

 dict(id="tickets", title="Tickets", cog="Tickets",
      container=lambda cog, g: cog.cfg(g.id), save=lambda cog, g: cog.save(),
      fields=[
        F("category", "Ticket category", "category", "New tickets are created inside it"),
        F("support_roles", "Support roles", "multirole"),
        F("log_channel", "Ticket log channel", "channel"),
        F("max_open", "Max open tickets per member", "number", min=1, max=10)]),

 dict(id="utility", title="Suggestions", cog="Utility",
      container=lambda cog, g: {}, save=lambda cog, g: cog.store.save(),
      fields=[F("suggest_channel", "Suggestions channel", "channel", "Where /suggest posts go",
                get=lambda cog, g: cog.store.data["suggest_channel"].get(str(g.id)),
                set=lambda cog, g, v: cog.store.data["suggest_channel"].pop(str(g.id), None) if v is None else cog.store.data["suggest_channel"].__setitem__(str(g.id), v))]),
]

def _fix_xp(cog, g):
    c = cog.guild(g.id)["config"]
    if c["xp_min"] > c["xp_max"]: c["xp_min"], c["xp_max"] = c["xp_max"], c["xp_min"]

def _set_rewards(cog, g, lines):
    out = {}
    for line in lines:
        lvl, _, name = line.partition("=")
        role = next((r for r in g.roles if r.name.lower() == name.strip().lower()), None)
        if not lvl.strip().isdigit() or not role: raise ValueError(f"Bad reward line: {line}")
        out[str(int(lvl))] = role.id
    cog.guild(g.id)["rewards"] = out

# ---------- type conversion ----------
def to_ui(f, v):
    t = f["type"]
    if t in ("channel", "role", "category"): return str(v) if v else ""
    if t in ("multichannel", "multirole"): return [str(x) for x in (v or [])]
    if t == "lines": return list(v or []) if isinstance(v, list) else []
    if t == "color": return "#%06x" % (v or 0)
    if t == "bool": return bool(v)
    return "" if v is None else v

def from_ui(f, v, g):
    t = f["type"]
    if t in ("channel", "role", "category"):
        if not v: return None
        i = int(v); ok = (g.get_role(i) if t == "role" else g.get_channel(i))
        if not ok: raise ValueError(f"{f['label']}: not found in this server")
        return i
    if t in ("multichannel", "multirole"):
        ids = [int(x) for x in v]
        if any(not (g.get_role(i) if t == "multirole" else g.get_channel(i)) for i in ids): raise ValueError(f"{f['label']}: not found in this server")
        return ids
    if t == "lines": return [str(x).strip() for x in v if str(x).strip()][:200]
    if t == "color": return int(str(v).lstrip("#"), 16) if len(str(v).lstrip("#")) == 6 else 0
    if t == "bool": return bool(v)
    if t == "number":
        n = float(v) if f.get("float") else int(v)
        return max(f["min"], min(f["max"], n))
    if t == "select":
        if v not in [o[0] for o in f["options"]]: raise ValueError(f"{f['label']}: invalid choice")
        return v
    s = str(v).strip()
    if f.get("max") and len(s) > f["max"]: raise ValueError(f"{f['label']}: too long")
    if f.get("https") and s and not (s.startswith("https://") and " " not in s): raise ValueError(f"{f['label']}: must be an https:// link")
    return (s or None) if f.get("nullable") else s

def read(sec, cog, g):
    box, out = sec["container"](cog, g), {}
    for f in sec["fields"]:
        if "get" in f: v = f["get"](cog, g)
        else:
            v = box
            for p in f["path"]: v = v[p]
        out[f["key"]] = to_ui(f, v)
    return out

def write(sec, cog, g, values):
    box = sec["container"](cog, g)
    for f in sec["fields"]:
        if f["key"] not in values: continue
        v = from_ui(f, values[f["key"]], g)
        if "set" in f: f["set"](cog, g, v)
        else:
            d = box
            for p in f["path"][:-1]: d = d[p]
            d[f["path"][-1]] = v
    sec["save"](cog, g)

def schema():
    return [dict(id=s["id"], title=s["title"], fields=[{k: v for k, v in f.items() if k not in ("get", "set", "path")} for f in s["fields"]]) for s in SECTIONS]

M = sys.modules[__name__]

# ================= web server + Discord login =================

API = "https://discord.com/api/v10"
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

    def _name(g, uid):
        m = g.get_member(int(uid)); return m.display_name if m else f"User {uid}"

    async def overview(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        g = bot.get_guild(int(gid)); n = lambda u: _name(g, u)
        A, W, L, T, Mo = (bot.get_cog(x) for x in ("AutoMod", "Welcome", "Leveling", "Tickets", "Moderation"))
        out = {"stats": {"members": g.member_count, "channels": len(g.channels), "roles": len(g.roles), "latency": round(bot.latency * 1000)}, "status": []}
        if A: c = A.cfg(g.id); k = sum(1 for v in c["modules"].values() if v); out["status"].append(["AutoMod", f"{k} of {len(c['modules'])} rules on", k > 0, "automod"])
        if W: c = W.cfg(g.id); ok = bool(c.get("welcome_enabled", True) and c["welcome_channel"]); out["status"].append(["Welcome", "Welcoming new members" if ok else "Off or no channel set", ok, "welcome"])
        if L: ok = bool(L.guild(g.id)["config"]["enabled"]); out["status"].append(["Leveling", "Members earn XP" if ok else "Off", ok, "leveling"])
        if Mo: ok = bool(Mo.store.guild(g.id)["log_channel"]); out["status"].append(["Moderation log", "Logging every action" if ok else "No log channel set", ok, "moderation"])
        if T: ok = bool(T.cfg(g.id)["category"]); out["status"].append(["Tickets", "Ready" if ok else "Not set up", ok, "tickets"])
        if L:
            from leveling import level_info
            out["leaderboard"] = [{"name": n(u), "xp": d["xp"], "level": level_info(d["xp"])[0], "messages": d.get("messages", 0)} for u, d in L.ranking(g.id)[:10]]
        if Mo: out["cases"] = [{"id": c["id"], "type": c["type"], "user": n(c["user"]), "mod": n(c["mod"]), "reason": str(c["reason"])[:140], "time": c["time"]} for c in Mo.store.guild(g.id)["cases"][-25:][::-1]]
        if T:
            out["tickets"] = [{"number": x["number"], "subject": x["subject"], "opener": n(x["opener"]), "claimed": n(x["claimed_by"]) if x["claimed_by"] else "", "created": x["created"]}
                              for x in T.data["tickets"].values() if x["guild"] == g.id]
        return J(out)

    async def announce(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        g, b = bot.get_guild(int(gid)), await request.json()
        ch, msg = g.get_channel(int(b.get("channel") or 0)), str(b.get("message", "")).strip()[:3500]
        if not ch or not msg: return J({"error": "Pick a channel and write a message."}, status=400)
        from theme import web_embed
        try: await ch.send(embed=web_embed(str(b.get("title", "")).strip()[:200] or "Announcement", msg))
        except Exception: return J({"error": "I can't post in that channel. Check my permissions there."}, status=400)
        return J({"ok": True})

    async def index(request): return web.Response(text=INDEX_HTML, content_type="text/html")
    app = web.Application(middlewares=[session_mw])
    app.add_routes([web.get("/", index), web.get("/auth/login", login), web.get("/auth/callback", callback), web.get("/auth/logout", logout),
        web.get("/api/config", config), web.get("/api/me", me), web.get("/api/schema", schema), web.get("/api/guilds", guilds),
        web.get("/api/guilds/{id}", guild_get), web.get("/api/guilds/{id}/overview", overview), web.post("/api/guilds/{id}/announce", announce), web.put("/api/guilds/{id}/{sec}", section_put)])
    return app

async def start_server(bot):
    if not (os.getenv("CLIENT_SECRET") and os.getenv("REDIRECT_URI")):
        print("[dashboard] CLIENT_SECRET / REDIRECT_URI not set, dashboard not started."); return None
    runner = web.AppRunner(build(bot)); await runner.setup()
    port = int(os.getenv("DASHBOARD_PORT") or os.getenv("PORT") or 3000)
    await web.TCPSite(runner, "0.0.0.0", port).start()
    print(f"🕸️ Dashboard running on port {port}")
    return runner

# ================= the page =================
INDEX_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spidey Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#12121f;--panel:#1b1b2f;--line:#2f3050;--tx:#ececf4;--mu:#9a9cb8;--ac:#e62429;--acx:#3a1520;--blue:#2b4ea2;--bad:#ff8a8a}
@media (prefers-color-scheme:light){:root{--bg:#f2f3f8;--panel:#fff;--line:#dcdeea;--tx:#1b1b2f;--mu:#63657f;--ac:#d01a22;--acx:#fde8ea;--bad:#c02a2a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.5 Sora,system-ui,sans-serif}
button,input,select,textarea{font:inherit;color:inherit}:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
.hide{display:none!important}
.btn{border:0;border-radius:10px;padding:11px 18px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
.dc{background:#5865F2;color:#fff;padding:14px 26px}.pri{background:var(--ac);color:#fff}.ghost{background:none;border:1px solid var(--line)}
.err{color:var(--bad);margin-top:16px}
header{display:flex;align-items:center;gap:12px;padding:14px 24px;border-bottom:1px solid var(--line)}header b{flex:1}
.av{width:32px;height:32px;border-radius:50%;background:var(--line)}
.wrap{max-width:1020px;margin:0 auto;padding:28px 24px 110px}
.servers{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:14px}
.srv{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;display:flex;gap:12px;align-items:center;cursor:pointer;text-align:left;width:100%}
.srv img,.srv i{width:44px;height:44px;border-radius:12px;background:var(--line);display:grid;place-items:center;font-style:normal;font-weight:700;flex:none}
.srv small{display:block;color:var(--mu)}
.layout{display:grid;grid-template-columns:190px 1fr;gap:28px}
nav{display:flex;flex-direction:column;gap:4px;position:sticky;top:16px;align-self:start}
nav button{background:none;border:0;text-align:left;padding:10px 12px;border-radius:9px;cursor:pointer;color:var(--mu)}
nav button.on{background:var(--acx);color:var(--ac);font-weight:600}
h2{margin:0 0 16px;font-size:1.5rem;letter-spacing:-.02em}h3{margin:22px 0 0;font-size:.95rem;color:var(--mu);font-weight:600}
.sec{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:4px 20px;margin-top:8px}
.row{display:flex;justify-content:space-between;align-items:center;gap:20px;padding:15px 0;border-bottom:1px solid var(--line)}
.row:last-child{border:0}.row label{font-weight:600}.row small{display:block;color:var(--mu);font-weight:400}
.row.col{flex-direction:column;align-items:stretch;gap:8px}
input[type=text],input[type=number],select,textarea{background:var(--bg);border:1px solid var(--line);border-radius:9px;padding:9px 11px;width:100%;max-width:300px}
.col input,.col textarea{max-width:none}textarea{min-height:90px;resize:vertical}input[type=color]{width:56px;height:38px;border:1px solid var(--line);border-radius:9px;background:var(--bg);padding:3px}
.chips{display:flex;flex-wrap:wrap;gap:6px;max-height:150px;overflow:auto}
.chips label{font-weight:400;border:1px solid var(--line);border-radius:99px;padding:3px 11px;cursor:pointer;font-size:.85rem}
.chips label:has(input:checked){background:var(--acx);border-color:var(--ac);color:var(--ac)}.chips input{display:none}
.sw{position:relative;width:44px;height:25px;flex:none}.sw input{position:absolute;inset:0;opacity:0;margin:0;cursor:pointer;z-index:1}
.sw span{position:absolute;inset:0;background:var(--line);border-radius:99px;transition:.15s}
.sw span:after{content:"";position:absolute;top:3px;left:3px;width:19px;height:19px;background:#fff;border-radius:50%;transition:.15s}
.sw input:checked+span{background:var(--ac)}.sw input:checked+span:after{transform:translateX(19px)}
.sw input:focus-visible+span{outline:2px solid var(--ac);outline-offset:2px}
.bar{position:fixed;left:0;right:0;bottom:0;background:var(--panel);border-top:1px solid var(--line);padding:12px 24px;display:flex;justify-content:flex-end;align-items:center;gap:14px;transform:translateY(110%);transition:.2s}
.bar.show{transform:none}.toast{color:var(--ac)}.toast.bad{color:var(--bad)}
@media(max-width:720px){.layout{grid-template-columns:1fr}nav{flex-direction:row;overflow-x:auto;position:static}nav button{white-space:nowrap}.row{flex-wrap:wrap}}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
.login{min-height:100vh;display:grid;place-items:center;padding:24px;text-align:center;position:relative;overflow:hidden;color:#ececf4;background:radial-gradient(120% 80% at 50% 0%,#1d1d3a 0%,#0b0b16 62%)}
.login>svg{position:absolute;top:-50px;left:-70px;width:min(75vw,640px);opacity:.3;pointer-events:none}
.login>svg.r{left:auto;top:auto;right:-130px;bottom:-130px;transform:rotate(180deg)}
.login>svg path{stroke:#9aa3d6;fill:none;stroke-width:1}
.thread{position:absolute;top:0;left:50%;width:1px;height:0;background:#cfd3f0;animation:drop 1.5s cubic-bezier(.3,.7,.2,1) forwards}
.thread:after{content:"\1F577";position:absolute;bottom:-28px;left:50%;transform:translateX(-50%);font-size:26px;line-height:1}
@keyframes drop{to{height:clamp(40px,calc(50vh - 190px),380px)}}
@keyframes rise{to{opacity:1;transform:none}}
.login .in{position:relative;opacity:0;transform:translateY(14px);animation:rise .7s 1.1s forwards}
.login h1{font-size:clamp(3.4rem,13vw,7rem);font-weight:800;letter-spacing:-.055em;line-height:.9;margin:0 0 16px}
.login p{color:#a9acc9;margin:0 auto 28px;max-width:38ch}
.login small{display:block;margin-top:16px;color:#7d80a0}
.login .dc{box-shadow:0 8px 30px #5865f255;transition:transform .15s}.login .dc:hover{transform:translateY(-2px)}
@media(prefers-reduced-motion:reduce){.thread{animation:none;height:clamp(40px,calc(50vh - 190px),380px)}.login .in{animation:none;opacity:1;transform:none}}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.stat b{display:block;font-size:1.7rem;letter-spacing:-.02em}.stat small{color:var(--mu)}
.row.st>div{flex:1}.dot{width:10px;height:10px;border-radius:50%;background:var(--line);flex:none}.dot.on{background:#3ddc84}
.meter{width:min(240px,40%);height:8px;border-radius:9px;background:var(--line);overflow:hidden;flex:none}.meter i{display:block;height:100%;width:var(--w);background:var(--ac)}
.empty{padding:26px 0;color:var(--mu);text-align:center}
</style></head><body>
<section id="login" class="login hide"><div class="thread"></div><div class="in">
  <h1>Spidey</h1>
  <p>Your server's control room. Tune AutoMod, welcomes, levels and tickets here instead of typing commands.</p>
  <a class="btn dc" href="/auth/login">Log in with Discord</a>
  <small>We only read your name and the list of servers you manage.</small><div id="loginErr" class="err"></div>
</div></section>
<section id="app" class="hide">
  <header><b id="title">Spidey Dashboard</b><span id="uname"></span><img class="av" id="uav" alt=""><a class="btn ghost" href="/auth/logout">Log out</a></header>
  <div class="wrap">
    <div id="picker"><h2>Choose a server</h2><div class="servers" id="servers"></div></div>
    <div id="panel" class="hide"><button class="btn ghost" id="back" style="margin-bottom:20px">Change server</button>
      <div class="layout"><nav id="nav"></nav><div><h2 id="secTitle"></h2><div id="fields"></div></div></div></div>
  </div>
  <div class="bar" id="bar"><span class="toast" id="toast"></span><button class="btn ghost" id="reset">Discard</button><button class="btn pri" id="save">Save changes</button></div>
</section>
<script>
const $=id=>document.getElementById(id);
const api=async(u,o)=>{const r=await fetch(u,o);const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||'Request failed');return j};
let SCHEMA=[],CID='',G=null,META=null,VAL={},ORIG={},TAB='',ready=false;
const clone=o=>JSON.parse(JSON.stringify(o));
const isDirty=()=>!!ORIG[TAB]&&JSON.stringify(VAL[TAB])!==JSON.stringify(ORIG[TAB]);
const touch=()=>{$('bar').classList.toggle('show',isDirty());$('toast').textContent='';$('toast').className='toast'};
const say=(t,bad)=>{$('toast').textContent=t;$('toast').className='toast'+(bad?' bad':'')};
async function init(){
  const q=new URLSearchParams(location.search).get('error');
  const {user}=await api('/api/me');
  if(!user){$('login').classList.remove('hide');if(q)$('loginErr').textContent=q==='state'?'Login expired. Try again.':'Discord login failed. Try again.';return}
  [SCHEMA,{clientId:CID}]=await Promise.all([api('/api/schema'),api('/api/config')]);
  $('app').classList.remove('hide');$('uname').textContent=user.global_name||user.username;
  if(user.avatar)$('uav').src=`https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=64`;
  loadServers();
}
async function loadServers(){
  $('servers').textContent='Loading servers…';
  try{const gs=await api('/api/guilds');$('servers').innerHTML='';
    if(!gs.length)$('servers').textContent='You do not manage any servers yet.';
    gs.forEach(g=>{const b=document.createElement('button');b.className='srv';
      const ic=g.icon?`<img src="https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=64" alt="">`:`<i>${g.name[0]}</i>`;
      b.innerHTML=`${ic}<span><b></b><small>${g.botIn?'Open settings':'Add Spidey first'}</small></span>`;b.querySelector('b').textContent=g.name;
      b.onclick=()=>g.botIn?openGuild(g):window.open(`https://discord.com/oauth2/authorize?client_id=${CID}&scope=bot%20applications.commands&permissions=8&guild_id=${g.id}`,'_blank');
      $('servers').append(b)})}catch(e){$('servers').textContent=e.message}
}
const SPECIAL=[['overview','Overview'],['board','Leaderboard'],['cases','Mod cases'],['otickets','Open tickets'],['announce','Announce']];
let TABS=[],OV=null;
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const when=t=>new Date(t*1000).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});
async function openGuild(g){
  try{[META,OV]=await Promise.all([api('/api/guilds/'+g.id),api(`/api/guilds/${g.id}/overview`)])}catch(e){return alert(e.message)}
  G=g;VAL=clone(META.values);ORIG=clone(META.values);
  TABS=[SPECIAL[0],...SCHEMA.filter(s=>VAL[s.id]).map(s=>[s.id,s.title]),...SPECIAL.slice(1)];TAB='overview';
  $('picker').classList.add('hide');$('panel').classList.remove('hide');$('title').textContent=g.name;
  $('nav').innerHTML='';TABS.forEach(([k,t])=>{const b=el('button','',t);b.dataset.k=k;b.onclick=()=>switchTab(k);$('nav').append(b)});
  render();
}
function renderSpecial(k){
  const f=$('fields');f.innerHTML='';$('secTitle').textContent=TABS.find(x=>x[0]===k)[1];
  const list=(rows,empty)=>{if(!rows.length){f.append(el('div','empty',empty));return}const box=el('div','sec');rows.forEach(r=>box.append(r));f.append(box)};
  if(k==='overview'){
    const st=el('div','stats');[['Members',OV.stats.members],['Channels',OV.stats.channels],['Roles',OV.stats.roles],['Bot ping',OV.stats.latency+' ms']].forEach(([a,b])=>{const c=el('div','stat');c.append(el('b','',b),el('small','',a));st.append(c)});f.append(st);
    f.append(el('h3','','What is switched on'));
    list(OV.status.map(([n,txt,on,tab])=>{const r=el('div','row st'),d=el('div'),b=el('button','btn ghost','Configure');d.append(el('label','',n),el('small','',txt));b.onclick=()=>switchTab(tab);r.append(el('span','dot'+(on?' on':'')),d,b);return r}),'');
  }else if(k==='board'){
    const L=OV.leaderboard||[],top=(L[0]&&L[0].xp)||1;
    list(L.map((u,i)=>{const r=el('div','row'),d=el('div'),m=el('div','meter');d.append(el('label','',`${i+1}. ${u.name}`),el('small','',`Level ${u.level}, ${u.xp} XP, ${u.messages} messages`));m.append(el('i'));m.firstChild.style.setProperty('--w',(u.xp/top*100)+'%');m.style.setProperty('--w','0');r.append(d,m);return r}),'Nobody has earned XP yet. Turn on Leveling and start chatting.');
  }else if(k==='cases'){
    list((OV.cases||[]).map(c=>{const r=el('div','row'),d=el('div');d.append(el('label','',`Case #${c.id}: ${c.type} ${c.user}`),el('small','',`By ${c.mod} on ${when(c.time)}. ${c.reason}`));r.append(d);return r}),'No moderation cases yet.');
  }else if(k==='otickets'){
    list((OV.tickets||[]).map(t=>{const r=el('div','row'),d=el('div');d.append(el('label','',`#${t.number} ${t.subject}`),el('small','',`Opened by ${t.opener} on ${when(t.created)}. ${t.claimed?'Claimed by '+t.claimed:'Not claimed yet'}`));r.append(d);return r}),'No open tickets.');
  }else if(k==='announce'){
    const box=el('div','sec'),mk=(l,h,c)=>{const r=el('div','row col'),d=el('div');d.append(el('label','',l),el('small','',h));r.append(d,c);box.append(r);return c};
    const ch=el('select');META.channels.forEach(o=>ch.add(new Option('#'+o.name,o.id)));mk('Channel','Where Spidey posts it',ch);
    const ti=el('input');ti.type='text';ti.maxLength=200;ti.placeholder='Announcement';mk('Title','',ti);
    const ms=el('textarea');ms.maxLength=3500;mk('Message','Posted as a Spidey-styled embed',ms);
    const go=el('button','btn pri','Send message');go.style.margin='14px 0';box.append(go);f.append(box);const out=el('div','toast');out.style.marginTop='12px';f.append(out);
    go.onclick=async()=>{try{await api(`/api/guilds/${G.id}/announce`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:ch.value,title:ti.value,message:ms.value})});out.className='toast';out.textContent='Sent.';ms.value=''}catch(e){out.className='toast bad';out.textContent=e.message}};
  }
}
function switchTab(k){if(isDirty()&&!confirm('Discard unsaved changes in this section?'))return;if(ORIG[TAB])VAL[TAB]=clone(ORIG[TAB]);TAB=k;render();touch();scrollTo(0,0)}
function field(sec,f){
  const row=document.createElement('div');row.className='row'+(f.type==='area'||f.type==='lines'||f.type.startsWith('multi')?' col':'');
  row.innerHTML='<div><label></label><small></small></div>';row.querySelector('label').textContent=f.label;row.querySelector('small').textContent=f.hint||'';
  const v=VAL[sec][f.key],set=x=>{VAL[sec][f.key]=x;touch()};let el;
  if(f.type==='bool'){const w=document.createElement('div');w.className='sw';w.innerHTML='<input type="checkbox" role="switch"><span></span>';el=w.firstChild;el.checked=v;el.onchange=()=>set(el.checked);row.append(w)}
  else if(f.type.startsWith('multi')){const list=f.type==='multirole'?META.roles:META.channels;const box=document.createElement('div');box.className='chips';
    list.forEach(o=>{const l=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.checked=v.includes(o.id);
      c.onchange=()=>set(list.filter(x=>[...box.querySelectorAll('input')].some((i,n)=>i.checked&&list[n].id===x.id)).map(x=>x.id));
      l.append(c,(f.type==='multirole'?'':'#')+o.name);box.append(l)});row.append(box)}
  else if(['channel','role','category','select'].includes(f.type)){el=document.createElement('select');
    const list=f.type==='select'?f.options:[['','None'],...(f.type==='role'?META.roles:f.type==='category'?META.categories:META.channels).map(o=>[o.id,(f.type==='channel'?'#':'')+o.name])];
    list.forEach(([a,b])=>el.add(new Option(b,a)));el.value=v;el.onchange=()=>set(el.value);row.append(el)}
  else if(f.type==='lines'||f.type==='area'){el=document.createElement('textarea');el.value=f.type==='lines'?v.join('\n'):v;
    el.oninput=()=>set(f.type==='lines'?el.value.split('\n'):el.value);row.append(el)}
  else if(f.type==='color'){el=document.createElement('input');el.type='color';el.value=v;el.oninput=()=>set(el.value);row.append(el)}
  else{el=document.createElement('input');el.type=f.type==='number'?'number':'text';el.value=v;
    if(f.type==='number'){el.min=f.min;el.max=f.max;el.step=f.float?'0.1':'1'}
    el.oninput=()=>set(f.type==='number'?(el.value===''?f.min:Number(el.value)):el.value);row.append(el)}
  if(el)el.id='f_'+f.key;return row;
}
function render(){
  document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.k===TAB));
  if(!SCHEMA.some(x=>x.id===TAB))return renderSpecial(TAB);
  const s=SCHEMA.find(x=>x.id===TAB);$('secTitle').textContent=s.title;$('fields').innerHTML='';
  let grp=null,box=null;
  s.fields.forEach(f=>{if(box===null||f.group!==grp){grp=f.group;if(grp){const h=document.createElement('h3');h.textContent=grp;$('fields').append(h)}box=document.createElement('div');box.className='sec';$('fields').append(box)}
    box.append(field(TAB,f))});
}
$('save').onclick=async()=>{try{await api(`/api/guilds/${G.id}/${TAB}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(VAL[TAB])});
  ORIG[TAB]=clone(VAL[TAB]);say('Saved. Spidey is using it now.');api(`/api/guilds/${G.id}/overview`).then(o=>OV=o).catch(()=>{});setTimeout(()=>{if(!isDirty())$('bar').classList.remove('show')},1500)}catch(e){say(e.message,true)}};
$('reset').onclick=()=>{VAL[TAB]=clone(ORIG[TAB]);render();touch()};
$('back').onclick=()=>{$('panel').classList.add('hide');$('picker').classList.remove('hide');$('title').textContent='Spidey Dashboard';$('bar').classList.remove('show')};
function webSvg(c){const n='http://www.w3.org/2000/svg',s=document.createElementNS(n,'svg');s.setAttribute('viewBox','0 0 400 400');if(c)s.setAttribute('class',c);
  const A=[0,18,36,54,72,90].map(a=>a*Math.PI/180);let d='';A.forEach(a=>d+=`M0 0L${400*Math.cos(a)} ${400*Math.sin(a)}`);
  for(let r=60;r<=400;r+=65){d+=`M${r} 0`;A.slice(1).forEach((a,i)=>{const m=(a+A[i])/2,q=r*.9;d+=`Q${q*Math.cos(m)} ${q*Math.sin(m)} ${r*Math.cos(a)} ${r*Math.sin(a)}`})}
  const p=document.createElementNS(n,'path');p.setAttribute('d',d);s.append(p);return s}
$('login').prepend(webSvg(),webSvg('r'));
init();
</script></body></html>
'''

# ================= bot extension =================

class Dashboard(commands.Cog):
    def __init__(self, bot): self.bot, self.runner = bot, None
    async def cog_load(self): self.runner = await start_server(self.bot)
    async def cog_unload(self):
        if self.runner: await self.runner.cleanup()

async def setup(bot): await bot.add_cog(Dashboard(bot))
