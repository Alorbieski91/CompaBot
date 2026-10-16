@echo off
rem Double-click to update laanp's Icarus mods in your own game. Close Icarus first.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-IcarusClientMods.ps1" %*
echo.
pause
