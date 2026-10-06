import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from Compabot import create_bot
from mod_update import ModError
from server_control import MAX_CHOICES, SCRIPTS, ServerControl, install_server_commands, players_online, read_config, run_process
from steam_query import Info

ADMIN = 2
# Trimmed from a real Icarus.log: two players join, then both leave.
PLAYING = ('[1]LogNet: NotifyAcceptingConnection accepted from: 1:17777\n'
           '[1]LogNet: Login request: ?password=x?Name=nscript userId: Steam:UNKNOWN [0x1] platform: Steam\n'
           '[1]LogNet: Join succeeded: nscript\n'
           '[2]LogNet: NotifyAcceptingConnection accepted from: 2:17777\n'
           '[2]LogNet: Join succeeded: Legiterately\n')
LEFT = ('[3]LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: 1:17777, Name: SteamNetConnection_1, Driver: x\n'
        '[4]LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: 2:17777, Name: SteamNetConnection_2, Driver: x\n')
SETTINGS = ('[/Script/Icarus.DedicatedServerSettings]\r\nMaxPlayers=2\r\nLoadProspect=\r\nCreateProspect=\r\n'
            'ResumeProspect=False\r\nLastProspectName=ZAWORLDOO\r\nSaveGameOnExit=True\r\n')


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
        self.bot.server.query = lambda port: None
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

    def add_worlds(self, *names):
        prospects = self.bot.server.prospects_dir
        prospects.mkdir(parents=True, exist_ok=True)
        for name in names:
            (prospects / f'{name}.json').write_text('{}')
            (prospects / f'{name}.json.backup').write_text('{}')

    def write_settings(self, text=SETTINGS, encoding='utf-8'):
        settings = self.bot.server.settings_file
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_bytes(text.encode(encoding))

    def settings(self, encoding='utf-8'):
        return self.bot.server.settings_file.read_bytes().decode(encoding)

    async def test_start_with_world_switches_prospect_then_starts(self):
        self.add_worlds('ELMUNDOFOO', 'ZAWORLDOO')
        self.write_settings(SETTINGS.replace('LoadProspect=', 'LoadProspect=ZAWORLDOO'))
        self.runner.result = (0, '{"running":false}')
        interaction = self.interaction()
        await self.command('start').callback(interaction, False, 'elmundofoo')
        self.assertEqual(self.scripts_run(), ['Get-IcarusStatus.ps1', 'Start-IcarusServer.ps1'])
        self.assertTrue(self.sent(interaction).startswith('Loading **ELMUNDOFOO**.\nStart finished.'))
        expected = (SETTINGS.replace('LastProspectName=ZAWORLDOO', 'LastProspectName=ELMUNDOFOO')
                    .replace('ResumeProspect=False', 'ResumeProspect=True'))
        self.assertEqual(self.settings(), expected)

    async def test_start_without_world_leaves_settings_alone(self):
        self.add_worlds('ELMUNDOFOO')
        self.write_settings()
        await self.command('start').callback(self.interaction(), False, None)
        self.assertEqual(self.scripts_run(), ['Start-IcarusServer.ps1'])
        self.assertEqual(self.settings(), SETTINGS)

    async def test_start_with_unknown_world_does_nothing(self):
        self.add_worlds('ELMUNDOFOO', 'ZAWORLDOO')
        self.write_settings()
        for update in (False, True):
            interaction = self.interaction()
            await self.command('start').callback(interaction, update, 'NEWWORLD')
            self.assertEqual(self.sent(interaction),
                             'There is no saved world called `NEWWORLD`. Saved worlds: `ELMUNDOFOO`, `ZAWORLDOO`.')
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.settings(), SETTINGS)

    async def test_start_with_world_refuses_while_running(self):
        self.add_worlds('ELMUNDOFOO')
        self.write_settings()
        self.runner.result = (0, '{"running":true,"started":1790000000}')
        interaction = self.interaction()
        await self.command('start').callback(interaction, False, 'ELMUNDOFOO')
        self.assertIn('already running. Stop it with `/server stop` to switch worlds.', self.sent(interaction))
        self.assertEqual(self.scripts_run(), ['Get-IcarusStatus.ps1'])
        self.assertEqual(self.settings(), SETTINGS)

    async def test_start_with_update_and_world_switches_after_updating(self):
        self.add_worlds('ELMUNDOFOO')
        self.write_settings()
        self.bot.server.mods.update = lambda: ['up to date']
        self.runner.result = (0, '{"running":false}')
        interaction = self.interaction()
        await self.command('start').callback(interaction, True, 'ELMUNDOFOO')
        self.assertEqual(self.scripts_run(), ['Update-IcarusServer.ps1', 'Get-IcarusStatus.ps1', 'Start-IcarusServer.ps1'])
        self.assertIn('\nLoading **ELMUNDOFOO**.\nStart finished.', self.sent(interaction))
        self.assertIn('LastProspectName=ELMUNDOFOO\r\n', self.settings())

    async def test_switch_keeps_utf16_and_adds_missing_keys(self):
        self.add_worlds('ELMUNDOFOO')
        self.write_settings('[/Script/Icarus.DedicatedServerSettings]\r\nMaxPlayers=2\r\n', 'utf-16')
        self.runner.result = (0, '{"running":false}')
        self.assertEqual(await self.bot.server.choose_prospect('ELMUNDOFOO'), 'ELMUNDOFOO')
        self.assertEqual(self.settings('utf-16'),
                         '[/Script/Icarus.DedicatedServerSettings]\r\nLastProspectName=ELMUNDOFOO\r\n'
                         'ResumeProspect=True\r\nLoadProspect=\r\nCreateProspect=\r\nMaxPlayers=2\r\n')

    async def test_world_autocomplete(self):
        self.add_worlds('ELMUNDOFOO', 'ZAWORLDOO', *(f'W{i:02}' for i in range(30)))
        worlds = self.command('start')._params['world'].autocomplete
        self.assertEqual([c.value for c in await worlds(self.interaction(), 'foo')], ['ELMUNDOFOO'])
        self.assertEqual(len(await worlds(self.interaction(), '')), MAX_CHOICES)
        self.assertEqual(await worlds(self.interaction(user=99), ''), [])

    async def test_each_action_runs_its_script(self):
        self.runner.result = (0, '{"running":false}')  # backup checks the server is off first
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
        self.bot.server.query = lambda port: Info(1, 2) if port == 27015 else None
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
        self.assertIn('Players: 1/2.', text)
        self.assertIn('Last backup: <t:1800000000:R>', text)

    def write_log(self, text, stamp):
        logs = Path(self.folder.name) / 'data' / 'Saved' / 'Logs'
        logs.mkdir(parents=True, exist_ok=True)
        (logs / 'Icarus.log').write_text(text)
        os.utime(logs / 'Icarus.log', (stamp, stamp))

    async def test_status_reads_players_from_the_log_when_the_query_port_is_quiet(self):
        self.runner.result = (0, '{"running":true,"started":1790000000}')
        self.write_log('IpNetDriver listening on port 17777\n' + PLAYING, 1_790_000_100)
        interaction = self.interaction()
        await self.command('status').callback(interaction)
        self.assertIn('Players: 2/2 (Legiterately, nscript).', self.sent(interaction))

    async def test_status_still_loading(self):
        self.runner.result = (0, '{"running":true,"started":1790000000}')
        for text, stamp in (('LogInit: starting\n', 1_790_000_100),  # current log, not listening yet
                            ('IpNetDriver listening on port 17777\n', 1_700_000_000)):  # stale log from an earlier run
            self.write_log(text, stamp)
            interaction = self.interaction()
            await self.command('status').callback(interaction)
            self.assertIn('still loading, not accepting players on port 17777 yet', self.sent(interaction))

    def write_backup(self, age):
        backups = Path(self.folder.name) / 'backups'
        backups.mkdir(exist_ok=True)
        stamp = time.time() - age
        (backups / 'Icarus-1.zip').write_text('')
        os.utime(backups / 'Icarus-1.zip', (stamp, stamp))

    def write_save(self, age):
        prospects = Path(self.folder.name) / 'data' / 'Saved' / 'PlayerData' / 'DedicatedServer' / 'Prospects'
        prospects.mkdir(parents=True, exist_ok=True)
        stamp = time.time() - age
        (prospects / 'ELMUNDOFOO.json').write_text('{}')
        os.utime(prospects / 'ELMUNDOFOO.json', (stamp, stamp))

    def scripts_run(self):
        return [Path(argv[argv.index('-File') + 1]).name for argv, _ in self.runner.calls]

    async def test_auto_backup_runs_when_offline_world_changed(self):
        self.runner.result = (0, '{"running":false}')
        self.write_backup(age=2 * 60 * 60)
        self.write_save(age=60 * 60)  # e.g. the server crashed after the last backup
        await self.bot.auto_backup()
        self.assertEqual(self.scripts_run(), ['Get-IcarusStatus.ps1', 'Get-IcarusStatus.ps1', 'Backup-IcarusServer.ps1'])

    async def test_auto_backup_skips(self):
        running = '{"running":true,"started":1790000000}'
        cases = (('server is running', running, 2 * 60 * 60, 60),
                 ('newest backup is up to date', '{"running":false}', 60, 2 * 60 * 60))
        for reason, status, backup_age, save_age in cases:
            self.runner.calls.clear()
            self.runner.result = (0, status)
            self.write_log(PLAYING, time.time())
            self.write_backup(backup_age)
            self.write_save(save_age)
            self.assertEqual(await self.bot.server.auto_backup(), reason)
            self.assertEqual(self.scripts_run(), ['Get-IcarusStatus.ps1'])

    async def test_auto_backup_skips_without_saves(self):
        self.runner.result = (0, '{"running":false}')
        self.assertEqual(await self.bot.server.auto_backup(), 'newest backup is up to date')

    async def test_auto_backup_waits_for_other_actions(self):
        self.bot.server.busy = 'stop'
        self.assertEqual(await self.bot.server.auto_backup(), 'Stop is running')
        self.assertEqual(self.runner.calls, [])

    async def test_backup_command_refuses_while_running(self):
        self.runner.result = (0, '{"running":true,"started":1790000000}')
        interaction = self.interaction()
        await self.command('backup').callback(interaction)
        self.assertIn("can't be backed up while it has them open", self.sent(interaction))
        self.assertEqual(self.scripts_run(), ['Get-IcarusStatus.ps1'])
        self.assertIsNone(self.bot.server.busy)

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
    def test_players_online(self):
        self.assertEqual(players_online(PLAYING), ['Legiterately', 'nscript'])
        self.assertEqual(players_online(PLAYING + LEFT), [])
        refused = ('[1]LogNet: NotifyAcceptingConnection accepted from: 3:17777\n'
                   '[1]LogNet: Login request: ?Name=stranger userId: Steam:UNKNOWN\n'
                   '[1]LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: 3:17777, Name: SteamNetConnection_3\n')
        self.assertEqual(players_online(PLAYING + refused), ['Legiterately', 'nscript'])

    def test_config_defaults(self):
        self.assertEqual(read_config('/missing/config.psd1'), {})
        control = ServerControl([], '/srv/IcarusServer/scripts')
        self.assertEqual((control.install_root, control.port), (Path('/srv/IcarusServer'), '17777'))

    async def test_run_process_returns_exit_code_and_output(self):
        code, out = await run_process([sys.executable, '-c', 'import os,sys; print(os.environ["X"]); sys.exit(3)'], {'X': 'hi'})
        self.assertEqual((code, out.strip()), (3, 'hi'))


if __name__ == '__main__':
    unittest.main()
