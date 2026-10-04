import asyncio
import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from auto_update import WHILE_PLAYING, AutoUpdater, installed_build, public_build
import patch_notes
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
NEWS = {'appnews': {'newsitems': [
    {'title': 'Week 253 Update', 'date': 1_790_500_000, 'url': 'https://store.steampowered.com/news/253',
     'contents': '[h2]New Content[/h2][list][*]Added Kiwi eggs: tamed Kiwis now lay eggs in their nest[/list]'
                 '[h2]Fixes[/h2][list][*]Fixed a crash when opening the map[*]Changed Iron ore yield from 4 to 6[/list]'},
    {'title': 'Community Spotlight', 'date': 1_790_600_000, 'url': 'https://x', 'contents': 'Art!'},
    {'title': 'Week 252 Update', 'date': 1_789_900_000, 'url': 'https://x', 'contents': '[*]Added old stuff'},
]}}
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
        async def news():
            return NEWS
        self.state = self.root / 'state.json'
        self.updater = AutoUpdater(self.control, notify, every_hours=2, query=query, state_file=self.state, news=news)

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


    def manifest(self, build):
        (self.root / 'server' / 'steamapps' / 'appmanifest_2089300.acf').write_text(f'"buildid"\t\t"{build}"\n')

    async def test_first_run_only_remembers_the_build(self):
        self.manifest(20400000)
        self.use(running=False)
        await self.updater.check()
        self.assertEqual(self.messages, [])
        self.assertEqual(json.loads(self.state.read_text())['build'], 20400000)

    @mock.patch.dict('os.environ', {'ANTHROPIC_API_KEY': ''})
    async def test_posts_gameplay_changes_after_a_new_build(self):
        self.state.write_text(json.dumps({'build': 20300000, 'since': 1_790_000_000}))
        self.manifest(20400000)  # updated by hand since the last check
        self.use(running=False)
        await self.updater.check()
        self.assertEqual(self.messages, [
            '**What changed in Icarus** (server now on build 20400000): '
            '[Week 253 Update](<https://store.steampowered.com/news/253>)\n'
            '- Added Kiwi eggs: tamed Kiwis now lay eggs in their nest\n- Changed Iron ore yield from 4 to 6'])
        self.assertEqual(json.loads(self.state.read_text()), {'build': 20400000, 'since': 1_790_500_000})
        await self.updater.check()
        self.assertEqual(len(self.messages), 1)

    async def test_waits_for_the_notes_to_be_posted(self):
        self.state.write_text(json.dumps({'build': 20300000, 'since': 1_790_700_000}))
        self.manifest(20400000)
        self.use(running=False)
        await self.updater.check()
        self.assertEqual(self.messages, [])
        self.assertEqual(json.loads(self.state.read_text())['build'], 20300000)

    async def test_auto_install_is_followed_by_the_notes(self):
        self.state.write_text(json.dumps({'build': 20300000, 'since': 1_790_000_000}))
        runner = self.use(running=False)
        async def install(argv, env=None):
            if Path(argv[-1]).name == 'Update-IcarusServer.ps1':
                self.manifest(20400000)
            return await runner(argv, env)
        self.control.run = install
        with mock.patch.object(patch_notes, 'ask_claude', mock.AsyncMock(return_value='- Kiwis lay eggs now.')):
            await self.updater.check()
        self.assertIn('Installed Icarus build 20400000', self.messages[0])
        self.assertTrue(self.messages[1].endswith('\n- Kiwis lay eggs now.'))


class PatchNotesTests(unittest.IsolatedAsyncioTestCase):
    def test_update_posts_skip_other_news_and_old_posts(self):
        self.assertEqual([p['title'] for p in patch_notes.update_posts(NEWS, 1_790_000_000)], ['Week 253 Update'])
        self.assertEqual([p['title'] for p in patch_notes.update_posts(NEWS, 0)], ['Week 252 Update', 'Week 253 Update'])

    @mock.patch.dict('os.environ', {'ANTHROPIC_API_KEY': ''})
    async def test_without_a_key_claude_is_not_called(self):
        self.assertIsNone(await patch_notes.ask_claude(NEWS['appnews']['newsitems']))

    @mock.patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'test-key'})
    async def test_claude_summary_is_used(self):
        response = mock.Mock(stop_reason='end_turn', content=[mock.Mock(type='text', text='- Kiwis lay eggs now.')])
        client = mock.MagicMock()
        client.__aenter__.return_value = client
        client.beta.messages.create = mock.AsyncMock(return_value=response)
        with mock.patch('anthropic.AsyncAnthropic', return_value=client):
            text = await patch_notes.describe(patch_notes.update_posts(NEWS, 1_790_000_000), 20400000)
        self.assertTrue(text.endswith('\n- Kiwis lay eggs now.'))
        sent = client.beta.messages.create.call_args.kwargs
        self.assertEqual(sent['model'], 'claude-opus-5-5')
        self.assertIn('Added Kiwi eggs', sent['messages'][0]['content'])
        self.assertNotIn('[*]', sent['messages'][0]['content'])


if __name__ == '__main__':
    unittest.main()
