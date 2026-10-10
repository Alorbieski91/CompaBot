# Keeps Compabot running and up to date. The CompaBot Watchdog task (see Install-CompaBotStartup.ps1)
# runs this every 5 minutes. Each run it:
#   1. pulls new commits from origin/main, installs requirements.txt if it changed, runs the tests,
#      and restarts Compabot on the new code. If any of that fails, or Compabot doesn't sign in to
#      Discord, it goes back to the previous commit and won't try that commit again;
#   2. starts Compabot again if neither it nor its restart loop (Start-CompaBot.ps1) is running.
# It does nothing while Compabot is stopped on purpose (Stop-CompaBot.ps1, or a clean exit).
# Its log is logs\watchdog.log. Set WATCHDOG_WEBHOOK_URL in .env to a Discord webhook to also be
# told there, which works even while Compabot is down.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location $PSScriptRoot

$branch = 'main'
$logs = Join-Path $PSScriptRoot 'logs'
$botLog = Join-Path $logs 'compabot.log'
$prevLog = Join-Path $logs 'compabot.prev.log'
$stoppedFlag = Join-Path $logs 'compabot.stopped'
$watchLog = Join-Path $logs 'watchdog.log'
$stateFile = Join-Path $logs 'watchdog.json'
$startScript = Join-Path $PSScriptRoot 'Start-CompaBot.ps1'
$stopScript = Join-Path $PSScriptRoot 'Stop-CompaBot.ps1'
# How long Compabot gets to sign in to Discord after a restart.
$loginTimeout = 120
# Restarts after a crash, at most this many an hour, so a bot that can't stay up isn't restarted forever.
$maxRestarts = 3
New-Item -ItemType Directory -Force $logs | Out-Null

function Write-Log([string]$text) {
    if ((Test-Path $watchLog) -and (Get-Item $watchLog).Length -gt 1MB) {
        Move-Item $watchLog (Join-Path $logs 'watchdog.prev.log') -Force
    }
    Add-Content $watchLog "$(Get-Date -Format s) $text"
}

function Get-WebhookUrl {
    $file = Join-Path $PSScriptRoot '.env'
    if (-not (Test-Path $file)) { return $null }
    foreach ($line in Get-Content $file) {
        if ($line -match '^\s*WATCHDOG_WEBHOOK_URL\s*=\s*(.*?)\s*$') { return $Matches[1].Trim('"', "'") }
    }
    $null
}

# Logs a message and posts it to the webhook. With a key, an ongoing problem is reported only the first
# time it is seen; Clear-Notice forgets it once it is over.
function Send-Notice([string]$text, [string]$key) {
    if ($key) {
        if ($state.notices.$key -eq $text) { return }
        $state.notices | Add-Member -NotePropertyName $key -NotePropertyValue $text -Force
    }
    Write-Log $text
    $url = Get-WebhookUrl
    if (-not $url) { return }
    if ($text.Length -gt 1900) { $text = $text.Substring(0, 1900) + '...' }
    try {
        $body = @{ content = $text; allowed_mentions = @{ parse = @() } } | ConvertTo-Json -Compress
        Invoke-RestMethod -Method Post -Uri $url -ContentType 'application/json; charset=utf-8' `
            -Body ([Text.Encoding]::UTF8.GetBytes($body)) | Out-Null
    } catch { Write-Log "Could not post to the webhook: $_" }
}

function Clear-Notice([string]$key) {
    if ($state.notices.PSObject.Properties[$key]) { $state.notices.PSObject.Properties.Remove($key) }
}

function Save-State { $state | ConvertTo-Json -Depth 4 | Set-Content $stateFile -Encoding UTF8 }

# Runs a program and returns its exit code and output, without stopping on the progress text that
# git and pip write to stderr (Windows PowerShell treats that as an error under 'Stop').
function Invoke-Native([string]$file, [string[]]$arguments) {
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $out = & $file @arguments 2>&1 | ForEach-Object { "$_" } }
    finally { $ErrorActionPreference = $old }
    [pscustomobject]@{ Code = $LASTEXITCODE; Output = (($out | Where-Object { $_ }) -join "`n").Trim() }
}

function Get-Tail([string]$path, [int]$lines = 15) {
    if (-not (Test-Path $path)) { return '' }
    ((Get-Content $path -Tail $lines) -join "`n").Trim()
}

function Format-Block([string]$text, [int]$limit = 1200) {
    if (-not $text) { return '' }
    if ($text.Length -gt $limit) { $text = '...' + $text.Substring($text.Length - $limit) }
    $fence = '```'
    "`n$fence`n$($text.Replace($fence, "'''"))`n$fence"
}

function Get-BotProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        ($_.Name -eq 'python.exe' -and $_.CommandLine -like '*Compabot.py*') -or
        ($_.Name -like 'powershell*' -and $_.CommandLine -like '*Start-CompaBot.ps1*') }
}

# An Icarus script or SteamCMD started by Compabot keeps running if Compabot is stopped, so wait for
# it to finish before restarting the bot for an update.
function Test-ServerWorkRunning {
    [bool](Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'steamcmd.exe' -or
        ($_.Name -like 'powershell*' -and $_.CommandLine -like '*IcarusServer.ps1*') })
}

# Stops Compabot if it is running, starts it again, and waits for it to sign in to Discord.
# Returns $true once the bot log says it has.
function Restart-Bot {
    & $stopScript | Out-Null
    Remove-Item $stoppedFlag -ErrorAction SilentlyContinue  # Stop-CompaBot.ps1 marks a stop as on purpose
    # Start-CompaBot.ps1 keeps the previous run's log as compabot.prev.log; doing that here instead
    # means any compabot.log that appears next belongs to the new run.
    if (Test-Path $botLog) { Move-Item $botLog $prevLog -Force }
    if (Get-ScheduledTask -TaskName 'CompaBot' -ErrorAction SilentlyContinue) {
        Start-ScheduledTask -TaskName 'CompaBot'
    } else {
        Write-Log 'The CompaBot task is missing, so Start-CompaBot.ps1 was started directly. Run Install-CompaBotStartup.ps1 to recreate it.'
        Start-Process powershell.exe -WindowStyle Hidden -WorkingDirectory $PSScriptRoot `
            -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$startScript`""
    }
    $deadline = (Get-Date).AddSeconds($loginTimeout)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        if (-not (Test-Path $botLog)) { continue }
        $text = Get-Content $botLog -Raw -ErrorAction SilentlyContinue
        if ($text -match 'Logged in as') { return $true }
        if ($text -match 'Compabot exited with code') { return $false }
    }
    $false
}

# Start-CompaBot.ps1 only stops restarting Compabot when it exits normally (code 0), which means it
# was shut down on purpose.
function Test-ShutDownOnPurpose {
    (Get-Content $botLog -Tail 1 -ErrorAction SilentlyContinue) -match 'exited with code 0\.'
}

function Short([string]$commit) { $commit.Substring(0, 7) }

# Returns $true if it restarted Compabot.
function Update-Bot {
    if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
        Send-Notice 'Watchdog: Git is not on the PATH, so I can not check for Compabot updates.' 'git'
        return $false
    }
    Clear-Notice 'git'
    $current = (Invoke-Native git.exe @('rev-parse', '--abbrev-ref', 'HEAD')).Output
    if ($current -ne $branch) {
        Send-Notice "Watchdog: the Compabot folder is on branch ``$current``, not ``$branch``, so I am not updating it." 'branch'
        return $false
    }
    Clear-Notice 'branch'
    $fetch = Invoke-Native git.exe @('fetch', '--quiet', 'origin', $branch)
    if ($fetch.Code -ne 0) {
        # Usually the network or GitHub; the next run tries again.
        Write-Log "git fetch failed: $($fetch.Output)"
        return $false
    }
    $head = (Invoke-Native git.exe @('rev-parse', 'HEAD')).Output
    $remote = (Invoke-Native git.exe @('rev-parse', "origin/$branch")).Output
    if ($head -eq $remote -or $remote -eq $state.badCommit) { return $false }
    if ((Invoke-Native git.exe @('merge-base', '--is-ancestor', 'HEAD', "origin/$branch")).Code -ne 0) {
        Send-Notice "Watchdog: the Compabot folder has commits that are not on GitHub's $branch, so I can not update it. Sort it out with git, then I will pick up new commits again." 'diverged'
        return $false
    }
    Clear-Notice 'diverged'
    $dirty = (Invoke-Native git.exe @('status', '--porcelain', '--untracked-files=no')).Output
    if ($dirty) {
        Send-Notice "Watchdog: Compabot has an update on GitHub, but these files were changed on this PC, so I left it alone:$(Format-Block $dirty)" 'dirty'
        return $false
    }
    Clear-Notice 'dirty'
    if (Test-ServerWorkRunning) {
        Write-Log 'Update waiting: an Icarus script or SteamCMD is running.'
        return $false
    }

    $changes = (Invoke-Native git.exe @('log', '--oneline', '--no-decorate', "HEAD..origin/$branch")).Output
    Write-Log "Updating Compabot from $(Short $head) to $(Short $remote)"
    $merge = Invoke-Native git.exe @('merge', '--ff-only', '--quiet', "origin/$branch")
    if ($merge.Code -ne 0) {
        Send-Notice "Watchdog: pulling Compabot $(Short $remote) failed, so it is still on $(Short $head).$(Format-Block $merge.Output)"
        $state.badCommit = $remote
        return $false
    }

    $python = & $startScript -FindPython
    $failure = $null
    if ((Invoke-Native git.exe @('diff', '--quiet', $head, 'HEAD', '--', 'requirements.txt')).Code -ne 0) {
        $pip = Invoke-Native $python @('-m', 'pip', 'install', '--disable-pip-version-check', '-q', '-r', 'requirements.txt')
        if ($pip.Code -ne 0) { $failure = "Installing requirements.txt failed.$(Format-Block $pip.Output)" }
    }
    if (-not $failure) {
        $tests = Invoke-Native $python @('-m', 'unittest')
        if ($tests.Code -ne 0) { $failure = "The tests failed.$(Format-Block $tests.Output)" }
    }
    if (-not $failure -and -not (Restart-Bot)) {
        $failure = "Compabot did not sign in to Discord on the new code.$(Format-Block (Get-Tail $botLog))"
    }
    if (-not $failure) {
        Send-Notice "Watchdog: updated Compabot from $(Short $head) to $(Short $remote) and restarted it.$(Format-Block $changes)"
        return $true
    }

    # Go back to the commit that was working. The working tree was clean before the pull, so this
    # only undoes the pull.
    & $stopScript | Out-Null
    Invoke-Native git.exe @('reset', '--hard', '--quiet', $head) | Out-Null
    $state.badCommit = $remote
    $back = if (Restart-Bot) { "Compabot is running $(Short $head) again." }
            else { "Compabot also did not come back on $(Short $head), so it needs a look.$(Format-Block (Get-Tail $botLog))" }
    Send-Notice "Watchdog: I tried to update Compabot to $(Short $remote), but it did not work, so I went back to $(Short $head). I won't try $(Short $remote) again; a newer commit will be tried as usual.`n$failure`n$back"
    $true
}

function Watch-Bot {
    if (Get-BotProcesses) {
        Clear-Notice 'down'
        return
    }
    if (Test-ShutDownOnPurpose) {
        Send-Notice 'Watchdog: Compabot shut down normally, so I am leaving it off. Start it with Start-ScheduledTask CompaBot.' 'down'
        return
    }
    $hourAgo = (Get-Date).AddHours(-1)
    $state.restarts = @($state.restarts | Where-Object { [datetime]$_ -gt $hourAgo })
    if ($state.restarts.Count -ge $maxRestarts) {
        Send-Notice "Watchdog: Compabot is down and I have already restarted it $maxRestarts times in the last hour, so I am waiting before trying again.$(Format-Block (Get-Tail $botLog))" 'down'
        return
    }
    $state.restarts += (Get-Date -Format o)
    $crash = Get-Tail $botLog
    if (Restart-Bot) {
        Clear-Notice 'down'
        Send-Notice "Watchdog: Compabot was not running, so I started it again. The end of its last log:$(Format-Block $crash)"
    } else {
        Send-Notice "Watchdog: Compabot was not running, and it did not sign in to Discord when I started it again.$(Format-Block (Get-Tail $botLog))" 'down'
    }
}

$state = [pscustomobject]@{ badCommit = $null; restarts = @(); notices = [pscustomobject]@{} }
if (Test-Path $stateFile) {
    try {
        $saved = Get-Content $stateFile -Raw | ConvertFrom-Json
        foreach ($name in 'badCommit', 'restarts', 'notices') {
            if ($null -ne $saved.$name) { $state.$name = $saved.$name }
        }
        $state.restarts = @($state.restarts)
    } catch { Write-Log "Starting with a fresh state: could not read $stateFile ($_)" }
}

try {
    if (Test-Path $stoppedFlag) { return }  # stopped with Stop-CompaBot.ps1; Start-CompaBot.ps1 clears it
    $running = [bool](Get-BotProcesses)
    $restarted = $false
    # Leave a bot that was shut down on purpose off, even when there is new code.
    if ($running -or -not (Test-ShutDownOnPurpose)) {
        try {
            $restarted = Update-Bot
            Clear-Notice 'update-error'
        } catch { Send-Notice "Watchdog: the update check failed: $_" 'update-error' }
    }
    if (-not $restarted) { Watch-Bot }
} catch {
    Write-Log "Watchdog run failed: $_"
} finally {
    Save-State
}
