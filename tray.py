# -*- coding: utf-8 -*-
"""跨平台系统托盘抽象。

Windows : 纯 ctypes 调用 Shell_NotifyIcon，零依赖
macOS   : 无标准库方案，降级为"后台进程 + 信号处理"（SIGTERM 退出）
Linux   : 同上；若检测到 pystray 则可用（但不强制依赖）

对外统一接口：
    Tray(tooltip, icon_path, menu, handlers, on_double_click=None)
    tray.run()        # 阻塞运行消息循环
    tray.quit()       # 请求退出
    tray.set_tip(text)  # 更新提示
"""
import os
import signal
import sys


def hide_console():
    if sys.platform == "win32":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
        except Exception:
            pass


def acquire_single_instance(name="TrafficProbe"):
    """跨平台单实例检测。Windows 用命名互斥量，其他用锁文件。"""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateMutexW.restype = wintypes.HANDLE
            k32.CreateMutexW(None, True, "Global\\" + name)
            return k32.GetLastError() != 183   # ERROR_ALREADY_EXISTS
        except Exception:
            return True
    # POSIX：用 flock，进程退出自动释放
    lock = os.path.join(_tmp_dir(), name + ".lock")
    try:
        import fcntl
        f = open(lock, "w")
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f.write(str(os.getpid()))
        f.flush()
        globals()["_lock_handle"] = f       # 保持引用，防止被回收
        return True
    except Exception:
        return False


def _tmp_dir():
    d = "/tmp" if sys.platform != "win32" else os.environ.get("TEMP", ".")
    return d


def msgbox(text, title="流量探针"):
    if sys.platform == "win32":
        try:
            import ctypes
            MB_OK, MB_ICONINFO, MB_TOPMOST = 0, 0x40, 0x40000
            ctypes.windll.user32.MessageBoxW(
                None, text, title, MB_OK | MB_ICONINFO | MB_TOPMOST)
            return
        except Exception:
            pass
    print("[%s] %s" % (title, text))


class Tray:
    """统一托盘接口。非 Windows 平台自动降级为后台常驻。"""

    def __init__(self, tooltip, icon_path, menu, handlers, on_double_click=None):
        self.tooltip = tooltip
        self.icon_path = icon_path
        self.menu = menu
        self.handlers = handlers
        self.on_double = on_double_click
        self._impl = None
        self._quit_flag = False

    def run(self):
        if sys.platform == "win32":
            from tray_win import Tray as WinTray
            self._impl = WinTray(self.tooltip, self.icon_path, self.menu,
                                 self.handlers, self.on_double)
            return self._impl.run()
        return self._run_headless()

    def _run_headless(self):
        """macOS/Linux：没有托盘，用信号保持常驻。"""
        def on_term(signum, frame):
            self._quit_flag = True
            for cid, fn in self.handlers.items():
                try:
                    fn()
                except Exception:
                    pass
            raise SystemExit(0)

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, on_term)
            except Exception:
                pass
        print("已在后台运行（无系统托盘）。收到的数据写入用户目录。")
        print("退出：Ctrl+C 或 kill %d" % os.getpid())
        while not self._quit_flag:
            signal.pause() if hasattr(signal, "pause") else None
        return 0

    def set_tip(self, text):
        self.tooltip = text
        if self._impl:
            self._impl.set_tip(text)

    def quit(self):
        self._quit_flag = True
        if self._impl:
            self._impl.quit()
