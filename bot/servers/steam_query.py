"""Ask a Steam game server's query port for its player count (A2S_INFO)."""
import socket
import struct
from dataclasses import dataclass

A2S_INFO = b'\xff\xff\xff\xffTSource Engine Query\x00'


@dataclass
class Info:
    players: int
    max_players: int
    names: tuple = ()  # Known only when read from the server log.


def query_info(port, host='127.0.0.1', timeout=2.0):
    """Ask the Steam query port for server info (A2S_INFO). Returns Info, or None if nothing answers."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            # Connecting makes a closed port fail at once instead of waiting out the timeout.
            sock.connect((host, port))
            sock.send(A2S_INFO)
            data = sock.recv(1400)
            if data[4:5] == b'A':  # Newer servers answer with a challenge to send back first.
                sock.send(A2S_INFO + data[5:9])
                data = sock.recv(1400)
        except OSError:
            return None
    return parse_info(data)


def parse_info(data):
    if data[:5] != b'\xff\xff\xff\xffI':
        return None
    pos = 6  # Skip the header and protocol byte, then the name, map, folder, and game strings.
    for _ in range(4):
        pos = data.find(b'\x00', pos)
        if pos < 0:
            return None
        pos += 1
    if len(data) < pos + 4:
        return None
    players, max_players = struct.unpack_from('<BB', data, pos + 2)
    return Info(players, max_players)
