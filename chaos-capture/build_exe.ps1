# Build ChaosCapture.exe — one file, double-clickable.
# Needs: pip install pyinstaller pillow sounddevice numpy keyboard pywin32
# Run:   powershell -ExecutionPolicy Bypass -File build_exe.ps1

pyinstaller --onefile --windowed --name ChaosCapture `
  --hidden-import sounddevice --hidden-import numpy `
  app.py

Write-Host ""
Write-Host "Done. The EXE is in .\dist\ChaosCapture.exe"
Write-Host "Note: unsigned EXEs trip Windows SmartScreen ('Windows protected"
Write-Host "your PC' -> More info -> Run anyway). The Microsoft Store build"
Write-Host "avoids that wall entirely - see the README."
