<#
.SYNOPSIS
Update laanp's Icarus mods (such as Pete's Beacon Teleport) in your own Icarus game install.

.DESCRIPTION
The game-client side of CompaBot's /server mods. It finds Icarus through Steam, checks each mod
in Icarus\Content\Paks\mods named like laanp's (laanp-PetesBeaconTeleport_v1_w252_P.pak) against
laanp's mod list (github.com/laanp/Icarus_Mods_Separated), and picks a release the same way the
server does: the latest one, unless its readme says it was made for a newer game build than
yours. The new file is checked to be a real .pak before the old one is moved to the backup
folder (the last 5 per mod are kept). If no laanp mod is installed yet, Pete's Beacon Teleport
is installed. Nothing is changed while Icarus is running.

Needs only Windows PowerShell; double-click Update-IcarusClientMods.cmd to run it.

.PARAMETER GamePath
The Icarus install folder (the one holding Icarus.exe). Found through Steam when left out.

.PARAMETER CheckOnly
Say what would be updated without downloading or moving anything.

.PARAMETER BackupPath
Where old mod files go. Defaults to %LOCALAPPDATA%\IcarusModBackups. It must not be inside the
game's Paks folder, or the game would load the old file too.
#>
[CmdletBinding()]
param(
    [string]$GamePath,
    [switch]$CheckOnly,
    [string]$BackupPath = (Join-Path $env:LOCALAPPDATA 'IcarusModBackups')
)

$ErrorActionPreference = 'Stop'

$IcarusAppId = '1149460'
$ModInfoUrl = 'https://raw.githubusercontent.com/laanp/Icarus_Mods_Separated/main/modinfo.json'
$DefaultMod = 'laanp-PetesBeaconTeleport'
# Same patterns as bot/icarus/mod_update.py.
$PakName = '^(?<name>.+)_v(?<fix>\d+)_w(?<week>\d+)_P\.pak$'
$ReadmeRev = 'Rev\.\s*(?<rev>\d+(?:\.\d+){3})\s*\(Week:\s*(?<week>\d+)\)'
$PakMagic = [byte[]](0xe1, 0x12, 0x6f, 0x5a)
$OldCopies = 5

function Get-SteamLibraries {
    $roots = @()
    foreach ($key in 'HKCU:\Software\Valve\Steam', 'HKLM:\SOFTWARE\WOW6432Node\Valve\Steam', 'HKLM:\SOFTWARE\Valve\Steam') {
        $item = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
        if ($item) {
            foreach ($value in $item.SteamPath, $item.InstallPath) {
                if ($value) { $roots += ($value -replace '/', '\') }
            }
        }
    }
    $roots += "${env:ProgramFiles(x86)}\Steam"
    $libraries = @()
    foreach ($root in ($roots | Select-Object -Unique)) {
        $libraries += $root
        $vdf = Join-Path $root 'steamapps\libraryfolders.vdf'
        if (Test-Path -LiteralPath $vdf) {
            foreach ($match in [regex]::Matches((Get-Content -LiteralPath $vdf -Raw), '"path"\s+"(?<path>[^"]+)"')) {
                $libraries += ($match.Groups['path'].Value -replace '\\\\', '\')
            }
        }
    }
    $libraries | Select-Object -Unique
}

function Find-Icarus {
    foreach ($library in Get-SteamLibraries) {
        $manifest = Join-Path $library "steamapps\appmanifest_$IcarusAppId.acf"
        if (Test-Path -LiteralPath $manifest) {
            $dir = 'Icarus'
            $match = [regex]::Match((Get-Content -LiteralPath $manifest -Raw), '"installdir"\s+"(?<dir>[^"]+)"')
            if ($match.Success) { $dir = $match.Groups['dir'].Value }
            $path = Join-Path $library "steamapps\common\$dir"
            if (Test-Path -LiteralPath $path) { return $path }
        }
    }
    $null
}

function Get-GameVersion([string]$root) {
    # The client keeps its build in the same place as the dedicated server.
    $file = Join-Path $root 'Icarus\Config\version.json'
    try {
        $v = (Get-Content -LiteralPath $file -Raw -ErrorAction Stop | ConvertFrom-Json).Version
        if ($null -eq $v.Changelist) { return $null }
        "$($v.Major).$($v.Minor).$($v.Patch).$($v.Changelist)"
    } catch { $null }
}

function ConvertTo-VersionKey([string]$rev) { [version]$rev }

function Read-Url([string]$url) {
    (Invoke-WebRequest -Uri $url -UseBasicParsing -Headers @{ 'User-Agent' = 'Compabot' } -TimeoutSec 60).Content
}

function Get-BuiltFor($entry) {
    # Read github.com/<owner>/<repo>/raw/<path> links straight from raw.githubusercontent.com.
    $url = [string]$entry.readmeURL -replace '^https://github\.com/([^/]+/[^/]+)/(?:raw|blob)/', 'https://raw.githubusercontent.com/$1/'
    if (-not $url) { return $null }
    try {
        $match = [regex]::Match((Read-Url $url), $ReadmeRev, 'IgnoreCase')
    } catch {
        Write-Warning "Could not read $url : $($_.Exception.Message)"
        return $null
    }
    if ($match.Success) { [pscustomobject]@{ Rev = $match.Groups['rev'].Value; Week = [int]$match.Groups['week'].Value } }
}

function Test-Pak([string]$path) {
    # UE4 .pak files end with a footer holding this magic number.
    $stream = [IO.File]::OpenRead($path)
    try {
        if ($stream.Length -le 1024) { return $false }
        $null = $stream.Seek(-1024, 'End')
        $tail = New-Object byte[] 1024
        $read = $stream.Read($tail, 0, 1024)
        [BitConverter]::ToString($tail, 0, $read).Contains([BitConverter]::ToString($PakMagic))
    } finally {
        $stream.Dispose()
    }
}

function Save-Pak([string]$url, [string]$target) {
    $part = "$target.download"
    try {
        Invoke-WebRequest -Uri $url -OutFile $part -UseBasicParsing -Headers @{ 'User-Agent' = 'Compabot' } -TimeoutSec 300
        if (-not (Test-Pak $part)) { throw "$(Split-Path $target -Leaf) did not download as a valid .pak file." }
        Move-Item -LiteralPath $part -Destination $target -Force
    } finally {
        Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue
    }
}

function Move-ToBackup([string]$path, [string]$name) {
    New-Item -ItemType Directory -Path $BackupPath -Force | Out-Null
    Move-Item -LiteralPath $path -Destination (Join-Path $BackupPath (Split-Path $path -Leaf)) -Force
    # Newest first, ties broken by version so the file just moved is kept.
    Get-ChildItem -LiteralPath $BackupPath -Filter '*.pak' |
        Where-Object { $_.Name -match $PakName -and $Matches['name'] -eq $name } |
        Sort-Object @{ e = { $_.LastWriteTimeUtc } }, @{ e = { $null = $_.Name -match $PakName; [int]$Matches['week'] } }, @{ e = { $null = $_.Name -match $PakName; [int]$Matches['fix'] } } -Descending |
        Select-Object -Skip $OldCopies |
        Remove-Item -Force
}

function Get-Release($index, [string]$name) {
    $entry = $index[$name.ToLower()]
    if (-not $entry) { return $null }
    $url = [string]$entry.files.pak
    $file = [IO.Path]::GetFileName(([uri]$url).AbsolutePath)
    $match = [regex]::Match($file, $PakName, 'IgnoreCase')
    if (-not $match.Success) { return $null }
    [pscustomobject]@{ Entry = $entry; Url = $url; File = $file; Week = [int]$match.Groups['week'].Value; Fix = [int]$match.Groups['fix'].Value }
}

function Invoke-ModUpdate {
    if (-not $GamePath) {
        $GamePath = Find-Icarus
        if (-not $GamePath) { throw 'Could not find Icarus in your Steam libraries. Run again with -GamePath "<folder holding Icarus.exe>".' }
    }
    $modsDir = Join-Path $GamePath 'Icarus\Content\Paks\mods'
    if (-not (Test-Path -LiteralPath (Join-Path $GamePath 'Icarus\Content\Paks'))) {
        throw "$GamePath does not look like an Icarus install (no Icarus\Content\Paks folder)."
    }
    Write-Host "Icarus: $GamePath"

    $running = Get-Process -Name 'Icarus', 'Icarus-Win64-Shipping' -ErrorAction SilentlyContinue
    if ($running -and -not $CheckOnly) { throw 'Icarus is running. Close the game, then run this again.' }

    $installed = @(Get-ChildItem -LiteralPath $modsDir -Filter '*.pak' -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match $PakName } |
        ForEach-Object { [pscustomobject]@{ Name = $Matches['name']; Week = [int]$Matches['week']; Fix = [int]$Matches['fix']; Path = $_.FullName } })

    $index = @{}
    try {
        foreach ($mod in (Read-Url $ModInfoUrl | ConvertFrom-Json).mods) { $index[$mod.name.ToLower()] = $mod }
    } catch {
        throw "Could not read the mod list from GitHub: $($_.Exception.Message)"
    }
    $game = Get-GameVersion $GamePath
    if ($game) { Write-Host "Game build: $game" } else { Write-Warning 'Could not read the game build, so mod releases are not checked against it.' }

    if (-not $installed) {
        Write-Host "No laanp mods installed yet, so $DefaultMod will be installed."
        $installed = @([pscustomobject]@{ Name = $DefaultMod; Week = 0; Fix = 0; Path = $null })
    }

    foreach ($mod in $installed) {
        $release = Get-Release $index $mod.Name
        $current = if ($mod.Path) { "w$($mod.Week) v$($mod.Fix)" } else { 'not installed' }
        if (-not $release) {
            Write-Host "$($mod.Name): not in laanp's mod list, left as is."
            continue
        }
        $builtFor = Get-BuiltFor $release.Entry
        if ($builtFor -and $game -and (ConvertTo-VersionKey $builtFor.Rev) -gt (ConvertTo-VersionKey $game)) {
            Write-Host "$($mod.Name): the latest release (w$($release.Week)) needs game $($builtFor.Rev), but you have $game. Kept $current; update the game in Steam first."
            continue
        }
        if (($release.Week -gt $mod.Week) -or ($release.Week -eq $mod.Week -and $release.Fix -gt $mod.Fix)) {
            $change = "$current to w$($release.Week) v$($release.Fix)"
            if ($CheckOnly) {
                Write-Host "$($mod.Name): update available, $change."
            } else {
                New-Item -ItemType Directory -Path $modsDir -Force | Out-Null
                $target = Join-Path $modsDir $release.File
                Save-Pak $release.Url $target
                if ($mod.Path -and $mod.Path -ne $target) { Move-ToBackup $mod.Path $mod.Name }
                if ($mod.Path) {
                    Write-Host "$($mod.Name): updated $change (old file kept in $BackupPath)." -ForegroundColor Green
                } else {
                    Write-Host "$($mod.Name): installed w$($release.Week) v$($release.Fix)." -ForegroundColor Green
                }
            }
        } else {
            Write-Host "$($mod.Name): $current is the latest release."
        }
        if ($builtFor -and $game -and (ConvertTo-VersionKey $builtFor.Rev) -lt (ConvertTo-VersionKey $game)) {
            Write-Warning "$($mod.Name) was made for game $($builtFor.Rev) (week $($builtFor.Week)) but you have $game. laanp has not released it for this game update yet; it may not load until they do."
        }
    }
}

# Windows PowerShell 5.1 does not offer TLS 1.2 to GitHub by default.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
# Invoke-WebRequest's progress bar makes big downloads very slow in Windows PowerShell.
$ProgressPreference = 'SilentlyContinue'

if ($MyInvocation.InvocationName -ne '.') {
    try {
        Invoke-ModUpdate
    } catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        exit 1
    }
}
