@echo off
setlocal EnableExtensions
title BOOYAH ARENA - MongoDB Server
cd /d "%~dp0"

REM ------------------------------------------------------------
REM BOOYAH ARENA launcher
REM This launcher elevates itself because starting a Windows
REM service may require Administrator permission.
REM ------------------------------------------------------------
net session >nul 2>&1
if not "%errorlevel%"=="0" (
    echo Requesting Administrator permission...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo ==========================================
echo       BOOYAH ARENA - STARTING
echo ==========================================
echo.

REM Check that the MongoDB Windows service exists.
sc query MongoDB >nul 2>&1
if errorlevel 1 (
    echo [ERROR] MongoDB Windows service was not found.
    echo.
    echo Install MongoDB Community Server and select:
    echo   Install MongoD as a Service
    echo.
    pause
    exit /b 1
)

REM Start MongoDB only when it is not already running.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$s=Get-Service -Name MongoDB -ErrorAction Stop; if ($s.Status -ne 'Running') { Start-Service -Name MongoDB -ErrorAction Stop; $s.WaitForStatus('Running','00:00:15') }; if ((Get-Service -Name MongoDB).Status -ne 'Running') { exit 1 }"

if errorlevel 1 (
    echo.
    echo [ERROR] MongoDB could not be started.
    echo.
    echo Run this in Administrator PowerShell to see the exact error:
    echo     Get-Service MongoDB
    echo     Start-Service MongoDB
    echo.
    pause
    exit /b 1
)

echo [OK] MongoDB is running.

REM Use existing virtual environment, or create it.
if exist "%~dp0.venv\Scripts\python.exe" (
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
    where py >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python was not found.
        echo Install Python 3 and try again.
        echo.
        pause
        exit /b 1
    )

    echo [INFO] Creating Python virtual environment...
    py -m venv "%~dp0.venv"
    if errorlevel 1 (
        echo [ERROR] Could not create Python virtual environment.
        echo.
        pause
        exit /b 1
    )
    set "PYTHON=%~dp0.venv\Scripts\python.exe"
)

REM Install/update project dependencies.
if exist "%~dp0requirements.txt" (
    echo [INFO] Checking Python dependencies...
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt"
    if errorlevel 1 (
        echo [ERROR] Dependency installation failed.
        echo.
        pause
        exit /b 1
    )
)

echo.
echo [OK] Starting BOOYAH ARENA
echo [OK] Website: http://127.0.0.1:8000
echo.
echo Keep this window open while using the website.
echo Press Ctrl+C to stop the server.
echo.

start "" "http://127.0.0.1:8000"
"%PYTHON%" "%~dp0server.py"

echo.
echo BOOYAH ARENA server stopped.
pause
endlocal
