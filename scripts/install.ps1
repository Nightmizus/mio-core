param(
    [string]$InstallRoot = 'C:\MioCore',
    [Parameter(Mandatory = $true)][string]$ServiceUser,
    [Parameter(Mandatory = $true)][SecureString]$ServicePassword,
    [Parameter(Mandatory = $true)][string]$WinSWPath
)
$ErrorActionPreference = 'Stop'
$sourceRoot = Split-Path -Parent $PSScriptRoot
$resolvedRoot = [System.IO.Path]::GetFullPath($InstallRoot)
if ($resolvedRoot -ne 'C:\MioCore') {
    throw 'The supported service root is C:\MioCore.'
}
$appRoot = Join-Path $resolvedRoot 'app'
$dataRoot = Join-Path $resolvedRoot 'data'
$workspaceRoot = Join-Path $resolvedRoot 'workspaces'
$toolRoot = Join-Path $appRoot 'tools'
New-Item -ItemType Directory -Force -Path $appRoot, $dataRoot, $workspaceRoot, $toolRoot | Out-Null

$winswSource = [System.IO.Path]::GetFullPath($WinSWPath)
if (-not (Test-Path -LiteralPath $winswSource -PathType Leaf)) {
    throw 'WinSW-x64.exe was not found at the supplied path.'
}

robocopy $sourceRoot $appRoot /MIR /XD .git .venv node_modules data workspaces | Out-Null
if ($LASTEXITCODE -gt 7) { throw "Robocopy failed with exit code $LASTEXITCODE" }
$installedWinSW = Join-Path $toolRoot 'WinSW-x64.exe'
Copy-Item -LiteralPath $winswSource -Destination $installedWinSW -Force

Set-Location -LiteralPath $appRoot
py -3.12 -m venv .venv
& '.\.venv\Scripts\python.exe' -m pip install --upgrade pip
& '.\.venv\Scripts\python.exe' -m pip install .
Push-Location frontend
corepack pnpm install --frozen-lockfile
corepack pnpm run build
Pop-Location

$credential = New-Object System.Management.Automation.PSCredential($ServiceUser, $ServicePassword)
$plainPassword = $credential.GetNetworkCredential().Password
foreach ($service in @(
    @{ Name = 'MioWeb'; Script = 'run-web.ps1'; Description = 'Mio Core web service' },
    @{ Name = 'MioWorker'; Script = 'run-worker.ps1'; Description = 'Mio Core publishing worker' }
)) {
    $exe = Join-Path $appRoot "$($service.Name).exe"
    $xml = Join-Path $appRoot "$($service.Name).xml"
    Copy-Item -LiteralPath $installedWinSW -Destination $exe -Force
    $xmlText = @"
<service>
  <id>$($service.Name)</id>
  <name>$($service.Name)</name>
  <description>$($service.Description)</description>
  <executable>powershell.exe</executable>
  <arguments>-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$appRoot\scripts\$($service.Script)"</arguments>
  <workingdirectory>$appRoot</workingdirectory>
  <log mode="roll-by-size"><sizeThreshold>10485760</sizeThreshold><keepFiles>4</keepFiles></log>
  <onfailure action="restart" delay="10 sec"/>
</service>
"@
    [System.IO.File]::WriteAllText($xml, $xmlText, (New-Object System.Text.UTF8Encoding($false)))
    & $exe uninstall 2>$null
    & $exe install
    sc.exe config $($service.Name) obj= $ServiceUser password= $plainPassword | Out-Null
    & $exe start
}
$plainPassword = $null
Write-Host 'Mio Core services installed. Configure .env and restart both services.'
