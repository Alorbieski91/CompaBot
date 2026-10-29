"""Ask the running Palworld server who is on, through its REST API on this PC.

The start script turns the API on (RESTAPIEnabled) and the Windows firewall keeps it local.
It signs in as admin with AdminPassword from config.psd1.
"""
import base64
import json
import urllib.request

from ...servers.steam_query import Info

DEFAULT_PORT = 8212


def request(control, path, timeout=3.0):
    """GET one REST API endpoint and return its JSON."""
    port = control.config.get('RestApiPort', DEFAULT_PORT)
    login = base64.b64encode(f"admin:{control.config.get('AdminPassword', '')}".encode()).decode()
    req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/api/{path}',
                                 headers={'Authorization': f'Basic {login}', 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def ask(control, fetch=request):
    """Who is on, as Info with names, or None if the server isn't answering."""
    try:
        players = fetch(control, 'players')['players']
        names = tuple(sorted(p['name'] for p in players if p.get('name')))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    # Only list names when every player has one, so a count and its names agree.
    return Info(len(players), control.max_players, names if len(names) == len(players) else ())
