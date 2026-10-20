param([switch]$RefreshConfig)
$ErrorActionPreference = 'Stop'
$backend = Join-Path $PSScriptRoot 'cloud-backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw 'Create cloud-backend/.venv and install requirements-local.txt first.'
}
Push-Location $backend
try {
    $arguments = @('-m', 'scripts.dev_runtime')
    if ($RefreshConfig) { $arguments += '--refresh-config' }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Local dev startup failed. See the log above.' }
} finally {
    Pop-Location
}
