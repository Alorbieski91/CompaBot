"""What the shared server code needs to know about a game: its profile.

Each game folder in bot/games defines one GameProfile. The /server commands and the background
jobs read these facts from the profile instead of hard-coding them, so adding a game means
adding a folder with a profile. Settings in the game's config.psd1 (install folder, server name,
ports, max players) win over the defaults in the profile.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Prints {"running": ..., "started": ...} as JSON; nothing in it is specific to one game.
STATUS_SCRIPT = Path(__file__).resolve().parent / 'Get-ServerStatus.ps1'


class ServerError(Exception):
    pass


class ModError(Exception):
    """A game's mod updater could not finish."""


@dataclass(frozen=True)
class GameProfile:
    key: str  # the value of the /server game: option
    name: str  # shown in Discord
    scripts_dir: str  # default folder of the PowerShell scripts and config.psd1
    scripts: dict  # action -> script file name in scripts_dir
    process: str  # server process name, as a WMI LIKE pattern
    server_name: str  # used when config.psd1 has no ServerName
    game_port: str
    query_port: int  # Steam query (A2S) port used to count players, unless the game has its own `ask`
    max_players: int
    steam_app_id: str  # the dedicated server's Steam app
    news_app_id: str  # the game's own Steam app, whose news feed has the patch notes
    backup_glob: str  # backup zips in <install root>\backups
    log_file: Optional[str]  # server log, relative to the install root; None if the game writes none
    saves_dir: str  # world saves, relative to the install root
    crashes_dir: Optional[str]  # crash report folders, relative to the install root; None if the game has none
    # Log text that means players can join; {port} is the game port. None: ready once `ask` answers.
    ready_line: Optional[str]
    # (server log text, max players) -> Info: who the log says is on, when the query port doesn't
    # answer; None if the game has no log to read.
    log_players: Optional[Callable]
    status_script: Path = STATUS_SCRIPT
    features: frozenset = field(default_factory=frozenset)  # extras: mods, worlds, ores, settings
    # Hooks into the game's own folder for its extras; None when the game has none.
    mods: Optional[Callable] = None  # install root -> mod updater with update() and available()
    worlds: Optional[Callable] = None  # ServerControl -> saved worlds, for /server start world:
    commands: Optional[Callable] = None  # adds the game's own /server commands (see servers.commands)
    # ServerControl -> Info, or None when nothing answers: the game's own way of asking the
    # running server who is on, used instead of the Steam query port.
    ask: Optional[Callable] = None
    # ServerControl -> the game's settings file (see bot/servers/settings.py), for /server settings;
    # None when the game has no settings Compabot can change.
    settings: Optional[Callable] = None
    # Whether the shared SERVER_START_TIME/SERVER_STOP_TIME schedule runs this game when its
    # <KEY>_ACTIVE_HOURS setting in .env is blank.
    active_hours: bool = True

    def supports(self, feature):
        return feature in self.features


def relative(root, path):
    """Join a backslash-separated path from a profile onto root, on Windows or not."""
    return Path(root).joinpath(*path.split('\\'))
