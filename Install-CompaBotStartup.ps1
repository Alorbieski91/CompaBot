# Creates two Task Scheduler tasks: CompaBot starts Compabot whenever you sign in to Windows, so it
# comes back after Windows Update restarts, and CompaBot Watchdog runs Watch-CompaBot.ps1 every
# 5 minutes to pull new code from GitHub and start Compabot again if it is down. Run it once from a
# normal PowerShell window in this folder:
#   powershell -ExecutionPolicy Bypass -File .\Install-CompaBotStartup.ps1
# Add -Remove to delete both tasks. The tasks run as you, in your session, because /server stop
# needs to reach the Icarus server's console window.
[CmdletBinding()]
param([switch]$Remove)

$ErrorActionPreference = 'Stop'
$name = 'CompaBot'
$watchdog = 'CompaBot Watchdog'
$user = "$env:USERDOMAIN\$env:USERNAME"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $watchdog -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $watchdog -Confirm:$false
        Write-Host "Removed the $watchdog task."
    }
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false
        Write-Host "Removed the $name startup task. Compabot will no longer start at sign-in."
    } else { Write-Host "There is no $name startup task." }
    exit 0
}

$launcher = Join-Path $PSScriptRoot 'Start-CompaBot.ps1'
$python = & $launcher -FindPython
$ErrorActionPreference = 'Continue'  # so the import check's error text doesn't stop the script
& $python -c 'import discord, dotenv' 2>$null
$ErrorActionPreference = 'Stop'
if ($LASTEXITCODE -ne 0) {
    throw "$python is missing Compabot's packages. Run: & '$python' -m pip install -r requirements.txt"
}
Write-Host "Using $python"
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

# The watchdog runs every 5 minutes from now on, while you are signed in.
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    Write-Warning 'Git is not on your PATH, so the watchdog will keep Compabot running but cannot pull updates.'
}
$watcher = Join-Path $PSScriptRoot 'Watch-CompaBot.ps1'
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$watcher`""
# A hidden PowerShell window still flashes up briefly. On Windows 11, conhost --headless avoids that.
if ([Environment]::OSVersion.Version.Build -ge 22000) {
    $action = New-ScheduledTaskAction -Execute 'conhost.exe' -WorkingDirectory $PSScriptRoot `
        -Argument "--headless powershell.exe $arguments"
} else {
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $PSScriptRoot -Argument $arguments
}
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName $watchdog -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description 'Every 5 minutes, pulls new Compabot code from GitHub and starts Compabot if it is down.' -Force | Out-Null
Write-Host "Created the $watchdog task. It runs every 5 minutes; its log is logs\watchdog.log."
Write-Host 'Done. Logs are in the logs folder. Use .\Stop-CompaBot.ps1 to stop it.'
