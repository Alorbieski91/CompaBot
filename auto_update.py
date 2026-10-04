"""Install Icarus game updates on a schedule, so the server is current before anyone plays.

Every few hours Compabot asks SteamCMD for the latest public build of the Icarus dedicated
server (Steam app 2089300) and compares it with the build in the installed appmanifest.
When Steam has a newer one, it waits until nobody is connected, stops the server (which
backs up), runs the update script (which backs up again), updates the mods, and starts the
server again if it was running. Who is connected is read from the server log.

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

import patch_notes

from server_control import ServerError, players_online, report

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
        await self.notify(await patch_notes.describe(posts, build))
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

    async def check(self):
        """Check for a game update once, installing it if nobody is playing. Returns seconds until the next check."""
        control = self.control
        if control.busy:
            return WHILE_PLAYING
        installed = installed_build(control.install_root)
        try:
            latest = public_build(await self.query(control.install_root / 'steamcmd' / 'steamcmd.exe'))
        except (OSError, asyncio.TimeoutError) as error:
            log.warning('Could not ask SteamCMD for the latest build: %r', error)
            return self.every
        if not installed or not latest:
            log.warning('Could not compare builds (installed %s, Steam %s)', installed, latest)
            return self.every
        if latest <= installed:
            log.info('Icarus server is up to date (build %s)', installed)
            await self.share_notes(installed)
            return self.every
        try:
            state = await control.status()
            if state['running']:
                players = players_online(control.current_log(state['started']))
                if players:
                    if self.announced != latest:
                        self.announced = latest
                        await self.notify(f'An Icarus update is out (build {installed} to {latest}). '
                                          f'I will install it once {" and ".join(players)} {"has" if len(players) == 1 else "have"} left the server.')
                    return WHILE_PLAYING
                code, output = await control.run_action('stop', window=SCRIPT_WINDOW)
                if code != 0:
                    await self.notify('An Icarus update is out, but I could not stop the server to install it.\n'
                                      + report('Stop', code, output))
                    return self.every
            code, text = await control.update_with_mods(window=SCRIPT_WINDOW)
            if state['running'] and code is not None:
                # Start again even after a failed update, so nobody is locked out until someone notices.
                text += '\n' + report('Start', *await control.run_action('start', window=SCRIPT_WINDOW))
        except ServerError as error:
            log.warning('Automatic update stopped: %s', error)
            return WHILE_PLAYING
        self.announced = None
        verb = 'Installed' if code == 0 else 'Tried to install'
        await self.notify(f'{verb} Icarus build {latest} automatically (was {installed}).\n{text}')
        if code == 0:
            await self.share_notes(installed_build(control.install_root))
        return self.every
