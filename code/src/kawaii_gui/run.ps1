# One-shot setup + launch for the RainMixer GUI on Windows 10/11.
#
#   run.cmd            install anything missing, build the frontend, open the GUI
#   run.cmd --clean    wipe .venv, node_modules and dist first, then do the above
#
# (run.cmd just calls this script with the execution policy bypassed, so it
# can be double-clicked. ./run.sh from Git Bash also ends up here.)
#
# Windows counterpart of run.sh:
#   winget -> Node.js LTS (+ npm), WebView2 runtime if missing
#   uv     -> Python 3.12 venv + packages -> frontend build -> GUI
# Safe to re-run: steps that are already done are skipped.
#
# Keep this file ASCII-only and Windows PowerShell 5.1 compatible (no &&, ||,
# ternaries or ?? operators) - that's the PowerShell Windows 11 ships with.

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

$NodeMin = 18
$VenvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

function Say([string]$Msg) { Write-Host "==> $Msg" -ForegroundColor Magenta }

function Die([string]$Msg) {
    Write-Host "Error: $Msg" -ForegroundColor Red
    exit 1
}

function Have([string]$Name) {
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

# $ErrorActionPreference doesn't cover native programs, so check exit codes.
# Plain $args (no param block) so flags like -r reach the program untouched.
function Invoke-Checked {
    $exe = $args[0]
    $rest = @($args | Select-Object -Skip 1)
    & $exe @rest
    if ($LASTEXITCODE -ne 0) { Die "'$exe $($rest -join ' ')' failed (exit code $LASTEXITCODE)." }
}

# Installers update the registry PATH, not this session's - re-read it.
function Update-SessionPath {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$HOME\.local\bin;$machine;$user;$env:Path"
}

function Test-NodeOk {
    if (-not ((Have 'node') -and (Have 'npm'))) { return $false }
    $major = & node -p "process.versions.node.split('.')[0]"
    if ($LASTEXITCODE -ne 0) { return $false }
    return ([int]$major -ge $NodeMin)
}

function Test-WebView2 {
    # Evergreen WebView2 runtime registers itself under this GUID.
    $guid = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    $keys = @(
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\$guid",
        "HKLM:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid",
        "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\$guid"
    )
    foreach ($k in $keys) {
        $pv = (Get-ItemProperty -Path $k -Name pv -ErrorAction SilentlyContinue).pv
        if ($pv -and $pv -ne '0.0.0.0') { return $true }
    }
    return $false
}

function Install-WithWinget([string]$Id, [string]$What) {
    if (-not (Have 'winget')) { return $false }
    Say "Installing $What (Windows may ask for permission)"
    & winget install --exact --id $Id --silent --accept-source-agreements --accept-package-agreements
    # winget returns non-zero when the package is already installed, so don't
    # treat that as fatal - the caller re-checks what it actually needs.
    return $true
}

$AppArgs = @($args)
if ($AppArgs.Count -gt 0 -and $AppArgs[0] -eq '--clean') {
    $AppArgs = @($AppArgs | Select-Object -Skip 1)
    Say 'Cleaning previous install'
    foreach ($d in '.venv', 'web\node_modules', 'web\dist') {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    }
}

Update-SessionPath
Say 'Detected platform: windows'

# --- 1. Node.js + npm -------------------------------------------------------

if (-not (Test-NodeOk)) {
    if (-not (Install-WithWinget 'OpenJS.NodeJS.LTS' 'Node.js LTS + npm')) {
        Die "Node.js $NodeMin+ not found and winget isn't available. Install Node.js LTS from https://nodejs.org and re-run."
    }
    Update-SessionPath
}
if (-not (Test-NodeOk)) {
    Die "Node.js $NodeMin+ not found. Install it from https://nodejs.org (or open a new terminal if you just did) and re-run."
}

# --- 2. WebView2 (what pywebview renders with on Windows) -------------------
# Preinstalled on Windows 11; best-effort install otherwise.

if (-not (Test-WebView2)) {
    if (-not (Install-WithWinget 'Microsoft.EdgeWebView2Runtime' 'Microsoft Edge WebView2 runtime')) {
        Say 'WebView2 runtime not found and winget is unavailable; get it from https://developer.microsoft.com/microsoft-edge/webview2/ if the window stays blank.'
    }
}

# --- 3. uv + Python packages ------------------------------------------------

if (-not (Have 'uv')) {
    Say 'Installing uv (Python package manager)'
    $installer = Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1'
    Invoke-Expression $installer
    Update-SessionPath
    if (-not (Have 'uv')) { Die 'uv install failed. See https://docs.astral.sh/uv/, then re-run.' }
}

if (-not (Test-Path $VenvPython)) {
    Say 'Creating Python virtual environment'
    # uv downloads its own Python 3.12 if Windows doesn't have one.
    Invoke-Checked uv venv --no-project --python 3.12 .venv
}

$marker = '.venv\.installed'
if (-not (Test-Path $marker) -or
    (Get-Item 'requirements.txt').LastWriteTime -gt (Get-Item $marker).LastWriteTime) {
    Say 'Installing Python packages'
    Invoke-Checked uv pip install --python $VenvPython -r requirements.txt
    New-Item -ItemType File -Force -Path $marker | Out-Null
}

# --- 4. Frontend ------------------------------------------------------------

if (-not (Test-Path 'web\node_modules') -or
    (Get-Item 'web\package-lock.json').LastWriteTime -gt (Get-Item 'web\node_modules').LastWriteTime) {
    Say 'Installing frontend packages'
    Push-Location web
    try { Invoke-Checked npm install --no-fund --no-audit } finally { Pop-Location }
    (Get-Item 'web\node_modules').LastWriteTime = Get-Date
}

$distIndex = 'web\dist\index.html'
$needBuild = -not (Test-Path $distIndex)
if (-not $needBuild) {
    $builtAt = (Get-Item $distIndex).LastWriteTime
    $newer = Get-ChildItem -Recurse -File -Path 'web\src', 'web\index.html', 'web\package.json' -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -gt $builtAt } |
        Select-Object -First 1
    $needBuild = [bool]$newer
}
if ($needBuild) {
    Say 'Building frontend'
    Push-Location web
    try { Invoke-Checked npm run build } finally { Pop-Location }
}

# --- 5. Launch --------------------------------------------------------------

Say 'Starting RainMixer'
& $VenvPython app.py @AppArgs
exit $LASTEXITCODE
