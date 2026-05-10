# Local mirror of .github/workflows/release.yml. Builds the same zip the
# CI would for a given tag, then extracts it to _build/test-install/ so
# you can exercise the end-user experience without pushing a tag.
#
# Usage:
#   pwsh scripts/build-release-local.ps1                # tag = v0.0.6
#   pwsh scripts/build-release-local.ps1 -Tag v9.9.9    # forces bootstrap to attempt self-update
#   pwsh scripts/build-release-local.ps1 -SkipNpm       # reuse existing webui/dist
param(
    [string]$Tag = 'v0.0.6',
    [switch]$SkipNpm
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root

$build   = Join-Path $root '_build'
$stage   = Join-Path $build '_stage'
$install = Join-Path $build 'test-install'

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Path $build | Out-Null

Write-Host "[1/5] Build webui ..." -ForegroundColor Cyan
Push-Location (Join-Path $root 'webui')
try {
    if (-not $SkipNpm) { npm ci --silent 2>&1 | Out-Host }
    npm run build 2>&1 | Out-Host
} finally { Pop-Location }

Write-Host "[2/5] PyInstaller build ..." -ForegroundColor Cyan
& uv run pyinstaller --noconfirm --clean tthol-reader.spec 2>&1 | Out-Host
if (-not (Test-Path 'dist\tthol-reader\tthol-reader.exe')) {
    throw "PyInstaller did not produce tthol-reader.exe"
}

Write-Host "[3/5] Stage release tree ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $stage | Out-Null
robocopy 'dist\tthol-reader' $stage /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy stage failed: $LASTEXITCODE" }
$global:LASTEXITCODE = 0
Set-Content -Path "$stage\VERSION" -Value $Tag -Encoding ascii -NoNewline
if (Test-Path 'README.md') { Copy-Item 'README.md' $stage\ -Force }

$required = @(
    "$stage\tthol-reader.exe",
    "$stage\_internal\bootstrap_splash.html",
    "$stage\_internal\webui\dist\index.html",
    "$stage\_internal\knowledge.json",
    "$stage\_internal\tthol.sqlite",
    "$stage\VERSION"
)
foreach ($p in $required) {
    if (-not (Test-Path $p)) { throw "Missing required entry: $p" }
}

Write-Host "[4/5] Create zip ..." -ForegroundColor Cyan
$zip = Join-Path $build "tthol-reader-$Tag.zip"
$zipScript = Join-Path $build 'zipit.py'
@"
import shutil, sys
shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])
"@ | Set-Content -Path $zipScript -Encoding ascii
$zipBase = $zip -replace '\.zip$',''
& uv run python $zipScript $zipBase $stage

Write-Host "[5/5] Extract for testing -> $install" -ForegroundColor Cyan
if (Test-Path $install) { Remove-Item -Recurse -Force $install }
New-Item -ItemType Directory -Path $install | Out-Null
Expand-Archive -Path $zip -DestinationPath $install -Force

$size = '{0:N1} MB' -f ((Get-Item $zip).Length / 1MB)
Write-Host "" -ForegroundColor Green
Write-Host "Done." -ForegroundColor Green
Write-Host "  zip:           $zip ($size)" -ForegroundColor Green
Write-Host "  test install:  $install" -ForegroundColor Green
Write-Host "  Try: double-click $install\tthol-reader.exe (UAC will prompt)" -ForegroundColor Green
