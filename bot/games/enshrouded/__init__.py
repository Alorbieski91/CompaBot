"""Enshrouded: its profile. Enshrouded has no extras of its own yet, only the shared /server commands."""
from ...servers.profile import GameProfile
from .players import log_players

ENSHROUDED = GameProfile(
    key='enshrouded',
    name='Enshrouded',
    scripts_dir=r'C:\EnshroudedServer\scripts',
    scripts={'start': 'Start-EnshroudedServer.ps1', 'stop': 'Stop-EnshroudedServer.ps1',
             'backup': 'Backup-EnshroudedServer.ps1', 'update': 'Update-EnshroudedServer.ps1'},
    process='enshrouded_server%',
    server_name='Enshrouded server',
    # Current Enshrouded uses one UDP port, its query port, for joining and the server list.
    # config.psd1's GamePort (15636) is only an old port the firewall keeps open.
    game_port='15637',
    port_key='QueryPort',
    query_port=15637,
    max_players=4,
    steam_app_id='2278520',
    news_app_id='1203620',
    backup_glob='Enshrouded-*.zip',
    # The server starts a fresh log each run and moves the old one to logs\backup.
    log_file=r'server\logs\enshrouded_server.log',
    saves_dir=r'server\savegame',
    crashes_dir=None,
    # Logged once the server is signed in to Steam and its session is open to players.
    ready_line="[Session] 'HostOnline' (up)!",
    log_players=log_players,
    active_hours=False,  # ENSHROUDED_ACTIVE_HOURS=on puts it on the shared schedule
)
