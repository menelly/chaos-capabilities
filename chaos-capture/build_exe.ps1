# Build ChaosCapture.exe — one file, double-clickable.
# Run:   powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# ⚠️ BUILD IN A CLEAN VENV, NOT YOUR DESK PYTHON (learned 2026-09-12).
# app.py imports faster-whisper lazily and optionally, but PyInstaller's static
# analysis bundles anything IMPORTABLE. On a machine where faster-whisper/torch/
# ctranslate2 are installed globally, the "32 MB" EXE came out at 3.03 GB.
# So this script builds its own throwaway venv with only what the app needs
# (the list below is every third-party import in app.py), and passes explicit
# excludes as a second guard. Expected result: ~30 MB.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path "build_venv\Scripts\python.exe")) {
    Write-Host "Creating build_venv ..."
    python -m venv build_venv
}
$py = ".\build_venv\Scripts\python.exe"
& $py -m pip install -q --upgrade pip
& $py -m pip install -q pyinstaller pillow sounddevice numpy keyboard pywin32

# Guard: the venv must NOT see the heavy optional deps.
$leak = & $py -c "import importlib.util as u; print(','.join(m for m in ['faster_whisper','torch','ctranslate2','onnxruntime'] if u.find_spec(m)))"
if ($leak) { throw "build_venv can import [$leak] - that is the 3 GB trap. Delete build_venv and rerun." }

& $py -m PyInstaller --onefile --windowed --name ChaosCapture `
  --hidden-import sounddevice --hidden-import numpy `
  --exclude-module faster_whisper --exclude-module torch --exclude-module ctranslate2 `
  --exclude-module onnxruntime --exclude-module torchaudio `
  app.py

$exe = Get-Item "dist\ChaosCapture.exe"
$mb  = [math]::Round($exe.Length / 1MB, 1)
Write-Host ""
Write-Host "Done. dist\ChaosCapture.exe = $mb MB"
if ($mb -gt 100) { Write-Host "⚠️ That is far too big - something heavy got bundled. Check the leak guard above." }
Write-Host "Note: unsigned EXEs trip Windows SmartScreen ('Windows protected"
Write-Host "your PC' -> More info -> Run anyway). The Microsoft Store build"
Write-Host "avoids that wall entirely - see the README."
