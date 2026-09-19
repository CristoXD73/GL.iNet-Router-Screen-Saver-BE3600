@echo off
rem Drag a .bea file onto this icon to replace the animation on your router.
rem Or double-click it, then drag a file into the window.
title GL.iNet Router Screen Saver (BE3600) - Animation drop zone
if "%~1"=="" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\set-animation.ps1"
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\set-animation.ps1" -Path "%~1"
)
echo.
pause
