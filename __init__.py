from discord.ext import commands
from . import server

class Dashboard(commands.Cog):
    def __init__(self, bot): self.bot, self.runner = bot, None
    async def cog_load(self): self.runner = await server.start(self.bot)
    async def cog_unload(self):
        if self.runner: await self.runner.cleanup()

async def setup(bot): await bot.add_cog(Dashboard(bot))
