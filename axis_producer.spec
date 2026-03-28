# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AXIS Producer."""

import os

block_cipher = None
project_dir = os.path.abspath('.')
src_dir = os.path.join(project_dir, 'src')

# Data files to include
datas = [
    ('static/dashboard.html', 'static'),
    ('static/nux.html', 'static'),
    ('static/phone_mic.html', 'static'),
    ('static/consent_notice.html', 'static'),
    ('static/favicon.ico', 'static'),
    ('static/axis.ico', 'static'),
    ('src', 'src'),
]

# Hidden imports that PyInstaller misses
hiddenimports = [
    'sounddevice',
    'faster_whisper',
    'ctranslate2',
    'anthropic',
    'numpy',
    'pystray',
    'PIL',
    'PIL.Image',
    'win32com',
    'win32com.client',
    'pythoncom',
    'supabase',
    'websockets',
    'qrcode',
    'httpx',
    'cryptography',
    'llm_provider',
    'backend_client',
    'settings',
    'session_controller',
    'tray_app',
    'producer',
    'cloud_sync',
]

a = Analysis(
    ['launcher.py'],
    pathex=[project_dir, src_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'pandas',
        'pytest', 'unittest', 'webrtcvad',
        'torch', 'torchvision', 'torchaudio',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='AXIS_Producer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='static/axis.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AXIS_Producer',
)
