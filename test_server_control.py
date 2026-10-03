import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from Compabot import create_bot
from mod_update import ModError
from server_control import SCRIPTS, ServerControl, install_server_commands, read_config, run_process

ADMIN = 2


class FakeRunner:
    def __init__(self, code=0, output='Done.'):
        self.calls = []
        self.result = (code, output)
        self.gate = None

    async def __call__(self, argv, env=None):
        self.calls.append((argv, env))
        if self.gate:
            await self.gate.wait()
        return self.result


class ServerCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.scripts = root / 'scripts'
        self.scripts.mkdir()
        for name in SCRIPTS.values():
            (self.scripts / name).write_text('')
        (self.scripts / 'config.psd1').write_text(
            f"@{{\n    InstallRoot = '{root}'\n    ServerName = 'CompaWorld'\n    GamePort = 17777\n}}\n")
        self.runner = FakeRunner()
        self.bot = create_bot(server_admins=[ADMIN], scripts_dir=self.scripts)
        self.bot.server.run = self.runner
        self.bot.server.windows = True
        install_server_commands(self.bot, self.bot.server)

    async def asyncTearDown(self):
        await self.bot.close()
        self.folder.cleanup()

    def command(self, name):
        return self.bot.tree.get_command('server').get_command(name)

    def interaction(self, user=ADMIN):
        return SimpleNamespace(user=SimpleNamespace(id=user),
                               response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                               followup=SimpleNamespace(send=AsyncMock()))

    def sent(self, interaction):
        return interaction.followup.send.call_args.args[0]

    async def test_registration(self):
        group = self.bot.tree.get_command('server')
        self.assertEqual({c.name for c in group.commands}, {'status', 'start', 'stop', 'backup', 'update', 'mods'})
        group.to_dict(self.bot.tree)

    async def test_non_admins_are_refused_before_anything_runs(self):
        for name in ('status', 'stop', 'backup', 'update', 'mods'):
            interaction = self.interaction(user=99)
            await self.command(name).callback(interaction)
            self.assertIn('Only the server admins', interaction.response.send_message.call_args.args[0])
            self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
        self.assertEqual(self.runner.calls, [])

    async def test_refuses_off_windows(self):
        self.bot.server.windows = False
        interaction = self.interaction()
        await self.command('backup').callback(interaction)
        self.assertIn('Windows server host', interaction.response.send_message.call_args.args[0])
        self.assertEqual(self.runner.calls, [])

    async def test_start_runs_script(self):
        interaction = self.interaction()
        await self.command('start').callback(interaction, False)
        argv, _ = self.runner.calls[0]
        self.assertEqual(argv[0], 'powershell.exe')
        self.assertEqual(argv[-2:], ['-File', str(self.scripts / 'Start-IcarusServer.ps1')])
        interaction.response.defer.assert_awaited_once()
        self.assertIn('Start finished.', self.sent(interaction))
        self.assertIn('Done.', self.sent(interaction))

    async def test_start_with_update_updates_game_then_mods_then_starts(self):
        order = []
        self.bot.server.mods.update = lambda: order.append('mods') or ['laanp-PetesBeaconTeleport: updated w251 v1 to w252 v1.']
        runner = self.runner
        async def tracking(argv, env=None):
            order.append(Path(argv[-1]).name)
            return await runner(argv, env)
        self.bot.server.run = tracking
        interaction = self.interaction()
        await self.command('start').callback(interaction, True)
        self.assertEqual(order, ['Update-IcarusServer.ps1', 'mods', 'Start-IcarusServer.ps1'])
        self.assertEqual(self.sent(interaction),
                         'Update finished.\n```\nDone.\n```\nMods:\nlaanp-PetesBeaconTeleport: updated w251 v1 to w252 v1.'
                         '\nStart finished.\n```\nDone.\n```')
        self.assertIsNone(self.bot.server.busy)

    async def test_start_with_failed_update_does_not_start(self):
        self.runner.result = (1, 'Stop the Icarus server before updating it.')
        interaction = self.interaction()
        await self.command('start').callback(interaction, True)
        self.assertEqual(len(self.runner.calls), 1)
        self.assertIn('Update failed (exit code 1)', self.sent(interaction))
        self.assertIn('The server was not started.', self.sent(interaction))

    async def test_each_action_runs_its_script(self):
        for name in ('stop', 'backup', 'update'):
            await self.command(name).callback(self.interaction())
            self.assertEqual(self.runner.calls[-1][0][-1], str(self.scripts / SCRIPTS[name]))

    async def test_failure_reports_exit_code_and_output(self):
        self.runner.result = (1, 'Stop the Icarus server before updating it.\n``` injected')
        interaction = self.interaction()
        await self.command('update').callback(interaction)
        text = self.sent(interaction)
        self.assertIn('Update failed (exit code 1)', text)
        self.assertIn('Stop the Icarus server before updating it.', text)
        self.assertEqual(text.count('```'), 2)

    async def test_update_then_updates_mods(self):
        self.bot.server.mods.update = lambda: ['laanp-PetesBeaconTeleport: updated w251 v1 to w252 v1.']
        interaction = self.interaction()
        await self.command('update').callback(interaction)
        self.assertEqual(self.sent(interaction),
                         'Update finished.\n```\nDone.\n```\nMods:\nlaanp-PetesBeaconTeleport: updated w251 v1 to w252 v1.')
        self.assertIsNone(self.bot.server.busy)

    async def test_failed_update_leaves_mods_alone(self):
        self.bot.server.mods.update = AsyncMock(side_effect=AssertionError('should not run'))
        self.runner.result = (1, 'Stop the Icarus server before updating it.')
        interaction = self.interaction()
        await self.command('update').callback(interaction)
        self.assertNotIn('Mods', self.sent(interaction))

    async def test_mod_update_error_is_reported(self):
        def boom():
            raise ModError('Could not read the mod list from GitHub: timed out')
        self.bot.server.mods.update = boom
        interaction = self.interaction()
        await self.command('update').callback(interaction)
        self.assertIn('Mods:\nMod update failed: Could not read the mod list', self.sent(interaction))

    async def test_mods_command_needs_server_stopped(self):
        self.bot.server.mods.update = lambda: ['laanp-PetesBeaconTeleport: w252 v1 is the latest release.']
        self.runner.result = (0, '{"running":true,"started":1790000000}')
        interaction = self.interaction()
        await self.command('mods').callback(interaction)
        self.assertEqual(self.sent(interaction), 'Stop the Icarus server before updating its mods.')
        self.runner.result = (0, '{"running":false}')
        interaction = self.interaction()
        await self.command('mods').callback(interaction)
        self.assertEqual(self.sent(interaction), 'laanp-PetesBeaconTeleport: w252 v1 is the latest release.')

    async def test_one_action_at_a_time(self):
        self.runner.gate = asyncio.Event()
        first = asyncio.create_task(self.command('update').callback(self.interaction()))
        await asyncio.sleep(0)
        second = self.interaction()
        await self.command('start').callback(second, False)
        self.assertIn('Update is still running', self.sent(second))
        self.runner.gate.set()
        await first
        self.assertIsNone(self.bot.server.busy)
        self.assertEqual(len(self.runner.calls), 1)

    async def test_slow_action_keeps_running_after_reply(self):
        self.runner.gate = asyncio.Event()
        self.bot.server.reply_window = 0.01
        interaction = self.interaction()
        await self.command('update').callback(interaction)
        self.assertIn('still running on the host', self.sent(interaction))
        self.assertEqual(self.bot.server.busy, 'update')
        self.runner.gate.set()
        await asyncio.sleep(0.01)
        self.assertIsNone(self.bot.server.busy)

    async def test_missing_script(self):
        (self.scripts / 'Backup-IcarusServer.ps1').unlink()
        interaction = self.interaction()
        await self.command('backup').callback(interaction)
        self.assertIn('Script not found', self.sent(interaction))
        self.assertEqual(self.runner.calls, [])

    async def test_status_online_with_latest_backup(self):
        backups = Path(self.folder.name) / 'backups'
        backups.mkdir()
        for name, stamp in (('Icarus-1.zip', 1_700_000_000), ('Icarus-2.zip', 1_800_000_000)):
            (backups / name).write_text('')
            os.utime(backups / name, (stamp, stamp))
        self.write_log('LogNet: GameNetDriver SteamNetDriver_1 IpNetDriver listening on port 17777\n', 1_790_000_100)
        self.runner.result = (0, '#< CLIXML\r\n{"running":true,"started":1790000000}\r\n'
                                 '<Objs Version="1.1.0.1"><Obj S="progress" RefId="0"></Obj></Objs>\r\n')
        interaction = self.interaction()
        await self.command('status').callback(interaction)
        argv, env = self.runner.calls[0]
        script = Path(argv[argv.index('-File') + 1])
        self.assertEqual(script.name, 'Get-IcarusStatus.ps1')
        self.assertIn("$ProgressPreference = 'SilentlyContinue'", script.read_text())
        self.assertNotIn('Get-NetUDPEndpoint', script.read_text())
        self.assertEqual(env, {'ICARUS_ROOT': self.folder.name})
        text = self.sent(interaction)
        self.assertIn('**CompaWorld** is online since <t:1790000000:R> (ready for players on port 17777)', text)
        self.assertIn('Last backup: <t:1800000000:R>', text)

    def write_log(self, text, stamp):
        logs = Path(self.folder.name) / 'data' / 'Saved' / 'Logs'
        logs.mkdir(parents=True, exist_ok=True)
        (logs / 'Icarus.log').write_text(text)
        os.utime(logs / 'Icarus.log', (stamp, stamp))

    async def test_status_still_loading(self):
        self.runner.result = (0, '{"running":true,"started":1790000000}')
        for text, stamp in (('LogInit: starting\n', 1_790_000_100),  # current log, not listening yet
                            ('IpNetDriver listening on port 17777\n', 1_700_000_000)):  # stale log from an earlier run
            self.write_log(text, stamp)
            interaction = self.interaction()
            await self.command('status').callback(interaction)
            self.assertIn('still loading, not accepting players on port 17777 yet', self.sent(interaction))

    async def test_status_offline_without_backups(self):
        self.runner.result = (0, '{"running":false}')
        interaction = self.interaction()
        await self.command('status').callback(interaction)
        self.assertEqual(self.sent(interaction), '**CompaWorld** is offline.\nNo backups found.')

    async def test_status_without_json(self):
        self.runner.result = (0, '#< CLIXML\r\n<Objs Version="1.1.0.1"><Obj S="progress"></Obj></Objs>\r\nWARNING: odd')
        interaction = self.interaction()
        await self.command('status').callback(interaction)
        text = self.sent(interaction)
        self.assertIn('Status check returned something unexpected', text)
        self.assertIn('WARNING: odd', text)
        self.assertNotIn('CLIXML', text)

    async def test_status_failure(self):
        self.runner.result = (1, 'Access denied')
        interaction = self.interaction()
        await self.command('status').callback(interaction)
        self.assertIn('Status check failed', self.sent(interaction))


class HelperTests(unittest.IsolatedAsyncioTestCase):
    def test_config_defaults(self):
        self.assertEqual(read_config('/missing/config.psd1'), {})
        control = ServerControl([], '/srv/IcarusServer/scripts')
        self.assertEqual((control.install_root, control.port), (Path('/srv/IcarusServer'), '17777'))

    async def test_run_process_returns_exit_code_and_output(self):
        code, out = await run_process([sys.executable, '-c', 'import os,sys; print(os.environ["X"]); sys.exit(3)'], {'X': 'hi'})
        self.assertEqual((code, out.strip()), (3, 'hi'))


if __name__ == '__main__':
    unittest.main()
