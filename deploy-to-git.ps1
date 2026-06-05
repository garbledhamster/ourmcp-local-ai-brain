[CmdletBinding()]
param(
    [string]$TargetRoot,

    [string]$TargetPath,

    [switch]$Clean,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Get-FullPathFromInput {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }

    return [System.IO.Path]::GetFullPath((Join-Path -Path (Get-Location) -ChildPath $Path))
}

function Get-RelativePathFromBase {
    param(
        [Parameter(Mandatory = $true)][string]$BasePath,
        [Parameter(Mandatory = $true)][string]$FullPath
    )

    $baseFullPath = [System.IO.Path]::GetFullPath($BasePath).TrimEnd("\", "/") + [System.IO.Path]::DirectorySeparatorChar
    $itemFullPath = [System.IO.Path]::GetFullPath($FullPath)
    $baseUri = [System.Uri]$baseFullPath
    $itemUri = [System.Uri]$itemFullPath
    $relativeUri = $baseUri.MakeRelativeUri($itemUri)
    $relativePath = [System.Uri]::UnescapeDataString($relativeUri.ToString())

    return $relativePath -replace "/", [System.IO.Path]::DirectorySeparatorChar
}

function Test-IsSafeTarget {
    param(
        [Parameter(Mandatory = $true)][string]$Source,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $sourceTrimmed = $Source.TrimEnd("\", "/")
    $targetTrimmed = $Target.TrimEnd("\", "/")
    $rootTrimmed = ([System.IO.Path]::GetPathRoot($targetTrimmed)).TrimEnd("\", "/")

    if ([System.StringComparer]::OrdinalIgnoreCase.Equals($sourceTrimmed, $targetTrimmed)) {
        throw "TargetPath must be different from the source folder."
    }

    if ([System.StringComparer]::OrdinalIgnoreCase.Equals($targetTrimmed, $rootTrimmed)) {
        throw "Refusing to deploy directly to a filesystem root: $Target"
    }

    if ($targetTrimmed.StartsWith($sourceTrimmed + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to deploy inside the source folder: $Target"
    }
}

function Test-ShareableFile {
    param(
        [Parameter(Mandatory = $true)][System.IO.FileInfo]$File,
        [Parameter(Mandatory = $true)][string]$Source
    )

    $relative = Get-RelativePathFromBase -BasePath $Source -FullPath $File.FullName
    $parts = $relative -split "[\\/]+"
    $excludedDirs = @("__pycache__", ".pytest_cache", ".opencode", ".git")

    foreach ($part in $parts) {
        if ($excludedDirs -contains $part) {
            return $false
        }
    }

    if ($File.Extension -in @(".pyc", ".pyo")) {
        return $false
    }

    if ($File.Name -eq ".DS_Store") {
        return $false
    }

    if ($File.Name -like ".local-ai-brain-*.json") {
        return $false
    }

    return $true
}

$sourcePath = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$defaultTargetRoot = if (-not [string]::IsNullOrWhiteSpace($env:LOCAL_AI_BRAIN_GIT_ROOT)) {
    $env:LOCAL_AI_BRAIN_GIT_ROOT
} else {
    "C:\Github\tools"
}

$usedDefaultTarget = $false
if ([string]::IsNullOrWhiteSpace($TargetRoot) -and [string]::IsNullOrWhiteSpace($TargetPath)) {
    $TargetRoot = $defaultTargetRoot
    $usedDefaultTarget = $true
    Write-Host "No target supplied; using default beta Git root: $TargetRoot"
}

if (-not [string]::IsNullOrWhiteSpace($TargetRoot) -and -not [string]::IsNullOrWhiteSpace($TargetPath)) {
    throw "Provide only one target option: -TargetRoot or -TargetPath."
}

if (-not [string]::IsNullOrWhiteSpace($TargetRoot)) {
    $targetRootFullPath = Get-FullPathFromInput -Path $TargetRoot
    $targetFullPath = Join-Path -Path $targetRootFullPath -ChildPath "local-ai-brain"
} else {
    $targetFullPath = Get-FullPathFromInput -Path $TargetPath
}

$effectiveClean = [bool]$Clean
if ($usedDefaultTarget -and -not $DryRun) {
    $effectiveClean = $true
}

Test-IsSafeTarget -Source $sourcePath -Target $targetFullPath

$files = Get-ChildItem -LiteralPath $sourcePath -Recurse -Force -File |
    Where-Object { Test-ShareableFile -File $_ -Source $sourcePath } |
    Sort-Object FullName

Write-Host "Local AI Brain deploy-to-git"
Write-Host "source: $sourcePath"
if (-not [string]::IsNullOrWhiteSpace($TargetRoot)) {
    Write-Host "target_root: $targetRootFullPath"
}
Write-Host "target: $targetFullPath"
Write-Host "clean: $effectiveClean"
Write-Host "dry_run: $DryRun"
Write-Host "files: $($files.Count)"

if ($DryRun) {
    if ($effectiveClean) {
        Write-Host "WOULD clean target contents, preserving target .git if present."
    }

    foreach ($file in $files) {
        $relative = Get-RelativePathFromBase -BasePath $sourcePath -FullPath $file.FullName
        Write-Host "WOULD copy $relative"
    }

    exit 0
}

New-Item -ItemType Directory -Force -Path $targetFullPath | Out-Null

if ($effectiveClean) {
    Get-ChildItem -LiteralPath $targetFullPath -Force |
        Where-Object { $_.Name -ne ".git" } |
        ForEach-Object {
            Remove-Item -LiteralPath $_.FullName -Recurse -Force
        }
}

foreach ($file in $files) {
    $relative = Get-RelativePathFromBase -BasePath $sourcePath -FullPath $file.FullName
    $destination = Join-Path -Path $targetFullPath -ChildPath $relative
    $destinationParent = Split-Path -Parent $destination

    New-Item -ItemType Directory -Force -Path $destinationParent | Out-Null
    Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
}

Write-Host "done: copied $($files.Count) files"
Write-Host "next:"
Write-Host "  cd `"$targetFullPath`""
Write-Host "  git status"
