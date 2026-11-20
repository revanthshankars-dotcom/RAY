# RAY installer for Windows (PowerShell).
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -WithDesktop -WithSpeech
#
# Creates .venv next to main.py, installs the runtime, and optionally the native
# window (PySide6) and offline speech recognition (faster-whisper).

[CmdletBinding()]
param(
    [switch]$WithDesktop,
    [switch]$WithSpeech,
    [switch]$WithAutomation,
    [switch]$Dev
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $root

Write-Host "RAY installer" -ForegroundColor Cyan
Write-Host "Project: $root"

$python = $null
foreach ($candidate in @("py", "python")) {
    try {
        & $candidate -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $python = $candidate; break }
    } catch { }
}

if (-not $python) {
    Write-Host "Python 3.10+ was not found. Install it from https://www.python.org/downloads/windows/ and tick 'Add python.exe to PATH'." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment in .venv ..."
    & $python -m venv .venv
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
& $venvPython -m pip install --upgrade pip wheel | Out-Null
& $venvPython -m pip install -r requirements.txt

if ($WithDesktop) {
    Write-Host "Installing the native window (PySide6) ..."
    & $venvPython -m pip install "PySide6>=6.6"
}
if ($WithSpeech) {
    Write-Host "Installing offline speech recognition (faster-whisper) ..."
    & $venvPython -m pip install "faster-whisper>=1.0"
}
if ($WithAutomation) {
    Write-Host "Installing keyboard, mouse and clipboard support ..."
    & $venvPython -m pip install "pynput>=1.7" "pyperclip>=1.8"
}
if ($Dev) {
    & $venvPython -m pip install pytest
}

Write-Host ""
Write-Host "Environment check:" -ForegroundColor Cyan
& $venvPython main.py --check

Write-Host ""
Write-Host "Done. Start RAY with:  .venv\Scripts\python.exe main.py" -ForegroundColor Green
Write-Host "Or double-click RAY.bat (it uses .venv automatically)."
Write-Host "Then open Settings -> AI Providers and add your Grok / Gemini / Fish Audio keys."
