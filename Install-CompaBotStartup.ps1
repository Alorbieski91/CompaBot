# Creates a Task Scheduler task that starts Compabot whenever you sign in to Windows, so it comes
# back after Windows Update restarts. Run it once from a normal PowerShell window in this folder:
#   powershell -ExecutionPolicy Bypass -File .\Install-CompaBotStartup.ps1
# Add -Remove to delete the task. The task runs as you, in your session, because /server stop
# needs to reach the Icarus server's console window.
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$name = 'CompaBot'
$user = "$env:USERDOMAIN\$env:USERNAME"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed the $name startup task. Compabot will no longer start at sign-in."
    } else { Write-Host "There is no $name startup task." }
    exit 0
}

$launcher = Join-Path $PSScriptRoot 'Start-CompaBot.ps1'
if (-not (Test-Path (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))) {
    throw 'Python environment not found in .venv. Follow the README setup first.'
}
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $PSScriptRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
# The short delay lets the network come up after sign-in.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = 'PT30S'
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Starts Compabot at sign-in and restarts it if it crashes.' -Force | Out-Null
Write-Host "Created the $name startup task for $user. Starting Compabot now..."
Start-ScheduledTask -TaskName $name
Write-Host 'Done. Logs are in the logs folder. Use .\Stop-CompaBot.ps1 to stop it.'
