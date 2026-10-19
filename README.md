# 🕷️ Spidey Bot

## Run it
1. Install Python 3.10+ from python.org
2. Open this folder in VS Code (File > Open Folder)
3. Open the terminal in VS Code (Ctrl + `) and run:
   pip install -r requirements.txt
4. Set your bot token as the DISCORD_TOKEN environment variable
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
- Actions:      /hug /slap /punch /dance, plus /action <type> [@user] for 42 actions with GIFs
                (kiss, pat, cuddle, poke, bite, kick, yeet, cry, laugh, wave, ...). The target gets a "do it back" button.
- AutoMod:      /automod status  (everything starts OFF)
  /automod toggle <module|all> on/off
  modules: spam, duplicates, links, invites, badwords, caps, mentions, emojis, zalgo
  /automod set, punishment, badwords, allowlist, ignorechannel, ignorerole,
  staff, strikes, clearstrikes, reset

Data is saved in voicemaster.json, moderation.json, utility.json and automod.json (created automatically).
