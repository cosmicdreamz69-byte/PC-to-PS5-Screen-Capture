@echo off
REM One-time registration: link your PSN account + this PC to the console.
cd /d "%~dp0"
set PYTHONWARNINGS=ignore
"venv\Scripts\python.exe" -m ps5capture.register
pause
