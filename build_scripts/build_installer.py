"""Build script — creates AXIS Producer Windows installer.

Steps:
1. Run PyInstaller to bundle the app
2. Run Inno Setup to create the installer .exe

Requirements:
- PyInstaller: pip install pyinstaller
- Inno Setup 6+: https://jrsoftware.org/isinfo.php
  (default install path: C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe)

Usage:
    python build_installer.py
"""

import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEC_FILE = os.path.join(PROJECT_DIR, "axis_producer.spec")
ISS_FILE = os.path.join(PROJECT_DIR, "installer.iss")

# Common Inno Setup install locations
ISCC_PATHS = [
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
]


def find_iscc() -> str | None:
    for path in ISCC_PATHS:
        if os.path.exists(path):
            return path
    return None


def main():
    print("=" * 60)
    print("AXIS Producer — Build Installer")
    print("=" * 60)

    # Step 1: PyInstaller
    print("\n[1/2] Running PyInstaller...")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--clean", "--noconfirm", SPEC_FILE],
        cwd=PROJECT_DIR,
    )
    if result.returncode != 0:
        print("PyInstaller failed.")
        sys.exit(1)
    print("PyInstaller complete.")

    # Verify output
    exe_path = os.path.join(PROJECT_DIR, "dist", "AXIS_Producer", "AXIS_Producer.exe")
    if not os.path.exists(exe_path):
        print(f"Expected output not found: {exe_path}")
        sys.exit(1)
    print(f"  Output: {exe_path}")

    # Step 2: Inno Setup
    iscc = find_iscc()
    if not iscc:
        print("\n[2/2] Inno Setup not found — skipping installer creation.")
        print("  Install Inno Setup 6 from https://jrsoftware.org/isinfo.php")
        print("  Then run: iscc installer.iss")
        print(f"\n  Portable build ready at: dist/AXIS_Producer/")
        return

    print(f"\n[2/2] Running Inno Setup ({iscc})...")
    result = subprocess.run([iscc, ISS_FILE], cwd=PROJECT_DIR)
    if result.returncode != 0:
        print("Inno Setup failed.")
        sys.exit(1)

    installer_path = os.path.join(PROJECT_DIR, "dist", "AXIS_Producer_Setup.exe")
    print(f"\nInstaller ready: {installer_path}")


if __name__ == "__main__":
    main()
