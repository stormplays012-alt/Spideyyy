import asyncio
import json
import os
import random
import time
from datetime import timedelta
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from theme import web_embed, RED, BLUE, DARK
from moderation import fail

DATA_FILE = "minigames.json"
REWARD_CAP_PER_DAY = 20      # how many web rewards one player can earn per day from games
TRIVIA_REWARD = 30
HANGMAN_REWARD = 40
CATCH_REWARD = 25
DAILY_BASE, DAILY_STEP, DAILY_MAX_STEPS = 100, 25, 10

# ------------------------------------------------------------------ trivia bank: (category, question, correct, [wrong x3])
QUESTIONS = [
    ("Spiders", "How many legs does a spider have?", "8", ["6", "10", "12"]),
    ("Spiders", "Spiders belong to which class of animals?", "Arachnids", ["Insects", "Crustaceans", "Reptiles"]),
    ("Spiders", "Which of these is NOT an arachnid?", "Ant", ["Scorpion", "Tick", "Mite"]),
    ("Spiders", "What do spiders use to spin their webs?", "Silk", ["Wool", "Sticky saliva", "Plant fibers"]),
    ("Spiders", "What is the fear of spiders called?", "Arachnophobia", ["Acrophobia", "Claustrophobia", "Entomophobia"]),
    ("Spiders", "Which spider is known for the red hourglass marking on its belly?", "Black widow", ["Wolf spider", "Jumping spider", "Orb weaver"]),
    ("Spiders", "The Goliath birdeater is a type of what?", "Tarantula", ["Scorpion", "Centipede", "Beetle"]),
    ("Spiders", "Which organs on a spider produce silk?", "Spinnerets", ["Fangs", "Pedipalps", "Book lungs"]),
    ("Spiders", "Most spiders have how many eyes?", "8", ["2", "4", "10"]),
    ("Spiders", "Which spider family is famous for its powerful jumps?", "Jumping spiders", ["Orb weavers", "Funnel-web spiders", "Cellar spiders"]),
    ("Spiders", "What do orb-weaver spiders' webs look like?", "A round wheel", ["A funnel", "A flat sheet", "A tangled cloud"]),
    ("Spiders", "Which spider has the longest leg span in the world?", "Giant huntsman spider", ["Black widow", "Wolf spider", "Zebra jumper"]),
    ("Heroes", "What is Peter Parker's famous uncle's name?", "Ben", ["Tony", "Steve", "Harry"]),
    ("Heroes", "Which city is Spider-Man's home?", "New York City", ["Gotham City", "Metropolis", "Los Angeles"]),
    ("Heroes", "Spider-Man was co-created by Stan Lee and which artist?", "Steve Ditko", ["Jack Kirby", "Bob Kane", "Frank Miller"]),
    ("Heroes", "Which newspaper does Peter Parker sell photos to?", "The Daily Bugle", ["The Daily Planet", "The Gotham Gazette", "The Times"]),
    ("Heroes", "Which villain wears a green costume and rides a glider?", "Green Goblin", ["Venom", "Sandman", "Vulture"]),
    ("Heroes", "Which Spider-Man villain has four mechanical arms?", "Doctor Octopus", ["Electro", "Lizard", "Mysterio"]),
    ("Heroes", "What is Peter Parker's aunt's name?", "May", ["June", "April", "Mary"]),
    ("Heroes", "In what year did Spider-Man first appear in comics?", "1962", ["1955", "1971", "1985"]),
    ("Heroes", "Which Spider-Man villain is a man made of sand?", "Sandman", ["Rhino", "Scorpion", "Shocker"]),
    ("Heroes", "With great power comes great…?", "Responsibility", ["Danger", "Fame", "Speed"]),
    ("Science", "Which planet is known as the Red Planet?", "Mars", ["Venus", "Jupiter", "Mercury"]),
    ("Science", "What is the chemical formula for water?", "H2O", ["CO2", "O2", "NaCl"]),
    ("Science", "What is the powerhouse of the cell?", "Mitochondria", ["Nucleus", "Ribosome", "Chloroplast"]),
    ("Science", "How many bones are in an adult human body?", "206", ["106", "256", "306"]),
    ("Science", "Which gas do plants absorb from the air?", "Carbon dioxide", ["Oxygen", "Nitrogen", "Helium"]),
    ("Science", "What is the hardest natural substance?", "Diamond", ["Gold", "Iron", "Quartz"]),
    ("Science", "Which is the largest planet in our solar system?", "Jupiter", ["Saturn", "Neptune", "Earth"]),
    ("Science", "What is the chemical symbol for gold?", "Au", ["Ag", "Gd", "Go"]),
    ("Science", "How many planets are in our solar system?", "8", ["7", "9", "10"]),
    ("Science", "Which organ pumps blood around the body?", "Heart", ["Liver", "Lungs", "Kidney"]),
    ("Geography", "What is the capital of Japan?", "Tokyo", ["Kyoto", "Osaka", "Seoul"]),
    ("Geography", "Which is the largest ocean on Earth?", "Pacific Ocean", ["Atlantic Ocean", "Indian Ocean", "Arctic Ocean"]),
    ("Geography", "What is the capital of Australia?", "Canberra", ["Sydney", "Melbourne", "Perth"]),
    ("Geography", "What is the capital of India?", "New Delhi", ["Mumbai", "Kolkata", "Chennai"]),
    ("Geography", "Which is the tallest mountain above sea level?", "Mount Everest", ["K2", "Kilimanjaro", "Mont Blanc"]),
    ("Geography", "Which continent is Egypt mostly in?", "Africa", ["Asia", "Europe", "South America"]),
    ("Geography", "What is the capital of France?", "Paris", ["Lyon", "Marseille", "Brussels"]),
    ("Geography", "Which is the largest hot desert in the world?", "Sahara", ["Gobi", "Kalahari", "Arabian"]),
    ("General", "How many days are in a leap year?", "366", ["365", "364", "367"]),
    ("General", "What color do you get by mixing blue and yellow?", "Green", ["Purple", "Orange", "Brown"]),
    ("General", "How many sides does a hexagon have?", "6", ["5", "7", "8"]),
    ("General", "What is the largest mammal on Earth?", "Blue whale", ["African elephant", "Giraffe", "Hippopotamus"]),
    ("General", "Which animal is the fastest on land?", "Cheetah", ["Lion", "Horse", "Greyhound"]),
    ("General", "How many continents are there?", "7", ["5", "6", "8"]),
    ("General", "Which is the only mammal that can truly fly?", "Bat", ["Flying squirrel", "Sugar glider", "Colugo"]),
    ("General", "What is the square root of 81?", "9", ["8", "7", "11"]),
    ("General", "What is the currency of Japan?", "Yen", ["Won", "Yuan", "Rupee"]),
    ("General", "Which instrument has 88 keys?", "Piano", ["Guitar", "Violin", "Flute"]),
    ("Tech", "What does CPU stand for?", "Central Processing Unit", ["Computer Power Unit", "Central Program Utility", "Core Processing Utility"]),
    ("Tech", "Which company makes the PlayStation?", "Sony", ["Microsoft", "Nintendo", "Sega"]),
    ("Tech", "In chess, which piece moves in an L-shape?", "Knight", ["Bishop", "Rook", "Pawn"]),
    ("Tech", "What does HTML stand for?", "HyperText Markup Language", ["High Tech Machine Language", "Hyperlink Text Mode Language", "Home Tool Markup Language"]),
    ("Tech", "Which programming language is Spidey's code written in?", "Python", ["Java", "Ruby", "Swift"]),
    ("Tech", "Who originally created Minecraft?", "Markus Persson", ["Gabe Newell", "Shigeru Miyamoto", "Hideo Kojima"]),
    ("Tech", "What does GPU stand for?", "Graphics Processing Unit", ["General Power Unit", "Game Playing Utility", "Graphical Program Unit"]),
]
CATEGORIES = sorted({q[0] for q in QUESTIONS})

WORDS = ["spider", "cobweb", "arachnid", "tarantula", "scorpion", "cocoon", "skyscraper", "superhero", "mystery",
         "galaxy", "planet", "guitar", "castle", "dragon", "rocket", "pirate", "jungle", "volcano", "whisper",
         "thunder", "diamond", "journey", "lantern", "harvest", "blanket", "compass", "bicycle", "library",
         "monster", "penguin", "rainbow", "shadow", "tornado", "unicorn", "wizard", "zeppelin", "oxygen", "cactus",
         "domino", "eclipse", "fossil", "glacier", "horizon", "island", "jigsaw", "kitten", "lemonade", "magnet",
         "nebula", "orchid", "puzzle", "quartz", "ribbon", "saddle", "tunnel", "umbrella", "velvet", "walrus",
         "zigzag", "avocado", "balloon", "camera", "dolphin", "elephant", "firefly", "giraffe", "hammock", "jellyfish",
         "kangaroo", "lizard", "mammoth", "narwhal", "octopus", "parrot", "raccoon", "vulture", "discord", "python"]

HANGMAN_STAGES = [
    "```\n  +---+\n  |   |\n      |\n      |\n      |\n      |\n=========\n```",
    "```\n  +---+\n  |   |\n  O   |\n      |\n      |\n      |\n=========\n```",
    "```\n  +---+\n  |   |\n  O   |\n  |   |\n      |\n      |\n=========\n```",
    "```\n  +---+\n  |   |\n  O   |\n /|   |\n      |\n      |\n=========\n```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n      |\n      |\n=========\n```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n /    |\n      |\n=========\n```",
    "```\n  +---+\n  |   |\n  O   |\n /|\\  |\n / \\  |\n      |\n=========\n```",
]
MAX_MISTAKES = 6

GUESS_MIN, GUESS_MAX = 1, 100


async def _err(interaction: discord.Interaction, text: str):
    """Ephemeral error that works whether or not the interaction was answered yet."""
    embed = web_embed("Web Snagged!", text, DARK)
    if interaction.response.is_done():
        await interaction.followup.send(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message(embed=embed, ephemeral=True)


def today_utc() -> str:
    return discord.utils.utcnow().date().isoformat()


# ------------------------------------------------------------------ challenge (accept / decline)
class ChallengeView(discord.ui.View):
    def __init__(self, challenger, opponent, title: str, builder):
        super().__init__(timeout=60)
        self.challenger, self.opponent, self.title, self.builder = challenger, opponent, title, builder
        self.message: Optional[discord.Message] = None

    def embed(self) -> discord.Embed:
        return web_embed(self.title, f"{self.opponent.mention}, {self.challenger.mention} challenges you!\n"
                                     "Press **Accept** to play. 🕷️", RED)

    @discord.ui.button(label="Accept", emoji="✅", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.opponent.id:
            return await _err(interaction, f"Only {self.opponent.mention} can accept this challenge.")
        self.stop()
        game = self.builder()
        game.message = interaction.message
        await interaction.response.edit_message(embed=game.embed(), view=game)

    @discord.ui.button(label="Decline", emoji="✖️", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id not in (self.challenger.id, self.opponent.id):
            return await _err(interaction, "This challenge isn't for you. 🕷️")
        self.stop()
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(
            embed=web_embed(self.title, f"{interaction.user.mention} declined the challenge. 🕸️", DARK), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(embed=web_embed(self.title, "Nobody answered the challenge. 🕸️", DARK), view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ trivia
class TriviaView(discord.ui.View):
    def __init__(self, cog: "MiniGames", question: tuple, guild_id: int, only_user=None, daily_info=None):
        super().__init__(timeout=None)           # timing is handled by the command (wait_for)
        self.cog, self.guild_id, self.only_user, self.daily_info = cog, guild_id, only_user, daily_info
        self.category, self.text, correct, wrong = question
        self.options = [correct] + list(wrong)
        random.shuffle(self.options)
        self.correct_idx = self.options.index(correct)
        self.answered: dict[int, int] = {}
        self.finished = False
        self.message: Optional[discord.Message] = None
        for i, opt in enumerate(self.options):
            btn = discord.ui.Button(label=opt[:80], style=discord.ButtonStyle.secondary, row=i // 2)
            btn.callback = self._make_callback(i)
            self.add_item(btn)

    def _make_callback(self, i: int):
        async def callback(interaction: discord.Interaction):
            await self.answer(interaction, i)
        return callback

    def embed(self, status: str = "", color: int = RED) -> discord.Embed:
        who = f"{self.only_user.mention}, this one's for you!" if self.only_user else "Anyone can answer. First correct answer wins!"
        embed = web_embed(f"Trivia • {self.category}", f"**{self.text}**\n\n{who}", color)
        if status:
            embed.add_field(name="Result", value=status, inline=False)
        return embed

    async def answer(self, interaction: discord.Interaction, i: int):
        if self.finished:
            return await _err(interaction, "This question is already over.")
        if self.only_user and interaction.user.id != self.only_user.id:
            return await _err(interaction, "This question isn't yours. Start your own with `/game trivia`. 🕷️")
        if interaction.user.id in self.answered:
            return await _err(interaction, "You already answered this one! 🕸️")
        self.answered[interaction.user.id] = i
        if i == self.correct_idx:
            return await self.finish(interaction, winner=interaction.user)
        if self.only_user:
            return await self.finish(interaction, winner=None)
        await interaction.response.send_message(
            embed=web_embed("Wrong!", "Nope! You're out for this question. 🕷️", DARK), ephemeral=True)

    async def finish(self, interaction: Optional[discord.Interaction], winner, timed_out: bool = False):
        if self.finished:
            return
        self.finished = True
        for idx, item in enumerate(self.children):
            item.disabled = True
            if idx == self.correct_idx:
                item.style = discord.ButtonStyle.success
            elif idx in self.answered.values():
                item.style = discord.ButtonStyle.danger
        self.stop()

        answer = self.options[self.correct_idx]
        if winner is not None:
            if self.daily_info is not None:
                reward_text = self.cog.finish_daily(self.guild_id, winner.id, True)
            else:
                got = self.cog.reward(self.guild_id, winner.id, TRIVIA_REWARD)
                reward_text = f"+{got} webs 🕸️" if got else "(daily web-reward limit reached)"
            status, color = f"✅ {winner.mention} got it! The answer was **{answer}**.\n{reward_text}", BLUE
        else:
            extra = ""
            if self.daily_info is not None:
                extra = "\n" + self.cog.finish_daily(self.guild_id, self.only_user.id, False)
            why = "⏰ Time's up!" if timed_out else "❌ Wrong answer!"
            status, color = f"{why} The answer was **{answer}**.{extra}", DARK
        embed = self.embed(status, color)
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ hangman
class HangmanModal(discord.ui.Modal, title="Guess a letter or the whole word"):
    guess = discord.ui.TextInput(label="Letter or word", min_length=1, max_length=20)

    def __init__(self, game: "HangmanView"):
        super().__init__()
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        await self.game.handle(interaction, str(self.guess).strip().lower())


class HangmanView(discord.ui.View):
    def __init__(self, cog: "MiniGames", starter, channel_id: int, guild_id: int):
        super().__init__(timeout=300)
        self.cog, self.starter, self.channel_id, self.guild_id = cog, starter, channel_id, guild_id
        self.word = random.choice(WORDS)
        self.letters: set[str] = set()
        self.wrong_letters: list[str] = []
        self.wrong_words: list[str] = []
        self.finished = False
        self.message: Optional[discord.Message] = None

    @property
    def mistakes(self) -> int:
        return len(self.wrong_letters) + len(self.wrong_words)

    def masked(self) -> str:
        return " ".join(c if c in self.letters else "_" for c in self.word)

    def embed(self, status: str = "", color: int = RED, reveal: bool = False) -> discord.Embed:
        desc = (f"{HANGMAN_STAGES[min(self.mistakes, MAX_MISTAKES)]}\n"
                f"# `{' '.join(self.word) if reveal else self.masked()}`\n"
                f"❌ **Wrong letters:** {' '.join(self.wrong_letters) or 'none'}\n"
                f"💬 **Wrong words:** {', '.join(self.wrong_words) or 'none'}\n"
                f"❤️ **Mistakes left:** {MAX_MISTAKES - self.mistakes}")
        embed = web_embed("Hangman", desc, color)
        embed.set_footer(text=f"Anyone can guess! Started by {self.starter.display_name} • 🕸️ Spidey")
        if status:
            embed.add_field(name="Result", value=status, inline=False)
        return embed

    async def end(self, interaction: Optional[discord.Interaction], winner):
        if self.finished:
            return
        self.finished = True
        self.cog.hangman_channels.discard(self.channel_id)
        for item in self.children:
            item.disabled = True
        self.stop()
        if winner is not None:
            got = self.cog.reward(self.guild_id, winner.id, HANGMAN_REWARD)
            status = (f"🎉 {winner.mention} solved it! The word was **{self.word}**.\n"
                      + (f"+{got} webs 🕸️" if got else "(daily web-reward limit reached)"))
            embed = self.embed(status, BLUE, reveal=True)
        else:
            embed = self.embed(f"💀 Out of mistakes! The word was **{self.word}**.", DARK, reveal=True)
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def handle(self, interaction: discord.Interaction, guess: str):
        if self.finished:
            return await _err(interaction, "This game is already over.")
        if not guess.isalpha():
            return await _err(interaction, "Letters only, please. 🕷️")
        if len(guess) == 1:
            if guess in self.letters or guess in self.wrong_letters:
                return await _err(interaction, f"`{guess}` was already guessed.")
            if guess in self.word:
                self.letters.add(guess)
                if all(c in self.letters for c in self.word):
                    return await self.end(interaction, interaction.user)
            else:
                self.wrong_letters.append(guess)
        else:
            if guess == self.word:
                return await self.end(interaction, interaction.user)
            if guess in self.wrong_words:
                return await _err(interaction, f"`{guess}` was already guessed.")
            self.wrong_words.append(guess)
        if self.mistakes >= MAX_MISTAKES:
            return await self.end(interaction, None)
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self):
        await self.end(None, None)

    @discord.ui.button(label="Guess", emoji="🔤", style=discord.ButtonStyle.primary)
    async def guess_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(HangmanModal(self))

    @discord.ui.button(label="Give up", emoji="🏳️", style=discord.ButtonStyle.secondary)
    async def giveup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.starter.id:
            return await _err(interaction, "Only the person who started the game can give up. 🕷️")
        await self.end(interaction, None)


# ------------------------------------------------------------------ tic-tac-toe
TTT_LINES = [(0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6)]


def ttt_winner(board: list) -> Optional[str]:
    for a, b, c in TTT_LINES:
        if board[a] and board[a] == board[b] == board[c]:
            return board[a]
    return None


def ttt_bot_move(board: list, bot_mark: str = "O", human_mark: str = "X") -> int:
    empty = [i for i, v in enumerate(board) if not v]
    for mark in (bot_mark, human_mark):          # win if possible, otherwise block
        for i in empty:
            board[i] = mark
            won = ttt_winner(board) == mark
            board[i] = None
            if won:
                return i
    if 4 in empty:
        return 4
    corners = [i for i in (0, 2, 6, 8) if i in empty]
    return random.choice(corners) if corners else random.choice(empty)


class TTTButton(discord.ui.Button):
    def __init__(self, index: int):
        super().__init__(label="\u200b", style=discord.ButtonStyle.secondary, row=index // 3)
        self.index = index

    async def callback(self, interaction: discord.Interaction):
        await self.view.play(interaction, self.index)


class TicTacToeView(discord.ui.View):
    MARKS = ["X", "O"]

    def __init__(self, p1, p2, vs_bot: bool = False):
        super().__init__(timeout=120)
        self.players, self.vs_bot = [p1, p2], vs_bot
        self.board: list = [None] * 9
        self.turn = 0
        self.finished = False
        self.message: Optional[discord.Message] = None
        for i in range(9):
            self.add_item(TTTButton(i))

    def embed(self, status: str = "", color: int = RED) -> discord.Embed:
        p1, p2 = self.players
        head = f"❌ {p1.mention}  vs  ⭕ {p2.mention}"
        if not status:
            status = f"It's {self.players[self.turn].mention}'s turn ({self.MARKS[self.turn]})."
        return web_embed("Tic-Tac-Toe", f"{head}\n\n{status}", color)

    def _place(self, i: int):
        mark = self.MARKS[self.turn]
        self.board[i] = mark
        btn = self.children[i]
        btn.label, btn.disabled = mark, True
        btn.style = discord.ButtonStyle.primary if mark == "X" else discord.ButtonStyle.danger

    def _end(self):
        self.finished = True
        for item in self.children:
            item.disabled = True
        self.stop()

    async def _after_move(self, interaction: discord.Interaction) -> bool:
        """Returns True if the game just ended (and the message was updated)."""
        winner = ttt_winner(self.board)
        if winner:
            self._end()
            who = self.players[self.MARKS.index(winner)]
            await interaction.response.edit_message(embed=self.embed(f"🎉 {who.mention} wins!", BLUE), view=self)
            return True
        if all(self.board):
            self._end()
            await interaction.response.edit_message(embed=self.embed("🤝 It's a draw!", DARK), view=self)
            return True
        return False

    async def play(self, interaction: discord.Interaction, i: int):
        if self.finished or self.board[i]:
            return await interaction.response.defer()
        if interaction.user.id != self.players[self.turn].id:
            return await _err(interaction, "It's not your turn! 🕷️")
        self._place(i)
        if await self._after_move(interaction):
            return
        self.turn = 1 - self.turn
        if self.vs_bot:
            self._place(ttt_bot_move(self.board))
            if await self._after_move(interaction):
                return
            self.turn = 0
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.players[0].id, self.players[1].id):
            await _err(interaction, "You're not in this game. Start your own with `/game tictactoe`. 🕷️")
            return False
        return True

    async def on_timeout(self):
        if self.finished:
            return
        self._end()
        if self.message is not None:
            try:
                await self.message.edit(embed=self.embed("⏰ The game timed out.", DARK), view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ connect four
C4_ROWS, C4_COLS = 6, 7
C4_EMOJI = {0: "⚫", 1: "🔴", 2: "🟡"}


def c4_wins(grid: list, r: int, c: int, p: int) -> bool:
    for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
        count = 1
        for sign in (1, -1):
            rr, cc = r + dr * sign, c + dc * sign
            while 0 <= rr < C4_ROWS and 0 <= cc < C4_COLS and grid[rr][cc] == p:
                count += 1
                rr += dr * sign
                cc += dc * sign
        if count >= 4:
            return True
    return False


class C4Button(discord.ui.Button):
    def __init__(self, col: int):
        super().__init__(label=str(col + 1), style=discord.ButtonStyle.secondary, row=0 if col < 5 else 1)
        self.col = col

    async def callback(self, interaction: discord.Interaction):
        await self.view.drop(interaction, self.col)


class Connect4View(discord.ui.View):
    def __init__(self, p1, p2):
        super().__init__(timeout=180)
        self.players = [p1, p2]
        self.grid = [[0] * C4_COLS for _ in range(C4_ROWS)]
        self.turn = 0
        self.finished = False
        self.message: Optional[discord.Message] = None
        for c in range(C4_COLS):
            self.add_item(C4Button(c))

    def render(self) -> str:
        header = "1️⃣2️⃣3️⃣4️⃣5️⃣6️⃣7️⃣"
        rows = ["".join(C4_EMOJI[v] for v in row) for row in self.grid]
        return header + "\n" + "\n".join(rows)

    def embed(self, status: str = "", color: int = RED) -> discord.Embed:
        p1, p2 = self.players
        if not status:
            status = f"It's {self.players[self.turn].mention}'s turn {C4_EMOJI[self.turn + 1]}"
        return web_embed("Connect Four", f"🔴 {p1.mention}  vs  🟡 {p2.mention}\n\n{self.render()}\n\n{status}", color)

    def _end(self):
        self.finished = True
        for item in self.children:
            item.disabled = True
        self.stop()

    async def drop(self, interaction: discord.Interaction, col: int):
        if self.finished:
            return await interaction.response.defer()
        if interaction.user.id != self.players[self.turn].id:
            return await _err(interaction, "It's not your turn! 🕷️")
        row = next((r for r in range(C4_ROWS - 1, -1, -1) if self.grid[r][col] == 0), None)
        if row is None:
            return await _err(interaction, "That column is full. Pick another one.")
        player = self.turn + 1
        self.grid[row][col] = player
        if c4_wins(self.grid, row, col, player):
            self._end()
            return await interaction.response.edit_message(
                embed=self.embed(f"🎉 {self.players[self.turn].mention} connected four!", BLUE), view=self)
        if all(self.grid[0][c] != 0 for c in range(C4_COLS)):
            self._end()
            return await interaction.response.edit_message(embed=self.embed("🤝 The board is full. It's a draw!", DARK), view=self)
        for b in self.children:
            b.disabled = self.grid[0][b.col] != 0
        self.turn = 1 - self.turn
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.players[0].id, self.players[1].id):
            await _err(interaction, "You're not in this game. Start your own with `/game connect4`. 🕷️")
            return False
        return True

    async def on_timeout(self):
        if self.finished:
            return
        self._end()
        if self.message is not None:
            try:
                await self.message.edit(embed=self.embed("⏰ The game timed out.", DARK), view=self)
            except discord.HTTPException:
                pass


# ------------------------------------------------------------------ rock paper scissors
RPS_BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}
RPS_EMOJI = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}


def rps_result(a: str, b: str) -> int:
    """0 = tie, 1 = a wins, 2 = b wins"""
    if a == b:
        return 0
    return 1 if RPS_BEATS[a] == b else 2


class RPSView(discord.ui.View):
    def __init__(self, p1, p2, vs_bot: bool):
        super().__init__(timeout=60)
        self.players, self.vs_bot = [p1, p2], vs_bot
        self.choices: dict[int, str] = {}
        self.finished = False
        self.message: Optional[discord.Message] = None

    def embed(self, status: str = "", color: int = RED) -> discord.Embed:
        p1, p2 = self.players
        if not status:
            waiting = [p.mention for p in self.players if p.id not in self.choices and not (self.vs_bot and p is p2)]
            status = "Pick your move below! 👇" + (f"\nWaiting for: {', '.join(waiting)}" if waiting else "")
        return web_embed("Rock Paper Scissors", f"{p1.mention}  vs  {p2.mention}\n\n{status}", color)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id not in (self.players[0].id, self.players[1].id):
            await _err(interaction, "You're not in this game. Start your own with `/game rps`. 🕷️")
            return False
        return True

    async def choose(self, interaction: discord.Interaction, move: str):
        if self.finished:
            return await _err(interaction, "This game is already over.")
        if interaction.user.id in self.choices:
            return await _err(interaction, "You already picked! 🕸️")
        self.choices[interaction.user.id] = move
        p1, p2 = self.players
        if self.vs_bot:
            self.choices[p2.id] = random.choice(list(RPS_BEATS))
        if p1.id in self.choices and p2.id in self.choices:
            return await self.resolve(interaction)
        await interaction.response.send_message(
            embed=web_embed("Locked In!", f"You chose {RPS_EMOJI[move]} **{move}**. Waiting for your opponent…", BLUE),
            ephemeral=True)
        if self.message is not None:
            try:
                await self.message.edit(embed=self.embed(), view=self)
            except discord.HTTPException:
                pass

    async def resolve(self, interaction: Optional[discord.Interaction]):
        self.finished = True
        for item in self.children:
            item.disabled = True
        self.stop()
        p1, p2 = self.players
        m1, m2 = self.choices[p1.id], self.choices[p2.id]
        outcome = rps_result(m1, m2)
        verdict = "🤝 It's a tie!" if outcome == 0 else f"🎉 {self.players[outcome - 1].mention} wins!"
        text = f"{p1.mention}: {RPS_EMOJI[m1]} **{m1}**\n{p2.mention}: {RPS_EMOJI[m2]} **{m2}**\n\n{verdict}"
        embed = self.embed(text, DARK if outcome == 0 else BLUE)
        if interaction is not None and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            await self.message.edit(embed=embed, view=self)

    async def on_timeout(self):
        if self.finished:
            return
        self.finished = True
        for item in self.children:
            item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(embed=self.embed("⏰ Someone didn't pick in time. No result.", DARK), view=self)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Rock", emoji="🪨", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "rock")

    @discord.ui.button(label="Paper", emoji="📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "paper")

    @discord.ui.button(label="Scissors", emoji="✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.choose(interaction, "scissors")


# ------------------------------------------------------------------ catch the villain
class CatchView(discord.ui.View):
    def __init__(self, cog: "MiniGames", guild_id: int):
        super().__init__(timeout=None)
        self.cog, self.guild_id = cog, guild_id
        self.ready_at: Optional[float] = None
        self.winner = None

    @discord.ui.button(label="Get ready…", emoji="👀", style=discord.ButtonStyle.secondary, disabled=True)
    async def catch(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.winner is not None or self.ready_at is None:
            return await interaction.response.defer()
        self.winner = interaction.user
        elapsed = time.monotonic() - self.ready_at
        got = self.cog.reward(self.guild_id, interaction.user.id, CATCH_REWARD)
        button.disabled = True
        button.label, button.style = "Caught!", discord.ButtonStyle.success
        self.stop()
        text = (f"🎉 {interaction.user.mention} caught the villain in **{elapsed:.2f}s**!\n"
                + (f"+{got} webs 🕸️" if got else "(daily web-reward limit reached)"))
        await interaction.response.edit_message(embed=web_embed("Villain Caught!", text, BLUE), view=self)


# ------------------------------------------------------------------ number guessing (/guess)
class GuessModal(discord.ui.Modal, title="Make your guess 🕷️"):
    number = discord.ui.TextInput(
        label=f"Pick a number from {GUESS_MIN} to {GUESS_MAX}",
        placeholder="e.g. 50", min_length=1, max_length=3,
    )

    def __init__(self, game: "GuessGame"):
        super().__init__()
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        await self.game.handle_guess(interaction, str(self.number).strip())


class GuessGame(discord.ui.View):
    def __init__(self, cog: "MiniGames", player: discord.abc.User):
        super().__init__(timeout=300)  # 5 minutes of inactivity ends the game
        self.cog = cog
        self.player = player
        self.secret = random.randint(GUESS_MIN, GUESS_MAX)
        self.guesses: list[tuple[int, str]] = []
        self.low, self.high = GUESS_MIN, GUESS_MAX
        self.message: discord.Message | None = None
        self.finished = False

    # ---------- embeds
    def build_embed(self, note: str = "") -> discord.Embed:
        desc = (f"{self.player.mention}, I'm thinking of a number between "
                f"**{GUESS_MIN}** and **{GUESS_MAX}**. 🕷️\n"
                "Press **Guess** and try to catch it in my web! 🕸️")
        if note:
            desc += f"\n\n{note}"
        embed = web_embed("Number Guessing Web", desc, RED)
        if self.guesses:
            lines = [f"**{n}** — {hint}" for n, hint in self.guesses[-15:]]
            embed.add_field(name=f"{self.player.display_name}'s guesses ({len(self.guesses)})",
                            value="\n".join(lines), inline=False)
            embed.add_field(name="Range left", value=f"`{self.low}` to `{self.high}`")
        embed.set_author(name=self.player.display_name, icon_url=self.player.display_avatar.url)
        return embed

    def end_embed(self, won: bool, gave_up: bool = False, timed_out: bool = False) -> discord.Embed:
        tries = len(self.guesses)
        if won:
            title, color = "You Caught It! 🎉", BLUE
            desc = (f"{self.player.mention} guessed my number **{self.secret}** "
                    f"in **{tries}** attempt{'s' if tries != 1 else ''}! 🕷️")
        elif gave_up:
            title, color = "Web Abandoned!", DARK
            desc = f"{self.player.mention} gave up. My number was **{self.secret}**. 🕸️"
        else:
            title, color = "Time's Up!", DARK
            desc = f"The web went quiet. My number was **{self.secret}**. 🕸️"
        embed = web_embed(title, desc, color)
        if self.guesses:
            lines = [f"**{n}** — {hint}" for n, hint in self.guesses[-15:]]
            embed.add_field(name=f"Guesses ({len(self.guesses)})", value="\n".join(lines), inline=False)
        embed.set_author(name=self.player.display_name, icon_url=self.player.display_avatar.url)
        return embed

    # ---------- game logic
    async def handle_guess(self, interaction: discord.Interaction, raw: str):
        if self.finished:
            return await _err(interaction, "This game is already over.")
        if not raw.isdigit() or not GUESS_MIN <= int(raw) <= GUESS_MAX:
            return await _err(interaction, f"Enter a whole number from {GUESS_MIN} to {GUESS_MAX}.")
        n = int(raw)
        if any(n == g[0] for g in self.guesses):
            return await _err(interaction, f"You already guessed **{n}**. Try a different number! 🕷️")

        if n == self.secret:
            self.guesses.append((n, "✅ Correct!"))
            return await self.finish(interaction, won=True)

        if n > self.secret:
            self.guesses.append((n, "⬆️ greater than my number"))
            self.high = min(self.high, n - 1)
            note = f"{self.player.mention} guessed **{n}**, which is **greater** than my number! ⬆️\nTry a smaller one."
        else:
            self.guesses.append((n, "⬇️ less than my number"))
            self.low = max(self.low, n + 1)
            note = f"{self.player.mention} guessed **{n}**, which is **less** than my number! ⬇️\nTry a bigger one."

        await interaction.response.edit_message(embed=self.build_embed(note), view=self)

    async def finish(self, interaction: discord.Interaction | None, won: bool, gave_up: bool = False,
                     timed_out: bool = False):
        self.finished = True
        self.cog.active.discard(self.player.id)
        for item in self.children:
            item.disabled = True
        embed = self.end_embed(won, gave_up, timed_out)
        self.stop()
        if interaction is not None:
            await interaction.response.edit_message(embed=embed, view=self)
        elif self.message is not None:
            try:
                await self.message.edit(embed=embed, view=self)
            except discord.HTTPException:
                pass

    async def on_timeout(self):
        if not self.finished:
            await self.finish(None, won=False, timed_out=True)

    # ---------- buttons
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await _err(
                interaction,
                f"This game belongs to {self.player.mention}. "
                "Start your own with `/guess`! 🕷️"
            )
            return False
        return True

    @discord.ui.button(
        label="Guess",
        emoji="🕸️",
        style=discord.ButtonStyle.secondary
    )
    async def guess_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_modal(GuessModal(self))

    @discord.ui.button(
        label="Give up",
        emoji="🏳️",
        style=discord.ButtonStyle.secondary
    )
    async def giveup_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await self.finish(interaction, won=False, gave_up=True)


# ------------------------------------------------------------------ the cog
class MiniGames(commands.Cog):
    game = app_commands.Group(name="game", description="Trivia, hangman, tic-tac-toe, connect four and more",
                              guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.hangman_channels: set[int] = set()
        self.active: set[int] = set()          # users with a running /guess game
        self.data = {"rewards": {}, "daily": {}}
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    for k in ("rewards", "daily"):
                        if isinstance(loaded.get(k), dict):
                            self.data[k] = loaded[k]
            except (OSError, json.JSONDecodeError):
                pass

    def save(self):
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f)
        os.replace(tmp, DATA_FILE)

    # ---------- web rewards (with a daily cap so it can't be farmed)
    def reward(self, guild_id: int, user_id: int, amount: int) -> int:
        eco = self.bot.get_cog("Economy")
        if eco is None:
            return 0
        r = self.data["rewards"].setdefault(str(user_id), {"day": today_utc(), "n": 0})
        if r["day"] != today_utc():
            r["day"], r["n"] = today_utc(), 0
        if r["n"] >= REWARD_CAP_PER_DAY:
            return 0
        r["n"] += 1
        eco.add(guild_id, user_id, amount)
        self.save()
        return amount

    def finish_daily(self, guild_id: int, user_id: int, correct: bool) -> str:
        d = self.data["daily"].setdefault(str(user_id), {"day": "", "streak": 0, "prev": ""})
        if not correct:
            d["streak"] = 0
            self.save()
            return "💔 Your streak was reset. Try again tomorrow!"
        continuing = d.get("prev") == (discord.utils.utcnow().date() - timedelta(days=1)).isoformat()
        d["streak"] = d["streak"] + 1 if continuing else 1
        amount = DAILY_BASE + DAILY_STEP * min(d["streak"] - 1, DAILY_MAX_STEPS)
        eco = self.bot.get_cog("Economy")
        if eco is not None:
            eco.add(guild_id, user_id, amount)
        self.save()
        return f"🔥 **Streak: {d['streak']} day(s)**\n+{amount} webs 🕸️"

    # ================================================================ TRIVIA
    @game.command(name="trivia", description="A trivia question. First correct answer wins webs!")
    @app_commands.describe(category="Pick a category (or leave empty for random)")
    @app_commands.choices(category=[app_commands.Choice(name=c, value=c) for c in CATEGORIES])
    @app_commands.checks.cooldown(1, 5.0)
    async def trivia(self, interaction: discord.Interaction, category: Optional[app_commands.Choice[str]] = None):
        pool = [q for q in QUESTIONS if category is None or q[0] == category.value]
        view = TriviaView(self, random.choice(pool), interaction.guild.id)
        await interaction.response.send_message(embed=view.embed(), view=view)
        view.message = await interaction.original_response()
        try:
            await asyncio.wait_for(view.wait(), timeout=20)
        except asyncio.TimeoutError:
            await view.finish(None, None, timed_out=True)

    @game.command(name="daily", description="Today's quiz question. Keep your streak for bonus webs!")
    async def daily(self, interaction: discord.Interaction):
        uid = str(interaction.user.id)
        d = self.data["daily"].setdefault(uid, {"day": "", "streak": 0, "prev": ""})
        today = today_utc()
        if d["day"] == today:
            return await _err(interaction, f"You already took today's quiz! Come back tomorrow. 🔥 Streak: **{d['streak']}**")
        d["prev"], d["day"] = d["day"], today
        self.save()
        question = random.Random(today).choice(QUESTIONS)        # same question for everyone today
        view = TriviaView(self, question, interaction.guild.id, only_user=interaction.user, daily_info=True)
        await interaction.response.send_message(embed=view.embed(), view=view)
        view.message = await interaction.original_response()
        try:
            await asyncio.wait_for(view.wait(), timeout=30)
        except asyncio.TimeoutError:
            await view.finish(None, None, timed_out=True)

    # ================================================================ HANGMAN
    @game.command(name="hangman", description="Guess the hidden word before the drawing is finished")
    async def hangman(self, interaction: discord.Interaction):
        cid = interaction.channel.id
        if cid in self.hangman_channels:
            return await _err(interaction, "There's already a hangman game running in this channel! 🕷️")
        view = HangmanView(self, interaction.user, cid, interaction.guild.id)
        self.hangman_channels.add(cid)
        try:
            await interaction.response.send_message(embed=view.embed(), view=view)
            view.message = await interaction.original_response()
        except discord.HTTPException:
            self.hangman_channels.discard(cid)

    # ================================================================ TIC-TAC-TOE / CONNECT 4 / RPS
    async def _challenge(self, interaction, opponent: discord.Member, title: str, builder):
        if opponent.bot or opponent.id == interaction.user.id:
            return await _err(interaction, "Pick a different human to play against. 🕷️")
        view = ChallengeView(interaction.user, opponent, title, builder)
        await interaction.response.send_message(content=opponent.mention, embed=view.embed(), view=view)
        view.message = await interaction.original_response()

    @game.command(name="tictactoe", description="Play tic-tac-toe against a friend (or Spidey)")
    @app_commands.describe(opponent="Who do you want to play? (leave empty to play Spidey)")
    async def tictactoe(self, interaction: discord.Interaction, opponent: Optional[discord.Member] = None):
        if opponent is None or opponent.id == self.bot.user.id:
            game = TicTacToeView(interaction.user, self.bot.user, vs_bot=True)
            await interaction.response.send_message(embed=game.embed(), view=game)
            game.message = await interaction.original_response()
            return
        await self._challenge(interaction, opponent, "Tic-Tac-Toe",
                              lambda: TicTacToeView(interaction.user, opponent))

    @game.command(name="connect4", description="Challenge a friend to Connect Four")
    @app_commands.describe(opponent="Who do you want to play?")
    async def connect4(self, interaction: discord.Interaction, opponent: discord.Member):
        await self._challenge(interaction, opponent, "Connect Four",
                              lambda: Connect4View(interaction.user, opponent))

    @game.command(name="rps", description="Rock paper scissors against a friend (or Spidey)")
    @app_commands.describe(opponent="Who do you want to play? (leave empty to play Spidey)")
    async def rps(self, interaction: discord.Interaction, opponent: Optional[discord.Member] = None):
        if opponent is None or opponent.id == self.bot.user.id:
            game = RPSView(interaction.user, self.bot.user, vs_bot=True)
            await interaction.response.send_message(embed=game.embed(), view=game)
            game.message = await interaction.original_response()
            return
        await self._challenge(interaction, opponent, "Rock Paper Scissors",
                              lambda: RPSView(interaction.user, opponent, vs_bot=False))

    # ================================================================ GUESS
    @app_commands.command(name="guess", description="Play a number guessing game (1-100)")
    @app_commands.guild_only()
    async def guess(self, interaction: discord.Interaction):
        if interaction.user.id in self.active:
            return await _err(interaction, "You already have a game running! Finish it or press **Give up**. 🕷️")
        game = GuessGame(self, interaction.user)
        self.active.add(interaction.user.id)
        try:
            await interaction.response.send_message(embed=game.build_embed(), view=game)
            game.message = await interaction.original_response()
        except discord.HTTPException:
            self.active.discard(interaction.user.id)

    # ================================================================ CATCH
    @game.command(name="catch", description="A villain appears! First to press the button catches it")
    @app_commands.checks.cooldown(1, 10.0)
    async def catch(self, interaction: discord.Interaction):
        view = CatchView(self, interaction.guild.id)
        await interaction.response.send_message(embed=web_embed(
            "Villain Alert!", "A villain is sneaking through the web… get ready! 👀", RED), view=view)
        message = await interaction.original_response()
        await asyncio.sleep(random.uniform(2, 5))
        view.catch.disabled, view.catch.label, view.catch.emoji = False, "Catch!", "🕷️"
        view.catch.style = discord.ButtonStyle.danger
        view.ready_at = time.monotonic()
        await message.edit(embed=web_embed("There It Is!", "**NOW!** Press the button! ⚡", RED), view=view)
        try:
            await asyncio.wait_for(view.wait(), timeout=15)
        except asyncio.TimeoutError:
            view.catch.disabled, view.catch.label = True, "Escaped…"
            view.stop()
            await message.edit(embed=web_embed("It Got Away!", "Nobody was fast enough. 🕸️", DARK), view=view)


async def setup(bot: commands.Bot):
    await bot.add_cog(MiniGames(bot))
