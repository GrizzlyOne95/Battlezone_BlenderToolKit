<#
.SYNOPSIS
  Batch-bake AO (and other mesh maps) for BZR Ogre models in Substance 3D Painter.

.EXAMPLE
  .\run_bake.ps1 -Out D:\bzr_bake                       # every stock model
  .\run_bake.ps1 -Out D:\bzr_bake -Only avapc,avtank    # just these
  .\run_bake.ps1 -Out D:\bzr_bake -Maps AO              # AO only
  .\run_bake.ps1 -Out D:\bzr_bake -FixNormals avapc     # bake avapc with recomputed normals
  .\run_bake.ps1 -Out D:\bzr_bake -Preview              # also render normal audit sheets

  Steps: ogre_to_obj.py (OBJ + normal PNGs + normal audit) -> optional Blender
  audit sheets -> install bzr_bake_batch.py as a Painter startup plugin ->
  launch Painter with BZR_BAKE_JOB set; Painter quits when the job is done.
#>
param(
  [Parameter(Mandatory)] [string] $Out,
  [string] $Assets = "C:\Program Files (x86)\GOG Galaxy\Games\Battlezone 98 Redux\BZ_ASSETS",
  [string[]] $Extra = @(),
  [string[]] $Only = @(),
  [string[]] $Maps = @("AO", "Curvature", "Thickness"),
  [string[]] $FixNormals = @(),
  [int] $BakeSize = 0,          # 0 = match each material's diffuse size
  [int] $MaxSize = 2048,
  [int] $BitDepth = 8,
  [switch] $Rebake,             # re-bake meshes that already have output
  [switch] $Preview,
  [switch] $SkipConvert,
  [switch] $KeepOpen,           # leave Painter open after the job
  [string] $Painter = "C:\Program Files (x86)\Steam\steamapps\common\Substance 3D Painter 2024\Adobe Substance 3D Painter.exe",
  [string] $Blender = "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
)
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
New-Item -ItemType Directory -Force $Out | Out-Null
$Out = (Resolve-Path $Out).Path

if (-not $SkipConvert) {
  $convert = @("$here\ogre_to_obj.py", "--assets", $Assets, "--out", $Out)
  foreach ($e in $Extra) { $convert += @("--extra", $e) }
  if ($Only) { $convert += @("--only") + $Only }
  if ($FixNormals) { $convert += @("--fix-normals") + $FixNormals }
  python @convert
  if ($LASTEXITCODE) { throw "ogre_to_obj.py failed" }
}

if ($Preview) {
  & $Blender -b --factory-startup -P "$here\normals_preview.py" -- "$Out\obj" "$Out\normals_preview" |
    Select-String "sheet:"
}

$startup = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "Adobe\Adobe Substance 3D Painter\python\startup"
New-Item -ItemType Directory -Force $startup | Out-Null
Copy-Item "$here\bzr_bake_batch.py" $startup -Force

$job = [ordered]@{
  manifest       = "$Out\manifest.json"
  out            = "$Out\maps"
  only           = $Only
  maps           = $Maps
  max_size       = $MaxSize
  bit_depth      = $BitDepth
  skip_existing  = -not $Rebake
  quit_when_done = -not $KeepOpen
  # Painter parameter overrides; names are listed in maps\bake_parameters.json after the first run
  common         = @{}
  bakers         = @{}
}
if ($BakeSize) { $job.bake_size = $BakeSize }
$jobPath = "$Out\bake_job.json"
$job | ConvertTo-Json -Depth 5 | Set-Content $jobPath -Encoding utf8
Remove-Item "$Out\maps\bake_done.flag" -ErrorAction SilentlyContinue

if (Get-Process "Adobe Substance 3D Painter" -ErrorAction SilentlyContinue) {
  throw "Painter is already running; close it first (the job is picked up at startup)."
}
# Steam relaunches the exe and drops the environment, so hand the job over by file.
Set-Content (Join-Path (Split-Path $startup) "bzr_bake_job.txt") $jobPath -Encoding utf8
Write-Host "Launching Painter; log: $Out\maps\bake_log.txt"
Start-Process $Painter | Out-Null
# The launched process may exit straight away (Steam relaunch); wait for the
# job's flag, or for every Painter process to be gone.
Start-Sleep 20
while (-not (Test-Path "$Out\maps\bake_done.flag") -and
       (Get-Process "Adobe Substance 3D Painter" -ErrorAction SilentlyContinue)) {
  Start-Sleep 5
}
if (Test-Path "$Out\maps\bake_done.flag") { Write-Host "Bake finished: $Out\maps" }
else { Write-Warning "Painter exited before the job finished; see $Out\maps\bake_log.txt" }
