# Stops Compabot and its restart loop (Start-CompaBot.ps1), however it was started.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if (Get-ScheduledTask -TaskName 'CompaBot' -ErrorAction SilentlyContinue) { Stop-ScheduledTask -TaskName 'CompaBot' }
$procs = Get-CimInstance Win32_Process | Where-Object {
    ($_.Name -like 'powershell*' -and $_.CommandLine -like '*Start-CompaBot.ps1*') -or
    ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*Compabot.py*') }
# Stop the restart loop first so it cannot start the bot again.
$procs | Sort-Object { $_.Name -ne 'python.exe' } -Descending | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
if ($procs) { Write-Host 'Compabot stopped.' } else { Write-Host 'Compabot was not running.' }
