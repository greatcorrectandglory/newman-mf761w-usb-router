@echo off
setlocal
title Newman MF761W Hotspot
powershell.exe -NoProfile -ExecutionPolicy RemoteSigned -File "%~dp0Setup.ps1"
set "MF_RESULT=%ERRORLEVEL%"
echo.
pause
exit /b %MF_RESULT%
