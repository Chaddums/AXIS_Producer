"""Desktop Edge Indicator — visual recording state indicator.

Shows recording state via system tray tooltip changes and optional
console title. The tray icon color change (in tray_app.py) is the
primary visual signal; this module provides supplementary indicators.
"""

import ctypes
import threading


class DesktopIndicator:
    """Supplementary recording state indicator."""

    def __init__(self):
        self._recording = False

    def start(self):
        """Initialize the indicator."""
        pass

    def stop(self):
        """Clean up."""
        self._set_title("")

    def show_recording(self):
        """Signal recording state."""
        if not self._recording:
            self._recording = True
            self._set_title("[RECORDING] AXIS Producer")
            # Flash the taskbar to draw attention
            self._flash_taskbar()

    def show_detecting(self):
        """Signal detecting state."""
        self._recording = False
        self._set_title("[LISTENING] AXIS Producer")

    def hide(self):
        """Signal idle state."""
        self._recording = False
        self._set_title("")

    @staticmethod
    def _set_title(title: str):
        """Set console window title as a secondary indicator."""
        try:
            if title:
                ctypes.windll.kernel32.SetConsoleTitleW(title)
            else:
                ctypes.windll.kernel32.SetConsoleTitleW("AXIS Producer")
        except Exception:
            pass

    @staticmethod
    def _flash_taskbar():
        """Flash the taskbar icon briefly to draw attention."""
        try:
            # Get console window handle and flash it
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                # FLASHW_ALL = 3, flash 2 times
                class FLASHWINFO(ctypes.Structure):
                    _fields_ = [
                        ("cbSize", ctypes.c_uint),
                        ("hwnd", ctypes.c_void_p),
                        ("dwFlags", ctypes.c_uint),
                        ("uCount", ctypes.c_uint),
                        ("dwTimeout", ctypes.c_uint),
                    ]
                fwi = FLASHWINFO()
                fwi.cbSize = ctypes.sizeof(FLASHWINFO)
                fwi.hwnd = hwnd
                fwi.dwFlags = 3  # FLASHW_ALL
                fwi.uCount = 2
                fwi.dwTimeout = 0
                ctypes.windll.user32.FlashWindowEx(ctypes.byref(fwi))
        except Exception:
            pass
