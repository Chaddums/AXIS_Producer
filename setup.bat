@echo off
echo.
echo  ========================================
echo   AXIS Producer - Team Setup
echo  ========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.11+ first.
    echo  https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Install dependencies
echo  [1/4] Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo  ERROR: pip install failed. Check the output above.
    pause
    exit /b 1
)
echo  Done.
echo.

:: Get user identity
set /p USERNAME="  [2/4] Your name (e.g. adam, stu): "
if "%USERNAME%"=="" (
    echo  ERROR: Name is required.
    pause
    exit /b 1
)

:: Write settings
echo  [3/4] Writing settings...
python -c "
import json, os

settings = {
    'mic_device': None,
    'loopback_device': None,
    'whisper_model': 'base.en',
    'batch_interval': 300,
    'log_dir': '.',
    'auto_detect': True,
    'vad_sensitivity': 1,
    'chat_monitor': True,
    'slack_monitor': False,
    'slack_channel_ids': [],
    'slack_poll_interval': 15.0,
    'email_monitor': False,
    'email_poll_interval': 30.0,
    'email_unread_only': True,
    'focus_advisor': True,
    'vcs_monitor': True,
    'vcs_repo_path': None,
    'vcs_poll_interval': 120.0,
    'calendar_monitor': False,
    'calendar_poll_interval': 60.0,
    'pre_meeting_minutes': 10,
    'roadmap_path': None,
    'daily_briefings': False,
    'standup_hour': 9,
    'checkin_hour': 13,
    'wrapup_hour': 17,
    'nag_interval_hours': 4,
    'cloud_sync': True,
    'supabase_url': os.environ.get('SUPABASE_URL', ''),
    'supabase_key': os.environ.get('SUPABASE_KEY', ''),
    'user_identity': '%USERNAME%',
    'synthesis_interval': 900,
    'claude_monitor': True,
    'claude_project_paths': [],
    'claude_poll_interval': 3.0,
    'verbose': True,
}

with open('tray_settings.json', 'w') as f:
    json.dump(settings, f, indent=2)
print('  Settings written to tray_settings.json')
"
echo.

:: Check API key
if "%ANTHROPIC_API_KEY%"=="" (
    echo  [4/4] WARNING: ANTHROPIC_API_KEY is not set.
    echo  You need this for voice transcription batches.
    echo  Set it with: set ANTHROPIC_API_KEY=sk-ant-...
    echo.
) else (
    echo  [4/4] ANTHROPIC_API_KEY found.
    echo.
)

echo  ========================================
echo   Setup complete!
echo  ========================================
echo.
echo  To start AXIS Producer:
echo    python tray_app.py
echo.
echo  To open the live dashboard:
echo    Open dashboard.html in your browser
echo.
echo  To list audio devices:
echo    python axis_producer.py --list-devices
echo    Then update mic_device/loopback_device in tray_settings.json
echo.
pause
