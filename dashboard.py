"""Spidey web dashboard (single file). Put this next to bot.py, then in bot.py: await self.load_extension("dashboard")
Env needed: CLIENT_SECRET, REDIRECT_URI (optional DASHBOARD_PORT). Edits your cogs' live settings."""
import os, sys, secrets, base64, aiohttp, discord
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

    async def logo(request): return web.Response(body=base64.b64decode(LOGO_B64), content_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})

    async def index(request): return web.Response(text=INDEX_HTML, content_type="text/html")
    app = web.Application(middlewares=[session_mw])
    app.add_routes([web.get("/", index), web.get("/auth/login", login), web.get("/auth/callback", callback), web.get("/auth/logout", logout),
        web.get("/api/config", config), web.get("/api/me", me), web.get("/api/schema", schema), web.get("/api/guilds", guilds),
        web.get("/api/guilds/{id}", guild_get), web.get("/api/guilds/{id}/overview", overview), web.get("/logo.jpg", logo), web.get("/api/guilds/{id}/panels", panels_get), web.put("/api/guilds/{id}/panels/{pid}", panel_put), web.delete("/api/guilds/{id}/panels/{pid}", panel_delete), web.post("/api/guilds/{id}/panels/{pid}/post", panel_post), web.post("/api/guilds/{id}/announce", announce), web.put("/api/guilds/{id}/{sec}", section_put)])
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
#app>*{position:relative;z-index:1}
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
nav button{transition:background .15s,color .15s,padding .15s}nav button:hover{color:var(--tx);padding-left:16px}nav button.on{box-shadow:inset 3px 0 0 var(--ac)}
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
</style></head><body>
<section id="login" class="login hide"><div class="hang"><i class="cord"></i><img class="medal" src="/logo.jpg" alt="Spidey"></div><div class="in">
  <h1>Spidey</h1>
  <p>Your server's control room. Tune AutoMod, welcomes, levels and tickets here instead of typing commands.</p>
  <a class="btn dc" href="/auth/login">Log in with Discord</a>
  <small>We only read your name and the list of servers you manage.</small><div id="loginErr" class="err"></div>
</div></section>
<section id="app" class="hide">
  <header><img class="logo" src="/logo.jpg" alt=""><b id="title">Spidey Dashboard</b><span id="uname"></span><img class="av" id="uav" alt=""><a class="btn ghost" href="/auth/logout">Log out</a></header>
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
const SPECIAL=[['overview','Overview'],['board','Leaderboard'],['cases','Mod cases'],['panels','Ticket panels'],['otickets','Open tickets'],['announce','Announce']];
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
    const st=el('div','stats');[['Members',OV.stats.members],['Channels',OV.stats.channels],['Roles',OV.stats.roles],['Bot ping',OV.stats.latency+' ms']].forEach(([a,b])=>{const c=el('div','stat');const bb=el('b','',b);countUp(bb,b);c.append(bb,el('small','',a));st.append(c)});f.append(st);
    f.append(el('h3','','What is switched on'));
    list(OV.status.map(([n,txt,on,tab])=>{const r=el('div','row st'),d=el('div'),b=el('button','btn ghost','Configure');d.append(el('label','',n),el('small','',txt));b.onclick=()=>switchTab(tab);r.append(el('span','dot'+(on?' on':'')),d,b);return r}),'');
  }else if(k==='board'){
    const L=OV.leaderboard||[],top=(L[0]&&L[0].xp)||1;
    list(L.map((u,i)=>{const r=el('div','row'),d=el('div'),m=el('div','meter');d.append(el('label','',`${i+1}. ${u.name}`),el('small','',`Level ${u.level}, ${u.xp} XP, ${u.messages} messages`));m.append(el('i'));m.firstChild.style.setProperty('--w',(u.xp/top*100)+'%');m.style.setProperty('--w','0');r.append(d,m);return r}),'Nobody has earned XP yet. Turn on Leveling and start chatting.');
  }else if(k==='cases'){
    list((OV.cases||[]).map(c=>{const r=el('div','row'),d=el('div');d.append(el('label','',`Case #${c.id}: ${c.type} ${c.user}`),el('small','',`By ${c.mod} on ${when(c.time)}. ${c.reason}`));r.append(d);return r}),'No moderation cases yet.');
  }else if(k==='otickets'){
    list((OV.tickets||[]).map(t=>{const r=el('div','row'),d=el('div');d.append(el('label','',`#${t.number} ${t.subject}`),el('small','',`Opened by ${t.opener} on ${when(t.created)}. ${t.claimed?'Claimed by '+t.claimed:'Not claimed yet'}`));r.append(d);return r}),'No open tickets.');
  }else if(k==='panels'){renderPanels();}else if(k==='announce'){
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
  const f=$('fields');f.innerHTML='';$('secTitle').textContent='Ticket panels';
  let P;try{P=await api(`/api/guilds/${G.id}/panels`)}catch(e){f.append(el('div','empty',e.message));return}
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
  if(D.buttons.length>1){const rm=el('button','btn ghost sm','Remove button');rm.onclick=()=>{D.buttons.splice(i,1);redraw()};hd.append(rm)}c.append(hd);
  const b=el('div','sec'),lb=fT(bt,'label',80),em=fT(bt,'emoji',32),st=fS(bt,'style',[['primary','Blue'],['secondary','Grey'],['success','Green'],['danger','Red']]);
  [lb,em].forEach(e=>e.addEventListener('input',upd));st.addEventListener('change',upd);
  fR(b,'Button text','',lb);fR(b,'Emoji','Optional',em);fR(b,'Color','',st);
  const fm=el('div'),draw=()=>{fm.innerHTML='';
    if(bt.form_enabled){fR(fm,'Form title','Top of the form, 45 characters max',fT(bt,'form_title',45));
      fm.append(el('h3','','Questions (Discord allows up to 5 per form)'));
      bt.questions.forEach((q,qi)=>{const qc=el('div','qcard'),t2=el('div','two'),t3=el('div','two'),tr=el('div','qrow');
        const ql=fT(q,'label',45),qp=fT(q,'placeholder',100);ql.placeholder='Question';qp.placeholder='Hint text (optional)';t2.append(ql,qp);
        t3.append(fS(q,'style',[['short','Short answer'],['paragraph','Long answer']]),fN(q,'max_length',1,4000));
        const rq=el('label','','Required');rq.prepend(fW(q,'required'));const rm=el('button','btn ghost sm','Remove');rm.onclick=()=>{bt.questions.splice(qi,1);draw()};
        tr.append(rq,rm);qc.append(t2,t3,tr);fm.append(qc)});
      if(bt.questions.length<5){const ad=el('button','btn ghost sm','Add question');ad.style.margin='14px 0';ad.onclick=()=>{bt.questions.push(newQ());draw()};fm.append(ad)}
    }else fR(fm,'Ticket subject','Used as the subject because no form is shown',fT(bt,'ticket_subject',100));
    fR(fm,'Message inside the ticket','Optional, replaces the panel one for this button',fA(bt,'ticket_message'),1)};
  fR(b,'Ask a form first','Turn off to open the ticket instantly',fW(bt,'form_enabled',draw));b.append(fm);draw();c.append(b);return c;
}
function panelEditor(p){
  const D=p?clone(p):newPanel(),f=$('fields');f.innerHTML='';$('secTitle').textContent=p?'Edit panel':'New panel';
  const back=el('button','btn ghost sm','Back to panels');back.onclick=()=>renderPanels();f.append(back);
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
    if(D.buttons.length<25){const ad=el('button','btn ghost','Add button');ad.style.marginTop='14px';ad.onclick=()=>{D.buttons.push(newBtn());draw()};bw.append(ad)}};draw();
  f.append(el('h3','','Publish'));b=el('div','sec');const chs=el('select');optsOf(META.channels,'Choose a channel','#').forEach(([a,n])=>chs.add(new Option(n,a)));chs.value=D.channel||'';
  fR(b,'Post in channel','Posting again updates the same message',chs);f.append(b);
  const out=el('div','toast'),sv=el('button','btn pri','Save panel'),ps=el('button','btn ghost','Save and post');
  const acts=el('div','acts');acts.style.margin='16px 0';acts.append(sv,ps,out);f.append(acts);
  const save=async()=>{const r=await api(`/api/guilds/${G.id}/panels/${D.id||'new'}`,PJ('PUT',D));D.id=r.id};
  const run=async(fn,ok)=>{out.className='toast';out.textContent='Working...';try{await fn();out.textContent=ok}catch(e){out.className='toast bad';out.textContent=e.message}};
  sv.onclick=()=>run(save,'Saved.');
  ps.onclick=()=>run(async()=>{if(!chs.value)throw new Error('Choose a channel first');await save();await api(`/api/guilds/${G.id}/panels/${D.id}/post`,PJ('POST',{channel:chs.value}));D.channel=chs.value},'Posted. Members can use the buttons now.');
}
function webSvg(c){const n='http://www.w3.org/2000/svg',s=document.createElementNS(n,'svg');s.setAttribute('viewBox','0 0 400 400');if(c)s.setAttribute('class',c);
  const A=[0,18,36,54,72,90].map(a=>a*Math.PI/180);let d='';A.forEach(a=>d+=`M0 0L${400*Math.cos(a)} ${400*Math.sin(a)}`);
  for(let r=60;r<=400;r+=65){d+=`M${r} 0`;A.slice(1).forEach((a,i)=>{const m=(a+A[i])/2,q=r*.9;d+=`Q${q*Math.cos(m)} ${q*Math.sin(m)} ${r*Math.cos(a)} ${r*Math.sin(a)}`})}
  const p=document.createElementNS(n,'path');p.setAttribute('d',d);s.append(p);return s}
$('login').prepend(webSvg(),webSvg('r'));document.body.append(webSvg('bgweb'));
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
