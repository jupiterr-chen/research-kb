<#
.SYNOPSIS
    Install and configure the Windows side of the Research KB: Syncthing client
    and Obsidian, paired to the Linux server, with user-level startup.

.DESCRIPTION
    Downloads official releases only, verifies the Syncthing SHA256 when the
    release publishes a digest, configures a sendreceive folder pointing at the
    user Vault, registers the Vault with Obsidian, and creates a per-user
    startup shortcut (no admin required). Idempotent; never deletes anything.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$ServerHost,
    [Parameter(Mandatory = $true)][string]$ServerDeviceId,
    [Parameter(Mandatory = $true)][string]$VaultPath,
    [Parameter(Mandatory = $true)][string]$ToolsDir,
    [int]$GuiPort = 18384,
    [string]$RepoDir = "",
    [switch]$SkipObsidian,
    [switch]$SkipSyncthing
)

$ErrorActionPreference = "Stop"
if (-not $RepoDir) {
    if ($PSScriptRoot) { $RepoDir = Split-Path -Parent $PSScriptRoot }
    else { $RepoDir = (Get-Location).Path }
}
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$LogDir = Join-Path $ToolsDir "logs"
New-Item -ItemType Directory -Force -Path $ToolsDir, $LogDir | Out-Null
$LogFile = Join-Path $LogDir "install-windows.log"

function Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"), $Message
    Write-Host $line
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
}

function Download-File([string]$Uri, [string]$OutFile) {
    Log "download $Uri -> $OutFile"
    Invoke-WebRequest -Uri $Uri -OutFile $OutFile -UseBasicParsing -TimeoutSec 120
}

function Get-Sha256([string]$Path) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try { $hash = $sha.ComputeHash($stream) } finally { $stream.Dispose(); $sha.Dispose() }
    return ([BitConverter]::ToString($hash)).Replace("-", "").ToLower()
}

$state = [ordered]@{
    generated_at = (Get-Date).ToString("o")
    server_host  = $ServerHost
    vault_path   = $VaultPath
    tools_dir    = $ToolsDir
}

# ---------------------------------------------------------------- Syncthing ---
if (-not $SkipSyncthing) {
    $api = "https://api.github.com/repos/syncthing/syncthing/releases/latest"
    Log "querying Syncthing latest release"
    $release = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "research-kb" } -TimeoutSec 60
    $asset = $release.assets | Where-Object { $_.name -match 'syncthing-windows-amd64-.*\.zip$' } | Select-Object -First 1
    if (-not $asset) { throw "no windows-amd64 zip asset in latest syncthing release" }
    $syncthingVersion = $release.tag_name
    $downloadDir = Join-Path $ToolsDir "downloads"
    New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
    $zipPath = Join-Path $downloadDir $asset.name
    if (-not (Test-Path -LiteralPath $zipPath)) {
        Download-File $asset.browser_download_url $zipPath
    }
    if ($asset.digest -and $asset.digest -match '^sha256:(.+)$') {
        $expected = $Matches[1].ToLower()
        $actual = Get-Sha256 $zipPath
        if ($actual -ne $expected) { throw "syncthing checksum mismatch: $actual != $expected" }
        Log "syncthing sha256 verified ($expected)"
    } else {
        Log "WARN: release publishes no digest; checksum not verified"
    }
    $syncthingDir = Join-Path $ToolsDir "syncthing"
    New-Item -ItemType Directory -Force -Path $syncthingDir | Out-Null
    $exe = Get-ChildItem -Path $syncthingDir -Recurse -Filter "syncthing.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $exe) {
        Log "extracting syncthing zip"
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $syncthingDir)
        $exe = Get-ChildItem -Path $syncthingDir -Recurse -Filter "syncthing.exe" | Select-Object -First 1
    }
    if (-not $exe) { throw "syncthing.exe not found after extraction" }
    $exePath = $exe.FullName
    $homeDir = Join-Path $syncthingDir "config"
    $state.syncthing = @{ version = $syncthingVersion; exe = $exePath; home = $homeDir }

    if (-not (Test-Path -LiteralPath (Join-Path $homeDir "config.xml"))) {
        Log "generating syncthing config"
        & $exePath generate "--home=$homeDir" *>&1 | Tee-Object -FilePath $LogFile -Append
    }

    # Windows reserves the 8328-8427 range, so use a fixed clean loopback port.
    $guiUrl = "http://127.0.0.1:$GuiPort"
    Log "using syncthing GUI address: $guiUrl"

    # ensure it is running
    $running = Get-Process -Name syncthing -ErrorAction SilentlyContinue
    if (-not $running) {
        Log "starting syncthing hidden"
        Start-Process -FilePath $exePath -ArgumentList @("serve", "--home=$homeDir", "--no-browser", "--gui-address=127.0.0.1:$GuiPort") -WindowStyle Hidden
    }
    Start-Sleep -Seconds 8

    $configure = Join-Path $RepoDir "scripts\syncthing_configure.py"
    $ignoreFile = Join-Path $RepoDir "config\stignore"
    Log "configuring syncthing folder -> $VaultPath"
    python $configure --home $homeDir --api-url $guiUrl `
        --folder-path $VaultPath --peer $ServerDeviceId `
        --peer-address "tcp://$($ServerHost):22000" `
        --set-gui "127.0.0.1:$GuiPort" --ignore-file $ignoreFile --restart
    Start-Sleep -Seconds 8

    # read our device id for the server side
    $myDeviceId = (& $exePath device-id "--home=$homeDir" 2>$null | Where-Object { $_ -match '\S' } | Select-Object -First 1)
    $state.syncthing.device_id = "$myDeviceId".Trim()
    Log "windows syncthing device id: $($state.syncthing.device_id)"

    # user-level startup shortcut
    $startup = [Environment]::GetFolderPath("Startup")
    $vbsPath = Join-Path $ToolsDir "start-syncthing.vbs"
    $vbs = @"
Set sh = CreateObject("WScript.Shell")
sh.Run """$exePath"" serve --home=""$homeDir"" --no-browser --gui-address=127.0.0.1:$GuiPort", 0, False
"@
    Set-Content -LiteralPath $vbsPath -Value $vbs -Encoding ASCII
    $lnkPath = Join-Path $startup "ResearchKB-Syncthing.lnk"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut($lnkPath)
    $lnk.TargetPath = $vbsPath
    $lnk.WorkingDirectory = $ToolsDir
    $lnk.Description = "Research KB Syncthing (hidden, user startup)"
    $lnk.Save()
    $state.syncthing.startup_shortcut = $lnkPath
    Log "startup shortcut created: $lnkPath"
}

# ------------------------------------------------------------------ Obsidian ---
if (-not $SkipObsidian) {
    $existing = Get-Command Obsidian -ErrorAction SilentlyContinue
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Obsidian\Obsidian.exe"),
        (Join-Path $env:LOCALAPPDATA "Programs\Obsidian\Obsidian.exe"),
        (Join-Path ${env:ProgramFiles} "Obsidian\Obsidian.exe")
    )
    $obsidianExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $obsidianExe) {
        $api = "https://api.github.com/repos/obsidianmd/obsidian-releases/releases?per_page=20"
        Log "querying Obsidian releases for a desktop installer"
        $releases = Invoke-RestMethod -Uri $api -Headers @{ "User-Agent" = "research-kb" } -TimeoutSec 60
        $release = $null; $asset = $null
        foreach ($candidate in $releases) {
            $exe = $candidate.assets | Where-Object { $_.name -match '^Obsidian-.*\.exe$' } | Select-Object -First 1
            if ($exe) { $release = $candidate; $asset = $exe; break }
        }
        if (-not $asset) { throw "no Obsidian desktop installer asset found" }
        $downloadDir = Join-Path $ToolsDir "downloads"
        New-Item -ItemType Directory -Force -Path $downloadDir | Out-Null
        $installer = Join-Path $downloadDir $asset.name
        if (-not (Test-Path -LiteralPath $installer)) {
            Download-File $asset.browser_download_url $installer
        }
        Log "running Obsidian silent install: $installer"
        $proc = Start-Process -FilePath $installer -ArgumentList "/S" -PassThru -Wait
        Log "installer exit code: $($proc.ExitCode)"
        Start-Sleep -Seconds 5
        $obsidianExe = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        $state.obsidian = @{ version = $release.tag_name; installer = $installer }
    } else {
        Log "Obsidian already installed at $obsidianExe"
    }
    $state.obsidian_exe = $obsidianExe

    # register the vault in obsidian.json (merge; never drop other vaults)
    $cfgDir = Join-Path $env:APPDATA "obsidian"
    New-Item -ItemType Directory -Force -Path $cfgDir | Out-Null
    $cfgFile = Join-Path $cfgDir "obsidian.json"
    $vaults = @{}
    if (Test-Path -LiteralPath $cfgFile) {
        try {
            $obj = Get-Content -LiteralPath $cfgFile -Raw | ConvertFrom-Json
            foreach ($p in $obj.vaults.PSObject.Properties) { $vaults[$p.Name] = $p.Value }
        } catch { Log "WARN: could not parse existing obsidian.json; preserving as-is" }
    }
    $found = $false
    foreach ($k in @($vaults.Keys)) {
        if ($vaults[$k].path -eq $VaultPath) { $vaults[$k].open = $true; $found = $true }
    }
    if (-not $found) {
        $vaultId = -join (1..16 | ForEach-Object { "{0:x}" -f (Get-Random -Maximum 16) })
        $ts = [int64](((Get-Date).ToUniversalTime()) - ([datetime]'1970-01-01T00:00:00Z')).TotalMilliseconds
        $vaults[$vaultId] = @{ path = $VaultPath; ts = $ts; open = $true }
        @{ vaults = $vaults } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $cfgFile -Encoding UTF8
        Log "registered vault in obsidian.json"
    } else {
        @{ vaults = $vaults } | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $cfgFile -Encoding UTF8
        Log "vault already registered in obsidian.json"
    }
    $state.obsidian_config = $cfgFile
}

$stateFile = Join-Path $ToolsDir "install-state.json"
$state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $stateFile -Encoding UTF8
Log "state written to $stateFile"
Write-Output (Get-Content -LiteralPath $stateFile -Raw)

