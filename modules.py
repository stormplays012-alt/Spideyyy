"""Maps dashboard settings onto YOUR cogs' own live config (AutoMod, Welcome, Leveling, Moderation, Tickets, Utility).
Nothing is duplicated: the dashboard edits the same dicts your /commands edit, then calls each cog's own save()."""
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
