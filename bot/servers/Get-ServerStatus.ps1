# Prints one line of JSON describing a game server, for Compabot's /server status.
# GAME_ROOT is the install folder; GAME_PROCESS is the server process name as a LIKE pattern.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$name = if ($env:GAME_PROCESS) { $env:GAME_PROCESS } else { 'IcarusServer%' }
$p = Get-CimInstance Win32_Process -Filter "Name LIKE '$name'" |
    Where-Object ExecutablePath -like "$($env:GAME_ROOT)*" | Sort-Object CreationDate | Select-Object -First 1
if (-not $p) { '{"running":false}'; exit 0 }
[pscustomobject]@{ running = $true
    started = [DateTimeOffset]::new($p.CreationDate).ToUnixTimeSeconds() } | ConvertTo-Json -Compress
