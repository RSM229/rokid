param(
  [ValidatePattern('^[a-z_][a-z0-9_-]*[$]?$')]
  [string]$R640User = 'baize',
  [string]$SshDirectory = (Join-Path $HOME '.ssh')
)

$ErrorActionPreference = 'Stop'

$sshConfig = Join-Path $SshDirectory 'config'
$identity = Join-Path $SshDirectory 'id_ed25519'
$r640Identity = Join-Path $SshDirectory 'id_ed25519_r640'
$sshKeygen = (Get-Command ssh-keygen.exe -ErrorAction Stop).Source
if (-not (Test-Path -LiteralPath $identity)) {
  throw "Cloud login key not found: $identity"
}
if (-not (Test-Path -LiteralPath $r640Identity)) {
  if ($r640Identity -match '["\r\n]') { throw 'Unsupported key path.' }
  # ProcessStartInfo preserves the empty passphrase on both PowerShell 5.1 and 7.
  $keygenInfo = New-Object System.Diagnostics.ProcessStartInfo
  $keygenInfo.FileName = $sshKeygen
  $keygenInfo.UseShellExecute = $false
  $keygenInfo.CreateNoWindow = $true
  $keygenInfo.Arguments = '-q -t ed25519 -N "" -C windows-to-r640 -f "' + $r640Identity + '"'
  $keygenProcess = [System.Diagnostics.Process]::Start($keygenInfo)
  $keygenProcess.WaitForExit()
  if ($keygenProcess.ExitCode -ne 0) { throw 'Could not create the R640-only SSH key.' }
  $keygenProcess.Dispose()
}
if (-not (Test-Path -LiteralPath "$r640Identity.pub")) {
  throw "R640 public key is missing: $r640Identity.pub"
}

$begin = '# BEGIN ROKID-R640-TUNNEL'
$end = '# END ROKID-R640-TUNNEL'
$block = @"
$begin
Host rokid-r640-gateway
    HostName 8.134.23.170
    Port 22
    User root
    IdentityFile "$($identity -replace '\\', '/')"
    IdentitiesOnly yes
    StrictHostKeyChecking yes

Host rokid-r640
    HostName 127.0.0.1
    Port 22220
    User $R640User
    ProxyJump rokid-r640-gateway
    IdentityFile "$($r640Identity -replace '\\', '/')"
    IdentitiesOnly yes
    HostKeyAlias rokid-r640-via-cloud
    StrictHostKeyChecking ask
$end
"@

$directory = Split-Path -Parent $sshConfig
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$existing = if (Test-Path -LiteralPath $sshConfig) {
  [System.IO.File]::ReadAllText($sshConfig)
} else {
  ''
}
if (Test-Path -LiteralPath $sshConfig) {
  Copy-Item -LiteralPath $sshConfig -Destination "$sshConfig.before-r640-tunnel.$(Get-Date -Format yyyyMMddHHmmssfff)"
}
$escapedBegin = [regex]::Escape($begin)
$escapedEnd = [regex]::Escape($end)
$pattern = "(?ms)^$escapedBegin\r?\n.*?^$escapedEnd\r?\n?"
$existing = [regex]::Replace($existing, $pattern, '')
$updated = $block.Trim() + "`r`n`r`n" + $existing.TrimEnd() + "`r`n"
[System.IO.File]::WriteAllText($sshConfig, $updated, [System.Text.UTF8Encoding]::new($false))

Write-Host 'SSH alias installed. Once the R640 key is authorized on the cloud, connect with:'
Write-Host '  ssh rokid-r640'
Write-Host ''
Write-Host 'Copy this Windows R640-only public key into setup-r640.sh when prompted:'
Get-Content -LiteralPath "$r640Identity.pub"
Write-Host 'Compare the first R640 host-key fingerprint with the fingerprint printed by setup-r640.sh.'
