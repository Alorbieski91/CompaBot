"""Who the Icarus server log says is connected, for when the Steam query port doesn't answer."""
import re

from ...servers.steam_query import Info

# A join is logged with the player's name only, so it is matched to the address from the
# connection accepted just before it.
ACCEPTED = re.compile(r'NotifyAcceptingConnection accepted from: (\S+)')
JOINED = re.compile(r'Join succeeded: (.+)')
CLOSED = re.compile(r'UNetConnection::Close: \[UNetConnection\] RemoteAddr: ([^,\s]+)')


def players_online(log_text):
    """Names of the players the server log says are connected right now."""
    online, address = {}, None
    for line in log_text.splitlines():
        if match := ACCEPTED.search(line):
            address = match[1]
        elif (match := JOINED.search(line)) and address:
            online[address] = match[1].strip()
        elif match := CLOSED.search(line):
            online.pop(match[1], None)
    return sorted(online.values())


def log_players(log_text, max_players):
    names = players_online(log_text)
    return Info(len(names), max_players, tuple(names))
