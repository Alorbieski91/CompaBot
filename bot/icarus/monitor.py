"""Watch the Icarus server and post to Discord when it goes down, crashes, or comes back."""
import asyncio
import io
import logging
import re
import time
import zipfile
from pathlib import Path

import discord

from .server_control import ServerError

log = logging.getLogger('compabot.monitor')

POLL_SECONDS = 60
# A change must be seen this many polls in a row before it is announced, so one
# dropped query packet or a slow status check does not cause a false alarm.
CONFIRM = 2
# /server stop closes the server on purpose; going down this soon after one is not announced.
INTENTIONAL_GRACE = 10 * 60
# Discord's smallest upload limit, with room for the message itself.
ATTACHMENT_LIMIT = 9 * 1024 * 1024

def latest_crash(crashes_dir, after):
    """The newest crash report folder written after `after` (Unix time), or None."""
    try:
        folders = [p for p in Path(crashes_dir).iterdir() if p.is_dir() and p.stat().st_mtime > after]
    except OSError:
        return None
    return max(folders, key=lambda p: p.stat().st_mtime, default=None)


def crash_summary(folder):
    """The error and the function it happened in, read from the crash report."""
    try:
        text = (folder / 'CrashContext.runtime-xml').read_text(encoding='utf-8', errors='replace')
    except OSError:
        return None
    error = re.search(r'<ErrorMessage>(.*?)</ErrorMessage>', text, re.S)
    stack = re.search(r'<CallStack>([^<\n]*)', text)
    parts = [m.group(1).strip() for m in (error, stack) if m and m.group(1).strip()]
    # Drop the build machine's source path from the call stack line.
    return '\n'.join(re.sub(r'\s*\[[A-Z]:\\[^\]]*\]', '', part) for part in parts) or None


def crash_archive(folder, limit=ATTACHMENT_LIMIT):
    """Zip the crash report (log, context, and minidump) into memory. Returns (name, bytes) or None."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    if buffer.tell() > limit:
        return None
    return f'{folder.name}.zip', buffer.getvalue()


class ServerMonitor:
    """Tracks the server as up, starting, hung, or down and reports changes.

    `observe` polls; `step` turns one observation into the message to post, if any.
    """

    def __init__(self, control, clock=time.time):
        self.control = control
        self.clock = clock
        self.state = None
        self.pending = None
        self.seen = 0
        self.info = None
        self.answered = False  # Whether the query port has ever answered, so silence there means something.
        self.started = None

    async def observe(self):
        """Returns (state, Info or None). State is up, starting, hung, or down."""
        info = await asyncio.to_thread(self.control.query, self.control.query_port)
        if info:
            self.answered = True
            if self.started is None:
                self.started = self.clock()
            return 'up', info
        try:
            status = await self.control.status()
        except ServerError as error:
            log.warning('Monitor could not check the server: %s', error)
            return None, None
        if not status['running']:
            return 'down', None
        self.started = status['started']
        if not self.control.listening(status['started']):
            return 'starting', None
        # The log says it is ready. If the query port has never answered, it may simply not
        # answer queries, so trust the log; otherwise the server stopped answering.
        if self.answered:
            return 'hung', None
        return 'up', status.get('players')

    def step(self, state, info):
        """Feed one observation. Returns (text, crash folder or None) to post, or None."""
        if state is None:
            return None
        if state == 'up':
            self.info = info or self.info
        if self.state is None:
            self.state = state  # Whatever it is when Compabot starts is not news.
            return None
        if state == self.state or state == 'starting':
            self.pending, self.seen = None, 0
            return None
        if state != self.pending:
            self.pending, self.seen = state, 0
        self.seen += 1
        if self.seen < CONFIRM:
            return None
        previous, self.state, self.pending, self.seen = self.state, state, None, 0
        return self.announce(previous, state)

    def announce(self, previous, state):
        name = f'**{self.control.name}**'
        now = self.clock()
        if state == 'up':
            text = f'{name} is back online and ready for players{self.players()}.'
            if previous == 'hung':
                text = f'{name} is answering again{self.players()}.'
            return text, None
        if state == 'hung':
            return (f'{name} has stopped answering on query port {self.control.query_port}, but it is still running. '
                    'It may be frozen. If it stays this way, use `/server stop`; if that fails, close it on the PC.'), None
        # Down.
        started, self.started = self.started, None
        if now - self.control.stopped_at < INTENTIONAL_GRACE:
            return None
        crash = latest_crash(self.control.install_root / 'data' / 'Saved' / 'Crashes', (started or now) - 60)
        if crash:
            summary = crash_summary(crash)
            text = f'{name} crashed <t:{int(crash.stat().st_mtime)}:R>.'
            if summary:
                text += f'\n```\n{summary[:800]}\n```'
            text += '\nUse `/server start` to bring it back.'
        else:
            text = (f'{name} went down. No crash report was written, so it may have been '
                    'closed on the PC. Use `/server start` to bring it back.')
        return text, crash

    def players(self):
        if not self.info:
            return ''
        return f' ({self.info.players}/{self.info.max_players} players)'


def install_monitor(bot, control, channel_id, interval=POLL_SECONDS):
    """Start polling once the bot is connected. Returns the running task."""
    monitor = ServerMonitor(control)
    bot.monitor = monitor

    async def loop():
        await bot.wait_until_ready()
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except discord.HTTPException:
                log.error('Server alerts are off: cannot open channel %s', channel_id)
                return
        log.info('Watching %s for server alerts in #%s', control.name, channel)
        while not bot.is_closed():
            try:
                result = monitor.step(*await monitor.observe())
                if result:
                    await post(channel, *result)
            except Exception:
                log.exception('Server monitor check failed')
            await asyncio.sleep(interval)

    return asyncio.create_task(loop(), name='server-monitor')


async def post(channel, text, crash):
    files = []
    if crash:
        packed = await asyncio.to_thread(crash_archive, crash)
        if packed:
            files.append(discord.File(io.BytesIO(packed[1]), filename=packed[0]))
            text += '\nThe crash report is attached.'
        else:
            text += f'\nThe crash report is too large to attach; it is in `{crash}`.'
    try:
        await channel.send(text, files=files)
    except discord.HTTPException:
        log.exception('Could not post server alert')
