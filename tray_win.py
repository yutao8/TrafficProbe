# -*- coding: utf-8 -*-
"""Windows 系统托盘（纯 ctypes，零第三方依赖）。

提供 Tray 类：创建隐藏窗口 → 注册托盘图标 → 右键出菜单 → 消息循环。
菜单项由调用方传入 [(id, 文案), None(分隔线), ...]，点击后回调对应 callable。
"""
import ctypes
import os
import sys
from ctypes import wintypes

if sys.platform != "win32":
    raise ImportError("tray_win 只能在 Windows 上使用")

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_APP = 0x8000
TRAY_MSG = WM_APP + 1

NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x01, 0x02, 0x04
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x0010, 0x0040
MF_STRING, MF_SEPARATOR = 0x0000, 0x0800
TPM_RETURNCMD, TPM_RIGHTBUTTON = 0x0100, 0x0002
MB_ICONINFORMATION, MB_TOPMOST, MB_SETFOREGROUND = 0x40, 0x40000, 0x10000
SW_HIDE = 0


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


# 显式声明原型：64 位下 LPARAM 是 64 位有符号，不声明会被当成 C int 溢出
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                  wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.CreateWindowExW.restype = wintypes.HWND
user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.LoadImageW.restype = wintypes.HANDLE
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                               ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                  ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                  wintypes.LPVOID]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                wintypes.WPARAM, wintypes.LPARAM]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                               wintypes.UINT, wintypes.UINT]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                      ctypes.POINTER(NOTIFYICONDATAW)]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR,
                               wintypes.LPCWSTR, wintypes.UINT]


def hide_console():
    try:
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, SW_HIDE)
    except Exception:
        pass


def msgbox(text, title="流量探针"):
    try:
        user32.MessageBoxW(None, text, title,
                           MB_ICONINFORMATION | MB_TOPMOST | MB_SETFOREGROUND)
    except Exception:
        print(text)


def acquire_single_instance(name="Global\\TrafficProbeTray"):
    """已有实例在跑就返回 False。"""
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    h = kernel32.CreateMutexW(None, True, name)
    if kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
        return False
    return True


class Tray:
    """托盘图标 + 右键菜单。menu 传 [(id, 文案) | None]。"""

    def __init__(self, tooltip, icon_path, menu, handlers, on_double_click=None):
        self.tooltip = tooltip[:120]
        self.icon_path = icon_path
        self.menu = menu
        self.handlers = handlers
        self.on_double = on_double_click
        self.hwnd = None
        self._hmenu = None
        self._hicon = None
        self._nid = None
        self._running = True
        self._wndproc = WNDPROC(self._on_message)   # 必须保留引用

    # ---- 内部 ----
    def _create_window(self):
        hinst = kernel32.GetModuleHandleW(None)
        cls = "TrafficProbeTrayWnd"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._wndproc
        wc.hInstance = hinst
        wc.lpszClassName = cls
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0, cls, "TrafficProbe", 0, 0, 0, 0, 0, None, None, hinst, None)

    def _load_icon(self):
        if self.icon_path and os.path.exists(self.icon_path):
            h = user32.LoadImageW(None, self.icon_path, IMAGE_ICON, 0, 0,
                                  LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if h:
                self._hicon = h
                return
        self._hicon = user32.LoadIconW(None, ctypes.cast(32512, wintypes.LPCWSTR))

    def _add_icon(self):
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = TRAY_MSG
        nid.hIcon = self._hicon
        nid.szTip = self.tooltip
        self._nid = nid
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

    def _update_tip(self, text):
        if self._nid:
            self._nid.szTip = text[:120]
            self._nid.uFlags = NIF_TIP
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self._nid))

    def _build_menu(self):
        hm = user32.CreatePopupMenu()
        for item in self.menu:
            if item is None:
                user32.AppendMenuW(hm, MF_SEPARATOR, 0, None)
            else:
                cid, label = item
                user32.AppendMenuW(hm, MF_STRING, cid, label)
        self._hmenu = hm

    def _show_menu(self):
        self._build_menu()
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cid = user32.TrackPopupMenu(self._hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(self._hmenu)
        if cid:
            fn = self.handlers.get(cid)
            if fn:
                try:
                    fn()
                except Exception as e:
                    msgbox("操作失败：%s" % e)

    def _on_message(self, hwnd, msg, wparam, lparam):
        if msg == TRAY_MSG:
            low = lparam & 0xFFFF
            if low == WM_RBUTTONUP:
                self._show_menu()
            elif low in (WM_LBUTTONUP, WM_LBUTTONDBLCLK) and self.on_double:
                try:
                    self.on_double()
                except Exception as e:
                    msgbox("操作失败：%s" % e)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 对外 ----
    def run(self):
        self._create_window()
        self._load_icon()
        self._add_icon()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
        return 0

    def set_tip(self, text):
        self._update_tip(text)

    def quit(self):
        """线程安全地请求退出消息循环。"""
        try:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass
