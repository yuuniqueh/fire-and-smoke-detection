@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
"%~dp0.venv-pytorch\Scripts\python.exe" "%~dp0main.py" --check
echo.
pause

