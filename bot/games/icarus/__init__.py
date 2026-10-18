"""Icarus: its profile, and its extras (mods, saved worlds, the ore map and their /server commands)."""
from ...servers.profile import GameProfile
from .mods import ModUpdater
from .players import log_players


# The worlds and commands modules use the shared /server code, which imports this package for
# the profile, so they are loaded when first needed rather than here.
def worlds(control):
    from .worlds import Worlds
    return Worlds(control)


def commands(server, shared):
    from .commands import install
    install(server, shared)


ICARUS = GameProfile(
    key='icarus',
    name='Icarus',
    scripts_dir=r'C:\IcarusServer\scripts',
    scripts={'start': 'Start-IcarusServer.ps1', 'stop': 'Stop-IcarusServer.ps1',
             'backup': 'Backup-IcarusServer.ps1', 'update': 'Update-IcarusServer.ps1'},
    process='IcarusServer%',
    server_name='Icarus server',
    game_port='17777',
    query_port=27015,
    max_players=2,
    steam_app_id='2089300',
    news_app_id='1149460',  # the dedicated server's app has no news feed
    backup_glob='Icarus-*.zip',
    log_file=r'data\Saved\Logs\Icarus.log',
    saves_dir=r'data\Saved\PlayerData',
    crashes_dir=r'data\Saved\Crashes',
    # Icarus uses Steam networking, so the game port never appears as a normal Windows
    # UDP endpoint; this log line is the reliable signal.
    ready_line='listening on port {port}',
    log_players=log_players,
    features=frozenset({'mods', 'worlds', 'ores'}),
    mods=ModUpdater,
    worlds=worlds,
    commands=commands,
)
