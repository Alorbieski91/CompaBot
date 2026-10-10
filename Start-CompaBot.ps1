# Runs Compabot and restarts it if it crashes or loses its connection, waiting 30 seconds
# between tries. Used by the CompaBot logon task (see Install-CompaBotStartup.ps1).
# Output goes to logs\compabot.log; the previous run's log is kept as logs\compabot.prev.log.
# -FindPython only prints which Python it would use.
param([switch]$FindPython)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
# Use a virtual environment in this folder if there is one, otherwise Python from PATH
# (skipping the Microsoft Store shortcut, which only opens the Store).
$python = @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe', 'Scripts\python.exe') |
    ForEach-Object { Join-Path $PSScriptRoot $_ } | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) {
    $python = Get-Command python.exe -All -ErrorAction SilentlyContinue |
        Where-Object Source -notlike '*\WindowsApps\*' | Select-Object -First 1 -ExpandProperty Source
}
if (-not $python) { throw 'Python not found in .venv, venv, or this folder, or on PATH. Follow the README setup first.' }
if ($FindPython) { $python; exit 0 }
$bot = Join-Path $PSScriptRoot 'Compabot.py'
$logs = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
# Stop-CompaBot.ps1 leaves this so the watchdog knows the bot was stopped on purpose.
Remove-Item (Join-Path $logs 'compabot.stopped') -ErrorAction SilentlyContinue

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object CommandLine -like '*Compabot.py*'
if ($running) { Write-Host 'Compabot is already running.'; exit 0 }

while ($true) {
    $log = Join-Path $logs 'compabot.log'
    if (Test-Path $log) { Move-Item $log (Join-Path $logs 'compabot.prev.log') -Force }
    $proc = Start-Process -FilePath $python -ArgumentList "`"$bot`"" -WorkingDirectory $PSScriptRoot `
        -NoNewWindow -PassThru -RedirectStandardError $log -RedirectStandardOutput (Join-Path $logs 'compabot.out.log')
    $null = $proc.Handle  # keeps ExitCode readable after the process ends
    $proc.WaitForExit()
    $code = $proc.ExitCode
    Add-Content $log "$(Get-Date -Format s) Compabot exited with code $code."
    if ($code -eq 0) { exit 0 }
    Start-Sleep -Seconds 30
}
