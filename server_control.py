"""/server commands that run the Icarus PowerShell scripts on this Windows host."""
import asyncio
import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

import discord
from discord import app_commands

from mod_update import ModError, ModUpdater

log = logging.getLogger('compabot.server')

DEFAULT_SCRIPTS_DIR = r'C:\IcarusServer\scripts'
SCRIPTS = {'start': 'Start-IcarusServer.ps1', 'stop': 'Stop-IcarusServer.ps1',
           'backup': 'Backup-IcarusServer.ps1', 'update': 'Update-IcarusServer.ps1'}
LABELS = {'start': 'Start', 'stop': 'Stop', 'backup': 'Backup', 'update': 'Update', 'mods': 'Mod update'}
# Interaction follow-ups expire after 15 minutes, so stop waiting a little before that.
REPLY_WINDOW = 14 * 60
STATUS_SCRIPT = Path(__file__).resolve().parent / 'Get-IcarusStatus.ps1'


class ServerError(Exception):
    pass


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
    def __init__(self, admins, scripts_dir=DEFAULT_SCRIPTS_DIR, run=run_process):
        self.admins = frozenset(admins)
        self.scripts_dir = Path(scripts_dir)
        config = read_config(self.scripts_dir / 'config.psd1')
        self.install_root = Path(config.get('InstallRoot') or self.scripts_dir.parent)
        self.name = config.get('ServerName', 'Icarus server')
        self.port = config.get('GamePort', '17777')
        self.run = run
        self.windows = os.name == 'nt'
        self.busy = None
        self.reply_window = REPLY_WINDOW
        self.mods = ModUpdater(self.install_root)

    def powershell(self, *args, env=None):
        return self.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', *args], env)

    async def run_action(self, action, *args):
        """Run one script. Returns (exit code, output); exit code None means it is still running."""
        script = self.scripts_dir / SCRIPTS[action]
        if not script.is_file():
            raise ServerError(f'Script not found: {script}')
        if self.busy:
            raise ServerError(f'{LABELS[self.busy]} is still running. Try again when it finishes.')
        self.busy = action
        task = asyncio.ensure_future(self.powershell('-File', str(script), *args))
        task.add_done_callback(lambda t: self._finished(action, t))
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.reply_window)
        except asyncio.TimeoutError:
            return None, ''

    def _finished(self, action, task):
        self.busy = None
        if task.cancelled():
            return
        if task.exception():
            log.error('%s failed', action, exc_info=task.exception())
        else:
            log.info('%s exited with %s:\n%s', action, *task.result())

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
        code, out = await self.powershell('-File', str(STATUS_SCRIPT),
                                          env={'ICARUS_ROOT': str(self.install_root)})
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
        backups = list((self.install_root / 'backups').glob('Icarus-*.zip'))
        state['backup'] = int(max(p.stat().st_mtime for p in backups)) if backups else None
        return state


    def listening(self, started):
        """Whether the current server log says the game port is accepting players.

        Icarus uses Steam networking, so the game port never appears as a normal
        Windows UDP endpoint; the log line is the reliable signal.
        """
        log_file = self.install_root / 'data' / 'Saved' / 'Logs' / 'Icarus.log'
        try:
            if log_file.stat().st_mtime < started:
                return False
            return f'listening on port {self.port}'.encode() in log_file.read_bytes()
        except OSError:
            return False


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


def mod_report(text, lines, limit=2000):
    # report() keeps script output to about 1,500 characters, so the mod lines fit under
    # Discord's 2,000-character message limit.
    text += '\nMods:\n' + '\n'.join(lines)
    return text if len(text) <= limit else text[:limit - 3] + '...'


def describe(control, state):
    lines = []
    if not state['running']:
        lines.append(f'**{control.name}** is offline.')
    else:
        ready = 'ready for players on port {}' if state.get('ready') else 'still loading, not accepting players on port {} yet'
        lines.append(f"**{control.name}** is online since <t:{state['started']}:R> ({ready.format(control.port)}).")
    if control.busy:
        lines.append(f'{LABELS[control.busy]} is running right now.')
    lines.append(f"Last backup: <t:{state['backup']}:R>." if state['backup'] else 'No backups found.')
    return '\n'.join(lines)


def install_server_commands(bot, control):
    server = app_commands.Group(name='server', description='Control the Icarus server (server admins only).', guild_only=True)

    async def allowed(interaction):
        if interaction.user.id not in control.admins:
            await interaction.response.send_message('Only the server admins can use /server, compa.', ephemeral=True)
            return False
        if not control.windows:
            await interaction.response.send_message('Server commands only work when Compabot runs on the Windows server host.', ephemeral=True)
            return False
        return True

    async def run(interaction, action, *args):
        if not await allowed(interaction):
            return
        await interaction.response.defer(thinking=True)
        try:
            text = report(LABELS[action], *await control.run_action(action, *args))
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    @server.command(name='status', description='Show whether the Icarus server is running and when it was last backed up.')
    async def status(interaction: discord.Interaction):
        if not await allowed(interaction):
            return
        await interaction.response.defer(thinking=True)
        try:
            text = describe(control, await control.status())
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    @server.command(name='start', description='Back up and start the Icarus server, optionally updating it first.')
    async def start(interaction: discord.Interaction, update: bool = False):
        await run(interaction, 'start', *(['-Update'] if update else []))

    @server.command(name='stop', description='Close the Icarus server safely and take a final backup.')
    async def stop(interaction: discord.Interaction):
        await run(interaction, 'stop')

    @server.command(name='backup', description='Back up the Icarus save files now.')
    async def backup(interaction: discord.Interaction):
        await run(interaction, 'backup')

    @server.command(name='update', description='Update the Icarus server through SteamCMD, then its mods (stop it first).')
    async def update(interaction: discord.Interaction):
        if not await allowed(interaction):
            return
        await interaction.response.defer(thinking=True)
        try:
            code, output = await control.run_action('update')
            text = report('Update', code, output)
            if code == 0:
                text = mod_report(text, await control.update_mods())
            elif code is None:
                text += ' Then run `/server mods` to update the mods.'
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    @server.command(name='mods', description='Update the server mods to their latest release (stop the server first).')
    async def mods(interaction: discord.Interaction):
        if not await allowed(interaction):
            return
        await interaction.response.defer(thinking=True)
        try:
            if (await control.status())['running']:
                raise ServerError('Stop the Icarus server before updating its mods.')
            text = '\n'.join(await control.update_mods())
        except ServerError as error:
            text = str(error)
        await interaction.followup.send(text)

    bot.tree.add_command(server)
