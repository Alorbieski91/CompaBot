import io
import json
import tempfile
import unittest
from pathlib import Path

from bot.icarus.mod_update import MODINFO_URL, PAK_MAGIC, ModError, ModUpdater, game_version

BASE = 'https://github.com/laanp/Icarus_Mods_Separated'
README = BASE + '/raw/main/laanp-PetesBeaconTeleport_Readme.md'
PAK = PAK_MAGIC.join([b'x' * 2000, b'y' * 200])


def pak_url(week, fix=1):
    return f'{BASE}/releases/download/v{fix}_w{week}/laanp-PetesBeaconTeleport_v{fix}_w{week}_P.pak'


class FakeWeb:
    def __init__(self, week=252, rev='3.0.30.158174', pak=PAK):
        self.requests = []
        self.pages = {
            MODINFO_URL: json.dumps({'mods': [{'name': 'laanp-PetesBeaconTeleport', 'compatibility': f'w{week}',
                                               'files': {'pak': pak_url(week)}, 'readmeURL': README}]}).encode(),
            README.replace('https://github.com/laanp/Icarus_Mods_Separated/raw/',
                           'https://raw.githubusercontent.com/laanp/Icarus_Mods_Separated/'): f'Mod Version: 1\n\nCompatible with Icarus Version: Rev. {rev} (Week: {week})\n'.encode(),
            pak_url(week): pak,
        }

    def __call__(self, url, timeout=60):
        self.requests.append(url)
        if url not in self.pages:
            raise OSError(f'HTTP Error 404 for {url}')
        return io.BytesIO(self.pages[url])


class ModUpdaterTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.mods = self.root / 'server' / 'Icarus' / 'Content' / 'Paks' / 'mods'
        self.mods.mkdir(parents=True)
        self.set_game(3, 0, 30, 158174)

    def tearDown(self):
        self.folder.cleanup()

    def set_game(self, major, minor, patch, changelist):
        config = self.root / 'server' / 'Icarus' / 'Config'
        config.mkdir(parents=True, exist_ok=True)
        (config / 'version.json').write_text(json.dumps(
            {'Name': 'Icarus', 'Version': {'Major': major, 'Minor': minor, 'Patch': patch, 'Changelist': changelist}}))

    def install(self, name='laanp-PetesBeaconTeleport_v1_w251_P.pak'):
        (self.mods / name).write_bytes(b'old')

    def run_update(self, web):
        return ModUpdater(self.root, opener=web).update()

    def test_reads_game_version(self):
        self.assertEqual(game_version(self.root), '3.0.30.158174')
        self.assertIsNone(game_version(self.root / 'missing'))

    def test_replaces_older_week_and_keeps_old_file(self):
        self.install()
        lines = self.run_update(FakeWeb())
        self.assertEqual(lines, ['laanp-PetesBeaconTeleport: updated w251 v1 to w252 v1 (old file kept in backups\\mods).'])
        self.assertEqual([p.name for p in self.mods.iterdir()], ['laanp-PetesBeaconTeleport_v1_w252_P.pak'])
        self.assertEqual((self.mods / 'laanp-PetesBeaconTeleport_v1_w252_P.pak').read_bytes(), PAK)
        self.assertEqual((self.root / 'backups' / 'mods' / 'laanp-PetesBeaconTeleport_v1_w251_P.pak').read_bytes(), b'old')

    def test_current_mod_is_left_alone(self):
        self.install('laanp-PetesBeaconTeleport_v1_w252_P.pak')
        web = FakeWeb()
        self.assertEqual(self.run_update(web), ['laanp-PetesBeaconTeleport: w252 v1 is the latest release.'])
        self.assertNotIn(pak_url(252), web.requests)
        self.assertEqual((self.mods / 'laanp-PetesBeaconTeleport_v1_w252_P.pak').read_bytes(), b'old')

    def test_warns_when_game_is_ahead_of_latest_mod(self):
        self.install('laanp-PetesBeaconTeleport_v1_w252_P.pak')
        self.set_game(3, 0, 31, 159000)
        lines = self.run_update(FakeWeb())
        self.assertIn('Warning: laanp-PetesBeaconTeleport was made for game 3.0.30.158174 (week 252) '
                      'but the server has 3.0.31.159000.', lines[1])

    def test_does_not_install_mod_for_newer_game(self):
        self.install()
        self.set_game(3, 0, 29, 157000)
        lines = self.run_update(FakeWeb())
        self.assertIn('needs game 3.0.30.158174, but the server has 3.0.29.157000. Kept w251', lines[0])
        self.assertTrue((self.mods / 'laanp-PetesBeaconTeleport_v1_w251_P.pak').exists())

    def test_bad_download_keeps_installed_mod(self):
        self.install()
        with self.assertRaisesRegex(ModError, 'did not download as a valid .pak'):
            self.run_update(FakeWeb(pak=b'<html>Not Found</html>'))
        self.assertEqual([p.name for p in self.mods.iterdir()], ['laanp-PetesBeaconTeleport_v1_w251_P.pak'])

    def test_unknown_mods_and_empty_folder(self):
        self.assertEqual(self.run_update(FakeWeb()), ['No mods installed.'])
        self.install('Someone-Else_v2_w250_P.pak')
        self.assertEqual(self.run_update(FakeWeb()), ["Someone-Else: not in laanp's mod list, left as is."])

    def test_mod_list_unreachable(self):
        self.install()
        web = FakeWeb()
        del web.pages[MODINFO_URL]
        with self.assertRaisesRegex(ModError, 'Could not read the mod list'):
            self.run_update(web)

    def test_keeps_five_old_copies(self):
        backups = self.root / 'backups' / 'mods'
        backups.mkdir(parents=True)
        for week in range(240, 246):
            (backups / f'laanp-PetesBeaconTeleport_v1_w{week}_P.pak').write_bytes(b'old')
        self.install()
        self.run_update(FakeWeb())
        self.assertEqual(len(list(backups.glob('*.pak'))), 5)
        self.assertTrue((backups / 'laanp-PetesBeaconTeleport_v1_w251_P.pak').exists())


if __name__ == '__main__':
    unittest.main()
