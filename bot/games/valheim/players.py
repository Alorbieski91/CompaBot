"""Who the Valheim server log says is connected, for when the Steam query port doesn't answer."""
import re

from ...servers.steam_query import Info

# Valheim logs each connection by Steam ID, and the character name a little later on its own.
CONNECTED = re.compile(r'Got connection SteamID (\d+)')
CHARACTER = re.compile(r'Got character ZDOID from (.+?) : ')
CLOSED = re.compile(r'Closing socket (\d+)')


def valheim_players_online(log_text):
    """Who the Valheim server log says is connected: {Steam ID: character name, or None while still joining}."""
    online = {}
    for line in log_text.splitlines():
        if match := CONNECTED.search(line):
            online[match[1]] = None
        elif match := CHARACTER.search(line):
            # The name is logged again after each death, so only a newcomer's first one counts.
            name = match[1].strip()
            waiting = [steam_id for steam_id, known in online.items() if known is None]
            if name not in online.values() and waiting:
                online[waiting[-1]] = name
        elif match := CLOSED.search(line):
            online.pop(match[1], None)
    return online


def log_players(log_text, max_players):
    online = valheim_players_online(log_text)
    names = sorted(name for name in online.values() if name)
    # Only list names when every connected player has one, so a count and its names agree.
    return Info(len(online), max_players, tuple(names) if len(names) == len(online) else ())
