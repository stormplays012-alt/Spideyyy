import random

import discord
from discord import app_commands
from discord.ext import commands

from theme import cozy_embed, PEACH, LAVENDER, NIGHT

MIN_NUM, MAX_NUM = 1, 100


async def _err(interaction: discord.Interaction, text: str):
    await interaction.response.send_message(embed=cozy_embed("Oops, a Little Bump!", text, NIGHT), ephemeral=True)


class GuessModal(discord.ui.Modal, title="Make your guess 🌙"):
    number = discord.ui.TextInput(
        label=f"Pick a number from {MIN_NUM} to {MAX_NUM}",
        placeholder="e.g. 50", min_length=1, max_length=3,
    )

    def __init__(self, game: "GuessGame"):
        super().__init__()
        self.game = game

    async def on_submit(self, interaction: discord.Interaction):
        await self.game.handle_guess(interaction, str(self.number).strip())


class GuessGame(discord.ui.View):
    def __init__(self, cog: "Games", player: discord.abc.User):
        super().__init__(timeout=300)  # 5 minutes of inactivity ends the game
        self.cog = cog
        self.player = player
        self.secret = random.randint(MIN_NUM, MAX_NUM)
        self.guesses: list[tuple[int, str]] = []
        self.low, self.high = MIN_NUM, MAX_NUM
        self.message: discord.Message | None = None
        self.finished = False

    # ---------- embeds
    def build_embed(self, note: str = "") -> discord.Embed:
        desc = (f"{self.player.mention}, I'm thinking of a number between "
                f"**{MIN_NUM}** and **{MAX_NUM}**. 🌙\n"
                "Press **Guess** and try to catch it before it slips away! ✨")
        if note:
            desc += f"\n\n{note}"
        embed = cozy_embed("Number Guessing Game", desc, PEACH)
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
            title, color = "You Caught It! 🎉", LAVENDER
            desc = (f"{self.player.mention} guessed my number **{self.secret}** "
                    f"in **{tries}** attempt{'s' if tries != 1 else ''}! 🌙")
        elif gave_up:
            title, color = "Game Abandoned!", NIGHT
            desc = f"{self.player.mention} gave up. My number was **{self.secret}**. ☁️"
        else:
            title, color = "Time's Up!", NIGHT
            desc = f"Time ran out. My number was **{self.secret}**. ☁️"
        embed = cozy_embed(title, desc, color)
        if self.guesses:
            lines = [f"**{n}** — {hint}" for n, hint in self.guesses[-15:]]
            embed.add_field(name=f"Guesses ({len(self.guesses)})", value="\n".join(lines), inline=False)
        embed.set_author(name=self.player.display_name, icon_url=self.player.display_avatar.url)
        return embed

    # ---------- game logic
    async def handle_guess(self, interaction: discord.Interaction, raw: str):
        if self.finished:
            return await _err(interaction, "This game is already over.")
        if not raw.isdigit() or not MIN_NUM <= int(raw) <= MAX_NUM:
            return await _err(interaction, f"Enter a whole number from {MIN_NUM} to {MAX_NUM}.")
        n = int(raw)
        if any(n == g[0] for g in self.guesses):
            return await _err(interaction, f"You already guessed **{n}**. Try a different number! 🌙")

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
        # ---------- buttons
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.id:
            await _err(
                interaction,
                f"This game belongs to {self.player.mention}. "
                "Start your own with `/guess`! 🌙"
            )
            return False
        return True

    @discord.ui.button(
        label="Guess",
        emoji="✨",
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



class Games(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active: set[int] = set()

    @app_commands.command(name="guess", description="Play a number guessing game (1-100)")
    async def guess(self, interaction: discord.Interaction):
        if interaction.user.id in self.active:
            return await _err(interaction, "You already have a game running! Finish it or press **Give up**. 🌙")
        game = GuessGame(self, interaction.user)
        self.active.add(interaction.user.id)
        await interaction.response.send_message(embed=game.build_embed(), view=game)
        game.message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(Games(bot))
