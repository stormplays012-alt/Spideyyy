import asyncio
import random
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK
from moderation import fail
from economy import parse_amount, MIN_BET, MAX_BET, EMOJI

# ------------------------------------------------------------------ pure game logic
SLOT_SYMBOLS = ["🕸️", "🪰", "🐞", "🕷️", "💎", "👑"]
SLOT_WEIGHTS = [35, 25, 18, 12, 7, 3]
SLOT_TRIPLE = {"🕸️": 3, "🪰": 5, "🐞": 8, "🕷️": 15, "💎": 40, "👑": 100}
SLOT_PAIR = 1.2

SUITS = ["♠️", "♥️", "♦️", "♣️"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]

MINES_N = 20            # 5 x 4 grid
MINES_EDGE = 0.95       # house keeps 5%
MINES_MAX_MULT = 100.0  # auto cash-out cap

RED_NUMBERS = {1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36}


def slot_payout(reels: list, bet: int) -> int:
    if reels[0] == reels[1] == reels[2]:
        return bet * SLOT_TRIPLE[reels[0]]
    if len(set(reels)) == 2:
        return int(bet * SLOT_PAIR)
    return 0


def card_value(rank: str) -> int:
    if rank == "A":
        return 11
    if rank in ("J", "Q", "K"):
        return 10
    return int(rank)


def hand_total(cards: list) -> int:
    total = sum(card_value(r) for r, _ in cards)
    aces = sum(1 for r, _ in cards if r == "A")
    while total > 21 and aces:
        total -= 10
        aces -= 1
    return total


def fmt_cards(cards: list) -> str:
    return "  ".join(f"`{r}`{s}" for r, s in cards)


def mines_multiplier(safe_found: int, mines: int) -> float:
    if safe_found <= 0:
        return 1.0
    mult = 1.0
    for i in range(safe_found):
        mult *= (MINES_N - i) / (MINES_N - mines - i)
    return mult * MINES_EDGE


def hilo_name(v: int) -> str:
    return {11: "J", 12: "Q", 13: "K", 14: "A"}.get(v, str(v))


def roulette_color(n: int) -> str:
    return "🟢" if n == 0 else ("🔴" if n in RED_NUMBERS else "⚫")


def roulette_result(choice: str, n: int):
    """-> (won?, multiplier) for a bet choice, or None if the choice is invalid."""
    c = choice.strip().lower()
    if c.isdigit() and 0 <= int(c) <= 36:
        return int(c) == n, 36
    if c == "green":
        return n == 0, 36
    if c == "red":
        return n in RED_NUMBERS, 2
    if c == "black":
        return n != 0 and n not in RED_NUMBERS, 2
    if c == "even":
        return n != 0 and n % 2 == 0, 2
    if c == "odd":
        return n % 2 == 1, 2
    if c == "low":
        return 1 <= n <= 18, 2
    if c == "high":
        return 19 <= n <= 36, 2
    return None


# ------------------------------------------------------------------ blackjack
class BlackjackView(discord.ui.View):
    def __init__(self, cog: "Casino", user: discord.Member, bet: int):
        super().__init__(timeout=90)
        self.cog, self.user, self.bet = cog, user, bet
        self.key = (user.guild.id, user.id)
        self.deck = [(r, s) for r in RANKS for s in SUITS]
        random.shuffle(self.deck)
        self.player = [self.deck.pop(), self.deck.pop()]
        self.dealer = [self.deck.pop(), self.deck.pop()]
        self.finished = False
        self.message: Optional[discord.Message] = None

    def embed(self, outcome: Optional[str] = None, payout: int = 0, profit: int = 0, note: str = "") -> discord.Embed:
        reveal = outcome is not None
        colors = {None: RED, "blackjack": BLUE, "win": BLUE, "push": RED, "lose": DARK, "bust": DARK}
        embed = web_embed("Blackjack", f"{self.user.mention}'s table 🕷️", colors[outcome])
        embed.add_field(name=f"Your hand ({hand_total(self.player)})", value=fmt_cards(self.player), inline=False)
        if reveal:
            embed.add_field(name=f"Dealer ({hand_total(self.dealer)})", value=fmt_cards(self.dealer), inline=False)
        else:
            embed.add_field(name="Dealer", value=f"{fmt_cards(self.dealer[:1])}  ❓", inline=False)
        embed.add_field(name="Bet", value=f"{self.bet:,} {EMOJI}")
        if reveal:
            text = {"blackjack": "🎉 **Blackjack!** Pays 3:2.", "win": "✅ **You win!**",
                    "push": "🤝 **Push.** Your bet is returned.", "lose": "❌ **Dealer wins.**",
                    "bust": "💥 **Bust!** Over 21."}[outcome]
            sign = f"+{profit:,}" if profit > 0 else f"{profit:,}"
            embed.add_field(name="Result", value=f"{text}\n**{sign}** webs{note}\n"
                                                 f"💰 Balance: {self.cog.eco.wallet(*self.key):,}", inline=False)
        return embed

    async def finish(self, interaction: Optional[discord.Interaction], outcome: str):
        if self.finished:
            return
        self.finished = True
        for item in self.children:
            item.disabled = True
        payout = {"blackjack": int(self.bet * 2.5), "win": self.bet * 2, "push": self.bet}.get(outcome, 0)
        payout, profit, note = self.cog.settle(*self.key, self.bet, payout)
        self.cog.busy.discard(self.user.id)
        self.stop()
        embed = self.embed(outcome, payout, profit, note)
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def stand(self, interaction: Optional[discord.Interaction]):
        while hand_total(self.dealer) < 17:
            self.dealer.append(self.deck.pop())
        p, d = hand_total(self.player), hand_total(self.dealer)
        outcome = "win" if (d > 21 or p > d) else ("push" if p == d else "lose")
        await self.finish(interaction, outcome)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This isn't your table! Start one with `/blackjack`. 🕷️", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if not self.finished:
            await self.stand(None)

    @discord.ui.button(label="Hit", emoji="🃏", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.double.disabled = True
        self.player.append(self.deck.pop())
        total = hand_total(self.player)
        if total > 21:
            return await self.finish(interaction, "bust")
        if total == 21:
            return await self.stand(interaction)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Stand", emoji="✋", style=discord.ButtonStyle.secondary)
    async def stand_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.stand(interaction)

    @discord.ui.button(label="Double", emoji="✖️", style=discord.ButtonStyle.success)
    async def double(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.cog.eco.take(*self.key, self.bet):
            return await interaction.response.send_message("You don't have enough webs to double down. 🕸️", ephemeral=True)
        self.bet *= 2
        self.player.append(self.deck.pop())
        if hand_total(self.player) > 21:
            return await self.finish(interaction, "bust")
        await self.stand(interaction)


# ------------------------------------------------------------------ mines
class MineTile(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(emoji="❓", style=discord.ButtonStyle.secondary, row=index // 5)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await self.view.pick(interaction, self)


class MinesView(discord.ui.View):
    def __init__(self, cog: "Casino", user: discord.Member, bet: int, mines: int):
        super().__init__(timeout=120)
        self.cog, self.user, self.bet, self.mines = cog, user, bet, mines
        self.key = (user.guild.id, user.id)
        self.mine_set = set(random.sample(range(MINES_N), mines))
        self.revealed: set[int] = set()
        self.finished = False
        self.message: Optional[discord.Message] = None
        self.tiles = [MineTile(i) for i in range(MINES_N)]
        for t in self.tiles:
            self.add_item(t)
        self.cash_button = discord.ui.Button(label="Cash Out", emoji="💰", style=discord.ButtonStyle.success,
                                             row=4, disabled=True)
        self.cash_button.callback = self.cash_callback
        self.add_item(self.cash_button)

    @property
    def mult(self) -> float:
        return min(mines_multiplier(len(self.revealed), self.mines), MINES_MAX_MULT)

    @property
    def potential(self) -> int:
        return int(self.bet * self.mult)

    def embed(self, status: str = "", color: int = RED) -> discord.Embed:
        desc = (f"{self.user.mention}, pick boxes! Each safe box {EMOJI} raises your multiplier.\n"
                "Hit a mine 💥 and you lose your bet. Cash out any time!")
        embed = web_embed("Mines", desc, color)
        embed.add_field(name="Bet", value=f"{self.bet:,}")
        embed.add_field(name="Mines", value=str(self.mines))
        embed.add_field(name="Safe found", value=f"{len(self.revealed)}/{MINES_N - self.mines}")
        embed.add_field(name="Multiplier", value=f"{self.mult:.2f}x")
        embed.add_field(name="Cash out now", value=f"{self.potential:,}")
        if status:
            embed.add_field(name="Result", value=status, inline=False)
        return embed

    def refresh_cash_button(self):
        self.cash_button.disabled = len(self.revealed) == 0 or self.finished
        self.cash_button.label = f"Cash Out · {self.potential:,}"

    def reveal_all(self):
        for t in self.tiles:
            t.disabled = True
            if t.index in self.mine_set:
                t.emoji, t.style = "💥", discord.ButtonStyle.danger
            elif t.index not in self.revealed:
                t.emoji, t.style = EMOJI, discord.ButtonStyle.secondary
        self.cash_button.disabled = True

    async def end(self, interaction: Optional[discord.Interaction], won: bool, reason: str):
        if self.finished:
            return
        self.finished = True
        payout = self.potential if won else 0
        payout, profit, note = self.cog.settle(*self.key, self.bet, payout)
        self.cog.busy.discard(self.user.id)
        self.reveal_all()
        self.stop()
        sign = f"+{profit:,}" if profit > 0 else f"{profit:,}"
        status = f"{reason}\n**{sign}** webs{note}\n💰 Balance: {self.cog.eco.wallet(*self.key):,}"
        embed = self.embed(status, BLUE if profit > 0 else DARK)
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def pick(self, interaction: discord.Interaction, tile: MineTile):
        if tile.index in self.mine_set:
            tile.emoji, tile.style = "💥", discord.ButtonStyle.danger
            return await self.end(interaction, False, "💥 **BOOM!** You hit a mine and lost your bet.")
        self.revealed.add(tile.index)
        tile.emoji, tile.style, tile.disabled = EMOJI, discord.ButtonStyle.success, True
        if self.mult >= MINES_MAX_MULT or len(self.revealed) == MINES_N - self.mines:
            return await self.end(interaction, True, "🏆 **Max win reached!** Auto cashed out.")
        self.refresh_cash_button()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def cash_callback(self, interaction: discord.Interaction):
        await self.end(interaction, True, f"💰 **Escaped the mines** at {self.mult:.2f}x!")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This isn't your mine! Start one with `/mines`. 🕷️", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.finished:
            return
        if self.revealed:
            await self.end(None, True, "⏰ Timed out, so you were auto cashed out.")
        else:
            await self._refund()

    async def _refund(self):
        self.finished = True
        self.cog.eco.add(*self.key, self.bet)
        self.cog.busy.discard(self.user.id)
        self.reveal_all()
        if self.message is not None:
            try:
                await self.message.edit(embed=self.embed("⏰ Timed out with no picks, so your bet was refunded.", DARK), view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ hi-lo
class HiLoView(discord.ui.View):
    MAX_MULT = 50.0

    def __init__(self, cog: "Casino", user: discord.Member, bet: int):
        super().__init__(timeout=90)
        self.cog, self.user, self.bet = cog, user, bet
        self.key = (user.guild.id, user.id)
        self.card = random.randint(2, 14)
        self.suit = random.choice(SUITS)
        self.mult = 1.0
        self.streak = 0
        self.finished = False
        self.message: Optional[discord.Message] = None
        self.update_buttons()

    @property
    def potential(self) -> int:
        return int(self.bet * self.mult)

    def p_higher(self) -> float:
        return (14 - self.card) / 13

    def p_lower(self) -> float:
        return (self.card - 2) / 13

    def update_buttons(self):
        self.higher.disabled = self.p_higher() <= 0
        self.lower.disabled = self.p_lower() <= 0
        self.higher.label = f"Higher ({0.95 / self.p_higher():.2f}x)" if self.p_higher() > 0 else "Higher"
        self.lower.label = f"Lower ({0.95 / self.p_lower():.2f}x)" if self.p_lower() > 0 else "Lower"
        self.cash.disabled = self.streak == 0
        self.cash.label = f"Cash Out · {self.potential:,}"

    def embed(self, status: str = "", color: int = RED) -> discord.Embed:
        embed = web_embed("Hi-Lo", f"{self.user.mention}, will the next card be higher or lower?\n"
                                   "Ties lose. Cash out whenever you like!", color)
        embed.add_field(name="Current card", value=f"`{hilo_name(self.card)}`{self.suit}")
        embed.add_field(name="Streak", value=str(self.streak))
        embed.add_field(name="Multiplier", value=f"{self.mult:.2f}x")
        embed.add_field(name="Bet", value=f"{self.bet:,}")
        if status:
            embed.add_field(name="Result", value=status, inline=False)
        return embed

    async def end(self, interaction: Optional[discord.Interaction], won: bool, reason: str):
        if self.finished:
            return
        self.finished = True
        payout, profit, note = self.cog.settle(*self.key, self.bet, self.potential if won else 0)
        self.cog.busy.discard(self.user.id)
        for item in self.children:
            item.disabled = True
        self.stop()
        sign = f"+{profit:,}" if profit > 0 else f"{profit:,}"
        status = f"{reason}\n**{sign}** webs{note}\n💰 Balance: {self.cog.eco.wallet(*self.key):,}"
        embed = self.embed(status, BLUE if profit > 0 else DARK)
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def guess(self, interaction: discord.Interaction, higher: bool):
        p = self.p_higher() if higher else self.p_lower()
        nxt, suit = random.randint(2, 14), random.choice(SUITS)
        correct = nxt > self.card if higher else nxt < self.card
        old = self.card
        self.card, self.suit = nxt, suit
        if not correct:
            reason = (f"❌ The next card was `{hilo_name(nxt)}`{suit} after `{hilo_name(old)}`"
                      + (" (a tie loses)." if nxt == old else "."))
            return await self.end(interaction, False, reason)
        self.mult *= 0.95 / p
        self.streak += 1
        if self.mult >= self.MAX_MULT:
            self.mult = self.MAX_MULT
            return await self.end(interaction, True, "🏆 **Max win reached!** Auto cashed out.")
        self.update_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user.id:
            await interaction.response.send_message("This isn't your game! Start one with `/casino hilo`. 🕷️", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if not self.finished:
            if self.streak:
                await self.end(None, True, "⏰ Timed out, so you were auto cashed out.")
            else:
                self.finished = True
                self.cog.eco.add(*self.key, self.bet)
                self.cog.busy.discard(self.user.id)
                for item in self.children:
                    item.disabled = True
                if self.message is not None:
                    try:
                        await self.message.edit(embed=self.embed("⏰ Timed out, so your bet was refunded.", DARK), view=self)
                    except discord.HTTPException:
                        pass

    @discord.ui.button(label="Higher", emoji="⬆️", style=discord.ButtonStyle.primary)
    async def higher(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.guess(interaction, True)

    @discord.ui.button(label="Lower", emoji="⬇️", style=discord.ButtonStyle.primary)
    async def lower(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.guess(interaction, False)

    @discord.ui.button(label="Cash Out", emoji="💰", style=discord.ButtonStyle.success)
    async def cash(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.end(interaction, True, f"💰 **Cashed out** at {self.mult:.2f}x!")


# ------------------------------------------------------------------ the cog
class Casino(commands.Cog):
    casino = app_commands.Group(name="casino", description="More web games: hilo, roulette, dice, rps",
                                guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.busy: set[int] = set()

    @property
    def eco(self):
        return self.bot.get_cog("Economy")

    # ---------- betting helpers
    async def start_bet(self, interaction: discord.Interaction, bet_text: str) -> Optional[int]:
        """Validate the bet, take it from the wallet and lock the player. Returns the bet or None."""
        eco = self.eco
        if eco is None:
            await fail(interaction, "The economy isn't loaded right now.")
            return None
        gid, uid = interaction.guild.id, interaction.user.id
        if uid in self.busy:
            await fail(interaction, "You already have a game running! Finish it first. 🕷️")
            return None
        wallet = eco.wallet(gid, uid)
        amount = parse_amount(bet_text, wallet)
        if amount is None:
            await fail(interaction, "Enter a valid bet like `500`, `2k`, `half` or `all`.")
            return None
        if bet_text.strip().lower() in ("all", "half"):
            amount = min(amount, MAX_BET)
        if amount < MIN_BET:
            await fail(interaction, f"The minimum bet is **{MIN_BET}** webs.")
            return None
        if amount > MAX_BET:
            await fail(interaction, f"The maximum bet is **{MAX_BET:,}** webs.")
            return None
        if not eco.take(gid, uid, amount):
            await fail(interaction, f"You only have **{wallet:,}** webs in your wallet. 🕸️")
            return None
        self.busy.add(uid)
        return amount

    def abort(self, gid: int, uid: int, amount: int):
        """Give the bet back (used if Discord fails before the game starts)."""
        self.eco.add(gid, uid, amount)
        self.busy.discard(uid)

    def settle(self, gid: int, uid: int, bet: int, payout: int):
        """Pay out `payout` (total returned, 0 = lost). Returns (payout, profit, note)."""
        note = ""
        profit = payout - bet
        if profit > 0:
            profit, note = self.eco.lucky_bonus(gid, uid, profit)
            payout = bet + profit
        self.eco.add(gid, uid, payout)
        self.eco.mark(gid, uid, profit)
        return payout, profit, note

    def result_embed(self, title: str, body: str, profit: int, gid: int, uid: int, note: str = "") -> discord.Embed:
        sign = f"+{profit:,}" if profit > 0 else f"{profit:,}"
        label = "🎉 You won" if profit > 0 else ("🤝 Push" if profit == 0 else "❌ You lost")
        desc = f"{body}\n\n{label}: **{sign}** webs{note}\n💰 **Balance:** {self.eco.wallet(gid, uid):,}"
        return web_embed(title, desc, BLUE if profit > 0 else (RED if profit == 0 else DARK))

    # ================================================================ COIN FLIP
    @app_commands.command(name="coinflip", description="Flip a coin and double your webs")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)", side="Heads or tails?")
    @app_commands.choices(side=[app_commands.Choice(name="Heads", value="heads"),
                                app_commands.Choice(name="Tails", value="tails")])
    @app_commands.guild_only()
    async def coinflip(self, interaction: discord.Interaction, bet: str, side: app_commands.Choice[str]):
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        try:
            await interaction.response.send_message(embed=web_embed(
                "Coin Flip", f"{interaction.user.mention} bets **{amount:,}** webs on **{side.name}**.\n"
                             "🪙 The coin spins through the web…", RED))
        except discord.HTTPException:
            return self.abort(gid, uid, amount)
        result = random.choice(["heads", "tails"])
        won = result == side.value
        payout, profit, note = self.settle(gid, uid, amount, amount * 2 if won else 0)
        self.busy.discard(uid)
        await asyncio.sleep(1.5)
        body = f"The coin landed on **{result.title()}** {'🪙' if won else '💨'}"
        await interaction.edit_original_response(embed=self.result_embed("Coin Flip", body, profit, gid, uid, note))

    # ================================================================ SLOTS
    @app_commands.command(name="slots", description="Spin the web slot machine")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)")
    @app_commands.guild_only()
    async def slots(self, interaction: discord.Interaction, bet: str):
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        paytable = ("3 match: 🕸️×3 • 🪰×5 • 🐞×8 • 🕷️×15 • 💎×40 • 👑×100\nAny pair: ×1.2")
        try:
            await interaction.response.send_message(embed=web_embed(
                "Web Slots", f"{interaction.user.mention} bets **{amount:,}** webs.\n\n"
                             f"┃ ❓ ┃ ❓ ┃ ❓ ┃\n\n{paytable}", RED))
        except discord.HTTPException:
            return self.abort(gid, uid, amount)
        reels = random.choices(SLOT_SYMBOLS, SLOT_WEIGHTS, k=3)
        payout, profit, note = self.settle(gid, uid, amount, slot_payout(reels, amount))
        self.busy.discard(uid)

        shown = ["❓", "❓", "❓"]
        for i in range(3):
            await asyncio.sleep(0.9)
            shown[i] = reels[i]
            if i < 2:
                await interaction.edit_original_response(embed=web_embed(
                    "Web Slots", f"{interaction.user.mention} bets **{amount:,}** webs.\n\n"
                                 f"┃ {' ┃ '.join(shown)} ┃\n\n{paytable}", RED))
        if reels[0] == reels[1] == reels[2]:
            headline = "🎊 **JACKPOT!** Three of a kind!"
        elif len(set(reels)) == 2:
            headline = "✨ A pair!"
        else:
            headline = "No match this time."
        body = f"┃ {' ┃ '.join(reels)} ┃\n{headline}"
        await interaction.edit_original_response(embed=self.result_embed("Web Slots", body, profit, gid, uid, note))

    # ================================================================ BLACKJACK
    @app_commands.command(name="blackjack", description="Play blackjack against Spidey")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)")
    @app_commands.guild_only()
    async def blackjack(self, interaction: discord.Interaction, bet: str):
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        view = BlackjackView(self, interaction.user, amount)
        try:
            await interaction.response.send_message(embed=view.embed(), view=view)
            view.message = await interaction.original_response()
        except discord.HTTPException:
            return self.abort(gid, uid, amount)
        if hand_total(view.player) == 21:  # natural blackjack
            await view.finish(None, "push" if hand_total(view.dealer) == 21 else "blackjack")

    # ================================================================ MINES
    @app_commands.command(name="mines", description="Pick boxes, avoid the mines, cash out before it's too late")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)", mines="How many mines hide in the 20 boxes (1-10)")
    @app_commands.guild_only()
    async def mines(self, interaction: discord.Interaction, bet: str,
                    mines: app_commands.Range[int, 1, 10] = 3):
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        view = MinesView(self, interaction.user, amount, mines)
        view.refresh_cash_button()
        try:
            await interaction.response.send_message(embed=view.embed(), view=view)
            view.message = await interaction.original_response()
        except discord.HTTPException:
            self.abort(gid, uid, amount)

    # ================================================================ HI-LO
    @casino.command(name="hilo", description="Guess higher or lower and build a streak")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)")
    async def hilo(self, interaction: discord.Interaction, bet: str):
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        view = HiLoView(self, interaction.user, amount)
        try:
            await interaction.response.send_message(embed=view.embed(), view=view)
            view.message = await interaction.original_response()
        except discord.HTTPException:
            self.abort(gid, uid, amount)

    # ================================================================ ROULETTE
    @casino.command(name="roulette", description="Bet on a color, odd/even, high/low or a number")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)",
                           choice="red, black, even, odd, low (1-18), high (19-36), green, or a number 0-36")
    async def roulette(self, interaction: discord.Interaction, bet: str, choice: str):
        if roulette_result(choice, 0) is None:
            return await fail(interaction, "Choose `red`, `black`, `even`, `odd`, `low`, `high`, `green` or a number from 0 to 36.")
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        try:
            await interaction.response.send_message(embed=web_embed(
                "Web Roulette", f"{interaction.user.mention} bets **{amount:,}** webs on **{choice.lower()}**.\n"
                                "🎡 The wheel is spinning…", RED))
        except discord.HTTPException:
            return self.abort(gid, uid, amount)
        n = random.randint(0, 36)
        won, mult = roulette_result(choice, n)
        payout, profit, note = self.settle(gid, uid, amount, amount * mult if won else 0)
        self.busy.discard(uid)
        await asyncio.sleep(2)
        body = f"The ball landed on {roulette_color(n)} **{n}**."
        await interaction.edit_original_response(embed=self.result_embed("Web Roulette", body, profit, gid, uid, note))

    @roulette.autocomplete("choice")
    async def roulette_autocomplete(self, interaction: discord.Interaction, current: str):
        names = ["red", "black", "even", "odd", "low", "high", "green"]
        return [app_commands.Choice(name=n, value=n) for n in names if current.lower() in n][:25]

    # ================================================================ DICE DUEL
    @casino.command(name="dice", description="Roll two dice against Spidey (higher total wins)")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)")
    async def dice(self, interaction: discord.Interaction, bet: str):
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        try:
            await interaction.response.send_message(embed=web_embed(
                "Dice Duel", f"{interaction.user.mention} bets **{amount:,}** webs.\n🎲 Rolling the dice…", RED))
        except discord.HTTPException:
            return self.abort(gid, uid, amount)
        mine = [random.randint(1, 6), random.randint(1, 6)]
        theirs = [random.randint(1, 6), random.randint(1, 6)]
        a, b = sum(mine), sum(theirs)
        payout = int(amount * 1.9) if a > b else (amount if a == b else 0)
        payout, profit, note = self.settle(gid, uid, amount, payout)
        self.busy.discard(uid)
        await asyncio.sleep(1.2)
        verdict = "You win! Pays 1.9x." if a > b else ("A tie." if a == b else "Spidey wins.")
        body = f"🎲 **You:** {mine[0]} + {mine[1]} = **{a}**\n🕷️ **Spidey:** {theirs[0]} + {theirs[1]} = **{b}**\n{verdict}"
        await interaction.edit_original_response(embed=self.result_embed("Dice Duel", body, profit, gid, uid, note))

    # ================================================================ ROCK PAPER SCISSORS
    @casino.command(name="rps", description="Rock paper scissors against Spidey")
    @app_commands.describe(bet="Amount (e.g. 500, 2k, half, all)", choice="Your move")
    @app_commands.choices(choice=[app_commands.Choice(name="🪨 Rock", value="rock"),
                                  app_commands.Choice(name="📄 Paper", value="paper"),
                                  app_commands.Choice(name="✂️ Scissors", value="scissors")])
    async def rps(self, interaction: discord.Interaction, bet: str, choice: app_commands.Choice[str]):
        amount = await self.start_bet(interaction, bet)
        if amount is None:
            return
        gid, uid = interaction.guild.id, interaction.user.id
        emojis = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
        beats = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
        spidey = random.choice(list(beats))
        if choice.value == spidey:
            payout, verdict = amount, "A tie!"
        elif beats[choice.value] == spidey:
            payout, verdict = int(amount * 1.9), "You win! Pays 1.9x."
        else:
            payout, verdict = 0, "Spidey wins."
        payout, profit, note = self.settle(gid, uid, amount, payout)
        self.busy.discard(uid)
        body = f"You: {emojis[choice.value]} **{choice.value}**\nSpidey: {emojis[spidey]} **{spidey}**\n{verdict}"
        await interaction.response.send_message(embed=self.result_embed("Rock Paper Scissors", body, profit, gid, uid, note))


async def setup(bot: commands.Bot):
    await bot.add_cog(Casino(bot))
