"""Spidey web dashboard (single file). Put this next to bot.py, then in bot.py: await self.load_extension("dashboard")
Env needed: CLIENT_SECRET, REDIRECT_URI (optional DASHBOARD_PORT). Edits your cogs' live settings."""
import os, sys, json, time, hashlib, secrets, base64, aiohttp, discord
from urllib.parse import urlencode
from aiohttp import web
from discord.ext import commands

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

    async def logo(request): return web.Response(body=base64.b64decode(LOGO_B64), content_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    async def index(request): return web.Response(text=INDEX_HTML, content_type="text/html")
    app = web.Application(middlewares=[session_mw])
    app.add_routes([web.get("/", index), web.get("/auth/login", login), web.get("/auth/callback", callback), web.get("/auth/logout", logout),
        web.get("/api/config", config), web.get("/api/me", me), web.get("/api/schema", schema), web.get("/api/guilds", guilds),
        web.get("/api/guilds/{id}", guild_get), web.get("/api/guilds/{id}/overview", overview), web.get("/logo.jpg", logo), web.get("/api/guilds/{id}/cases", cases_get), web.put("/api/guilds/{id}/cases/{cid}", case_put), web.delete("/api/guilds/{id}/cases/{cid}", case_delete), web.get("/api/guilds/{id}/templates", tpl_get), web.put("/api/guilds/{id}/templates/{tid}", tpl_put), web.delete("/api/guilds/{id}/templates/{tid}", tpl_delete), web.post("/api/guilds/{id}/templates/{tid}/send", tpl_send), web.get("/api/guilds/{id}/panels", panels_get), web.put("/api/guilds/{id}/panels/{pid}", panel_put), web.delete("/api/guilds/{id}/panels/{pid}", panel_delete), web.post("/api/guilds/{id}/panels/{pid}/post", panel_post), web.post("/api/guilds/{id}/announce", announce), web.put("/api/guilds/{id}/{sec}", section_put)])
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
<title>Spidey Dashboard</title><link rel="icon" href="/logo.jpg">
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
</style></head><body>
<div class="prog" id="prog"></div><div class="scrim" id="scrim"></div><div class="boot" id="boot"></div>
<section id="login" class="login hide"><div class="hang"><i class="cord"></i><img class="medal" src="/logo.jpg" alt="Spidey"></div><div class="in">
  <h1>Spidey</h1>
  <p>Your server's control room. Tune AutoMod, welcomes, levels and tickets here instead of typing commands.</p>
  <a class="btn dc" id="dclogin" href="/auth/login?remember=1">Log in with Discord</a>
  <div><label class="keep"><input type="checkbox" id="keep" checked><span class="box"><svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg></span>Keep me logged in on this device</label></div>
  <small>We only read your name and the list of servers you manage.</small><div id="loginErr" class="err"></div>
</div></section>
<section id="app" class="hide">
  <header><img class="logo" src="/logo.jpg" alt=""><b id="title">Spidey Dashboard</b>
    <button class="ib" id="fsbtn" aria-label="Search features"><svg viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg></button>
    <div class="fs" id="fs"><input id="fsearch" type="search" placeholder="Search features (press /)" autocomplete="off"><div class="fsres" id="fsres"></div></div>
    <div class="um" id="um"><button class="avb" id="avb" aria-label="Account menu"><img class="av" id="uav" alt=""></button>
      <div class="umenu" id="umenu"><div><small>Signed in as</small><div class="uname" id="uname"></div></div><a class="btn ghost sm" href="/auth/logout">Log out</a></div></div>
  </header>
  <div class="wrap">
    <div id="picker"><div class="hero"><h2 id="hello">Hey</h2><p>Pick a server to manage. Servers with Spidey come first.</p><input id="q" type="search" placeholder="Search your servers"></div><div class="servers" id="servers"></div></div>
    <div id="panel" class="hide"><div class="mbar"><button class="mb" id="menubtn" aria-label="Open menu"><svg viewBox="0 0 24 24"><path d="M4 7h16M4 12h16M4 17h10"/></svg></button><span id="mtitle"></span></div><div class="layout"><nav id="nav"></nav><div id="main"><h2 id="secTitle"></h2><p class="hint" id="secDesc"></p><div id="fields"></div></div></div></div>
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
  $('app').classList.remove('hide');$('uname').textContent=user.global_name||user.username;$('hello').textContent='Hey, '+(user.global_name||user.username);
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
const TITLES={leave:'Leave messages',joinroles:'Join roles',messages:'Messages',overview:'Overview',automod:'AutoMod',welcome:'Welcome',leveling:'Leveling',moderation:'Moderation',utility:'Suggestions',tickets:'Ticket settings',panels:'Ticket panels',otickets:'Open tickets',board:'Leaderboard',cases:'Mod cases',announce:'Announce'};
const ICON={leave:'\ud83d\udeaa',joinroles:'\ud83c\udfad',messages:'\ud83d\udcac',overview:'\ud83c\udfe0',automod:'\ud83d\udee1\ufe0f',welcome:'\ud83d\udc4b',leveling:'\u2b50',moderation:'\ud83d\udd28',utility:'\ud83d\udca1',tickets:'\u2699\ufe0f',panels:'\ud83c\udfab',otickets:'\ud83d\udceb',board:'\ud83c\udfc6',cases:'\ud83d\udccb',announce:'\ud83d\udce3'};
const DESC={leave:'Say goodbye when someone leaves the server.',joinroles:'Roles and checks for brand new members.',messages:'Build reusable messages with embeds and buttons, then send them anywhere.',overview:'A quick look at your server and what Spidey is doing in it.',automod:'Rules that keep your chat clean.',welcome:'Greet new members and say goodbye.',leveling:'XP, levels and role rewards.',moderation:'Where moderation actions are logged.',utility:'Where /suggest posts end up.',tickets:'Default settings for the ticket system.',panels:'Build ticket panels with buttons and forms.',otickets:'Tickets that are open right now.',board:'Your most active members.',cases:'The latest moderation actions.',announce:'Post a message as Spidey.'};
const GROUPS=[['',['overview']],['Modules',['automod','welcome','leave','joinroles','leveling','moderation','utility']],['Tickets',['tickets','panels','otickets']],['Insights',['board','cases']],['Tools',['messages','announce']]];
const el=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
const when=t=>new Date(t*1000).toLocaleString([], {dateStyle:'medium',timeStyle:'short'});
async function openGuild(g){
  $('servers').innerHTML='';$('servers').append(spl('Opening '+g.name));
  try{[META,OV]=await Promise.all([api('/api/guilds/'+g.id),api(`/api/guilds/${g.id}/overview`)])}catch(e){alert(e.message);loadServers();return}
  G=g;VAL=clone(META.values);ORIG=clone(META.values);
  const have=new Set(['overview','messages','panels','otickets','board','cases','announce',...SCHEMA.filter(s=>VAL[s.id]).map(s=>s.id)]);
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
    f.append(el('h3','','Quick actions'));const qa=el('div','qa');[['panels','Build a ticket panel','Buttons, forms, categories'],['automod','Tune AutoMod','Spam, links, bad words'],['welcome','Set up welcomes','Messages and auto-roles'],['messages','Create a custom message','Embeds and link buttons'],['announce','Send an announcement','Post as Spidey']].filter(([k])=>TABS.some(t=>t[0]===k)).forEach(([k,t,d])=>{const b=el('button');b.append(el('span','',(ICON[k]||'')+' '+t),el('small','',d));b.onclick=()=>switchTab(k);qa.append(b)});f.append(qa);
    f.append(el('h3','','What is switched on'));
    list(OV.status.map(([n,txt,on,tab])=>{const r=el('div','row st'),d=el('div'),b=el('button','btn ghost','Configure');d.append(el('label','',n),el('small','',txt));b.onclick=()=>switchTab(tab);r.append(el('span','dot'+(on?' on':'')),d,b);return r}),'');
  }else if(k==='board'){
    const L=OV.leaderboard||[],top=(L[0]&&L[0].xp)||1;
    list(L.map((u,i)=>{const r=el('div','row'),d=el('div'),m=el('div','meter');d.append(el('label','',`${i+1}. ${u.name}`),el('small','',`Level ${u.level}, ${u.xp} XP, ${u.messages} messages`));m.append(el('i'));m.firstChild.style.setProperty('--w',(u.xp/top*100)+'%');m.style.setProperty('--w','0');r.append(d,m);return r}),'Nobody has earned XP yet. Turn on Leveling and start chatting.');
  }else if(k==='cases'){renderCases();}else if(k==='otickets'){
    list((OV.tickets||[]).map(t=>{const r=el('div','row'),d=el('div');d.append(el('label','',`#${t.number} ${t.subject}`),el('small','',`Opened by ${t.opener} on ${when(t.created)}. ${t.claimed?'Claimed by '+t.claimed:'Not claimed yet'}`));r.append(d);return r}),'No open tickets.');
  }else if(k==='panels'){renderPanels();}else if(k==='messages'){renderMessages();}else if(k==='announce'){
    const box=el('div','sec'),mk=(l,h,c)=>{const r=el('div','row col'),d=el('div');d.append(el('label','',l),el('small','',h));r.append(d,c);box.append(r);return c};
    const ch=el('select');META.channels.forEach(o=>ch.add(new Option('#'+o.name,o.id)));mk('Channel','Where Spidey posts it',ch);
    const ti=el('input');ti.type='text';ti.maxLength=200;ti.placeholder='Announcement';mk('Title','',ti);
    const ms=el('textarea');ms.maxLength=3500;mk('Message','Posted as a Spidey-styled embed',ms);
    const go=el('button','btn pri','Send message');go.style.margin='14px 0';box.append(go);f.append(box);const out=el('div','toast');out.style.marginTop='12px';f.append(out);
    go.onclick=async()=>{try{await api(`/api/guilds/${G.id}/announce`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({channel:ch.value,title:ti.value,message:ms.value})});out.className='toast';out.textContent='Sent.';ms.value=''}catch(e){out.className='toast bad';out.textContent=e.message}};
  }
}
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
  $('secDesc').textContent=DESC[TAB]||'';$('mtitle').textContent=TITLES[TAB]||'';
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
function goBack(){drawer(false);if(cur().dirty()&&!confirm('Discard unsaved changes?'))return;CTL=null;PREV=null;$('fs').classList.remove('on');$('bar').classList.remove('show');$('panel').classList.add('hide');$('picker').classList.remove('hide');$('title').textContent='Spidey Dashboard';loadServers()}
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
const KW={leave:'goodbye leave left farewell',joinroles:'auto role join verify account age new members',automod:'spam links invites caps bad words filter punishment',welcome:'greet join leave goodbye autorole embed',leveling:'xp rank level rewards voice',moderation:'log cases ban kick mute',utility:'suggest suggestions',tickets:'ticket category support roles',panels:'ticket panel button form question',otickets:'tickets claimed open',board:'leaderboard top xp',cases:'cases warn ban mute kick history search',announce:'announcement broadcast',messages:'template embed button custom message send'};
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
  PREV=()=>{pv.innerHTML='';const hd=el('div','dhead'),im=el('img');im.src='/logo.jpg';im.alt='';hd.append(im,el('b','','Spidey'),el('span','tag','BOT'));pv.append(hd);
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

# ================= bot extension =================

class Dashboard(commands.Cog):
    def __init__(self, bot): self.bot, self.runner = bot, None
    async def cog_load(self): self.runner = await start_server(self.bot)
    async def cog_unload(self):
        if self.runner: await self.runner.cleanup()

async def setup(bot): await bot.add_cog(Dashboard(bot))
