@echo off
rem Double-click me. Installs the BE3600 animation screensaver on your router.
rem Extra options are passed through, e.g.:  Install.cmd -Router 192.168.8.1
title BE3600 Screensaver - Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1" %*
echo.
pause
