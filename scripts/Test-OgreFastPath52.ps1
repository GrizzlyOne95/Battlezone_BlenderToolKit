[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Mesh,

    [Parameter(Position = 1)]
    [string]$Skeleton,

    [string]$OutputDir,

    [switch]$NoOpen
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvDir = Join-Path $RepoRoot ".venv-ogre52"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$SmokeScript = Join-Path $RepoRoot "tests\blender52_real_asset_smoke.py"

function Resolve-InputFile {
    param(
        [string]$Value,
        [string]$Prompt
    )

    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = Read-Host $Prompt
    }

    $Value = $Value.Trim().Trim('"')
    if (-not (Test-Path -LiteralPath $Value -PathType Leaf)) {
        throw "File not found: $Value"
    }
    return (Resolve-Path -LiteralPath $Value).Path
}

function Get-Python313Launcher {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.13 -c "import sys; assert sys.version_info[:2] == (3, 13); print(sys.executable)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($py.Source, "-3.13")
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c "import sys; assert sys.version_info[:2] == (3, 13); print(sys.executable)" *> $null
        if ($LASTEXITCODE -eq 0) {
            return @($python.Source)
        }
    }

    throw @"
Python 3.13 was not found.
Install Python 3.13 (the Windows Python Launcher 'py' is recommended), then run this script again.
The test intentionally uses Python 3.13 because Blender 5.2's bpy package runs on that ABI.
"@
}

Write-Host ""
Write-Host "=== BZ98R Ogre Fast Path - Blender 5.2 Real Asset Acceptance ===" -ForegroundColor Cyan
Write-Host "Repository: $RepoRoot"

$Mesh = Resolve-InputFile $Mesh "Path to aspilo.mesh"
$Skeleton = Resolve-InputFile $Skeleton "Path to aspilo.skeleton (or the Drive-downloaded .skeleton.bin file)"

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot "artifacts\ogre-fastpath-acceptance\aspilo"
} elseif (-not [System.IO.Path]::IsPathRooted($OutputDir)) {
    $OutputDir = Join-Path $RepoRoot $OutputDir
}
$OutputDir = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$launcher = Get-Python313Launcher
$basePython = $launcher[0]
$baseArgs = @()
if ($launcher.Count -gt 1) {
    $baseArgs = $launcher[1..($launcher.Count - 1)]
}

if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    Write-Host "Creating isolated Python 3.13 environment: $VenvDir" -ForegroundColor Yellow
    & $basePython @baseArgs -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create Python 3.13 virtual environment."
    }
}

# Reuse the environment when possible. Install/repair bpy only if it cannot
# import as Blender 5.2.x. This avoids a large reinstall on every acceptance run.
& $VenvPython -c "import bpy, sys; print('bpy', bpy.app.version_string); sys.exit(0 if bpy.app.version[:2] == (5, 2) else 1)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing Blender 5.2.2 Python module into the isolated environment..." -ForegroundColor Yellow
    & $VenvPython -m pip install --disable-pip-version-check --upgrade "bpy==5.2.2"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install bpy==5.2.2."
    }
}

Write-Host ""
Write-Host "Input mesh:     $Mesh"
Write-Host "Input skeleton: $Skeleton"
Write-Host "Output folder:  $OutputDir"
Write-Host ""
Write-Host "Running real Blender 5.2 pure-fast-path import -> export -> binary validation..." -ForegroundColor Cyan

Push-Location $RepoRoot
try {
    & $VenvPython $SmokeScript $Mesh $Skeleton --output-dir $OutputDir
    $SmokeExit = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($SmokeExit -ne 0) {
    throw "Blender 5.2 real-asset acceptance FAILED with exit code $SmokeExit. See the traceback above."
}

$OutputMesh = Join-Path $OutputDir "aspilo_fast52.mesh"
$OutputSkeleton = Join-Path $OutputDir "aspilo_fast52.skeleton"
if (-not (Test-Path -LiteralPath $OutputMesh -PathType Leaf)) {
    throw "Acceptance reported success but output mesh is missing: $OutputMesh"
}
if (-not (Test-Path -LiteralPath $OutputSkeleton -PathType Leaf)) {
    throw "Acceptance reported success but output skeleton is missing: $OutputSkeleton"
}

$MeshInfo = Get-Item -LiteralPath $OutputMesh
$SkeletonInfo = Get-Item -LiteralPath $OutputSkeleton
$MeshHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputMesh).Hash
$SkeletonHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $OutputSkeleton).Hash

Write-Host ""
Write-Host "=== PASS: Blender 5.2 real-asset Ogre fast path ===" -ForegroundColor Green
Write-Host "Mesh:     $OutputMesh"
Write-Host "          $($MeshInfo.Length) bytes"
Write-Host "          SHA256 $MeshHash"
Write-Host "Skeleton: $OutputSkeleton"
Write-Host "          $($SkeletonInfo.Length) bytes"
Write-Host "          SHA256 $SkeletonHash"
Write-Host ""
Write-Host "Next gate: copy BOTH files into a controlled BZR addon/test asset set and verify pilot orientation, texturing/UVs, skinning, and animation playback." -ForegroundColor Cyan

if (-not $NoOpen) {
    Start-Process explorer.exe -ArgumentList @("/select,`"$OutputMesh`"")
}
