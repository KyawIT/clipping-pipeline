@echo off
rem Make clips on Windows: clip.cmd [link] [--mode stream^|podcast]. Without a link it asks for one.
rem Runs clip.ps1 without changing the PowerShell execution policy.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0clip.ps1" %*
exit /b %ERRORLEVEL%
