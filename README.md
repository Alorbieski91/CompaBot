# Compabot

A small personal-server bot with a quote book, shared favorites, bilingual greetings,
and controls for the CompaWorld Icarus server.
Requires Python 3.10 or newer.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Skip the copy if you already have `.env`. Set `TOKEN` to your bot token. Optionally set
`GUILD_ID` to your personal server ID for immediate server command registration.
With it blank, commands register globally and may take time to appear. Keep the
registration mode consistent to avoid duplicate global/server commands.

In the [Discord Developer Portal](https://discord.com/developers/applications):

1. Open your application > Bot. Enable **Server Members Intent** and **Message Content Intent**.
2. Under OAuth2, generate an invite with `bot` and `applications.commands` scopes.
3. Grant **View Channels**, **Send Messages**, **Embed Links**, and **Read Message History**.
   For use inside threads, also grant **Send Messages in Threads**. Administrator is unnecessary.
4. Invite the bot to your server, then run:

```powershell
.\.venv\Scripts\python.exe Compabot.py
```

Leave the process running for the bot to stay online. Never commit or share `.env`.
Intents guidance: https://discordpy.readthedocs.io/en/stable/intents.html

## Commands

| Command | Behavior |
| --- | --- |
| Message right-click > Apps > Save quote | Save a text message with author and original link; duplicate saves are ignored. |
| `/quote` | Random quote from the current channel only. |
| `/quote_remove quote_id:123` | Remove a quote by the ID on its footer. Author, saver, or someone with Manage Messages can remove it. |
| `/favorites add category:game item:Halo` | Add a shared server favorite. Categories: game, movie, restaurant. |
| `/favorites list category:game page:1` | Privately show saved items, 15 per page. |
| `/favorites remove category:game item:Halo` | Remove a favorite; requires Manage Messages. |
| `/pick category:game` | Choose a random saved favorite. |
| `/say text:Saludos` or `!say Saludos` | Repeat text; requires Manage Messages, with a 10-second per-user cooldown. |

Everyone can save quotes and add favorites. Favorite duplicates are case-insensitive.
Quotes stay in their source channel to avoid exposing private-channel messages elsewhere;
use `/quote` in the same thread for quotes saved in a thread. Text is saved as a snapshot:
edits or deletion of the original do not update the saved quote. Attachments are not saved.
Use `/quote_remove` to delete a snapshot.

The bot ignores all bots and DMs. Greetings respond to `hello`, `saludos`, and `bye`
with a 30-second channel cooldown. New members receive a welcome in the system channel.
Outgoing messages suppress user, role, and everyone pings. Quote saving, quote playback,
and picking have 5-second per-user cooldowns. Errors are logged to the console.

## Icarus server commands

`/server` runs the PowerShell scripts installed with the Icarus server, so Compabot must run
on the same Windows PC as the server, signed in as the same user (not as a Windows service:
the stop script needs the server's console window). Set `SERVER_ADMIN_IDS` in `.env` to the
Discord user IDs allowed to use it, separated by commas (Developer Mode > right-click a
user > Copy User ID). Everyone else is refused. If the scripts are not in
`C:\IcarusServer\scripts`, set `ICARUS_SCRIPTS_DIR`.

| Command | Behavior |
| --- | --- |
| `/server status` | Whether the server is running, since when, whether port 17777 is open, and the last backup time. |
| `/server start update:False` | Back up and start the server. `update:True` runs SteamCMD first. |
| `/server stop` | Close the server window normally, wait up to 30 seconds, then take a final backup. |
| `/server backup` | Zip the save folder into `backups` now. |
| `/server update` | Back up and update through SteamCMD. The server must be stopped first. |

Only one start, stop, backup, or update runs at a time. Results are posted in the channel
with the script's output. If a script takes longer than 14 minutes, Compabot says it is
still running and lets it finish; use `/server status` to check on it.

## Storage and checks

Quotes and favorites live in `data/compabot.sqlite3`, relative to the script, and survive
restarts. Stop the bot before copying that file for a backup. `.env`, the database, and
local environments are ignored by Git. Only one bot process should use the database.

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

`Compabot.py` handles startup and events, `features.py` defines slash commands, and
`storage.py` handles SQLite, and `server_control.py` defines `/server`. No external database or paid service is needed.
