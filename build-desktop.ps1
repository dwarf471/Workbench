param([string]$InnoCompiler)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$desktopPython = Join-Path $PSScriptRoot 'backend/.venv/Scripts/python.exe'
if (!(Test-Path $desktopPython)) { throw 'Create backend/.venv and install backend/requirements-build.txt first.' }
Push-Location frontend
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
& $desktopPython -m PyInstaller --noconfirm --clean packaging/workbench.spec
if ($LASTEXITCODE -ne 0) { throw 'Desktop bundle failed.' }
if ($InnoCompiler) {
    $bootstrapper = Join-Path $PSScriptRoot '.build-tools/MicrosoftEdgeWebview2Setup.exe'
    if (!(Test-Path $bootstrapper)) {
        New-Item -ItemType Directory -Force '.build-tools' | Out-Null
        Invoke-WebRequest 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $bootstrapper
    }
    $signature = Get-AuthenticodeSignature $bootstrapper
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw 'WebView2 bootstrapper signature verification failed.'
    }
    & $InnoCompiler packaging/workbench.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
}
Write-Host 'Desktop bundle: dist/PersonalWorkbench/PersonalWorkbench.exe'
