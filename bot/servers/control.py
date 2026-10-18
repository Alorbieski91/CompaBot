"""Run a game server's PowerShell scripts on this Windows host, and the helpers the /server commands share."""
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from ..games import DEFAULT_GAME, ICARUS
from .profile import ModError, ServerError, relative
from .steam_query import query_info

log = logging.getLogger('compabot.server')

DEFAULT_SCRIPTS_DIR = ICARUS.scripts_dir
SCRIPTS = ICARUS.scripts
LABELS = {'start': 'Start', 'stop': 'Stop', 'backup': 'Backup', 'update': 'Update', 'mods': 'Mod update',
          'restart': 'Restart'}
# Interaction follow-ups expire after 15 minutes, so stop waiting a little before that.
REPLY_WINDOW = 14 * 60
# How long a freshly started server gets to load its world (usually 10-20 seconds), and how often to look.
READY_TIMEOUT = 5 * 60
READY_POLL = 10
# The running server keeps its save and log files open, so backups only run while it is off.
RUNNING_BACKUP = ("{name} is running, and its save files can't be backed up while it has them open. "
                  '{stop} backs it up as soon as it has closed.')
# Discord shows at most 25 autocomplete choices.
MAX_CHOICES = 25


def read_config(path):
    """Read the simple key = value pairs from config.psd1."""
    try:
        text = Path(path).read_text(encoding='utf-8-sig')
    except OSError:
        return {}
    return {k: v.strip("'\"") for k, v in re.findall(r"^\s*(\w+)\s*=\s*('[^']*'|\"[^\"]*\"|\d+)", text, re.M)}


async def run_process(argv, env=None):
    """Run a process to completion and return (exit code, combined output).

    Output goes to a temp file rather than a pipe: Start-IcarusServer.ps1 launches the
    game server, which would otherwise hold the pipe open and block us until it exits.
    """
    with tempfile.TemporaryFile() as out:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            env={**os.environ, **(env or {})})
        code = await proc.wait()
        out.seek(0)
        return code, out.read().decode('utf-8', 'replace')


class ServerControl:
    def __init__(self, admins, scripts_dir=None, run=run_process, game=ICARUS):
        self.game = game
        self.admins = frozenset(admins)
        self.scripts_dir = Path(scripts_dir or game.scripts_dir)
        config = read_config(self.scripts_dir / 'config.psd1')
        self.install_root = Path(config.get('InstallRoot') or self.scripts_dir.parent)
        self.name = config.get('ServerName', game.server_name)
        self.port = config.get('GamePort', game.game_port)
        self.query_port = int(config.get('QueryPort', game.query_port))
        self.max_players = int(config.get('MaxPlayers', game.max_players))
        self.run = run
        self.query = query_info
        self.windows = os.name == 'nt'
        self.busy = None
        self.holder = None  # the task running a restart, which may run its own stop and start
        self.stopped_at = float('-inf')
        self.reply_window = REPLY_WINDOW
        # The game's extras, from its folder in bot/games; None when it has none.
        self.mods = game.mods(self.install_root) if game.supports('mods') else None
        self.worlds = game.worlds(self) if game.supports('worlds') else None

    def slash(self, command):
        """A /server command to suggest in a reply, with game: added for any game but the default."""
        game = '' if self.game.key == DEFAULT_GAME else f' game:{self.game.key}'
        return f'`/server {command}{game}`'

    def powershell(self, *args, env=None):
        return self.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', *args], env)

    async def run_action(self, action, *args, window=None):
        """Run one script. Returns (exit code, output); exit code None means it is still running."""
        script = self.scripts_dir / self.game.scripts[action]
        if not script.is_file():
            raise ServerError(f'Script not found: {script}')
        self.check_free()
        if action == 'backup':
            if (await self.status())['running']:
                raise ServerError(RUNNING_BACKUP.format(name=f'**{self.name}**', stop=self.slash('stop')))
            self.check_free()  # another action may have started during the status check
        self.busy = action
        if action == 'stop':
            self.stopped_at = time.time()
        task = asyncio.ensure_future(self.powershell('-File', str(script), *args))
        task.add_done_callback(lambda t: self._finished(action, t))
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.reply_window if window is None else window)
        except asyncio.TimeoutError:
            return None, ''

    def check_free(self):
        """Refuse if another action is running, unless it is a restart and this is that restart's own step."""
        if self.busy and not (self.busy == 'restart' and asyncio.current_task() is self.holder):
            raise ServerError(f'{LABELS[self.busy]} is still running. Try again when it finishes.')

    def _finished(self, action, task):
        # A restart keeps everything else out between its stop and its start.
        self.busy = 'restart' if self.holder else None
        if task.cancelled():
            return
        if task.exception():
            log.error('%s failed', action, exc_info=task.exception())
        else:
            log.info('%s exited with %s:\n%s', action, *task.result())

    async def stop(self, force=False, window=None):
        """Stop the server for /server stop. Returns (exit code, output).

        Refuses while anyone is online unless force is set.
        """
        if not force:
            self.check_free()
            state = await self.status()
            if state['running'] and state['players'].players:
                raise ServerError(f'{online(state["players"])} on **{self.name}**, so I did not stop it. '
                                  f"Use {self.slash('stop force:True')} to stop it anyway.")
        return await self.run_action('stop', window=window)

    async def restart(self, window=None):
        """Stop the server, make sure it has closed, then start it and wait until it is ready. Returns a report.

        Refuses while anyone is online. The whole restart counts as one action, so the schedule, the
        auto-updater and other commands can't slip in between the stop and the start.
        """
        began = time.monotonic()
        window = self.reply_window if window is None else window
        name = f'**{self.name}**'
        self.check_free()
        state = await self.status()
        self.check_free()  # another action may have started during the status check
        if state['running'] and state['players'].players:
            raise ServerError(f'{online(state["players"])} on {name}, so I did not restart it.')
        self.busy, self.holder = 'restart', asyncio.current_task()
        try:
            text, failed = '', f'{name} was stopped, but it did not come back up.'
            if state['running']:
                code, output = await self.run_action('stop', window=window)
                if code != 0:
                    return report('Stop', code, output) + '\nThe server was not started again.'
                if (await self.status())['running']:
                    return f'{name} still looks like it is running after the stop, so I did not start it again.'
            else:
                text, failed = f'{name} was not running, so I just started it.\n', 'It did not come up.'
            problem, retried = await launch(self, max(1, window - (time.monotonic() - began)))
            if problem:
                return text + f'{failed}\n{problem}'
            again = ' It closed while loading the first time, so I started it again.' if retried else ''
            done = 'It is ready for players.' if text else f'{name} restarted and is ready for players.'
            return text + done + again
        finally:
            self.holder = None
            if self.busy == 'restart':
                self.busy = None

    async def update_with_mods(self, window=None):
        """Update the game, then its mods if that worked. Returns (update exit code, report)."""
        code, output = await self.run_action('update', window=window)
        text = report('Update', code, output)
        if code == 0 and self.mods:
            text = mod_report(text, await self.update_mods())
        elif code is None and self.mods:
            text += ' Then run `/server mods` to update the mods.'
        return code, text

    async def update_mods(self):
        """Bring installed mods up to the latest release. Returns lines to report."""
        if self.busy:
            raise ServerError(f'{LABELS[self.busy]} is still running. Try again when it finishes.')
        self.busy = 'mods'
        try:
            return await asyncio.to_thread(self.mods.update)
        except (ModError, OSError) as error:
            log.warning('Mod update failed: %s', error)
            return [f'Mod update failed: {error}']
        finally:
            self.busy = None

    async def status(self):
        code, out = await self.powershell('-File', str(self.game.status_script),
                                          env={'GAME_ROOT': str(self.install_root), 'GAME_PROCESS': self.game.process})
        if code != 0:
            raise ServerError(f'Status check failed:\n{tail(out)}')
        # Windows PowerShell can mix progress records (#< CLIXML) into the output, so
        # read the JSON line itself rather than whatever line comes last.
        lines = [line.strip() for line in out.splitlines() if line.strip().startswith('{')]
        try:
            state = json.loads(lines[-1])
        except (IndexError, ValueError):
            raise ServerError(f'Status check returned something unexpected:\n{tail(out)}') from None
        if state['running']:
            state['ready'] = self.listening(state['started'])
            state['players'] = await self.players(state['started'])
        backups = list((self.install_root / 'backups').glob(self.game.backup_glob))
        state['backup'] = int(max(p.stat().st_mtime for p in backups)) if backups else None
        return state


    async def wait_ready(self, timeout=READY_TIMEOUT, poll=READY_POLL):
        """After a start, wait for the server to accept players.

        Returns 'ready', 'closed' if the server process went away while loading, or 'loading' if
        it is still not ready after `timeout` seconds. The start script only launches the server,
        so its exit code says nothing about whether the server got through loading.
        """
        deadline = time.monotonic() + timeout
        while True:
            await asyncio.sleep(poll)
            try:
                state = await self.status()
            except ServerError as error:
                log.warning('Could not check whether the server is up: %s', error)
            else:
                if not state['running']:
                    return 'closed'
                if state['ready']:
                    return 'ready'
            if time.monotonic() >= deadline:
                return 'loading'

    def current_log(self, started):
        """The server log text, or '' if it is missing or left over from an earlier run."""
        log_file = relative(self.install_root, self.game.log_file)
        try:
            if log_file.stat().st_mtime < started:
                return ''
            return log_file.read_bytes().decode('utf-8', 'replace')
        except OSError:
            return ''

    async def players(self, started):
        """Who is on: the count from the Steam query port, else the names from the server log."""
        info = await asyncio.to_thread(self.query, self.query_port)
        if info:
            return info
        return self.game.log_players(self.current_log(started), self.max_players)

    def listening(self, started):
        """Whether the current server log says the game port is accepting players."""
        return self.game.ready_line.format(port=self.port) in self.current_log(started)

    @property
    def crashes_dir(self):
        return relative(self.install_root, self.game.crashes_dir) if self.game.crashes_dir else None

    def newest_save(self):
        """When the world save files last changed, or None if there are none."""
        saves = relative(self.install_root, self.game.saves_dir)
        try:
            return max((p.stat().st_mtime for p in saves.rglob('*') if p.is_file()), default=None)
        except OSError:
            return None

    async def auto_backup(self):
        """Back up while the server is off, if the world has changed since the newest backup.

        Stop, start, and update already back up with the server off; this catches the rest, such
        as a crash or a server closed outside Compabot. Returns a short reason when it skips, or
        the backup's (exit code, output).
        """
        if self.busy:
            return f'{LABELS[self.busy]} is running'
        state = await self.status()
        if state['running']:
            return 'server is running'
        saved = self.newest_save()
        if saved is None or (state['backup'] and state['backup'] >= saved):
            return 'newest backup is up to date'
        return await self.run_action('backup')


def label(control):
    """The server's name in bold, with its game added when the name does not already say it."""
    name, game = control.name, control.game.name
    return f'**{name}**' if game.lower() in name.lower() else f'**{name}** ({game})'


async def launch(control, window):
    """Start the server and wait until it accepts players, starting it once more if it closes while loading.

    Returns (problem or None, whether it had to be started twice).
    """
    for attempt in (1, 2):
        code, output = await control.run_action('start', window=window)
        if code != 0:
            return report('Start', code, output), attempt > 1
        result = await control.wait_ready()
        if result == 'ready':
            return None, attempt > 1
        if result == 'loading':
            return (f'It is running but still not ready for players after {READY_TIMEOUT // 60} minutes. '
                    f"Check {control.slash('status')}; if it stays this way, try {control.slash('restart')}."), attempt > 1
        log.warning('The server closed while loading (start %s of 2)', attempt)
    return ('It closed while loading, twice, without a crash report. '
            f"Check `{control.game.log_file}` on the PC, then try {control.slash('start')}."), True


def online(players):
    """'nscript is online' or '2 players are online'."""
    if players.names:
        return f'{" and ".join(players.names)} {"is" if len(players.names) == 1 else "are"} online'
    return f'{players.players} {"player is" if players.players == 1 else "players are"} online'



def tail(text, limit=1500):
    text = '\n'.join(line for line in text.splitlines()
                     if not line.startswith(('#< CLIXML', '<Objs '))).strip().replace('```', "'''")
    return text if len(text) <= limit else '...' + text[-limit:]


def report(label, code, output):
    if code is None:
        return f'{label} is still running on the host. Check `/server status` in a few minutes.'
    body = f'\n```\n{tail(output)}\n```' if output.strip() else ''
    if code == 0:
        return f'{label} finished.{body}'
    return f'{label} failed (exit code {code}).{body}'


def fit(text, limit=2000):
    """Keep a reply under Discord's 2,000-character message limit."""
    return text if len(text) <= limit else text[:limit - 3] + '...'


def mod_report(text, lines):
    return fit(text + '\nMods:\n' + '\n'.join(lines))


def describe(control, state):
    lines = []
    if not state['running']:
        lines.append(f'**{control.name}** is offline.')
    else:
        ready = 'ready for players on port {}' if state.get('ready') else 'still loading, not accepting players on port {} yet'
        lines.append(f"**{control.name}** is online since <t:{state['started']}:R> ({ready.format(control.port)}).")
        players = state['players']
        names = f" ({', '.join(players.names)})" if players.names else ''
        lines.append(f'Players: {players.players}/{players.max_players}{names}.')
    if control.busy:
        lines.append(f'{LABELS[control.busy]} is running right now.')
    lines.append(f"Last backup: <t:{state['backup']}:R>." if state['backup'] else 'No backups found.')
    return '\n'.join(lines)
