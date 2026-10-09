@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo FireGuard single-frame baseline. Press Q or Esc in the video window to stop.
"%~dp0.venv-pytorch\Scripts\python.exe" "%~dp0main.py" --source "%~dp0videos\fireplace_cc0.webm" --baseline
echo.
pause
