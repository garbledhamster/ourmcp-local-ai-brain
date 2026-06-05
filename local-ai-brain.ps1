[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

$ErrorActionPreference = "Stop"
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Error "Python 3.10+ is required for Local AI Brain. Install Python or run through a host that provides a Python runtime."
}
$oldPythonPath = $env:PYTHONPATH
try {
    if ([string]::IsNullOrWhiteSpace($oldPythonPath)) {
        $env:PYTHONPATH = $scriptRoot
    } else {
        $env:PYTHONPATH = "$scriptRoot$([IO.Path]::PathSeparator)$oldPythonPath"
    }
    & $python.Source -m local_ai_brain @Arguments
    exit $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $oldPythonPath
}
