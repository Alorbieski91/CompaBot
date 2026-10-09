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
| `/server status` | Whether the server is running, since when, whether it is ready for players on port 17777 (read from the server log), how many players are on, and the last backup time. |
| `/server start update:False` | Back up and start the server. `update:True` runs `/server update` (game and mods) first, and starts only if it worked. |
| `/server start world:NAME` | Load a different saved world (prospect) and start the server. The name autocompletes from `data\Saved\PlayerData\DedicatedServer\Prospects`; a name with no save there is refused, so this never creates a new world. The server must be stopped first. Compabot sets `LastProspectName=NAME` and `ResumeProspect=True` in `ServerSettings.ini` and clears `LoadProspect` and `CreateProspect`. Without `world`, the ini is left alone and the server resumes the last world. Scheduled starts and update restarts never change the world. |
| `/server stop` | Close the server window normally, wait up to 30 seconds, then take a final backup. |
| `/server backup` | Zip the save folder into `backups` now. The server must be stopped first. |
| `/server update` | Back up and update through SteamCMD, then update the mods (below). The server must be stopped first. |
| `/server mods` | Update the mods only. The server must be stopped first. |

Backups only run while the server is off, because the running server keeps its save and log
files open. `/server stop`, `/server start`, `/server update`, and the scheduled stops and restarts
already back up with the server off. Every five minutes Compabot also checks whether the server
is off and the world has changed since the newest backup (for example after a crash, or after the
server was closed outside Compabot), and backs it up if so. `/server backup` refuses while the
server is running. A failed backup is retried at the next check and written to the bot log. The
backup script still deletes backups older than 14 days.

Only one start, stop, backup, update, or mod update runs at a time. Results are posted in the channel
with the script's output. If a script takes longer than 14 minutes, Compabot says it is
still running and lets it finish; use `/server status` to check on it.

### Down and crash alerts

Set `ALERT_CHANNEL_ID` in `.env` to a channel ID (right-click the channel > Copy Channel ID) to
have Compabot watch the server. Every minute it asks the server's Steam query port (27015,
`QueryPort` in `config.psd1`) for its player count, and checks the server process when that
gets no answer. It posts in that channel when:

- the server crashes: with the error from the crash report, and the newest report from
  `data\Saved\Crashes` (log, crash details, and minidump) attached as a zip;
- the server closes without a crash report, for example when it is closed on the PC;
- the server is still running but has stopped answering, which usually means it is frozen;
- the server is back and ready for players, with the player count.

A change has to be seen on two checks in a row before it is posted, so alerts arrive one to two
minutes after the fact. `/server stop` is not announced as an outage, and nothing is posted
for whatever state the server is in when Compabot starts. The bot needs **Attach Files** in
that channel, in addition to the permissions listed in Setup.

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


### Automatic game and mod updates

While Compabot runs, it checks for an Icarus server update every 2 hours (and a minute after
it starts) by asking SteamCMD for the latest public build and comparing it with the build in
`server\steamapps\appmanifest_2089300.acf`. When Steam has a newer build:

- If the server is stopped, it updates the game and mods, and leaves the server stopped.
- If the server is running with nobody connected, it stops it (with a final backup), updates
  the game and mods, and starts it again.
- If someone is connected (the same player check as `/server status`), it waits, checks again every 15
  minutes, and installs the update once everyone has left.

The update script backs up the saves before SteamCMD runs, as with `/server update`. If the
update fails, Compabot still starts the server again (if it was running) and tries the update
at the next check. It skips a check while a `/server` command is running.

When there is no game update, the same check also asks laanp's mod list whether an installed
mod has a newer release that the installed game can run. If one does, Compabot installs it the
same way: it waits until nobody is connected, stops the server, swaps the mod (old file to
`backups\mods`), and starts the server again if it was running. A mod made for the next game
week waits for that game update, which brings it in with one restart. If a mod update fails,
Compabot starts the server again and does not retry that same release on its own; run
`/server mods` to try again. Remember that your own game client needs the new mod file too.

Set `AUTO_UPDATE_CHANNEL_ID` in `.env` to a channel ID (Developer Mode > right-click the
channel > Copy Channel ID) to have Compabot post there when an update is waiting and when it
has installed one. Without it, these messages only go to the log. Set `AUTO_UPDATE_HOURS`
to change how often it checks (for example `1` for hourly, or `0.5`), or to `0` to turn
automatic game and mod updates off.

#### What changed in the game

When the server's build changes (by the automatic update or by hand), Compabot also posts the
gameplay changes from the new "Week N Update" and hotfix posts on Steam: new or changed creature
behaviour (like Kiwis laying eggs), taming and farming, new items and recipes, balance changes,
missions, and anything that touches saves. Bug fixes and polish are left out. It waits until
Steam has the notes, and remembers the last build it posted about in `data/auto_update.json`, so
the first run after setup only records the current build.

The list comes straight from the notes: list items that mention creatures, taming, farming,
items, recipes, resources, survival stats, missions and the like come first, then other
"Added", "Changed", "Increased" lines, up to 12. Sections about bug fixes, UI, audio and
performance are skipped. Follow the post link for the full notes.

### Active hours

Compabot keeps the server to the hours you play, in US Central time:

- At 8:00 PM, if nobody is on, it stops the server (with the usual final backup). If someone
  is on, the server is left running for the night.
- At 6:00 AM, it starts the server if it is off. If it is still running with nobody on (it was
  started by hand after 8:00 PM, or someone was on at 8:00 PM), it restarts it to refresh it.
  If someone is playing at 6:00 AM, it is left alone.

Each of these is posted in `ALERT_CHANNEL_ID` (the 6:00 AM start or restart, the 8:00 PM
shutdown, and anything that failed). The usual "back online" alert follows a minute or two after
a start, and scheduled stops are not reported as outages. If another `/server` action is running
at that time, or Compabot was restarting, it tries again every 30 seconds for up to an hour, then
waits for the next day.

Set `SERVER_START_TIME` and `SERVER_STOP_TIME` in `.env` to other 24-hour times (like `07:30`),
or to `off` to turn either one off. `SERVER_TIMEZONE` defaults to `America/Chicago`, which follows
daylight saving time. Run `pip install -r requirements.txt` again after updating, since Windows
needs the `tzdata` package for time zones. Without it Compabot runs with the schedule off and says so in `logs\compabot.log`.

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

## Layout

```
Compabot.py                  starts the bot and handles events (run this)
Start-CompaBot.ps1           restart loop used by the CompaBot startup task
Stop-CompaBot.ps1            stops the bot and its restart loop
Install-CompaBotStartup.ps1  creates or removes the startup task
bot/
  features.py                quotes, favorites, /pick and /say
  storage.py                 SQLite storage for quotes and favorites
  icarus/
    server_control.py        /server commands and the Icarus script runner
    Get-IcarusStatus.ps1     tells server_control.py whether the server is running
    monitor.py               down, crash and back-up alerts
    steam_query.py           player count from the Steam query port
    auto_update.py           scheduled game updates
    patch_notes.py           gameplay changes from the Steam patch notes
    mod_update.py            laanp mod updates
    active_hours.py          scheduled start and stop
tests/                       unit tests (run with the command above)
data/, logs/, .env           created locally and ignored by Git
```

No external database or paid service is needed.
