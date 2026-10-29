"""Spidey web dashboard (single file). Put this next to bot.py, then in bot.py: await self.load_extension("dashboard")
Env needed: CLIENT_SECRET, REDIRECT_URI (optional DASHBOARD_PORT). Edits your cogs' live settings."""
import os, sys, json, time, hashlib, secrets, base64, aiohttp, discord
from urllib.parse import urlencode
from aiohttp import web
from discord.ext import commands, tasks
from discord import app_commands
from typing import Optional
import random

_TF = "dashboard_data.json"   # message templates are stored here (next to your other .json files)
def _tload():
    try:
        with open(_TF, encoding="utf8") as f: return json.load(f)
    except (FileNotFoundError, ValueError): return {}
def _tsave(d):
    with open(_TF, "w", encoding="utf8") as f: json.dump(d, f, indent=1)

# ================= settings <-> your cogs =================
from theme import safe_dm
from automod import MODULES   # your automod.py

def F(key, label, type, hint="", path=None, group="", **kw):
    return dict(key=key, label=label, type=type, hint=hint, group=group, path=tuple(path or (key,)), **kw)

def _it(id, name, **kw):
    return {"id": id, "name": name, "title": "", "author": "", "desc": "", "fields": [], "footer": "", "thumb": False, "color": "#e0222d", **kw}
INV_TPLS = [
    _it("classic", "Classic", desc="{user} joined the server\nInvited by {inviter}\nTotal invites: **{invites}**"),
    _it("card", "Card", author="New member joined", desc="**{user}** was invited by **{inviter}**\nInvite code: `{code}`", footer="Member #{count}", thumb=True),
    _it("detailed", "Detailed", title="\U0001F4E5 Member joined", fields=[["Member", "{user}"], ["Invited by", "{inviter}"], ["Invites", "{invites}"]], footer="Code {code} | {count} members", thumb=True),
    _it("minimal", "Minimal", color="#2b2d31", desc="\u2795 {user} joined, invited by {inviter} ({invites} invites)"),
]

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
        F("welcome_dm", "Also DM the welcome message to new members", "bool", group="Join"),
        F("welcome_text", "Text above the embed", "text", "The only part that can ping the new member", max=500, group="Join"),
        F("welcome_title", "Embed title", "text", max=200, group="Join"),
        F("welcome_message", "Embed message", "area", "{user} {name} {server} {count} {line}", max=1800, group="Join"),
        F("welcome_footer", "Footer", "text", "Blank = Spidey default, __off__ = no footer", nullable=True, max=200, group="Join"),
        F("welcome_color", "Embed color", "color", group="Look"),
        F("thumbnail_mode", "Thumbnail", "select", path=("thumbnail_mode",), group="Look",
          options=[["avatar", "Member avatar"], ["server", "Server icon"], ["custom", "Custom URL"], ["none", "None"]]),
        F("thumbnail_url", "Custom thumbnail URL", "text", "https:// link", nullable=True, https=True, group="Look"),
        F("image_url", "Big image URL", "text", "https:// link", nullable=True, https=True, group="Look")]),

 dict(id="leave", title="Leave messages", cog="Welcome",
      container=lambda cog, g: cog.cfg(g.id), save=lambda cog, g: cog.save(),
      fields=[
        F("goodbye_enabled", "Send goodbye messages", "bool"),
        F("goodbye_channel", "Goodbye channel", "channel", "Where the message is posted when someone leaves"),
        F("goodbye_message", "Goodbye message", "area", "You can use {name} {server} {count}", max=1800)]),

 dict(id="joinroles", title="Join roles", cog="Welcome",
      container=lambda cog, g: cog.cfg(g.id), save=lambda cog, g: cog.save(),
      fields=[
        F("auto_roles_enabled", "Give roles to new members", "bool", group="Roles"),
        F("auto_roles", "Roles given on join", "multirole", "Every new member gets these automatically", group="Roles"),
        F("verify_role", "Verified role", "role", "Given to members who press the verify button", group="Checks"),
        F("min_age_days", "Minimum account age (days)", "number", "Accounts younger than this are turned away. 0 turns it off", min=0, max=365, group="Checks")]),

 dict(id="invites", title="Invite tracker", cog="InviteTracker",
      container=lambda cog, g: cog.cfg(g.id), save=lambda cog, g: cog.save(),
      fields=[
        F("enabled", "Track invites", "bool", "Needs the Manage Server permission so I can see invite uses"),
        F("channel", "Invite log channel", "channel", "Every new member is announced here"),
        F("template", "Message design", "invtpl", "Pick how the message looks", options=INV_TPLS)]),

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
    if t == "invtpl":
        if v not in [o["id"] for o in f["options"]]: raise ValueError("Pick one of the designs")
        return v
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
_SF = "dashboard_sessions.json"   # remembered logins (only a hash of each cookie is stored, never the cookie itself)
_hash = lambda sid: hashlib.sha256(sid.encode()).hexdigest()
def _sload():
    try:
        with open(_SF, encoding="utf8") as f: d = json.load(f)
        SESSIONS.update({k: v for k, v in d.items() if v.get("exp", 0) > time.time()})
    except (FileNotFoundError, ValueError, AttributeError): pass
def _spersist():
    keep = {k: v for k, v in SESSIONS.items() if v.get("remember") and v.get("user") and v.get("exp", 0) > time.time()}
    with open(_SF, "w", encoding="utf8") as f: json.dump(keep, f)
_sload()
SEC = {s["id"]: s for s in M.SECTIONS}

def can_manage(g):
    p = int(g["permissions"]); return bool(g.get("owner") or p & 0x8 or p & 0x20)

@web.middleware
async def session_mw(request, handler):
    sid = request.cookies.get("sid"); key = _hash(sid) if sid else None
    if not key or key not in SESSIONS or SESSIONS[key].get("exp", time.time() + 1) < time.time():
        if len(SESSIONS) > 2000:
            for k in [k for k, v in SESSIONS.items() if not v.get("user")]: SESSIONS.pop(k, None)
        sid = secrets.token_hex(24); key = _hash(sid); SESSIONS[key] = {}
    request["s"], request["k"] = SESSIONS[key], key
    resp = await handler(request)
    resp.set_cookie("sid", sid, httponly=True, samesite="Lax", secure=(os.getenv("REDIRECT_URI") or "").startswith("https"),
                    max_age=30 * 86400 if request["s"].get("remember") else None)
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
        if gid:   # live check, so a remembered login stops working the moment someone loses their permissions
            g = bot.get_guild(int(gid)); m = g.get_member(int(request["s"]["user"]["id"]))
            if m and not (g.owner_id == m.id or m.guild_permissions.administrator or m.guild_permissions.manage_guild):
                return J({"error": "You no longer have permission to manage this server"}, status=403)

    async def login(request):
        request["s"]["state"] = secrets.token_hex(16); request["s"]["remember"] = request.query.get("remember") == "1"
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
            s["exp"] = time.time() + (30 if s.get("remember") else 1) * 86400
            if s.get("remember"): _spersist()
            return web.HTTPFound("/")
        except Exception: return web.HTTPFound("/?error=login")
    async def logout(request):
        request["s"].clear(); SESSIONS.pop(request["k"], None); _spersist(); return web.HTTPFound("/")
    async def config(request): return J({"clientId": str(bot.user.id)})
    async def me(request): return J({"user": request["s"].get("user")})
    async def schema(request): return deny(request) or J(M.schema())
    async def guilds(request):
        return deny(request) or J([{"id": g["id"], "name": g["name"], "icon": g.get("icon"), "botIn": bot.get_guild(int(g["id"])) is not None, "members": getattr(bot.get_guild(int(g["id"])), "member_count", 0)} for g in request["s"]["guild_list"]])

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
        I = bot.get_cog("InviteTracker")
        if I: c = I.cfg(g.id); ok = bool(c["enabled"] and c["channel"]); out["status"].append(["Invite tracker", "Posting invite messages" if ok else "Off or no channel set", ok, "invites"])
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

    STY = ("primary", "secondary", "success", "danger")
    def clean_panel(g, d, old):
        t = lambda v, n: str(v or "").strip()[:n]
        def rid(v, role=False):
            if not v: return None
            i = int(v)
            if not (g.get_role(i) if role else g.get_channel(i)): raise ValueError("Something you picked is not in this server")
            return i
        cat = rid(d.get("category"))
        if cat and not isinstance(g.get_channel(cat), discord.CategoryChannel): raise ValueError("Ticket category must be a category")
        col = t(d.get("embed_color"), 7)
        out = {"name": t(d.get("name"), 60) or "Panel", "embed_title": t(d.get("embed_title"), 200) or "Need Help?", "embed_description": t(d.get("embed_description"), 2000),
               "embed_color": col if len(col) == 7 and col[0] == "#" else "#e0222d", "max_open": max(1, min(10, int(d.get("max_open") or 1))), "ticket_message": t(d.get("ticket_message"), 1500), "buttons": []}
        if cat: out["category"] = cat
        if (lg := rid(d.get("log_channel"))): out["log_channel"] = lg
        if (roles := [rid(x, True) for x in d.get("support_roles", [])]): out["support_roles"] = roles
        for b in (d.get("buttons") or [])[:25]:
            qs = [{"label": t(q.get("label"), 45), "style": "paragraph" if q.get("style") == "paragraph" else "short", "required": bool(q.get("required", True)),
                   "placeholder": t(q.get("placeholder"), 100), "max_length": max(1, min(4000, int(q.get("max_length") or 100)))} for q in (b.get("questions") or [])[:5] if t(q.get("label"), 45)]
            out["buttons"].append({"label": t(b.get("label"), 80) or "Open Ticket", "emoji": t(b.get("emoji"), 32), "style": b.get("style") if b.get("style") in STY else "primary",
                                   "form_enabled": bool(b.get("form_enabled", True)), "form_title": t(b.get("form_title"), 45), "questions": qs,
                                   "ticket_subject": t(b.get("ticket_subject"), 100), "ticket_message": t(b.get("ticket_message"), 1500)})
        if not out["buttons"]: raise ValueError("Add at least one button")
        out["button_label"] = out["buttons"][0]["label"]
        for k in ("channel", "message_id"):
            if old.get(k): out[k] = old[k]
        return out

    def panel_ui(pid, p):
        btn = {"label": "Open Ticket", "emoji": "", "style": "primary", "form_enabled": True, "form_title": "Open a ticket", "questions": [], "ticket_subject": "", "ticket_message": ""}
        return {"id": pid, "name": p.get("name") or p.get("embed_title") or "Panel", "embed_title": p.get("embed_title", "Need Help?"), "embed_description": p.get("embed_description", ""),
                "embed_color": p.get("embed_color", "#e0222d"), "category": str(p.get("category") or ""), "support_roles": [str(x) for x in p.get("support_roles", [])],
                "log_channel": str(p.get("log_channel") or ""), "max_open": p.get("max_open", 1), "ticket_message": p.get("ticket_message", ""),
                "channel": str(p.get("channel") or ""), "message_id": str(p.get("message_id") or ""), "buttons": [{**btn, **b} for b in p.get("buttons") or [btn]]}

    def panel_msg(pid, p):
        from theme import web_embed
        from tickets import TicketPanel
        return web_embed(p["embed_title"], p["embed_description"], int(p["embed_color"].lstrip("#"), 16)), TicketPanel(p["button_label"], pid, p)

    def tick(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e, None, None
        T = bot.get_cog("Tickets")
        return (None, bot.get_guild(int(gid)), T) if T else (J({"error": "The Tickets module is not loaded"}, status=404), None, None)

    async def refresh(g, pid, p):
        try: ch = g.get_channel(int(p["channel"])); msg = await ch.fetch_message(int(p["message_id"])); e, v = panel_msg(pid, p); await msg.edit(embed=e, view=v)
        except Exception: pass

    async def panels_get(request):
        e, g, T = tick(request)
        return e or J([panel_ui(k, v) for k, v in T.cfg(g.id)["panels"].items()])

    async def panel_put(request):
        e, g, T = tick(request)
        if e: return e
        P, pid = T.cfg(g.id)["panels"], request.match_info["pid"]
        try: data = clean_panel(g, await request.json(), P.get(pid, {}))
        except (ValueError, TypeError, AttributeError) as ex: return J({"error": str(ex) or "Invalid panel"}, status=400)
        if pid not in P: pid = "p" + secrets.token_hex(3)
        P[pid] = data; T.save()
        if data.get("message_id"): await refresh(g, pid, data)
        return J({"id": pid})

    async def panel_delete(request):
        e, g, T = tick(request)
        if e: return e
        P = T.cfg(g.id)["panels"]; p = P.pop(request.match_info["pid"], None); T.save()
        if p and p.get("message_id"):
            try: await (await g.get_channel(int(p["channel"])).fetch_message(int(p["message_id"]))).delete()
            except Exception: pass
        return J({"ok": True})

    async def panel_post(request):
        e, g, T = tick(request)
        if e: return e
        pid = request.match_info["pid"]; p = T.cfg(g.id)["panels"].get(pid); ch = g.get_channel(int((await request.json()).get("channel") or 0))
        if not p or not ch: return J({"error": "Save the panel and pick a channel first"}, status=400)
        try:
            emb, view = panel_msg(pid, p); msg = None
            if p.get("message_id") and p.get("channel") == ch.id:
                try: msg = await ch.fetch_message(int(p["message_id"])); await msg.edit(embed=emb, view=view)
                except discord.NotFound: msg = None
            if msg is None: msg = await ch.send(embed=emb, view=view)
        except discord.HTTPException: return J({"error": "I can't post in that channel. Check my permissions there."}, status=400)
        p["channel"], p["message_id"] = ch.id, msg.id; T.save()
        return J({"ok": True})

    async def cases_get(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        Mo, g = bot.get_cog("Moderation"), bot.get_guild(int(gid))
        if not Mo: return J({"error": "The Moderation module is not loaded"}, status=404)
        return J([{"id": c["id"], "type": c["type"], "user": _name(g, c["user"]), "user_id": str(c["user"]), "mod": _name(g, c["mod"]), "reason": str(c["reason"])[:500],
                   "time": c["time"], "active": bool(c.get("active"))} for c in Mo.store.guild(g.id)["cases"][::-1][:300]])

    def case_of(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e, None, None
        Mo = bot.get_cog("Moderation")
        if not Mo: return J({"error": "The Moderation module is not loaded"}, status=404), None, None
        c = next((x for x in Mo.store.guild(int(gid))["cases"] if str(x["id"]) == request.match_info["cid"]), None)
        return (None, Mo, (int(gid), c)) if c else (J({"error": "Case not found"}, status=404), None, None)

    async def case_put(request):
        e, Mo, gc = case_of(request)
        if e: return e
        gc[1]["reason"] = str((await request.json()).get("reason", "")).strip()[:500] or "No reason provided"; Mo.store.save()
        return J({"ok": True})

    async def case_delete(request):
        e, Mo, gc = case_of(request)
        if e: return e
        Mo.store.guild(gc[0])["cases"].remove(gc[1]); Mo.store.save()
        return J({"ok": True})

    def clean_tpl(d, old):
        t = lambda v, n: str(v or "").strip()[:n]
        def url(v):
            v = t(v, 500)
            if v and not v.startswith("https://"): raise ValueError("Links must start with https://")
            return v
        out = {"name": t(d.get("name"), 60) or "Template", "content": t(d.get("content"), 2000), "embeds": [], "buttons": []}
        for e in (d.get("embeds") or [])[:3]:
            col = t(e.get("color"), 7)
            out["embeds"].append({"title": t(e.get("title"), 256), "description": t(e.get("description"), 4000), "color": col if len(col) == 7 and col[0] == "#" else "#e0222d",
                                  "author": t(e.get("author"), 256), "footer": t(e.get("footer"), 2048), "image": url(e.get("image")), "thumbnail": url(e.get("thumbnail")),
                                  "fields": [{"name": t(f.get("name"), 256), "value": t(f.get("value"), 1024), "inline": bool(f.get("inline"))} for f in (e.get("fields") or [])[:10]
                                             if t(f.get("name"), 256) and t(f.get("value"), 1024)]})
        for b in (d.get("buttons") or [])[:5]:
            if t(b.get("label"), 80) and url(b.get("url")): out["buttons"].append({"label": t(b.get("label"), 80), "url": url(b.get("url")), "emoji": t(b.get("emoji"), 32)})
        if not out["content"] and not any(x["title"] or x["description"] or x["image"] or x["fields"] for x in out["embeds"]): raise ValueError("Add some text or an embed first")
        for k in ("channel", "message_id"):
            if old.get(k): out[k] = old[k]
        return out

    def tpl_msg(tp):
        embeds = []
        for x in tp["embeds"]:
            em = discord.Embed(title=x["title"] or None, description=x["description"] or None, color=int(x["color"].lstrip("#"), 16))
            if x["author"]: em.set_author(name=x["author"])
            if x["footer"]: em.set_footer(text=x["footer"])
            if x["image"]: em.set_image(url=x["image"])
            if x["thumbnail"]: em.set_thumbnail(url=x["thumbnail"])
            for f in x["fields"]: em.add_field(name=f["name"], value=f["value"], inline=f["inline"])
            embeds.append(em)
        view = discord.ui.View(timeout=None)
        for b in tp["buttons"]: view.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=b["label"], url=b["url"], emoji=b["emoji"] or None))
        return tp["content"] or None, embeds, view

    async def tpl_get(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        return J([{**v, "id": k} for k, v in _tload().get(gid, {}).items()])

    async def tpl_put(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        d = _tload(); T = d.setdefault(gid, {}); tid = request.match_info["tid"]
        try: data = clean_tpl(await request.json(), T.get(tid, {}))
        except (ValueError, TypeError, AttributeError) as ex: return J({"error": str(ex) or "Invalid template"}, status=400)
        if tid not in T: tid = "t" + secrets.token_hex(3)
        T[tid] = data; _tsave(d)
        if data.get("message_id"):
            try:
                ch = bot.get_guild(int(gid)).get_channel(int(data["channel"])); msg = await ch.fetch_message(int(data["message_id"])); c, em, v = tpl_msg(data)
                await msg.edit(content=c, embeds=em, view=v if data["buttons"] else None)
            except Exception: pass
        return J({"id": tid})

    async def tpl_delete(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        d = _tload(); d.get(gid, {}).pop(request.match_info["tid"], None); _tsave(d)
        return J({"ok": True})

    async def tpl_send(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        g = bot.get_guild(int(gid)); d = _tload(); tp = d.get(gid, {}).get(request.match_info["tid"]); ch = g.get_channel(int((await request.json()).get("channel") or 0))
        if not tp or not ch: return J({"error": "Save the template and pick a channel first"}, status=400)
        try:
            c, em, v = tpl_msg(tp); msg = None
            if tp.get("message_id") and tp.get("channel") == ch.id:
                try: msg = await ch.fetch_message(int(tp["message_id"])); await msg.edit(content=c, embeds=em, view=v if tp["buttons"] else None)
                except discord.NotFound: msg = None
            if msg is None: msg = await ch.send(content=c, embeds=em, **({"view": v} if tp["buttons"] else {}))
        except discord.HTTPException: return J({"error": "Discord rejected the message. Check the emoji, links and my permissions in that channel."}, status=400)
        tp["channel"], tp["message_id"] = ch.id, msg.id; _tsave(d)
        return J({"ok": True})

    def gwc(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e, None, None
        cog = bot.get_cog("Giveaways")
        return (None, bot.get_guild(int(gid)), cog) if cog else (J({"error": "The giveaway module is not loaded"}, status=404), None, None)

    async def gw_list(request):
        e, g, cog = gwc(request)
        if e: return e
        out = []
        for k, d in cog.g(g.id).items():
            ch = g.get_channel(int(d["channel"]))
            out.append({"id": k, "prize": d["prize"], "status": d["status"], "end": d["end"], "winners": d["winners"], "entries": len(d["entries"]),
                        "channel": ch.name if ch else "deleted-channel", "winner_names": [_name(g, u) for u in d.get("winners_ids", [])]})
        return J(sorted([x for x in out if x["status"] == "active"], key=lambda x: x["end"]) + sorted([x for x in out if x["status"] != "active"], key=lambda x: -x["end"])[:30])

    async def gw_start(request):
        e, g, cog = gwc(request)
        if e: return e
        try:
            b = await request.json(); prize = str(b.get("prize", "")).strip()[:200]
            mult = {"minutes": 60, "hours": 3600, "days": 86400}.get(b.get("unit")); secs = int(float(b.get("dur") or 0) * mult) if mult else 0
            ch = g.get_channel(int(b.get("channel") or 0)); role = int(b["role"]) if b.get("role") else None
        except (ValueError, TypeError, AttributeError): return J({"error": "Something in the form is not valid"}, status=400)
        if not prize: return J({"error": "Write the prize first"}, status=400)
        if not 60 <= secs <= 30 * 86400: return J({"error": "Pick a duration between 1 minute and 30 days"}, status=400)
        if not isinstance(ch, discord.TextChannel): return J({"error": "Choose a text channel"}, status=400)
        if role and not g.get_role(role): return J({"error": "That role is not in this server"}, status=400)
        try: k = await cog.start(g, ch, int(request["s"]["user"]["id"]), prize, str(b.get("description", "")).strip()[:1000], max(1, min(20, int(b.get("winners") or 1))), secs, role)
        except discord.HTTPException: return J({"error": "I can't post in that channel. Check my permissions there."}, status=400)
        return J({"id": k})

    async def gw_act(request):
        e, g, cog = gwc(request)
        if e: return e
        d, act = cog.g(g.id).get(request.match_info["gid"]), request.match_info["act"]
        if not d: return J({"error": "Giveaway not found"}, status=404)
        if act == "end" and d["status"] == "active": await cog.finish(g.id, d["id"])
        elif act == "reroll" and d["status"] != "active": await cog.finish(g.id, d["id"], reroll=True)
        else: return J({"error": "That action doesn't apply to this giveaway"}, status=400)
        return J({"ok": True})

    async def gw_del(request):
        e, g, cog = gwc(request)
        if e: return e
        d = cog.g(g.id).pop(request.match_info["gid"], None); cog.save()
        if d and d["status"] == "active":
            try: await (await g.get_channel(int(d["channel"])).fetch_message(int(d["message_id"]))).delete()
            except Exception: pass
        return J({"ok": True})

    async def inviters(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e
        cog, g = bot.get_cog("InviteTracker"), bot.get_guild(int(gid))
        if not cog: return J({"error": "The invite tracker module is not loaded"}, status=404)
        rows = sorted(((int(u), c) for u, c in cog.guild(g.id)["counts"].items() if c["total"]), key=lambda r: -(r[1]["total"] - r[1]["left"]))[:10]
        return J([{"name": _name(g, u), "invites": c["total"] - c["left"], "total": c["total"], "left": c["left"]} for u, c in rows])

    def rrc(request):
        gid = request.match_info["id"]
        if (e := deny(request, gid)): return e, None, None
        cog = bot.get_cog("ReactionRoles")
        return (None, bot.get_guild(int(gid)), cog) if cog else (J({"error": "The reaction roles module is not loaded"}, status=404), None, None)

    def clean_rr(g, d, old, uid):
        t = lambda v, n: str(v or "").strip()[:n]
        mode = d.get("mode") if d.get("mode") in ("buttons", "dropdown", "reactions") else "buttons"
        col, host = t(d.get("color"), 7), g.get_member(uid)
        strong = host and (g.owner_id == host.id or host.guild_permissions.administrator)
        out = {"name": t(d.get("name"), 60) or "Roles", "mode": mode, "title": t(d.get("title"), 200) or "Pick your roles", "description": t(d.get("description"), 2000),
               "color": col if len(col) == 7 and col[0] == "#" else "#e0222d", "unique": bool(d.get("unique")), "items": []}
        for x in (d.get("items") or [])[:20 if mode == "reactions" else 25]:
            role = g.get_role(int(x.get("role") or 0))
            if not role: raise ValueError("Choose a role for every row")
            if role.managed or role.is_default(): raise ValueError(f"{role.name} can't be given out")
            if role >= g.me.top_role: raise ValueError(f"I can't give {role.name} because it is above my highest role. Move my role higher first")
            if host and not strong and not (host.guild_permissions.manage_roles and role < host.top_role): raise ValueError(f"You can't hand out {role.name}. It is above your highest role")
            emoji, label = t(x.get("emoji"), 64), t(x.get("label"), 80)
            if mode == "reactions" and not emoji: raise ValueError("Every row needs an emoji in reactions mode")
            if (mode == "dropdown" or not emoji) and not label: label = role.name[:80]
            out["items"].append({"emoji": emoji, "label": label, "role": role.id, "description": t(x.get("description"), 100)})
        if not out["items"]: raise ValueError("Add at least one role")
        if mode == "reactions" and len({i["emoji"] for i in out["items"]}) < len(out["items"]): raise ValueError("Each role needs a different emoji")
        for k in ("channel", "message_id"):
            if old.get(k): out[k] = old[k]
        return out

    async def rr_list(request):
        e, g, cog = rrc(request)
        return e or J([{**v, "id": k, "role_ids": [str(i["role"]) for i in v["items"]], "channel": str(v.get("channel") or ""), "message_id": str(v.get("message_id") or ""),
                        "items": [{**i, "role": str(i["role"])} for i in v["items"]]} for k, v in cog.g(g.id).items()])

    async def rr_put(request):
        e, g, cog = rrc(request)
        if e: return e
        P, pid = cog.g(g.id), request.match_info["pid"]
        try: data = clean_rr(g, await request.json(), P.get(pid, {}), int(request["s"]["user"]["id"]))
        except (ValueError, TypeError, AttributeError) as ex: return J({"error": str(ex) or "Invalid panel"}, status=400)
        if pid not in P: pid = "r" + secrets.token_hex(3)
        P[pid] = data; cog.save()
        if data.get("message_id") and (ch := g.get_channel(int(data["channel"]))):
            try: await cog.publish(g, pid, data, ch)
            except Exception: pass
        return J({"id": pid})

    async def rr_delete(request):
        e, g, cog = rrc(request)
        if e: return e
        p = cog.g(g.id).pop(request.match_info["pid"], None); cog.save()
        if p and p.get("message_id"):
            try: await (await g.get_channel(int(p["channel"])).fetch_message(int(p["message_id"]))).delete()
            except Exception: pass
        return J({"ok": True})

    async def rr_post(request):
        e, g, cog = rrc(request)
        if e: return e
        pid = request.match_info["pid"]; p = cog.g(g.id).get(pid)
        try: ch = g.get_channel(int((await request.json()).get("channel") or 0))
        except (ValueError, TypeError): ch = None
        if not p or not isinstance(ch, discord.TextChannel): return J({"error": "Save the panel and pick a text channel first"}, status=400)
        try: await cog.publish(g, pid, p, ch)
        except discord.HTTPException: return J({"error": "Discord rejected the message. Check the emojis and my permissions in that channel."}, status=400)
        return J({"ok": True})

    async def logo(request): return web.Response(body=base64.b64decode(LOGO_B64), content_type="image/jpeg", headers={"Cache-Control": "public, no-store"})

    async def index(request): return web.Response(text=INDEX_HTML, content_type="text/html")
    app = web.Application(middlewares=[session_mw])
    app.add_routes([web.get("/", index), web.get("/auth/login", login), web.get("/auth/callback", callback), web.get("/auth/logout", logout),
        web.get("/api/config", config), web.get("/api/me", me), web.get("/api/schema", schema), web.get("/api/guilds", guilds),
        web.get("/api/guilds/{id}", guild_get), web.get("/api/guilds/{id}/overview", overview), web.get("/logo.jpg", logo), web.get("/spidey-pfp.jpg", logo), web.get("/api/guilds/{id}/rroles", rr_list), web.put("/api/guilds/{id}/rroles/{pid}", rr_put), web.delete("/api/guilds/{id}/rroles/{pid}", rr_delete), web.post("/api/guilds/{id}/rroles/{pid}/post", rr_post), web.get("/api/guilds/{id}/giveaways", gw_list), web.post("/api/guilds/{id}/giveaways", gw_start), web.post("/api/guilds/{id}/giveaways/{gid}/{act}", gw_act), web.delete("/api/guilds/{id}/giveaways/{gid}", gw_del), web.get("/api/guilds/{id}/inviters", inviters), web.get("/api/guilds/{id}/cases", cases_get), web.put("/api/guilds/{id}/cases/{cid}", case_put), web.delete("/api/guilds/{id}/cases/{cid}", case_delete), web.get("/api/guilds/{id}/templates", tpl_get), web.put("/api/guilds/{id}/templates/{tid}", tpl_put), web.delete("/api/guilds/{id}/templates/{tid}", tpl_delete), web.post("/api/guilds/{id}/templates/{tid}/send", tpl_send), web.get("/api/guilds/{id}/panels", panels_get), web.put("/api/guilds/{id}/panels/{pid}", panel_put), web.delete("/api/guilds/{id}/panels/{pid}", panel_delete), web.post("/api/guilds/{id}/panels/{pid}/post", panel_post), web.post("/api/guilds/{id}/announce", announce), web.put("/api/guilds/{id}/{sec}", section_put)])
    from urllib.parse import urlparse
    path = (urlparse(REDIRECT or "").path or "").rstrip("/")
    if path and path not in ("/auth/callback", "") and not any(getattr(r, "resource", None) and r.resource.canonical == path for r in app.router.routes()):
        app.router.add_get(path, callback)   # works even if REDIRECT_URI uses another path, like /callback
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
LOGO_B64 = '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'
INDEX_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Spidey Dashboard</title><link rel="icon" href="/spidey-pfp.jpg?v=2">
<link href="https://fonts.googleapis.com/css2?family=Sora:wght@400;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}:root{--bg:#121215;--panel:#1a1a1f;--line:#38282d;--tx:#f2eced;--mu:#a69a9d;--ac:#e0222d;--acx:#3a1418;--bad:#ff8a8a}
body{margin:0;color:var(--tx);font:15px/1.5 Sora,system-ui,sans-serif;background:var(--bg) url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .08 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}
.bgweb{position:fixed;right:-150px;top:-150px;width:600px;opacity:.16;pointer-events:none;z-index:0;transform:rotate(90deg);animation:breathe 9s ease-in-out infinite}
.bgweb path{stroke:#e0222d;fill:none;stroke-width:1.2;stroke-dasharray:3000;stroke-dashoffset:3000;animation:draw 4s ease-out forwards}
#app>.wrap{position:relative;z-index:1}
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
@media(max-width:720px){.layout{grid-template-columns:1fr}nav{flex-direction:row;overflow-x:auto;position:static}nav button{white-space:nowrap}.row{flex-wrap:wrap}}
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}.cord{height:clamp(10px,calc(50vh - 300px),300px)}.login .in{opacity:1!important;transform:none!important}.bgweb path{stroke-dashoffset:0}}
.login{min-height:100vh;display:grid;place-items:center;padding:24px;text-align:center;position:relative;overflow:hidden;color:#ececf4;background:radial-gradient(120% 80% at 50% 0%,#1d1d3a 0%,#0b0b16 62%)}
.login>svg{position:absolute;top:-50px;left:-70px;width:min(75vw,640px);opacity:.3;pointer-events:none}
.login>svg.r{left:auto;top:auto;right:-130px;bottom:-130px;transform:rotate(180deg)}
.login>svg path{stroke:#e0222d;fill:none;stroke-width:1}
@keyframes rise{to{opacity:1;transform:none}}
.login .in{position:relative;opacity:0;transform:translateY(14px);animation:rise .7s 1.1s forwards}
.login h1{font-size:clamp(3.4rem,13vw,7rem);font-weight:800;letter-spacing:-.055em;line-height:.9;margin:0 0 16px}
.login p{color:#a9acc9;margin:0 auto 28px;max-width:38ch}
.login small{display:block;margin-top:16px;color:#7d80a0}
.login .dc{box-shadow:0 8px 30px #5865f255;transition:transform .15s}.login .dc:hover{transform:translateY(-2px)}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:12px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.stat b{display:block;font-size:1.7rem;letter-spacing:-.02em}.stat small{color:var(--mu)}
.row.st>div{flex:1}.dot{width:10px;height:10px;border-radius:50%;background:var(--line);flex:none}.dot.on{background:#3ddc84}
.meter{width:min(240px,40%);height:8px;border-radius:9px;background:var(--line);overflow:hidden;flex:none}.meter i{display:block;height:100%;width:var(--w);background:var(--ac)}
.empty{padding:26px 0;color:var(--mu);text-align:center}
@keyframes draw{to{stroke-dashoffset:0}}@keyframes breathe{50%{opacity:.26;transform:rotate(94deg) scale(1.04)}}
@keyframes sway{0%,100%{transform:rotate(-3.5deg)}50%{transform:rotate(3.5deg)}}
@keyframes cord{to{height:clamp(10px,calc(50vh - 300px),300px)}}
@keyframes pop{from{opacity:0;transform:translateY(12px)}to{opacity:1;transform:none}}
@keyframes pulse{50%{box-shadow:0 0 0 5px #3ddc8422}}
.hang{position:absolute;top:0;left:50%;margin-left:-58px;width:116px;display:flex;flex-direction:column;align-items:center;transform-origin:50% 0;animation:sway 5s ease-in-out 2s infinite}
.cord{width:2px;height:0;background:linear-gradient(#d9dcf3,#e0222d);animation:cord 1.5s cubic-bezier(.3,.7,.2,1) forwards}
.medal{width:112px;height:112px;border-radius:50%;object-fit:cover;border:3px solid #e0222d;box-shadow:0 0 44px #e0222d77;margin-top:-3px}
.logo{width:36px;height:36px;border-radius:50%;border:2px solid var(--ac);box-shadow:0 0 14px #e0222d66}
header{background:#121215cc;backdrop-filter:blur(8px);position:sticky;top:0;z-index:5}
.pri{box-shadow:0 6px 22px #e0222d44;transition:transform .15s,box-shadow .15s}.pri:hover{transform:translateY(-2px);box-shadow:0 10px 28px #e0222d77}
.btn.ghost:hover{border-color:var(--ac)}
.srv{transition:transform .2s,border-color .2s,box-shadow .2s}.srv:hover{transform:translateY(-4px);border-color:var(--ac);box-shadow:0 12px 32px #e0222d26}
nav button{transition:background .15s,color .15s,padding .15s}nav>button:hover{color:var(--tx);padding-left:16px}nav button.on{box-shadow:inset 3px 0 0 var(--ac)}
#fields>*{animation:pop .4s both}#fields>:nth-child(2){animation-delay:.05s}#fields>:nth-child(3){animation-delay:.1s}#fields>:nth-child(4){animation-delay:.15s}
.sec,.stat,.bcard,.pcard{transition:border-color .2s}.sec:hover,.stat:hover,.pcard:hover{border-color:#e0222d66}
.dot.on{animation:pulse 2s infinite}
.bar-top{display:flex;justify-content:space-between;align-items:center;gap:16px;flex-wrap:wrap}.hint{color:var(--mu);margin:0}.sm{padding:7px 12px;font-size:.85rem}
.pgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:14px;margin-top:16px}
.pcard{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px}.pcard small{display:block;color:var(--mu)}.acts{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
.bcard{background:var(--panel);border:1px solid var(--line);border-radius:14px;margin-top:14px;padding:6px 18px 14px}.bhead{display:flex;justify-content:space-between;align-items:center;padding:10px 0}
.dbtn{display:inline-block;padding:8px 16px;border-radius:8px;font-weight:600;font-size:.9rem;color:#fff;background:#5865f2}.dbtn.secondary{background:#4e5058}.dbtn.success{background:#248046}.dbtn.danger{background:#da373c}
.qcard{border:1px dashed var(--line);border-radius:12px;padding:12px;margin-top:12px;display:grid;gap:8px}.qcard input,.qcard select{max-width:none}
.two{display:grid;grid-template-columns:1fr 1fr;gap:8px}.qrow{display:flex;justify-content:space-between;align-items:center}.qrow label{display:flex;align-items:center;gap:10px;font-weight:400}
.row.st .btn{flex:none}
.toast{color:var(--ac)}.toast.bad{color:var(--bad)}
.wrap{max-width:1180px}.layout{grid-template-columns:240px 1fr}
.hero{margin-bottom:24px}.hero h2{font-size:clamp(1.9rem,5vw,2.8rem);letter-spacing:-.035em;margin:0 0 6px}.hero p{color:var(--mu);margin:0 0 18px}
#q{max-width:380px}
.servers{grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:16px}
.srv{position:relative;overflow:hidden;flex-direction:column;align-items:flex-start;gap:14px;padding:20px;min-height:158px;animation:pop .45s both}
.srv::before{content:"";position:absolute;inset:-24px;background:var(--ic,none) center/cover;opacity:.2;filter:blur(24px);z-index:0}
.srv>*{position:relative;z-index:1}.srv img,.srv i{width:62px;height:62px;border-radius:18px;font-size:1.6rem}
.srv b{font-size:1.05rem;display:block}.srv .go{margin-top:auto;font-size:.85rem;font-weight:600;color:var(--ac)}
.srv.off{border-style:dashed}.srv.off .go{color:var(--mu)}
nav>button{display:flex;align-items:center;gap:10px}.ico{width:22px;text-align:center}
.ng{margin:18px 0 4px 12px;font-size:.7rem;letter-spacing:.1em;text-transform:uppercase;color:var(--mu)}
.nsrv{display:flex;align-items:center;gap:10px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:10px;margin-bottom:6px}
.nsrv img,.nsrv i{width:38px;height:38px;border-radius:11px;background:var(--line);display:grid;place-items:center;font-style:normal;font-weight:700;flex:none}
.nsrv div{flex:1;min-width:0}.nsrv b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.nsrv small{color:var(--mu)}
#secDesc{margin:-8px 0 18px}
.qa{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-top:12px}
.qa button{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:16px;text-align:left;cursor:pointer;font-weight:600;transition:transform .2s,border-color .2s}
.qa button:hover{transform:translateY(-3px);border-color:var(--ac)}.qa small{display:block;color:var(--mu);font-weight:400}
.pop{position:fixed;left:50%;bottom:24px;z-index:20;display:flex;align-items:center;gap:12px;padding:9px 9px 9px 22px;background:#1d1d23f2;backdrop-filter:blur(12px);border:1px solid #e0222d77;border-radius:99px;box-shadow:0 16px 50px #000a,0 0 30px #e0222d33;transform:translate(-50%,180%);opacity:0;transition:transform .45s cubic-bezier(.2,1.3,.4,1),opacity .25s,border-color .2s;max-width:calc(100vw - 20px);white-space:nowrap}
.pop.show{transform:translate(-50%,0);opacity:1}.pop .btn{padding:9px 22px;border-radius:99px;min-width:84px;text-align:center}
#popmsg{font-weight:600;font-size:.9rem}.pop.ok{border-color:#3ddc84aa}.pop.ok #popmsg{color:#3ddc84}.pop.bad #popmsg{color:var(--bad)}
.dots{display:none;gap:5px;align-items:center;justify-content:center;height:20px}.dots i{width:7px;height:7px;border-radius:50%;background:#fff;animation:bounce 1s infinite}
.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.45}30%{transform:translateY(-6px);opacity:1}}
.pop.saving .dots{display:flex}.pop.saving #savetxt{display:none}.pop.saving .btn{pointer-events:none}.pop.saving #reset{opacity:.4}
@media(max-width:720px){.ng,.nsrv{display:none}.pop{gap:8px;padding-left:16px;bottom:14px}#popmsg{font-size:.8rem}.pop .btn{padding:8px 14px;min-width:0}}
.prog{position:fixed;top:0;left:0;height:3px;width:0;opacity:0;background:linear-gradient(90deg,#e0222d,#ff8a8a);box-shadow:0 0 12px #e0222d;z-index:60;transition:width .5s ease,opacity .3s}
.btn{position:relative;overflow:hidden}.btn:active{transform:scale(.97)}
.ripple{position:absolute;border-radius:50%;background:#ffffff55;transform:scale(0);animation:rip .6s ease-out forwards;pointer-events:none}
@keyframes rip{to{transform:scale(4);opacity:0}}
.srv,.stat,.pcard,.qa button,.sec,.bcard{position:relative;background-image:radial-gradient(260px circle at var(--mx,-999px) var(--my,-999px),#e0222d24,transparent 62%)}
.glide{position:absolute;left:0;top:0;width:100%;height:0;border-radius:9px;background:var(--acx);box-shadow:inset 3px 0 0 var(--ac);transition:transform .38s cubic-bezier(.3,1.25,.4,1),height .3s,opacity .2s;pointer-events:none;animation:none!important}
nav>button{position:relative;z-index:1}nav button.on{background:none;box-shadow:none;color:var(--ac);font-weight:600}nav>*{animation:pop .4s both}
.sec>.row{animation:pop .35s both}.sec>.row:nth-child(1){animation-delay:0.03s}.sec>.row:nth-child(2){animation-delay:0.06s}.sec>.row:nth-child(3){animation-delay:0.09s}.sec>.row:nth-child(4){animation-delay:0.12s}.sec>.row:nth-child(5){animation-delay:0.15s}.sec>.row:nth-child(6){animation-delay:0.18s}.sec>.row:nth-child(7){animation-delay:0.21s}.sec>.row:nth-child(8){animation-delay:0.24s}.sec>.row:nth-child(9){animation-delay:0.27s}.sec>.row:nth-child(10){animation-delay:0.30s}.sec>.row:nth-child(11){animation-delay:0.33s}.sec>.row:nth-child(12){animation-delay:0.36s}
.skel{height:78px;border-radius:14px;margin-top:12px;background:linear-gradient(90deg,var(--panel) 25%,#2a2a32 50%,var(--panel) 75%);background-size:200% 100%;animation:shim 1.2s linear infinite}
@keyframes shim{to{background-position:-200% 0}}
.sw span:after{transition:transform .35s cubic-bezier(.3,1.6,.5,1)}
.pop.ok #popmsg::before{content:"\2713  ";display:inline-block;animation:tick .45s cubic-bezier(.3,1.8,.5,1)}@keyframes tick{from{transform:scale(0) rotate(-45deg)}}
.fs{position:relative;width:min(300px,36vw);display:none}.fs.on{display:block}.fs input{width:100%;max-width:none;padding-top:8px;padding-bottom:8px}
.fsres{position:absolute;top:calc(100% + 8px);left:0;right:0;background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:none;z-index:30;box-shadow:0 16px 44px #000b}
.fsres.open{display:block;animation:pop .2s both}.fsres button{display:flex;gap:10px;width:100%;padding:11px 14px;background:none;border:0;text-align:left;cursor:pointer}.fsres button:hover{background:var(--acx)}
.row.case{cursor:pointer;transition:background .15s}.row.case:hover{background:#ffffff08}.row.case>div{flex:1}.row.case .go{color:var(--mu);font-size:.85rem;transition:transform .2s,color .2s}.row.case:hover .go{transform:translateX(4px);color:var(--ac)}
.fl{margin:14px 0 8px}
.tgrid{display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:22px;align-items:start}.prev{position:sticky;top:76px}
.dmsg{background:#313338;border-radius:12px;padding:16px;color:#dbdee1;font-family:system-ui,sans-serif;font-size:.92rem}
.dhead{display:flex;align-items:center;gap:8px;margin-bottom:6px}.dhead img{width:34px;height:34px;border-radius:50%}.dhead b{color:#fff}.tag{background:#5865f2;color:#fff;font-size:.65rem;padding:1px 5px;border-radius:4px}
.dtext{white-space:pre-wrap;margin:4px 0 8px;overflow-wrap:anywhere}
.emb{display:flex;gap:12px;background:#2b2d31;border-left:4px solid #e0222d;border-radius:4px;padding:10px 12px;margin-top:8px}
.embody{flex:1;min-width:0}.eau{font-size:.8rem;font-weight:600;margin-bottom:4px}.eti{font-weight:700;color:#fff;margin-bottom:4px;overflow-wrap:anywhere}.ede{white-space:pre-wrap;font-size:.88rem;overflow-wrap:anywhere}
.efs{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:8px}.ef{grid-column:1/-1;font-size:.85rem}.ef.in{grid-column:auto}.ef b{display:block;color:#fff;font-size:.82rem}
.eim{max-width:100%;border-radius:6px;margin-top:10px}.eth{width:64px;height:64px;object-fit:cover;border-radius:6px}.eft{font-size:.72rem;color:#b5bac1;margin-top:8px}.dbtns{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
@media(max-width:1000px){.tgrid{grid-template-columns:1fr}.prev{position:static}}
@media(max-width:720px){.glide{display:none}nav button.on{background:var(--acx)}.fs{width:40vw}}
header{position:sticky;top:0}
.pop{position:fixed}
html,body{height:100%;overflow:hidden}
#app{height:100vh;height:100dvh;display:flex;flex-direction:column}
#app>header{position:relative;flex:none}
#app>.wrap{flex:1;min-height:0;overflow:hidden;width:100%;padding-bottom:0;display:flex;flex-direction:column}
#picker{flex:1;min-height:0;overflow-y:auto;padding-bottom:48px;scrollbar-width:thin}
#panel:not(.hide){flex:1;min-height:0;display:flex;flex-direction:column}
.layout{flex:1;min-height:0;grid-template-columns:240px minmax(0,1fr);grid-template-rows:minmax(0,1fr)}
.layout nav{position:relative;top:auto;align-self:stretch;max-height:none;min-height:0;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;padding-bottom:32px}
#main{min-height:0;overflow-y:auto;overscroll-behavior:contain;scrollbar-width:thin;padding:0 8px 140px 0}
.prev{top:12px}
.login{height:100vh;height:100dvh;overflow-x:hidden;overflow-y:auto}
@media(max-width:720px){.layout{grid-template-columns:minmax(0,1fr);grid-template-rows:auto minmax(0,1fr)}.layout nav{flex-direction:row;overflow-x:auto;overflow-y:hidden;padding:6px 0;align-self:start}#app>.wrap{padding:16px 14px 0}#main{padding-right:0}}
.keep{display:inline-flex;align-items:center;gap:10px;margin-top:20px;cursor:pointer;color:#a9acc9;font-size:.9rem;user-select:none;position:relative}
.keep input{position:absolute;opacity:0;width:0;height:0}
.keep .box{width:22px;height:22px;border-radius:7px;border:2px solid #5b5d7a;display:grid;place-items:center;transition:background .2s,border-color .2s,transform .2s,box-shadow .2s}
.keep .box svg{width:16px;height:16px;fill:none;stroke:#fff;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:24;stroke-dashoffset:24;transition:stroke-dashoffset .3s .05s}
.keep input:checked+.box{background:#e0222d;border-color:#e0222d;box-shadow:0 0 16px #e0222d88}.keep input:checked+.box svg{stroke-dashoffset:0}
.keep:active .box{transform:scale(.86)}.keep input:focus-visible+.box{outline:2px solid #fff;outline-offset:2px}
.spl{display:grid;justify-items:center;gap:8px;padding:48px 0;color:var(--mu)}
.spl .thr{display:flex;flex-direction:column;align-items:center;transform-origin:50% 0;animation:bob 1.8s ease-in-out infinite}
.spl .thr i{width:2px;height:40px;background:linear-gradient(#d9dcf3,#e0222d)}
.lg path{fill:none;stroke:#e0222d;stroke-width:2.4;stroke-linecap:round;stroke-linejoin:round}.bd{fill:#e0222d}
.lg{transform-origin:24px 27px;animation:legs .4s ease-in-out infinite alternate}.lg.r{animation-direction:alternate-reverse}
@keyframes legs{from{transform:rotate(-8deg)}to{transform:rotate(8deg)}}@keyframes bob{0%,100%{transform:translateY(-16px)}50%{transform:translateY(8px)}}
.boot{position:fixed;inset:0;z-index:100;display:grid;place-items:center;background:#0b0b16;color:#a9acc9;transition:opacity .45s}.boot.gone{opacity:0;pointer-events:none}
html{color-scheme:dark}
input[type=search]{-webkit-appearance:none;appearance:none;width:100%;background:#0e0e12 url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23a69a9d' stroke-width='2.4' stroke-linecap='round'><circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/></svg>") no-repeat 15px center/18px;color:var(--tx);-webkit-text-fill-color:var(--tx);caret-color:var(--ac);border:1px solid var(--line);border-radius:99px;padding:11px 16px 11px 44px;transition:border-color .2s,box-shadow .2s,background-color .2s}
input[type=search]::placeholder{color:var(--mu);-webkit-text-fill-color:var(--mu);opacity:1}
input[type=search]:hover{border-color:#e0222d66}
input[type=search]:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px #e0222d33;background-color:#15151a}
input[type=search]::-webkit-search-decoration,input[type=search]::-webkit-search-results-button{display:none}
input[type=search]::-webkit-search-cancel-button{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;cursor:pointer;background:#3a2a2f url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23f2eced' stroke-width='3' stroke-linecap='round'><path d='M6 6l12 12M18 6L6 18'/></svg>") center/10px no-repeat}
input:-webkit-autofill,textarea:-webkit-autofill{-webkit-box-shadow:0 0 0 100px #0e0e12 inset;-webkit-text-fill-color:var(--tx)}
header{gap:12px}header #title{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.um{position:relative;flex:none}.avb{background:none;border:0;padding:0;cursor:pointer;border-radius:50%;display:block}
.avb .av{display:block;border:2px solid var(--line);transition:border-color .2s,transform .2s}.avb:hover .av{border-color:var(--ac);transform:scale(1.06)}
.umenu{position:absolute;right:0;top:calc(100% + 12px);min-width:200px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:14px;display:none;gap:14px;box-shadow:0 16px 44px #000b;z-index:50}
.umenu.open{display:grid;animation:pop .2s both}.uname{font-weight:600;overflow-wrap:anywhere}.umenu small{color:var(--mu)}
.ib{display:none;width:38px;height:38px;flex:none;border-radius:50%;border:1px solid var(--line);background:none;cursor:pointer;place-items:center}
.ib svg{width:18px;height:18px;fill:none;stroke:var(--tx);stroke-width:2.2;stroke-linecap:round}
@media(max-width:720px){
  header{padding:10px 14px;gap:10px}header #title{font-size:1rem}
  header:has(.fs.on) .ib{display:grid}.fs.on{display:none}
  .fs.on.open{display:block;position:absolute;left:0;right:0;top:100%;width:auto;padding:10px 14px;background:#121215f5;border-bottom:1px solid var(--line);z-index:40;animation:pop .2s both}
  .fs.open .fsres{left:14px;right:14px;top:calc(100% - 2px)}
}
.mbar{display:none;flex:none;align-items:center;gap:12px;padding:0 0 14px}
.mb{width:44px;height:44px;border-radius:12px;border:1px solid var(--line);background:var(--panel);display:grid;place-items:center;cursor:pointer;transition:border-color .2s,transform .2s}
.mb:hover{border-color:var(--ac)}.mb:active{transform:scale(.9)}.mb svg{width:20px;height:20px;fill:none;stroke:var(--tx);stroke-width:2.4;stroke-linecap:round}
#mtitle{font-weight:600;font-size:1.05rem}
.scrim{position:fixed;inset:0;z-index:70;background:#000b;backdrop-filter:blur(3px);-webkit-backdrop-filter:blur(3px);opacity:0;pointer-events:none;transition:opacity .3s}.scrim.show{opacity:1;pointer-events:auto}
@media(max-width:720px){
  #app>.wrap{z-index:auto}
  .mbar{display:flex}
  .layout{grid-template-columns:minmax(0,1fr);grid-template-rows:minmax(0,1fr)}
  .layout nav{position:fixed;left:0;top:0;bottom:0;width:min(84vw,320px);z-index:80;flex-direction:column;overflow-x:hidden;overflow-y:auto;padding:18px 14px 36px;background:var(--panel);border-right:1px solid var(--line);box-shadow:0 0 60px #000c;transform:translateX(-105%);transition:transform .35s cubic-bezier(.3,1,.4,1);align-self:auto}
  .layout nav.open{transform:none}
  .ng{display:block}.nsrv{display:flex}.glide{display:block}
  nav button{white-space:normal}nav button.on{background:none}
}
.greet{animation:pop .5s both;margin-bottom:6px}
.greet h1{font-size:clamp(1.7rem,5.5vw,2.7rem);letter-spacing:-.035em;line-height:1.1;margin:0 0 6px;overflow-wrap:anywhere}
.greet .nm{color:var(--ac);text-shadow:0 0 28px #e0222d55}
.greet p{color:var(--mu);margin:0 0 22px}
.wave{display:inline-block;transform-origin:70% 75%;animation:wave 2.4s ease-in-out .6s 2}
@keyframes wave{0%,60%,100%{transform:rotate(0)}10%{transform:rotate(16deg)}20%{transform:rotate(-8deg)}30%{transform:rotate(16deg)}40%{transform:rotate(-4deg)}50%{transform:rotate(10deg)}}
.tplgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin-top:4px}
.tplcard{position:relative;display:grid;gap:10px;text-align:left;cursor:pointer;color:var(--tx);background:var(--bg);border:2px solid var(--line);border-radius:16px;padding:14px;transition:transform .2s,border-color .2s,box-shadow .2s}
.tplcard:hover{transform:translateY(-3px);border-color:#e0222d88}
.tplcard.sel{border-color:var(--ac);box-shadow:0 0 0 3px #e0222d33,0 12px 30px #e0222d22}
.tplcard.sel::after{content:"\2713";position:absolute;top:10px;right:12px;width:22px;height:22px;border-radius:50%;background:var(--ac);color:#fff;display:grid;place-items:center;font-size:.8rem;animation:tick .4s cubic-bezier(.3,1.8,.5,1)}
.dmsg.mini{padding:10px;font-size:.82rem;pointer-events:none}.eth.fake{display:grid;place-items:center;background:#5865f2;color:#fff;font-weight:700;border-radius:50%;width:44px;height:44px;flex:none}
.selmock{background:#1e1f22;border:1px solid #3f4147;border-radius:6px;padding:10px 12px;margin-top:8px;color:#b5bac1}
.rx{display:inline-grid;place-items:center;min-width:34px;height:28px;padding:0 8px;border-radius:8px;background:#2b2d31;border:1px solid #3f4147}
</style></head><body>
<div class="prog" id="prog"></div><div class="scrim" id="scrim"></div><div class="boot" id="boot"></div>
<section id="login" class="login hide"><div class="hang"><i class="cord"></i><img class="medal" src="/spidey-pfp.jpg?v=2" alt="Spidey"></div><div class="in">
  <h1>Spidey</h1>
  <p>Your server's control room. Tune AutoMod, welcomes, levels and tickets here instead of typing commands.</p>
  <a class="btn dc" id="dclogin" href="/auth/login?remember=1">Log in with Discord</a>
  <div><label class="keep"><input type="checkbox" id="keep" checked><span class="box"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></span>Keep me logged in on this device</label></div>
  <small>We only read your name and the list of servers you manage.</small><div id="loginErr" class="err"></div>
</div></section>
<section id="app" class="hide">
  <header><img class="logo" id="hlogo" src="/spidey-pfp.jpg?v=2" alt=""><b id="title">Spidey Dashboard</b>
    <button class="ib" id="fsbtn" aria-label="Search features"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg></button>
    <div class="fs" id="fs"><input id="fsearch" type="search" placeholder="Search features (press /)" autocomplete="off"><div class="fsres" id="fsres"></div></div>
    <div class="um" id="um"><button class="avb" id="avb" aria-label="Account menu"><img class="av" id="uav" alt=""></button>
      <div class="umenu" id="umenu"><div><small>Signed in as</small><div class="uname" id="uname"></div></div><a class="btn ghost sm" href="/auth/logout">Log out</a></div></div>
  </header>
  <div class="wrap">
    <div id="picker"><div class="hero"><h2 id="hello">Hey</h2><p>Pick a server to manage. Servers with Spidey come first.</p><input id="q" type="search" placeholder="Search your servers"></div><div class="servers" id="servers"></div></div>
    <div id="panel" class="hide"><div class="mbar"><button class="mb" id="menubtn" aria-label="Open menu"><svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h10"/></svg></button><span id="mtitle"></span></div><div class="layout"><nav id="nav"></nav><div id="main"><div class="greet hide" id="greet"></div><h2 id="secTitle"></h2><p class="hint" id="secDesc"></p><div id="fields"></div></div></div></div>
  </div>
  <div class="pop" id="bar" role="status"><span id="popmsg">You have unsaved changes</span><button class="btn ghost" id="reset">Reset</button><button class="btn pri" id="save"><span id="savetxt">Save</span><span class="dots"><i></i><i></i><i></i></span></button></div>
</section>
<script>
const $=id=>document.getElementById(id);
let _p=0;const prog=d=>{_p+=d;const b=$('prog');if(!b)return;if(_p>0){b.style.opacity=1;b.style.width='72%'}else{_p=0;b.style.width='100%';setTimeout(()=>{b.style.opacity=0;setTimeout(()=>{b.style.width='0'},300)},250)}};
const api=async(u,o)=>{prog(1);try{const r=await fetch(u,o);const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||'Request failed');return j}finally{prog(-1)}};
let SCHEMA=[],CID='',G=null,META=null,VAL={},ORIG={},TAB='',ready=false;
const clone=o=>JSON.parse(JSON.stringify(o));
const isDirty=()=>!!ORIG[TAB]&&JSON.stringify(VAL[TAB])!==JSON.stringify(ORIG[TAB]);
let CTL=null,busy=false,PREV=null;
async function init0(){
  const q=new URLSearchParams(location.search).get('error');
  const {user}=await api('/api/me');
  if(!user){$('login').classList.remove('hide');if(q)$('loginErr').textContent=q==='state'?'Login expired. Try again.':'Discord login failed. Try again.';return}
  [SCHEMA,{clientId:CID}]=await Promise.all([api('/api/schema'),api('/api/config')]);
  $('app').classList.remove('hide');$('uname').textContent=user.global_name||user.username;window.ME=user.global_name||user.username;$('hello').textContent='Hey, '+window.ME;
  $('uav').src=user.avatar?`https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png?size=64`:`https://cdn.discordapp.com/embed/avatars/${Number((BigInt(user.id)>>22n)%6n)}.png`;
  loadServers();
}
async function loadServers(){
  const box=$('servers');box.innerHTML='';box.append(spl('Finding your servers'));let gs;
  try{gs=await api('/api/guilds')}catch(e){box.textContent=e.message;return}
  const draw=()=>{const q=$('q').value.trim().toLowerCase();box.innerHTML='';
    const list=gs.filter(g=>g.name.toLowerCase().includes(q)).sort((a,b)=>b.botIn-a.botIn);
    if(!list.length){box.append(el('div','empty',gs.length?'No server matches your search.':'You do not manage any servers yet.'));return}
    list.forEach((g,i)=>{const c=el('button','srv'+(g.botIn?'':' off')),url=g.icon?`https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=128`:'';
      c.style.animationDelay=(i*45)+'ms';if(url)c.style.setProperty('--ic',`url(${url})`);
      const ic=url?el('img'):el('i','',g.name[0]);if(url){ic.src=url;ic.alt=''}
      const t=el('div');t.append(el('b','',g.name),el('small','',g.botIn?`${g.members.toLocaleString()} members`:'Spidey is not here yet'));
      c.append(ic,t,el('span','go',g.botIn?'Manage server \u2192':'Add Spidey \u2192'));
      c.onclick=()=>g.botIn?openGuild(g):window.open(`https://discord.com/oauth2/authorize?client_id=${CID}&scope=bot%20applications.commands&permissions=8&guild_id=${g.id}`,'_blank');
      box.append(c)})};
  $('q').oninput=draw;draw();
}
const SPECIAL=[['overview','Overview'],['board','Leaderboard'],['cases','Mod cases'],['panels','Ticket panels'],['otickets','Open tickets'],['announce','Announce']];
let TABS=[],OV=null;
const TITLES={rroles:'Reaction roles',giveaways:'Giveaways',invites:'Invite tracker',inviters:'Top inviters',leave:'Leave messages',joinroles:'Join roles',messages:'Messages',overview:'Overview',automod:'AutoMod',welcome:'Welcome',leveling:'Leveling',moderation:'Moderation',utility:'Suggestions',tickets:'Ticket settings',panels:'Ticket panels',otickets:'Open tickets',board:'Leaderboard',cases:'Mod cases',announce:'Announce'};
const ICON={rroles:'\ud83c\udff7\ufe0f',giveaways:'\ud83c\udf89',invites:'\ud83d\udce8',inviters:'\ud83c\udfc5',leave:'\ud83d\udeaa',joinroles:'\ud83c\udfad',messages:'\ud83d\udcac',overview:'\ud83c\udfe0',automod:'\ud83d\udee1\ufe0f',welcome:'\ud83d\udc4b',leveling:'\u2b50',moderation:'\ud83d\udd28',utility:'\ud83d\udca1',tickets:'\u2699\ufe0f',panels:'\ud83c\udfab',otickets:'\ud83d\udceb',board:'\ud83c\udfc6',cases:'\ud83d\udccb',announce:'\ud83d\udce3'};
const DESC={rroles:'Let members pick their own roles with buttons, a menu or emoji reactions.',giveaways:'Start giveaways and let members enter with one button.',invites:'See who invited each new member and post it to a channel.',inviters:'The members who brought in the most people.',leave:'Say goodbye when someone leaves the server.',joinroles:'Roles and checks for brand new members.',messages:'Build reusable messages with embeds and buttons, then send them anywhere.',overview:'A quick look at your server and what Spidey is doing in it.',automod:'Rules that keep your chat clean.',welcome:'Greet new members and say goodbye.',leveling:'XP, levels and role rewards.',moderation:'Where moderation actions are logged.',utility:'Where /suggest posts end up.',tickets:'Default settings for the ticket system.',panels:'Build ticket panels with buttons and forms.',otickets:'Tickets that are open right now.',board:'Your most active members.',cases:'The latest moderation actions.',announce:'Post a message as Spidey.'};
const GROUPS=[['',['overview']],['Modules',['automod','welcome','leave','joinroles','rroles','invites','leveling','moderation','utility']],['Tickets',['tickets','panels','otickets']],['Insights',['board','inviters','cases']],['Tools',['messages','giveaways','announce']]];
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const when=t=>new Date(t*1000).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});
function setHL(g){const h=$('hlogo');h.onerror=()=>{h.onerror=null;h.src='/spidey-pfp.jpg?v=2'};h.src=g&&g.icon?`https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=64`:'/spidey-pfp.jpg?v=2'}
async function openGuild(g){
  $('servers').innerHTML='';$('servers').append(spl('Opening '+g.name));
  try{[META,OV]=await Promise.all([api('/api/guilds/'+g.id),api(`/api/guilds/${g.id}/overview`)])}catch(e){alert(e.message);loadServers();return}
  G=g;VAL=clone(META.values);ORIG=clone(META.values);
  const have=new Set(['overview','rroles','giveaways','inviters','messages','panels','otickets','board','cases','announce',...SCHEMA.filter(s=>VAL[s.id]).map(s=>s.id)]);
  TABS=GROUPS.flatMap(([,ids])=>ids.filter(i=>have.has(i)).map(i=>[i,TITLES[i]]));TAB='overview';CTL=null;
  $('picker').classList.add('hide');$('panel').classList.remove('hide');$('title').textContent=g.name;$('fs').classList.add('on');setHL(g);
  const nv=$('nav');nv.innerHTML='';
  const nh=el('div','nsrv'),ni=g.icon?el('img'):el('i','',g.name[0]);if(g.icon){ni.src=`https://cdn.discordapp.com/icons/${g.id}/${g.icon}.png?size=64`;ni.alt=''}
  const nt=el('div'),bk=el('button','btn ghost sm','Switch');nt.append(el('b','',g.name),el('small','',`${OV.stats.members} members`));bk.onclick=goBack;nh.append(ni,nt,bk);nv.append(nh);
  GROUPS.forEach(([gn,ids])=>{const vis=ids.filter(i=>have.has(i));if(!vis.length)return;if(gn)nv.append(el('div','ng',gn));
    vis.forEach(i=>{const b=el('button');b.dataset.k=i;b.append(el('span','ico',ICON[i]),TITLES[i]);b.onclick=()=>switchTab(i);nv.append(b)})});
  render();
}
function renderSpecial(k){
  const f=$('fields');f.innerHTML='';$('secTitle').textContent=TABS.find(x=>x[0]===k)[1];
  const list=(rows,empty)=>{if(!rows.length){f.append(el('div','empty',empty));return}const box=el('div','sec');rows.forEach(r=>box.append(r));f.append(box)};
  if(k==='overview'){
    const st=el('div','stats');[['Members',OV.stats.members],['Channels',OV.stats.channels],['Roles',OV.stats.roles],['Bot ping',OV.stats.latency+' ms']].forEach(([a,b])=>{const c=el('div','stat');const bb=el('b','',b);countUp(bb,b);c.append(bb,el('small','',a));st.append(c)});f.append(st);
    f.append(el('h3','','Quick actions'));const qa=el('div','qa');[['panels','Build a ticket panel','Buttons, forms, categories'],['automod','Tune AutoMod','Spam, links, bad words'],['welcome','Set up welcomes','Messages and auto-roles'],['giveaways','Start a giveaway','Winners are picked for you'],['messages','Create a custom message','Embeds and link buttons'],['announce','Send an announcement','Post as Spidey']].filter(([k])=>TABS.some(t=>t[0]===k)).forEach(([k,t,d])=>{const b=el('button');b.append(el('span','',(ICON[k]||'')+' '+t),el('small','',d));b.onclick=()=>switchTab(k);qa.append(b)});f.append(qa);
    f.append(el('h3','','What is switched on'));
    list(OV.status.map(([n,txt,on,tab])=>{const r=el('div','row st'),d=el('div'),b=el('button','btn ghost','Configure');d.append(el('label','',n),el('small','',txt));b.onclick=()=>switchTab(tab);r.append(el('span','dot'+(on?' on':'')),d,b);return r}),'');
  }else if(k==='board'){
    const L=OV.leaderboard||[],top=(L[0]&&L[0].xp)||1;
    list(L.map((u,i)=>{const r=el('div','row'),d=el('div'),m=el('div','meter');d.append(el('label','',`${i+1}. ${u.name}`),el('small','',`Level ${u.level}, ${u.xp} XP, ${u.messages} messages`));m.append(el('i'));m.firstChild.style.setProperty('--w',(u.xp/top*100)+'%');m.style.setProperty('--w','0');r.append(d,m);return r}),'Nobody has earned XP yet. Turn on Leveling and start chatting.');
  }else if(k==='cases'){renderCases();}else if(k==='otickets'){
    list((OV.tickets||[]).map(t=>{const r=el('div','row'),d=el('div');d.append(el('label','',`#${t.number} ${t.subject}`),el('small','',`Opened by ${t.opener} on ${when(t.created)}. ${t.claimed?'Claimed by '+t.claimed:'Not claimed yet'}`));r.append(d);return r}),'No open tickets.');
  }else if(k==='panels'){renderPanels();}else if(k==='messages'){renderMessages();}else if(k==='giveaways'){renderGiveaways();}else if(k==='rroles'){renderRR();}else if(k==='inviters'){renderInviters();}else if(k==='announce'){
    const box=el('div','sec'),mk=(l,h,c)=>{const r=el('div','row col'),d=el('div');d.append(el('label','',l),el('small','',h));r.append(d,c);box.append(r);return c};
    const ch=el('select');META.channels.forEach(o=>ch.add(new Option('#'+o.name,o.id)));mk('Channel','Where Spidey posts it',ch);
    const ti=el('input');ti.type='text';ti.maxLength=200;ti.placeholder='Announcement';mk('Title','',ti);
    const ms=el('textarea');ms.maxLength=3500;mk('Message','Posted as a Spidey-styled embed',ms);
    const go=el('button','btn pri','Send message');go.style.margin='14px 0';box.append(go);f.append(box);const out=el('div','toast');out.style.marginTop='12px';f.append(out);
    go.onclick=async()=>{try{await api(`/api/guilds/${G.id}/announce`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:ch.value,title:ti.value,message:ms.value})});out.className='toast';out.textContent='Sent.';ms.value=''}catch(e){out.className='toast bad';out.textContent=e.message}};
  }
}
function showGreet(){const g=$('greet');if(TAB!=='overview'){g.classList.add('hide');return}
  g.classList.remove('hide');g.innerHTML='';const t=el('h1');t.append('Welcome, ',el('span','nm',window.ME||'there'),' ',el('span','wave','\ud83d\udc4b'));
  g.append(t,el('p','',`Here is how ${G.name} is doing today.`))}
function drawer(o){$('nav').classList.toggle('open',o);$('scrim').classList.toggle('show',o)}
function switchTab(k){if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;if(ORIG[TAB])VAL[TAB]=clone(ORIG[TAB]);CTL=null;PREV=null;drawer(false);TAB=k;render();touch();$('main').scrollTo({top:0})}
function field(sec,f){
  const row=document.createElement('div');row.className='row'+(f.type==='area'||f.type==='lines'||f.type.startsWith('multi')?' col':'');
  row.innerHTML='<div><label></label><small></small></div>';row.querySelector('label').textContent=f.label;row.querySelector('small').textContent=f.hint||'';
  const v=VAL[sec][f.key],set=x=>{VAL[sec][f.key]=x;touch()};let el;
  if(f.type==='bool'){const w=document.createElement('div');w.className='sw';w.innerHTML='<input type="checkbox" role="switch"><span></span>';el=w.firstChild;el.checked=v;el.onchange=()=>set(el.checked);row.append(w)}
  else if(f.type.startsWith('multi')){const list=f.type==='multirole'?META.roles:META.channels;const box=document.createElement('div');box.className='chips';
    list.forEach(o=>{const l=document.createElement('label'),c=document.createElement('input');c.type='checkbox';c.checked=v.includes(o.id);
      c.onchange=()=>set(list.filter(x=>[...box.querySelectorAll('input')].some((i,n)=>i.checked&&list[n].id===x.id)).map(x=>x.id));
      l.append(c,(f.type==='multirole'?'':'#')+o.name);box.append(l)});row.append(box)}
  else if(f.type==='invtpl'){row.classList.add('col');const grid=el('div','tplgrid');
    f.options.forEach(t=>{const c=el('button','tplcard'+(v===t.id?' sel':''));c.type='button';c.append(el('b','',t.name),tplPrev(t));
      c.onclick=()=>{set(t.id);grid.querySelectorAll('.tplcard').forEach(x=>x.classList.remove('sel'));c.classList.add('sel')};grid.append(c)});row.append(grid)}
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
  document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('on',b.dataset.k===TAB));moveGlide();
  $('secDesc').textContent=DESC[TAB]||'';$('mtitle').textContent=TITLES[TAB]||'';showGreet();
  if(!SCHEMA.some(x=>x.id===TAB))return renderSpecial(TAB);
  const s=SCHEMA.find(x=>x.id===TAB);$('secTitle').textContent=TITLES[TAB]||s.title;$('fields').innerHTML='';
  let grp=null,box=null;
  s.fields.forEach(f=>{if(box===null||f.group!==grp){grp=f.group;if(grp){const h=document.createElement('h3');h.textContent=grp;$('fields').append(h)}box=document.createElement('div');box.className='sec';$('fields').append(box)}
    box.append(field(TAB,f))});
}
const saveTab=async()=>{await api(`/api/guilds/${G.id}/${TAB}`,PJ('PUT',VAL[TAB]));ORIG[TAB]=clone(VAL[TAB]);api(`/api/guilds/${G.id}/overview`).then(o=>OV=o).catch(()=>{})};
const cur=()=>CTL||{dirty:isDirty,save:saveTab,reset:()=>{if(ORIG[TAB])VAL[TAB]=clone(ORIG[TAB]);render()}};
function touch(){if(PREV)PREV();if(busy)return;const b=$('bar');b.classList.toggle('show',cur().dirty());b.classList.remove('ok','bad');$('popmsg').textContent='You have unsaved changes'}
$('save').onclick=async()=>{if(busy)return;busy=true;const b=$('bar');b.classList.add('saving');
  try{await Promise.all([cur().save(),new Promise(r=>setTimeout(r,800))]);busy=false;b.classList.remove('saving');b.classList.add('ok');$('popmsg').textContent='Saved. Spidey is using it now.';
    setTimeout(()=>{if(!cur().dirty()){b.classList.remove('show');setTimeout(()=>b.classList.remove('ok'),400)}},1700)}
  catch(e){busy=false;b.classList.remove('saving');b.classList.add('bad');$('popmsg').textContent=e.message}};
$('reset').onclick=()=>{if(busy)return;cur().reset();touch()};
function goBack(){drawer(false);if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;CTL=null;PREV=null;$('fs').classList.remove('on');$('bar').classList.remove('show');$('panel').classList.add('hide');$('picker').classList.remove('hide');$('title').textContent='Spidey Dashboard';setHL(null);loadServers()}
$('fields').addEventListener('input',touch);$('fields').addEventListener('change',touch);
addEventListener('beforeunload',e=>{if(cur().dirty()){e.preventDefault();e.returnValue=''}});
function countUp(n,v){const num=parseFloat(v);if(isNaN(num))return;const suf=String(v).replace(/[\d.]/g,''),t0=performance.now();
  const step=t=>{const p=Math.min(1,(t-t0)/800);n.textContent=Math.round(num*(1-Math.pow(1-p,3)))+suf;if(p<1)requestAnimationFrame(step)};requestAnimationFrame(step)}
const bindv=(o,k,e,ev,g)=>{e.addEventListener(ev||'input',()=>{o[k]=g?g(e):e.value});return e};
const fT=(o,k,max)=>{const e=el('input');e.type='text';e.value=o[k]??'';if(max)e.maxLength=max;return bindv(o,k,e)};
const fA=(o,k)=>{const e=el('textarea');e.value=o[k]??'';return bindv(o,k,e)};
const fN=(o,k,a,b)=>{const e=el('input');e.type='number';e.min=a;e.max=b;e.value=o[k];e.title='Max answer length';return bindv(o,k,e,'input',x=>x.value===''?a:Math.max(a,Math.min(b,Number(x.value))))};
const fS=(o,k,opts)=>{const e=el('select');opts.forEach(([a,b])=>e.add(new Option(b,a)));e.value=o[k];return bindv(o,k,e,'change')};
const fW=(o,k,after)=>{const w=el('div','sw');w.innerHTML='<input type="checkbox" role="switch"><span></span>';const i=w.firstChild;i.checked=!!o[k];i.onchange=()=>{o[k]=i.checked;if(after)after()};return w};
const fR=(p,l,h,c,col)=>{const r=el('div','row'+(col?' col':'')),d=el('div');d.append(el('label','',l),el('small','',h||''));r.append(d,c);p.append(r);return r};
const fC=(o,k,list)=>{const b=el('div','chips');list.forEach(x=>{const l=el('label'),c=el('input');c.type='checkbox';c.checked=o[k].includes(x.id);c.onchange=()=>{o[k]=c.checked?[...o[k],x.id]:o[k].filter(i=>i!==x.id)};l.append(c,x.name);b.append(l)});return b};
const optsOf=(list,none,pre)=>[['',none],...list.map(x=>[x.id,(pre||'')+x.name])];
const newQ=()=>({label:'',style:'short',required:true,placeholder:'',max_length:100});
const newBtn=()=>({label:'Open Ticket',emoji:'\ud83c\udfab',style:'primary',form_enabled:true,form_title:'Open a ticket',questions:[{label:'What do you need help with?',style:'short',required:true,placeholder:'',max_length:100}],ticket_subject:'',ticket_message:''});
const newPanel=()=>({id:'',name:'Support',embed_title:'Need Help?',embed_description:'Press a button below to open a private ticket with our staff.',embed_color:'#e0222d',category:'',support_roles:[],log_channel:'',max_open:1,ticket_message:'',channel:'',buttons:[newBtn()]});
const PJ=(m,b)=>({method:m,headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
async function renderPanels(){
  CTL=null;touch();const f=$('fields');f.innerHTML='';$('secTitle').textContent='Ticket panels';
  skel(f,3);let P;try{P=await api(`/api/guilds/${G.id}/panels`)}catch(e){f.innerHTML='';f.append(el('div','empty',e.message));return}f.innerHTML='';
  const top=el('div','bar-top'),nb=el('button','btn pri','New panel');nb.onclick=()=>panelEditor(null);
  top.append(el('p','hint','A panel is a message with buttons. Every button can ask its own form, then opens a ticket.'),nb);f.append(top);
  if(!P.length){f.append(el('div','empty','No panels yet. Create your first one.'));return}
  const grid=el('div','pgrid');
  P.forEach(p=>{const c=el('div','pcard'),a=el('div','acts'),ed=el('button','btn ghost sm','Edit'),dl=el('button','btn ghost sm','Delete');
    c.append(el('b','',p.name),el('small','',`${p.buttons.length} button${p.buttons.length>1?'s':''}, ${p.message_id?'posted':'not posted yet'}`));
    ed.onclick=()=>panelEditor(p);
    dl.onclick=async()=>{if(!confirm('Delete this panel and its message?'))return;try{await api(`/api/guilds/${G.id}/panels/${p.id}`,{method:'DELETE'});renderPanels()}catch(e){alert(e.message)}};
    a.append(ed,dl);c.append(a);grid.append(c)});
  f.append(grid);
}
function btnCard(D,bt,i,redraw){
  const c=el('div','bcard'),hd=el('div','bhead'),pv=el('span','dbtn');
  const upd=()=>{pv.className='dbtn '+bt.style;pv.textContent=((bt.emoji||'')+' '+(bt.label||'')).trim()};upd();hd.append(pv);
  if(D.buttons.length>1){const rm=el('button','btn ghost sm','Remove button');rm.onclick=()=>{D.buttons.splice(i,1);redraw();touch()};hd.append(rm)}c.append(hd);
  const b=el('div','sec'),lb=fT(bt,'label',80),em=fT(bt,'emoji',32),st=fS(bt,'style',[['primary','Blue'],['secondary','Grey'],['success','Green'],['danger','Red']]);
  [lb,em].forEach(e=>e.addEventListener('input',upd));st.addEventListener('change',upd);
  fR(b,'Button text','',lb);fR(b,'Emoji','Optional',em);fR(b,'Color','',st);
  const fm=el('div'),draw=()=>{fm.innerHTML='';
    if(bt.form_enabled){fR(fm,'Form title','Top of the form, 45 characters max',fT(bt,'form_title',45));
      fm.append(el('h3','','Questions (Discord allows up to 5 per form)'));
      bt.questions.forEach((q,qi)=>{const qc=el('div','qcard'),t2=el('div','two'),t3=el('div','two'),tr=el('div','qrow');
        const ql=fT(q,'label',45),qp=fT(q,'placeholder',100);ql.placeholder='Question';qp.placeholder='Hint text (optional)';t2.append(ql,qp);
        t3.append(fS(q,'style',[['short','Short answer'],['paragraph','Long answer']]),fN(q,'max_length',1,4000));
        const rq=el('label','','Required');rq.prepend(fW(q,'required'));const rm=el('button','btn ghost sm','Remove');rm.onclick=()=>{bt.questions.splice(qi,1);draw();touch()};
        tr.append(rq,rm);qc.append(t2,t3,tr);fm.append(qc)});
      if(bt.questions.length<5){const ad=el('button','btn ghost sm','Add question');ad.style.margin='14px 0';ad.onclick=()=>{bt.questions.push(newQ());draw();touch()};fm.append(ad)}
    }else fR(fm,'Ticket subject','Used as the subject because no form is shown',fT(bt,'ticket_subject',100));
    fR(fm,'Message inside the ticket','Optional, replaces the panel one for this button',fA(bt,'ticket_message'),1)};
  fR(b,'Ask a form first','Turn off to open the ticket instantly',fW(bt,'form_enabled',draw));b.append(fm);draw();c.append(b);return c;
}
function panelEditor(p){
  const D=p?clone(p):newPanel(),f=$('fields');f.innerHTML='';$('secTitle').textContent=p?'Edit panel':'New panel';
  const back=el('button','btn ghost sm','Back to panels');back.onclick=()=>{if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;renderPanels()};f.append(back);
  let base=p?clone(p):null,snap=JSON.stringify(D);
  const saveD=async()=>{const r=await api(`/api/guilds/${G.id}/panels/${D.id||'new'}`,PJ('PUT',D));D.id=r.id;base=clone(D);snap=JSON.stringify(D)};
  CTL={dirty:()=>JSON.stringify(D)!==snap,save:saveD,reset:()=>panelEditor(base)};touch();
  f.append(el('h3','','Panel message'));let b=el('div','sec');
  fR(b,'Panel name','Only you see this',fT(D,'name',60));fR(b,'Title','',fT(D,'embed_title',200));fR(b,'Description','',fA(D,'embed_description'),1);
  const col=el('input');col.type='color';col.value=D.embed_color;fR(b,'Color','',bindv(D,'embed_color',col));f.append(b);
  f.append(el('h3','','Tickets'));b=el('div','sec');
  fR(b,'Category','New ticket channels are created here',(()=>{const e=fS(D,'category',optsOf(META.categories,'None'));return e})());
  fR(b,'Support roles','Who can see these tickets',fC(D,'support_roles',META.roles),1);
  fR(b,'Log channel','Transcripts and events',fS(D,'log_channel',optsOf(META.channels,'None','#')));
  fR(b,'Max open per member','',fN(D,'max_open',1,10));fR(b,'Message inside new tickets','',fA(D,'ticket_message'),1);f.append(b);
  f.append(el('h3','','Buttons (up to 25)'));const bw=el('div');f.append(bw);
  const draw=()=>{bw.innerHTML='';D.buttons.forEach((bt,i)=>bw.append(btnCard(D,bt,i,draw)));
    if(D.buttons.length<25){const ad=el('button','btn ghost','Add button');ad.style.marginTop='14px';ad.onclick=()=>{D.buttons.push(newBtn());draw();touch()};bw.append(ad)}};draw();
  f.append(el('h3','','Publish'));b=el('div','sec');const chs=el('select');optsOf(META.channels,'Choose a channel','#').forEach(([a,n])=>chs.add(new Option(n,a)));chs.value=D.channel||'';
  fR(b,'Post in channel','Posting again updates the same message',chs);f.append(b);
  const out=el('div','toast'),ps=el('button','btn pri','Post to channel');
  const acts=el('div','acts');acts.style.margin='16px 0';acts.append(ps,out);f.append(acts);
  ps.onclick=async()=>{out.className='toast';out.textContent='Posting...';
    try{if(!chs.value)throw new Error('Choose a channel first');await saveD();await api(`/api/guilds/${G.id}/panels/${D.id}/post`,PJ('POST',{channel:chs.value}));
      D.channel=chs.value;base=clone(D);snap=JSON.stringify(D);out.textContent='Posted. Members can use the buttons now.';touch()}
    catch(e){out.className='toast bad';out.textContent=e.message}};
}
const SPIDER='<svg viewBox="0 0 48 48" width="48" height="48"><g class="lg l"><path d="M20 24L9 14L4 20M20 27L7 27L3 34M20 30L9 40L6 46M21 21L14 8L9 8"/></g><g class="lg r"><path d="M28 24L39 14L44 20M28 27L41 27L45 34M28 30L39 40L42 46M27 21L34 8L39 8"/></g><ellipse cx="24" cy="29" rx="6.5" ry="9" class="bd"/><circle cx="24" cy="18.5" r="4.6" class="bd"/></svg>';
const spl=t=>{const d=el('div','spl');d.innerHTML=`<div class="thr"><i></i>${SPIDER}</div>`;d.append(el('small','',t||'Loading'));return d};
const skel=f=>f.append(spl('Loading'));
function showBoot(t){const b=$('boot');b.innerHTML='';b.append(spl(t));b.classList.remove('gone')}
function hideBoot(){$('boot').classList.add('gone')}
$('keep').onchange=()=>{$('dclogin').href='/auth/login?remember='+($('keep').checked?1:0)};
$('dclogin').onclick=()=>showBoot('Taking you to Discord');
function moveGlide(){const nv=$('nav');let g=nv.querySelector('.glide');if(!g){g=el('div','glide');nv.prepend(g)}const b=nv.querySelector('button.on');if(!b){g.style.opacity=0;return}g.style.opacity=1;g.style.height=b.offsetHeight+'px';g.style.transform=`translateY(${b.offsetTop}px)`}
const KW={rroles:'reaction roles button roles self roles dropdown emoji react get role',giveaways:'giveaway prize winner raffle draw reroll',invites:'invite tracker who invited join code',inviters:'invites leaderboard top inviters',leave:'goodbye leave left farewell',joinroles:'auto role join verify account age new members',automod:'spam links invites caps bad words filter punishment',welcome:'greet join leave goodbye autorole embed',leveling:'xp rank level rewards voice',moderation:'log cases ban kick mute',utility:'suggest suggestions',tickets:'ticket category support roles',panels:'ticket panel button form question',otickets:'tickets claimed open',board:'leaderboard top xp',cases:'cases warn ban mute kick history search',announce:'announcement broadcast',messages:'template embed button custom message send'};
function fsearch(){const q=$('fsearch').value.trim().toLowerCase(),r=$('fsres');r.innerHTML='';if(!q){r.classList.remove('open');return}
  const hits=TABS.filter(([k,t])=>(t+' '+(KW[k]||'')+' '+(DESC[k]||'')).toLowerCase().includes(q)).slice(0,6);
  if(!hits.length){const d=el('div','empty','No feature found');d.style.padding='14px';r.append(d)}
  hits.forEach(([k,t])=>{const b=el('button');b.append(el('span','ico',ICON[k]),el('span','',t));b.onclick=()=>{$('fsearch').value='';r.classList.remove('open');$('fs').classList.remove('open');switchTab(k)};r.append(b)});r.classList.add('open')}
$('fsearch').oninput=fsearch;$('fsearch').onkeydown=e=>{if(e.key==='Enter'){const b=$('fsres').querySelector('button');if(b)b.click()}};
addEventListener('keydown',e=>{const t=document.activeElement.tagName;if(e.key==='/'&&t!=='INPUT'&&t!=='TEXTAREA'&&t!=='SELECT'&&!$('panel').classList.contains('hide')){e.preventDefault();$('fsearch').focus()}if(e.key==='Escape'){drawer(false);$('fsres').classList.remove('open');$('fs').classList.remove('open');$('umenu').classList.remove('open');$('fsearch').blur()}});
document.addEventListener('click',e=>{if(!e.target.closest('.fs'))$('fsres').classList.remove('open');const b=e.target.closest('.btn');if(b){const r=b.getBoundingClientRect(),d=Math.max(r.width,r.height),x=el('span','ripple');x.style.cssText=`width:${d}px;height:${d}px;left:${e.clientX-r.left-d/2}px;top:${e.clientY-r.top-d/2}px`;b.append(x);setTimeout(()=>x.remove(),650)}});
document.addEventListener('pointermove',e=>{const t=e.target.closest&&e.target.closest('.srv,.stat,.pcard,.qa button,.sec,.bcard');if(t){const r=t.getBoundingClientRect();t.style.setProperty('--mx',(e.clientX-r.left)+'px');t.style.setProperty('--my',(e.clientY-r.top)+'px')}});
async function renderCases(){
  CTL=null;touch();const f=$('fields');f.innerHTML='';skel(f,4);let C;
  try{C=await api(`/api/guilds/${G.id}/cases`)}catch(e){f.innerHTML='';f.append(el('div','empty',e.message));return}
  f.innerHTML='';const q=el('input');q.type='search';q.placeholder='Search by case ID, user or reason';q.style.maxWidth='none';
  const chips=el('div','chips fl'),cnt=el('p','hint'),holder=el('div');let ft='';
  const draw=()=>{const s=q.value.trim().toLowerCase();holder.innerHTML='';
    const L=C.filter(c=>(!ft||c.type===ft)&&(!s||`${c.id} ${c.user} ${c.user_id} ${c.reason} ${c.mod}`.toLowerCase().includes(s)));
    cnt.textContent=`Found ${L.length} case${L.length===1?'':'s'}.`;
    if(!L.length){holder.append(el('div','empty','No cases match.'));return}
    const box=el('div','sec');L.slice(0,100).forEach(c=>{const r=el('div','row case'),d=el('div');
      d.append(el('label','',`#${c.id}  ${c.type.toUpperCase()}  ${c.user}`),el('small','',`${c.reason||'No reason provided'}, ${when(c.time)}`));
      r.append(el('span','dot'+(c.active?' on':'')),d,el('span','go','Open \u2192'));r.onclick=()=>caseDetail(c,renderCases);box.append(r)});holder.append(box)};
  ['All',...new Set(C.map(c=>c.type))].forEach(t=>{const l=el('label'),i=el('input');i.type='radio';i.name='ct';i.checked=t==='All';i.onchange=()=>{ft=t==='All'?'':t;draw()};l.append(i,t);chips.append(l)});
  q.oninput=draw;f.append(q,chips,cnt,holder);draw();
}
function caseDetail(c,back){
  const f=$('fields');f.innerHTML='';$('secTitle').textContent=`Case #${c.id}`;
  const bk=el('button','btn ghost sm','Back to cases'),dl=el('button','btn ghost sm','Delete case'),top=el('div','bar-top');
  bk.onclick=()=>{if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;back()};
  dl.onclick=async()=>{if(!confirm('Delete this case permanently?'))return;try{await api(`/api/guilds/${G.id}/cases/${c.id}`,{method:'DELETE'});CTL=null;back()}catch(e){alert(e.message)}};
  top.append(bk,dl);f.append(top);const box=el('div','sec');box.style.marginTop='16px';
  const kv=(k,v)=>{const r=el('div','row');r.append(el('label','',k),el('span','',v));box.append(r)};
  kv('Case ID','#'+c.id);kv('Type',c.type.toUpperCase());kv('Status',c.active?'Active':'Closed');kv('User',`${c.user} (${c.user_id})`);kv('Moderator',c.mod);kv('Created',when(c.time));
  const ta=el('textarea');ta.value=c.reason;ta.maxLength=500;fR(box,'Reason','Edit it, then use the Save popup',ta,1);f.append(box);
  let saved=c.reason;CTL={dirty:()=>ta.value!==saved,save:async()=>{await api(`/api/guilds/${G.id}/cases/${c.id}`,PJ('PUT',{reason:ta.value}));c.reason=ta.value;saved=ta.value},reset:()=>caseDetail(c,back)};touch();
}
const newEmbed=()=>({title:'',description:'',color:'#e0222d',author:'',footer:'',image:'',thumbnail:'',fields:[]});
const newTpl=()=>({id:'',name:'My message',content:'',embeds:[{...newEmbed(),title:'Hello!',description:'Write your message here.'}],buttons:[],channel:'',message_id:''});
async function renderMessages(){
  CTL=null;PREV=null;touch();const f=$('fields');f.innerHTML='';$('secTitle').textContent='Messages';skel(f,3);let P;
  try{P=await api(`/api/guilds/${G.id}/templates`)}catch(e){f.innerHTML='';f.append(el('div','empty',e.message));return}
  f.innerHTML='';const top=el('div','bar-top'),nb=el('button','btn pri','Create template');nb.onclick=()=>tplEditor(null);
  top.append(el('p','hint','Templates are reusable messages with embeds and link buttons. Send them to any channel.'),nb);f.append(top);
  if(!P.length){f.append(el('div','empty','No templates yet. Create your first one.'));return}
  const grid=el('div','pgrid');
  P.forEach(p=>{const c=el('div','pcard'),a=el('div','acts'),ed=el('button','btn ghost sm','Edit'),dl=el('button','btn ghost sm','Delete');
    c.append(el('b','',p.name),el('small','',`${p.embeds.length} embed${p.embeds.length===1?'':'s'}, ${p.buttons.length} button${p.buttons.length===1?'':'s'}, ${p.message_id?'sent':'not sent yet'}`));
    ed.onclick=()=>tplEditor(p);dl.onclick=async()=>{if(!confirm('Delete this template? Messages already sent stay in Discord.'))return;try{await api(`/api/guilds/${G.id}/templates/${p.id}`,{method:'DELETE'});renderMessages()}catch(e){alert(e.message)}};
    a.append(ed,dl);c.append(a);grid.append(c)});f.append(grid);
}
function embCard(D,e,i,redraw){
  const c=el('div','bcard'),hd=el('div','bhead');hd.append(el('b','',`Embed ${i+1}`));
  if(D.embeds.length>1){const rm=el('button','btn ghost sm','Remove');rm.onclick=()=>{D.embeds.splice(i,1);redraw();touch()};hd.append(rm)}c.append(hd);
  const b=el('div','sec'),col=el('input');col.type='color';col.value=e.color;
  fR(b,'Title','',fT(e,'title',256));fR(b,'Description','',fA(e,'description'),1);fR(b,'Color','',bindv(e,'color',col));
  fR(b,'Author','Small text above the title',fT(e,'author',256));fR(b,'Footer','',fT(e,'footer',2048));
  fR(b,'Thumbnail link','https:// image, shown top right',fT(e,'thumbnail',500));fR(b,'Big image link','https:// image, shown at the bottom',fT(e,'image',500));
  const fw=el('div'),drawF=()=>{fw.innerHTML='';
    e.fields.forEach((x,k)=>{const q=el('div','qcard'),t=el('div','two'),n=fT(x,'name',256),v=fT(x,'value',1024),tr=el('div','qrow'),il=el('label','','Side by side'),rm=el('button','btn ghost sm','Remove');
      n.placeholder='Field name';v.placeholder='Field value';t.append(n,v);il.prepend(fW(x,'inline'));rm.onclick=()=>{e.fields.splice(k,1);drawF();touch()};tr.append(il,rm);q.append(t,tr);fw.append(q)});
    if(e.fields.length<10){const ad=el('button','btn ghost sm','Add field');ad.style.margin='12px 0';ad.onclick=()=>{e.fields.push({name:'',value:'',inline:false});drawF();touch()};fw.append(ad)}};
  drawF();b.append(fw);c.append(b);return c;
}
function tplEditor(p){
  const D=p?clone(p):newTpl(),f=$('fields');f.innerHTML='';$('secTitle').textContent=p?'Edit template':'New template';
  const back=el('button','btn ghost sm','Back to templates');back.onclick=()=>{if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;renderMessages()};f.append(back);
  let base=p?clone(p):null,snap=JSON.stringify(D);
  const saveD=async()=>{const r=await api(`/api/guilds/${G.id}/templates/${D.id||'new'}`,PJ('PUT',D));D.id=r.id;base=clone(D);snap=JSON.stringify(D)};
  CTL={dirty:()=>JSON.stringify(D)!==snap,save:saveD,reset:()=>tplEditor(base)};
  const grid=el('div','tgrid'),L=el('div'),R=el('div','prev'),pv=el('div','dmsg');grid.append(L,R);R.append(el('h3','','Preview'),pv);f.append(grid);
  PREV=()=>{pv.innerHTML='';const hd=el('div','dhead'),im=el('img');im.src='/spidey-pfp.jpg?v=2';im.alt='';hd.append(im,el('b','','Spidey'),el('span','tag','BOT'));pv.append(hd);
    if(D.content)pv.append(el('div','dtext',D.content));
    D.embeds.forEach(e=>{const bx=el('div','emb'),bd=el('div','embody');bx.style.borderLeftColor=e.color;
      if(e.author)bd.append(el('div','eau',e.author));if(e.title)bd.append(el('div','eti',e.title));if(e.description)bd.append(el('div','ede',e.description));
      if(e.fields.length){const g=el('div','efs');e.fields.forEach(x=>{const c=el('div','ef'+(x.inline?' in':''));c.append(el('b','',x.name||'Name'),el('div','',x.value||'Value'));g.append(c)});bd.append(g)}
      if((e.image||'').startsWith('https://')){const i=el('img','eim');i.src=e.image;bd.append(i)}
      if(e.footer)bd.append(el('div','eft',e.footer));bx.append(bd);
      if((e.thumbnail||'').startsWith('https://')){const t=el('img','eth');t.src=e.thumbnail;bx.append(t)}pv.append(bx)});
    if(D.buttons.length){const r=el('div','dbtns');D.buttons.forEach(x=>r.append(el('span','dbtn secondary',`${x.emoji||''} ${x.label||'Button'} \u2197`.trim())));pv.append(r)}};
  let b=el('div','sec');fR(b,'Template name','Only you see this',fT(D,'name',60));fR(b,'Message text','Shown above the embeds',fA(D,'content'),1);L.append(el('h3','','Message'),b);
  L.append(el('h3','','Embeds (up to 3)'));const ew=el('div');L.append(ew);
  const drawE=()=>{ew.innerHTML='';D.embeds.forEach((e,i)=>ew.append(embCard(D,e,i,drawE)));
    if(D.embeds.length<3){const ad=el('button','btn ghost','Add embed');ad.style.marginTop='14px';ad.onclick=()=>{D.embeds.push(newEmbed());drawE();touch()};ew.append(ad)}};drawE();
  L.append(el('h3','','Link buttons (up to 5)'));const bw=el('div');L.append(bw);
  const drawB=()=>{bw.innerHTML='';
    D.buttons.forEach((x,i)=>{const r=el('div','qcard'),t2=el('div','two'),la=fT(x,'label',80),em=fT(x,'emoji',32),ur=fT(x,'url',500),rm=el('button','btn ghost sm','Remove');
      la.placeholder='Button text';em.placeholder='Emoji (optional)';ur.placeholder='https://...';t2.append(la,em);rm.onclick=()=>{D.buttons.splice(i,1);drawB();touch()};r.append(t2,ur,rm);bw.append(r)});
    if(D.buttons.length<5){const ad=el('button','btn ghost','Add button');ad.style.marginTop='14px';ad.onclick=()=>{D.buttons.push({label:'',url:'',emoji:''});drawB();touch()};bw.append(ad)}};drawB();
  L.append(el('h3','','Send'));b=el('div','sec');const chs=el('select');optsOf(META.channels,'Choose a channel','#').forEach(([a,n])=>chs.add(new Option(n,a)));chs.value=D.channel||'';
  fR(b,'Channel','Sending again to the same channel updates the message',chs);L.append(b);
  const out=el('div','toast'),ps=el('button','btn pri','Send message'),acts=el('div','acts');acts.style.margin='16px 0';acts.append(ps,out);L.append(acts);
  ps.onclick=async()=>{out.className='toast';out.textContent='Sending...';
    try{if(!chs.value)throw new Error('Choose a channel first');await saveD();await api(`/api/guilds/${G.id}/templates/${D.id}/send`,PJ('POST',{channel:chs.value}));
      D.channel=chs.value;base=clone(D);snap=JSON.stringify(D);out.textContent='Sent!';touch()}catch(e){out.className='toast bad';out.textContent=e.message}};
  touch();
}
const rel=t=>{const d=t*1000-Date.now(),m=Math.round(Math.abs(d)/60000),hh=Math.floor(m/60),dd=Math.floor(hh/24),x=dd?`${dd}d ${hh%24}h`:hh?`${hh}h ${m%60}m`:`${m}m`;return d>0?`in ${x}`:`${x} ago`};
async function renderGiveaways(){
  CTL=null;PREV=null;touch();const f=$('fields');f.innerHTML='';$('secTitle').textContent='Giveaways';skel(f);let L;
  try{L=await api(`/api/guilds/${G.id}/giveaways`)}catch(e){f.innerHTML='';f.append(el('div','empty',e.message));return}
  f.innerHTML='';const top=el('div','bar-top'),nb=el('button','btn pri','Start giveaway');nb.onclick=gwForm;
  top.append(el('p','hint','Members enter with a button. Spidey picks the winners when the timer ends.'),nb);f.append(top);
  if(!L.length){f.append(el('div','empty','No giveaways yet. Start your first one.'));return}
  const grid=el('div','pgrid');
  L.forEach(x=>{const c=el('div','pcard'),a=el('div','acts'),on=x.status==='active';
    c.append(el('b','',x.prize),el('small','',`${on?'Ends ':'Ended '}${rel(x.end)}`),el('small','',`${x.entries} entr${x.entries===1?'y':'ies'}, ${x.winners} winner${x.winners===1?'':'s'}, in #${x.channel}`));
    if(!on)c.append(el('small','',x.winner_names.length?'Won by '+x.winner_names.join(', '):'Nobody entered'));
    const act=(label,url,method,ask)=>{const b=el('button','btn ghost sm',label);b.onclick=async()=>{if(!confirm(ask))return;try{await api(url,{method});renderGiveaways()}catch(e){alert(e.message)}};a.append(b)};
    const base=`/api/guilds/${G.id}/giveaways/${x.id}`;
    if(on){act('End now',base+'/end','POST','End this giveaway now and pick winners?');act('Cancel',base,'DELETE','Cancel this giveaway? No winners will be picked.')}
    else{act('Reroll',base+'/reroll','POST','Pick new winners?');act('Delete',base,'DELETE','Remove this giveaway from the list?')}
    c.append(a);grid.append(c)});f.append(grid);
}
function gwForm(){
  CTL=null;PREV=null;touch();const f=$('fields');f.innerHTML='';$('secTitle').textContent='Start a giveaway';
  const D={prize:'',description:'',winners:1,dur:1,unit:'hours',channel:'',role:''},back=el('button','btn ghost sm','Back to giveaways');back.onclick=renderGiveaways;f.append(back);
  const b=el('div','sec');b.style.marginTop='16px';
  fR(b,'Prize','What the winner gets',fT(D,'prize',200));fR(b,'Description','Optional extra details',fA(D,'description'),1);fR(b,'Winners','How many people win',fN(D,'winners',1,20));
  const dr=el('div','two');dr.style.maxWidth='300px';dr.append(fN(D,'dur',1,999),fS(D,'unit',[['minutes','Minutes'],['hours','Hours'],['days','Days']]));fR(b,'Duration','Between 1 minute and 30 days',dr);
  fR(b,'Channel','Where the giveaway is posted',fS(D,'channel',optsOf(META.channels,'Choose a channel','#')));
  fR(b,'Required role','Optional. Only members with this role can enter',fS(D,'role',optsOf(META.roles,'Anyone can enter')));f.append(b);
  const out=el('div','toast'),go=el('button','btn pri','Start giveaway'),acts=el('div','acts');acts.style.margin='16px 0';acts.append(go,out);f.append(acts);
  go.onclick=async()=>{out.className='toast';out.textContent='Starting...';
    try{if(!D.prize.trim())throw new Error('Write the prize first');if(!D.channel)throw new Error('Choose a channel');await api(`/api/guilds/${G.id}/giveaways`,PJ('POST',D));renderGiveaways()}
    catch(e){out.className='toast bad';out.textContent=e.message}};
}
async function renderInviters(){
  CTL=null;PREV=null;touch();const f=$('fields');f.innerHTML='';$('secTitle').textContent='Top inviters';skel(f);let L;
  try{L=await api(`/api/guilds/${G.id}/inviters`)}catch(e){f.innerHTML='';f.append(el('div','empty',e.message));return}
  f.innerHTML='';if(!L.length){f.append(el('div','empty','No invites tracked yet. Turn on the Invite tracker and new members will be counted.'));return}
  const top=Math.max(1,L[0].invites),box=el('div','sec');
  L.forEach((u,i)=>{const r=el('div','row'),d=el('div'),m=el('div','meter');d.append(el('label','',`${i+1}. ${u.name}`),el('small','',`${u.invites} invites (${u.total} joined, ${u.left} left)`));
    m.append(el('i'));m.firstChild.style.setProperty('--w',Math.max(4,u.invites/top*100)+'%');r.append(d,m);box.append(r)});f.append(box);
}
function tplPrev(t){
  const S={'{user}':'@Peter','{inviter}':'@Mary','{code}':'x7Kp2','{invites}':'12','{count}':'248','{server}':'Spidey HQ'};
  const sb=x=>Object.entries(S).reduce((a,[k,v])=>a.split(k).join(v),(x||'').replace(/\*\*/g,'').replace(/`/g,''));
  const d=el('div','dmsg mini'),bx=el('div','emb'),bd=el('div','embody');bx.style.borderLeftColor=t.color;
  if(t.author)bd.append(el('div','eau',sb(t.author)));if(t.title)bd.append(el('div','eti',sb(t.title)));if(t.desc)bd.append(el('div','ede',sb(t.desc)));
  if(t.fields.length){const g=el('div','efs');t.fields.forEach(([n,v])=>{const c=el('div','ef in');c.append(el('b','',sb(n)),el('div','',sb(v)));g.append(c)});bd.append(g)}
  if(t.footer)bd.append(el('div','eft',sb(t.footer)));bx.append(bd);if(t.thumb)bx.append(el('div','eth fake','P'));d.append(bx);return d}
const newItem=()=>({emoji:'',label:'',role:'',description:''});
const newRR=()=>({id:'',name:'Roles',mode:'buttons',title:'Pick your roles',description:'Tap a button below to get or remove a role.',color:'#e0222d',unique:false,items:[newItem()],channel:'',message_id:''});
const RRMODE={buttons:'Buttons',dropdown:'Dropdown menu',reactions:'Emoji reactions'};
async function renderRR(){
  CTL=null;PREV=null;touch();const f=$('fields');f.innerHTML='';$('secTitle').textContent='Reaction roles';skel(f);let P;
  try{P=await api(`/api/guilds/${G.id}/rroles`)}catch(e){f.innerHTML='';f.append(el('div','empty',e.message));return}
  f.innerHTML='';const top=el('div','bar-top'),nb=el('button','btn pri','New role panel');nb.onclick=()=>rrEditor(null);
  top.append(el('p','hint','Post a message where members pick their own roles. Choose buttons, a dropdown menu or emoji reactions.'),nb);f.append(top);
  if(!P.length){f.append(el('div','empty','No role panels yet. Create your first one.'));return}
  const grid=el('div','pgrid');
  P.forEach(p=>{const c=el('div','pcard'),a=el('div','acts'),ed=el('button','btn ghost sm','Edit'),dl=el('button','btn ghost sm','Delete');
    c.append(el('b','',p.name),el('small','',`${p.items.length} role${p.items.length===1?'':'s'}, ${RRMODE[p.mode]}, ${p.message_id?'posted':'not posted yet'}`));
    ed.onclick=()=>rrEditor(p);dl.onclick=async()=>{if(!confirm('Delete this panel and its message?'))return;try{await api(`/api/guilds/${G.id}/rroles/${p.id}`,{method:'DELETE'});renderRR()}catch(e){alert(e.message)}};
    a.append(ed,dl);c.append(a);grid.append(c)});f.append(grid);
}
function rrEditor(p){
  const D=p?clone(p):newRR(),f=$('fields');f.innerHTML='';$('secTitle').textContent=p?'Edit role panel':'New role panel';
  const back=el('button','btn ghost sm','Back to list');back.onclick=()=>{if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;renderRR()};f.append(back);
  let base=p?clone(p):null,snap=JSON.stringify(D);
  const saveD=async()=>{const r=await api(`/api/guilds/${G.id}/rroles/${D.id||'new'}`,PJ('PUT',D));D.id=r.id;base=clone(D);snap=JSON.stringify(D)};
  CTL={dirty:()=>JSON.stringify(D)!==snap,save:saveD,reset:()=>rrEditor(base)};
  const grid=el('div','tgrid'),L=el('div'),R=el('div','prev'),pv=el('div','dmsg');grid.append(L,R);R.append(el('h3','','Preview'),pv);f.append(grid);
  const roleName=id=>(META.roles.find(r=>r.id===id)||{}).name||'Role';
  PREV=()=>{pv.innerHTML='';const hd=el('div','dhead'),im=el('img');im.src='/spidey-pfp.jpg?v=2';im.alt='';hd.append(im,el('b','','Spidey'),el('span','tag','BOT'));pv.append(hd);
    const bx=el('div','emb'),bd=el('div','embody');bx.style.borderLeftColor=D.color;if(D.title)bd.append(el('div','eti',D.title));if(D.description)bd.append(el('div','ede',D.description));bx.append(bd);pv.append(bx);
    if(D.mode==='dropdown')pv.append(el('div','selmock','Choose your roles...  \u25be'));
    else{const r=el('div','dbtns');D.items.forEach(x=>r.append(el('span',D.mode==='buttons'?'dbtn secondary':'rx',D.mode==='buttons'?`${x.emoji||''} ${x.label||(x.emoji?'':roleName(x.role))}`.trim():(x.emoji||'?'))));pv.append(r)}};
  PREV();
  const hints={buttons:'Each role gets its own button. Tap to get the role, tap again to remove it.',dropdown:'One menu where members tick the roles they want.',reactions:'Members react to the message with an emoji to get the role.'};
  let b=el('div','sec');const col=el('input'),ms=fS(D,'mode',Object.entries(RRMODE)),hint=el('small','',hints[D.mode]);col.type='color';col.value=D.color;
  fR(b,'Panel name','Only you see this',fT(D,'name',60));fR(b,'Style','',ms);b.append(hint);
  fR(b,'One role at a time','Picking a new role removes the other roles from this panel',fW(D,'unique'));
  fR(b,'Title','',fT(D,'title',200));fR(b,'Description','',fA(D,'description'),1);fR(b,'Color','',bindv(D,'color',col));L.append(el('h3','','Message'),b);
  L.append(el('h3','','Roles'));const iw=el('div');L.append(iw);
  const drawI=()=>{iw.innerHTML='';hint.textContent=hints[D.mode];
    D.items.forEach((x,i)=>{const c=el('div','qcard'),t2=el('div','two'),em=fT(x,'emoji',64),lb=fT(x,'label',80),rs=fS(x,'role',optsOf(META.roles,'Choose a role'));
      em.placeholder='Emoji';lb.placeholder=D.mode==='reactions'?'Name (optional)':D.mode==='dropdown'?'Option text':'Button text (empty = emoji only)';t2.append(em,lb);c.append(t2,rs);
      if(D.mode==='dropdown'){const ds=fT(x,'description',100);ds.placeholder='Short description (optional)';c.append(ds)}
      if(D.items.length>1){const rm=el('button','btn ghost sm','Remove');rm.onclick=()=>{D.items.splice(i,1);drawI();touch()};c.append(rm)}iw.append(c)});
    if(D.items.length<(D.mode==='reactions'?20:25)){const ad=el('button','btn ghost','Add role');ad.style.marginTop='14px';ad.onclick=()=>{D.items.push(newItem());drawI();touch()};iw.append(ad)}};
  ms.addEventListener('change',drawI);drawI();
  L.append(el('h3','','Post'));b=el('div','sec');const chs=el('select');optsOf(META.channels,'Choose a channel','#').forEach(([a,n])=>chs.add(new Option(n,a)));chs.value=D.channel||'';
  fR(b,'Channel','Posting again updates the same message',chs);L.append(b);
  const out=el('div','toast'),ps=el('button','btn pri','Post to channel'),acts=el('div','acts');acts.style.margin='16px 0';acts.append(ps,out);L.append(acts);
  ps.onclick=async()=>{out.className='toast';out.textContent='Posting...';
    try{if(!chs.value)throw new Error('Choose a channel first');await saveD();await api(`/api/guilds/${G.id}/rroles/${D.id}/post`,PJ('POST',{channel:chs.value}));
      D.channel=chs.value;base=clone(D);snap=JSON.stringify(D);out.textContent='Posted. Members can pick their roles now.';touch()}catch(e){out.className='toast bad';out.textContent=e.message}};
  touch();
}
function webSvg(c){const n='http://www.w3.org/2000/svg',s=document.createElementNS(n,'svg');s.setAttribute('viewBox','0 0 400 400');if(c)s.setAttribute('class',c);
  const A=[0,18,36,54,72,90].map(a=>a*Math.PI/180);let d='';A.forEach(a=>d+=`M0 0L${400*Math.cos(a)} ${400*Math.sin(a)}`);
  for(let r=60;r<=400;r+=65){d+=`M${r} 0`;A.slice(1).forEach((a,i)=>{const m=(a+A[i])/2,q=r*.9;d+=`Q${q*Math.cos(m)} ${q*Math.sin(m)} ${r*Math.cos(a)} ${r*Math.sin(a)}`})}
  const p=document.createElementNS(n,'path');p.setAttribute('d',d);s.append(p);return s}
$('login').prepend(webSvg(),webSvg('r'));document.body.append(webSvg('bgweb'));
$('avb').onclick=e=>{e.stopPropagation();$('umenu').classList.toggle('open')};
$('fsbtn').onclick=e=>{e.stopPropagation();const f=$('fs');f.classList.toggle('open');if(f.classList.contains('open'))setTimeout(()=>$('fsearch').focus(),60)};
document.addEventListener('click',e=>{if(!e.target.closest('.um'))$('umenu').classList.remove('open');if(!e.target.closest('.fs,#fsbtn'))$('fs').classList.remove('open')});
$('menubtn').onclick=()=>drawer(true);$('scrim').onclick=()=>drawer(false);
addEventListener('resize',()=>{if(innerWidth>720)drawer(false)});
let tx=0,ty=0;addEventListener('touchstart',e=>{tx=e.touches[0].clientX;ty=e.touches[0].clientY},{passive:true});
addEventListener('touchend',e=>{const dx=e.changedTouches[0].clientX-tx,dy=e.changedTouches[0].clientY-ty;if(dx<-60&&Math.abs(dy)<Math.abs(dx)*.6)drawer(false)},{passive:true});
const setHH=()=>{const hd=document.querySelector('#app>header');if(hd)document.documentElement.style.setProperty('--hh',hd.offsetHeight+'px')};
addEventListener('resize',setHH);new ResizeObserver(setHH).observe(document.querySelector('#app>header'));
showBoot('Waking Spidey up');init0().catch(()=>{}).finally(()=>setTimeout(hideBoot,300));
</script></body></html>
'''


# ================= giveaways (bot side) =================
_GF, _IF = "giveaways.json", "invites.json"
def _jl(path):
    try:
        with open(path, encoding="utf8") as f: return json.load(f)
    except (FileNotFoundError, ValueError): return {}
def _js(path, d):
    with open(path, "w", encoding="utf8") as f: json.dump(d, f)

class GwView(discord.ui.View):
    def __init__(self, cog, k):
        super().__init__(timeout=None)
        b = discord.ui.Button(style=discord.ButtonStyle.primary, label="Enter", emoji="\U0001F389", custom_id=f"gw:{k}")
        async def cb(it): await cog.enter(it, k)
        b.callback = cb; self.add_item(b)

class Giveaways(commands.Cog):
    def __init__(self, bot): self.bot, self.data, self.dirty = bot, _jl(_GF), set()
    def g(self, guild_id): return self.data.setdefault(str(guild_id), {})
    def save(self): _js(_GF, self.data)

    async def cog_load(self):
        for items in self.data.values():
            for k, d in items.items():
                if d["status"] == "active" and d.get("message_id"): self.bot.add_view(GwView(self, k), message_id=int(d["message_id"]))
        self.loop.start()
    async def cog_unload(self): self.loop.cancel()

    def embed(self, d):
        ended, e = d["status"] != "active", discord.Embed(title="\U0001F389 " + d["prize"], color=0x2B2D31 if d["status"] != "active" else 0xE0222D)
        t = (d.get("description") or "") + ("\n\n" if d.get("description") else "")
        if ended:
            w = d.get("winners_ids", [])
            t += "**Winners:** " + (", ".join(f"<@{x}>" for x in w) if w else "No valid entries") + f"\n**Ended:** <t:{int(d['end'])}:R>"
        else:
            t += f"**Ends:** <t:{int(d['end'])}:R> (<t:{int(d['end'])}:f>)\n**Winners:** {d['winners']}"
            if d.get("role"): t += f"\n**Requires:** <@&{d['role']}>"
        e.description = t + f"\n**Hosted by:** <@{d['host_id']}>"
        e.set_footer(text=f"{len(d['entries'])} entries" + (" | Ended" if ended else ""))
        return e

    async def start(self, guild, ch, host_id, prize, desc, winners, seconds, role):
        k = secrets.token_hex(4)
        d = {"id": k, "channel": ch.id, "message_id": 0, "prize": prize, "description": desc, "winners": winners, "end": time.time() + seconds,
             "host_id": host_id, "role": role, "status": "active", "entries": [], "winners_ids": []}
        msg = await ch.send(embed=self.embed(d), view=GwView(self, k))
        d["message_id"] = msg.id; self.g(guild.id)[k] = d; self.save()
        return k

    async def enter(self, it, k):
        d = self.g(it.guild_id).get(k)
        if not d or d["status"] != "active" or time.time() >= d["end"]:
            return await it.response.send_message("This giveaway has ended.", ephemeral=True)
        if d.get("role") and not any(r.id == int(d["role"]) for r in it.user.roles):
            return await it.response.send_message(f"You need the <@&{d['role']}> role to enter.", ephemeral=True)
        if it.user.id in d["entries"]: d["entries"].remove(it.user.id); txt = "You left the giveaway."
        else: d["entries"].append(it.user.id); txt = "You're in! Good luck \U0001F340"
        self.save(); self.dirty.add((it.guild_id, k))
        await it.response.send_message(txt, ephemeral=True)

    async def finish(self, guild_id, k, reroll=False):
        d, guild = self.g(guild_id).get(k), self.bot.get_guild(int(guild_id))
        if not d or not guild: return
        pool = [u for u in d["entries"] if (m := guild.get_member(u)) and not m.bot]
        d["winners_ids"] = random.sample(pool, min(d["winners"], len(pool)))
        d["status"] = "ended"
        if not reroll: d["end"] = min(d["end"], time.time())
        self.save()
        ch, msg = guild.get_channel(int(d["channel"])), None
        if not ch: return
        try: msg = await ch.fetch_message(int(d["message_id"])); await msg.edit(embed=self.embed(d), view=None)
        except discord.HTTPException: pass
        win = ", ".join(f"<@{x}>" for x in d["winners_ids"])
        txt = (f"\U0001F389 {'New winner(s): ' if reroll else 'Congratulations '}{win}! You won **{d['prize']}**" if win else f"No valid entries for **{d['prize']}**, so nobody won.")
        try:
            if msg: await ch.send(txt, reference=msg)
            else: await ch.send(txt)
        except discord.HTTPException: pass
        for x in d["winners_ids"]:      # DM every winner
            try:
                m = guild.get_member(int(x))
                if m: await safe_dm(m, "You won! \U0001F389", f"**Server:** {guild.name}\n**Prize:** {d['prize']}", BLUE)
            except Exception: pass

    @tasks.loop(seconds=15)
    async def loop(self):
        now = time.time()
        for gid, items in list(self.data.items()):
            for k, d in list(items.items()):
                if d["status"] != "active": continue
                if d["end"] <= now: await self.finish(gid, k)
                elif (int(gid), k) in self.dirty:
                    self.dirty.discard((int(gid), k)); guild = self.bot.get_guild(int(gid)); ch = guild.get_channel(int(d["channel"])) if guild else None
                    try: msg = await ch.fetch_message(int(d["message_id"])); await msg.edit(embed=self.embed(d))
                    except (discord.HTTPException, AttributeError): pass
    @loop.before_loop
    async def _wait(self): await self.bot.wait_until_ready()

# ================= invite tracker (bot side) =================
class InviteTracker(commands.Cog):
    DEF = {"enabled": False, "channel": None, "template": "classic"}
    def __init__(self, bot): self.bot, self.data, self.cache = bot, _jl(_IF), {}
    def guild(self, gid):
        g = self.data.setdefault(str(gid), {})
        for k in ("cfg", "joins", "counts"): g.setdefault(k, {})
        return g
    def cfg(self, gid):
        c = self.guild(gid)["cfg"]
        for k, v in self.DEF.items(): c.setdefault(k, v)
        return c
    def save(self): _js(_IF, self.data)
    def total(self, gid, uid):
        c = self.guild(gid)["counts"].get(str(uid), {"total": 0, "left": 0}); return c["total"] - c["left"]

    async def snap(self, guild):
        try: self.cache[guild.id] = {i.code: (i.uses or 0) for i in await guild.invites()}
        except discord.HTTPException: self.cache.pop(guild.id, None)
    @commands.Cog.listener()
    async def on_ready(self):
        for g in self.bot.guilds: await self.snap(g)
    @commands.Cog.listener()
    async def on_guild_join(self, guild): await self.snap(guild)
    @commands.Cog.listener()
    async def on_invite_create(self, inv): self.cache.setdefault(inv.guild.id, {})[inv.code] = inv.uses or 0
    @commands.Cog.listener()
    async def on_invite_delete(self, inv): self.cache.get(inv.guild.id, {}).pop(inv.code, None)

    def fmt(self, tpl, m, inviter=None, code=None):
        return (tpl.replace("{user}", m.mention).replace("{inviter}", f"<@{inviter}>" if inviter else "Unknown").replace("{code}", code or "unknown")
                   .replace("{invites}", str(self.total(m.guild.id, inviter)) if inviter else "0").replace("{count}", str(m.guild.member_count)).replace("{server}", m.guild.name))
    def build(self, tid, m, inviter, code):
        t, f = next((x for x in INV_TPLS if x["id"] == tid), INV_TPLS[0]), (lambda x: self.fmt(x, m, inviter, code))
        e = discord.Embed(title=f(t["title"]) or None, description=f(t["desc"]) or None, color=int(t["color"].lstrip("#"), 16))
        if t["author"]: e.set_author(name=f(t["author"]), icon_url=m.display_avatar.url)
        for n, v in t["fields"]: e.add_field(name=f(n), value=f(v), inline=True)
        if t["footer"]: e.set_footer(text=f(t["footer"]))
        if t["thumb"]: e.set_thumbnail(url=m.display_avatar.url)
        return e

    @commands.Cog.listener()
    async def on_member_join(self, m):
        if m.bot: return
        used, old = None, self.cache.get(m.guild.id)
        try:
            now = {i.code: i for i in await m.guild.invites()}
            if old is not None: used = next((i for c, i in now.items() if (i.uses or 0) > old.get(c, 0)), None)
            self.cache[m.guild.id] = {c: (i.uses or 0) for c, i in now.items()}
        except discord.HTTPException: pass
        inviter = used.inviter.id if used and used.inviter else None
        g = self.guild(m.guild.id); g["joins"][str(m.id)] = {"inviter": inviter, "code": used.code if used else None}
        if inviter: g["counts"].setdefault(str(inviter), {"total": 0, "left": 0})["total"] += 1
        self.save()
        c = self.cfg(m.guild.id); ch = m.guild.get_channel(int(c["channel"])) if c["channel"] else None
        if c["enabled"] and ch:
            try: await ch.send(embed=self.build(c["template"], m, inviter, used.code if used else None), allowed_mentions=discord.AllowedMentions.none())
            except discord.HTTPException: pass
    @commands.Cog.listener()
    async def on_member_remove(self, m):
        if m.bot: return
        g = self.guild(m.guild.id); j = g["joins"].pop(str(m.id), None)
        if j and j["inviter"]: g["counts"].setdefault(str(j["inviter"]), {"total": 0, "left": 0})["left"] += 1
        self.save()

    @app_commands.command(name="invites", description="See how many people someone has invited")
    @app_commands.guild_only()
    async def invites(self, it: discord.Interaction, member: Optional[discord.Member] = None):
        m = member or it.user; c = self.guild(it.guild_id)["counts"].get(str(m.id), {"total": 0, "left": 0})
        await it.response.send_message(f"**{m.display_name}** has **{c['total'] - c['left']}** invites ({c['total']} joined, {c['left']} left).")

# ================= reaction roles (bot side) =================
_RF = "reactionroles.json"
def _norm(e): return str(e).replace("\ufe0f", "").strip()

class RRButtons(discord.ui.View):
    def __init__(self, cog, pid, p):
        super().__init__(timeout=None)
        for i, it in enumerate(p["items"]):
            b = discord.ui.Button(style=discord.ButtonStyle.secondary, label=it["label"] or None, emoji=it["emoji"] or None, custom_id=f"rr:{pid}:{i}", row=i // 5)
            async def cb(inter, i=i): await cog.toggle(inter, pid, i)
            b.callback = cb; self.add_item(b)

class RRSelect(discord.ui.View):
    def __init__(self, cog, pid, p):
        super().__init__(timeout=None)
        opts = [discord.SelectOption(label=(it["label"] or "Role")[:100], value=str(i), emoji=it["emoji"] or None, description=it.get("description") or None) for i, it in enumerate(p["items"])]
        sel = discord.ui.Select(custom_id=f"rr:{pid}:menu", placeholder="Choose your roles...", min_values=0, max_values=1 if p["unique"] else len(opts), options=opts)
        async def cb(inter): await cog.menu(inter, pid, sel.values)
        sel.callback = cb; self.add_item(sel)

class ReactionRoles(commands.Cog):
    def __init__(self, bot): self.bot, self.data = bot, _jl(_RF)
    def g(self, guild_id): return self.data.setdefault(str(guild_id), {})
    def save(self): _js(_RF, self.data)

    def message(self, pid, p):
        from theme import web_embed
        e = web_embed(p["title"], p["description"], int(p["color"].lstrip("#"), 16))
        return e, (RRButtons(self, pid, p) if p["mode"] == "buttons" else RRSelect(self, pid, p) if p["mode"] == "dropdown" else None)

    async def cog_load(self):
        for items in self.data.values():
            for pid, p in items.items():
                if p.get("message_id") and p["mode"] != "reactions": self.bot.add_view(self.message(pid, p)[1], message_id=int(p["message_id"]))

    async def publish(self, g, pid, p, ch):
        e, v = self.message(pid, p); msg = None
        if p.get("message_id") and p.get("channel") == ch.id:
            try: msg = await ch.fetch_message(int(p["message_id"])); await msg.edit(embed=e, view=v)
            except discord.NotFound: msg = None
        if msg is None: msg = await (ch.send(embed=e, view=v) if v else ch.send(embed=e))
        if p["mode"] == "reactions":
            try: await msg.clear_reactions()
            except discord.HTTPException: pass
            for it in p["items"]:
                try: await msg.add_reaction(it["emoji"])
                except discord.HTTPException: pass
        p["channel"], p["message_id"] = ch.id, msg.id; self.save()

    def panel_roles(self, guild, p): return [r for r in (guild.get_role(int(x["role"])) for x in p["items"]) if r]

    async def toggle(self, it, pid, i):
        await it.response.defer(ephemeral=True)
        p = self.g(it.guild_id).get(pid)
        role = it.guild.get_role(int(p["items"][i]["role"])) if p else None
        if not role: return await it.followup.send("That role no longer exists.", ephemeral=True)
        try:
            if role in it.user.roles: await it.user.remove_roles(role, reason="Reaction roles"); txt = f"Removed {role.mention}"
            else:
                if p["unique"]:
                    others = [r for r in self.panel_roles(it.guild, p) if r in it.user.roles and r != role]
                    if others: await it.user.remove_roles(*others, reason="Reaction roles")
                await it.user.add_roles(role, reason="Reaction roles"); txt = f"You now have {role.mention}"
        except discord.HTTPException: txt = "I can't manage that role. Ask an admin to move my role above it."
        await it.followup.send(txt, ephemeral=True)

    async def menu(self, it, pid, values):
        await it.response.defer(ephemeral=True)
        p = self.g(it.guild_id).get(pid)
        if not p: return await it.followup.send("This panel was removed.", ephemeral=True)
        chosen = {int(p["items"][int(v)]["role"]) for v in values}; allr = self.panel_roles(it.guild, p)
        add = [r for r in allr if r.id in chosen and r not in it.user.roles]; rem = [r for r in allr if r.id not in chosen and r in it.user.roles]
        try:
            if add: await it.user.add_roles(*add, reason="Reaction roles")
            if rem: await it.user.remove_roles(*rem, reason="Reaction roles")
            txt = "Your roles are updated."
        except discord.HTTPException: txt = "I can't manage one of those roles. Ask an admin to move my role higher."
        await it.followup.send(txt, ephemeral=True)

    async def react(self, pl, add):
        if not pl.guild_id or pl.user_id == self.bot.user.id: return
        p = next((x for x in self.g(pl.guild_id).values() if x["mode"] == "reactions" and str(x.get("message_id")) == str(pl.message_id)), None)
        item = next((x for x in p["items"] if _norm(x["emoji"]) == _norm(pl.emoji)), None) if p else None
        guild = self.bot.get_guild(pl.guild_id)
        if not item or not guild: return
        member, role = (pl.member if add and pl.member else guild.get_member(pl.user_id)), guild.get_role(int(item["role"]))
        if not member or not role or member.bot: return
        try:
            if add:
                if p["unique"]:
                    others = [r for r in self.panel_roles(guild, p) if r in member.roles and r != role]
                    if others: await member.remove_roles(*others, reason="Reaction roles")
                await member.add_roles(role, reason="Reaction roles")
            else: await member.remove_roles(role, reason="Reaction roles")
        except discord.HTTPException: pass
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, pl): await self.react(pl, True)
    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, pl): await self.react(pl, False)

# ================= bot extension =================

class Dashboard(commands.Cog):
    def __init__(self, bot): self.bot, self.runner = bot, None
    async def cog_load(self): self.runner = await start_server(self.bot)
    async def cog_unload(self):
        if self.runner: await self.runner.cleanup()

async def setup(bot):
    await bot.add_cog(Giveaways(bot)); await bot.add_cog(InviteTracker(bot)); await bot.add_cog(ReactionRoles(bot)); await bot.add_cog(Dashboard(bot))
