import json
import copy
import math
import os
import random
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from theme import web_embed, RED, BLUE, DARK
from moderation import fail

DATA_FILE = "economy.json"

EMOJI = "🕸️"
START_WEBS = 500
MIN_BET = 10
MAX_BET = 100_000

DAILY_BASE, DAILY_STEP, DAILY_MAX_STREAK = 250, 25, 30
WEEKLY_AMOUNT = 2000
WORK_RANGE = (80, 220)
COOLDOWNS = {"daily": 86400, "weekly": 604800, "work": 3600, "rob": 3600}

JOBS = [
    "delivered pizzas across Queens 🍕",
    "stopped a runaway bus 🚌",
    "took photos for the Daily Bugle 📸",
    "rescued a cat from a tree 🐈",
    "fixed web-shooters for a neighbor 🔧",
    "tutored a kid in science 🧪",
    "cleaned graffiti off a skyscraper 🏙️",
    "caught a pickpocket mid-swing 🕷️",
]

# built-in shop items (every server gets these; admins can add role items)
ITEMS = {
    "lock":    {"name": "Web Lock", "emoji": "🔒", "price": 600, "kind": "consumable",
                "desc": "Passive: blocks the next /webs rob against you."},
    "lucky":   {"name": "Lucky Charm", "emoji": "🍀", "price": 2000, "kind": "consumable",
                "desc": "Use it: your next casino win pays +25% extra."},
    "booster": {"name": "Web Booster", "emoji": "⚡", "price": 1500, "kind": "consumable",
                "desc": "Use it: your next /daily, /webs weekly or /work pays double."},
    "mask":    {"name": "Hero Mask", "emoji": "🎭", "price": 15_000, "kind": "collectible",
                "desc": "A collectible to show off."},
    "trophy":  {"name": "Golden Web Trophy", "emoji": "🏆", "price": 75_000, "kind": "collectible",
                "desc": "Pure bragging rights."},
    "crown":   {"name": "Spider Crown", "emoji": "👑", "price": 250_000, "kind": "collectible",
                "desc": "For the richest hero in the web."},
}


# ------------------------------------------------------------------ pure helpers
def parse_amount(text: str, available: int) -> Optional[int]:
    """'500', '2k', '1.5m', 'half', 'all' -> int (or None if invalid)."""
    t = text.strip().lower().replace(",", "").replace("_", "")
    if t == "all":
        return available
    if t == "half":
        return available // 2
    mult = 1
    if t.endswith("k"):
        mult, t = 1_000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    try:
        val = float(t)
    except ValueError:
        return None
    if not math.isfinite(val) or val <= 0:
        return None
    return int(val * mult)


def new_user() -> dict:
    return {
        "webs": START_WEBS, "bank": 0,
        "daily": 0.0, "streak": 0, "weekly": 0.0, "work": 0.0, "rob": 0.0,
        "inv": {}, "buffs": {},
        "stats": {"won": 0, "lost": 0, "games": 0},
    }


# ------------------------------------------------------------------ cog
class Economy(commands.Cog):
    ecoadmin = app_commands.Group(
        name="ecoadmin", description="Manage the web economy", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True))
    webs = app_commands.Group(
        name="webs", description="More web-economy commands: weekly, deposit, withdraw, rob, use",
        guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.data = {"guilds": {}}
        self.dirty = False
        loaded = {}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data.update(loaded)
            except (OSError, json.JSONDecodeError):
                pass

        # One-time migration: old versions stored each member inside each guild.
        # Keep the complete account with the highest net worth (wallet + bank).
        if isinstance(loaded, dict) and isinstance(loaded.get("users"), dict):
            self.data["users"] = loaded["users"]
        else:
            self.data["users"] = self._migrate_global_users()
            self.dirty = True
        self.data.setdefault("guilds", {})
        self.save_loop.start()

    def cog_unload(self):
        self.save_loop.cancel()
        self.save()

    # ---------- storage
    def save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, DATA_FILE)
        self.dirty = False

    @tasks.loop(seconds=10)
    async def save_loop(self):
        if self.dirty:
            self.save()

    @save_loop.before_loop
    async def _wait(self):
        await self.bot.wait_until_ready()

    def _migrate_global_users(self) -> dict:
        """Choose each member's highest-value legacy account across all guilds."""
        chosen = {}
        for guild_data in self.data.get("guilds", {}).values():
            if not isinstance(guild_data, dict):
                continue
            users = guild_data.get("users", {})
            if not isinstance(users, dict):
                continue
            for uid, account in users.items():
                if not isinstance(account, dict):
                    continue
                try:
                    value = int(account.get("webs", START_WEBS)) + int(account.get("bank", 0))
                    wallet = int(account.get("webs", START_WEBS))
                except (TypeError, ValueError):
                    value, wallet = START_WEBS, START_WEBS
                current = chosen.get(str(uid))
                # Prefer the largest wallet balance; break ties by net worth.
                if current is None or (wallet, value) > (current[1], current[0]):
                    chosen[str(uid)] = (value, wallet, copy.deepcopy(account))

        users = {}
        defaults = new_user()
        for uid, (_, __, account) in chosen.items():
            for key, value in defaults.items():
                account.setdefault(key, copy.deepcopy(value))
            if not isinstance(account.get("stats"), dict):
                account["stats"] = copy.deepcopy(defaults["stats"])
            else:
                for key, value in defaults["stats"].items():
                    account["stats"].setdefault(key, value)
            users[uid] = account
        return users

    def _guild(self, gid: int) -> dict:
        # Shop configuration remains server-specific; balances do not.
        g = self.data["guilds"].setdefault(str(gid), {})
        g.setdefault("users", {})  # retained only for legacy data compatibility
        g.setdefault("shop", {})
        return g

    # ---------- API used by the casino cog
    def user(self, gid: int, uid: int) -> dict:
        # gid is retained in the API for compatibility with existing commands/casino.py.
        users = self.data.setdefault("users", {})
        u = users.setdefault(str(uid), new_user())
        for k, v in new_user().items():       # fill any missing keys
            u.setdefault(k, v)
        return u

    def wallet(self, gid: int, uid: int) -> int:
        return self.user(gid, uid)["webs"]

    def add(self, gid: int, uid: int, amount: int):
        u = self.user(gid, uid)
        u["webs"] = max(0, u["webs"] + amount)
        self.dirty = True

    def take(self, gid: int, uid: int, amount: int) -> bool:
        u = self.user(gid, uid)
        if amount <= 0 or u["webs"] < amount:
            return False
        u["webs"] -= amount
        self.dirty = True
        return True

    def mark(self, gid: int, uid: int, profit: int):
        s = self.user(gid, uid)["stats"]
        s["games"] += 1
        if profit > 0:
            s["won"] += profit
        elif profit < 0:
            s["lost"] += -profit
        self.dirty = True

    def lucky_bonus(self, gid: int, uid: int, profit: int):
        """Consume a Lucky Charm (if active) on a winning payout."""
        u = self.user(gid, uid)
        if profit > 0 and u["buffs"].pop("lucky", None):
            self.dirty = True
            bonus = max(1, profit // 4)
            return profit + bonus, f"\n🍀 Lucky Charm added **+{bonus:,}** webs!"
        return profit, ""

    def _boost(self, u: dict, amount: int):
        if u["buffs"].pop("booster", None):
            self.dirty = True
            return amount * 2, "\n⚡ Web Booster doubled it!"
        return amount, ""

    def items(self, gid: int) -> dict:
        merged = dict(ITEMS)
        merged.update(self._guild(gid)["shop"])
        return merged

    def net_worth(self, u: dict) -> int:
        return u["webs"] + u["bank"]

    # ---------- small helpers
    @staticmethod
    def _wait_msg(last: float, cd: int) -> Optional[int]:
        ready = last + cd
        return int(ready) if ready > time.time() else None

    async def item_autocomplete(self, interaction: discord.Interaction, current: str):
        out = []
        for key, it in self.items(interaction.guild.id).items():
            label = f"{it['emoji']} {it['name']} — {it['price']:,} webs"
            if current.lower() in label.lower() or current.lower() in key:
                out.append(app_commands.Choice(name=label[:100], value=key))
        return out[:25]

    # ================================================================ BASICS
    @app_commands.command(name="balance", description="Check your webs")
    @app_commands.describe(member="Whose balance? (defaults to you)")
    @app_commands.guild_only()
    async def balance(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        if member.bot:
            return await fail(interaction, "Bots don't have webs. 🕷️")
        gid = interaction.guild.id
        u = self.user(gid, member.id)
        board = sorted(self.data["users"].items(), key=lambda kv: self.net_worth(kv[1]), reverse=True)
        pos = next((i for i, (uid, _) in enumerate(board, 1) if uid == str(member.id)), 0)
        s = u["stats"]
        desc = (f"{EMOJI} **Wallet:** {u['webs']:,}\n🏦 **Bank:** {u['bank']:,}\n"
                f"💎 **Net worth:** {self.net_worth(u):,}  (rank **#{pos}**)\n\n"
                f"🎰 **Games played:** {s['games']:,}\n"
                f"📈 **Won:** {s['won']:,}  •  📉 **Lost:** {s['lost']:,}")
        if u["buffs"]:
            desc += "\n✨ **Active:** " + ", ".join(f"{ITEMS[k]['emoji']} {ITEMS[k]['name']}" for k in u["buffs"] if k in ITEMS)
        embed = web_embed(f"{member.display_name}'s Webs", desc, BLUE)
        embed.set_thumbnail(url=member.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="daily", description="Claim your daily webs (streaks give bonuses!)")
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction):
        gid, uid, now = interaction.guild.id, interaction.user.id, time.time()
        u = self.user(gid, uid)
        ready = self._wait_msg(u["daily"], COOLDOWNS["daily"])
        if ready:
            return await fail(interaction, f"Your daily web is still spinning! Come back <t:{ready}:R>. 🕸️")
        streak = u["streak"] + 1 if u["daily"] and now - u["daily"] <= 172800 else 1
        amount = DAILY_BASE + DAILY_STEP * min(streak, DAILY_MAX_STREAK)
        amount, note = self._boost(u, amount)
        u.update(daily=now, streak=streak)
        u["webs"] += amount
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Daily Webs!", f"You collected **{amount:,}** webs {EMOJI}\n🔥 **Streak:** {streak} day(s) "
                           f"(max bonus at {DAILY_MAX_STREAK}){note}\n💰 **Wallet:** {u['webs']:,}", BLUE))

    @app_commands.command(name="work", description="Do a hero job for some webs (once an hour)")
    @app_commands.guild_only()
    async def work(self, interaction: discord.Interaction):
        gid, uid = interaction.guild.id, interaction.user.id
        u = self.user(gid, uid)
        ready = self._wait_msg(u["work"], COOLDOWNS["work"])
        if ready:
            return await fail(interaction, f"You're tired from your last job. Rest until <t:{ready}:R>. 🕷️")
        amount, note = self._boost(u, random.randint(*WORK_RANGE))
        u["work"] = time.time()
        u["webs"] += amount
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Hard Day's Work!", f"You {random.choice(JOBS)} and earned **{amount:,}** webs {EMOJI}{note}\n"
                                f"💰 **Wallet:** {u['webs']:,}", BLUE))

    @app_commands.command(name="give", description="Give some of your webs to a friend")
    @app_commands.describe(member="Who gets the webs?", amount="Amount (e.g. 500, 2k, half, all)")
    @app_commands.guild_only()
    async def give(self, interaction: discord.Interaction, member: discord.Member, amount: str):
        gid = interaction.guild.id
        if member.bot or member.id == interaction.user.id:
            return await fail(interaction, "Pick a different human to give webs to. 🕷️")
        value = parse_amount(amount, self.wallet(gid, interaction.user.id))
        if not value:
            return await fail(interaction, "Enter a valid amount like `500`, `2k`, `half` or `all`.")
        if not self.take(gid, interaction.user.id, value):
            return await fail(interaction, "You don't have that many webs in your wallet. 🕸️")
        self.add(gid, member.id, value)
        await interaction.response.send_message(embed=web_embed(
            "Webs Shared!", f"{interaction.user.mention} gave **{value:,}** webs {EMOJI} to {member.mention}! 🕷️\n"
                            f"💰 **Your wallet:** {self.wallet(gid, interaction.user.id):,}", BLUE))

    @app_commands.command(name="richest", description="The richest heroes across Spidey servers")
    @app_commands.guild_only()
    async def richest(self, interaction: discord.Interaction):
        board = sorted(self.data["users"].items(),
                       key=lambda kv: self.net_worth(kv[1]), reverse=True)[:10]
        medals = ["🥇", "🥈", "🥉"]
        lines = [f"{medals[i] if i < 3 else f'**{i + 1}.**'} <@{uid}> — **{self.net_worth(u):,}** webs"
                 for i, (uid, u) in enumerate(board)]
        await interaction.response.send_message(embed=web_embed(
            "Richest Across the Web", "\n".join(lines) or "Nobody has webs yet. 🕸️", BLUE))

    # ================================================================ /webs ...
    @webs.command(name="weekly", description="Claim your weekly webs")
    async def weekly(self, interaction: discord.Interaction):
        gid, uid = interaction.guild.id, interaction.user.id
        u = self.user(gid, uid)
        ready = self._wait_msg(u["weekly"], COOLDOWNS["weekly"])
        if ready:
            return await fail(interaction, f"Your weekly web isn't ready yet. Come back <t:{ready}:R>. 🕸️")
        amount, note = self._boost(u, WEEKLY_AMOUNT)
        u["weekly"] = time.time()
        u["webs"] += amount
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Weekly Webs!", f"You collected **{amount:,}** webs {EMOJI}{note}\n💰 **Wallet:** {u['webs']:,}", BLUE))

    @webs.command(name="deposit", description="Move webs into your safe bank (can't be robbed)")
    @app_commands.describe(amount="Amount (e.g. 500, 2k, half, all)")
    async def deposit(self, interaction: discord.Interaction, amount: str):
        u = self.user(interaction.guild.id, interaction.user.id)
        value = parse_amount(amount, u["webs"])
        if not value or value > u["webs"]:
            return await fail(interaction, "Enter a valid amount you actually have in your wallet.")
        u["webs"] -= value
        u["bank"] += value
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Deposited!", f"🏦 Stored **{value:,}** webs.\n💰 Wallet: {u['webs']:,} • 🏦 Bank: {u['bank']:,}", BLUE),
            ephemeral=True)

    @webs.command(name="withdraw", description="Take webs out of your bank")
    @app_commands.describe(amount="Amount (e.g. 500, 2k, half, all)")
    async def withdraw(self, interaction: discord.Interaction, amount: str):
        u = self.user(interaction.guild.id, interaction.user.id)
        value = parse_amount(amount, u["bank"])
        if not value or value > u["bank"]:
            return await fail(interaction, "Enter a valid amount you actually have in your bank.")
        u["bank"] -= value
        u["webs"] += value
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Withdrawn!", f"💰 Took out **{value:,}** webs.\n💰 Wallet: {u['webs']:,} • 🏦 Bank: {u['bank']:,}", BLUE),
            ephemeral=True)

    @webs.command(name="rob", description="Try to steal webs from someone's wallet (risky!)")
    @app_commands.describe(member="Your target")
    async def rob(self, interaction: discord.Interaction, member: discord.Member):
        gid = interaction.guild.id
        if member.bot or member.id == interaction.user.id:
            return await fail(interaction, "Pick a different human to rob. 🕷️")
        me, target = self.user(gid, interaction.user.id), self.user(gid, member.id)
        ready = self._wait_msg(me["rob"], COOLDOWNS["rob"])
        if ready:
            return await fail(interaction, f"The cops are watching you. Try again <t:{ready}:R>. 🚔")
        if me["webs"] < 200:
            return await fail(interaction, "You need at least **200** webs in your wallet to risk a robbery.")
        if target["webs"] < 100:
            return await fail(interaction, f"{member.display_name} has almost nothing in their wallet.")

        me["rob"] = time.time()
        self.dirty = True
        if target["inv"].get("lock", 0) > 0:
            target["inv"]["lock"] -= 1
            if target["inv"]["lock"] <= 0:
                del target["inv"]["lock"]
            fine = min(me["webs"], max(50, me["webs"] // 10))
            me["webs"] -= fine
            return await interaction.response.send_message(embed=web_embed(
                "Blocked by a Web Lock!", f"🔒 {member.mention}'s **Web Lock** stopped {interaction.user.mention}!\n"
                                          f"You dropped **{fine:,}** webs while running away.", DARK))

        if random.random() < 0.45:
            stolen = max(1, int(target["webs"] * random.uniform(0.15, 0.35)))
            stolen = min(stolen, 25_000)
            target["webs"] -= stolen
            me["webs"] += stolen
            await interaction.response.send_message(embed=web_embed(
                "Web Heist!", f"🕷️ {interaction.user.mention} swiped **{stolen:,}** webs from {member.mention}!", RED))
        else:
            fine = min(me["webs"], max(100, int(me["webs"] * 0.15)))
            me["webs"] -= fine
            target["webs"] += fine // 2
            await interaction.response.send_message(embed=web_embed(
                "Caught in the Act!", f"🚔 {interaction.user.mention} got caught robbing {member.mention} and paid "
                                      f"**{fine:,}** webs. ({member.display_name} got {fine // 2:,} as compensation.)", DARK))

    @webs.command(name="use", description="Activate a Lucky Charm or Web Booster")
    @app_commands.describe(item="Which item?")
    @app_commands.choices(item=[app_commands.Choice(name="🍀 Lucky Charm", value="lucky"),
                                app_commands.Choice(name="⚡ Web Booster", value="booster")])
    async def use(self, interaction: discord.Interaction, item: app_commands.Choice[str]):
        u = self.user(interaction.guild.id, interaction.user.id)
        key = item.value
        if u["inv"].get(key, 0) <= 0:
            return await fail(interaction, f"You don't own a {item.name}. Buy one with `/buy`. 🛒")
        if key in u["buffs"]:
            return await fail(interaction, f"{item.name} is already active!")
        u["inv"][key] -= 1
        if u["inv"][key] <= 0:
            del u["inv"][key]
        u["buffs"][key] = True
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Item Activated!", f"{item.name} is now active. {ITEMS[key]['desc']}", BLUE))

    # ================================================================ SHOP
    @app_commands.command(name="shop", description="Browse the web shop")
    @app_commands.guild_only()
    async def shop(self, interaction: discord.Interaction):
        groups = {"consumable": [], "collectible": [], "role": []}
        for key, it in self.items(interaction.guild.id).items():
            groups.setdefault(it["kind"], []).append(
                f"{it['emoji']} **{it['name']}** — {it['price']:,} {EMOJI}\n┗ {it['desc']}")
        embed = web_embed("Spidey's Web Shop", "Buy with `/buy`. Check your stuff with `/inventory`.", BLUE)
        for kind, label in (("consumable", "🧪 Boosts & tools"), ("collectible", "🏆 Collectibles"), ("role", "🎖️ Server roles")):
            if groups.get(kind):
                embed.add_field(name=label, value="\n".join(groups[kind])[:1024], inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="buy", description="Buy something from the shop")
    @app_commands.describe(item="What to buy", quantity="How many (roles are always 1)")
    @app_commands.guild_only()
    async def buy(self, interaction: discord.Interaction, item: str,
                  quantity: app_commands.Range[int, 1, 99] = 1):
        gid, uid = interaction.guild.id, interaction.user.id
        it = self.items(gid).get(item)
        if not it:
            return await fail(interaction, "That item isn't in the shop. Pick one from the autocomplete list.")
        if it["kind"] == "role":
            quantity = 1
            role = interaction.guild.get_role(it["role_id"])
            if role is None:
                return await fail(interaction, "That role no longer exists.")
            if role in interaction.user.roles:
                return await fail(interaction, "You already have that role!")
            if role >= interaction.guild.me.top_role:
                return await fail(interaction, "That role is above mine, so I can't hand it out. Tell an admin.")
        total = it["price"] * quantity
        if not self.take(gid, uid, total):
            return await fail(interaction, f"You need **{total:,}** webs but only have **{self.wallet(gid, uid):,}**.")
        if it["kind"] == "role":
            try:
                await interaction.user.add_roles(role, reason="Bought in the Spidey web shop")
            except discord.HTTPException:
                self.add(gid, uid, total)
                return await fail(interaction, "I couldn't give you that role, so you were refunded.")
        else:
            inv = self.user(gid, uid)["inv"]
            inv[item] = inv.get(item, 0) + quantity
            self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Purchase Complete!", f"You bought **{quantity}× {it['emoji']} {it['name']}** for **{total:,}** webs.\n"
                                  f"💰 **Wallet:** {self.wallet(gid, uid):,}", BLUE))

    @buy.autocomplete("item")
    async def buy_autocomplete(self, interaction: discord.Interaction, current: str):
        return await self.item_autocomplete(interaction, current)

    @app_commands.command(name="inventory", description="See what you own")
    @app_commands.describe(member="Whose inventory? (defaults to you)")
    @app_commands.guild_only()
    async def inventory(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        member = member or interaction.user
        gid = interaction.guild.id
        u = self.user(gid, member.id)
        items = self.items(gid)
        lines = [f"{items[k]['emoji']} **{items[k]['name']}** × {q}" for k, q in u["inv"].items() if k in items]
        embed = web_embed(f"{member.display_name}'s Inventory", "\n".join(lines) or "Empty. Visit the `/shop`! 🛒", BLUE)
        if u["buffs"]:
            embed.add_field(name="✨ Active boosts",
                            value=", ".join(f"{ITEMS[k]['emoji']} {ITEMS[k]['name']}" for k in u["buffs"] if k in ITEMS))
        await interaction.response.send_message(embed=embed)

    # ================================================================ ADMIN
    @ecoadmin.command(name="add", description="Give webs to a member (free money)")
    async def admin_add(self, interaction: discord.Interaction, member: discord.Member,
                        amount: app_commands.Range[int, 1, 100_000_000]):
        self.add(interaction.guild.id, member.id, amount)
        await interaction.response.send_message(embed=web_embed(
            "Webs Added!", f"Added **{amount:,}** webs to {member.mention}.", BLUE))

    @ecoadmin.command(name="remove", description="Remove webs from a member")
    async def admin_remove(self, interaction: discord.Interaction, member: discord.Member,
                           amount: app_commands.Range[int, 1, 100_000_000]):
        self.add(interaction.guild.id, member.id, -amount)
        await interaction.response.send_message(embed=web_embed(
            "Webs Removed!", f"Removed up to **{amount:,}** webs from {member.mention}.", DARK))

    @ecoadmin.command(name="reset", description="Reset a member's economy data")
    async def admin_reset(self, interaction: discord.Interaction, member: discord.Member):
        self.data.setdefault("users", {}).pop(str(member.id), None)
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Reset!", f"{member.mention}'s economy data was wiped.", DARK))

    @ecoadmin.command(name="addrole", description="Sell a role in the shop")
    @app_commands.describe(role="Role to sell", price="Price in webs", description="Short description")
    async def admin_addrole(self, interaction: discord.Interaction, role: discord.Role,
                            price: app_commands.Range[int, 1, 100_000_000], description: Optional[str] = None):
        if role.managed or role.is_default():
            return await fail(interaction, "I can't hand out that role.")
        if role >= interaction.guild.me.top_role:
            return await fail(interaction, "That role is above mine. Move my role higher in Server Settings → Roles.")
        self._guild(interaction.guild.id)["shop"][f"role_{role.id}"] = {
            "name": role.name, "emoji": "🎖️", "price": price, "kind": "role", "role_id": role.id,
            "desc": (description or f"Unlocks the {role.name} role.")[:100],
        }
        self.dirty = True
        await interaction.response.send_message(embed=web_embed(
            "Role Added to Shop!", f"{role.mention} now costs **{price:,}** webs.", BLUE))

    @ecoadmin.command(name="removeitem", description="Remove a role from the shop")
    async def admin_removeitem(self, interaction: discord.Interaction, item: str):
        if self._guild(interaction.guild.id)["shop"].pop(item, None) is None:
            return await fail(interaction, "That's not a custom shop item (built-in items can't be removed).")
        self.dirty = True
        await interaction.response.send_message(embed=web_embed("Item Removed!", "It's gone from the shop. 🕸️", DARK))

    @admin_removeitem.autocomplete("item")
    async def removeitem_autocomplete(self, interaction: discord.Interaction, current: str):
        custom = self._guild(interaction.guild.id)["shop"]
        return [c for c in await self.item_autocomplete(interaction, current) if c.value in custom]


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
