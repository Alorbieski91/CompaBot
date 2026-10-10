# Stops Compabot and its restart loop (Start-CompaBot.ps1), however it was started.
# It stays off until Start-CompaBot.ps1 runs again (Start-ScheduledTask CompaBot); until then the
# watchdog (Watch-CompaBot.ps1) leaves it alone.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$logs = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force $logs | Out-Null
Set-Content (Join-Path $logs 'compabot.stopped') "Stopped with Stop-CompaBot.ps1 at $(Get-Date -Format s)."
if (Get-ScheduledTask -TaskName 'CompaBot' -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName 'CompaBot' }
$procs = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -like 'powershell*' -and $_.CommandLine -like '*Start-CompaBot.ps1*') -or
    ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*Compabot.py*') }
# Stop the restart loop first so it cannot start the bot again.
$procs | Sort-Object { $_.Name -ne 'python.exe' } -Descending | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($procs) { Write-Host 'Compabot stopped.' } else { Write-Host 'Compabot was not running.' }
