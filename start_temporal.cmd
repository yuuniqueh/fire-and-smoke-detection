@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo FireGuard temporal-only comparison. Press Q or Esc to stop.
"%~dp0.venv-pytorch\Scripts\python.exe" "%~dp0main.py" --source "%~dp0videos\fireplace_cc0.webm" --temporal-only
echo.
pause
