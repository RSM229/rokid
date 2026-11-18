$ErrorActionPreference = 'Stop'

$taskName = 'RokidLocalBackend'
$pythonw  = 'D:\rokid\rokid\cloud-backend\.venv\Scripts\pythonw.exe'
$launcher = 'D:\rokid\rokid\tmp\launch_backend.py'

if (-not (Test-Path -LiteralPath $pythonw))  { throw "pythonw not found: $pythonw" }
if (-not (Test-Path -LiteralPath $launcher)) { throw "launcher not found: $launcher" }
if ($pythonw -match ' ' -or $launcher -match ' ') { throw 'Paths must not contain spaces (schtasks limitation).' }

$tr = "$pythonw $launcher"

Write-Host "Registering scheduled task: $taskName (every 5 minutes, self-healing)"
schtasks /Create /TN $taskName /TR $tr /SC MINUTE /MO 5 /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'schtasks /Create failed.' }

Write-Host 'Starting it once now...'
schtasks /Run /TN $taskName | Out-Null

Start-Sleep -Seconds 6

$ok = $false
try {
    $client = New-Object System.Net.Sockets.TcpClient
    $client.Connect('127.0.0.1', 8000)
    $ok = $client.Connected
    $client.Close()
} catch { $ok = $false }

if ($ok) {
    Write-Host ''
    Write-Host 'SUCCESS: backend is listening on http://127.0.0.1:8000' -ForegroundColor Green
    Write-Host 'Swagger UI: http://127.0.0.1:8000/docs'
} else {
    Write-Host ''
    Write-Host 'Port 8000 not up yet. Wait a few seconds, or check the log:' -ForegroundColor Yellow
    Write-Host '  D:\rokid\rokid\tmp\local_backend.log'
}

Write-Host ''
Write-Host 'The task re-checks every 5 minutes and restarts the backend if it died.'
Write-Host 'To remove it later, run:'
Write-Host "  schtasks /Delete /TN $taskName /F"
