#requires -Version 7.0
[CmdletBinding()]
param([string]$SshAlias = 'lon1')
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$remotePath = '/tmp/isitmeornot-' + [guid]::NewGuid().ToString('N')
$archive = Join-Path ([IO.Path]::GetTempPath()) ('isitmeornot-' + [guid]::NewGuid().ToString('N') + '.tar.gz')
try {
    foreach ($tool in @('ssh','scp','tar')) {
        if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Required tool missing: $tool" }
    }
    if ($SshAlias -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]*$') { throw 'Use an SSH config alias such as lon1.' }
    Write-Host "Connecting using your existing '$SshAlias' SSH alias..."
    & tar -czf $archive -C $PSScriptRoot site server.py checker.py certbot_guard.py deploy-server.py nginx_frontend.py
    if ($LASTEXITCODE -ne 0) { throw 'Could not create site archive.' }
    & ssh $SshAlias "umask 077; mkdir '$remotePath'"
    if ($LASTEXITCODE -ne 0) { throw 'SSH connection failed.' }
    & scp $archive "${SshAlias}:$remotePath/site.tar.gz"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed.' }
    Write-Host 'Installing URL checker through Nginx. Enter your lon1 sudo password when prompted.'
    & ssh -t $SshAlias "tar -xzf '$remotePath/site.tar.gz' -C '$remotePath' && sudo python3 '$remotePath/deploy-server.py'"
    if ($LASTEXITCODE -ne 0) { throw "Installation failed. Installer retained at $remotePath for diagnosis." }
    & ssh $SshAlias "rm -rf -- '$remotePath'"
    Write-Host 'Ready: https://isitmeornot.online' -ForegroundColor Green
} finally {
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
}
