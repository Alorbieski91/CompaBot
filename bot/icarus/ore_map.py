"""Draw a world's deep ore deposits on its 16 x 16 map grid as an image, like the ore map site.

Compabot ships no map artwork. Put a square top-down image of a map in data/maps, named
after the map (Olympus.jpg, Elysium.png, ...), and the deposits are drawn over it;
otherwise they are drawn on a plain background with the grid.
"""
import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .ore_deposits import COLUMNS, GRID, MAP_EDGE, by_ore, ore_label

MAPS_DIR = Path(__file__).resolve().parents[2] / 'data' / 'maps'
MAP_SIZE = 1600
LEGEND_WIDTH = 380
CELL_PX = MAP_SIZE / GRID
RADIUS = 13
ONE_ORE_RADIUS = 16

SURFACE = (26, 26, 25)
PLAIN_MAP = (38, 40, 38)
GRID_LINE = (120, 120, 112)
GRID_LABEL = (165, 164, 155)
TEXT = (240, 239, 236)
TEXT_SECONDARY = (195, 194, 183)
DARK_INK = (26, 26, 25)

# Twenty-one ores is far more than color alone can tell apart, so every marker carries a short
# code; the color only groups related ores.
BLUE, ORANGE, AQUA, YELLOW, MAGENTA, GREEN, VIOLET = '#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#008300', '#9085e9'
CODES = {
    'Iron': ('Fe', BLUE), 'Copper': ('Cu', BLUE), 'Aluminium': ('Al', BLUE), 'Titanium': ('Ti', BLUE),
    'Gold': ('Au', ORANGE), 'Platinum': ('Pt', ORANGE),
    'Silicon': ('Si', AQUA), 'Oxite': ('Ox', AQUA), 'Super_Cooled_Ice': ('Ic', AQUA), 'Salt': ('Sa', AQUA),
    'Coal': ('Co', YELLOW), 'Sulfur': ('S', YELLOW),
    'Exotic': ('Ex', MAGENTA), 'Exotic_Red_Raw': ('Rx', MAGENTA), 'Exotic_Raw_Uranium': ('U', MAGENTA),
    'Stone': ('St', GREEN), 'Clay': ('Cy', GREEN), 'Limestone': ('Ls', GREEN), 'Frozen_Wood': ('Wd', GREEN),
    'Obsidian': ('Ob', VIOLET), 'Scoria': ('Sc', VIOLET),
}
FONTS = ('arialbd.ttf', 'arial.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans.ttf')


def code(ore):
    return CODES.get(ore, (ore[:2], VIOLET))


def font(size):
    for name in FONTS:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def rgb(hex_color):
    return tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))


def ink(fill):
    """Dark or light text, whichever reads better on a marker color."""
    r, g, b = fill
    return DARK_INK if 0.299 * r + 0.587 * g + 0.114 * b > 150 else TEXT


def to_px(x, y):
    return (x + MAP_EDGE) / (2 * MAP_EDGE) * MAP_SIZE, (y + MAP_EDGE) / (2 * MAP_EDGE) * MAP_SIZE


def base_map(world, maps_dir=None):
    """The map artwork from data/maps scaled to the canvas and dimmed, or a plain square."""
    folder = Path(maps_dir or MAPS_DIR)
    for suffix in ('.png', '.jpg', '.jpeg', '.webp'):
        path = folder / f'{world}{suffix}'
        if path.is_file():
            try:
                with Image.open(path) as image:
                    art = image.convert('RGB').resize((MAP_SIZE, MAP_SIZE), Image.LANCZOS)
                return Image.blend(art, Image.new('RGB', art.size, SURFACE), 0.35)
            except OSError:
                break
    return Image.new('RGB', (MAP_SIZE, MAP_SIZE), PLAIN_MAP)


def render(prospect, world, deposits, ore=None, maps_dir=None):
    """JPEG bytes of the map with the deposits (or one ore's) marked and a legend beside it."""
    if ore:
        deposits = [d for d in deposits if d.ore.lower() == ore.lower()]
    canvas = Image.new('RGB', (MAP_SIZE + LEGEND_WIDTH, MAP_SIZE), SURFACE)
    canvas.paste(base_map(world, maps_dir), (0, 0))
    draw = ImageDraw.Draw(canvas)

    label_font = font(15)
    for i in range(GRID + 1):
        at = round(i * CELL_PX)
        draw.line([(at, 0), (at, MAP_SIZE)], fill=GRID_LINE, width=1)
        draw.line([(0, at), (MAP_SIZE, at)], fill=GRID_LINE, width=1)
    for column in range(GRID):
        for row in range(GRID):
            draw.text((column * CELL_PX + 4, row * CELL_PX + 2), f'{COLUMNS[column]}{row + 1}',
                      font=label_font, fill=GRID_LABEL)

    radius = ONE_ORE_RADIUS if ore else RADIUS
    code_font = font(radius + 1)
    for deposit in sorted(deposits, key=lambda d: d.y):
        px, py = to_px(deposit.x, deposit.y)
        text, color = code(deposit.ore)
        fill = rgb(color)
        # A ring in the surface color keeps overlapping markers apart.
        draw.ellipse([px - radius - 2, py - radius - 2, px + radius + 2, py + radius + 2], fill=SURFACE)
        draw.ellipse([px - radius, py - radius, px + radius, py + radius], fill=fill)
        draw.text((px, py), text, font=code_font, fill=ink(fill), anchor='mm')

    left = MAP_SIZE + 28
    draw.text((left, 28), prospect, font=font(34), fill=TEXT)
    draw.text((left, 74), world, font=font(22), fill=TEXT_SECONDARY)
    draw.text((left, 102), f'{len(deposits)} deep ore deposits', font=font(22), fill=TEXT_SECONDARY)
    row_font, y = font(22), 152
    for name, group in by_ore(deposits):
        text, color = code(name)
        fill = rgb(color)
        draw.ellipse([left, y, left + 30, y + 30], fill=fill)
        draw.text((left + 15, y + 15), text, font=font(15), fill=ink(fill), anchor='mm')
        draw.text((left + 44, y + 15), f'{ore_label(name)}  {len(group)}', font=row_font, fill=TEXT, anchor='lm')
        y += 42
    if not deposits:
        draw.text((left, y), 'None found.', font=row_font, fill=TEXT_SECONDARY)

    out = io.BytesIO()
    # JPEG keeps a map with artwork well under Discord's upload limit; PNG would be ~4 MB.
    canvas.save(out, 'JPEG', quality=90)
    return out.getvalue()
