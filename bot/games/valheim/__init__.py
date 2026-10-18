"""Valheim: its profile. Valheim has no extras of its own yet, only the shared /server commands."""
from ...servers.profile import GameProfile
from .players import log_players

VALHEIM = GameProfile(
    key='valheim',
    name='Valheim',
    scripts_dir=r'C:\ValheimServer\scripts',
    scripts={'start': 'Start-ValheimServer.ps1', 'stop': 'Stop-ValheimServer.ps1',
             'backup': 'Backup-ValheimServer.ps1', 'update': 'Update-ValheimServer.ps1'},
    process='valheim_server%',
    server_name='Valheim server',
    game_port='2456',
    # Valheim answers Steam queries on the port after the game port.
    query_port=2457,
    max_players=10,
    steam_app_id='896660',
    news_app_id='892970',
    backup_glob='Valheim-*.zip',
    log_file=r'logs\valheim_server.log',
    saves_dir=r'data\worlds_local',
    crashes_dir=None,
    # Logged once the world is loaded and the server is signed in to Steam.
    ready_line='Game server connected',
    log_players=log_players,
    active_hours=False,  # not being played for now; VALHEIM_ACTIVE_HOURS=on turns it on
)
