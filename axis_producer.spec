# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AXIS Producer."""

import os

block_cipher = None
project_dir = os.path.abspath('.')

# Data files to include
datas = [
    ('dashboard.html', '.'),
    ('nux.html', '.'),
    ('phone_mic.html', '.'),
    ('consent_notice.html', '.'),
    ('favicon.ico', '.'),
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
]

a = Analysis(
    ['launcher.py'],
    pathex=[project_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'tkinter', 'matplotlib', 'scipy', 'pandas',
        'pytest', 'unittest', 'webrtcvad',
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
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='axis.ico',
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
