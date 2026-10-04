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
| `/server status` | Whether the server is running, since when, whether it is ready for players on port 17777 (read from the server log), and the last backup time. |
| `/server start update:False` | Back up and start the server. `update:True` runs `/server update` (game and mods) first, and starts only if it worked. |
| `/server stop` | Close the server window normally, wait up to 30 seconds, then take a final backup. |
| `/server backup` | Zip the save folder into `backups` now. |
| `/server update` | Back up and update through SteamCMD, then update the mods (below). The server must be stopped first. |
| `/server mods` | Update the mods only. The server must be stopped first. |

While someone is playing, Compabot also backs the world up every hour on its own. Every five
minutes it reads the server log to see who is connected, and backs up only if the newest backup
(from any source, including `/server start`, `/server stop`, and `/server backup`) is at least an
hour old, so it never doubles up on a recent one. It waits if another action is running, retries
at the next check if a backup fails, and writes what it did to the bot log. The backup script
still deletes backups older than 14 days.

Only one start, stop, backup, update, or mod update runs at a time. Results are posted in the channel
with the script's output. If a script takes longer than 14 minutes, Compabot says it is
still running and lets it finish; use `/server status` to check on it.

### Mods

After a successful game update (`/server update` or `/server start update:True`), Compabot checks each `.pak` in
`server\Icarus\Content\Paks\mods` that is named like laanp's mods
(`laanp-PetesBeaconTeleport_v1_w252_P.pak`: fix 1 for game week 252) against laanp's
[mod list](https://github.com/laanp/Icarus_Mods_Separated). When a newer week or fix is out,
it downloads it, checks it is a real `.pak`, and moves the old file to `backups\mods`
(the last 5 per mod are kept). Each mod's readme names the game build it was made for; the
installed build is read from `server\Icarus\Config\version.json`. If the game is newer than
the latest mod, Compabot keeps the mod and warns that laanp has not caught up yet. It never
installs a mod made for a newer game than the server has. Other mods are left alone.
Your own game client needs the same mod file in its own `Content\Paks\mods` folder.


### Automatic game updates

While Compabot runs, it checks for an Icarus server update every 2 hours (and a minute after
it starts) by asking SteamCMD for the latest public build and comparing it with the build in
`server\steamapps\appmanifest_2089300.acf`. When Steam has a newer build:

- If the server is stopped, it updates the game and mods, and leaves the server stopped.
- If the server is running with nobody connected, it stops it (with a final backup), updates
  the game and mods, and starts it again.
- If someone is connected (read from the server log), it waits, checks again every 15
  minutes, and installs the update once everyone has left.

The update script backs up the saves before SteamCMD runs, as with `/server update`. If the
update fails, Compabot still starts the server again (if it was running) and tries the update
at the next check. It skips a check while a `/server` command is running.

Set `AUTO_UPDATE_CHANNEL_ID` in `.env` to a channel ID (Developer Mode > right-click the
channel > Copy Channel ID) to have Compabot post there when an update is waiting and when it
has installed one. Without it, these messages only go to the log. Set `AUTO_UPDATE_HOURS`
to change how often it checks, or to `0` to turn automatic updates off.

## Start automatically after restarts

Windows Update restarts the PC often. To have Compabot start every time you sign in, and
restart itself if it crashes or loses its connection, run this once in the Compabot folder
from a normal (not administrator) PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-CompaBotStartup.ps1
```

This creates a Task Scheduler task named `CompaBot` that runs `Start-CompaBot.ps1` hidden,
30 seconds after you sign in. It uses Python from `.venv`, `venv`, or this folder if one has
a virtual environment, otherwise the Python on your PATH. Output goes to `logs\compabot.log` (the run before is kept as
`logs\compabot.prev.log`). Use `.\Stop-CompaBot.ps1` to stop it, `Start-ScheduledTask CompaBot`
to start it again, and `.\Install-CompaBotStartup.ps1 -Remove` to turn auto-start off.
Don't also run `Compabot.py` by hand while the task is running.

The task only runs once you are signed in, because `/server stop` needs the Icarus server's
console window. For unattended restarts, have Windows sign in automatically:

1. Download [Autologon](https://learn.microsoft.com/sysinternals/downloads/autologon) from
   Microsoft Sysinternals, run it, enter your Windows password, and click **Enable**. It stores
   the password encrypted. Run it again and click **Disable** to undo.
2. If you sign in with a PIN or fingerprint, first turn off **Settings > Accounts > Sign-in
   options > For improved security, only allow Windows Hello sign-in**.
3. Lock the PC (Windows key + L) rather than signing out when you walk away.

Anyone with physical access to the PC will be able to use it after a restart, so only do
this on a PC kept somewhere you trust.

## Storage and checks

Quotes and favorites live in `data/compabot.sqlite3`, relative to the script, and survive
restarts. Stop the bot before copying that file for a backup. `.env`, the database, and
local environments are ignored by Git. Only one bot process should use the database.

```powershell
.\.venv\Scripts\python.exe -m unittest -v
```

`Compabot.py` handles startup and events, `features.py` defines slash commands, and
`storage.py` handles SQLite, `server_control.py` defines `/server`, `mod_update.py` updates mods, and `auto_update.py` installs game updates on a schedule. No external database or paid service is needed.
