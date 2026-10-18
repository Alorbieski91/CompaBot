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

Every `/server` command takes an optional `game:` option that picks which game server it acts on:
Icarus or Valheim. Leaving it empty means Icarus. Each game's facts (folders, scripts, process
name, ports, and which extras it has) are in one profile in that game's folder under `bot/games/`.
`mods`, `ores` and `start world:` are Icarus extras and are refused for Valheim.

`game:Valheim` runs the scripts in `C:\ValheimServer\scripts` (set `VALHEIM_SCRIPTS_DIR` if they
are elsewhere) and reads `ServerName` and `GamePort` from their `config.psd1`. `status`, `start`,
`stop`, `restart`, `backup` and `update` work as described below for Icarus, except that the server
counts as ready once `logs\valheim_server.log` says `Game server connected`, players are counted on
the Steam query port 2457 (or from who the log says has connected), and `update` has no mods to
refresh. Down and crash alerts and automatic updates (below) look after Valheim too, and so can the
scheduled start and stop (turned off for Valheim until you set `VALHEIM_ACTIVE_HOURS=on`). Automatic
backups cover Valheim too, zipping `data` into `backups\Valheim-*.zip` whenever its world in
`data\worlds_local` has changed while the server is off.

| Command | Behavior |
| --- | --- |
| `/server status` | Whether the server is running, since when, whether it is ready for players on port 17777 (read from the server log), how many players are on, and the last backup time. |
| `/server start update:False` | Back up and start the server. `update:True` runs `/server update` (game and mods) first, and starts only if it worked. |
| `/server start world:NAME` | Load a different saved world (prospect) and start the server. The name autocompletes from `data\Saved\PlayerData\DedicatedServer\Prospects`; a name with no save there is refused, so this never creates a new world. The server must be stopped first. Compabot sets `LastProspectName=NAME` and `ResumeProspect=True` in `ServerSettings.ini` and clears `LoadProspect` and `CreateProspect`. Without `world`, the ini is left alone and the server resumes the last world. Scheduled starts and update restarts never change the world. |
| `/server stop` | Close the server window normally, wait up to 30 seconds, then take a final backup. Refused while anyone is online; `force:True` stops it anyway. |
| `/server restart` | Stop the server (with its final backup), check that it has fully closed, then start it again and wait until it is ready for players. If it closes while loading, it is started once more. Refused while anyone is online. If the server was already off, it just starts it. Nothing else (other commands, the schedule, auto-updates) can run until the restart is done. |
| `/server backup` | Zip the save folder into `backups` now. The server must be stopped first. |
| `/server update` | Back up and update through SteamCMD, then update the mods (below). The server must be stopped first. |
| `/server mods` | Update the mods only. The server must be stopped first. |
| `/server ores world:NAME ore:NAME file:UPLOAD` | Post a map picture of the deep ore deposits and oil wells (`ore:Oil` for just the wells) in a saved world, with each deposit marked by an ore code (Ti, Pt, Au...) on the A1 (top left) to P16 (bottom right) grid, plus counts per ore. It also attaches the full list by grid square and the world's save, which can be dropped on the [ore map site](https://faysm.github.io/IcarusOreDeposit/) for a map you can filter and zoom. Compabot ships no map artwork: put a square top-down image named after the map (`Olympus.jpg`, `Elysium.png`, `Styx.webp`, `Prometheus.jpg`) in `data\maps` and the deposits are drawn over it; otherwise they are drawn on a plain grid. `world` defaults to the one the server loads; `ore` lists where just that ore is. Works while the server is running. `file:` reads a prospect .json uploaded from your own PC (`%LOCALAPPDATA%\Icarus\Saved\PlayerData\<Steam ID>\Prospects`) instead of a server world. |

Backups only run while the server is off, because the running server keeps its save and log
files open. `/server stop`, `/server start`, `/server update`, and the scheduled stops and restarts
already back up with the server off. Every five minutes Compabot also checks whether the server
is off and the world has changed since the newest backup (for example after a crash, or after the
server was closed outside Compabot), and backs it up if so. It does this for each game whose
scripts folder is on the PC, using that game's own backup script. `/server backup` refuses while the
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

Valheim is watched the same way, on its query port 2457, once its scripts folder exists on the PC.
Valheim writes no crash reports, so a Valheim crash is posted as the server going down. Each alert
names the server and, when the name does not already say it, the game, for example
`**CompaWorld** (Icarus) went down` or `**CompaValheim** is back online`.

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

#### Updating the mods in your own game

Your game needs the same mod file as the server. `clients\Update-IcarusClientMods.cmd` does on a
gaming PC what `/server mods` does on the server, and needs only Windows (no Python or Compabot).
Close Icarus and double-click it. It finds Icarus through Steam, checks each laanp mod in
`Icarus\Content\Paks\mods` with the same rules as the server (the latest release, unless its
readme says it needs a newer game build than yours), downloads it, checks it is a real `.pak`,
and moves the old file to `%LOCALAPPDATA%\IcarusModBackups` (the last 5 per mod are kept). If no
laanp mod is installed yet, it installs Pete's Beacon Teleport. It refuses to change anything
while Icarus is running.

To hand it to a friend, send them both files from the `clients` folder and have them keep the two
together. From PowerShell you can also run it with options:

```powershell
.\Update-IcarusClientMods.ps1 -CheckOnly                    # only say what would change
.\Update-IcarusClientMods.ps1 -GamePath 'D:\Games\Icarus'    # if Steam lookup misses your install
```


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

Valheim gets the same check, on the same schedule, once its scripts folder exists on the PC. It
asks its own SteamCMD (`C:\ValheimServer\steamcmd`) about Steam app 896660, compares the answer with
`server\steamapps\appmanifest_896660.acf`, counts players the way `/server status game:Valheim`
does, and runs `Update-ValheimServer.ps1`. Valheim has no mods to update. Each game's messages
name the game, for example "A Valheim update is out".

Set `AUTO_UPDATE_CHANNEL_ID` in `.env` to a channel ID (Developer Mode > right-click the
channel > Copy Channel ID) to have Compabot post there when an update is waiting and when it
has installed one. Without it, these messages only go to the log. Set `AUTO_UPDATE_HOURS`
to change how often it checks (for example `1` for hourly, or `0.5`), or to `0` to turn
automatic game and mod updates off.

#### What changed in the game

When the server's build changes (by the automatic update or by hand), Compabot also posts the
gameplay changes from the new "Week N Update" and hotfix posts on Steam (for Valheim, its "Patch"
posts): new or changed creature behaviour (like Kiwis laying eggs), taming and farming, new items
and recipes, balance changes, missions, and anything that touches saves. Bug fixes and polish are
left out. It waits until Steam has the notes, and remembers the last build it posted about in
`data/auto_update.json` (`data/auto_update_valheim.json` for Valheim), so the first run after
setup only records the current build.

The list comes straight from the notes: list items that mention creatures, taming, farming,
items, recipes, resources, survival stats, missions and the like come first, then other
"Added", "Changed", "Increased" lines, up to 12. Sections about bug fixes, UI, audio and
performance are skipped. Follow the post link for the full notes.

### Active hours

Compabot keeps each game server to the hours you play, in US Central time:

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

After a 6:00 AM start or restart, Compabot waits up to 5 minutes for the server log to say it is
ready for players before posting. If the server closes while loading, it starts it once more, and
if it closes again or is still loading after 5 minutes, the post says so instead.

Set `SERVER_START_TIME` and `SERVER_STOP_TIME` in `.env` to other 24-hour times (like `07:30`),
or to `off` to turn either one off. `SERVER_TIMEZONE` defaults to `America/Chicago`, which follows
daylight saving time. Run `pip install -r requirements.txt` again after updating, since Windows
needs the `tzdata` package for time zones. Without it Compabot runs with the schedule off and says so in `logs\compabot.log`.

Every game shares these times, and each game can be taken off the schedule on its own:
`ICARUS_ACTIVE_HOURS` and `VALHEIM_ACTIVE_HOURS` in `.env` take `on` or `off`. Left blank, Icarus
is on and Valheim is off, so Valheim is only started and stopped by hand until you set
`VALHEIM_ACTIVE_HOURS=on` and restart Compabot. A game is also skipped if its scripts folder is
not on the PC. Each game is checked separately, so with both on, both are started at 6:00 AM and
each is stopped at 8:00 PM only if nobody is on that game. Each post names its game, for example
`**CompaWorld** (Icarus) was shut down at 8:00 PM because nobody was online.`

## Start automatically after restarts

Windows Update restarts the PC often. To have Compabot start every time you sign in, and
restart itself if it crashes or loses its connection, run this once in the Compabot folder
from a normal (not administrator) PowerShell window:

```powershell
powershell -ExecutionPolicy Bypass -File .\Install-CompaBotStartup.ps1
```

This creates a Task Scheduler task named `CompaBot` that runs `Start-CompaBot.ps1` hidden,
30 seconds after you sign in, and a `CompaBot Watchdog` task (below). It uses Python from `.venv`, `venv`, or this folder if one has
a virtual environment, otherwise the Python on your PATH. Output goes to `logs\compabot.log` (the run before is kept as
`logs\compabot.prev.log`). Use `.\Stop-CompaBot.ps1` to stop it, `Start-ScheduledTask CompaBot`
to start it again, and `.\Install-CompaBotStartup.ps1 -Remove` to remove both tasks.
Don't also run `Compabot.py` by hand while the task is running.

### Watchdog: updates and restarts

The `CompaBot Watchdog` task runs `Watch-CompaBot.ps1` every 5 minutes while you are signed in.
It is a plain PowerShell script, so it costs nothing to run. Each time it:

1. **Pulls new code.** If GitHub's `main` has new commits, it fast-forwards this folder, runs
   `pip install -r requirements.txt` if that file changed, runs the tests, and restarts Compabot.
   If any of that fails, or Compabot doesn't sign in to Discord within 2 minutes, it goes back to
   the commit it was on, restarts Compabot there, and won't try that commit again (a newer commit
   is tried as usual). It skips the update while an Icarus script or SteamCMD is running, so a
   `/server` action or automatic update is never cut off. It never overwrites changes made on this
   PC: if a tracked file was edited here, or this folder is not on `main` or has commits that
   aren't on GitHub, it leaves the folder alone and says so.
2. **Starts Compabot if it is down.** `Start-CompaBot.ps1` already restarts the bot after a crash;
   the watchdog covers that restart loop itself being gone. It restarts Compabot at most 3 times an
   hour, so a bot that can't stay up isn't restarted forever.

It leaves Compabot off when it was stopped on purpose: after `.\Stop-CompaBot.ps1` (until the next
`Start-ScheduledTask CompaBot`), or after Compabot shut down normally. So the usual
`.\Stop-CompaBot.ps1` then `Start-ScheduledTask CompaBot` still works, and after a merge on GitHub
you no longer need to `git pull` and restart by hand.

What it does is written to `logs\watchdog.log`. To also hear about it in Discord, even while
Compabot is down, create a webhook (channel settings > Integrations > Webhooks > New Webhook >
Copy Webhook URL) and set `WATCHDOG_WEBHOOK_URL` in `.env` to it. It posts each update, rollback and
restart, and each ongoing problem once. It needs Git on your PATH to pull updates; without it, it
only keeps Compabot running.

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
Watch-CompaBot.ps1           pulls updates and restarts Compabot if it is down (watchdog task)
Install-CompaBotStartup.ps1  creates or removes the startup and watchdog tasks
bot/
  features.py                quotes, favorites, /pick and /say
  storage.py                 SQLite storage for quotes and favorites
  servers/                   what every game server shares
    profile.py               GameProfile: what a game folder tells the shared code
    control.py               runs a game's scripts; status, start, stop, restart, backups
    commands.py              the /server commands every game has
    Get-ServerStatus.ps1     tells control.py whether a server is running
    monitor.py               down, crash and back-up alerts
    steam_query.py           player count from the Steam query port
    auto_update.py           scheduled game (and mod) updates
    patch_notes.py           gameplay changes from the Steam patch notes
    active_hours.py          scheduled start and stop
  games/                     one folder per game; __init__.py lists them
    icarus/
      __init__.py            the Icarus profile
      players.py             who is on, from the server log
      commands.py            /server ores and /server mods
      worlds.py              saved prospects for /server start world:
      mods.py                laanp mod updates
      ore_deposits.py        deep ore deposits in a prospect save
      ore_map.py             the ore map picture
    valheim/
      __init__.py            the Valheim profile
      players.py             who is on, from the server log
tests/                       unit tests (run with the command above)
data/, logs/, .env           created locally and ignored by Git
```

No external database or paid service is needed.
