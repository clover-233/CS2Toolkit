"""Locate CS2 by executable name and position overlays in native screen pixels."""

import ctypes
from ctypes import wintypes
import os


class GameWindowTracker:
    def __init__(self, process_name="cs2.exe"):
        self.process_name = process_name.lower()
        self.hwnd = None
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        declarations = [
            (self.user32.GetForegroundWindow, [], wintypes.HWND),
            (self.user32.IsWindow, [wintypes.HWND], wintypes.BOOL),
            (self.user32.IsWindowVisible, [wintypes.HWND], wintypes.BOOL),
            (self.user32.IsIconic, [wintypes.HWND], wintypes.BOOL),
            (self.user32.GetWindowThreadProcessId,
             [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            (self.user32.EnumWindows, [self.enum_proc, wintypes.LPARAM], wintypes.BOOL),
            (self.user32.GetClientRect,
             [wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            (self.user32.ClientToScreen,
             [wintypes.HWND, ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            (self.user32.SetWindowPos,
             [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
              ctypes.c_int, ctypes.c_int, wintypes.UINT], wintypes.BOOL),
            (self.kernel32.OpenProcess,
             [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            (self.kernel32.QueryFullProcessImageNameW,
             [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
              ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
            (self.kernel32.CloseHandle, [wintypes.HANDLE], wintypes.BOOL),
        ]
        for function, args, result in declarations:
            function.argtypes = args
            function.restype = result

    def is_game_window(self, hwnd):
        if not hwnd or not self.user32.IsWindow(hwnd):
            return False
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        # Only query the process image name; do not read game memory.
        handle = self.kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return False
        try:
            name = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(name))
            if not self.kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
                return False
            return os.path.basename(name.value).lower() == self.process_name
        finally:
            self.kernel32.CloseHandle(handle)

    def foreground_game(self):
        hwnd = self.user32.GetForegroundWindow()
        if self.is_game_window(hwnd) and not self.user32.IsIconic(hwnd):
            self.hwnd = hwnd
            return hwnd
        return None

    def find(self):
        foreground = self.foreground_game()
        if foreground:
            return foreground
        if self.is_game_window(self.hwnd) and self.user32.IsWindowVisible(self.hwnd):
            return self.hwnd
        found = []

        @self.enum_proc
        def visit(hwnd, _):
            if self.user32.IsWindowVisible(hwnd) and self.is_game_window(hwnd):
                found.append(hwnd)
                return False
            return True

        self.user32.EnumWindows(visit, 0)
        self.hwnd = found[0] if found else None
        return self.hwnd

    def client_geometry(self, hwnd):
        if not hwnd or self.user32.IsIconic(hwnd):
            return None
        rect = wintypes.RECT()
        origin = wintypes.POINT(0, 0)
        if not self.user32.GetClientRect(hwnd, ctypes.byref(rect)):
            return None
        if not self.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            return None
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width <= 0 or height <= 0:
            return None
        return origin.x, origin.y, width, height

    def place_overlay(self, overlay_hwnd, geometry=None):
        # Win32 coordinates are physical pixels. Passing these to Qt.setGeometry
        # applies display scaling a second time on high-DPI monitors.
        flags = 0x0010  # SWP_NOACTIVATE
        if geometry is None:
            flags |= 0x0001 | 0x0002  # SWP_NOSIZE | SWP_NOMOVE
            geometry = (0, 0, 0, 0)
        return bool(self.user32.SetWindowPos(
            int(overlay_hwnd), wintypes.HWND(-1), *geometry, flags))
