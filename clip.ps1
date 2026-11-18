# One command: prepare locally (free), then open Claude Code interactively to pick + render clips.
# Usage: .\clip.cmd [<twitch/youtube URL or local file>] [--mode stream|podcast]
#        Without a link it asks for one (pasting avoids quoting trouble with '&' in URLs).
# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts in the ANSI code page.

$argv = @($args)
if ($argv.Count -lt 1 -or "$($argv[0])".StartsWith('-')) {
    $link = (Read-Host "Paste the video link (or a local file path)").Trim().Trim('"')
    if (-not $link) { Write-Host "usage: .\clip.cmd <url-or-file> [--mode stream|podcast]"; exit 1 }
    $argv = @($link) + $argv
}
# local files are relative to where the user is, not to this folder
if (Test-Path -LiteralPath $argv[0] -PathType Leaf) { $argv[0] = (Resolve-Path -LiteralPath $argv[0]).Path }

Set-Location $PSScriptRoot

if ($env:ANTHROPIC_API_KEY) {
    Write-Host "ANTHROPIC_API_KEY is set: Claude Code would bill the API instead of your Pro plan."
    Write-Host "Run 'Remove-Item Env:ANTHROPIC_API_KEY' (PowerShell) or 'set ANTHROPIC_API_KEY=' (cmd) and try again."
    Write-Host "If it comes back in every new window, remove it under Settings > System > About > Advanced system settings > Environment Variables."
    exit 1
}

$py = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host "Not set up yet: run .\setup.cmd first."; exit 1 }

# Python prints UTF-8 (titles, transcripts); decode it as such and don't buffer the progress output.
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'

$script:job = $null
& $py clip.py prepare @argv | ForEach-Object {
    Write-Host $_
    if ($_ -match 'READY\s+job=(\S+)') { $script:job = $Matches[1] }
}
if (-not $script:job) { Write-Host "prepare did not finish"; exit 1 }

if (-not (Get-Command claude -ErrorAction SilentlyContinue)) {
    Write-Host "Claude Code not found. Install it, then run:  claude `"/clip $($script:job)`""
    exit 1
}
# Interactive session (uses your Pro plan limits, no API key needed)
& claude "/clip $($script:job)"
exit $LASTEXITCODE
