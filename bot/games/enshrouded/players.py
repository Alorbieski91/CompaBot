"""Who the Enshrouded server log says is on, for when the Steam query port doesn't answer."""
import re

from ...servers.steam_query import Info

# Enshrouded logs a player by name when they finish joining, and again when their character is removed.
JOINED = re.compile(r"\[server\] Player '(.+?)' logged in")
LEFT = re.compile(r"\[server\] Remove Entity for Player '(.+?)'")
# Every player is dropped when the session closes.
CLOSED = "[Session] 'HostOnline' (down)!"


def enshrouded_players_online(log_text):
    """The names of the players the Enshrouded server log says are on, in joining order."""
    online = []
    for line in log_text.splitlines():
        if match := JOINED.search(line):
            if match[1] not in online:
                online.append(match[1])
        elif match := LEFT.search(line):
            if match[1] in online:
                online.remove(match[1])
        elif CLOSED in line:
            online = []
    return online


def log_players(log_text, max_players):
    names = enshrouded_players_online(log_text)
    return Info(len(names), max_players, tuple(sorted(names)))
