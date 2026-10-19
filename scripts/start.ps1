param([int]$Port = 8000, [switch]$SkipInstall)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Creating .venv failed. Install Python 3.11+ first.' }
}
if (-not $SkipInstall) {
    & '.venv/Scripts/python.exe' -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
& '.venv/Scripts/python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port $Port --workers 1
