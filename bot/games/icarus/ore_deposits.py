"""Find the deep ore deposits and oil wells in an Icarus prospect save, as the ore map site shows ores.

The site is https://faysm.github.io/IcarusOreDeposit/. It only takes a dropped file, so
/server ores lists the deposits itself and attaches the save for anyone who wants the map.
"""
import base64
import binascii
import json
import struct
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass

ORE_MAP_SITE = 'https://faysm.github.io/IcarusOreDeposit/'
# The terrain each map's save mentions.
MAPS = {b'Terrain_016': 'Olympus', b'Terrain_017': 'Styx', b'Terrain_019': 'Prometheus', b'Terrain_021': 'Elysium'}
# Deep ores the site has icons for (the save names them the same way), then oil wells.
ORES = ('Aluminium', 'Clay', 'Coal', 'Copper', 'Frozen_Wood', 'Gold', 'Iron', 'Limestone', 'Obsidian', 'Oxite',
        'Platinum', 'Salt', 'Scoria', 'Silicon', 'Stone', 'Sulfur', 'Titanium', 'Exotic', 'Exotic_Red_Raw',
        'Super_Cooled_Ice', 'Exotic_Raw_Uranium', 'Oil')
# Oil wells aren't deep ore, but the save records where each one is, so they are mapped too.
OIL = 'Oil'
# Every map spans -403,200 to 403,200 units on both axes, split into a 16 x 16 grid:
# columns A-P from left to right, rows 1-16 from top to bottom, as on the ore map site.
MAP_EDGE = 403200
GRID = 16
CELL = 2 * MAP_EDGE / GRID
COLUMNS = 'ABCDEFGHIJKLMNOP'

DEPOSIT = b'Script/Icarus.ResourceDeposit'
NAME = b'NameProperty'
VECTOR = b'Vector'
OIL_WELL = b'Script/Icarus.OilGeyserRecorderComponent'
TRANSLATION = b'Translation'
# After "NameProperty": its terminator, the 8-byte property size and a flag byte, then the name.
NAME_SKIP = len(NAME) + 1 + 8 + 1
# After "Vector": its terminator, the 16-byte struct GUID and a flag byte, then x, y, z floats.
VECTOR_SKIP = len(VECTOR) + 1 + 16 + 1


class ProspectError(Exception):
    pass


@dataclass(frozen=True)
class Deposit:
    ore: str
    x: float
    y: float

    @property
    def cell(self):
        return grid_cell(self.x, self.y)


def grid_cell(x, y):
    """The map grid square (A1 top left to P16 bottom right) holding a world position."""
    column = min(GRID - 1, max(0, int((x + MAP_EDGE) // CELL)))
    row = min(GRID - 1, max(0, int((y + MAP_EDGE) // CELL)))
    return f'{COLUMNS[column]}{row + 1}'


def ore_label(ore):
    return 'Oil wells' if ore == OIL else ore.replace('_', ' ')


def prospect_blob(raw):
    """The decompressed world data inside a prospect .json save."""
    encoding = 'utf-16' if raw[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig'
    try:
        save = json.loads(raw.decode(encoding))
        blob = save['ProspectBlob']['BinaryBlob']
        return zlib.decompress(base64.b64decode(blob))
    except (UnicodeDecodeError, ValueError, KeyError, TypeError, binascii.Error, zlib.error) as error:
        raise ProspectError(f"it doesn't look like a prospect save ({error})") from None


def find_deposits(data):
    """The map name and the deep ore deposits, then the oil wells, in decompressed prospect data."""
    world = next((name for marker, name in MAPS.items() if marker in data), 'Unknown map')
    deposits, at = [], 0
    while (at := data.find(DEPOSIT, at)) >= 0:
        at += len(DEPOSIT)
        name_at = data.find(NAME, at)
        if name_at < 0:
            break
        name_at += NAME_SKIP
        try:
            (length,) = struct.unpack_from('<i', data, name_at)
            if not 1 < length < 128:
                continue
            ore = data[name_at + 4:name_at + 3 + length].decode('utf-8')
            vector_at = data.find(VECTOR, name_at + 4 + length)
            if vector_at < 0:
                break
            x, y, _ = struct.unpack_from('<fff', data, vector_at + VECTOR_SKIP)
        except (struct.error, UnicodeDecodeError):
            continue
        deposits.append(Deposit(ore, x, y))
        at = vector_at + VECTOR_SKIP + 12
    return world, deposits + oil_wells(data)


def oil_wells(data):
    """Oil wells, from the position (ActorTransform's Translation) each one's record starts with."""
    wells, at = [], 0
    while (at := data.find(OIL_WELL, at)) >= 0:
        at += len(OIL_WELL)
        translation = data.find(TRANSLATION, at)
        vector_at = data.find(VECTOR, translation) if translation >= 0 else -1
        if vector_at < 0:
            break
        try:
            x, y, _ = struct.unpack_from('<fff', data, vector_at + VECTOR_SKIP)
        except struct.error:
            break
        wells.append(Deposit(OIL, x, y))
    return wells


def read_deposits(raw):
    return find_deposits(prospect_blob(raw))


def cells(deposits):
    """Grid squares in map order, with a count where a square holds more than one deposit."""
    counts = Counter(d.cell for d in deposits)
    order = sorted(counts, key=lambda c: (COLUMNS.index(c[0]), int(c[1:])))
    return ', '.join(c if counts[c] == 1 else f'{c} ×{counts[c]}' for c in order)


def by_ore(deposits):
    groups = defaultdict(list)
    for deposit in deposits:
        groups[deposit.ore].append(deposit)
    return sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))


def split_oil(deposits):
    return [d for d in deposits if d.ore != OIL], [d for d in deposits if d.ore == OIL]


def summary(prospect, world, deposits, ore=None):
    """The reply text: counts per ore and where the oil wells are, or one ore's grid squares."""
    head = f'**{prospect}** ({world})'
    if ore and ore.lower() == OIL.lower():
        wells = split_oil(deposits)[1]
        return f'{head}: {len(wells)} oil wells in {cells(wells)}.' if wells else f'{head} has no oil wells.'
    if ore:
        found = [d for d in deposits if d.ore.lower() == ore.lower()]
        if not found:
            return f'{head} has no {ore_label(ore)} deep ore deposits.'
        return f'{head}: {len(found)} {ore_label(found[0].ore)} deep ore deposits in {cells(found)}.'
    ores, wells = split_oil(deposits)
    oil = f'\nOil wells: {len(wells)} in {cells(wells)}.' if wells else ''
    if not ores:
        return f'{head} has no deep ore deposits that Compabot could find.' + oil
    counts = ' · '.join(f'{ore_label(name)} {len(group)}' for name, group in by_ore(ores))
    return f'{head}: {len(ores)} deep ore deposits.\n{counts}' + oil


def listing(prospect, world, deposits):
    """A text file listing every deposit by ore and grid square."""
    ores, wells = split_oil(deposits)
    lines = [f'{prospect} ({world}): {len(ores)} deep ore deposits, {len(wells)} oil wells',
             'Grid squares run A-P left to right and 1-16 top to bottom, as on ' + ORE_MAP_SITE, '']
    for name, group in by_ore(ores) + by_ore(wells):
        lines.append(f'{ore_label(name)} ({len(group)})')
        for deposit in sorted(group, key=lambda d: (COLUMNS.index(d.cell[0]), int(d.cell[1:]), d.x, d.y)):
            lines.append(f'  {deposit.cell:<4} x {deposit.x:>9.0f}  y {deposit.y:>9.0f}')
        lines.append('')
    return '\n'.join(lines)
