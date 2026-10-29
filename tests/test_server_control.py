import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from Compabot import create_bot
from bot.games import GAMES, ICARUS, PALWORLD, VALHEIM, relative
from bot.games.icarus.mods import ModError
from bot.games.icarus.players import players_online
from bot.games.palworld import api as palworld_api
from bot.games.valheim.players import valheim_players_online
from bot.servers.commands import install_server_commands
from bot.servers.control import MAX_CHOICES, SCRIPTS, ServerControl, ServerError, read_config, run_process
from bot.servers.steam_query import Info
from tests.test_ore_deposits import WORLD, save

ADMIN = 2
# Trimmed from a real Icarus.log: two players join, then both leave.
PLAYING = ('[1]LogNet: NotifyAcceptingConnection accepted from: 1:17777\n'
           '[1]LogNet: Login request: ?password=x?Name=nscript userId: Steam:UNKNOWN [0x1] platform: Steam\n'
           '[1]LogNet: Join succeeded: nscript\n'
           '[2]LogNet: NotifyAcceptingConnection accepted from: 2:17777\n'
           '[2]LogNet: Join succeeded: Legiterately\n')
LEFT = ('[3]LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: 1:17777, Name: SteamNetConnection_1, Driver: x\n'
        '[4]LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: 2:17777, Name: SteamNetConnection_2, Driver: x\n')
# Trimmed from a real valheim_server.log: Shelob joins and leaves, then Bjorn joins.
VALHEIM_LOG = ('10/17/2026 12:00:00: Game server connected\n'
               '10/17/2026 12:28:09: Got connection SteamID 76561198039777309\n'
               '10/17/2026 12:28:09: Got handshake from client 76561198039777309\n'
               '10/17/2026 12:28:30: Got character ZDOID from Shelob : 760666702:1\n'
               '10/17/2026 12:28:56: Closing socket 76561198039777309\n'
               '10/17/2026 13:31:02: Got connection SteamID 76561197996718084\n'
               '10/17/2026 13:31:36: Got character ZDOID from Bjorn : 2666027640:1\n')
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
        # Valheim and Palworld folders that don't exist, so the jobs never touch the real servers.
        self.bot = create_bot(server_admins=[ADMIN], scripts_dir=self.scripts, valheim_scripts_dir=root / 'no-valheim',
                              palworld_scripts_dir=root / 'no-palworld')
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
        self.assertEqual({c.name for c in group.commands}, {'status', 'start', 'stop', 'restart', 'backup', 'update', 'mods', 'ores'})
        group.to_dict(self.bot.tree)

    async def test_game_commands_come_from_the_games_that_have_them(self):
        bot = create_bot(server_admins=[ADMIN], scripts_dir=self.scripts, valheim_scripts_dir=self.scripts)
        install_server_commands(bot, {'valheim': bot.servers['valheim']})
        group = bot.tree.get_command('server')
        self.assertEqual({c.name for c in group.commands}, {'status', 'start', 'stop', 'restart', 'backup', 'update'})
        await bot.close()

    async def test_non_admins_are_refused_before_anything_runs(self):
        for name in ('status', 'stop', 'restart', 'backup', 'update', 'mods'):
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
        prospects = self.bot.server.worlds.prospects_dir
        prospects.mkdir(parents=True, exist_ok=True)
        for name in names:
            (prospects / f'{name}.json').write_text('{}')
            (prospects / f'{name}.json.backup').write_text('{}')

    def write_settings(self, text=SETTINGS, encoding='utf-8'):
        settings = self.bot.server.worlds.settings_file
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_bytes(text.encode(encoding))

    def settings(self, encoding='utf-8'):
        return self.bot.server.worlds.settings_file.read_bytes().decode(encoding)

    async def test_start_with_world_switches_prospect_then_starts(self):
        self.add_worlds('ELMUNDOFOO', 'ZAWORLDOO')
        self.write_settings(SETTINGS.replace('LoadProspect=', 'LoadProspect=ZAWORLDOO'))
        self.runner.result = (0, '{"running":false}')
        interaction = self.interaction()
        await self.command('start').callback(interaction, False, 'elmundofoo')
        self.assertEqual(self.scripts_run(), ['Get-ServerStatus.ps1', 'Start-IcarusServer.ps1'])
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
        self.assertEqual(self.scripts_run(), ['Get-ServerStatus.ps1'])
        self.assertEqual(self.settings(), SETTINGS)

    async def test_start_with_update_and_world_switches_after_updating(self):
        self.add_worlds('ELMUNDOFOO')
        self.write_settings()
        self.bot.server.mods.update = lambda: ['up to date']
        self.runner.result = (0, '{"running":false}')
        interaction = self.interaction()
        await self.command('start').callback(interaction, True, 'ELMUNDOFOO')
        self.assertEqual(self.scripts_run(), ['Update-IcarusServer.ps1', 'Get-ServerStatus.ps1', 'Start-IcarusServer.ps1'])
        self.assertIn('\nLoading **ELMUNDOFOO**.\nStart finished.', self.sent(interaction))
        self.assertIn('LastProspectName=ELMUNDOFOO\r\n', self.settings())

    async def test_switch_keeps_utf16_and_adds_missing_keys(self):
        self.add_worlds('ELMUNDOFOO')
        self.write_settings('[/Script/Icarus.DedicatedServerSettings]\r\nMaxPlayers=2\r\n', 'utf-16')
        self.runner.result = (0, '{"running":false}')
        self.assertEqual(await self.bot.server.worlds.choose('ELMUNDOFOO'), 'ELMUNDOFOO')
        self.assertEqual(self.settings('utf-16'),
                         '[/Script/Icarus.DedicatedServerSettings]\r\nLastProspectName=ELMUNDOFOO\r\n'
                         'ResumeProspect=True\r\nLoadProspect=\r\nCreateProspect=\r\nMaxPlayers=2\r\n')

    async def test_world_autocomplete(self):
        self.add_worlds('ELMUNDOFOO', 'ZAWORLDOO', *(f'W{i:02}' for i in range(30)))
        worlds = self.command('start')._params['world'].autocomplete
        self.assertEqual([c.value for c in await worlds(self.interaction(), 'foo')], ['ELMUNDOFOO'])
        self.assertEqual(len(await worlds(self.interaction(), '')), MAX_CHOICES)
        self.assertEqual(await worlds(self.interaction(user=99), ''), [])

    def add_save(self, name, raw):
        self.add_worlds(name)
        (self.bot.server.worlds.prospects_dir / f'{name}.json').write_bytes(raw)

    def files(self, interaction):
        return {f.filename: f.fp.read() for f in interaction.followup.send.call_args.kwargs['files']}

    async def test_ores_lists_deposits_and_attaches_the_save(self):
        raw = save(WORLD)
        self.add_save('ELMUNDOFOO', raw)
        self.add_worlds('ZAWORLDOO')
        self.write_settings(SETTINGS.replace('ZAWORLDOO', 'ELMUNDOFOO'))
        self.bot.server.status = AsyncMock(side_effect=AssertionError('works while running'))
        interaction = self.interaction()
        await self.command('ores').callback(interaction, None, None)
        self.assertEqual(self.sent(interaction),
                         '**ELMUNDOFOO** (Olympus): 5 deep ore deposits.\nTitanium 3 · Gold 1 · Super Cooled Ice 1\n'
                         'For a map you can filter and zoom, open <https://faysm.github.io/IcarusOreDeposit/> and drop `ELMUNDOFOO.json` on it.')
        files = self.files(interaction)
        self.assertEqual(files['ELMUNDOFOO.json'], raw)
        self.assertIn(b'Titanium (3)', files['ELMUNDOFOO-deep-ores.txt'])
        self.assertEqual(files['ELMUNDOFOO-deep-ores.jpg'][:3], b'\xff\xd8\xff')
        self.assertEqual(list(files), ['ELMUNDOFOO-deep-ores.jpg', 'ELMUNDOFOO-deep-ores.txt', 'ELMUNDOFOO.json'])
        self.assertEqual(self.runner.calls, [])

    async def test_ores_for_one_ore_in_a_named_world(self):
        self.add_save('ELMUNDOFOO', save(WORLD))
        self.add_worlds('ZAWORLDOO')
        interaction = self.interaction()
        await self.command('ores').callback(interaction, 'elmundofoo', 'Titanium')
        self.assertTrue(self.sent(interaction).startswith(
            '**ELMUNDOFOO** (Olympus): 3 Titanium deep ore deposits in A1 ×2, P16.\nFor a map'))

    async def test_ores_falls_back_to_the_backup_save(self):
        self.add_save('ELMUNDOFOO', b'{"half written')
        (self.bot.server.worlds.prospects_dir / 'ELMUNDOFOO.json.backup').write_bytes(save(WORLD))
        interaction = self.interaction()
        await self.command('ores').callback(interaction, None, None)
        self.assertIn('5 deep ore deposits', self.sent(interaction))

    async def test_ores_errors(self):
        self.add_worlds('ELMUNDOFOO', 'ZAWORLDOO')
        interaction = self.interaction()
        await self.command('ores').callback(interaction, None, None)
        self.assertEqual(self.sent(interaction), 'Pick a world. Saved worlds: `ELMUNDOFOO`, `ZAWORLDOO`.')
        await self.command('ores').callback(interaction, 'ELMUNDOFOO', None)
        self.assertIn("Couldn't read the **ELMUNDOFOO** save: it doesn't look like a prospect save", self.sent(interaction))
        interaction = self.interaction(user=99)
        await self.command('ores').callback(interaction, None, None)
        self.assertIn('Only the server admins', interaction.response.send_message.call_args.args[0])

    def upload(self, raw, filename='MYSOLO.json'):
        return SimpleNamespace(filename=filename, size=len(raw), read=AsyncMock(return_value=raw))

    async def test_ores_reads_an_uploaded_save(self):
        interaction = self.interaction()
        upload = self.upload(save(WORLD))
        await self.command('ores').callback(interaction, None, 'Gold', upload)
        self.assertTrue(self.sent(interaction).startswith(
            '**MYSOLO** (Olympus): 1 Gold deep ore deposits in I9.'))
        self.assertEqual(list(self.files(interaction)), ['MYSOLO-deep-ores.jpg', 'MYSOLO-deep-ores.txt'])

    async def test_ores_refuses_bad_uploads(self):
        for upload, message in ((self.upload(b'{}', 'notes.txt'), 'is not a prospect save'),
                                (self.upload(b'{}'), "Couldn't read `MYSOLO.json`: it doesn't look like a prospect save")):
            interaction = self.interaction()
            await self.command('ores').callback(interaction, None, None, upload)
            self.assertIn(message, self.sent(interaction))

    async def test_ore_autocomplete(self):
        ores = self.command('ores')._params['ore'].autocomplete
        self.assertEqual([(c.name, c.value) for c in await ores(self.interaction(), 'ice')],
                         [('Super Cooled Ice', 'Super_Cooled_Ice')])
        self.assertEqual(await ores(self.interaction(user=99), ''), [])

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

    def restart_server(self, *states, loads=('ready',)):
        """Run /server restart with status() returning states in order and wait_ready() returning loads."""
        control = self.bot.server
        control.status = AsyncMock(side_effect=list(states))
        control.wait_ready = AsyncMock(side_effect=list(loads))
        order = []
        runner = self.runner
        async def tracking(argv, env=None):
            order.append(Path(argv[-1]).name)
            self.assertEqual(control.busy, 'stop' if order[-1].startswith('Stop') else 'start')
            return await runner(argv, env)
        control.run = tracking
        interaction = self.interaction()
        return interaction, order, self.command('restart').callback(interaction)

    async def test_stop_refuses_while_someone_is_on_unless_forced(self):
        control = self.bot.server
        control.status = AsyncMock(return_value={'running': True, 'started': 1000, 'players': Info(1, 2, ('nscript',))})
        interaction = self.interaction()
        await self.command('stop').callback(interaction)
        self.assertEqual(self.sent(interaction), 'nscript is online on **CompaWorld**, so I did not stop it. '
                         'Use `/server stop force:True` to stop it anyway.')
        self.assertEqual(self.runner.calls, [])
        self.assertIsNone(control.busy)
        interaction = self.interaction()
        await self.command('stop').callback(interaction, True)
        self.assertEqual(self.scripts_run(), ['Stop-IcarusServer.ps1'])
        self.assertIn('Stop finished.', self.sent(interaction))

    async def test_stop_runs_when_nobody_is_on(self):
        control = self.bot.server
        control.status = AsyncMock(return_value={'running': True, 'started': 1000, 'players': Info(0, 2)})
        interaction = self.interaction()
        await self.command('stop').callback(interaction)
        self.assertEqual(self.scripts_run(), ['Stop-IcarusServer.ps1'])
        self.assertIn('Stop finished.', self.sent(interaction))

    async def test_restart_stops_then_starts_and_waits_until_ready(self):
        running = {'running': True, 'started': 1000, 'players': Info(0, 2)}
        interaction, order, call = self.restart_server(running, {'running': False})
        await call
        self.assertEqual(order, ['Stop-IcarusServer.ps1', 'Start-IcarusServer.ps1'])
        self.bot.server.wait_ready.assert_awaited_once()
        self.assertEqual(self.sent(interaction), '**CompaWorld** restarted and is ready for players.')
        self.assertIsNone(self.bot.server.busy)
        self.assertIsNone(self.bot.server.holder)

    async def test_restart_refuses_while_someone_is_on(self):
        for players, who in ((Info(1, 2, ('nscript',)), 'nscript is online'), (Info(2, 2), '2 players are online')):
            interaction, order, call = self.restart_server({'running': True, 'started': 1000, 'players': players})
            await call
            self.assertEqual(order, [])
            self.assertEqual(self.sent(interaction), f'{who} on **CompaWorld**, so I did not restart it.')
            self.assertIsNone(self.bot.server.busy)

    async def test_restart_does_not_start_when_stop_fails(self):
        self.runner.result = (1, 'The server did not exit within 30 seconds.')
        interaction, order, call = self.restart_server({'running': True, 'started': 1000, 'players': Info(0, 2)})
        await call
        self.assertEqual(order, ['Stop-IcarusServer.ps1'])
        self.assertIn('Stop failed (exit code 1)', self.sent(interaction))
        self.assertIn('The server was not started again.', self.sent(interaction))
        self.assertIsNone(self.bot.server.busy)

    async def test_restart_does_not_start_while_server_still_runs(self):
        running = {'running': True, 'started': 1000, 'players': Info(0, 2)}
        interaction, order, call = self.restart_server(running, running)
        await call
        self.assertEqual(order, ['Stop-IcarusServer.ps1'])
        self.assertIn('still looks like it is running', self.sent(interaction))

    async def test_restart_starts_a_stopped_server_and_retries_a_failed_load(self):
        interaction, order, call = self.restart_server({'running': False}, loads=('closed', 'ready'))
        await call
        self.assertEqual(order, ['Start-IcarusServer.ps1', 'Start-IcarusServer.ps1'])
        self.assertEqual(self.sent(interaction), '**CompaWorld** was not running, so I just started it.\nIt is ready '
                         'for players. It closed while loading the first time, so I started it again.')

    async def test_restart_keeps_other_actions_out_until_it_finishes(self):
        control = self.bot.server
        running = {'running': True, 'started': 1000, 'players': Info(0, 2)}
        ready = asyncio.Event()
        async def wait_ready():
            await ready.wait()
            return 'ready'
        control.status = AsyncMock(side_effect=[running, {'running': False}])
        control.wait_ready = wait_ready
        restart = asyncio.create_task(self.command('restart').callback(self.interaction()))
        for _ in range(20):
            await asyncio.sleep(0)
        self.assertEqual(control.busy, 'restart')
        other = self.interaction()
        await self.command('stop').callback(other)
        self.assertEqual(self.sent(other), 'Restart is still running. Try again when it finishes.')
        ready.set()
        await restart
        self.assertEqual(len(self.runner.calls), 2)
        self.assertIsNone(control.busy)

    async def test_wait_ready(self):
        control = self.bot.server
        loading, ready = {'running': True, 'ready': False}, {'running': True, 'ready': True}
        control.status = AsyncMock(side_effect=[loading, ServerError('busy'), ready])
        self.assertEqual(await control.wait_ready(timeout=5, poll=0), 'ready')
        control.status = AsyncMock(side_effect=[loading, {'running': False}])
        self.assertEqual(await control.wait_ready(timeout=5, poll=0), 'closed')
        control.status = AsyncMock(return_value=loading)
        self.assertEqual(await control.wait_ready(timeout=0, poll=0), 'loading')

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
        self.assertEqual(script.name, 'Get-ServerStatus.ps1')
        self.assertIn("$ProgressPreference = 'SilentlyContinue'", script.read_text())
        self.assertNotIn('Get-NetUDPEndpoint', script.read_text())
        self.assertEqual(env, {'GAME_ROOT': self.folder.name, 'GAME_PROCESS': 'IcarusServer%'})
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
        self.assertEqual(self.scripts_run(), ['Get-ServerStatus.ps1', 'Get-ServerStatus.ps1', 'Backup-IcarusServer.ps1'])

    async def test_auto_backup_covers_each_installed_game(self):
        valheim_scripts = Path(self.folder.name) / 'valheim' / 'scripts'
        valheim_scripts.mkdir(parents=True)
        for name in VALHEIM.scripts.values():
            (valheim_scripts / name).write_text('')
        valheim = ServerControl([ADMIN], valheim_scripts, game=VALHEIM)
        valheim.run = self.runner
        self.bot.servers['valheim'] = valheim
        self.runner.result = (0, '{"running":false}')
        self.write_backup(age=2 * 60 * 60)
        self.write_save(age=60 * 60)
        worlds = valheim.install_root / 'data' / 'worlds_local' / 'MiddleEarf'
        worlds.mkdir(parents=True)
        (worlds / '_main.0.fwl2').write_text('')
        await self.bot.auto_backup()
        self.assertEqual(self.scripts_run(), ['Get-ServerStatus.ps1', 'Get-ServerStatus.ps1', 'Backup-IcarusServer.ps1',
                                              'Get-ServerStatus.ps1', 'Get-ServerStatus.ps1', 'Backup-ValheimServer.ps1'])
        # A Valheim failure does not stop the Icarus backup, and the reverse.
        self.runner.calls.clear()
        valheim.busy = 'update'
        self.write_save(age=0)
        await self.bot.auto_backup()
        self.assertEqual(self.scripts_run(), ['Get-ServerStatus.ps1', 'Get-ServerStatus.ps1', 'Backup-IcarusServer.ps1'])

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
            self.assertEqual(self.scripts_run(), ['Get-ServerStatus.ps1'])

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
        self.assertEqual(self.scripts_run(), ['Get-ServerStatus.ps1'])
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


class GameOptionTests(unittest.IsolatedAsyncioTestCase):
    """The game: option on every /server command, with Icarus, and Valheim and Palworld (which have no extras)."""

    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.runner = FakeRunner()
        self.bot = create_bot(server_admins=[ADMIN], scripts_dir=self.scripts('icarus', ICARUS))
        self.valheim = ServerControl([ADMIN], self.scripts('valheim', VALHEIM), game=VALHEIM)
        self.bot.servers['valheim'] = self.valheim
        self.palworld = ServerControl([ADMIN], self.scripts('palworld', PALWORLD), game=PALWORLD)
        self.bot.servers['palworld'] = self.palworld
        for control in self.bot.servers.values():
            control.run = self.runner
            control.windows = True
            control.query = lambda port: None
        install_server_commands(self.bot, self.bot.servers)

    async def asyncTearDown(self):
        await self.bot.close()
        self.folder.cleanup()

    def scripts(self, name, game):
        root = Path(self.folder.name) / name
        scripts = root / 'scripts'
        scripts.mkdir(parents=True)
        for script in game.scripts.values():
            (scripts / script).write_text('')
        (scripts / 'config.psd1').write_text(f"@{{\n    InstallRoot = '{root}'\n}}\n")
        return scripts

    def command(self, name):
        return self.bot.tree.get_command('server').get_command(name)

    def interaction(self, game=None):
        return SimpleNamespace(user=SimpleNamespace(id=ADMIN), namespace=SimpleNamespace(game=game),
                               response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                               followup=SimpleNamespace(send=AsyncMock()))

    def ran(self):
        return [Path(argv[-1]).name for argv, _ in self.runner.calls]

    async def test_every_command_offers_each_game(self):
        for command in self.bot.tree.get_command('server').commands:
            choices = command._params['game'].choices
            self.assertEqual([(c.name, c.value) for c in choices], [('Icarus', 'icarus'), ('Valheim', 'valheim'), ('Palworld', 'palworld')], command.name)
            self.assertFalse(command._params['game'].required)
        self.bot.tree.get_command('server').to_dict(self.bot.tree)

    async def test_blank_game_is_icarus(self):
        self.runner.result = (0, '{"running":false}')
        await self.command('backup').callback(self.interaction())
        await self.command('backup').callback(self.interaction(), 'Icarus')
        self.assertEqual(self.ran(), ['Get-ServerStatus.ps1', 'Backup-IcarusServer.ps1'] * 2)

    async def test_the_chosen_game_runs_its_own_scripts(self):
        await self.command('stop').callback(self.interaction(), True, 'valheim')
        self.assertEqual(self.ran(), ['Stop-ValheimServer.ps1'])

    async def test_an_unknown_game_is_refused_before_anything_runs(self):
        interaction = self.interaction()
        await self.command('status').callback(interaction, 'minecraft')
        self.assertIn('does not run a `minecraft` server. Games: Icarus, Valheim, Palworld.', interaction.response.send_message.call_args.args[0])
        self.assertTrue(interaction.response.send_message.call_args.kwargs['ephemeral'])
        self.assertEqual(self.runner.calls, [])

    async def test_icarus_extras_are_refused_for_other_games(self):
        for name, args, what in (('mods', (), 'Mod updates'), ('ores', (None, None, None), 'The ore map'),
                                 ('start', (False, 'ELMUNDOFOO'), 'Picking a world')):
            interaction = self.interaction()
            await self.command(name).callback(interaction, *args, 'valheim')
            self.assertEqual(interaction.followup.send.call_args.args[0], f'{what} only works for Icarus, not Valheim.')
        self.assertEqual(self.runner.calls, [])

    async def test_world_autocomplete_follows_the_game(self):
        prospects = self.bot.server.worlds.prospects_dir
        prospects.mkdir(parents=True)
        (prospects / 'ELMUNDOFOO.json').write_text('{}')
        worlds = self.command('start')._params['world'].autocomplete
        self.assertEqual([c.value for c in await worlds(self.interaction(), '')], ['ELMUNDOFOO'])
        self.assertEqual(await worlds(self.interaction('valheim'), ''), [])

    async def test_update_skips_mods_for_a_game_without_them(self):
        interaction = self.interaction()
        await self.command('update').callback(interaction, 'valheim')
        self.assertEqual(self.ran(), ['Update-ValheimServer.ps1'])
        self.assertNotIn('Mods', interaction.followup.send.call_args.args[0])

    async def test_bot_runs_every_game(self):
        bot = create_bot()
        self.assertEqual(list(bot.servers), ['icarus', 'valheim', 'palworld'])
        self.assertIs(bot.server, bot.servers['icarus'])
        self.assertEqual(bot.servers['valheim'].scripts_dir, Path(r'C:\ValheimServer\scripts'))
        self.assertEqual(bot.servers['palworld'].scripts_dir, Path(r'C:\PalworldServer\scripts'))
        await bot.close()

    def palworld_running(self, info):
        self.runner.result = (0, f'{{"running":true,"started":{int(time.time()) - 60}}}')
        self.palworld.query = lambda port: info

    async def test_palworld_status_asks_its_rest_api(self):
        self.palworld_running(Info(1, 4, ('Shelob',)))
        interaction = self.interaction()
        await self.command('status').callback(interaction, 'palworld')
        self.assertEqual(self.runner.calls[0][1], {'GAME_ROOT': str(self.palworld.install_root), 'GAME_PROCESS': 'PalServer%'})
        text = interaction.followup.send.call_args.args[0]
        self.assertIn('ready for players on port 8211', text)
        self.assertIn('Players: 1/4 (Shelob).', text)

    async def test_palworld_is_loading_until_its_api_answers(self):
        self.palworld_running(None)
        interaction = self.interaction()
        await self.command('status').callback(interaction, 'palworld')
        text = interaction.followup.send.call_args.args[0]
        self.assertIn('still loading', text)
        self.assertIn('Players: 0/4.', text)

    async def test_palworld_stop_refused_with_players_on(self):
        self.palworld_running(Info(2, 4, ('Bjorn', 'Shelob')))
        interaction = self.interaction()
        await self.command('stop').callback(interaction, False, 'palworld')
        self.assertEqual(interaction.followup.send.call_args.args[0],
                         'Bjorn and Shelob are online on **Palworld server**, so I did not stop it. '
                         'Use `/server stop force:True game:palworld` to stop it anyway.')
        self.assertEqual(self.ran(), ['Get-ServerStatus.ps1'])

    async def test_palworld_runs_its_own_scripts(self):
        self.runner.result = (0, '{"running":false}')
        for name, args in (('backup', ()), ('update', ()), ('stop', (True,))):
            await self.command(name).callback(self.interaction(), *args, 'palworld')
        self.assertEqual(self.ran(), ['Get-ServerStatus.ps1', 'Backup-PalworldServer.ps1', 'Update-PalworldServer.ps1',
                                      'Stop-PalworldServer.ps1'])

    def valheim_running(self, log_text):
        started = int(time.time()) - 60
        self.runner.result = (0, f'{{"running":true,"started":{started}}}')
        log_file = self.valheim.install_root / 'logs' / 'valheim_server.log'
        log_file.parent.mkdir(parents=True)
        log_file.write_text(log_text)

    async def test_valheim_status_reads_its_own_log(self):
        self.valheim_running(VALHEIM_LOG)
        interaction = self.interaction()
        await self.command('status').callback(interaction, 'valheim')
        status_env = self.runner.calls[0][1]
        self.assertEqual(status_env, {'GAME_ROOT': str(self.valheim.install_root), 'GAME_PROCESS': 'valheim_server%'})
        text = interaction.followup.send.call_args.args[0]
        self.assertIn('ready for players on port 2456', text)
        self.assertIn('Players: 1/10 (Bjorn).', text)

    async def test_valheim_status_while_still_loading(self):
        self.valheim_running('10/17/2026 11:59:53: Steam game server initialized\n')
        interaction = self.interaction()
        await self.command('status').callback(interaction, 'valheim')
        self.assertIn('still loading', interaction.followup.send.call_args.args[0])

    async def test_valheim_stop_refusal_names_the_game(self):
        self.valheim_running(VALHEIM_LOG)
        interaction = self.interaction()
        await self.command('stop').callback(interaction, False, 'valheim')
        self.assertEqual(interaction.followup.send.call_args.args[0],
                         'Bjorn is online on **Valheim server**, so I did not stop it. '
                         'Use `/server stop force:True game:valheim` to stop it anyway.')
        self.assertEqual(self.ran(), ['Get-ServerStatus.ps1'])

    async def test_valheim_backup_refused_while_running(self):
        self.valheim_running(VALHEIM_LOG)
        interaction = self.interaction()
        await self.command('backup').callback(interaction, 'valheim')
        self.assertIn('`/server stop game:valheim` backs it up', interaction.followup.send.call_args.args[0])
        self.assertEqual(self.ran(), ['Get-ServerStatus.ps1'])

    async def test_valheim_restart_waits_for_its_ready_line(self):
        self.valheim_running(VALHEIM_LOG.replace('Got connection', 'x').replace('Got character', 'x'))
        statuses = iter([True, False, True])

        async def runner(argv, env=None):
            self.runner.calls.append((argv, env))
            if Path(argv[-1]).name == 'Get-ServerStatus.ps1':
                if next(statuses, True):
                    return 0, f'{{"running":true,"started":{int(time.time()) - 60}}}'
                return 0, '{"running":false}'
            return 0, 'Done.'
        self.valheim.run = runner
        self.valheim.wait_ready = lambda: ServerControl.wait_ready(self.valheim, poll=0)
        interaction = self.interaction()
        await self.command('restart').callback(interaction, 'valheim')
        self.assertEqual(interaction.followup.send.call_args.args[0], '**Valheim server** restarted and is ready for players.')
        self.assertEqual(self.ran(), ['Get-ServerStatus.ps1', 'Stop-ValheimServer.ps1', 'Get-ServerStatus.ps1',
                                      'Start-ValheimServer.ps1', 'Get-ServerStatus.ps1'])


class ProfileTests(unittest.TestCase):
    def test_icarus_profile_keeps_the_old_defaults(self):
        control = ServerControl([], Path(tempfile.gettempdir()) / 'no-such-scripts')
        self.assertIs(control.game, GAMES['icarus'])
        self.assertEqual((control.name, control.port, control.query_port, control.max_players),
                         ('Icarus server', '17777', 27015, 2))
        self.assertEqual(SCRIPTS['start'], 'Start-IcarusServer.ps1')
        self.assertTrue(ICARUS.status_script.is_file())
        self.assertEqual(control.crashes_dir, control.install_root / 'data' / 'Saved' / 'Crashes')
        self.assertEqual(control.worlds.prospects_dir,
                         control.install_root / 'data' / 'Saved' / 'PlayerData' / 'DedicatedServer' / 'Prospects')

    def test_valheim_profile_defaults_and_config(self):
        control = ServerControl([], Path(tempfile.gettempdir()) / 'no-such-scripts', game=VALHEIM)
        self.assertEqual((control.name, control.port, control.query_port, control.max_players),
                         ('Valheim server', '2456', 2457, 10))
        self.assertIsNone(control.crashes_dir)
        self.assertIsNone(control.mods)
        self.assertIsNone(control.worlds)
        self.assertEqual(control.slash('start'), '`/server start game:valheim`')
        with tempfile.TemporaryDirectory() as folder:
            # The real config.psd1 on the server PC, with the password left out.
            (Path(folder) / 'config.psd1').write_text(
                "@{\n    InstallRoot = 'C:\\ValheimServer'\n    ServerName = 'CompaValheim'\n    WorldName = 'MiddleEarf'\n"
                "    GamePort = 2456\n    Public = $false\n    BackupRetentionDays = 14\n}\n")
            control = ServerControl([], folder, game=VALHEIM)
        self.assertEqual((str(control.install_root), control.name, control.port), ('C:\\ValheimServer', 'CompaValheim', '2456'))

    def test_palworld_profile_defaults_and_config(self):
        control = ServerControl([], Path(tempfile.gettempdir()) / 'no-such-scripts', game=PALWORLD)
        self.assertEqual((control.name, control.port, control.query_port, control.max_players),
                         ('Palworld server', '8211', 27016, 4))
        self.assertEqual(control.crashes_dir, control.install_root / 'server' / 'Pal' / 'Saved' / 'Crashes')
        self.assertEqual(control.current_log(0), '')
        self.assertIsNone(control.mods)
        self.assertIsNone(control.worlds)
        with tempfile.TemporaryDirectory() as folder:
            # The real config.psd1 on the server PC, with made-up passwords.
            (Path(folder) / 'config.psd1').write_text(
                "@{\n    InstallRoot = 'C:\\PalworldServer'\n    ServerName = 'CompaPalworld'\n"
                "    Password = 'joinme'\n    AdminPassword = 'admin-secret'\n"
                "    GamePort = 8211        # UDP, forward this on the router\n    QueryPort = 27016\n"
                "    RestApiPort = 8212     # TCP, local only\n    MaxPlayers = 4\n}\n")
            control = ServerControl([], folder, game=PALWORLD)
        self.assertEqual((str(control.install_root), control.name, control.port, control.query_port),
                         ('C:\\PalworldServer', 'CompaPalworld', '8211', 27016))
        self.assertEqual((control.config['AdminPassword'], control.config['RestApiPort']), ('admin-secret', '8212'))

    def test_relative_splits_backslashes(self):
        self.assertEqual(relative('root', r'data\Saved\Logs\Icarus.log'), Path('root', 'data', 'Saved', 'Logs', 'Icarus.log'))


class HelperTests(unittest.IsolatedAsyncioTestCase):
    def test_players_online(self):
        self.assertEqual(players_online(PLAYING), ['Legiterately', 'nscript'])
        self.assertEqual(players_online(PLAYING + LEFT), [])
        refused = ('[1]LogNet: NotifyAcceptingConnection accepted from: 3:17777\n'
                   '[1]LogNet: Login request: ?Name=stranger userId: Steam:UNKNOWN\n'
                   '[1]LogNet: UNetConnection::Close: [UNetConnection] RemoteAddr: 3:17777, Name: SteamNetConnection_3\n')
        self.assertEqual(players_online(PLAYING + refused), ['Legiterately', 'nscript'])

    def test_valheim_players_online(self):
        self.assertEqual(valheim_players_online(VALHEIM_LOG), {'76561197996718084': 'Bjorn'})
        died = '10/17/2026 13:40:00: Got character ZDOID from Bjorn : 0:0\n'
        joining = '10/17/2026 13:41:00: Got connection SteamID 76561198039777309\n'
        self.assertEqual(valheim_players_online(VALHEIM_LOG + died + joining),
                         {'76561197996718084': 'Bjorn', '76561198039777309': None})
        self.assertEqual(valheim_players_online(VALHEIM_LOG + '10/17/2026 13:39:47: Closing socket 76561197996718084\n'), {})

    def test_palworld_players_from_the_rest_api(self):
        control = SimpleNamespace(max_players=4)
        answer = {'players': [{'name': 'Shelob', 'level': 12}, {'name': 'Bjorn', 'level': 3}]}
        self.assertEqual(palworld_api.ask(control, lambda c, path: answer), Info(2, 4, ('Bjorn', 'Shelob')))
        self.assertEqual(palworld_api.ask(control, lambda c, path: {'players': []}), Info(0, 4))
        # A player whose name hasn't loaded yet still counts, but then no names are listed.
        self.assertEqual(palworld_api.ask(control, lambda c, path: {'players': [{'name': ''}, {'name': 'Bjorn'}]}), Info(2, 4))

        def refused(c, path):
            raise ConnectionRefusedError
        self.assertIsNone(palworld_api.ask(control, refused))
        self.assertIsNone(palworld_api.ask(control, lambda c, path: {'error': 'Unauthorized'}))

    def test_palworld_rest_request_signs_in_as_admin(self):
        control = SimpleNamespace(config={'AdminPassword': 'admin-secret', 'RestApiPort': '8212'})

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self, *args):
                return b'{"players": []}'
        with patch('bot.games.palworld.api.urllib.request.urlopen', return_value=Response()) as urlopen:
            self.assertEqual(palworld_api.request(control, 'players'), {'players': []})
        req = urlopen.call_args.args[0]
        self.assertEqual(req.full_url, 'http://127.0.0.1:8212/v1/api/players')
        self.assertEqual(req.get_header('Authorization'), 'Basic YWRtaW46YWRtaW4tc2VjcmV0')  # admin:admin-secret

    def test_config_defaults(self):
        self.assertEqual(read_config('/missing/config.psd1'), {})
        control = ServerControl([], '/srv/IcarusServer/scripts')
        self.assertEqual((control.install_root, control.port), (Path('/srv/IcarusServer'), '17777'))

    async def test_run_process_returns_exit_code_and_output(self):
        code, out = await run_process([sys.executable, '-c', 'import os,sys; print(os.environ["X"]); sys.exit(3)'], {'X': 'hi'})
        self.assertEqual((code, out.strip()), (3, 'hi'))


if __name__ == '__main__':
    unittest.main()
