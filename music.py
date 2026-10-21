"""
Music for Spidey: /music play <song name or link>

Runs on its own once it is playing:
  * joins your voice channel by itself and starts playing
  * moves to the next song by itself and keeps the queue going
  * a "Now Playing" panel with buttons (pause, skip, stop, loop, shuffle, volume, queue)
    so nobody has to type commands while listening
  * leaves by itself when it is alone in the channel, or when nothing has played for a while
  * resolves each song's stream right before playing it, so long queues never go stale

Needs (see README): FFmpeg installed on the host, plus `yt-dlp` and `PyNaCl` from requirements.txt.
Supports YouTube search, YouTube / SoundCloud / Bandcamp etc. links and YouTube playlists.
Spotify links are not supported (Spotify does not allow audio streaming), so type the song name instead.
"""
import asyncio
import datetime
import json
import os
import random
import re
import shutil
import time
import urllib.parse
import urllib.request
from collections import deque
from dataclasses import dataclass
from math import ceil
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK

try:
    import yt_dlp
except ImportError:  # pragma: no cover
    yt_dlp = None

DEFAULT_VOLUME = 50          # percent
MAX_QUEUE = 200
MAX_PLAYLIST = 50            # songs taken from one playlist link
IDLE_TIMEOUT = 180           # seconds with an empty queue before leaving
ALONE_TIMEOUT = 60           # seconds alone in the channel before leaving
PAGE_SIZE = 10
STREAM_TTL = 600             # a resolved stream link is reused for 10 minutes at most

COOKIE_FILE = os.getenv("YTDL_COOKIES", "cookies.txt")     # optional: exported browser cookies (see README)
JS_RUNTIMES = {"deno": "deno", "node": "node", "bun": "bun", "quickjs": "qjs"}   # yt-dlp name -> program name
YTDL_SEARCH_OPTS = {
    "quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": "in_playlist",
    "noplaylist": False, "socket_timeout": 15, "cachedir": False, "source_address": "0.0.0.0",
}
YTDL_STREAM_OPTS = {
    "format": "bestaudio/best", "quiet": True, "no_warnings": True, "skip_download": True,
    "noplaylist": True, "socket_timeout": 15, "cachedir": False, "source_address": "0.0.0.0",
}


def found_js_runtimes() -> list[str]:
    return [name for name, prog in JS_RUNTIMES.items() if shutil.which(prog)]


def build_opts(base: dict, with_js: bool = True) -> dict:
    """YouTube now needs a JavaScript runtime for yt-dlp; hand it whichever one this machine has."""
    opts = dict(base)
    if os.path.exists(COOKIE_FILE):
        opts["cookiefile"] = COOKIE_FILE
    if with_js:
        runtimes = found_js_runtimes()
        if runtimes:
            opts["js_runtimes"] = {name: {} for name in runtimes}
        opts["remote_components"] = ["ejs:github"]
    return opts


def friendly_error(e: Exception) -> str:
    """Turn a scary traceback into something a server owner can act on."""
    text = str(e)
    low = text.lower()
    if "ffmpeg" in low and ("not found" in low or "no such file" in low):
        return "FFmpeg is not installed on the machine that runs me. See the README (Music needs FFmpeg)."
    if "sign in to confirm" in low or "not a bot" in low or "cookies" in low or "http error 403" in low:
        return BLOCK_FIX
    if "javascript" in low or "n challenge" in low or "js runtime" in low or "ejs" in low:
        return "yt-dlp needs a JavaScript runtime (Node.js or Deno) installed. See the README."
    if "not available" in low or "unavailable" in low or "private video" in low:
        return "That video isn't available (private, removed, or region-locked)."
    if "timeout" in low or "timed out" in low:
        return "The request timed out. Try again."
    text = re.sub(r"\x1b\[[0-9;]*m", "", text).replace("ERROR:", "").strip()
    return (text[:200] + "…") if len(text) > 200 else (text or type(e).__name__)


YT_RE = re.compile(r"(?:youtube\.com|youtu\.be)", re.I)
BLOCK_FIX = ("YouTube is blocking this host (normal for hosting companies like Replit, Railway, Render or a VPS). "
             "Fixes: run the bot on your own PC/home network, or put a `cookies.txt` next to `bot.py` (see README). "
             "I also try SoundCloud automatically, but it didn't have this song.")


def is_youtube(url: str) -> bool:
    return bool(url and YT_RE.search(url))


def search_text(title: str) -> str:
    """'Song (Official Video) [HD]' -> 'Song' so a SoundCloud search finds it."""
    t = re.sub(r"[\(\[][^\)\]]*[\)\]]", " ", title or "")
    t = re.sub(r"(?i)\b(official|video|lyrics?|audio|hd|4k|mv)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t or (title or "")


def ytdlp_age_days() -> Optional[int]:
    """How old this yt-dlp is (YouTube changes often, so very old versions stop working)."""
    try:
        y, m, d = (int(x) for x in yt_dlp.version.__version__.split(".")[:3])
        return (datetime.date.today() - datetime.date(y, m, d)).days
    except Exception:
        return None


FFMPEG_OPTS = {
    "before_options": "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}
URL_RE = re.compile(r"^https?://", re.I)
LOOP_LABELS = {"off": "Off", "track": "🔂 This song", "queue": "🔁 Whole queue"}


# ------------------------------------------------------------------ helpers
def fmt_time(seconds) -> str:
    seconds = int(seconds or 0)
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def progress_bar(elapsed: float, total: Optional[int], width: int = 15) -> str:
    if not total:
        return "🔴 LIVE / unknown length"
    pos = min(width - 1, max(0, int(width * elapsed / total)))
    return "▬" * pos + "🔘" + "▬" * (width - pos - 1)


def clean(text: str, limit: int = 90) -> str:
    """Make a title safe to put inside a markdown link."""
    text = (text or "Unknown").replace("[", "(").replace("]", ")").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


async def _err(interaction: discord.Interaction, text: str):
    embed = web_embed("Web Snagged!", text, DARK)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


@dataclass
class Track:
    title: str
    webpage_url: str
    duration: Optional[int]
    thumbnail: Optional[str]
    uploader: str
    requester_id: int
    stream_url: Optional[str] = None
    resolved_at: float = 0.0
    fallback: Optional[str] = None      # e.g. "SoundCloud" when YouTube refused

    @property
    def link(self) -> str:
        return f"[{clean(self.title)}]({self.webpage_url})"


# ------------------------------------------------------------------ per-server player
class GuildPlayer:
    def __init__(self, cog: "Music", guild: discord.Guild, text_channel: discord.abc.Messageable):
        self.cog, self.bot, self.guild, self.text_channel = cog, cog.bot, guild, text_channel
        self.queue: deque[Track] = deque()
        self.current: Optional[Track] = None
        self.volume = DEFAULT_VOLUME
        self.loop_mode = "off"
        self.wakeup = asyncio.Event()
        self.done = asyncio.Event()
        self.skipped = False
        self.destroyed = False
        self.started_at = 0.0
        self.paused_at: Optional[float] = None
        self.paused_total = 0.0
        self.np_message: Optional[discord.Message] = None
        self.ticker: Optional[asyncio.Task] = None
        self.alone_task: Optional[asyncio.Task] = None
        self.task = self.bot.loop.create_task(self.run())

    # ---------- state helpers
    @property
    def vc(self) -> Optional[discord.VoiceClient]:
        return self.guild.voice_client

    @property
    def paused(self) -> bool:
        return self.paused_at is not None

    def elapsed(self) -> float:
        if self.current is None:
            return 0.0
        ref = self.paused_at if self.paused_at is not None else time.monotonic()
        return max(0.0, ref - self.started_at - self.paused_total)

    def pause(self):
        if self.vc and self.vc.is_playing() and not self.paused:
            self.vc.pause()
            self.paused_at = time.monotonic()

    def resume(self):
        if self.vc and self.paused:
            self.vc.resume()
            self.paused_total += time.monotonic() - self.paused_at
            self.paused_at = None

    def skip(self):
        self.skipped = True
        if self.paused:
            self.resume()
        if self.vc:
            self.vc.stop()

    def set_volume(self, percent: int):
        self.volume = max(1, min(100, percent))
        if self.vc and self.vc.source is not None and hasattr(self.vc.source, "volume"):
            self.vc.source.volume = self.volume / 100

    def shuffle(self):
        items = list(self.queue)
        random.shuffle(items)
        self.queue = deque(items)

    def cycle_loop(self) -> str:
        order = ["off", "track", "queue"]
        self.loop_mode = order[(order.index(self.loop_mode) + 1) % 3]
        return self.loop_mode

    # ---------- embeds
    def now_playing_embed(self) -> discord.Embed:
        t = self.current
        if t is None:
            return web_embed("Music", "Nothing is playing right now. Use `/music play`! 🎶", DARK)
        title = "Paused" if self.paused else "Now Playing"
        el = self.elapsed()
        total = t.duration
        time_line = (f"`{fmt_time(el)}` {progress_bar(el, total)} `{fmt_time(total)}`" if total
                     else f"`{fmt_time(el)}` {progress_bar(el, None)}")
        desc = f"### {'⏸️' if self.paused else '🎶'} {t.link}\nby **{clean(t.uploader, 60)}**\n\n{time_line}"
        embed = web_embed(title, desc, BLUE if self.paused else RED)
        embed.add_field(name="Requested by", value=f"<@{t.requester_id}>", inline=True)
        embed.add_field(name="Volume", value=f"🔊 {self.volume}%", inline=True)
        embed.add_field(name="Loop", value=LOOP_LABELS[self.loop_mode], inline=True)
        if t.fallback:
            embed.add_field(name="Source", value=f"☁️ {t.fallback} (YouTube blocked this server)", inline=False)
        if self.queue:
            nxt = self.queue[0]
            embed.add_field(name=f"Up next ({len(self.queue)} in queue)", value=nxt.link, inline=False)
        if t.thumbnail:
            embed.set_thumbnail(url=t.thumbnail)
        return embed

    def queue_embed(self, page: int = 0) -> tuple[discord.Embed, int]:
        items = list(self.queue)
        pages = max(1, ceil(len(items) / PAGE_SIZE))
        page = max(0, min(page, pages - 1))
        head = f"**Now playing:** {self.current.link}" if self.current else "**Now playing:** nothing"
        start = page * PAGE_SIZE
        lines = [f"`{i}.` {t.link} `{fmt_time(t.duration) if t.duration else 'live'}` • <@{t.requester_id}>"
                 for i, t in enumerate(items[start:start + PAGE_SIZE], start=start + 1)]
        body = "\n".join(lines) if lines else "The queue is empty. Add songs with `/music play`. 🕸️"
        embed = web_embed("Queue", f"{head}\n\n{body}", RED)
        total = sum(t.duration or 0 for t in items)
        embed.set_footer(text=f"Page {page + 1}/{pages} • {len(items)} songs • {fmt_time(total)} total • 🕸️ Spidey")
        return embed, pages

    # ---------- messages
    async def say(self, text: str, color: int = DARK):
        try:
            await self.text_channel.send(embed=web_embed("Music", text, color))
        except discord.HTTPException:
            pass

    async def clear_np(self):
        if self.np_message is not None:
            try:
                await self.np_message.delete()
            except discord.HTTPException:
                pass
            self.np_message = None

    async def send_now_playing(self):
        await self.clear_np()
        try:
            self.np_message = await self.text_channel.send(embed=self.now_playing_embed(), view=PlayerView(self))
        except discord.HTTPException:
            self.np_message = None

    async def refresh_np(self):
        if self.np_message is not None and self.current is not None:
            try:
                await self.np_message.edit(embed=self.now_playing_embed())
            except discord.HTTPException:
                pass

    async def finish_np(self):
        """Song ended and nothing is queued: turn the panel into a tidy 'finished' note."""
        if self.np_message is not None:
            try:
                await self.np_message.edit(
                    embed=web_embed("Queue Finished", "That was the last song. Add more with `/music play`! 🎶", DARK),
                    view=None)
            except discord.HTTPException:
                pass
            self.np_message = None

    async def _tick(self):
        while True:
            await asyncio.sleep(15)
            await self.refresh_np()

    # ---------- the main loop (runs for as long as the bot is in the voice channel)
    def _after(self, error):
        if error:
            print(f"[music] playback error in {self.guild.id}: {error!r}")
        self.bot.loop.call_soon_threadsafe(self.done.set)

    async def run(self):
        try:
            while not self.destroyed:
                if not self.queue:
                    self.wakeup.clear()
                    try:
                        await asyncio.wait_for(self.wakeup.wait(), timeout=IDLE_TIMEOUT)
                    except asyncio.TimeoutError:
                        await self.say("💤 Nothing played for a while, so I left the voice channel.")
                        break
                    continue

                track = self.queue.popleft()
                vc = self.vc
                if vc is None or not vc.is_connected():
                    break
                try:
                    source = await self.cog.make_source(track, self.volume / 100)
                except Exception as e:
                    print(f"[music] could not load {track.webpage_url}: {e!r}")
                    await self.say(f"⚠️ I couldn't play **{clean(track.title, 60)}**.\n**Why:** {friendly_error(e)}")
                    continue

                self.current, self.skipped = track, False
                self.started_at, self.paused_at, self.paused_total = time.monotonic(), None, 0.0
                self.done.clear()
                try:
                    vc.play(source, after=self._after)
                except discord.ClientException as e:
                    print(f"[music] play failed: {e!r}")
                    self.current = None
                    continue
                await self.send_now_playing()
                self.ticker = self.bot.loop.create_task(self._tick())
                await self.done.wait()
                self.ticker.cancel()

                finished, self.current = track, None
                if self.loop_mode == "track" and not self.skipped:
                    self.queue.appendleft(finished)
                elif self.loop_mode == "queue":
                    self.queue.append(finished)
                if not self.queue:
                    await self.finish_np()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[music] player loop crashed in {self.guild.id}: {e!r}")
        finally:
            await self.destroy(from_loop=True)

    # ---------- alone-in-channel timer
    def start_alone_timer(self):
        if self.alone_task is None or self.alone_task.done():
            self.alone_task = self.bot.loop.create_task(self._alone_countdown())

    def cancel_alone_timer(self):
        if self.alone_task is not None and not self.alone_task.done():
            self.alone_task.cancel()
        self.alone_task = None

    async def _alone_countdown(self):
        await asyncio.sleep(ALONE_TIMEOUT)
        vc = self.vc
        if vc is not None and vc.channel is not None and not [m for m in vc.channel.members if not m.bot]:
            await self.say("👋 Everyone left, so I left too. See you next time!")
            await self.destroy()

    # ---------- teardown
    async def destroy(self, from_loop: bool = False, disconnect: bool = True):
        if self.destroyed and not from_loop:
            return
        self.destroyed = True
        self.queue.clear()
        self.current = None
        if self.ticker is not None:
            self.ticker.cancel()
        self.cancel_alone_timer()
        await self.clear_np()
        vc = self.vc
        if vc is not None:
            try:
                vc.stop()
                if disconnect:
                    await vc.disconnect(force=True)
            except Exception:
                pass
        if self.cog.players.get(self.guild.id) is self:
            self.cog.players.pop(self.guild.id, None)
        if not from_loop and self.task is not asyncio.current_task():
            self.task.cancel()


# ------------------------------------------------------------------ UI
class QueueView(discord.ui.View):
    def __init__(self, player: GuildPlayer, page: int = 0):
        super().__init__(timeout=120)
        self.player, self.page = player, page
        self._sync()

    def _sync(self):
        _, pages = self.player.queue_embed(self.page)
        self.prev_btn.disabled = self.page <= 0
        self.next_btn.disabled = self.page >= pages - 1

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self._sync()
        await interaction.response.edit_message(embed=self.player.queue_embed(self.page)[0], view=self)

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self._sync()
        await interaction.response.edit_message(embed=self.player.queue_embed(self.page)[0], view=self)


class PlayerView(discord.ui.View):
    """The control panel under the Now Playing message."""

    def __init__(self, player: GuildPlayer):
        super().__init__(timeout=None)
        self.player = player

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        vc = self.player.vc
        voice = getattr(interaction.user, "voice", None)
        if vc is None or voice is None or voice.channel != vc.channel:
            await _err(interaction, "Join my voice channel to use these buttons. 🎧")
            return False
        return True

    async def _refresh(self, interaction: discord.Interaction):
        await interaction.response.edit_message(embed=self.player.now_playing_embed(), view=self)

    @discord.ui.button(emoji="⏯️", label="Pause / Resume", style=discord.ButtonStyle.primary, row=0)
    async def pause_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.player.paused:
            self.player.resume()
        else:
            self.player.pause()
        await self._refresh(interaction)

    @discord.ui.button(emoji="⏭️", label="Skip", style=discord.ButtonStyle.secondary, row=0)
    async def skip_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.skip()
        await interaction.response.defer()

    @discord.ui.button(emoji="⏹️", label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.player.destroy()

    @discord.ui.button(emoji="🔁", label="Loop", style=discord.ButtonStyle.secondary, row=1)
    async def loop_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.cycle_loop()
        await self._refresh(interaction)

    @discord.ui.button(emoji="🔀", label="Shuffle", style=discord.ButtonStyle.secondary, row=1)
    async def shuffle_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.shuffle()
        await self._refresh(interaction)

    @discord.ui.button(emoji="🔉", style=discord.ButtonStyle.secondary, row=2)
    async def vol_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.set_volume(self.player.volume - 10)
        await self._refresh(interaction)

    @discord.ui.button(emoji="🔊", style=discord.ButtonStyle.secondary, row=2)
    async def vol_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.player.set_volume(self.player.volume + 10)
        await self._refresh(interaction)

    @discord.ui.button(emoji="📜", label="Queue", style=discord.ButtonStyle.secondary, row=2)
    async def queue_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed, _ = self.player.queue_embed(0)
        await interaction.response.send_message(embed=embed, view=QueueView(self.player), ephemeral=True)


# ------------------------------------------------------------------ the cog
class Music(commands.Cog):
    music = app_commands.Group(name="music", description="Play music in your voice channel", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    async def cog_load(self):
        if yt_dlp is None:
            raise RuntimeError("yt-dlp is not installed. Run: pip install -r requirements.txt")
        if not discord.voice_client.has_nacl:
            raise RuntimeError("PyNaCl is not installed (needed for voice). Run: pip install -r requirements.txt")
        # Since March 2026 Discord requires DAVE end-to-end encryption for every voice connection.
        if tuple(discord.version_info[:3]) < (2, 7, 0):
            raise RuntimeError("discord.py is too old for voice (Discord now requires DAVE encryption, so the bot "
                               "is disconnected right after joining). Run: pip install -U \"discord.py[voice]\"")
        try:
            import davey  # noqa: F401
        except ImportError:
            raise RuntimeError("The 'davey' package is missing (Discord voice needs it). "
                               "Run: pip install -U \"discord.py[voice]\"")
        problems = []
        if shutil.which("ffmpeg") is None:
            problems.append("FFmpeg was not found (install it, see README)")
        if not found_js_runtimes():
            problems.append("no JavaScript runtime (Node.js or Deno) found, YouTube will likely fail (see README)")
        print(f"🎵 [music] yt-dlp {yt_dlp.version.__version__} | JS runtimes: {found_js_runtimes() or 'none'} | "
              f"cookies: {'yes' if os.path.exists(COOKIE_FILE) else 'no'}")
        for p in problems:
            print(f"⚠️  [music] {p}")

    async def cog_unload(self):
        for player in list(self.players.values()):
            await player.destroy()

    # ---------- yt-dlp (blocking, so it runs in a thread)
    @staticmethod
    def _entry_to_dict(e: dict, single: bool) -> dict:
        url = e.get("webpage_url") or e.get("original_url") or e.get("url") or ""
        if not URL_RE.match(url) and e.get("id"):
            url = f"https://www.youtube.com/watch?v={e['id']}"
        thumb = e.get("thumbnail")
        if not thumb and e.get("thumbnails"):
            thumb = e["thumbnails"][-1].get("url")
        return {
            "title": e.get("title") or "Unknown title",
            "webpage_url": url,
            "duration": int(e["duration"]) if e.get("duration") else None,
            "thumbnail": thumb,
            "uploader": e.get("uploader") or e.get("channel") or "Unknown artist",
            "stream_url": e.get("url") if single and e.get("formats") else None,
        }

    @staticmethod
    def _ydl_extract(base_opts: dict, target: str):
        try:
            with yt_dlp.YoutubeDL(build_opts(base_opts)) as ydl:
                return ydl.extract_info(target, download=False)
        except (TypeError, AttributeError, KeyError, ValueError):
            # the JS-runtime options were not understood by this yt-dlp version: retry without them
            with yt_dlp.YoutubeDL(build_opts(base_opts, with_js=False)) as ydl:
                return ydl.extract_info(target, download=False)

    # ----- SoundCloud fallback (used when YouTube refuses this host)
    def _soundcloud_find(self, title: str, duration: Optional[int]) -> Optional[dict]:
        """Find the closest SoundCloud copy of a song and return yt-dlp info with a playable stream."""
        info = self._ydl_extract(YTDL_SEARCH_OPTS, f"scsearch5:{search_text(title)}")
        entries = [e for e in ((info or {}).get("entries") or []) if e and (e.get("url") or e.get("webpage_url"))]
        if not entries:
            return None
        if duration:
            best = min(entries, key=lambda e: abs((e.get("duration") or 10 ** 6) - duration))
            if best.get("duration") and abs(best["duration"] - duration) > max(45, duration * 0.4):
                return None          # closest result is a different version (preview, remix...), better to say so
        else:
            best = entries[0]
        target = best.get("webpage_url") or best.get("url")
        full = self._ydl_extract(YTDL_STREAM_OPTS, target)
        if full and full.get("entries"):
            full = next((x for x in full["entries"] if x), None)
        if not full or not full.get("url"):
            return None
        full["_fallback"] = "SoundCloud"
        return full

    @staticmethod
    def _oembed(url: str) -> Optional[dict]:
        """YouTube's public oEmbed gives title/artist/thumbnail even when playback is blocked."""
        try:
            api = "https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe="")
            with urllib.request.urlopen(api, timeout=10) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            return None

    def _fallback_lookup(self, query: str, is_url: bool) -> Optional[list[dict]]:
        if is_url:
            if not is_youtube(query):
                return None
            meta = self._oembed(query)
            if not meta or not meta.get("title"):
                return None
            return [{"title": meta["title"], "webpage_url": query, "duration": None,
                     "thumbnail": meta.get("thumbnail_url"), "uploader": meta.get("author_name") or "Unknown artist",
                     "stream_url": None}]
        info = self._ydl_extract(YTDL_SEARCH_OPTS, f"scsearch1:{query}")
        entries = [e for e in ((info or {}).get("entries") or []) if e]
        return [self._entry_to_dict(e, False) for e in entries] or None

    def _extract_sync(self, query: str) -> list[dict]:
        is_url = bool(URL_RE.match(query))
        target = query if is_url else f"ytsearch1:{query}"
        try:
            info = self._ydl_extract(YTDL_SEARCH_OPTS, target)
        except Exception as first_error:
            try:
                alt = self._fallback_lookup(query, is_url)
            except Exception:
                alt = None
            if alt:
                return alt
            raise first_error
        if not info:
            return []
        entries = info.get("entries")
        if entries is None:
            return [self._entry_to_dict(info, True)]
        return [self._entry_to_dict(e, False) for e in list(entries)[:MAX_PLAYLIST] if e]

    def _resolve_sync(self, url: str, title: Optional[str] = None, duration: Optional[int] = None) -> dict:
        try:
            info = self._ydl_extract(YTDL_STREAM_OPTS, url)
            if info and info.get("entries"):
                info = next((e for e in info["entries"] if e), None)
            if not info or not info.get("url"):
                raise RuntimeError("no playable stream found")
            return info
        except Exception as first_error:
            if title and is_youtube(url):
                try:
                    alt = self._soundcloud_find(title, duration)
                except Exception as e:
                    print(f"[music] SoundCloud fallback failed for {title!r}: {e!r}")
                    alt = None
                if alt:
                    print(f"[music] YouTube refused {url}; playing {title!r} from SoundCloud instead")
                    return alt
            raise first_error

    async def extract(self, query: str, requester: discord.abc.User) -> list[Track]:
        loop = asyncio.get_running_loop()
        data = await asyncio.wait_for(loop.run_in_executor(None, self._extract_sync, query), timeout=60)
        out = []
        for d in data:
            if not d["webpage_url"]:
                continue
            t = Track(d["title"], d["webpage_url"], d["duration"], d["thumbnail"], d["uploader"], requester.id)
            if d["stream_url"]:
                t.stream_url, t.resolved_at = d["stream_url"], time.time()
            out.append(t)
        return out

    async def make_source(self, track: Track, volume: float) -> discord.PCMVolumeTransformer:
        url = track.stream_url if track.stream_url and time.time() - track.resolved_at < STREAM_TTL else None
        if url is None:
            loop = asyncio.get_running_loop()
            info = await asyncio.wait_for(
                loop.run_in_executor(None, self._resolve_sync, track.webpage_url, track.title, track.duration), timeout=90)
            url = info["url"]
            track.stream_url, track.resolved_at = url, time.time()
            track.fallback = info.get("_fallback")
            if track.fallback and info.get("duration"):
                track.duration = int(info["duration"])
            if not track.duration and info.get("duration"):
                track.duration = int(info["duration"])
            if not track.thumbnail and info.get("thumbnail"):
                track.thumbnail = info["thumbnail"]
        return discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, **FFMPEG_OPTS), volume=volume)

    # ---------- voice-state tracking (auto-leave + cleanup if the bot is kicked)
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        player = self.players.get(member.guild.id)
        if player is None:
            return
        if member.id == self.bot.user.id:
            if after.channel is None:                       # kicked / disconnected
                await player.destroy(disconnect=False)
            return
        vc = member.guild.voice_client
        if vc is None or vc.channel is None:
            return
        if [m for m in vc.channel.members if not m.bot]:
            player.cancel_alone_timer()
        else:
            player.start_alone_timer()

    async def _leave_if_unused(self, guild: discord.Guild):
        vc = guild.voice_client
        player = self.players.get(guild.id)
        if vc is not None and (player is None or (player.current is None and not player.queue)):
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass

    # ---------- shared checks
    async def _control_player(self, interaction: discord.Interaction, need_playing: bool = False) -> Optional[GuildPlayer]:
        player = self.players.get(interaction.guild.id)
        vc = interaction.guild.voice_client
        if player is None or vc is None:
            await _err(interaction, "I'm not playing anything here. Start with `/music play`! 🎶")
            return None
        voice = getattr(interaction.user, "voice", None)
        if voice is None or voice.channel != vc.channel:
            await _err(interaction, f"Join {vc.channel.mention} to control the music. 🎧")
            return None
        if need_playing and player.current is None:
            await _err(interaction, "Nothing is playing at the moment. 🕸️")
            return None
        return player

    # ================================================================ PLAY
    @music.command(name="play", description="Play a song or playlist (name or link) in your voice channel")
    @app_commands.describe(query="Song name, YouTube/SoundCloud link or YouTube playlist link")
    @app_commands.checks.cooldown(1, 3.0)
    async def play(self, interaction: discord.Interaction, query: str):
        voice = getattr(interaction.user, "voice", None)
        if voice is None or voice.channel is None:
            return await _err(interaction, "Join a voice channel first, then run `/music play`. 🎧")
        if "open.spotify.com" in query or query.startswith("spotify:"):
            return await _err(interaction, "Spotify links can't be streamed. Type the song name instead, like "
                                           "`/music play blinding lights`. 🕸️")
        channel = voice.channel
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.connect and perms.speak):
            return await _err(interaction, f"I need **Connect** and **Speak** permissions in {channel.mention}.")

        await interaction.response.defer()
        guild = interaction.guild
        player = self.players.get(guild.id)
        vc = guild.voice_client
        try:
            if vc is None:
                vc = await channel.connect(self_deaf=True, timeout=20)
            elif vc.channel != channel:
                if player is not None and (player.current or player.queue):
                    return await _err(interaction, f"I'm already playing in {vc.channel.mention}. Join me there! 🎧")
                await vc.move_to(channel)
        except (asyncio.TimeoutError, discord.ClientException) as e:
            return await _err(interaction, f"I couldn't join the voice channel. Try again in a moment. ({type(e).__name__})")

        try:
            tracks = await self.extract(query.strip(), interaction.user)
        except asyncio.TimeoutError:
            return await _err(interaction, "That took too long to look up. Try again or use another link. 🕸️")
        except Exception as e:
            print(f"[music] extract failed for {query!r}: {e!r}")
            await self._leave_if_unused(guild)
            return await _err(interaction, f"I couldn't find or open that.\n**Why:** {friendly_error(e)}")
        if not tracks:
            await self._leave_if_unused(guild)
            return await _err(interaction, "No results. Try another search. 🕸️")

        if player is None or player.destroyed:
            player = GuildPlayer(self, guild, interaction.channel)
            self.players[guild.id] = player
        player.text_channel = interaction.channel
        player.cancel_alone_timer()

        room = MAX_QUEUE - len(player.queue)
        if room <= 0:
            return await _err(interaction, f"The queue is full ({MAX_QUEUE} songs). 🕸️")
        tracks = tracks[:room]
        was_idle = player.current is None and not player.queue
        position = len(player.queue) + (1 if player.current else 0) + 1
        player.queue.extend(tracks)
        player.wakeup.set()

        if len(tracks) > 1:
            embed = web_embed("Playlist Added", f"Added **{len(tracks)}** songs to the queue. 🎶\n"
                                                f"First up: {tracks[0].link}", BLUE)
        elif was_idle:
            embed = web_embed("Loading…", f"Getting {tracks[0].link} ready. 🎶", BLUE)
        else:
            t = tracks[0]
            embed = web_embed("Added To Queue", f"{t.link}\nPosition in queue: **#{position}**", BLUE)
            if t.thumbnail:
                embed.set_thumbnail(url=t.thumbnail)
        await interaction.followup.send(embed=embed)

    # ================================================================ CONTROLS
    @music.command(name="pause", description="Pause the music")
    async def pause(self, interaction: discord.Interaction):
        player = await self._control_player(interaction, need_playing=True)
        if player is None:
            return
        if player.paused:
            return await _err(interaction, "It's already paused. Use `/music resume`.")
        player.pause()
        await player.refresh_np()
        await interaction.response.send_message(embed=web_embed("Paused", "⏸️ Music paused.", BLUE))

    @music.command(name="resume", description="Resume the music")
    async def resume(self, interaction: discord.Interaction):
        player = await self._control_player(interaction, need_playing=True)
        if player is None:
            return
        if not player.paused:
            return await _err(interaction, "The music isn't paused. 🕸️")
        player.resume()
        await player.refresh_np()
        await interaction.response.send_message(embed=web_embed("Resumed", "▶️ Back to the music!", BLUE))

    @music.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        player = await self._control_player(interaction, need_playing=True)
        if player is None:
            return
        title = player.current.link
        player.skip()
        await interaction.response.send_message(embed=web_embed("Skipped", f"⏭️ Skipped {title}", BLUE))

    @music.command(name="stop", description="Stop the music, clear the queue and leave")
    async def stop(self, interaction: discord.Interaction):
        player = await self._control_player(interaction)
        if player is None:
            return
        await interaction.response.send_message(embed=web_embed("Stopped", "⏹️ Stopped the music and left the channel. 🕸️", DARK))
        await player.destroy()

    @music.command(name="nowplaying", description="Show the current song with the control panel")
    async def nowplaying(self, interaction: discord.Interaction):
        player = await self._control_player(interaction, need_playing=True)
        if player is None:
            return
        await interaction.response.send_message(embed=player.now_playing_embed(), view=PlayerView(player))

    @music.command(name="queue", description="Show the song queue")
    async def queue(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild.id)
        if player is None:
            return await _err(interaction, "There's no queue yet. Start with `/music play`! 🎶")
        embed, pages = player.queue_embed(0)
        await interaction.response.send_message(embed=embed, view=QueueView(player) if pages > 1 else discord.utils.MISSING)

    @music.command(name="loop", description="Repeat the current song or the whole queue")
    @app_commands.describe(mode="What to repeat")
    @app_commands.choices(mode=[app_commands.Choice(name="Off", value="off"),
                                app_commands.Choice(name="This song", value="track"),
                                app_commands.Choice(name="Whole queue", value="queue")])
    async def loop(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        player = await self._control_player(interaction)
        if player is None:
            return
        player.loop_mode = mode.value
        await player.refresh_np()
        await interaction.response.send_message(embed=web_embed("Loop", f"🔁 Loop: **{LOOP_LABELS[mode.value]}**", BLUE))

    @music.command(name="shuffle", description="Shuffle the queue")
    async def shuffle(self, interaction: discord.Interaction):
        player = await self._control_player(interaction)
        if player is None:
            return
        if len(player.queue) < 2:
            return await _err(interaction, "Need at least 2 songs in the queue to shuffle. 🕸️")
        player.shuffle()
        await player.refresh_np()
        await interaction.response.send_message(embed=web_embed("Shuffled", "🔀 Queue shuffled!", BLUE))

    @music.command(name="volume", description="Set the volume (1-100)")
    @app_commands.describe(level="Volume from 1 to 100")
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 100]):
        player = await self._control_player(interaction)
        if player is None:
            return
        player.set_volume(level)
        await player.refresh_np()
        await interaction.response.send_message(embed=web_embed("Volume", f"🔊 Volume set to **{level}%**", BLUE))

    @music.command(name="remove", description="Remove a song from the queue")
    @app_commands.describe(position="Its number in /music queue")
    async def remove(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 200]):
        player = await self._control_player(interaction)
        if player is None:
            return
        if position > len(player.queue):
            return await _err(interaction, f"There are only {len(player.queue)} songs in the queue.")
        items = list(player.queue)
        removed = items.pop(position - 1)
        player.queue = deque(items)
        await interaction.response.send_message(embed=web_embed("Removed", f"🗑️ Removed {removed.link}", BLUE))

    @music.command(name="clear", description="Clear the queue (keeps the current song playing)")
    async def clear(self, interaction: discord.Interaction):
        player = await self._control_player(interaction)
        if player is None:
            return
        count = len(player.queue)
        player.queue.clear()
        await interaction.response.send_message(embed=web_embed("Cleared", f"🧹 Removed **{count}** songs from the queue.", BLUE))

    @music.command(name="jump", description="Skip ahead to a song in the queue")
    @app_commands.describe(position="Its number in /music queue")
    async def jump(self, interaction: discord.Interaction, position: app_commands.Range[int, 1, 200]):
        player = await self._control_player(interaction)
        if player is None:
            return
        if position > len(player.queue):
            return await _err(interaction, f"There are only {len(player.queue)} songs in the queue.")
        for _ in range(position - 1):
            player.queue.popleft()
        target = player.queue[0]
        player.skip()
        await interaction.response.send_message(embed=web_embed("Jumped", f"⏩ Jumping to {target.link}", BLUE))

    # ================================================================ DIAGNOSE
    @music.command(name="diagnose", description="Check why music isn't working (server managers only)")
    async def diagnose(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.manage_guild:
            return await _err(interaction, "You need the **Manage Server** permission for this. 🕷️")
        await interaction.response.defer(ephemeral=True)
        ok, bad = "✅", "❌"
        ffmpeg = shutil.which("ffmpeg")
        runtimes = found_js_runtimes()
        age = ytdlp_age_days()
        try:
            import davey  # noqa: F401
            has_davey = True
        except ImportError:
            has_davey = False
        voice_ok = has_davey and tuple(discord.version_info[:3]) >= (2, 7, 0)
        voice_hint = "ready" if voice_ok else 'run pip install -U "discord.py[voice]"'
        lines = [
            f"{ok if voice_ok else bad} **Voice (discord.py {discord.__version__} + davey):** {voice_hint}",
            f"{ok if ffmpeg else bad} **FFmpeg:** {ffmpeg or 'NOT FOUND, install it'}",
            f"{ok if discord.voice_client.has_nacl else bad} **PyNaCl (voice):** "
            f"{'installed' if discord.voice_client.has_nacl else 'missing'}",
            f"{bad if (age is not None and age > 60) else ok} **yt-dlp:** {yt_dlp.version.__version__}"
            + (f" ({age} days old, update with `pip install -U yt-dlp`)" if (age is not None and age > 60) else ""),
            f"{ok if runtimes else bad} **JS runtime:** {', '.join(runtimes) if runtimes else 'NONE, install Node.js or Deno'}",
            f"{'✅' if os.path.exists(COOKIE_FILE) else 'ℹ️'} **cookies.txt:** "
            f"{'found' if os.path.exists(COOKIE_FILE) else 'not used (only needed if YouTube blocks your host)'}",
        ]
        loop = asyncio.get_running_loop()
        yt_ok = sc_ok = False
        started = time.monotonic()
        try:      # YouTube on its own (no fallback), so you can see whether this host is blocked
            info = await asyncio.wait_for(loop.run_in_executor(
                None, self._ydl_extract, YTDL_STREAM_OPTS, "https://www.youtube.com/watch?v=dQw4w9WgXcQ"), 60)
            yt_ok = bool(info and info.get("url"))
            lines.append(f"{ok if yt_ok else bad} **YouTube:** " + (f"works ({time.monotonic() - started:.1f}s)" if yt_ok else "no stream"))
        except Exception as e:
            lines.append(f"{bad} **YouTube:** {friendly_error(e)}")
        started = time.monotonic()
        try:      # SoundCloud, the automatic backup
            info = await asyncio.wait_for(loop.run_in_executor(
                None, self._soundcloud_find, "never gonna give you up", None), 60)
            sc_ok = bool(info and info.get("url"))
            lines.append(f"{ok if sc_ok else bad} **SoundCloud backup:** " + (f"works ({time.monotonic() - started:.1f}s)" if sc_ok else "nothing found"))
        except Exception as e:
            lines.append(f"{bad} **SoundCloud backup:** {friendly_error(e)}")
        if yt_ok:
            verdict = "🎉 **Everything works.** YouTube plays directly."
        elif sc_ok:
            verdict = ("🟡 **Music works through SoundCloud.** YouTube is blocking this host. For YouTube too: run the bot "
                       "on your own PC/home network, or add a `cookies.txt` (see README).")
        else:
            verdict = ("🔴 **Neither source works from this host.** Check the internet/firewall, update yt-dlp "
                       "(`pip install -U yt-dlp`), and see the README.")
        lines += ["", verdict]
        await interaction.followup.send(embed=web_embed("Music Check", "\n".join(lines), RED), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
