# Local mirror of .github/workflows/release.yml.
# Outputs to _build/ to avoid clobbering the dev-time toolkit/ and webui/dist/.
#
# Usage:
#   pwsh scripts/build-release-local.ps1                # tag = v0.0.6 (no auto-update on first launch)
#   pwsh scripts/build-release-local.ps1 -Tag v9.9.9    # forces bootstrap to attempt update path
#
# After completion the script extracts the zip to _build/test-install/ so you
# can run start.bat there to exercise the end-user experience.
param(
    [string]$Tag = 'v0.0.6',
    [switch]$SkipNpm
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path "$PSScriptRoot\..").Path
Set-Location $root

$build   = Join-Path $root '_build'
$cache   = Join-Path $build 'cache'
$tk      = Join-Path $build 'toolkit\python'
$stage   = Join-Path $build '_stage'
$reqs    = Join-Path $build 'requirements.txt'
$install = Join-Path $build 'test-install'

if (Test-Path $build) { Remove-Item -Recurse -Force $build }
New-Item -ItemType Directory -Path $build, $cache | Out-Null

Write-Host "[1/7] Build webui ..." -ForegroundColor Cyan
Push-Location (Join-Path $root 'webui')
try {
    if (-not $SkipNpm) {
        npm ci --silent 2>&1 | Out-Host
    }
    npm run build 2>&1 | Out-Host
} finally { Pop-Location }

Write-Host "[2/7] Embeddable Python 3.11.9 ..." -ForegroundColor Cyan
$pyZip = Join-Path $cache 'python-embed.zip'
curl.exe -L -s 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip' -o $pyZip
New-Item -ItemType Directory -Path $tk -Force | Out-Null
Expand-Archive -Path $pyZip -DestinationPath $tk -Force

Write-Host "[3/7] Configure _pth + sitecustomize ..." -ForegroundColor Cyan
$pth = @"
python311.zip
.
Lib\site-packages

import site
"@
Set-Content -Path "$tk\python311._pth" -Value $pth -Encoding ascii

$sc = @"
import sys, os
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(os.path.dirname(_here))
if _root not in sys.path:
    sys.path.insert(0, _root)
"@
Set-Content -Path "$tk\sitecustomize.py" -Value $sc -Encoding ascii

Write-Host "[4/7] pip + dependencies ..." -ForegroundColor Cyan
$gp = Join-Path $cache 'get-pip.py'
curl.exe -L -s 'https://bootstrap.pypa.io/get-pip.py' -o $gp
& "$tk\python.exe" $gp --no-warn-script-location 2>&1 | Out-Host

# Generate requirements.txt from pyproject.toml using uv's python (CI uses runner Python)
$genScript = Join-Path $build 'gen_reqs.py'
@"
import tomllib
d = tomllib.load(open('pyproject.toml','rb'))
open(r'$reqs','w').write('\n'.join(d['project']['dependencies']))
"@ | Set-Content -Path $genScript -Encoding ascii
& uv run python $genScript
Get-Content $reqs

& "$tk\python.exe" -m pip install --no-warn-script-location -r $reqs 2>&1 | Out-Host

# Slim: pip itself & __pycache__
Remove-Item -Recurse -Force "$tk\Lib\site-packages\pip" -ErrorAction SilentlyContinue
Get-ChildItem -Path "$tk\Lib\site-packages" -Filter 'pip-*.dist-info' -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem -Path $tk -Recurse -Filter '__pycache__' -Directory -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "[5/7] Stage release tree ..." -ForegroundColor Cyan
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$files = @(
    'start.bat','bootstrap.py','bootstrap_splash.html',
    'app.py','auto_detect.py','reader.py','warehouse_scan.py',
    'knowledge.json','tthol.sqlite',
    'icon.png','icon_small.png','brand_banner.png','README.md'
)
foreach ($f in $files) { if (Test-Path $f) { Copy-Item $f $stage\ -Force } }
Set-Content -Path "$stage\VERSION" -Value $Tag -Encoding ascii -NoNewline

robocopy services           "$stage\services"    /E /XD __pycache__ /XF *.pyc *.pyo | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy services failed: $LASTEXITCODE" }
robocopy 'webui\dist'       "$stage\webui\dist"  /E | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy webui\dist failed: $LASTEXITCODE" }
robocopy "$build\toolkit"   "$stage\toolkit"     /E /XD __pycache__ /XF *.pyc *.pyo | Out-Null
if ($LASTEXITCODE -ge 8) { throw "robocopy toolkit failed: $LASTEXITCODE" }
$global:LASTEXITCODE = 0

$required = @(
    "$stage\app.py","$stage\bootstrap.py","$stage\start.bat",
    "$stage\webui\dist\index.html",
    "$stage\toolkit\python\python.exe","$stage\toolkit\python\pythonw.exe",
    "$stage\tthol.sqlite","$stage\VERSION","$stage\services\__init__.py"
)
foreach ($p in $required) {
    if (-not (Test-Path $p)) { throw "Missing required entry: $p" }
}

Write-Host "[6/7] Create zip ..." -ForegroundColor Cyan
$zip = Join-Path $build "tthol-reader-$Tag.zip"
$zipScript = Join-Path $build 'zipit.py'
@"
import shutil, sys
shutil.make_archive(sys.argv[1], 'zip', sys.argv[2])
"@ | Set-Content -Path $zipScript -Encoding ascii
$zipBase = $zip -replace '\.zip$',''
& "$tk\python.exe" $zipScript $zipBase $stage

Write-Host "[7/7] Extract for testing -> $install" -ForegroundColor Cyan
if (Test-Path $install) { Remove-Item -Recurse -Force $install }
New-Item -ItemType Directory -Path $install | Out-Null
Expand-Archive -Path $zip -DestinationPath $install -Force

$size = '{0:N1} MB' -f ((Get-Item $zip).Length / 1MB)
Write-Host "" -ForegroundColor Green
Write-Host "Done." -ForegroundColor Green
Write-Host "  zip:           $zip ($size)" -ForegroundColor Green
Write-Host "  test install:  $install" -ForegroundColor Green
Write-Host "  Try: right-click $install\start.bat -> Run as administrator" -ForegroundColor Green
