$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (Get-NetTCPConnection -State Listen -LocalPort 8765 -ErrorAction SilentlyContinue) { throw 'Port 8765 is in use.' }
if (!(Test-Path 'backend/.venv/Scripts/python.exe')) {
    python -m venv backend/.venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment creation failed.' }
}
& backend/.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
Push-Location frontend
try {
    npm.cmd install
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
Write-Host 'Workbench: http://127.0.0.1:8765'
& backend/.venv/Scripts/python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8765 --no-access-log
