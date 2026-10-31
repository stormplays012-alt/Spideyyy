# 🕷️ Spidey Bot

## Run it
1. Install Python 3.10+ from python.org
2. Open this folder in VS Code (File > Open Folder)
3. Open the terminal in VS Code (Ctrl + `) and run:
   pip install -r requirements.txt
4. Set your bot token as the DISCORD_TOKEN environment variable
   (for AI chat also set GEMINI_API_KEY - free key from https://aistudio.google.com/app/apikey)
5. Run:
   python bot.py

## Discord Developer Portal checklist
- Bot tab > turn ON "Server Members Intent" AND "Message Content Intent"
  (both are required - the bot will not start without them)
- Invite the bot with scopes: bot + applications.commands
- Easiest: give the bot the Administrator permission, or at least:
  Manage Channels, Manage Roles, Manage Nicknames, Kick Members, Ban Members,
  Moderate Members, Manage Messages, Read Message History, Mute Members,
  Deafen Members, Move Members, Connect, View Channel, Send Messages, Embed Links
- IMPORTANT: in Server Settings > Roles, drag the bot's role ABOVE the roles it
  should moderate, otherwise kick/ban/mute/role commands will fail.

## Features
- AI Chat:      just ping the bot: @Spidey can you help me?   (@Spidey reset clears the channel memory)
- Help:         /help  (one dropdown menu with every category, updates itself)
- VoiceMaster:  /voicemaster setup  (join-to-create rooms + control panel)
- Moderation:   (see /help)
  kick, ban, tempban, softban, unban, banlist, mute, unmute, warn, warnings,
  delwarn, clearwarnings, modlogs, case, setlogchannel, purge, slowmode, lock,
  unlock, lockdown, unlockdown, nick, addrole, removerole, userinfo,
  vcmute, vcunmute, vcdeafen, vcundeafen, vckick, vcmove
- Run /setlogchannel to get a log of every mod action (case system).
- Utility:      (see /help)
  serverinfo, whois, roleinfo, channelinfo, membercount, banner, servericon,
  emojis, roles, botinfo, uptime, invite, calc, choose, roll, color,
  timestamp, snowflake, password, base64, say, embed, poll, suggest,
  setsuggestchannel, remind, reminders, delreminder, afk, todo, giveaway

- Economy:      /balance /daily /work /give /shop /buy /inventory /richest, /webs ..., /ecoadmin ...
- Casino:       /coinflip /slots /blackjack /mines, /casino hilo|roulette|dice|rps
- Mini-games:   /guess (number guessing game, 1-100), /game trivia|daily|hangman|tictactoe|connect4|rps|catch
- Actions:      42 actions with GIFs: /hug /slap /punch /dance, plus every other one as its own command:
                /interact kiss|pat|cuddle|poke|tickle|bite|kick|shoot|yeet|bonk|... @user
                /react cry|laugh|wave|wink|blush|smile|smug|sleep|yawn|run|... [@user]
                The target gets a "do it back" button.
- Leveling:     /rank /leaderboard, /levelconfig ..., /levelreward ...
- Welcome:      /welcome status|channel|message|goodbyechannel|goodbyemessage|autorole|minage|verify|test
                and the look: /welcome text|title|footer|color|thumbnail|image|resetstyle
- Tickets:      /ticket setup|panel|supportrole|add|remove|rename|close
- AutoMod:      /automod status  (everything starts OFF)
  /automod toggle <module|all> on/off
  modules: spam, duplicates, links, invites, badwords, caps, mentions, emojis, zalgo
  /automod set, punishment, badwords, allowlist, ignorechannel, ignorerole,
  staff, strikes, clearstrikes, reset

## Customising the welcome message (staff with Manage Server)
Use `/welcome test` after every change to preview exactly what new members will see.
- `/welcome channel #welcome`   where it is sent (nothing is sent until you set this)
- `/welcome text ...`           text ABOVE the embed. `{user}` here pings the new member (empty = none)
- `/welcome title ...`          embed title      |  `/welcome message ...` the embed text
- `/welcome footer ...`         small text at the bottom (empty = Spidey default, `off` = none)
- `/welcome color #00ff99`      color strip      |  `/welcome thumbnail`  avatar / server icon / custom / none
- `/welcome image`              big picture at the bottom: give a https:// link or upload a file (PNG/JPG/GIF/WEBP, 8 MB)
Placeholders that work in text, title, message and footer: `{user}` `{name}` `{server}` `{count}` (type `\n` for a new line).
Uploaded pictures are saved in the `welcome_images` folder next to bot.py. `/welcome resetstyle` restores the default look.

Data is saved in .json files next to bot.py (created automatically) plus the welcome_images folder.

## 🖥️ Web dashboard
Log in with Discord and edit AutoMod, Welcome, Leveling, Moderation log, Tickets and Suggestions from a browser.
Changes go straight into the same settings your /commands use, so they apply instantly.
1. Developer Portal > OAuth2: copy the **Client Secret** and add a **Redirect** (example: `http://localhost:3000/auth/callback`).
2. Set environment variables next to `DISCORD_TOKEN`:
   `CLIENT_SECRET=...`  `REDIRECT_URI=http://localhost:3000/auth/callback`  (optional `DASHBOARD_PORT=3000`)
3. `python bot.py`, then open http://localhost:3000
