@echo off
rem Double-click to let Motion Studio (in your browser) send animations straight to your router.
rem Leave the window open while you use Motion Studio's drop zone.
title GL.iNet Router Screen Saver (BE3600) - Studio Link
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\studio-link.ps1" %*
echo.
pause
