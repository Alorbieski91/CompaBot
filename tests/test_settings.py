import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from Compabot import create_bot
from bot.games import PALWORLD
from bot.games.palworld import settings as palworld_settings
from bot.games.palworld.settings import CATALOGUE, PalworldSettings, read_options, split_options, write_options
from bot.servers.commands import install_server_commands
from bot.servers.control import ServerControl, ServerError
from bot.servers.settings import parse, suggestions
from bot.servers.settings_watch import SettingsWatch
from bot.servers.steam_query import Info

ADMIN = 2
INI = ('[/Script/Pal.PalGameWorldSettings]\r\n'
       'OptionSettings=(Difficulty=None,ExpRate=1.000000,DeathPenalty=All,bIsPvP=False,'
       'ServerName="Compa, Palworld",CrossplayPlatforms=(Steam,Xbox,PS5,Mac),AdminPassword="secret",'
       'BaseCampWorkerMaxNum=15)\r\n')
DEFAULTS = ('[/Script/Pal.PalGameWorldSettings]\r\n'
            'OptionSettings=(ExpRate=1.000000,DeathPenalty=Item,bIsPvP=False,BaseCampWorkerMaxNum=15)\r\n')


class OptionLineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.ini = Path(self.folder.name) / 'PalWorldSettings.ini'
        self.ini.write_bytes(INI.encode())

    def tearDown(self):
        self.folder.cleanup()

    def test_quoted_text_and_lists_stay_whole(self):
        pairs = dict(split_options(INI.split('(', 1)[1].rsplit(')', 1)[0]))
        self.assertEqual(pairs['ServerName'], '"Compa, Palworld"')
        self.assertEqual(pairs['CrossplayPlatforms'], '(Steam,Xbox,PS5,Mac)')
        self.assertEqual(pairs['BaseCampWorkerMaxNum'], '15')

    def test_write_changes_only_the_named_keys(self):
        write_options(self.ini, {'ExpRate': '2.500000', 'bHardcore': 'True'})
        text = self.ini.read_bytes().decode()
        self.assertEqual(text, INI.replace('ExpRate=1.000000', 'ExpRate=2.500000')
                         .replace('BaseCampWorkerMaxNum=15)', 'BaseCampWorkerMaxNum=15,bHardcore=True)'))
        self.assertEqual(read_options(self.ini)['bHardcore'], 'True')

    def test_write_keeps_a_byte_order_mark(self):
        self.ini.write_bytes(b'\xef\xbb\xbf' + INI.encode())
        write_options(self.ini, {'ExpRate': '3.000000'})
        self.assertTrue(self.ini.read_bytes().startswith(b'\xef\xbb\xbf[/Script'))
        self.assertEqual(read_options(self.ini)['ExpRate'], '3.000000')

    def test_a_file_without_the_line_is_refused(self):
        self.ini.write_text('[/Script/Pal.PalGameWorldSettings]\n')
        with self.assertRaises(ServerError):
            write_options(self.ini, {'ExpRate': '2.000000'})


class ValueTests(unittest.TestCase):
    def test_parse(self):
        self.assertIs(parse(CATALOGUE['bIsPvP'], 'yes'), True)
        self.assertIs(parse(CATALOGUE['bIsPvP'], 'False'), False)
        self.assertEqual(parse(CATALOGUE['DeathPenalty'], 'itemandequipment'), 'ItemAndEquipment')
        self.assertEqual(parse(CATALOGUE['ExpRate'], '2.5'), 2.5)
        self.assertEqual(parse(CATALOGUE['BaseCampWorkerMaxNum'], '20'), 20)
        for key, text in (('bIsPvP', 'maybe'), ('DeathPenalty', 'Some'), ('ExpRate', '50'), ('ExpRate', 'nan'),
                          ('ExpRate', 'fast'), ('BaseCampWorkerMaxNum', '20.5'), ('BaseCampWorkerMaxNum', '0')):
            with self.assertRaises(ServerError, msg=(key, text)):
                parse(CATALOGUE[key], text)

    def test_suggestions(self):
        self.assertEqual(suggestions(CATALOGUE['bIsPvP'], False, False),
                         [('True', 'True'), ('False (now, default)', 'False')])
        names = [name for name, _ in suggestions(CATALOGUE['DeathPenalty'], 'All', 'Item')]
        self.assertEqual(names[1], 'Item (drop items, keep gear, default)')
        self.assertEqual(suggestions(CATALOGUE['ExpRate'], 1.0, 1.0, '25')[0], ('25 (not allowed: 0.1 to 20)', '25'))
        self.assertEqual(suggestions(CATALOGUE['ExpRate'], 1.0, 1.0, '2.5')[0], ('2.5', '2.5'))
        self.assertIn(('1 (now, default)', '1'), suggestions(CATALOGUE['ExpRate'], 1.0, 1.0))

    def test_secrets_and_ports_are_never_offered(self):
        for key in ('AdminPassword', 'ServerPassword', 'PublicPort', 'RESTAPIEnabled', 'RESTAPIPort', 'RCONEnabled',
                    'ServerName', 'ServerPlayerMaxNum', 'CrossplayPlatforms', 'DenyTechnologyList'):
            self.assertNotIn(key, CATALOGUE)


def palworld_folder(root):
    """A Palworld install in root with scripts, a settings file and its defaults."""
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    for script in PALWORLD.scripts.values():
        (scripts / script).write_text('')
    (scripts / 'config.psd1').write_text(f"@{{\n    InstallRoot = '{root}'\n    ServerName = 'CompaPalworld'\n}}\n")
    config = root / 'server' / 'Pal' / 'Saved' / 'Config' / 'WindowsServer'
    config.mkdir(parents=True)
    (config / 'PalWorldSettings.ini').write_bytes(INI.encode())
    (root / 'server' / 'DefaultPalWorldSettings.ini').write_bytes(DEFAULTS.encode())
    return scripts


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.control = ServerControl([ADMIN], palworld_folder(root), game=PALWORLD)
        self.store = self.control.settings

    def tearDown(self):
        self.folder.cleanup()

    def test_read_and_defaults(self):
        values = self.store.read()
        self.assertEqual((values['ExpRate'], values['DeathPenalty'], values['bIsPvP']), (1.0, 'All', False))
        self.assertIsNone(values['bHardcore'])
        defaults = self.store.defaults()
        self.assertEqual(defaults['DeathPenalty'], 'Item')  # from DefaultPalWorldSettings.ini
        self.assertIs(defaults['bHardcore'], False)  # from the catalogue

    def test_write_backs_up_and_remembers_what_changed(self):
        self.store.write({'ExpRate': 2.0}, 'Andrew')
        self.assertEqual(read_options(self.store.file)['ExpRate'], '2.000000')
        self.assertEqual(len(self.store.saved_copies()), 1)
        self.assertEqual(self.store.saved_copies()[0].read_bytes(), INI.encode())
        self.assertEqual(self.store.applied()['ExpRate']['old'], 1.0)
        self.assertIn('"what": "written"', (self.store.folder / 'history.jsonl').read_text())

    def test_backups_are_capped(self):
        with patch.object(palworld_settings, 'KEEP_BACKUPS', 3):
            for rate in range(2, 7):
                self.store.write({'ExpRate': rate}, 'Andrew')
        self.assertEqual(len(self.store.saved_copies()), 3)

    def test_undo_walks_back(self):
        self.store.write({'ExpRate': 2.0}, 'Andrew')
        self.store.write({'ExpRate': 3.0}, 'Andrew')
        self.store.undo('Andrew')
        self.assertEqual(read_options(self.store.file)['ExpRate'], '2.000000')
        self.store.undo('Andrew')
        self.assertEqual(read_options(self.store.file)['ExpRate'], '1.000000')
        with self.assertRaises(ServerError):
            self.store.undo('Andrew')

    def test_pending_changes(self):
        self.store.add_pending('ExpRate', 2.0, 1.0, 'Andrew')
        self.store.add_pending('bIsPvP', True, False, 'nscript')
        self.store.add_pending('bIsPvP', False, False, 'nscript')  # back to what the file has
        self.assertEqual(list(self.store.pending()['changes']), ['ExpRate'])
        self.store.want_restart()
        self.assertTrue(self.store.pending()['restart'])
        self.assertEqual(self.store.apply_pending(), {'ExpRate': 2.0})
        self.assertEqual(read_options(self.store.file)['ExpRate'], '2.000000')
        self.assertEqual(self.store.pending(), {'changes': {}, 'restart': False})
        self.assertEqual(self.store.applied()['ExpRate']['by'], 'Andrew')

    def test_cancel(self):
        self.store.add_pending('ExpRate', 2.0, 1.0, 'Andrew')
        self.store.add_pending('bIsPvP', True, False, 'Andrew')
        self.assertEqual(self.store.cancel('ExpRate'), ['ExpRate'])
        self.assertEqual(self.store.cancel('ExpRate'), [])
        self.assertEqual(self.store.cancel(), ['bIsPvP'])

    def test_world_option(self):
        self.assertIsNone(self.store.world_option())
        world = self.store.saves / '0' / 'ABC123'
        world.mkdir(parents=True)
        (world / 'WorldOption.sav').write_bytes(b'')
        self.assertEqual(self.store.world_option(), world / 'WorldOption.sav')


class Runner:
    """Answers the status script from self.running and records the other scripts run."""

    def __init__(self):
        self.running = False
        self.ran = []

    async def __call__(self, argv, env=None):
        script = Path(argv[-1]).name
        if script == 'Get-ServerStatus.ps1':
            return 0, json.dumps({'running': self.running, 'started': 1790000000})
        self.ran.append(script)
        self.running = script == 'Start-PalworldServer.ps1'
        return 0, 'Done.'


class SettingsCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.bot = create_bot(server_admins=[ADMIN], scripts_dir=root / 'no-icarus',
                              valheim_scripts_dir=root / 'no-valheim', palworld_scripts_dir=palworld_folder(root))
        self.control = self.bot.servers['palworld']
        self.runner = Runner()
        self.players = Info(0, 4)
        self.control.run = self.runner
        self.control.windows = True
        self.control.query = lambda port: self.players
        self.control.wait_ready = AsyncMock(return_value='ready')
        self.store = self.control.settings
        install_server_commands(self.bot, self.bot.servers)

    async def asyncTearDown(self):
        await self.bot.close()
        self.folder.cleanup()

    def command(self, name):
        return self.bot.tree.get_command('server').get_command('settings').get_command(name)

    def interaction(self, user=ADMIN, **options):
        return SimpleNamespace(user=SimpleNamespace(id=user, display_name='Andrew'),
                               namespace=SimpleNamespace(game=None, **options),
                               response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                               followup=SimpleNamespace(send=AsyncMock()))

    async def run_set(self, setting, value, restart=False):
        interaction = self.interaction()
        await self.command('set').callback(interaction, setting, value, restart)
        return interaction.followup.send.call_args.args[0]

    def file_value(self, key):
        return read_options(self.store.file).get(key)

    async def test_registration(self):
        group = self.bot.tree.get_command('server').get_command('settings')
        self.assertEqual({c.name for c in group.commands}, {'set', 'show', 'pending', 'cancel', 'undo'})
        for command in group.commands:
            self.assertEqual([c.value for c in command._params['game'].choices], ['palworld'])
        self.bot.tree.get_command('server').to_dict(self.bot.tree)

    async def test_stopped_server_gets_the_file_written_now(self):
        text = await self.run_set('ExpRate', '2')
        self.assertEqual(text, '`ExpRate` 1 → 2. Saved to the settings file; it takes effect when **CompaPalworld** starts.')
        self.assertEqual(self.file_value('ExpRate'), '2.000000')
        self.assertEqual(self.runner.ran, [])

    async def test_running_server_with_players_waits(self):
        self.runner.running = True
        self.players = Info(2, 4, ('Andrew', 'nscript'))
        text = await self.run_set('DeathPenalty', 'item')
        self.assertIn('`DeathPenalty` All → Item is waiting.', text)
        self.assertIn('Andrew and nscript are online', text)
        self.assertEqual(self.file_value('DeathPenalty'), 'All')
        self.assertEqual(self.store.pending()['changes']['DeathPenalty']['value'], 'Item')
        self.assertEqual(self.runner.ran, [])

    async def test_restart_with_players_on_waits_for_them_to_leave(self):
        self.runner.running = True
        self.players = Info(1, 4, ('nscript',))
        text = await self.run_set('ExpRate', '3', restart=True)
        self.assertIn('nscript is online, so I will restart **CompaPalworld** once nobody is on.', text)
        self.assertTrue(self.store.pending()['restart'])
        self.assertEqual(self.runner.ran, [])

    async def test_restart_on_an_empty_server_applies_it_before_the_start(self):
        self.runner.running = True
        text = await self.run_set('ExpRate', '3', restart=True)
        self.assertIn('Nobody is on, so restarting', text)
        self.assertEqual(self.runner.ran, ['Stop-PalworldServer.ps1', 'Start-PalworldServer.ps1'])
        self.assertEqual(self.file_value('ExpRate'), '3.000000')
        self.assertEqual(self.store.pending()['changes'], {})

    async def test_bad_values_and_unknown_settings_are_refused(self):
        self.assertIn('0.1 to 20', await self.run_set('ExpRate', '100'))
        self.assertIn('is not a Palworld setting', await self.run_set('AdminPassword', 'hunter2'))
        self.assertEqual(self.file_value('AdminPassword'), '"secret"')

    async def test_world_option_blocks_changes(self):
        world = self.store.saves / '0' / 'ABC'
        world.mkdir(parents=True)
        (world / 'WorldOption.sav').write_bytes(b'')
        self.assertIn('overrides the settings file', await self.run_set('ExpRate', '2'))
        self.assertEqual(self.file_value('ExpRate'), '1.000000')

    async def test_non_admins_are_refused(self):
        interaction = self.interaction(user=99)
        await self.command('set').callback(interaction, 'ExpRate', '2', False)
        self.assertIn('Only the server admins', interaction.response.send_message.call_args.args[0])
        autocomplete = self.command('set')._params['setting'].autocomplete
        self.assertEqual(await autocomplete(self.interaction(user=99), 'exp'), [])

    async def test_autocomplete(self):
        settings = self.command('set')._params['setting'].autocomplete
        found = await settings(self.interaction(), 'exprate')
        self.assertEqual([c.value for c in found], ['ExpRate'])
        self.assertEqual(found[0].name, 'ExpRate · XP gain for players and Pals (now 1)')
        values = self.command('set')._params['value'].autocomplete
        found = await values(self.interaction(setting='DeathPenalty'), '')
        self.assertEqual([c.value for c in found], ['None', 'Item', 'ItemAndEquipment', 'All'])
        self.assertEqual(found[3].name, 'All (drop items, gear and team Pals, now)')

    async def test_show_and_pending(self):
        self.store.write({'ExpRate': 2.0}, 'Andrew')
        self.store.add_pending('bIsPvP', True, False, 'nscript')
        interaction = self.interaction()
        await self.command('show').callback(interaction)
        text = interaction.followup.send.call_args.args[0]
        self.assertIn('`ExpRate` 2 (default 1)', text)
        self.assertIn('`DeathPenalty` All (default Item)', text)
        self.assertIn('`bIsPvP` False → True (set by nscript)', text)
        interaction = self.interaction()
        await self.command('show').callback(interaction, 'bIsPvP')
        text = interaction.followup.send.call_args.args[0]
        self.assertIn('Waiting for the next start: True (set by nscript).', text)

    async def test_undo_only_while_stopped(self):
        self.store.write({'ExpRate': 2.0}, 'Andrew')
        self.runner.running = True
        interaction = self.interaction()
        await self.command('undo').callback(interaction)
        self.assertIn('Stop **CompaPalworld** first', interaction.followup.send.call_args.args[0])
        self.runner.running = False
        await self.command('undo').callback(self.interaction())
        self.assertEqual(self.file_value('ExpRate'), '1.000000')

    async def test_any_start_writes_waiting_changes_first(self):
        self.store.add_pending('ExpRate', 2.0, 1.0, 'Andrew')
        await self.control.run_action('start')
        self.assertEqual(self.file_value('ExpRate'), '2.000000')
        self.assertEqual(self.runner.ran, ['Start-PalworldServer.ps1'])

    async def test_a_start_on_a_running_server_leaves_the_file_alone(self):
        self.runner.running = True
        self.store.add_pending('ExpRate', 2.0, 1.0, 'Andrew')
        await self.control.run_action('start')
        self.assertEqual(self.file_value('ExpRate'), '1.000000')

    async def test_watch_writes_once_the_server_is_off_and_confirms_after_the_start(self):
        notes = []
        watch = SettingsWatch(self.control, AsyncMock(side_effect=notes.append))
        self.store.add_pending('ExpRate', 2.0, 1.0, 'Andrew')
        self.runner.running = True
        await watch.check()  # running, nobody asked for a restart: wait
        self.assertEqual(self.file_value('ExpRate'), '1.000000')
        self.runner.running = False
        await watch.check()
        self.assertEqual(self.file_value('ExpRate'), '2.000000')
        self.runner.running = True
        with patch.object(PalworldSettings, 'live', return_value={'ExpRate': 2.0}):
            await watch.check()
        self.assertEqual(notes, ['**CompaPalworld** settings now in effect:\n`ExpRate` 1 → 2 (set by Andrew)'])
        self.assertEqual(self.store.applied(), {})

    async def test_watch_warns_when_the_server_ignores_a_change(self):
        notes = []
        watch = SettingsWatch(self.control, AsyncMock(side_effect=notes.append))
        self.store.write({'ExpRate': 2.0}, 'Andrew')
        self.runner.running = True
        with patch.object(PalworldSettings, 'live', return_value={'ExpRate': 1.0}):
            await watch.check()
        self.assertIn('did not pick up these settings', notes[0])
        self.assertIn('the server is using 1', notes[0])

    async def test_watch_restarts_once_everyone_has_left(self):
        notes = []
        watch = SettingsWatch(self.control, AsyncMock(side_effect=notes.append))
        self.store.add_pending('ExpRate', 2.0, 1.0, 'Andrew')
        self.store.want_restart()
        self.runner.running = True
        self.players = Info(1, 4, ('nscript',))
        with patch.object(PalworldSettings, 'live', side_effect=OSError):
            await watch.check()
            self.assertEqual(self.runner.ran, [])
            self.players = Info(0, 4)
            await watch.check()
        self.assertEqual(self.runner.ran, ['Stop-PalworldServer.ps1', 'Start-PalworldServer.ps1'])
        self.assertEqual(self.file_value('ExpRate'), '2.000000')
        self.assertIn('Everyone left **CompaPalworld**, so I restarted it to apply `ExpRate`.', notes[0])

    async def test_other_games_have_no_settings(self):
        self.assertIsNone(self.bot.servers['icarus'].settings)
        self.assertIsNone(self.bot.servers['valheim'].settings)
        self.assertEqual(self.bot.install_settings_watches()[0].control, self.control)


if __name__ == '__main__':
    unittest.main()
