"""Hamsafar web dashboard (single file). Put this next to bot.py, then in bot.py: await self.load_extension("dashboard")
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
from automod import MODULES   # your automod.py

def F(key, label, type, hint="", path=None, group="", **kw):
    return dict(key=key, label=label, type=type, hint=hint, group=group, path=tuple(path or (key,)), **kw)

def _it(id, name, **kw):
    return {"id": id, "name": name, "title": "", "author": "", "desc": "", "fields": [], "footer": "", "thumb": False, "color": "#f2a47e", **kw}
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
        F("welcome_text", "Text above the embed", "text", "The only part that can ping the new member", max=500, group="Join"),
        F("welcome_title", "Embed title", "text", max=200, group="Join"),
        F("welcome_message", "Embed message", "area", "{user} {name} {server} {count} {line}", max=1800, group="Join"),
        F("welcome_footer", "Footer", "text", "Blank = Hamsafar default, __off__ = no footer", nullable=True, max=200, group="Join"),
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
            return J({"error": "You cannot manage this server, or Hamsafar is not in it"}, status=403)
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
        from theme import cozy_embed
        try: await ch.send(embed=cozy_embed(str(b.get("title", "")).strip()[:200] or "Announcement", msg))
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
               "embed_color": col if len(col) == 7 and col[0] == "#" else "#f2a47e", "max_open": max(1, min(10, int(d.get("max_open") or 1))), "ticket_message": t(d.get("ticket_message"), 1500), "buttons": []}
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
                "embed_color": p.get("embed_color", "#f2a47e"), "category": str(p.get("category") or ""), "support_roles": [str(x) for x in p.get("support_roles", [])],
                "log_channel": str(p.get("log_channel") or ""), "max_open": p.get("max_open", 1), "ticket_message": p.get("ticket_message", ""),
                "channel": str(p.get("channel") or ""), "message_id": str(p.get("message_id") or ""), "buttons": [{**btn, **b} for b in p.get("buttons") or [btn]]}

    def panel_msg(pid, p):
        from theme import cozy_embed
        from tickets import TicketPanel
        return cozy_embed(p["embed_title"], p["embed_description"], int(p["embed_color"].lstrip("#"), 16)), TicketPanel(p["button_label"], pid, p)

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
            out["embeds"].append({"title": t(e.get("title"), 256), "description": t(e.get("description"), 4000), "color": col if len(col) == 7 and col[0] == "#" else "#f2a47e",
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
               "color": col if len(col) == 7 and col[0] == "#" else "#f2a47e", "unique": bool(d.get("unique")), "items": []}
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

    async def logo(request): return web.Response(body=base64.b64decode(LOGO_B64), content_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    async def index(request): return web.Response(text=INDEX_HTML, content_type="text/html")
    app = web.Application(middlewares=[session_mw])
    app.add_routes([web.get("/", index), web.get("/auth/login", login), web.get("/auth/callback", callback), web.get("/auth/logout", logout),
        web.get("/api/config", config), web.get("/api/me", me), web.get("/api/schema", schema), web.get("/api/guilds", guilds),
        web.get("/api/guilds/{id}", guild_get), web.get("/api/guilds/{id}/overview", overview), web.get("/logo.jpg", logo), web.get("/api/guilds/{id}/rroles", rr_list), web.put("/api/guilds/{id}/rroles/{pid}", rr_put), web.delete("/api/guilds/{id}/rroles/{pid}", rr_delete), web.post("/api/guilds/{id}/rroles/{pid}/post", rr_post), web.get("/api/guilds/{id}/giveaways", gw_list), web.post("/api/guilds/{id}/giveaways", gw_start), web.post("/api/guilds/{id}/giveaways/{gid}/{act}", gw_act), web.delete("/api/guilds/{id}/giveaways/{gid}", gw_del), web.get("/api/guilds/{id}/inviters", inviters), web.get("/api/guilds/{id}/cases", cases_get), web.put("/api/guilds/{id}/cases/{cid}", case_put), web.delete("/api/guilds/{id}/cases/{cid}", case_delete), web.get("/api/guilds/{id}/templates", tpl_get), web.put("/api/guilds/{id}/templates/{tid}", tpl_put), web.delete("/api/guilds/{id}/templates/{tid}", tpl_delete), web.post("/api/guilds/{id}/templates/{tid}/send", tpl_send), web.get("/api/guilds/{id}/panels", panels_get), web.put("/api/guilds/{id}/panels/{pid}", panel_put), web.delete("/api/guilds/{id}/panels/{pid}", panel_delete), web.post("/api/guilds/{id}/panels/{pid}/post", panel_post), web.post("/api/guilds/{id}/announce", announce), web.put("/api/guilds/{id}/{sec}", section_put)])
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
    print(f"🌙 Dashboard running on port {port}")
    return runner

# ================= the page =================
LOGO_B64 = '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'
INDEX_HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hamsafar Dashboard</title><link rel="icon" href="/logo.jpg">
<link href="https://fonts.googleapis.com/css2?family=Nunito:wght@400;600;700;800&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box}:root{--bg:#1b1727;--panel:#251f35;--line:#3b3252;--tx:#f6ede4;--mu:#a99fb8;--ac:#f2a47e;--acx:#3d2d3f;--bad:#ff8a8a}
body{margin:0;color:var(--tx);font:15px/1.55 Nunito,system-ui,sans-serif;background:var(--bg) url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/><feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 .08 0'/></filter><rect width='100%' height='100%' filter='url(%23n)'/></svg>")}
.bgsky{position:fixed;right:-150px;top:-150px;width:600px;opacity:.16;pointer-events:none;z-index:0;transform:rotate(90deg);animation:breathe 9s ease-in-out infinite}
.bgsky path{stroke:#f2a47e;fill:none;stroke-width:1.2;stroke-dasharray:3000;stroke-dashoffset:3000;animation:draw 4s ease-out forwards}
#app>.wrap{position:relative;z-index:1}
button,input,select,textarea{font:inherit;color:inherit}:focus-visible{outline:2px solid var(--ac);outline-offset:2px}
.hide{display:none!important}
.btn{border:0;border-radius:10px;padding:11px 18px;font-weight:600;cursor:pointer;text-decoration:none;display:inline-block}
.dc{background:#5865F2;color:#fff;padding:14px 26px}.pri{background:var(--ac);color:#2a1f3a}.ghost{background:none;border:1px solid var(--line)}
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
@media(prefers-reduced-motion:reduce){*{transition:none!important;animation:none!important}.cord{height:clamp(10px,calc(50vh - 300px),300px)}.login .in{opacity:1!important;transform:none!important}.bgsky path{stroke-dashoffset:0}}
.login{min-height:100vh;display:grid;place-items:center;padding:24px;text-align:center;position:relative;overflow:hidden;color:#f6ede4;background:radial-gradient(90% 55% at 50% 112%,#f2a47e40,transparent 70%),radial-gradient(120% 90% at 50% 0%,#3b2f5c 0%,#1b1727 58%,#120f1c 100%)}
.login>svg{position:absolute;top:-50px;left:-70px;width:min(75vw,640px);opacity:.3;pointer-events:none}
.login>svg.r{left:auto;top:auto;right:-130px;bottom:-130px;transform:rotate(180deg)}
.login>svg path{stroke:#f2a47e;fill:none;stroke-width:1}
@keyframes rise{to{opacity:1;transform:none}}
.login .in{position:relative;opacity:0;transform:translateY(14px);animation:rise .7s 1.1s forwards}
.login .ur{font-size:1.6rem;color:var(--ac);margin:0 0 14px;opacity:.95}.login h1{font-size:clamp(3rem,11vw,5.6rem);font-weight:800;letter-spacing:-.02em;line-height:.95;margin:0 0 8px}
.login p{color:#b9aecb;margin:0 auto 28px;max-width:38ch}
.login small{display:block;margin-top:16px;color:#8b819e}
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
.cord{width:2px;height:0;background:linear-gradient(#e8def8,#f2a47e);animation:cord 1.5s cubic-bezier(.3,.7,.2,1) forwards}
.medal{width:112px;height:112px;border-radius:50%;object-fit:cover;border:3px solid #f2a47e;box-shadow:0 0 44px #f2a47e77;margin-top:-3px}
.logo{width:36px;height:36px;border-radius:50%;border:2px solid var(--ac);box-shadow:0 0 14px #f2a47e66}
header{background:#1b1727cc;backdrop-filter:blur(8px);position:sticky;top:0;z-index:5}
.pri{box-shadow:0 6px 22px #f2a47e44;transition:transform .15s,box-shadow .15s}.pri:hover{transform:translateY(-2px);box-shadow:0 10px 28px #f2a47e77}
.btn.ghost:hover{border-color:var(--ac)}
.srv{transition:transform .2s,border-color .2s,box-shadow .2s}.srv:hover{transform:translateY(-4px);border-color:var(--ac);box-shadow:0 12px 32px #f2a47e26}
nav button{transition:background .15s,color .15s,padding .15s}nav>button:hover{color:var(--tx);padding-left:16px}nav button.on{box-shadow:inset 3px 0 0 var(--ac)}
#fields>*{animation:pop .4s both}#fields>:nth-child(2){animation-delay:.05s}#fields>:nth-child(3){animation-delay:.1s}#fields>:nth-child(4){animation-delay:.15s}
.sec,.stat,.bcard,.pcard{transition:border-color .2s}.sec:hover,.stat:hover,.pcard:hover{border-color:#f2a47e66}
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
.pop{position:fixed;left:50%;bottom:24px;z-index:20;display:flex;align-items:center;gap:12px;padding:9px 9px 9px 22px;background:#2a2340f2;backdrop-filter:blur(12px);border:1px solid #f2a47e77;border-radius:99px;box-shadow:0 16px 50px #000a,0 0 30px #f2a47e33;transform:translate(-50%,180%);opacity:0;transition:transform .45s cubic-bezier(.2,1.3,.4,1),opacity .25s,border-color .2s;max-width:calc(100vw - 20px);white-space:nowrap}
.pop.show{transform:translate(-50%,0);opacity:1}.pop .btn{padding:9px 22px;border-radius:99px;min-width:84px;text-align:center}
#popmsg{font-weight:600;font-size:.9rem}.pop.ok{border-color:#3ddc84aa}.pop.ok #popmsg{color:#3ddc84}.pop.bad #popmsg{color:var(--bad)}
.dots{display:none;gap:5px;align-items:center;justify-content:center;height:20px}.dots i{width:7px;height:7px;border-radius:50%;background:#fff;animation:bounce 1s infinite}
.dots i:nth-child(2){animation-delay:.15s}.dots i:nth-child(3){animation-delay:.3s}
@keyframes bounce{0%,60%,100%{transform:translateY(0);opacity:.45}30%{transform:translateY(-6px);opacity:1}}
.pop.saving .dots{display:flex}.pop.saving #savetxt{display:none}.pop.saving .btn{pointer-events:none}.pop.saving #reset{opacity:.4}
@media(max-width:720px){.ng,.nsrv{display:none}.pop{gap:8px;padding-left:16px;bottom:14px}#popmsg{font-size:.8rem}.pop .btn{padding:8px 14px;min-width:0}}
.prog{position:fixed;top:0;left:0;height:3px;width:0;opacity:0;background:linear-gradient(90deg,#f2a47e,#b8a9d9);box-shadow:0 0 12px #f2a47e;z-index:60;transition:width .5s ease,opacity .3s}
.btn{position:relative;overflow:hidden}.btn:active{transform:scale(.97)}
.ripple{position:absolute;border-radius:50%;background:#ffffff55;transform:scale(0);animation:rip .6s ease-out forwards;pointer-events:none}
@keyframes rip{to{transform:scale(4);opacity:0}}
.srv,.stat,.pcard,.qa button,.sec,.bcard{position:relative;background-image:radial-gradient(260px circle at var(--mx,-999px) var(--my,-999px),#f2a47e24,transparent 62%)}
.glide{position:absolute;left:0;top:0;width:100%;height:0;border-radius:9px;background:var(--acx);box-shadow:inset 3px 0 0 var(--ac);transition:transform .38s cubic-bezier(.3,1.25,.4,1),height .3s,opacity .2s;pointer-events:none;animation:none!important}
nav>button{position:relative;z-index:1}nav button.on{background:none;box-shadow:none;color:var(--ac);font-weight:600}nav>*{animation:pop .4s both}
.sec>.row{animation:pop .35s both}.sec>.row:nth-child(1){animation-delay:0.03s}.sec>.row:nth-child(2){animation-delay:0.06s}.sec>.row:nth-child(3){animation-delay:0.09s}.sec>.row:nth-child(4){animation-delay:0.12s}.sec>.row:nth-child(5){animation-delay:0.15s}.sec>.row:nth-child(6){animation-delay:0.18s}.sec>.row:nth-child(7){animation-delay:0.21s}.sec>.row:nth-child(8){animation-delay:0.24s}.sec>.row:nth-child(9){animation-delay:0.27s}.sec>.row:nth-child(10){animation-delay:0.30s}.sec>.row:nth-child(11){animation-delay:0.33s}.sec>.row:nth-child(12){animation-delay:0.36s}
.skel{height:78px;border-radius:14px;margin-top:12px;background:linear-gradient(90deg,var(--panel) 25%,#352d4a 50%,var(--panel) 75%);background-size:200% 100%;animation:shim 1.2s linear infinite}
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
.emb{display:flex;gap:12px;background:#2b2d31;border-left:4px solid #f2a47e;border-radius:4px;padding:10px 12px;margin-top:8px}
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
.keep{display:inline-flex;align-items:center;gap:10px;margin-top:20px;cursor:pointer;color:#b9aecb;font-size:.9rem;user-select:none;position:relative}
.keep input{position:absolute;opacity:0;width:0;height:0}
.keep .box{width:22px;height:22px;border-radius:7px;border:2px solid #6b5f87;display:grid;place-items:center;transition:background .2s,border-color .2s,transform .2s,box-shadow .2s}
.keep .box svg{width:16px;height:16px;fill:none;stroke:#2a1f3a;stroke-width:3;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:24;stroke-dashoffset:24;transition:stroke-dashoffset .3s .05s}
.keep input:checked+.box{background:#f2a47e;border-color:#f2a47e;box-shadow:0 0 16px #f2a47e88}.keep input:checked+.box svg{stroke-dashoffset:0}
.keep:active .box{transform:scale(.86)}.keep input:focus-visible+.box{outline:2px solid #fff;outline-offset:2px}
.spl{display:grid;justify-items:center;gap:8px;padding:48px 0;color:var(--mu)}
.spl .thr{display:flex;flex-direction:column;align-items:center;transform-origin:50% 0;animation:bob 1.8s ease-in-out infinite}
.spl .thr i{width:2px;height:40px;background:linear-gradient(#e8def8,#f2a47e)}
.bd{fill:#f2a47e}.tw{fill:#fff1e0;transform-box:fill-box;transform-origin:center;animation:twinkle 1.6s ease-in-out infinite}
@keyframes twinkle{50%{opacity:.35;transform:scale(.6)}}@keyframes bob{0%,100%{transform:translateY(-16px)}50%{transform:translateY(8px)}}
.boot{position:fixed;inset:0;z-index:100;display:grid;place-items:center;background:#120f1c;color:#b9aecb;transition:opacity .45s}.boot.gone{opacity:0;pointer-events:none}
html{color-scheme:dark}
input[type=search]{-webkit-appearance:none;appearance:none;width:100%;background:#17121f url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23a69a9d' stroke-width='2.4' stroke-linecap='round'><circle cx='11' cy='11' r='7'/><path d='M20 20l-3.5-3.5'/></svg>") no-repeat 15px center/18px;color:var(--tx);-webkit-text-fill-color:var(--tx);caret-color:var(--ac);border:1px solid var(--line);border-radius:99px;padding:11px 16px 11px 44px;transition:border-color .2s,box-shadow .2s,background-color .2s}
input[type=search]::placeholder{color:var(--mu);-webkit-text-fill-color:var(--mu);opacity:1}
input[type=search]:hover{border-color:#f2a47e66}
input[type=search]:focus{outline:none;border-color:var(--ac);box-shadow:0 0 0 3px #f2a47e33;background-color:#1f1930}
input[type=search]::-webkit-search-decoration,input[type=search]::-webkit-search-results-button{display:none}
input[type=search]::-webkit-search-cancel-button{-webkit-appearance:none;width:18px;height:18px;border-radius:50%;cursor:pointer;background:#3b3252 url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23f2eced' stroke-width='3' stroke-linecap='round'><path d='M6 6l12 12M18 6L6 18'/></svg>") center/10px no-repeat}
input:-webkit-autofill,textarea:-webkit-autofill{-webkit-box-shadow:0 0 0 100px #17121f inset;-webkit-text-fill-color:var(--tx)}
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
  .fs.on.open{display:block;position:absolute;left:0;right:0;top:100%;width:auto;padding:10px 14px;background:#1b1727f5;border-bottom:1px solid var(--line);z-index:40;animation:pop .2s both}
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
.greet .nm{color:var(--ac);text-shadow:0 0 28px #f2a47e55}
.greet p{color:var(--mu);margin:0 0 22px}
.wave{display:inline-block;transform-origin:70% 75%;animation:wave 2.4s ease-in-out .6s 2}
@keyframes wave{0%,60%,100%{transform:rotate(0)}10%{transform:rotate(16deg)}20%{transform:rotate(-8deg)}30%{transform:rotate(16deg)}40%{transform:rotate(-4deg)}50%{transform:rotate(10deg)}}
.tplgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin-top:4px}
.tplcard{position:relative;display:grid;gap:10px;text-align:left;cursor:pointer;color:var(--tx);background:var(--bg);border:2px solid var(--line);border-radius:16px;padding:14px;transition:transform .2s,border-color .2s,box-shadow .2s}
.tplcard:hover{transform:translateY(-3px);border-color:#f2a47e88}
.tplcard.sel{border-color:var(--ac);box-shadow:0 0 0 3px #f2a47e33,0 12px 30px #f2a47e22}
.tplcard.sel::after{content:"\2713";position:absolute;top:10px;right:12px;width:22px;height:22px;border-radius:50%;background:var(--ac);color:#2a1f3a;display:grid;place-items:center;font-size:.8rem;animation:tick .4s cubic-bezier(.3,1.8,.5,1)}
.dmsg.mini{padding:10px;font-size:.82rem;pointer-events:none}.eth.fake{display:grid;place-items:center;background:#5865f2;color:#fff;font-weight:700;border-radius:50%;width:44px;height:44px;flex:none}
.selmock{background:#1e1f22;border:1px solid #3f4147;border-radius:6px;padding:10px 12px;margin-top:8px;color:#b5bac1}
.rx{display:inline-grid;place-items:center;min-width:34px;height:28px;padding:0 8px;border-radius:8px;background:#2b2d31;border:1px solid #3f4147}
</style></head><body>
<div class="prog" id="prog"></div><div class="scrim" id="scrim"></div><div class="boot" id="boot"></div>
<section id="login" class="login hide"><div class="hang"><i class="cord"></i><img class="medal" src="/logo.jpg" alt="Hamsafar"></div><div class="in">
  <h1>Hamsafar</h1>
  <div class="ur" dir="rtl" lang="ur">همسفر</div>
  <p>Your server's cozy control room. Tune AutoMod, welcomes, levels and tickets from here, no commands needed. ☕</p>
  <a class="btn dc" id="dclogin" href="/auth/login?remember=1">Log in with Discord</a>
  <div><label class="keep"><input type="checkbox" id="keep" checked><span class="box"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></span>Keep me logged in on this device</label></div>
  <small>We only read your name and the list of servers you manage. Nothing else, promise. 🌙</small><div id="loginErr" class="err"></div>
</div></section>
<section id="app" class="hide">
  <header><img class="logo" src="/logo.jpg" alt=""><b id="title">Hamsafar Dashboard</b>
    <button class="ib" id="fsbtn" aria-label="Search features"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg></button>
    <div class="fs" id="fs"><input id="fsearch" type="search" placeholder="Search features (press /)" autocomplete="off"><div class="fsres" id="fsres"></div></div>
    <div class="um" id="um"><button class="avb" id="avb" aria-label="Account menu"><img class="av" id="uav" alt=""></button>
      <div class="umenu" id="umenu"><div><small>Signed in as</small><div class="uname" id="uname"></div></div><a class="btn ghost sm" href="/auth/logout">Log out</a></div></div>
  </header>
  <div class="wrap">
    <div id="picker"><div class="hero"><h2 id="hello">Hey</h2><p>Pick a server to manage. Servers with Hamsafar come first.</p><input id="q" type="search" placeholder="Search your servers"></div><div class="servers" id="servers"></div></div>
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
      const t=el('div');t.append(el('b','',g.name),el('small','',g.botIn?`${g.members.toLocaleString()} members`:'Hamsafar is not here yet'));
      c.append(ic,t,el('span','go',g.botIn?'Manage server \u2192':'Add Hamsafar \u2192'));
      c.onclick=()=>g.botIn?openGuild(g):window.open(`https://discord.com/oauth2/authorize?client_id=${CID}&scope=bot%20applications.commands&permissions=8&guild_id=${g.id}`,'_blank');
      box.append(c)})};
  $('q').oninput=draw;draw();
}
const SPECIAL=[['overview','Overview'],['board','Leaderboard'],['cases','Mod cases'],['panels','Ticket panels'],['otickets','Open tickets'],['announce','Announce']];
let TABS=[],OV=null;
const TITLES={rroles:'Reaction roles',giveaways:'Giveaways',invites:'Invite tracker',inviters:'Top inviters',leave:'Leave messages',joinroles:'Join roles',messages:'Messages',overview:'Overview',automod:'AutoMod',welcome:'Welcome',leveling:'Leveling',moderation:'Moderation',utility:'Suggestions',tickets:'Ticket settings',panels:'Ticket panels',otickets:'Open tickets',board:'Leaderboard',cases:'Mod cases',announce:'Announce'};
const ICON={rroles:'\ud83c\udff7\ufe0f',giveaways:'\ud83c\udf89',invites:'\ud83d\udce8',inviters:'\ud83c\udfc5',leave:'\ud83d\udeaa',joinroles:'\ud83c\udfad',messages:'\ud83d\udcac',overview:'\ud83c\udfe0',automod:'\ud83d\udee1\ufe0f',welcome:'\ud83d\udc4b',leveling:'\u2b50',moderation:'\ud83d\udd28',utility:'\ud83d\udca1',tickets:'\u2699\ufe0f',panels:'\ud83c\udfab',otickets:'\ud83d\udceb',board:'\ud83c\udfc6',cases:'\ud83d\udccb',announce:'\ud83d\udce3'};
const DESC={rroles:'Let members pick their own roles with buttons, a menu or emoji reactions.',giveaways:'Start giveaways and let members enter with one button.',invites:'See who invited each new member and post it to a channel.',inviters:'The members who brought in the most people.',leave:'Say goodbye when someone leaves the server.',joinroles:'Roles and checks for brand new members.',messages:'Build reusable messages with embeds and buttons, then send them anywhere.',overview:'A quick look at your server and what Hamsafar is doing in it.',automod:'Rules that keep your chat clean.',welcome:'Greet new members and say goodbye.',leveling:'XP, levels and role rewards.',moderation:'Where moderation actions are logged.',utility:'Where /suggest posts end up.',tickets:'Default settings for the ticket system.',panels:'Build ticket panels with buttons and forms.',otickets:'Tickets that are open right now.',board:'Your most active members.',cases:'The latest moderation actions.',announce:'Post a message as Hamsafar.'};
const GROUPS=[['',['overview']],['Modules',['automod','welcome','leave','joinroles','rroles','invites','leveling','moderation','utility']],['Tickets',['tickets','panels','otickets']],['Insights',['board','inviters','cases']],['Tools',['messages','giveaways','announce']]];
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const when=t=>new Date(t*1000).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});
async function openGuild(g){
  $('servers').innerHTML='';$('servers').append(spl('Opening '+g.name));
  try{[META,OV]=await Promise.all([api('/api/guilds/'+g.id),api(`/api/guilds/${g.id}/overview`)])}catch(e){alert(e.message);loadServers();return}
  G=g;VAL=clone(META.values);ORIG=clone(META.values);
  const have=new Set(['overview','rroles','giveaways','inviters','messages','panels','otickets','board','cases','announce',...SCHEMA.filter(s=>VAL[s.id]).map(s=>s.id)]);
  TABS=GROUPS.flatMap(([,ids])=>ids.filter(i=>have.has(i)).map(i=>[i,TITLES[i]]));TAB='overview';CTL=null;
  $('picker').classList.add('hide');$('panel').classList.remove('hide');$('title').textContent=g.name;$('fs').classList.add('on');
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
    f.append(el('h3','','Quick actions'));const qa=el('div','qa');[['panels','Build a ticket panel','Buttons, forms, categories'],['automod','Tune AutoMod','Spam, links, bad words'],['welcome','Set up welcomes','Messages and auto-roles'],['giveaways','Start a giveaway','Winners are picked for you'],['messages','Create a custom message','Embeds and link buttons'],['announce','Send an announcement','Post as Hamsafar']].filter(([k])=>TABS.some(t=>t[0]===k)).forEach(([k,t,d])=>{const b=el('button');b.append(el('span','',(ICON[k]||'')+' '+t),el('small','',d));b.onclick=()=>switchTab(k);qa.append(b)});f.append(qa);
    f.append(el('h3','','What is switched on'));
    list(OV.status.map(([n,txt,on,tab])=>{const r=el('div','row st'),d=el('div'),b=el('button','btn ghost','Configure');d.append(el('label','',n),el('small','',txt));b.onclick=()=>switchTab(tab);r.append(el('span','dot'+(on?' on':'')),d,b);return r}),'');
  }else if(k==='board'){
    const L=OV.leaderboard||[],top=(L[0]&&L[0].xp)||1;
    list(L.map((u,i)=>{const r=el('div','row'),d=el('div'),m=el('div','meter');d.append(el('label','',`${i+1}. ${u.name}`),el('small','',`Level ${u.level}, ${u.xp} XP, ${u.messages} messages`));m.append(el('i'));m.firstChild.style.setProperty('--w',(u.xp/top*100)+'%');m.style.setProperty('--w','0');r.append(d,m);return r}),'Nobody has earned XP yet. Turn on Leveling and start chatting.');
  }else if(k==='cases'){renderCases();}else if(k==='otickets'){
    list((OV.tickets||[]).map(t=>{const r=el('div','row'),d=el('div');d.append(el('label','',`#${t.number} ${t.subject}`),el('small','',`Opened by ${t.opener} on ${when(t.created)}. ${t.claimed?'Claimed by '+t.claimed:'Not claimed yet'}`));r.append(d);return r}),'No open tickets.');
  }else if(k==='panels'){renderPanels();}else if(k==='messages'){renderMessages();}else if(k==='giveaways'){renderGiveaways();}else if(k==='rroles'){renderRR();}else if(k==='inviters'){renderInviters();}else if(k==='announce'){
    const box=el('div','sec'),mk=(l,h,c)=>{const r=el('div','row col'),d=el('div');d.append(el('label','',l),el('small','',h));r.append(d,c);box.append(r);return c};
    const ch=el('select');META.channels.forEach(o=>ch.add(new Option('#'+o.name,o.id)));mk('Channel','Where Hamsafar posts it',ch);
    const ti=el('input');ti.type='text';ti.maxLength=200;ti.placeholder='Announcement';mk('Title','',ti);
    const ms=el('textarea');ms.maxLength=3500;mk('Message','Posted as a Hamsafar-styled embed',ms);
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
  try{await Promise.all([cur().save(),new Promise(r=>setTimeout(r,800))]);busy=false;b.classList.remove('saving');b.classList.add('ok');$('popmsg').textContent='Saved. Hamsafar is using it now.';
    setTimeout(()=>{if(!cur().dirty()){b.classList.remove('show');setTimeout(()=>b.classList.remove('ok'),400)}},1700)}
  catch(e){busy=false;b.classList.remove('saving');b.classList.add('bad');$('popmsg').textContent=e.message}};
$('reset').onclick=()=>{if(busy)return;cur().reset();touch()};
function goBack(){drawer(false);if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;CTL=null;PREV=null;$('fs').classList.remove('on');$('bar').classList.remove('show');$('panel').classList.add('hide');$('picker').classList.remove('hide');$('title').textContent='Hamsafar Dashboard';loadServers()}
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
const newPanel=()=>({id:'',name:'Support',embed_title:'Need Help?',embed_description:'Press a button below to open a private ticket with our staff.',embed_color:'#f2a47e',category:'',support_roles:[],log_channel:'',max_open:1,ticket_message:'',channel:'',buttons:[newBtn()]});
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
const MOON='<svg viewBox="0 0 48 48" width="48" height="48"><path class="bd" d="M33 8A18 18 0 1 0 40 33A14 14 0 1 1 33 8Z"/><path class="tw" d="M38 8l1.6 3.6 3.6 1.6-3.6 1.6L38 18.4l-1.6-3.6-3.6-1.6 3.6-1.6z"/></svg>';
const spl=t=>{const d=el('div','spl');d.innerHTML=`<div class="thr"><i></i>${MOON}</div>`;d.append(el('small','',t||'Loading'));return d};
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
const newEmbed=()=>({title:'',description:'',color:'#f2a47e',author:'',footer:'',image:'',thumbnail:'',fields:[]});
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
  PREV=()=>{pv.innerHTML='';const hd=el('div','dhead'),im=el('img');im.src='/logo.jpg';im.alt='';hd.append(im,el('b','','Hamsafar'),el('span','tag','BOT'));pv.append(hd);
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
  top.append(el('p','hint','Members enter with a button. Hamsafar picks the winners when the timer ends.'),nb);f.append(top);
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
  const S={'{user}':'@Aarav','{inviter}':'@Meera','{code}':'x7Kp2','{invites}':'12','{count}':'248','{server}':'Hamsafar HQ'};
  const sb=x=>Object.entries(S).reduce((a,[k,v])=>a.split(k).join(v),(x||'').replace(/\*\*/g,'').replace(/`/g,''));
  const d=el('div','dmsg mini'),bx=el('div','emb'),bd=el('div','embody');bx.style.borderLeftColor=t.color;
  if(t.author)bd.append(el('div','eau',sb(t.author)));if(t.title)bd.append(el('div','eti',sb(t.title)));if(t.desc)bd.append(el('div','ede',sb(t.desc)));
  if(t.fields.length){const g=el('div','efs');t.fields.forEach(([n,v])=>{const c=el('div','ef in');c.append(el('b','',sb(n)),el('div','',sb(v)));g.append(c)});bd.append(g)}
  if(t.footer)bd.append(el('div','eft',sb(t.footer)));bx.append(bd);if(t.thumb)bx.append(el('div','eth fake','P'));d.append(bx);return d}
const newItem=()=>({emoji:'',label:'',role:'',description:''});
const newRR=()=>({id:'',name:'Roles',mode:'buttons',title:'Pick your roles',description:'Tap a button below to get or remove a role.',color:'#f2a47e',unique:false,items:[newItem()],channel:'',message_id:''});
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
  PREV=()=>{pv.innerHTML='';const hd=el('div','dhead'),im=el('img');im.src='/logo.jpg';im.alt='';hd.append(im,el('b','','Hamsafar'),el('span','tag','BOT'));pv.append(hd);
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
function skySvg(c){const n='http://www.w3.org/2000/svg',s=document.createElementNS(n,'svg');s.setAttribute('viewBox','0 0 400 400');if(c)s.setAttribute('class',c);
  let d='';[70,130,190,250,310,370].forEach(r=>{d+=`M${r} 0A${r} ${r} 0 0 1 0 ${r}`});
  const p=document.createElementNS(n,'path');p.setAttribute('d',d);s.append(p);
  [[120,90],[250,160],[180,260],[310,310],[60,200],[330,60]].forEach(([x,y],i)=>{const k=5+i%3*2,q=document.createElementNS(n,'path');
    q.setAttribute('d',`M${x} ${y-k}L${x+k/3} ${y-k/3}L${x+k} ${y}L${x+k/3} ${y+k/3}L${x} ${y+k}L${x-k/3} ${y+k/3}L${x-k} ${y}L${x-k/3} ${y-k/3}Z`);s.append(q)});
  return s}
$('login').prepend(skySvg(),skySvg('r'));document.body.append(skySvg('bgsky'));
$('avb').onclick=e=>{e.stopPropagation();$('umenu').classList.toggle('open')};
$('fsbtn').onclick=e=>{e.stopPropagation();const f=$('fs');f.classList.toggle('open');if(f.classList.contains('open'))setTimeout(()=>$('fsearch').focus(),60)};
document.addEventListener('click',e=>{if(!e.target.closest('.um'))$('umenu').classList.remove('open');if(!e.target.closest('.fs,#fsbtn'))$('fs').classList.remove('open')});
$('menubtn').onclick=()=>drawer(true);$('scrim').onclick=()=>drawer(false);
addEventListener('resize',()=>{if(innerWidth>720)drawer(false)});
let tx=0,ty=0;addEventListener('touchstart',e=>{tx=e.touches[0].clientX;ty=e.touches[0].clientY},{passive:true});
addEventListener('touchend',e=>{const dx=e.changedTouches[0].clientX-tx,dy=e.changedTouches[0].clientY-ty;if(dx<-60&&Math.abs(dy)<Math.abs(dx)*.6)drawer(false)},{passive:true});
const setHH=()=>{const hd=document.querySelector('#app>header');if(hd)document.documentElement.style.setProperty('--hh',hd.offsetHeight+'px')};
addEventListener('resize',setHH);new ResizeObserver(setHH).observe(document.querySelector('#app>header'));
showBoot('Lighting the lanterns');init0().catch(()=>{}).finally(()=>setTimeout(hideBoot,300));
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
        from theme import cozy_embed
        e = cozy_embed(p["title"], p["description"], int(p["color"].lstrip("#"), 16))
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
