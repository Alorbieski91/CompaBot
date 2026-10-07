import datetime
import unittest
from zoneinfo import ZoneInfo

from bot.icarus.active_hours import ActiveHours, clock_time, parse_time
from bot.icarus.server_control import ServerError
from bot.icarus.steam_query import Info

CHICAGO = ZoneInfo('America/Chicago')


def at(*args):
    return datetime.datetime(*args, tzinfo=CHICAGO).timestamp()


class FakeControl:
    def __init__(self, running=False, players=0):
        self.name = 'CompaWorld'
        self.running = running
        self.players = players
        self.busy = None
        self.ran = []
        self.codes = {}

    async def status(self):
        if not self.running:
            return {'running': False}
        return {'running': True, 'started': 1000, 'players': Info(self.players, 2)}

    async def run_action(self, action, window=None):
        if self.busy:
            raise ServerError('busy')
        self.ran.append(action)
        code = self.codes.get(action, 0)
        if code == 0:
            self.running = action == 'start'
        return code, 'Done.'


class ParseTests(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(parse_time('20:00'), datetime.time(20))
        self.assertEqual(parse_time(' 6:30 '), datetime.time(6, 30))
        self.assertIsNone(parse_time('OFF'))
        for bad in ('', '6', '6pm', '24:00', '6:5'):
            with self.assertRaises(ValueError, msg=bad):
                parse_time(bad)

    def test_clock_time(self):
        self.assertEqual(clock_time(datetime.time(20)), '8:00 PM')
        self.assertEqual(clock_time(datetime.time(6)), '6:00 AM')
        self.assertEqual(clock_time(datetime.time(0, 5)), '12:05 AM')
        self.assertEqual(clock_time(datetime.time(12)), '12:00 PM')


class ActiveHoursTests(unittest.IsolatedAsyncioTestCase):
    def make(self, control, now):
        self.now = now
        self.posts = []

        async def notify(text):
            self.posts.append(text)
        return ActiveHours(control, notify, datetime.time(6), datetime.time(20), CHICAGO, clock=lambda: self.now)

    async def test_evening_stops_an_empty_server_once(self):
        control = FakeControl(running=True)
        hours = self.make(control, at(2026, 10, 5, 19, 59))
        await hours.check()
        self.assertEqual(control.ran, [])
        self.now = at(2026, 10, 5, 20, 0, 10)
        await hours.check()
        await hours.check()
        self.assertEqual(control.ran, ['stop'])
        self.assertEqual(self.posts, ['**CompaWorld** was shut down at 8:00 PM because nobody was online. It starts again at 6:00 AM.'])

    async def test_evening_leaves_players_alone(self):
        control = FakeControl(running=True, players=1)
        hours = self.make(control, at(2026, 10, 5, 20, 0))
        await hours.check()
        control.players = 0  # they leave later in the evening: still not stopped until tomorrow
        self.now = at(2026, 10, 5, 20, 30)
        await hours.check()
        self.assertEqual(control.ran, [])
        self.assertEqual(self.posts, [])

    async def test_morning_starts_a_stopped_server(self):
        control = FakeControl()
        hours = self.make(control, at(2026, 10, 6, 6, 0))
        await hours.check()
        self.assertEqual(control.ran, ['start'])
        self.assertEqual(self.posts, ['**CompaWorld** was started at 6:00 AM for the day.'])

    async def test_morning_restarts_an_empty_running_server(self):
        control = FakeControl(running=True)
        hours = self.make(control, at(2026, 10, 6, 6, 0))
        await hours.check()
        self.assertEqual(control.ran, ['stop', 'start'])
        self.assertIn('restarted at 6:00 AM', self.posts[0])

    async def test_morning_leaves_players_alone(self):
        control = FakeControl(running=True, players=2)
        hours = self.make(control, at(2026, 10, 6, 6, 0))
        await hours.check()
        self.assertEqual(control.ran, [])
        self.assertEqual(self.posts, [])

    async def test_waits_while_busy_then_runs_within_the_hour(self):
        control = FakeControl()
        control.busy = 'update'
        hours = self.make(control, at(2026, 10, 6, 6, 0))
        await hours.check()
        self.assertEqual(control.ran, [])
        control.busy = None
        self.now = at(2026, 10, 6, 6, 40)
        await hours.check()
        self.assertEqual(control.ran, ['start'])

    async def test_skips_after_the_grace_hour(self):
        control = FakeControl()
        hours = self.make(control, at(2026, 10, 6, 7, 1))
        await hours.check()
        self.assertEqual(control.ran, [])

    async def test_reports_a_failed_start(self):
        control = FakeControl()
        control.codes['start'] = 1
        hours = self.make(control, at(2026, 10, 6, 6, 0))
        await hours.check()
        self.assertIn('did not start', self.posts[0])
        self.assertIn('exit code 1', self.posts[0])

    async def test_follows_daylight_saving(self):
        # 6:00 AM Central is 11:00 UTC in summer and 12:00 UTC once the clocks go back.
        control = FakeControl()
        hours = self.make(control, datetime.datetime(2026, 12, 1, 11, 0, tzinfo=datetime.timezone.utc).timestamp())
        await hours.check()
        self.assertEqual(control.ran, [])
        self.now = datetime.datetime(2026, 12, 1, 12, 0, tzinfo=datetime.timezone.utc).timestamp()
        await hours.check()
        self.assertEqual(control.ran, ['start'])

    async def test_off_turns_a_rule_off(self):
        control = FakeControl(running=True)

        async def notify(text):
            pass
        hours = ActiveHours(control, notify, datetime.time(6), None, CHICAGO, clock=lambda: at(2026, 10, 5, 20, 0))
        await hours.check()
        self.assertEqual(control.ran, [])


if __name__ == '__main__':
    unittest.main()
