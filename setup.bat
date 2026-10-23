@echo off
REM One-time setup: build the virtual environment and install dependencies.
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo Creating Python 3.8 virtual environment...
    py -3.8 -m venv venv
    if errorlevel 1 (
        echo.
        echo Could not create the venv with Python 3.8. Is it installed?
        echo Check with:  py -0
        pause
        exit /b 1
    )
)

echo Installing dependencies ^(this downloads PySide6 + PyAV, ~150 MB^)...
"venv\Scripts\python.exe" -m pip install --upgrade pip
"venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Setup complete.
echo   1) Enable Remote Play on the PS5, then run  register.bat
echo   2) Launch the capture app with  run.bat
pause
