"""Load with:  await bot.load_extension("dashboard")"""
from .settings import get, command_enabled, get_prefix, fmt   # use anywhere: dashboard.get(guild.id)

async def setup(bot):
    from .cog import DashboardCog
    await bot.add_cog(DashboardCog(bot))
