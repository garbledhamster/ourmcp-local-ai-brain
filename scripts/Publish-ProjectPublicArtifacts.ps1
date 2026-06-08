[CmdletBinding()]
param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path,
  [switch]$WhatIf
)

$ErrorActionPreference = "Stop"

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$env:PYTHONPATH = $PSScriptRoot + [System.IO.Path]::PathSeparator + $env:PYTHONPATH

$PythonArgs = @(
  "-m", "local_ai_brain.artifacts",
  "--repo-root", $RepoRoot,
  "--project-root", $ProjectRoot
)
if ($WhatIf) {
  $PythonArgs += "--what-if"
}

python @PythonArgs
exit $LASTEXITCODE
