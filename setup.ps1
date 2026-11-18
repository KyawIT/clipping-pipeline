# One-time setup for the clipping pipeline (Windows 10/11 x64).
# Usage: .\setup.cmd            (add -Demucs to also install vocal separation)
#    or: powershell -ExecutionPolicy Bypass -File setup.ps1 [-Demucs]
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.
param([switch]$Demucs)

Set-Location $PSScriptRoot
$ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest is very slow with the progress bar

function Say($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Die($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }
function Check($what) { if ($LASTEXITCODE -ne 0) { Die "$what failed (exit code $LASTEXITCODE)" } }

# --- system tools --------------------------------------------------------------
Say "Checking system tools"
if (-not (Has ffmpeg))  { Die "ffmpeg missing -> winget install Gyan.FFmpeg   (then open a NEW terminal)" }
if (-not (Has ffprobe)) { Die "ffprobe missing -> winget install Gyan.FFmpeg   (then open a NEW terminal)" }
if (-not (Has node))    { Die "Node.js missing -> winget install OpenJS.NodeJS.LTS   (then open a NEW terminal)" }
if (-not (Has npm))     { Die "npm missing -> reinstall Node.js (winget install OpenJS.NodeJS.LTS)" }
$nodeMajor = [int](& node -p "process.versions.node.split('.')[0]")
if ($nodeMajor -lt 18) { Die "Node 18+ required (found $(& node -v)) -> winget install OpenJS.NodeJS.LTS" }

# WhisperX / torch work best on Python 3.10-3.12. Prefer the py launcher, then python on PATH.
# (The "python" in WindowsApps can be a Microsoft Store stub; running it fails, so it is skipped.)
function Find-Python([string]$check) {
    $cands = @()
    if (Has py) { $cands += ,@('py', '-3.12'); $cands += ,@('py', '-3.11'); $cands += ,@('py', '-3.10'); $cands += ,@('py', '-3') }
    if (Has python) { $cands += ,@('python') }
    foreach ($c in $cands) {
        $exe = $c[0]
        $pre = @($c | Select-Object -Skip 1)
        $out = & $exe @pre -c "import sys; print(sys.executable) if $check else sys.exit(1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -Last 1).Trim() }
    }
    return $null
}
$PY = Find-Python "(3,10) <= sys.version_info[:2] <= (3,12)"
if (-not $PY) {
    $PY = Find-Python "sys.version_info[:2] >= (3,10)"
    if (-not $PY) { Die "Python 3.10-3.12 missing -> winget install Python.Python.3.12   (then open a NEW terminal)" }
    Write-Host "WARNING: WhisperX is best supported on Python 3.10-3.12. If pip fails: winget install Python.Python.3.12" -ForegroundColor Yellow
}
Write-Host "Using $(& $PY --version) ($PY)"

# --- python venv -----------------------------------------------------------------
Say "Creating Python venv (.venv) and installing WhisperX, yt-dlp, OpenCV (can take a few minutes)"
if (-not (Test-Path .venv\Scripts\python.exe)) { & $PY -m venv .venv; Check "Creating the venv" }
$VPY = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $VPY -m pip install -q --upgrade pip wheel; Check "pip upgrade"
& $VPY -m pip install -r requirements.txt; Check "pip install -r requirements.txt"
if ($Demucs) { & $VPY -m pip install demucs; Check "pip install demucs" }

# PyPI only has CPU builds of torch for Windows. With an NVIDIA card, swap in the CUDA build
# of the exact same versions so the rest of the dependency tree stays as pip resolved it.
if (Has nvidia-smi) {
    $hasCuda = & $VPY -c "import torch; print(torch.cuda.is_available())"
    if ("$hasCuda".Trim() -ne 'True') {
        Say "NVIDIA GPU found: installing the CUDA build of torch (large download, ~2-3 GB)"
        $pins = & $VPY -c @"
from importlib import metadata
for name in ('torch', 'torchaudio', 'torchvision'):
    try:
        print(name + '==' + metadata.version(name).split('+')[0])
    except metadata.PackageNotFoundError:
        pass
"@
        $pins = @($pins | Where-Object { $_ })
        $ok = $false
        foreach ($tag in 'cu128', 'cu126', 'cu124', 'cu121', 'cu118') {
            Write-Host "Trying $tag for $($pins -join ' ')"
            & $VPY -m pip install -q --force-reinstall --no-deps @pins --index-url "https://download.pytorch.org/whl/$tag"
            if ($LASTEXITCODE -eq 0) { $ok = $true; break }
        }
        if (-not $ok) { Write-Host "WARNING: no CUDA build of $($pins -join ' ') found; staying on CPU." -ForegroundColor Yellow }
    }
}
& $VPY -c "import torch; print('CUDA GPU:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'none (CPU mode, use a smaller model in config.yaml)')"

# --- TwitchDownloaderCLI (Twitch chat replay) --------------------------------------
Say "Installing TwitchDownloaderCLI into .\bin"
New-Item -ItemType Directory -Force bin | Out-Null
if (-not (Test-Path bin\TwitchDownloaderCLI.exe)) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $rel = Invoke-RestMethod -Uri 'https://api.github.com/repos/lay295/TwitchDownloader/releases/latest' `
                                 -Headers @{ 'User-Agent' = 'clipping-pipeline-setup' }
        $asset = $rel.assets | Where-Object { $_.name -like 'TwitchDownloaderCLI*Windows-x64*.zip' } | Select-Object -First 1
        if (-not $asset) { throw "no Windows-x64 CLI build in the latest release" }
        Write-Host "Downloading $($asset.browser_download_url)"
        $zip = Join-Path $env:TEMP 'tdcli.zip'
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
        Expand-Archive -Path $zip -DestinationPath bin -Force
        Remove-Item $zip -Force
    } catch {
        Write-Host "WARNING: could not install TwitchDownloaderCLI ($_). Twitch clips will work without the chat signal." -ForegroundColor Yellow
    }
} else {
    Write-Host "already installed"
}

# --- Remotion -------------------------------------------------------------------
Say "Installing Remotion (npm)"
Push-Location remotion
& npm install --no-audit --no-fund
$npmExit = $LASTEXITCODE
if ($npmExit -eq 0) {
    & npx remotion browser ensure
    if ($LASTEXITCODE -ne 0) { Write-Host "Could not pre-download Chrome headless shell; Remotion will retry on first render." }
}
Pop-Location
if ($npmExit -ne 0) { Die "npm install failed (exit code $npmExit)" }

New-Item -ItemType Directory -Force jobs, layouts | Out-Null

Say "Done!"
Write-Host @"
Next steps:
  1. Log in to Claude Code once:   claude    (then type /login)
  2. Make clips:                    .\web.cmd   (web app)   or   .\clip.cmd
  Your clips end up in jobs\<name>\out\

"@
