$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $appRoot
& (Join-Path $appRoot '.venv\Scripts\python.exe') -m mio_core.worker
