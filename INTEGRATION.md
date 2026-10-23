# Dashboard for discord.py 2.x

## 1. Copy
Put the `dashboard/` folder in your bot repo root (next to your main file).

## 2. Environment (.env or host variables)
    CLIENT_SECRET=...     # Developer Portal → OAuth2 → Client Secret
    REDIRECT_URI=http://localhost:3000/auth/callback   # also add it under OAuth2 → Redirects
    DASHBOARD_PORT=3000   # optional

No new packages: it uses aiohttp, which discord.py already installs.

## 3. Edit your main file (2 changes)
    import dashboard

    intents = discord.Intents.default()
    intents.members = True            # welcome / auto-role (also enable in Developer Portal)
    intents.message_content = True    # automod / leveling

    bot = commands.Bot(command_prefix=dashboard.get_prefix, intents=intents)   # <- dynamic prefix

    async def setup_hook():           # or inside your existing setup_hook
        await bot.load_extension("dashboard")                                 # <- starts dashboard + features
    bot.setup_hook = setup_hook

If you use `class MyBot(commands.Bot)`, put the `load_extension` line in its `setup_hook` method.

## 4. Read settings anywhere in your own code
    import dashboard
    s = dashboard.get(ctx.guild.id)
    s["general"]["language"]; s["moderation"]["warnLimit"]

## 5. Command switches
Toggles match your command names (`ping`, `ban`, ...). Edit the `commands` list in `settings.py` DEFAULTS to match your real commands.
Disabled commands reply "turned off". Silence the log noise by ignoring `commands.CheckFailure` in your `on_command_error`.

Data is stored in `dashboard/data/` (settings.json, levels.json) so add it to .gitignore.
