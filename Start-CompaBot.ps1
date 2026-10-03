# Runs Compabot and restarts it if it crashes or loses its connection, waiting 30 seconds
# between tries. Used by the CompaBot logon task (see Install-CompaBotStartup.ps1).
# Output goes to logs\compabot.log; the previous run's log is kept as logs\compabot.prev.log.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$bot = Join-Path $PSScriptRoot 'Compabot.py'
$logs = Join-Path $PSScriptRoot 'logs'
if (-not (Test-Path $python)) { throw "Python environment not found at $python. Follow the README setup first." }
New-Item -ItemType Directory -Force $logs | Out-Null

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
