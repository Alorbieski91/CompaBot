"""Palworld: its profile. Palworld has no extras of its own yet, only the shared /server commands."""
from ...servers.profile import GameProfile
from .api import ask

PALWORLD = GameProfile(
    key='palworld',
    name='Palworld',
    scripts_dir=r'C:\PalworldServer\scripts',
    scripts={'start': 'Start-PalworldServer.ps1', 'stop': 'Stop-PalworldServer.ps1',
             'backup': 'Backup-PalworldServer.ps1', 'update': 'Update-PalworldServer.ps1'},
    # PalServer.exe, and the PalServer-Win64-Shipping-Cmd.exe it launches.
    process='PalServer%',
    server_name='Palworld server',
    game_port='8211',
    # Moved off the default 27015, which Icarus uses. Players are counted through the REST API instead.
    query_port=27016,
    max_players=4,
    steam_app_id='2394010',
    news_app_id='1623730',
    backup_glob='Palworld-*.zip',
    # Palworld only prints to its console window.
    log_file=None,
    saves_dir=r'server\Pal\Saved\SaveGames',
    crashes_dir=r'server\Pal\Saved\Crashes',
    # The REST API answers once the world has loaded, so it tells both when it is ready and who is on.
    ready_line=None,
    log_players=None,
    ask=ask,
    active_hours=False,  # PALWORLD_ACTIVE_HOURS=on puts it on the shared schedule
)
