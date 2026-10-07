"""Keep the Icarus server up during active hours: close it at night if it is empty, start it each morning.

At the stop time (default 8:00 PM Central) the server is stopped if nobody is on; if someone is,
it is left running. At the start time (default 6:00 AM) a stopped server is started, and one
still running with nobody on (started by hand overnight, or left on at night for a player) is
restarted to refresh it. A server with players on in the morning is left alone.
"""
import asyncio
import datetime
import logging
import time

from .server_control import ServerError, report

log = logging.getLogger('compabot.schedule')

POLL_SECONDS = 30
# A scheduled action that cannot run on time (another /server action is running, or the bot was
# restarting) is retried until this long after its time, then skipped until the next day.
GRACE = datetime.timedelta(hours=1)
# Stop and start can each take a while; there is no Discord reply window to fit in here.
SCRIPT_WINDOW = 2 * 60 * 60


def parse_time(text):
    """'20:00' -> time(20, 0), or None for 'off'."""
    text = text.strip().lower()
    if text == 'off':
        return None
    hours, sep, minutes = text.partition(':')
    if not (sep and hours.isdecimal() and minutes.isdecimal() and len(minutes) == 2):
        raise ValueError(text)
    return datetime.time(int(hours), int(minutes))


def clock_time(t):
    """time(20, 0) -> '8:00 PM'."""
    return f'{t.hour % 12 or 12}:{t.minute:02d} {"AM" if t.hour < 12 else "PM"}'


class ActiveHours:
    def __init__(self, control, notify, start, stop, zone, clock=time.time):
        self.control = control
        self.notify = notify
        self.events = {name: at for name, at in (('stop', stop), ('start', start)) if at}
        self.zone = zone
        self.clock = clock
        self.done = {}  # event -> the local date it last ran

    def due(self):
        """The events whose time has come today and which have not run yet."""
        now = datetime.datetime.fromtimestamp(self.clock(), self.zone)
        due = []
        for name, at in self.events.items():
            scheduled = datetime.datetime.combine(now.date(), at, self.zone)
            if self.done.get(name) != now.date() and scheduled <= now < scheduled + GRACE:
                due.append((name, now.date()))
        return due

    async def run(self, interval=POLL_SECONDS):
        while True:
            try:
                await self.check()
            except Exception:
                log.exception('Scheduled start/stop check failed')
            await asyncio.sleep(interval)

    async def check(self):
        for name, day in self.due():
            if self.control.busy:
                log.info('Scheduled %s waiting: another server action is running', name)
                continue
            try:
                state = await self.control.status()
                text = await (self.evening if name == 'stop' else self.morning)(state)
            except ServerError as error:
                log.warning('Scheduled %s will retry: %s', name, error)
                continue
            self.done[name] = day
            if text:
                await self.notify(text)

    async def evening(self, state):
        name, at = f'**{self.control.name}**', clock_time(self.events['stop'])
        if not state['running']:
            log.info('Scheduled stop: the server is already off')
            return None
        players = state['players']
        if players.players:
            log.info('Scheduled stop skipped: %s player(s) online', players.players)
            return None
        code, output = await self.control.run_action('stop', window=SCRIPT_WINDOW)
        if code != 0:
            return f'{name} was due to shut down at {at} with nobody online, but stopping it did not work.\n' + report('Stop', code, output)
        morning = self.events.get('start')
        again = f' It starts again at {clock_time(morning)}.' if morning else ''
        return f'{name} was shut down at {at} because nobody was online.{again}'

    async def morning(self, state):
        name, at = f'**{self.control.name}**', clock_time(self.events['start'])
        if not state['running']:
            code, output = await self.control.run_action('start', window=SCRIPT_WINDOW)
            if code != 0:
                return f'{name} was due to start at {at}, but it did not start.\n' + report('Start', code, output)
            return f'{name} was started at {at} for the day.'
        players = state['players']
        if players.players:
            log.info('Scheduled restart skipped: %s player(s) online', players.players)
            return None
        code, output = await self.control.run_action('stop', window=SCRIPT_WINDOW)
        if code != 0:
            return f'{name} was due to restart at {at}, but stopping it did not work.\n' + report('Stop', code, output)
        code, output = await self.control.run_action('start', window=SCRIPT_WINDOW)
        if code != 0:
            return f'{name} was stopped for its {at} restart, but it did not start again.\n' + report('Start', code, output)
        return f'{name} was restarted at {at} to refresh it, since it had been running overnight with nobody online.'
