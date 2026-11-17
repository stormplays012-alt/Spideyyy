"""🎯 Auto React: when a message contains a trigger word or sentence, Spidey reacts with emojis
and/or replies. Rules are edited from the web dashboard (React tab). Data: autoreact.json

Load it in bot.py BEFORE the dashboard:  await self.load_extension("autoreact")
"""
import json
import os
import re
import secrets
import time
from functools import lru_cache

import discord
from discord.ext import commands

_FILE = "autoreact.json"

MAX_RULES = 50          # rules per server
MAX_TRIGGERS = 25       # triggers per rule
MAX_EMOJIS = 5          # reactions per rule
MAX_REACTIONS = 10      # reactions Spidey adds to ONE message, across all matching rules
MODES = ("word", "contains", "exact", "starts")
KINDS = ("emoji", "reply", "both")

_CUSTOM = re.compile(r"^<(a?):(\w{2,32}):(\d{15,25})>$")
_SHORT = re.compile(r"^:(\w{2,32}):$")
_SHORTCODE_IN_TEXT = re.compile(r":(\w{2,32}):")


# ------------------------------------------------------------------ matching (pure, no Discord needed)
@lru_cache(maxsize=2048)
def _word_re(trigger: str):
    # whole word / whole phrase: "legit" matches "Legit!" and "so legit", but not "illegitimate"
    return re.compile(r"(?<!\w)" + re.escape(trigger) + r"(?!\w)", re.IGNORECASE)


def matches(rule: dict, content: str) -> bool:
    text = content.strip()
    if not text:
        return False
    low = text.lower()
    mode = rule.get("match", "word")
    for t in rule.get("triggers", []):
        tl = t.lower()
        if mode == "contains":
            ok = tl in low
        elif mode == "exact":
            ok = low == tl
        elif mode == "starts":
            ok = low.startswith(tl)
        else:
            ok = _word_re(t).search(text) is not None
        if ok:
            return True
    return False


def parse_emoji(raw: str, guild=None):
    """Turn what the dashboard stored into something add_reaction() accepts, or None."""
    raw = (raw or "").strip()
    m = _CUSTOM.match(raw)
    if m:
        return discord.PartialEmoji(name=m.group(2), id=int(m.group(3)), animated=bool(m.group(1)))
    if _SHORT.match(raw) and guild:
        name = raw.strip(":").lower()
        e = next((x for x in guild.emojis if x.name.lower() == name and x.available), None)
        return e
    return discord.PartialEmoji(name=raw) if raw else None


def fill(text: str, message) -> str:
    """Placeholders for reply text + :servername: shortcuts for the server's custom emojis."""
    g, a = message.guild, message.author
    text = (text.replace("{user}", a.mention).replace("{name}", a.display_name)
                .replace("{server}", g.name).replace("{channel}", message.channel.mention))
    names = {e.name: str(e) for e in g.emojis if e.available}
    return _SHORTCODE_IN_TEXT.sub(lambda m: names.get(m.group(1), m.group(0)), text)


# ------------------------------------------------------------------ storage
def _load():
    try:
        with open(_FILE, encoding="utf8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save(d):
    tmp = _FILE + ".tmp"          # write then swap, so a crash can never leave a half-written file
    with open(tmp, "w", encoding="utf8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False)
    os.replace(tmp, _FILE)


class AutoReact(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = _load()
        self.cd: dict = {}        # (guild, rule id, channel) -> last time it fired

    # ---- settings used by the dashboard
    def cfg(self, gid):
        c = self.data.setdefault(str(gid), {})
        c.setdefault("enabled", True)
        c.setdefault("rules", [])
        return c

    def save(self):
        _save(self.data)

    def sanitize(self, guild, payload: dict) -> dict:
        """Validate what the dashboard sent. Raises ValueError with a message the user can read."""
        if not isinstance(payload, dict):
            raise ValueError("Invalid data")
        t = lambda v, n: str(v or "").strip()[:n]
        rules = payload.get("rules") or []
        if len(rules) > MAX_RULES:
            raise ValueError(f"You can have at most {MAX_RULES} rules")
        out, seen = [], set()
        for n, r in enumerate(rules, 1):
            label = f"Rule {n}"
            triggers = []
            for x in (r.get("triggers") or [])[:MAX_TRIGGERS]:
                x = t(x, 200)
                if x and x.lower() not in {y.lower() for y in triggers}:
                    triggers.append(x)
            if not triggers:
                raise ValueError(f"{label}: add at least one word or sentence to look for")
            kind = r.get("kind") if r.get("kind") in KINDS else "emoji"
            match = r.get("match") if r.get("match") in MODES else "word"

            emojis = []
            if kind in ("emoji", "both"):
                for raw in (r.get("emojis") or [])[:MAX_EMOJIS]:
                    raw = t(raw, 80)
                    if not raw:
                        continue
                    cm, sm = _CUSTOM.match(raw), _SHORT.match(raw)
                    if cm:
                        emo = self.bot.get_emoji(int(cm.group(3)))
                        if not emo:
                            raise ValueError(f"{label}: I can't use :{cm.group(2)}: because I'm not in the server it belongs to")
                        raw = str(emo)
                    elif sm:
                        emo = parse_emoji(raw, guild)
                        if not emo:
                            raise ValueError(f"{label}: this server has no emoji called {raw}")
                        raw = str(emo)
                    elif not any(ord(c) > 127 for c in raw):
                        raise ValueError(f"{label}: \"{raw}\" is not an emoji")
                    if raw not in emojis:
                        emojis.append(raw)
                if not emojis:
                    raise ValueError(f"{label}: pick at least one emoji")

            reply = t(r.get("reply"), 2000)
            if kind in ("reply", "both") and not reply:
                raise ValueError(f"{label}: write the reply text")

            chans = []
            for c in (r.get("channels") or [])[:100]:
                ch = guild.get_channel(int(c)) if str(c).isdigit() else None
                if not ch:
                    raise ValueError(f"{label}: a chosen channel was not found in this server")
                chans.append(ch.id)

            try:
                cooldown = max(0, min(3600, int(r.get("cooldown", 5))))
            except (TypeError, ValueError):
                cooldown = 5

            rid = t(r.get("id"), 12)
            if not rid or rid in seen:
                rid = "a" + secrets.token_hex(3)
            seen.add(rid)
            out.append({"id": rid, "name": t(r.get("name"), 60), "enabled": bool(r.get("enabled", True)),
                        "triggers": triggers, "match": match, "kind": kind, "emojis": emojis, "reply": reply,
                        "reply_to": bool(r.get("reply_to", True)), "channels": chans, "cooldown": cooldown})
        return {"enabled": bool(payload.get("enabled", True)), "rules": out}

    # ---- the actual feature
    def ready(self, rule, message) -> bool:
        if not rule.get("enabled", True):
            return False
        if rule.get("channels"):
            ch = message.channel
            ids = {ch.id, getattr(ch, "parent_id", None)}      # a thread counts as its parent channel
            if not ids & set(rule["channels"]):
                return False
        return True

    def cooled(self, message, rule) -> bool:
        """True (and starts the cooldown) if this rule may fire in this channel right now."""
        now, key = time.monotonic(), (message.guild.id, rule["id"], message.channel.id)
        if now - self.cd.get(key, -1e9) < rule.get("cooldown", 0):
            return False
        if len(self.cd) > 5000:
            self.cd = {k: v for k, v in self.cd.items() if now - v < 3600}
        self.cd[key] = now
        return True

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.author.bot or message.webhook_id or not message.content:
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        c = self.data.get(str(message.guild.id))
        if not c or not c.get("enabled", True) or not c.get("rules"):
            return

        hits = [r for r in c["rules"] if self.ready(r, message) and matches(r, message.content) and self.cooled(message, r)]
        if not hits:
            return

        perms = message.channel.permissions_for(message.guild.me)
        emojis, reply_rule = [], None
        for r in hits:
            if r["kind"] in ("emoji", "both"):
                for e in r["emojis"]:
                    if e not in emojis:
                        emojis.append(e)
            if r["kind"] in ("reply", "both") and reply_rule is None:
                reply_rule = r     # first matching reply rule wins, so one message never gets a pile of replies

        if emojis and perms.add_reactions and perms.read_message_history:
            for e in emojis[:MAX_REACTIONS]:
                emo = parse_emoji(e, message.guild)
                if not emo:
                    continue
                try:
                    await message.add_reaction(emo)
                except discord.NotFound:
                    # either the message was deleted (stop) or this one emoji no longer exists (keep going)
                    try:
                        await message.channel.fetch_message(message.id)
                    except discord.NotFound:
                        return
                except discord.HTTPException:
                    continue   # reaction limit reached, blocked by the user, etc.

        can_send = perms.send_messages_in_threads if isinstance(message.channel, discord.Thread) else perms.send_messages
        if reply_rule and can_send:
            text = fill(reply_rule["reply"], message)[:2000]
            mentions = discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=False)
            try:
                if reply_rule.get("reply_to", True):
                    await message.reply(text, allowed_mentions=mentions, mention_author=False)
                else:
                    await message.channel.send(text, allowed_mentions=mentions)
            except discord.HTTPException:
                pass


async def setup(bot):
    await bot.add_cog(AutoReact(bot))
