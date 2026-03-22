@echo off
title AXIS Producer
cd /d "%~dp0"

:: First run? Launch setup wizard
if not exist tray_settings.json (
    echo  First time? Let's get you set up...
    echo.
    python setup.py
    if errorlevel 1 (
        echo  Setup failed or was cancelled.
        pause
        exit /b 1
    )
    echo.
)

:: Launch everything
python launcher.py
if errorlevel 1 (
    echo.
    echo  AXIS Producer exited with an error.
    pause
)
