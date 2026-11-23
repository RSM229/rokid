#Requires -Version 5.1
<#
.SYNOPSIS
    把 cloud-backend 的指定文件部署到 rokid-r640，校验哈希并确认热重载。

.DESCRIPTION
    服务器不是 git 仓库，靠「挂载目录 + uvicorn --reload」生效，所以部署就是传文件。
    脚本把实际踩过的坑都固化掉了：

    1. 一次 SSH 会话干完所有事。这条链路对连续短连接有限制：一次部署要传 6 个文件，
       如果每个文件开两次连接（落盘 + 改名），很快就会
       `Connection closed by UNKNOWN port 65535`。所以把所有文件内容嵌进同一个远端
       脚本，只建一次连接。
    2. 文件内容绝不进命令行。kb_search.py 约 30KB，base64 后约 40KB，超过 Windows
       命令行 32KB 上限，ssh.exe 会直接起不来（报错还伪装成连接问题）。大脚本走 stdin。
    3. 远端脚本必须是 LF。PS 5.1 的 $NewLine 是 CRLF，直接发过去 bash 会报
       `cd: $'/path\r'` 这种看起来像路径问题的错误。
    4. 脚本文件必须是 UTF-8 带 BOM。PS 5.1 读无 BOM 的 .ps1 会按系统 ANSI 解码，
       中文字符串全变乱码并可能破坏引号配对。
    5. 不能把 $Host 当参数名，它是 PowerShell 只读变量。
    6. 本地 CRLF、服务器 LF，校验哈希前先抹平行尾。
    7. 每次部署前备份到 .deploy-backup-<时间戳>/。

.PARAMETER Files
    相对 cloud-backend 的路径列表。

.PARAMETER SshHost
    SSH 主机别名，默认 rokid-r640。

.PARAMETER NoVerify
    跳过部署后的接口探测。

.EXAMPLE
    .\deploy-backend.ps1
    .\deploy-backend.ps1 -Files app/services/kb_search.py -NoVerify
#>
[CmdletBinding()]
param(
    [string[]]$Files = @(
        'app/services/kb_search.py',
        'app/routers/knowledge_base.py',
        'app/models/knowledge_base.py',
        'app/routers/agent.py',
        'app/services/rag_index.py',
        'app/services/agent_refs.py'
    ),
    [string]$SshHost = 'rokid-r640',
    [string]$RemoteRoot = '/home/baize/rokid_glass/cloud-backend',
    [int]$Retries = 4,
    [switch]$NoVerify
)

$ErrorActionPreference = 'Stop'
$localRoot = Join-Path $PSScriptRoot 'cloud-backend'
if (-not (Test-Path $localRoot)) { throw "找不到 cloud-backend 目录：$localRoot" }

function Get-LfSha256 {
    param([byte[]]$Bytes)
    $norm = New-Object System.Collections.Generic.List[byte]
    for ($i = 0; $i -lt $Bytes.Length; $i++) {
        if ($Bytes[$i] -eq 13 -and ($i + 1) -lt $Bytes.Length -and $Bytes[$i + 1] -eq 10) { continue }
        $norm.Add($Bytes[$i])
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    return ([BitConverter]::ToString($sha.ComputeHash($norm.ToArray())) -replace '-', '').ToLower()
}

function Invoke-RemoteScript {
    param([string]$Script, [int]$Attempts = 4)
    # 远端 bash 只认 LF，必须显式归一化
    $normalized = ($Script -replace "`r`n", "`n")
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($normalized))
    for ($i = 1; $i -le $Attempts; $i++) {
        $result = $encoded | & ssh -o BatchMode=yes -o ConnectTimeout=20 -o ServerAliveInterval=5 $SshHost 'base64 -d | bash'
        if ($LASTEXITCODE -eq 0) { return @($result) }
        Write-Warning "远端执行第 $i 次失败，重试…"
        Start-Sleep -Seconds 3
    }
    throw '远端执行连续失败'
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = $RemoteRoot + '/.deploy-backup-' + $stamp

$plan = @()
foreach ($rel in $Files) {
    $local = Join-Path $localRoot ($rel -replace '/', '\')
    if (-not (Test-Path $local)) { throw "本地文件不存在：$local" }
    $bytes = [System.IO.File]::ReadAllBytes($local)
    $plan += [pscustomobject]@{
        Rel    = $rel
        B64    = [Convert]::ToBase64String($bytes)
        Hash   = Get-LfSha256 $bytes
        Length = $bytes.Length
    }
}

Write-Host '== 一次会话完成：备份 + 落盘 + 校验 ==' -ForegroundColor Cyan
$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('set -e')
$lines.Add("cd '$RemoteRoot'")
$lines.Add("mkdir -p '$backup'")

foreach ($item in $plan) {
    $rel = $item.Rel
    # base64 字符集不含单引号，用单引号包裹安全
    $lines.Add("printf '%s' '$($item.B64)' | base64 -d > '$rel.tmp'")
    $lines.Add("if [ -f '$rel' ]; then cp -a '$rel' '$backup/'; fi")
    $lines.Add("mv '$rel.tmp' '$rel'")
}

$lines.Add('echo "---HASHES---"')
foreach ($item in $plan) {
    $rel = $item.Rel
    # 用 cat | tr 而不是 tr < file：PowerShell 解析器会拒绝双引号里的 < 重定向符
    $lines.Add("printf '%s  %s\n' `"`$(cat '$rel' | tr -d '\r' | sha256sum | cut -d' ' -f1)`" '$rel'")
}

$raw = Invoke-RemoteScript ($lines -join "`n")

$remoteHashes = @{}
$inHashes = $false
foreach ($line in $raw) {
    $text = ([string]$line).Trim()
    if ($text -eq '---HASHES---') { $inHashes = $true; continue }
    if (-not $inHashes -or -not $text) { continue }
    $parts = $text -split '\s+', 2
    if ($parts.Count -eq 2 -and $parts[0].Length -eq 64) {
        $remoteHashes[$parts[1].Trim()] = $parts[0].ToLower()
    }
}

if ($remoteHashes.Count -eq 0) {
    Write-Warning '  没有解析到远端哈希，原始输出：'
    $raw | ForEach-Object { Write-Warning ("    {0}" -f $_) }
    throw '部署失败：无法确认远端文件状态'
}

$bad = New-Object System.Collections.Generic.List[string]
foreach ($item in $plan) {
    $got = $remoteHashes[$item.Rel]
    if ($got -eq $item.Hash) {
        Write-Host ('  OK {0} ({1} 字节, {2})' -f $item.Rel, $item.Length, $item.Hash.Substring(0, 12))
    } else {
        $gotText = '缺失'
        if ($got) { $gotText = $got.Substring(0, 12) }
        $bad.Add(('{0}: 本地 {1} / 远端 {2}' -f $item.Rel, $item.Hash.Substring(0, 12), $gotText))
    }
}
if ($bad.Count -gt 0) {
    throw ('哈希不一致，部署中止：' + [Environment]::NewLine + ($bad -join [Environment]::NewLine))
}
Write-Host '  哈希全部一致'

if ($NoVerify) {
    Write-Host '== 已跳过接口探测（-NoVerify） ==' -ForegroundColor Yellow
    return
}

Write-Host '== 确认热重载 ==' -ForegroundColor Cyan
Start-Sleep -Seconds 6
$probe = @(
    'docker logs --tail 5 rokid-backend 2>&1 | tail -5',
    'echo "---HTTP---"',
    "curl -s -o /dev/null -w '%{http_code}' 'http://127.0.0.1:6081/api/kb/search?q='"
) -join "`n"
$probeRaw = Invoke-RemoteScript $probe
$httpCode = ''
foreach ($line in $probeRaw) {
    $text = ([string]$line).Trim()
    if ($text -match '^\d{3}$') { $httpCode = $text }
    elseif ($text -and $text -ne '---HTTP---') { Write-Host "  $text" }
}
Write-Host ('  /api/kb/search 探测：HTTP {0}' -f $httpCode)
if (@('200', '401', '403') -notcontains $httpCode) {
    Write-Warning "  接口返回 $httpCode，请检查容器日志"
}

Write-Host ''
Write-Host ('部署完成。回滚：cp -a {0}/* {1}/' -f $backup, $RemoteRoot) -ForegroundColor Green
