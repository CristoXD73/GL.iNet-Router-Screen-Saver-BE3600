@echo off
rem Double-click me. Installs the GL.iNet Router Screen Saver (BE3600) on your router.
rem Extra options are passed through, e.g.:  Install.cmd -Router 192.168.8.1
title GL.iNet Router Screen Saver (BE3600) - Installer
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install.ps1" %*
echo.
pause
