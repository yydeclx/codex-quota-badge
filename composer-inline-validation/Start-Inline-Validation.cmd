@echo off
"%ProgramFiles%\PowerShell\7\pwsh.exe" -NoLogo -NoProfile -File "%~dp0switch.ps1"
if errorlevel 1 pause
