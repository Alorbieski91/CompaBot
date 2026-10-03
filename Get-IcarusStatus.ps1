# Prints one line of JSON describing the Icarus server, for Compabot's /server status.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$p = Get-CimInstance Win32_Process -Filter "Name LIKE 'IcarusServer%'" |
    Where-Object ExecutablePath -like "$($env:ICARUS_ROOT)*" | Sort-Object CreationDate | Select-Object -First 1
if (-not $p) { '{"running":false}'; exit 0 }
$port = [bool](Get-NetUDPEndpoint -LocalPort ([int]$env:ICARUS_PORT) -ErrorAction SilentlyContinue)
[pscustomobject]@{ running = $true; port_open = $port
    started = [DateTimeOffset]::new($p.CreationDate).ToUnixTimeSeconds() } | ConvertTo-Json -Compress
