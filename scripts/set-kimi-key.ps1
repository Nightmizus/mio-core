param(
    [string]$InstallRoot = 'D:\MioCore'
)

$ErrorActionPreference = 'Stop'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if (-not $isAdministrator) {
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', "`"$PSCommandPath`"",
        '-InstallRoot', "`"$InstallRoot`""
    )
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments
    exit
}

$envPath = Join-Path $InstallRoot 'app\.env'
if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
    throw "Mio Core configuration was not found: $envPath"
}

$secure = Read-Host 'Paste Kimi API Key' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
    if ([string]::IsNullOrWhiteSpace($key) -or $key.Length -lt 12) {
        throw 'API Key is empty or too short.'
    }

    $found = $false
    $updated = Get-Content -LiteralPath $envPath -Encoding UTF8 | ForEach-Object {
        if ($_ -match '^MIO_LLM_API_KEY=') {
            $found = $true
            "MIO_LLM_API_KEY=$key"
        } else {
            $_
        }
    }
    if (-not $found) {
        $updated += "MIO_LLM_API_KEY=$key"
    }

    [IO.File]::WriteAllLines(
        $envPath,
        $updated,
        [Text.UTF8Encoding]::new($false)
    )

    Restart-Service -Name 'MioWorker', 'MioWeb' -Force
    (Get-Service -Name 'MioWorker').WaitForStatus(
        'Running',
        [TimeSpan]::FromSeconds(20)
    )
    (Get-Service -Name 'MioWeb').WaitForStatus(
        'Running',
        [TimeSpan]::FromSeconds(20)
    )
    Write-Host 'Kimi API Key saved. Mio Core services restarted.' -ForegroundColor Green
} finally {
    if ($bstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    }
    $key = $null
    $secure = $null
}
