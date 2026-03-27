@echo off
title AXIS Producer
cd /d "%~dp0"
python launcher.py
if errorlevel 1 (
    echo.
    echo  AXIS Producer exited with an error.
    pause
)
