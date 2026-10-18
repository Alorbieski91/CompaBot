import base64
import json
import struct
import unittest
import zlib

from bot.games.icarus.ore_deposits import ProspectError, find_deposits, grid_cell, listing, prospect_blob, read_deposits, summary


def deposit(ore, x, y, z=0.0):
    """One deposit laid out the way a prospect save stores it."""
    name = ore.encode() + b'\0'
    return (b'/Script/Icarus.ResourceDeposit\0junk' + b'NameProperty\0' + b'\x09' * 8 + b'\0'
            + struct.pack('<i', len(name)) + name + b'\0StructProperty\0' + b'Vector\0' + b'\x07' * 16 + b'\0'
            + struct.pack('<fff', x, y, z) + b'more junk')


def oil_well(x, y, z=0.0):
    """One oil well record: its transform comes first, then its own Vector-typed fields."""
    return (b'/Script/Icarus.OilGeyserRecorderComponent\0BinaryData\0ActorTransform\0Rotation\0Quat\0' + b'\0' * 16
            + b'Translation\0StructProperty\0' + b'Vector\0' + b'\x07' * 16 + b'\0' + struct.pack('<fff', x, y, z)
            + b'Scale3D\0StructProperty\0Vector\0' + b'\x07' * 17 + struct.pack('<fff', 1, 1, 1))


def save(data, bom=b''):
    blob = base64.b64encode(zlib.compress(data)).decode()
    text = json.dumps({'ProspectInfo': {'ProspectID': 'TEST'}, 'ProspectBlob': {'BinaryBlob': blob}}, indent='\t')
    return bom + text.encode('utf-16-le' if bom == b'\xff\xfe' else 'utf-8')


WORLD = (b'/Game/Terrain_016/Terrain_016' + deposit('Titanium', -400000, -400000) + deposit('Gold', 1000, 2000)
         + deposit('Titanium', 402000, 402000) + deposit('Titanium', -399000, -380000) + deposit('Super_Cooled_Ice', 0, 0))


OILY = WORLD + oil_well(-310000, 140000) + oil_well(-311000, 139000) + oil_well(113000, -244000)


class OreDepositTests(unittest.TestCase):
    def test_finds_map_and_deposits(self):
        world, deposits = find_deposits(WORLD)
        self.assertEqual(world, 'Olympus')
        self.assertEqual([(d.ore, d.cell) for d in deposits],
                         [('Titanium', 'A1'), ('Gold', 'I9'), ('Titanium', 'P16'), ('Titanium', 'A1'), ('Super_Cooled_Ice', 'I9')])

    def test_finds_oil_wells_after_the_ores(self):
        world, deposits = find_deposits(OILY)
        self.assertEqual([(d.ore, d.cell) for d in deposits[5:]], [('Oil', 'B11'), ('Oil', 'B11'), ('Oil', 'K4')])
        self.assertEqual(summary('ELMUNDOFOO', world, deposits),
                         '**ELMUNDOFOO** (Olympus): 5 deep ore deposits.\nTitanium 3 · Gold 1 · Super Cooled Ice 1\n'
                         'Oil wells: 3 in B11 ×2, K4.')
        self.assertEqual(summary('ELMUNDOFOO', world, deposits, 'oil'), '**ELMUNDOFOO** (Olympus): 3 oil wells in B11 ×2, K4.')
        self.assertEqual(summary('ELMUNDOFOO', world, deposits[:5], 'Oil'), '**ELMUNDOFOO** (Olympus) has no oil wells.')
        text = listing('ELMUNDOFOO', world, deposits)
        self.assertIn('ELMUNDOFOO (Olympus): 5 deep ore deposits, 3 oil wells\n', text)
        self.assertTrue(text.rstrip().endswith('Oil wells (3)\n  B11  x   -311000  y    139000\n'
                                               '  B11  x   -310000  y    140000\n  K4   x    113000  y   -244000'))

    def test_grid_matches_the_ore_map_site(self):
        self.assertEqual(grid_cell(-403200, -403200), 'A1')
        self.assertEqual(grid_cell(-353000, 0), 'A9')
        self.assertEqual(grid_cell(-352000, -1), 'B8')
        self.assertEqual(grid_cell(403200, 403200), 'P16')
        self.assertEqual(grid_cell(-500000, 500000), 'A16')

    def test_reads_utf8_and_utf16_saves(self):
        for bom in (b'', b'\xef\xbb\xbf', b'\xff\xfe'):
            self.assertEqual(len(read_deposits(save(WORLD, bom))[1]), 5)

    def test_rejects_other_files(self):
        for raw in (b'{}', b'not json', save(WORLD)[:-40]):
            with self.assertRaises(ProspectError):
                prospect_blob(raw)

    def test_unknown_map_and_no_deposits(self):
        self.assertEqual(find_deposits(b'nothing here'), ('Unknown map', []))
        self.assertEqual(summary('X', 'Unknown map', []), '**X** (Unknown map) has no deep ore deposits that Compabot could find.')

    def test_summary(self):
        world, deposits = find_deposits(WORLD)
        self.assertEqual(summary('ELMUNDOFOO', world, deposits),
                         '**ELMUNDOFOO** (Olympus): 5 deep ore deposits.\nTitanium 3 · Gold 1 · Super Cooled Ice 1')
        self.assertEqual(summary('ELMUNDOFOO', world, deposits, 'titanium'),
                         '**ELMUNDOFOO** (Olympus): 3 Titanium deep ore deposits in A1 ×2, P16.')
        self.assertEqual(summary('ELMUNDOFOO', world, deposits, 'Copper'),
                         '**ELMUNDOFOO** (Olympus) has no Copper deep ore deposits.')

    def test_listing(self):
        text = listing('ELMUNDOFOO', *find_deposits(WORLD))
        self.assertIn('Titanium (3)\n  A1   x   -400000  y   -400000\n  A1   x   -399000  y   -380000\n  P16', text)
        self.assertIn('Super Cooled Ice (1)\n  I9 ', text)


if __name__ == '__main__':
    unittest.main()
