param(
    [Parameter(Mandatory = $true)][string]$Destination,
    [string]$InstallRoot = 'C:\MioCore'
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($InstallRoot)
$target = [System.IO.Path]::GetFullPath($Destination)
if ($root -ne 'C:\MioCore') { throw 'Unexpected install root.' }
if ($target.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup destination must be outside C:\MioCore.'
}
New-Item -ItemType Directory -Force -Path $target | Out-Null
& (Join-Path $root 'app\MioWorker.exe') stop
& (Join-Path $root 'app\MioWeb.exe') stop
try {
    robocopy (Join-Path $root 'data') (Join-Path $target 'data') /MIR /COPY:DAT /DCOPY:DAT | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Data backup failed with exit code $LASTEXITCODE" }
    robocopy (Join-Path $root 'workspaces') (Join-Path $target 'workspaces') /MIR /COPY:DAT /DCOPY:DAT | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Workspace backup failed with exit code $LASTEXITCODE" }
} finally {
    & (Join-Path $root 'app\MioWeb.exe') start
    & (Join-Path $root 'app\MioWorker.exe') start
}
Write-Host "Backup written to $target. Protect it: it contains the Deploy Key and private chats."
