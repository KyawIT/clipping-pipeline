@echo off
rem Start the Clipper web app (paste links, live progress, clip library) and open it in the browser.
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Not set up yet: run setup.cmd first.
  exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0clip.py" web %*
