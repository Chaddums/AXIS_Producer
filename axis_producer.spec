# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AXIS Producer.

Build with: pyinstaller axis_producer.spec
Output: dist/AXIS_Producer/
"""

import os
import sys

block_cipher = None
project_dir = os.path.abspath('.')

# Collect all Python source files
py_files = [f for f in os.listdir(project_dir)
            if f.endswith('.py') and f not in ('setup.py', 'setup.bat',
                                                'test_producer.py', 'test_digest.py',
                                                'test_features.py', 'triage.py',
                                                'verify.py')]

# Data files to include
datas = [
    ('dashboard.html', '.'),
    ('nux.html', '.'),
    ('phone_mic.html', '.'),
    ('CLAUDE.md', '.'),
    ('PRODUCT.md', '.'),
]

# Hidden imports that PyInstaller misses
hiddenimports = [
    'sounddevice',
    'webrtcvad',
    'faster_whisper',
    'ctranslate2',
    'anthropic',
    'numpy',
    'pystray',
    'PIL',
    'win32com',
    'win32com.client',
    'pythoncom',
    'supabase',
    'websockets',
    'qrcode',
    'httpx',
    'cryptography',
    'bcrypt',
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
        'pytest', 'unittest',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# Collect all faster-whisper and ctranslate2 data/binaries
from PyInstaller.utils.hooks import collect_all
for pkg in ['faster_whisper', 'ctranslate2']:
    try:
        tmp_ret = collect_all(pkg)
        a.datas += tmp_ret[0]
        a.binaries += tmp_ret[1]
        a.hiddenimports += tmp_ret[2]
    except Exception:
        pass

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
    console=False,  # No console window — tray app
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # TODO: add AXIS icon
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='AXIS_Producer',
)
