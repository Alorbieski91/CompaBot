import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from auto_update import WHILE_PLAYING, AutoUpdater, installed_build, public_build
from server_control import SCRIPTS, ServerControl

APP_INFO = '''AppID : 2089300, change number : 31234567
"2089300"
{
	"depots"
	{
		"branches"
		{
			"experimental"
			{
				"buildid"		"20500000"
			}
			"public"
			{
				"buildid"		"20400000"
				"timeupdated"		"1790000000"
			}
		}
	}
}
'''
JOIN = '[1]LogNet: NotifyAcceptingConnection accepted from: {0}:17777\n[1]LogNet: Join succeeded: {1}\n'
LEAVE = '[2]LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: {}:17777, Name: SteamNetConnection_1\n'


class ParsingTests(unittest.TestCase):
    def test_public_build_ignores_other_branches(self):
        self.assertEqual(public_build(APP_INFO), 20400000)
        self.assertIsNone(public_build('Redirecting stderr to steamcmd/logs/stderr.txt\nERROR! Timed out'))

    def test_installed_build(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertIsNone(installed_build(root))
            apps = Path(root) / 'server' / 'steamapps'
            apps.mkdir(parents=True)
            (apps / 'appmanifest_2089300.acf').write_text('"AppState"\n{\n\t"appid"\t\t"2089300"\n\t"buildid"\t\t"20300000"\n}\n')
            self.assertEqual(installed_build(root), 20300000)


class Runner:
    """Stands in for PowerShell: answers the status script and records the others."""
    def __init__(self, running):
        self.running = running
        self.ran = []
        self.codes = {}

    async def __call__(self, argv, env=None):
        script = Path(argv[-1]).name
        if script == 'Get-IcarusStatus.ps1':
            return 0, '{"running":true,"started":1000}' if self.running else '{"running":false}'
        self.ran.append(script)
        return self.codes.get(script, 0), 'Done.'


class AutoUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        scripts = self.root / 'scripts'
        scripts.mkdir()
        for name in SCRIPTS.values():
            (scripts / name).write_text('')
        (scripts / 'config.psd1').write_text(f"@{{\n    InstallRoot = '{self.root}'\n}}\n")
        apps = self.root / 'server' / 'steamapps'
        apps.mkdir(parents=True)
        (apps / 'appmanifest_2089300.acf').write_text('"buildid"\t\t"20300000"\n')
        self.control = ServerControl([], scripts)
        self.control.mods.update = lambda: ['laanp-PetesBeaconTeleport: w252 v1 is the latest release.']
        self.messages = []
        self.queries = []

        async def notify(text):
            self.messages.append(text)

        async def query(steamcmd):
            self.queries.append(steamcmd)
            return APP_INFO
        self.updater = AutoUpdater(self.control, notify, every_hours=2, query=query)

    async def asyncTearDown(self):
        self.folder.cleanup()

    def use(self, running):
        self.control.run = Runner(running)
        return self.control.run

    def write_log(self, text):
        logs = self.root / 'data' / 'Saved' / 'Logs'
        logs.mkdir(parents=True, exist_ok=True)
        (logs / 'Icarus.log').write_text(text)
        os.utime(logs / 'Icarus.log', (2_000, 2_000))

    async def test_up_to_date_does_nothing(self):
        (self.root / 'server' / 'steamapps' / 'appmanifest_2089300.acf').write_text('"buildid"\t\t"20400000"\n')
        runner = self.use(running=False)
        self.assertEqual(await self.updater.check(), 2 * 60 * 60)
        self.assertEqual(self.queries, [self.root / 'steamcmd' / 'steamcmd.exe'])
        self.assertEqual((runner.ran, self.messages), ([], []))

    async def test_updates_a_stopped_server_and_leaves_it_stopped(self):
        runner = self.use(running=False)
        await self.updater.check()
        self.assertEqual(runner.ran, ['Update-IcarusServer.ps1'])
        self.assertIn('Installed Icarus build 20400000 automatically (was 20300000).', self.messages[0])
        self.assertIn('Mods:\nlaanp-PetesBeaconTeleport', self.messages[0])

    async def test_restarts_an_empty_running_server(self):
        self.write_log(JOIN.format(1, 'nscript') + LEAVE.format(1))
        runner = self.use(running=True)
        await self.updater.check()
        self.assertEqual(runner.ran, ['Stop-IcarusServer.ps1', 'Update-IcarusServer.ps1', 'Start-IcarusServer.ps1'])
        self.assertIn('Start finished.', self.messages[0])
        self.assertIsNone(self.control.busy)

    async def test_waits_while_players_are_on(self):
        self.write_log(JOIN.format(1, 'nscript'))
        runner = self.use(running=True)
        self.assertEqual(await self.updater.check(), WHILE_PLAYING)
        self.assertEqual(await self.updater.check(), WHILE_PLAYING)
        self.assertEqual(runner.ran, [])
        self.assertEqual(self.messages, ['An Icarus update is out (build 20300000 to 20400000). '
                                         'I will install it once nscript has left the server.'])
        self.write_log(JOIN.format(1, 'nscript') + LEAVE.format(1))
        await self.updater.check()
        self.assertEqual(runner.ran, ['Stop-IcarusServer.ps1', 'Update-IcarusServer.ps1', 'Start-IcarusServer.ps1'])

    async def test_names_everyone_still_on(self):
        self.write_log(JOIN.format(1, 'nscript') + JOIN.format(2, 'Legiterately'))
        self.use(running=True)
        await self.updater.check()
        self.assertIn('once Legiterately and nscript have left', self.messages[0])

    async def test_failed_stop_leaves_the_server_alone(self):
        self.write_log('')
        runner = self.use(running=True)
        runner.codes['Stop-IcarusServer.ps1'] = 1
        await self.updater.check()
        self.assertEqual(runner.ran, ['Stop-IcarusServer.ps1'])
        self.assertIn('could not stop the server', self.messages[0])

    async def test_failed_update_still_restarts(self):
        self.write_log('')
        runner = self.use(running=True)
        runner.codes['Update-IcarusServer.ps1'] = 1
        await self.updater.check()
        self.assertEqual(runner.ran, ['Stop-IcarusServer.ps1', 'Update-IcarusServer.ps1', 'Start-IcarusServer.ps1'])
        self.assertIn('Tried to install Icarus build 20400000', self.messages[0])
        self.assertIn('Update failed (exit code 1)', self.messages[0])
        self.assertNotIn('Mods', self.messages[0])

    async def test_skips_while_a_command_is_running(self):
        runner = self.use(running=False)
        self.control.busy = 'backup'
        self.assertEqual(await self.updater.check(), WHILE_PLAYING)
        self.assertEqual((self.queries, runner.ran), ([], []))

    async def test_steamcmd_trouble_waits_for_the_next_check(self):
        async def broken(steamcmd):
            raise asyncio.TimeoutError
        self.updater.query = broken
        runner = self.use(running=False)
        self.assertEqual(await self.updater.check(), 2 * 60 * 60)
        self.assertEqual((runner.ran, self.messages), ([], []))


if __name__ == '__main__':
    unittest.main()
