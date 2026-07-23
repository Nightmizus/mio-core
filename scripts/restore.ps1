param(
    [Parameter(Mandatory = $true)][string]$Source,
    [string]$InstallRoot = 'C:\MioCore'
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($InstallRoot)
$backup = [System.IO.Path]::GetFullPath($Source)
if ($root -ne 'C:\MioCore' -or -not (Test-Path -LiteralPath (Join-Path $backup 'data'))) {
    throw 'Invalid restore source or install root.'
}
& (Join-Path $root 'app\MioWorker.exe') stop
& (Join-Path $root 'app\MioWeb.exe') stop
try {
    robocopy (Join-Path $backup 'data') (Join-Path $root 'data') /MIR /COPY:DAT /DCOPY:DAT | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Data restore failed with exit code $LASTEXITCODE" }
    if (Test-Path -LiteralPath (Join-Path $backup 'workspaces')) {
        robocopy (Join-Path $backup 'workspaces') (Join-Path $root 'workspaces') /MIR /COPY:DAT /DCOPY:DAT | Out-Null
    }
} finally {
    & (Join-Path $root 'app\MioWeb.exe') start
    & (Join-Path $root 'app\MioWorker.exe') start
}
