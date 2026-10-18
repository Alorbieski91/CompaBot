"""Icarus saved worlds (prospects): list them, pick the one the server loads next, and read their saves."""
import logging
import re
import time
from pathlib import Path

import discord

from ...servers.control import LABELS, ServerError
from ...servers.profile import relative
from .ore_deposits import ProspectError, read_deposits

log = logging.getLogger('compabot.server')


class Worlds:
    """The saved prospects of one Icarus server. /server start world: uses names(), find() and choose()."""

    def __init__(self, control):
        self.control = control

    @property
    def prospects_dir(self):
        return relative(self.control.install_root, self.control.game.saves_dir) / 'DedicatedServer' / 'Prospects'

    @property
    def settings_file(self):
        return self.control.install_root / 'data' / 'Saved' / 'Config' / 'WindowsServer' / 'ServerSettings.ini'

    def names(self):
        """Names of the saved prospects (worlds), from their .json save files."""
        try:
            return sorted((p.stem for p in self.prospects_dir.glob('*.json') if p.is_file()), key=str.lower)
        except OSError:
            return []

    def find(self, name):
        """The saved prospect matching name, ignoring case. Never creates a new world."""
        saved = {p.lower(): p for p in self.names()}
        prospect = saved.get(name.strip().lower())
        if not prospect:
            known = ', '.join(f'`{p}`' for p in saved.values()) or 'none'
            raise ServerError(f'There is no saved world called `{discord.utils.escape_markdown(name)}`. '
                              f'Saved worlds: {known}.')
        return prospect

    def current(self):
        """The prospect the server loads next (LastProspectName), the only saved one, or None."""
        try:
            raw = self.settings_file.read_bytes()
        except OSError:
            raw = b''
        text = raw.decode('utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig', 'replace')
        match = re.search(r'^\s*LastProspectName\s*=\s*(\S+)', text, re.M)
        saved = self.names()
        if match and match[1] in saved:
            return match[1]
        return saved[0] if len(saved) == 1 else None

    def read(self, name):
        """Return (prospect name, save file bytes, map, deposits) for a saved prospect.

        The running server keeps the file and may be rewriting it, so a save that reads
        half-written is retried once and then read from its newest .backup.
        """
        prospect = self.find(name) if name else self.current()
        if not prospect:
            known = ', '.join(f'`{p}`' for p in self.names()) or 'none'
            raise ServerError(f'Pick a world. Saved worlds: {known}.')
        path = self.prospects_dir / f'{prospect}.json'
        error = None
        for attempt, wait in ((path, 0.5), (path, 0), (path.with_name(path.name + '.backup'), 0)):
            try:
                raw = attempt.read_bytes()
                return (prospect, raw, *read_deposits(raw))
            except (OSError, ProspectError) as caught:
                error = caught
                time.sleep(wait)
        raise ServerError(f"Couldn't read the **{prospect}** save: {error}")

    async def choose(self, name):
        """Point the server at a saved prospect for its next start. Returns the prospect's saved name.

        The server only reads ServerSettings.ini when it starts and rewrites it while it runs,
        so this refuses while it is running or another action is busy.
        """
        control = self.control
        prospect = self.find(name)
        if control.busy:
            raise ServerError(f'{LABELS[control.busy]} is still running. Try again when it finishes.')
        if (await control.status())['running']:
            raise ServerError(f'**{control.name}** is already running. Stop it with `/server stop` to switch worlds.')
        if control.busy:  # another action started during the status check
            raise ServerError(f'{LABELS[control.busy]} is still running. Try again when it finishes.')
        try:
            set_prospect(self.settings_file, prospect)
        except OSError as error:
            raise ServerError(f'Could not switch the world in {self.settings_file}: {error}') from None
        log.info('Next start loads prospect %s', prospect)
        return prospect


def set_prospect(path, prospect):
    """Make ServerSettings.ini resume the given prospect on the next start.

    With ResumeProspect=True the server loads LastProspectName, the same setting it writes
    itself when a prospect is launched. LoadProspect and CreateProspect would override it, so
    they are cleared. Other settings, line endings and the file's encoding are kept.
    """
    path = Path(path)
    section = '[/Script/Icarus.DedicatedServerSettings]'
    wanted = {'LastProspectName': prospect, 'ResumeProspect': 'True', 'LoadProspect': '', 'CreateProspect': ''}
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raw = b''
    encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig' if raw[:3] == b'\xef\xbb\xbf' else 'utf-8'
    text = raw.decode(encoding)
    newline = '\r\n' if '\r\n' in text or not text else '\n'
    lines = text.splitlines()
    if section not in (line.strip() for line in lines):
        lines += [section]
    current, start = None, None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith('['):
            current = stripped
            if current == section:
                start = i
            continue
        key = stripped.split('=', 1)[0].strip()
        if current == section and '=' in stripped and key in wanted:
            lines[i] = f'{key}={wanted.pop(key)}'
    lines[start + 1:start + 1] = [f'{key}={value}' for key, value in wanted.items()]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((newline.join(lines) + newline).encode(encoding))

