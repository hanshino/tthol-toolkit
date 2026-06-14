# Local mirror of .github/workflows/release.yml. Builds the same zip the
# CI would for a given tag, then extracts it to _build/test-install/ so
# you can exercise the end-user experience without pushing a tag.
#
# Usage:
#   pwsh scripts/build-release-local.ps1                # tag = v1.2.0
#   pwsh scripts/build-release-local.ps1 -Tag v9.9.9    # name the output zip differently
#   pwsh scripts/build-release-local.ps1 -SkipNpm       # reuse existing webui/dist
param(
    [string]$Tag = 'v1.2.0',
    [switch]$SkipNpm
)

$ErrorActionPreference = 'Stop'

# PowerShell 5.1 wraps native command stderr as ErrorRecords when stderr is
# captured (e.g. by CI / non-interactive harness). PyInstaller writes INFO
# logs to stderr — under EAP=Stop that would abort the script. Run native
# commands with EAP=Continue and rely on $LASTEXITCODE instead.
function Invoke-Exe {
    param([string]$Name, [scriptblock]$Block)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block } finally { $ErrorActionPreference = $prev }
    if ($LASTEXITCODE -ne 0) { throw "$Name failed: exit $LASTEXITCODE" }
}

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
    if (-not $SkipNpm) { Invoke-Exe 'npm ci' { & npm.cmd ci --silent } }
    Invoke-Exe 'npm run build' { & npm.cmd run build }
} finally { Pop-Location }

Write-Host "[2/5] PyInstaller build ..." -ForegroundColor Cyan
Invoke-Exe 'pyinstaller' { & uv run pyinstaller --noconfirm --clean tthol-reader.spec }
if (-not (Test-Path 'dist\tthol-reader\tthol-reader.exe')) {
    throw "PyInstaller did not produce tthol-reader.exe"
}
# .NET app config (loadFromRemoteSources) so the pythonnet/.NET backend loads
# even when the downloaded zip is extracted with Mark-of-the-Web. Must sit next
# to the exe, not under _internal.
Copy-Item 'tthol-reader.exe.config' 'dist\tthol-reader\' -Force

Write-Host "[3/5] Stage release tree ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $stage | Out-Null
robocopy 'dist\tthol-reader' $stage /E /NFL /NDL /NJH /NJS /NC /NS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy stage failed: $LASTEXITCODE" }
$global:LASTEXITCODE = 0
if (Test-Path 'README.md') { Copy-Item 'README.md' $stage\ -Force }

$required = @(
    "$stage\tthol-reader.exe",
    "$stage\tthol-reader.exe.config",
    "$stage\_internal\webui\dist\index.html",
    "$stage\_internal\knowledge.json",
    "$stage\_internal\tthol.sqlite"
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
Invoke-Exe 'zip' { & uv run python $zipScript $zipBase $stage }

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
