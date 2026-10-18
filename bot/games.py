"""What Compabot knows about each game server it runs: where it lives and how to talk to it.

Each game has one profile. The /server commands and the background jobs read these facts
from the profile instead of hard-coding them, so adding a game means adding a profile.
Settings in the game's config.psd1 (install folder, server name, ports, max players) win
over the defaults here.
"""
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent


@dataclass(frozen=True)
class GameProfile:
    key: str  # the value of the /server game: option
    name: str  # shown in Discord
    scripts_dir: str  # default folder of the PowerShell scripts and config.psd1
    scripts: dict  # action -> script file name in scripts_dir
    status_script: Path  # prints {"running": ..., "started": ...} as JSON
    process: str  # server process name, as a WMI LIKE pattern
    server_name: str  # used when config.psd1 has no ServerName
    game_port: str
    query_port: int  # Steam query (A2S) port used to count players
    max_players: int
    steam_app_id: str  # the dedicated server's Steam app
    backup_glob: str  # backup zips in <install root>\backups
    log_file: str  # server log, relative to the install root
    saves_dir: str  # world saves, relative to the install root
    crashes_dir: str  # crash report folders, relative to the install root
    ready_line: str  # log text that means players can join; {port} is the game port
    features: frozenset = field(default_factory=frozenset)  # extras: mods, worlds, ores

    def supports(self, feature):
        return feature in self.features


ICARUS = GameProfile(
    key='icarus',
    name='Icarus',
    scripts_dir=r'C:\IcarusServer\scripts',
    scripts={'start': 'Start-IcarusServer.ps1', 'stop': 'Stop-IcarusServer.ps1',
             'backup': 'Backup-IcarusServer.ps1', 'update': 'Update-IcarusServer.ps1'},
    status_script=HERE / 'icarus' / 'Get-IcarusStatus.ps1',
    process='IcarusServer%',
    server_name='Icarus server',
    game_port='17777',
    query_port=27015,
    max_players=2,
    steam_app_id='2089300',
    backup_glob='Icarus-*.zip',
    log_file=r'data\Saved\Logs\Icarus.log',
    saves_dir=r'data\Saved\PlayerData',
    crashes_dir=r'data\Saved\Crashes',
    # Icarus uses Steam networking, so the game port never appears as a normal Windows
    # UDP endpoint; this log line is the reliable signal.
    ready_line='listening on port {port}',
    features=frozenset({'mods', 'worlds', 'ores'}),
)

GAMES = {game.key: game for game in (ICARUS,)}
DEFAULT_GAME = ICARUS.key


def relative(root, path):
    """Join a backslash-separated path from a profile onto root, on Windows or not."""
    return Path(root).joinpath(*path.split('\\'))
