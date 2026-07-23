$ErrorActionPreference = 'Stop'
$appRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $appRoot
& (Join-Path $appRoot '.venv\Scripts\python.exe') -m alembic upgrade head
& (Join-Path $appRoot '.venv\Scripts\python.exe') -m mio_core.main
