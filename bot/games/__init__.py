"""The game servers Compabot runs, one folder each.

A game's folder holds its profile (where it lives and how to talk to it, in __init__.py) and
anything only that game has, such as its own /server commands. Code every game shares is in
bot/servers.
"""
from ..servers.profile import GameProfile, relative  # noqa: F401 (kept importable from here)
from .icarus import ICARUS
from .valheim import VALHEIM

GAMES = {game.key: game for game in (ICARUS, VALHEIM)}
DEFAULT_GAME = ICARUS.key
