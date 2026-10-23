@echo off
REM Launch the PS5 Screen Capture GUI.
cd /d "%~dp0"
set PYTHONWARNINGS=ignore
"venv\Scripts\python.exe" -m ps5capture.app
