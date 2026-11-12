import ctypes
from ctypes import wintypes
import os
import subprocess

from .steam_utils import SteamUtils


class _ProcessEntry32W(ctypes.Structure):
    _fields_ = [
        ('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD),
        ('th32ProcessID', wintypes.DWORD), ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', wintypes.DWORD), ('cntThreads', wintypes.DWORD),
        ('th32ParentProcessID', wintypes.DWORD), ('pcPriClassBase', wintypes.LONG),
        ('dwFlags', wintypes.DWORD), ('szExeFile', wintypes.WCHAR * 260),
    ]


class LaunchManager:
    @staticmethod
    def is_cs2_running():
        return LaunchManager._is_process_running('cs2.exe')

    @staticmethod
    def _is_process_running(executable):
        # This is also called by the UI status timer. Spawning tasklist here
        # blocks all queued GSI/paint events for hundreds of milliseconds.
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        for function in (kernel32.Process32FirstW, kernel32.Process32NextW):
            function.argtypes = [wintypes.HANDLE, ctypes.POINTER(_ProcessEntry32W)]
            function.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == ctypes.c_void_p(-1).value or snapshot is None:
            return False
        try:
            entry = _ProcessEntry32W()
            entry.dwSize = ctypes.sizeof(entry)
            valid = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while valid:
                if entry.szExeFile.casefold() == executable.casefold():
                    return True
                valid = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            return False
        finally:
            kernel32.CloseHandle(snapshot)

    @staticmethod
    def launch_cs2(cs2_path, use_vulkan=False):
        if not cs2_path or not os.path.isdir(cs2_path):
            return False, "CS2 路径无效，请先正确设置游戏路径。"

        steam_root = SteamUtils.auto_detect_steam_path()
        if not steam_root:
            return False, "未检测到 Steam 安装目录，无法通过 Steam 启动 CS2。"

        steam_exe = os.path.join(steam_root, "Steam.exe")
        if not os.path.isfile(steam_exe):
            return False, f"找不到 Steam 可执行文件：{steam_exe}"

        command = [steam_exe, "-applaunch", "730"]
        if use_vulkan:
            command.append("-vulkan")

        try:
            subprocess.Popen(command, cwd=steam_root)
            if use_vulkan:
                return True, "已通过 Steam 启动 CS2，并附加 -vulkan 参数。"
            return True, "已通过 Steam 启动 CS2。"
        except Exception as e:
            return False, f"启动 CS2 失败：{e}"

    @staticmethod
    def close_cs2():
        try:
            result = subprocess.run(
                ["taskkill", "/F", "/IM", "cs2.exe"],
                capture_output=True,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            if result.returncode == 0:
                return True, "已请求关闭 CS2。"

            output = (result.stdout or "") + (result.stderr or "")
            if "not found" in output.lower() or "没有运行的实例" in output or "找不到进程" in output:
                return False, "当前未检测到正在运行的 CS2。"
            return False, output.strip() or "关闭 CS2 失败。"
        except Exception as e:
            return False, f"关闭 CS2 失败：{e}"
