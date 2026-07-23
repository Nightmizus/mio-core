param(
    [Parameter(Mandatory = $true)][string]$SourceRoot,
    [string]$InstallRoot = 'C:\MioCore'
)
$ErrorActionPreference = 'Stop'
$root = [System.IO.Path]::GetFullPath($InstallRoot)
$source = [System.IO.Path]::GetFullPath($SourceRoot)
if ($root -ne 'C:\MioCore') { throw 'Unexpected install root.' }
if (-not (Test-Path -LiteralPath (Join-Path $source 'pyproject.toml'))) {
    throw 'SourceRoot is not a Mio Core checkout.'
}
$appRoot = Join-Path $root 'app'
$webService = Join-Path $appRoot 'MioWeb.exe'
$workerService = Join-Path $appRoot 'MioWorker.exe'
& $workerService stop
& $webService stop
try {
    robocopy $source $appRoot /MIR /XD .git .venv node_modules data workspaces tools /XF .env | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Application update failed with exit code $LASTEXITCODE" }
    Set-Location -LiteralPath $appRoot
    & '.\.venv\Scripts\python.exe' -m pip install .
    Push-Location frontend
    corepack pnpm install --frozen-lockfile
    corepack pnpm run build
    Pop-Location
    & '.\.venv\Scripts\python.exe' -m alembic upgrade head
} finally {
    & $webService start
    & $workerService start
}
Write-Host 'Mio Core upgrade completed.'
