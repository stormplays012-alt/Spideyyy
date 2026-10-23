"""Single source of truth. The dashboard WRITES here, your bot code READS from here."""
import json, os
from discord.ext import commands

FILE = os.getenv("DASHBOARD_DB", os.path.join(os.path.dirname(__file__), "data", "settings.json"))
DEFAULTS = {
    "general": {"prefix": "!", "language": "en", "nickname": "", "status": "online", "activityType": "playing", "activityText": ""},
    "welcome": {"enabled": False, "channel": "", "message": "Welcome {user} to {server}!", "embed": True, "autoRole": "", "leaveEnabled": False, "leaveMessage": "{user} left the server."},
    "moderation": {"logChannel": "", "antiSpam": False, "antiLinks": False, "antiInvites": False, "badWords": False, "badWordList": "", "capsFilter": False, "warnLimit": 3, "punishment": "mute", "mutedRole": ""},
    "leveling": {"enabled": False, "xpRate": 1, "announceChannel": "", "announce": True, "roleRewards": ""},
    "commands": {c: True for c in "help ping userinfo serverinfo avatar poll ban kick mute warn clear rank".split()},
}
os.makedirs(os.path.dirname(FILE), exist_ok=True)
try:
    with open(FILE, encoding="utf8") as f: _db = json.load(f)
except (FileNotFoundError, ValueError):
    _db = {}

def get(guild_id) -> dict:
    """Full settings for a guild (defaults filled in). e.g. get(ctx.guild.id)["welcome"]["channel"]"""
    saved = _db.get(str(guild_id), {})
    return {k: {**d, **saved.get(k, {})} for k, d in DEFAULTS.items()}

def _ok(v, default):  # type check; bool is not accepted as number
    if isinstance(default, bool): return isinstance(v, bool)
    if isinstance(default, (int, float)): return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, type(default))

def save(guild_id, body: dict) -> dict:
    clean = {k: {f: (body.get(k, {}).get(f) if _ok(body.get(k, {}).get(f), d) else d) for f, d in fields.items()} for k, fields in DEFAULTS.items()}
    _db[str(guild_id)] = clean
    with open(FILE, "w", encoding="utf8") as f: json.dump(_db, f, indent=2)
    return clean

def command_enabled(guild_id, name) -> bool:
    return get(guild_id)["commands"].get(name, True)

def fmt(template: str, member) -> str:
    return template.replace("{user}", member.mention).replace("{server}", member.guild.name)

async def get_prefix(bot, message):
    """Use as: commands.Bot(command_prefix=dashboard.get_prefix, ...)"""
    p = get(message.guild.id)["general"]["prefix"] if message.guild else "!"
    return commands.when_mentioned_or(p)(bot, message)
