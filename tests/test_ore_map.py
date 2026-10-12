import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from bot.icarus.ore_deposits import find_deposits
from bot.icarus.ore_map import CODES, LEGEND_WIDTH, MAP_SIZE, ORANGE, render, rgb, to_px
from bot.icarus.ore_deposits import ORES
from tests.test_ore_deposits import WORLD


def near(pixel, color, slack=40):
    return all(abs(a - b) <= slack for a, b in zip(pixel, color))


class OreMapTests(unittest.TestCase):
    def setUp(self):
        self.world, self.deposits = find_deposits(WORLD)
        self.empty = tempfile.TemporaryDirectory()
        self.addCleanup(self.empty.cleanup)

    def image(self, raw):
        return Image.open(io.BytesIO(raw)).convert('RGB')

    def test_every_ore_has_a_code(self):
        self.assertEqual(set(CODES), set(ORES))
        self.assertEqual(len({code for code, _ in CODES.values()}), len(CODES))

    def test_marks_deposits_where_they_are(self):
        image = self.image(render('TEST', self.world, self.deposits, maps_dir=self.empty.name))
        self.assertEqual(image.size, (MAP_SIZE + LEGEND_WIDTH, MAP_SIZE))
        gold = next(d for d in self.deposits if d.ore == 'Gold')
        x, y = to_px(gold.x, gold.y)
        # Just off the code text, inside the marker.
        self.assertTrue(near(image.getpixel((round(x) - 10, round(y))), rgb(ORANGE)))

    def test_one_ore_leaves_the_others_off(self):
        image = self.image(render('TEST', self.world, self.deposits, ore='titanium', maps_dir=self.empty.name))
        gold = next(d for d in self.deposits if d.ore == 'Gold')
        x, y = to_px(gold.x, gold.y)
        self.assertFalse(near(image.getpixel((round(x) - 10, round(y))), rgb(ORANGE)))

    def test_draws_over_map_artwork(self):
        Image.new('RGB', (64, 64), (250, 0, 250)).save(Path(self.empty.name) / 'Olympus.png')
        image = self.image(render('TEST', self.world, [], maps_dir=self.empty.name))
        self.assertGreater(image.getpixel((MAP_SIZE // 3, MAP_SIZE // 3))[0], 120)


if __name__ == '__main__':
    unittest.main()
