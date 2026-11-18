@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 1
call "%~dp0start_desktop.cmd" --monitor-center %*
exit /b %ERRORLEVEL%
