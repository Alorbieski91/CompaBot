"""Install Icarus game and mod updates on a schedule, so the server is current before anyone plays.

Every few hours Compabot asks SteamCMD for the latest public build of the Icarus dedicated
server (Steam app 2089300) and compares it with the build in the installed appmanifest.
When Steam has a newer one, it waits until nobody is connected, stops the server (which
backs up), runs the update script (which backs up again), updates the mods, and starts the
server again if it was running. Who is connected is read from the server log.

Without a game update, it also checks laanp's mod list for a newer release of an installed mod
that the installed game can run, and installs it the same way: wait until nobody is on, stop,
swap the mod, start again.

Whenever the installed build changes (automatically or by hand), it also posts the gameplay
changes from that week's patch notes; see patch_notes.py.
"""
import asyncio
import json
import logging
import re
import subprocess
import time
from pathlib import Path

from . import patch_notes
from .server_control import ServerError, mod_report, report

log = logging.getLogger('compabot.autoupdate')

APP_ID = '2089300'
FIRST_CHECK = 60
WHILE_PLAYING = 15 * 60
STEAMCMD_TIMEOUT = 5 * 60
# Stop, update and start can each take a while; there is no Discord reply window to fit in here.
SCRIPT_WINDOW = 2 * 60 * 60
MANIFEST_BUILD = re.compile(r'"buildid"\s+"(\d+)"')


def installed_build(install_root):
    """The Steam build ID of the installed server, or None if the manifest can't be read."""
    path = Path(install_root) / 'server' / 'steamapps' / f'appmanifest_{APP_ID}.acf'
    try:
        match = MANIFEST_BUILD.search(path.read_text(encoding='utf-8', errors='replace'))
    except OSError:
        return None
    return int(match[1]) if match else None


def public_build(app_info):
    """The public branch's build ID from SteamCMD's app_info_print output, or None."""
    branches = app_info.find('"branches"')
    public = app_info.find('"public"', branches) if branches >= 0 else -1
    match = MANIFEST_BUILD.search(app_info, public) if public >= 0 else None
    return int(match[1]) if match else None


async def query_steam(steamcmd):
    """Ask SteamCMD for the server's app info. Returns its output."""
    # A stale cached appinfo.vdf can make SteamCMD report an old build, so refresh it.
    (Path(steamcmd).parent / 'appcache' / 'appinfo.vdf').unlink(missing_ok=True)
    proc = await asyncio.create_subprocess_exec(
        str(steamcmd), '+login', 'anonymous', '+app_info_update', '1', '+app_info_print', APP_ID, '+quit',
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), STEAMCMD_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return out.decode('utf-8', 'replace')


class AutoUpdater:
    def __init__(self, control, notify, every_hours=2, query=query_steam, state_file=None, news=None):
        self.control = control
        self.notify = notify
        self.every = every_hours * 60 * 60
        self.query = query
        self.announced = None
        self.failed_mods = None
        # Remembers which build the last patch-notes post covered, across restarts.
        self.state_file = Path(state_file) if state_file else None
        self.news = news or (lambda: asyncio.to_thread(patch_notes.fetch_json, patch_notes.NEWS_URL))

    def load_state(self):
        try:
            return json.loads(self.state_file.read_text(encoding='utf-8'))
        except (OSError, ValueError, AttributeError):
            return None

    def save_state(self, build, since):
        if self.state_file:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps({'build': build, 'since': since}), encoding='utf-8')

    async def share_notes(self, build):
        """Post the gameplay changes once the server is on a build we haven't posted about yet."""
        state = self.load_state()
        if not state:
            self.save_state(build, int(time.time()))  # first run: start from now
            return
        if state.get('build') == build:
            return
        try:
            posts = patch_notes.update_posts(await self.news(), state.get('since', 0))
        except Exception as error:
            log.warning('Could not read the Icarus patch notes from Steam: %r', error)
            return
        if not posts:
            log.info('Build %s has no new patch notes on Steam yet', build)
            return  # Steam often posts the notes a little after the build; try again next check
        await self.notify(patch_notes.describe(posts, build))
        self.save_state(build, posts[-1]['date'])

    async def run(self):
        await asyncio.sleep(FIRST_CHECK)
        while True:
            try:
                delay = await self.check()
            except Exception:
                log.exception('Update check failed')
                delay = self.every
            await asyncio.sleep(delay)

    async def mod_updates(self):
        """Mods with a newer release the installed game can run, or [] if laanp's list can't be read."""
        try:
            return await asyncio.to_thread(self.control.mods.available)
        except Exception as error:
            log.warning('Could not check for mod updates: %r', error)
            return []

    async def check(self):
        """Check for a game or mod update once, installing it if nobody is playing. Returns seconds until the next check."""
        control = self.control
        if control.busy:
            return WHILE_PLAYING
        installed = installed_build(control.install_root)
        try:
            latest = public_build(await self.query(control.install_root / 'steamcmd' / 'steamcmd.exe'))
        except (OSError, asyncio.TimeoutError) as error:
            log.warning('Could not ask SteamCMD for the latest build: %r', error)
            latest = None
        game = bool(installed and latest and latest > installed)
        if not installed or not latest:
            log.warning('Could not compare builds (installed %s, Steam %s)', installed, latest)
        elif not game:
            log.info('Icarus server is up to date (build %s)', installed)
            await self.share_notes(installed)
        # A game update brings the mods along with it, so only look for mod releases without one.
        mods = [] if game else await self.mod_updates()
        if mods and mods == self.failed_mods:
            log.info('Not retrying the mod update that failed (%s); /server mods tries again', '; '.join(mods))
            mods = []
        if not game and not mods:
            return self.every
        if game:
            key, what = latest, f'An Icarus update is out (build {installed} to {latest})'
        else:
            key, what = mods, f'A mod update is out ({"; ".join(mods)})'
        try:
            state = await control.status()
            if state['running']:
                players = state['players']
                if players.players:
                    if self.announced != key:
                        self.announced = key
                        who = (f'{" and ".join(players.names)} {"has" if len(players.names) == 1 else "have"}' if players.names
                               else f'{players.players} {"player has" if players.players == 1 else "players have"}')
                        await self.notify(f'{what}. I will install it once {who} left the server.')
                    return WHILE_PLAYING
                code, output = await control.run_action('stop', window=SCRIPT_WINDOW)
                if code != 0:
                    await self.notify(f'{what}, but I could not stop the server to install it.\n'
                                      + report('Stop', code, output))
                    return self.every
            if game:
                code, text = await control.update_with_mods(window=SCRIPT_WINDOW)
            else:
                lines = await control.update_mods()
                code = 1 if any(line.startswith('Mod update failed') for line in lines) else 0
                self.failed_mods = mods if code else None
                text = mod_report('', lines).lstrip('\n')
            if state['running'] and code is not None:
                # Start again even after a failed update, so nobody is locked out until someone notices.
                text += '\n' + report('Start', *await control.run_action('start', window=SCRIPT_WINDOW))
        except ServerError as error:
            log.warning('Automatic update stopped: %s', error)
            return WHILE_PLAYING
        self.announced = None
        if not game:
            verb = 'Updated the mods' if code == 0 else 'Tried to update the mods'
            again = '' if code == 0 else '\nI will not try this again on my own; run `/server mods` to retry.'
            await self.notify(f'{verb} automatically.\n{text}{again}')
            return self.every
        verb = 'Installed' if code == 0 else 'Tried to install'
        await self.notify(f'{verb} Icarus build {latest} automatically (was {installed}).\n{text}')
        if code == 0:
            await self.share_notes(installed_build(control.install_root))
        return self.every
