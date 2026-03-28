"""Desktop Edge Indicator — thin colored bar at top of screen showing recording state.

Red bar = recording. Hidden when idle. Impossible to miss, impossible to forget.
Uses Win32 API directly via ctypes — no GUI framework needed.
"""

import ctypes
import ctypes.wintypes
import threading

# Win32 constants
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
LWA_ALPHA = 0x00000002
LWA_COLORKEY = 0x00000001
SW_SHOW = 5
SW_HIDE = 0
GWL_EXSTYLE = -20
WM_DESTROY = 0x0002
WM_PAINT = 0x000F
WM_TIMER = 0x0113

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# Bar dimensions
BAR_HEIGHT = 3
BAR_ALPHA = 200  # 0-255 transparency

# Colors (BGR format for Win32)
COLOR_RECORDING = 0x000000FF    # red
COLOR_DETECTING = 0x00FFAA00    # cyan
COLOR_IDLE = 0x00000000         # hidden


class DesktopIndicator:
    """Thin colored bar at top of screen. Shows recording state."""

    def __init__(self):
        self._hwnd = None
        self._visible = False
        self._color = COLOR_IDLE
        self._thread = None
        self._stop = threading.Event()
        self._class_registered = False

    def start(self):
        """Start the indicator on a background thread."""
        self._thread = threading.Thread(target=self._run, name="edge-indicator", daemon=True)
        self._thread.start()

    def stop(self):
        """Hide and destroy the indicator."""
        self._stop.set()
        if self._hwnd:
            try:
                user32.PostMessageW(self._hwnd, WM_DESTROY, 0, 0)
            except Exception:
                pass

    def show_recording(self):
        """Show red bar — recording active."""
        self._color = COLOR_RECORDING
        self._update()

    def show_detecting(self):
        """Show cyan bar — detecting/listening."""
        self._color = COLOR_DETECTING
        self._update()

    def hide(self):
        """Hide the bar — idle."""
        self._color = COLOR_IDLE
        self._update()

    def _update(self):
        if not self._hwnd:
            return
        try:
            if self._color == COLOR_IDLE:
                user32.ShowWindow(self._hwnd, SW_HIDE)
                self._visible = False
            else:
                # Repaint with new color
                user32.InvalidateRect(self._hwnd, None, True)
                if not self._visible:
                    user32.ShowWindow(self._hwnd, SW_SHOW)
                    self._visible = True
        except Exception:
            pass

    def _run(self):
        """Create and run the indicator window."""
        WNDPROC = ctypes.WINFUNCTYPE(
            ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
            ctypes.c_void_p, ctypes.c_void_p
        )

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_PAINT:
                # Paint the bar
                ps = ctypes.create_string_buffer(64)
                hdc = user32.BeginPaint(hwnd, ps)
                screen_w = user32.GetSystemMetrics(0)
                brush = gdi32.CreateSolidBrush(self._color)
                rect = ctypes.wintypes.RECT(0, 0, screen_w, BAR_HEIGHT)
                user32.FillRect(hdc, ctypes.byref(rect), brush)
                gdi32.DeleteObject(brush)
                user32.EndPaint(hwnd, ps)
                return 0
            elif msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROC(wnd_proc)

        # Register window class
        class_name = "AXISIndicator"
        wc = ctypes.wintypes.WNDCLASSW()
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = kernel32.GetModuleHandleW(None)
        wc.lpszClassName = class_name
        wc.hbrBackground = gdi32.CreateSolidBrush(0)

        if not self._class_registered:
            user32.RegisterClassW(ctypes.byref(wc))
            self._class_registered = True

        screen_w = user32.GetSystemMetrics(0)

        ex_style = WS_EX_TOPMOST | WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT
        style = WS_POPUP

        self._hwnd = user32.CreateWindowExW(
            ex_style, class_name, "AXIS Indicator",
            style,
            0, 0, screen_w, BAR_HEIGHT,
            None, None, wc.hInstance, None
        )

        # Set transparency
        user32.SetLayeredWindowAttributes(self._hwnd, 0, BAR_ALPHA, LWA_ALPHA)

        # Start hidden
        user32.ShowWindow(self._hwnd, SW_HIDE)

        # Message loop
        msg = ctypes.wintypes.MSG()
        while not self._stop.is_set():
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                if msg.message == WM_DESTROY:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                self._stop.wait(timeout=0.05)

        if self._hwnd:
            try:
                user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
            self._hwnd = None
