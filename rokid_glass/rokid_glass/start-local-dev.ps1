param([switch]$RefreshConfig)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
& (Join-Path $repoRoot 'start-local-dev.ps1') -RefreshConfig:$RefreshConfig
