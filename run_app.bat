@echo off
REM run_app.bat
REM Launcher for the inventory management app.
REM
REM Always uses this project's .venv (virtual environment) python.exe to run
REM inventory_app\main.py, regardless of which Python the system PATH would
REM otherwise resolve to (e.g. a separate system-wide install at
REM C:\python310). This guarantees the app runs with the dependencies
REM (opencv-python-headless, etc.) installed into .venv.
REM
REM Usage: double-click this file. No need to open a command prompt manually.
REM
REM NOTE: kept ASCII-only intentionally. A batch file containing non-ASCII
REM text saved as UTF-8 (no BOM) is misread by cmd.exe under the Japanese
REM (cp932) console code page, which can corrupt command parsing.

setlocal

set "SCRIPT_DIR=%~dp0"
set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
set "MAIN_PY=%SCRIPT_DIR%inventory_app\main.py"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtual environment not found: %PYTHON_EXE%
    echo Please create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\python.exe -m pip install -r inventory_app\requirements.txt
    pause
    exit /b 1
)

"%PYTHON_EXE%" "%MAIN_PY%"

if errorlevel 1 (
    echo.
    echo [ERROR] The app exited with an error. See the messages above.
    pause
)

endlocal
