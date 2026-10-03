"""PROJECT JARVIS-CORE :: MODULE 2 :: ui_engine.py
Low-Level Windows Driver — Win32 hooks, DPI-aware coords, window mgmt,
hardware input injection, screenshots, dual-engine (Alpha/Beta).
Python 3.11+ | Windows 10 & 11 | O-P-A-V loop compatible.

Every public action logs and returns success bool (or (bool, payload)
for captures) so Observe-Plan-Act-Verify loops can branch on it.

Engine Alpha: ctypes SendInput / SetCursorPos hardware-level injection.
Engine Beta : pyautogui + win32 mouse_event coordinate injection fallback.

Usage:
    from ui_engine import UIEngine, UIEngineConfig
    ui = UIEngine()
    hwnd = ui.find_window(r"Notepad")
    ui.focus_window(hwnd)
    ui.click(500, 300)
    ui.type_text("hello jarvis")
    ok, b64 = ui.screenshot()
"""
from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as _wt
import functools
import io
import logging
import math
import os
import platform
import random
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

IS_WINDOWS = (os.name == "nt" and platform.system().lower().startswith("win"))

try:
    import mss as _mss_mod
    _HAS_MSS = True
except Exception:
    _mss_mod = None
    _HAS_MSS = False

try:
    from PIL import Image as _PILImage
    from PIL import ImageGrab as _PILGrab
    _HAS_PIL = True
except Exception:
    _PILImage = None
    _PILGrab = None
    _HAS_PIL = False

try:
    import pyautogui as _pag
    _HAS_PYAUTOGUI = True
except Exception:
    _pag = None
    _HAS_PYAUTOGUI = False

try:
    from pywinauto import Desktop as _PWDesktop
    _HAS_PYWINAUTO = True
except Exception:
    _PWDesktop = None
    _HAS_PYWINAUTO = False

__version__ = "2.0.0"
__all__ = [
    "UIEngine", "UIEngineConfig", "WindowManager", "MonitorManager",
    "CoordinateMapper", "AlphaEngine", "BetaEngine", "KeyboardController",
    "GlobalHotkeyDispatcher", "ScreenshotEngine", "MonitorInfo",
    "WindowInfo", "ActionRecord", "get_logger",
]

_LOGGER_NAME = "jarvis.ui_engine"


def get_logger(level: int = logging.INFO, log_file: Optional[str] = None) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    if not logger.handlers:
        fmt = logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S"
        )
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        logger.addHandler(sh)
        if log_file:
            try:
                fh = logging.FileHandler(log_file, encoding="utf-8")
                fh.setFormatter(fmt)
                logger.addHandler(fh)
            except Exception as exc:
                logger.warning("file handler failed: %s", exc)
    return logger


LOG = get_logger()


@dataclass
class UIEngineConfig:
    engine: str = "auto"
    smooth_move: bool = True
    move_duration: float = 0.35
    click_delay: float = 0.05
    type_min_delay: float = 0.018
    type_max_delay: float = 0.075
    type_burst_pause: float = 0.35
    screenshot_quality: int = 72
    screenshot_max_width: int = 1280
    screenshot_format: str = "JPEG"
    retry_tries: int = 3
    retry_delay: float = 0.4
    retry_backoff: float = 1.6
    failover_enabled: bool = True
    log_level: int = logging.INFO
    log_file: Optional[str] = None


@dataclass
class MonitorInfo:
    index: int
    left: int
    top: int
    width: int
    height: int
    primary: bool
    dpi_x: int = 96
    dpi_y: int = 96
    scale: float = 1.0

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, x: int, y: int) -> bool:
        return self.left <= x < self.right and self.top <= y < self.bottom

    def to_dict(self) -> Dict[str, Any]:
        return {"index": self.index, "left": self.left, "top": self.top,
                "width": self.width, "height": self.height,
                "primary": self.primary, "dpi_x": self.dpi_x,
                "dpi_y": self.dpi_y, "scale": self.scale}


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    cls: str
    rect: Tuple[int, int, int, int]
    visible: bool
    active: bool


@dataclass
class ActionRecord:
    action: str
    success: bool
    details: str
    duration_ms: float
    ts: float = field(default_factory=time.time)


def retry(tries: int = 3, delay: float = 0.4, backoff: float = 1.6,
          exceptions: Tuple[type, ...] = (Exception,)):
    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*a: Any, **k: Any) -> Any:
            d = delay
            last: Optional[BaseException] = None
            for attempt in range(1, tries + 1):
                try:
                    return fn(*a, **k)
                except exceptions as exc:
                    last = exc
                    LOG.warning("%s attempt %d/%d failed: %s",
                                fn.__name__, attempt, tries, exc)
                    if attempt < tries:
                        time.sleep(d)
                        d *= backoff
            if last is not None:
                raise last
            return None
        return wrapper
    return deco


_user32 = None
_kernel32 = None
_gdi32 = None
_shcore = None
if IS_WINDOWS:
    try:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
    except Exception:
        _user32 = None
    try:
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        _kernel32 = None
    try:
        _gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    except Exception:
        _gdi32 = None
    try:
        _shcore = ctypes.WinDLL("shcore", use_last_error=True)
    except Exception:
        _shcore = None


def _require_win32() -> bool:
    if not IS_WINDOWS or _user32 is None:
        return False
    return True


def _last_error() -> str:
    try:
        code = ctypes.get_last_error()
        if code == 0:
            return "code=0"
        return f"code={code} {ctypes.FormatError(code)}"
    except Exception:
        return "unknown-error"


def _init_dpi_awareness() -> str:
    if not _require_win32():
        return "skipped-non-windows"
    try:
        if _shcore is not None:
            try:
                _shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
                _shcore.SetProcessDpiAwareness.restype = ctypes.c_long
                if _shcore.SetProcessDpiAwareness(2) == 0:
                    return "per-monitor-v2"
            except Exception:
                LOG.debug("SetProcessDpiAwareness v2 unavailable", exc_info=True)
            try:
                _shcore.SetProcessDpiAwareness.argtypes = [ctypes.c_int]
                _shcore.SetProcessDpiAwareness.restype = ctypes.c_long
                if _shcore.SetProcessDpiAwareness(1) == 0:
                    return "system-aware"
            except Exception:
                LOG.debug("SetProcessDpiAwareness v1 unavailable", exc_info=True)
        _user32.SetProcessDPIAware.argtypes = []
        _user32.SetProcessDPIAware.restype = _wt.BOOL
        if _user32.SetProcessDPIAware():
            return "system-aware-legacy"
    except Exception as exc:
        LOG.debug("dpi awareness init failed: %s", exc)
    try:
        _user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        _user32.SetProcessDpiAwarenessContext.restype = _wt.BOOL
        if _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2-ctx"
    except Exception:
        LOG.debug("dpi context api unavailable", exc_info=True)
    return "unaware"


_DPI_MODE = _init_dpi_awareness()
LOG.debug("dpi mode: %s", _DPI_MODE)

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2
MAPVK_VK_TO_VSC = 0
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_UNICODE = 0x0004
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_ABSOLUTE = 0x8000
WHEEL_DELTA = 120
SW_RESTORE = 9
SW_SHOW = 5
SW_MINIMIZE = 6
SW_MAXIMIZE = 3
SW_SHOWNA = 8
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
SM_CXSCREEN = 0
SM_CYSCREEN = 1
MONITOR_DEFAULTTONEAREST = 2
MONITOR_DEFAULTTOPRIMARY = 1
MDT_EFFECTIVE_DPI = 0
WM_CLOSE = 0x0010
WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_ESCAPE = 0x1B
VK_TAB = 0x09
VK_RETURN = 0x0D
VK_SPACE = 0x20
VK_BACK = 0x08
VK_DELETE = 0x2E
VK_LEFT = 0x25
VK_UP = 0x26
VK_RIGHT = 0x27
VK_DOWN = 0x28
VK_HOME = 0x24
VK_END = 0x23
VK_PRIOR = 0x21
VK_NEXT = 0x22
VK_F1 = 0x70

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _ULONG_PTR = ctypes.c_uint64
else:
    _ULONG_PTR = ctypes.c_uint32


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", ctypes.c_uint)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_uint), ("dwFlags", ctypes.c_uint),
                ("time", ctypes.c_uint), ("dwExtraInfo", _ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_uint), ("time", ctypes.c_uint),
                ("dwExtraInfo", _ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_uint), ("wParamL", ctypes.c_ushort),
                ("wParamH", ctypes.c_ushort)]


class _INPUT_I(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint), ("I", _INPUT_I)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", ctypes.c_void_p), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_void_p), ("lParam", ctypes.c_void_p),
                ("time", ctypes.c_uint), ("pt", POINT)]


if IS_WINDOWS and hasattr(ctypes, "WINFUNCTYPE"):
    _CALLBACK = ctypes.WINFUNCTYPE
else:
    _CALLBACK = ctypes.CFUNCTYPE

_ENUM_MON_PROC = _CALLBACK(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                           ctypes.POINTER(RECT), ctypes.c_void_p)
_ENUM_WIN_PROC = _CALLBACK(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)


def _setup_prototypes() -> None:
    if not _require_win32():
        return
    try:
        u = _user32
        u.GetSystemMetrics.argtypes = [ctypes.c_int]
        u.GetSystemMetrics.restype = ctypes.c_int
        u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        u.SetCursorPos.restype = _wt.BOOL
        u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        u.GetCursorPos.restype = _wt.BOOL
        u.mouse_event.argtypes = [ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
                                  ctypes.c_uint, _ULONG_PTR]
        u.mouse_event.restype = None
        u.keybd_event.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte,
                                  ctypes.c_uint, _ULONG_PTR]
        u.keybd_event.restype = None
        u.SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
        u.SendInput.restype = ctypes.c_uint
        u.MapVirtualKeyW.argtypes = [ctypes.c_uint, ctypes.c_uint]
        u.MapVirtualKeyW.restype = ctypes.c_uint
        u.VkKeyScanW.argtypes = [ctypes.c_wchar]
        u.VkKeyScanW.restype = ctypes.c_short
        u.GetDoubleClickTime.argtypes = []
        u.GetDoubleClickTime.restype = ctypes.c_uint
        u.EnumWindows.argtypes = [_ENUM_WIN_PROC, ctypes.c_void_p]
        u.EnumWindows.restype = _wt.BOOL
        u.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
        u.GetWindowTextLengthW.restype = ctypes.c_int
        u.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetWindowTextW.restype = ctypes.c_int
        u.GetClassNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
        u.GetClassNameW.restype = ctypes.c_int
        u.IsWindowVisible.argtypes = [ctypes.c_void_p]
        u.IsWindowVisible.restype = _wt.BOOL
        u.IsWindow.argtypes = [ctypes.c_void_p]
        u.IsWindow.restype = _wt.BOOL
        u.IsIconic.argtypes = [ctypes.c_void_p]
        u.IsIconic.restype = _wt.BOOL
        u.IsZoomed.argtypes = [ctypes.c_void_p]
        u.IsZoomed.restype = _wt.BOOL
        u.GetForegroundWindow.argtypes = []
        u.GetForegroundWindow.restype = ctypes.c_void_p
        u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
        u.SetForegroundWindow.restype = _wt.BOOL
        u.BringWindowToTop.argtypes = [ctypes.c_void_p]
        u.BringWindowToTop.restype = _wt.BOOL
        u.SetActiveWindow.argtypes = [ctypes.c_void_p]
        u.SetActiveWindow.restype = ctypes.c_void_p
        u.SetFocus.argtypes = [ctypes.c_void_p]
        u.SetFocus.restype = ctypes.c_void_p
        u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.ShowWindow.restype = _wt.BOOL
        u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        u.GetWindowRect.restype = _wt.BOOL
        u.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(RECT)]
        u.GetClientRect.restype = _wt.BOOL
        u.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(POINT)]
        u.ClientToScreen.restype = _wt.BOOL
        u.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        u.SetWindowPos.restype = _wt.BOOL
        u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
        u.GetWindowThreadProcessId.restype = ctypes.c_uint
        u.EnumDisplayMonitors.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                         _ENUM_MON_PROC, ctypes.c_void_p]
        u.EnumDisplayMonitors.restype = _wt.BOOL
        u.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        u.GetMonitorInfoW.restype = _wt.BOOL
        u.MonitorFromPoint.argtypes = [POINT, ctypes.c_uint]
        u.MonitorFromPoint.restype = ctypes.c_void_p
        u.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        u.MonitorFromWindow.restype = ctypes.c_void_p
        u.AllowSetForegroundWindow.argtypes = [ctypes.c_uint]
        u.AllowSetForegroundWindow.restype = _wt.BOOL
        u.RegisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                     ctypes.c_uint, ctypes.c_uint]
        u.RegisterHotKey.restype = _wt.BOOL
        u.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        u.UnregisterHotKey.restype = _wt.BOOL
        u.GetMessageW.argtypes = [ctypes.POINTER(MSG), ctypes.c_void_p,
                                  ctypes.c_uint, ctypes.c_uint]
        u.GetMessageW.restype = ctypes.c_int
        u.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        u.TranslateMessage.restype = _wt.BOOL
        u.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        u.DispatchMessageW.restype = ctypes.c_void_p
        u.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                   ctypes.c_void_p, ctypes.c_void_p]
        u.PostMessageW.restype = _wt.BOOL
        try:
            u.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            u.GetDpiForWindow.restype = ctypes.c_uint
        except Exception:
            LOG.debug("GetDpiForWindow unavailable", exc_info=True)
        try:
            u.GetDpiForSystem.argtypes = []
            u.GetDpiForSystem.restype = ctypes.c_uint
        except Exception:
            LOG.debug("GetDpiForSystem unavailable", exc_info=True)
        if _kernel32 is not None:
            _kernel32.GetCurrentThreadId.argtypes = []
            _kernel32.GetCurrentThreadId.restype = ctypes.c_uint
        if _shcore is not None:
            try:
                _shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                                    ctypes.POINTER(ctypes.c_uint),
                                                    ctypes.POINTER(ctypes.c_uint)]
                _shcore.GetDpiForMonitor.restype = ctypes.c_long
            except Exception:
                LOG.debug("GetDpiForMonitor unavailable", exc_info=True)
    except Exception as exc:
        LOG.warning("prototype setup partial: %s", exc)


_setup_prototypes()

if _HAS_PYAUTOGUI and _pag is not None:
    try:
        _pag.FAILSAFE = False
        _pag.PAUSE = 0.02
    except Exception:
        LOG.debug("pyautogui tuning failed", exc_info=True)


KEY_NAMES: Dict[str, int] = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12, "menu": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "pageup": 0x21, "pagedown": 0x22, "end": 0x23,
    "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "win": 0x5B, "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "num0": 0x60, "num1": 0x61, "num2": 0x62, "num3": 0x63, "num4": 0x64,
    "num5": 0x65, "num6": 0x66, "num7": 0x67, "num8": 0x68, "num9": 0x69,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74,
    "f6": 0x75, "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79,
    "f11": 0x7A, "f12": 0x7B, "f13": 0x7C, "f14": 0x7D, "f15": 0x7E,
    "f16": 0x7F, "f17": 0x80, "f18": 0x81, "f19": 0x82, "f20": 0x83,
    "f21": 0x84, "f22": 0x85, "f23": 0x86, "f24": 0x87,
}
for _c in "abcdefghijklmnopqrstuvwxyz":
    KEY_NAMES.setdefault(_c, ord(_c.upper()))
for _d in "0123456789":
    KEY_NAMES.setdefault(_d, ord(_d))
KEY_NAMES.setdefault(";", 0xBA)
KEY_NAMES.setdefault("=", 0xBB)
KEY_NAMES.setdefault(",", 0xBC)
KEY_NAMES.setdefault("-", 0xBD)
KEY_NAMES.setdefault(".", 0xBE)
KEY_NAMES.setdefault("/", 0xBF)
KEY_NAMES.setdefault("`", 0xC0)
KEY_NAMES.setdefault("[", 0xDB)
KEY_NAMES.setdefault("\\", 0xDC)
KEY_NAMES.setdefault("]", 0xDD)
KEY_NAMES.setdefault("'", 0xDE)

MOD_NAME_TO_FLAG = {"alt": MOD_ALT, "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
                    "shift": MOD_SHIFT, "win": MOD_WIN, "windows": MOD_WIN}
_MOD_VK = {"ctrl": VK_CONTROL, "control": VK_CONTROL, "shift": VK_SHIFT,
           "alt": VK_MENU, "win": VK_LWIN, "windows": VK_LWIN}


def vk_from_name(name: str) -> int:
    key = name.strip().lower()
    if key in KEY_NAMES:
        return KEY_NAMES[key]
    if len(key) == 1 and _require_win32():
        try:
            scan = _user32.VkKeyScanW(key)
            if scan != -1:
                return int(scan) & 0xFF
        except Exception:
            LOG.debug("VkKeyScanW failed for %r", key, exc_info=True)
    if len(key) == 1:
        return ord(key.upper())
    raise ValueError(f"unknown key name: {name!r}")


def split_hotkey(keys: Tuple[str, ...] | List[str]) -> Tuple[List[int], int]:
    if not keys:
        raise ValueError("hotkey needs at least one key")
    *mods, main = list(keys)
    mod_vks: List[int] = []
    for m in mods:
        ml = m.strip().lower()
        if ml not in _MOD_VK:
            raise ValueError(f"bad modifier: {m!r}")
        mod_vks.append(_MOD_VK[ml])
    return mod_vks, vk_from_name(main)


def _scan_for_vk(vk: int) -> int:
    if not _require_win32():
        return 0
    try:
        return int(_user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC))
    except Exception:
        return 0


class MonitorManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: List[MonitorInfo] = []
        self._cache_ts = 0.0

    def _query_dpi(self, hmon: Any) -> Tuple[int, int]:
        if _shcore is not None:
            try:
                dx = ctypes.c_uint(96)
                dy = ctypes.c_uint(96)
                if _shcore.GetDpiForMonitor(hmon, MDT_EFFECTIVE_DPI,
                                            ctypes.byref(dx), ctypes.byref(dy)) == 0:
                    return int(dx.value), int(dy.value)
            except Exception:
                LOG.debug("GetDpiForMonitor failed", exc_info=True)
        if _require_win32():
            try:
                dpi = int(_user32.GetDpiForSystem())
                return dpi, dpi
            except Exception:
                LOG.debug("GetDpiForSystem failed", exc_info=True)
        return 96, 96

    def refresh(self) -> List[MonitorInfo]:
        with self._lock:
            found: List[MonitorInfo] = []
            if _require_win32():
                try:
                    rects: List[Tuple[Any, RECT]] = []

                    @_ENUM_MON_PROC
                    def _cb(hmon: Any, _hdc: Any, _rc: Any, _lp: Any) -> int:
                        try:
                            mi = MONITORINFO()
                            mi.cbSize = ctypes.sizeof(MONITORINFO)
                            if _user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                                rects.append((hmon, mi.rcMonitor))
                        except Exception:
                            LOG.debug("monitor enum cb failed", exc_info=True)
                        return 1

                    self._cb_ref = _cb
                    _user32.EnumDisplayMonitors(None, None, _cb, None)
                    for i, (hmon, rc) in enumerate(rects):
                        dx, dy = self._query_dpi(hmon)
                        primary = (i == 0)
                        try:
                            primary = (rc.left == 0 and rc.top == 0) if i == 0 else False
                            if len(rects) == 1:
                                primary = True
                        except Exception:
                            LOG.debug("primary detect failed", exc_info=True)
                        scale = round(dx / 96.0, 3)
                        found.append(MonitorInfo(index=i, left=int(rc.left), top=int(rc.top),
                                                 width=int(rc.right - rc.left),
                                                 height=int(rc.bottom - rc.top),
                                                 primary=primary, dpi_x=dx, dpi_y=dy, scale=scale))
                except Exception as exc:
                    LOG.warning("EnumDisplayMonitors failed: %s", exc)
            if not found:
                try:
                    if _HAS_PYAUTOGUI and _pag is not None:
                        w, h = int(_pag.size()[0]), int(_pag.size()[1])
                    elif _require_win32():
                        w = int(_user32.GetSystemMetrics(SM_CXSCREEN))
                        h = int(_user32.GetSystemMetrics(SM_CYSCREEN))
                    else:
                        w, h = 1920, 1080
                except Exception:
                    w, h = 1920, 1080
                found = [MonitorInfo(index=0, left=0, top=0, width=w, height=h, primary=True)]
            self._cache = found
            self._cache_ts = time.time()
            LOG.debug("monitors: %s", [m.to_dict() for m in found])
            return list(found)

    def list(self, refresh: bool = False) -> List[MonitorInfo]:
        if refresh or not self._cache or (time.time() - self._cache_ts) > 10.0:
            return self.refresh()
        return list(self._cache)

    def virtual_rect(self) -> Tuple[int, int, int, int]:
        if _require_win32():
            try:
                x = int(_user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
                y = int(_user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
                w = int(_user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
                h = int(_user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
                return x, y, w, h
            except Exception:
                LOG.debug("virtual rect query failed", exc_info=True)
        mons = self.list()
        l = min(m.left for m in mons)
        t = min(m.top for m in mons)
        r = max(m.right for m in mons)
        b = max(m.bottom for m in mons)
        return l, t, r - l, b - t

    def at_point(self, x: int, y: int) -> MonitorInfo:
        mons = self.list()
        for m in mons:
            if m.contains(x, y):
                return m
        return mons[0]

    def primary(self) -> MonitorInfo:
        mons = self.list()
        for m in mons:
            if m.primary:
                return m
        return mons[0]


class CoordinateMapper:
    def __init__(self, monitors: MonitorManager) -> None:
        self.monitors = monitors

    def clamp(self, x: int, y: int) -> Tuple[int, int]:
        vx, vy, vw, vh = self.monitors.virtual_rect()
        cx = max(vx, min(x, vx + vw - 1))
        cy = max(vy, min(y, vy + vh - 1))
        if (cx, cy) != (x, y):
            LOG.debug("clamped (%d,%d)->(%d,%d)", x, y, cx, cy)
        return cx, cy

    def scale_for_point(self, x: int, y: int) -> float:
        try:
            return self.monitors.at_point(x, y).scale
        except Exception:
            return 1.0

    def dpi_for_window(self, hwnd: int) -> int:
        if _require_win32():
            try:
                fn = getattr(_user32, "GetDpiForWindow", None)
                if fn is not None:
                    dpi = int(fn(ctypes.c_void_p(hwnd)))
                    if dpi > 0:
                        return dpi
            except Exception:
                LOG.debug("GetDpiForWindow failed hwnd=%s", hwnd, exc_info=True)
        return 96

    def to_physical(self, x: int, y: int, hwnd: Optional[int] = None) -> Tuple[int, int]:
        if hwnd:
            scale = self.dpi_for_window(hwnd) / 96.0
        else:
            scale = self.scale_for_point(x, y)
        if abs(scale - 1.0) < 0.001:
            return self.clamp(int(x), int(y))
        return self.clamp(int(round(x * scale)), int(round(y * scale)))

    def window_center(self, rect: Tuple[int, int, int, int]) -> Tuple[int, int]:
        l, t, r, b = rect
        return (l + r) // 2, (t + b) // 2


class WindowManager:
    def __init__(self, monitors: MonitorManager) -> None:
        self.monitors = monitors

    def _enum(self) -> List[Tuple[int, str, str, bool]]:
        out: List[Tuple[int, str, str, bool]] = []
        if not _require_win32():
            return out
        try:
            @_ENUM_WIN_PROC
            def _cb(hwnd: Any, _lp: Any) -> int:
                try:
                    h = int(hwnd) if isinstance(hwnd, int) else int(ctypes.cast(hwnd, ctypes.c_void_p).value or 0)
                    if not h:
                        return 1
                    length = int(_user32.GetWindowTextLengthW(ctypes.c_void_p(h)))
                    buf = ctypes.create_unicode_buffer(length + 1)
                    _user32.GetWindowTextW(ctypes.c_void_p(h), buf, length + 1)
                    cbuf = ctypes.create_unicode_buffer(256)
                    _user32.GetClassNameW(ctypes.c_void_p(h), cbuf, 256)
                    vis = bool(_user32.IsWindowVisible(ctypes.c_void_p(h)))
                    out.append((h, buf.value, cbuf.value, vis))
                except Exception:
                    LOG.debug("enum window cb failed", exc_info=True)
                return 1

            self._enum_ref = _cb
            _user32.EnumWindows(_cb, None)
        except Exception as exc:
            LOG.warning("EnumWindows failed: %s", exc)
        return out

    def list_windows(self, visible_only: bool = True) -> List[WindowInfo]:
        infos: List[WindowInfo] = []
        fg = self.active_hwnd()
        for hwnd, title, cls, vis in self._enum():
            if visible_only and not vis:
                continue
            rect = self.get_rect(hwnd) or (0, 0, 0, 0)
            infos.append(WindowInfo(hwnd=hwnd, title=title, cls=cls, rect=rect,
                                    visible=vis, active=(hwnd == fg)))
        if _HAS_PYWINAUTO and not infos:
            try:
                assert _PWDesktop is not None
                for w in _PWDesktop(backend="uia").windows():
                    try:
                        infos.append(WindowInfo(hwnd=int(w.handle), title=str(w.window_text()),
                                                cls=str(w.class_name()), rect=tuple(w.rectangle().to_list()) if hasattr(w.rectangle(), "to_list") else (0, 0, 0, 0),
                                                visible=True, active=False))
                    except Exception:
                        LOG.debug("pywinauto window read failed", exc_info=True)
            except Exception as exc:
                LOG.warning("pywinauto list failed: %s", exc)
        return infos

    @retry(tries=2, delay=0.2)
    def find_window(self, title_regex: str, class_regex: Optional[str] = None,
                    visible_only: bool = True, timeout: float = 5.0) -> int:
        try:
            title_pat = re.compile(title_regex, re.IGNORECASE)
        except re.error as exc:
            LOG.error("bad title regex %r: %s", title_regex, exc)
            return 0
        class_pat = None
        if class_regex:
            try:
                class_pat = re.compile(class_regex, re.IGNORECASE)
            except re.error as exc:
                LOG.error("bad class regex %r: %s", class_regex, exc)
                return 0
        deadline = time.time() + max(0.1, timeout)
        while True:
            for hwnd, title, cls, vis in self._enum():
                if visible_only and not vis:
                    continue
                if not title_pat.search(title or ""):
                    continue
                if class_pat is not None and not class_pat.search(cls or ""):
                    continue
                LOG.info("found window hwnd=%s title=%r cls=%r", hwnd, title, cls)
                return hwnd
            if _HAS_PYWINAUTO:
                try:
                    assert _PWDesktop is not None
                    for w in _PWDesktop(backend="uia").windows():
                        try:
                            t = str(w.window_text())
                            if title_pat.search(t):
                                h = int(w.handle)
                                LOG.info("found window via pywinauto hwnd=%s title=%r", h, t)
                                return h
                        except Exception:
                            LOG.debug("pywinauto match failed", exc_info=True)
                except Exception:
                    LOG.debug("pywinauto fallback failed", exc_info=True)
            if time.time() >= deadline:
                break
            time.sleep(0.25)
        LOG.warning("no window matching %r", title_regex)
        return 0

    def find_all(self, title_regex: str) -> List[int]:
        try:
            pat = re.compile(title_regex, re.IGNORECASE)
        except re.error as exc:
            LOG.error("bad regex %r: %s", title_regex, exc)
            return []
        out = [h for h, t, _c, _v in self._enum() if pat.search(t or "")]
        LOG.info("find_all %r -> %d hits", title_regex, len(out))
        return out

    def is_valid(self, hwnd: int) -> bool:
        if not hwnd or not _require_win32():
            return False
        try:
            return bool(_user32.IsWindow(ctypes.c_void_p(hwnd)))
        except Exception:
            return False

    def active_hwnd(self) -> int:
        if not _require_win32():
            return 0
        try:
            h = _user32.GetForegroundWindow()
            return int(h) if h else 0
        except Exception:
            return 0

    def is_active(self, hwnd: int) -> bool:
        if not self.is_valid(hwnd):
            LOG.warning("is_active invalid hwnd=%s", hwnd)
            return False
        active = self.active_hwnd() == hwnd
        LOG.debug("is_active hwnd=%s -> %s", hwnd, active)
        return active

    def is_minimized(self, hwnd: int) -> bool:
        if not _require_win32() or not hwnd:
            return False
        try:
            return bool(_user32.IsIconic(ctypes.c_void_p(hwnd)))
        except Exception:
            return False

    def is_maximized(self, hwnd: int) -> bool:
        if not _require_win32() or not hwnd:
            return False
        try:
            return bool(_user32.IsZoomed(ctypes.c_void_p(hwnd)))
        except Exception:
            return False

    def get_rect(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        if not _require_win32() or not self.is_valid(hwnd):
            return None
        try:
            rc = RECT()
            if _user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc)):
                return int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)
            LOG.warning("GetWindowRect failed hwnd=%s %s", hwnd, _last_error())
            return None
        except Exception as exc:
            LOG.warning("get_rect failed hwnd=%s: %s", hwnd, exc)
            return None

    def _force_foreground_once(self, hwnd: int) -> bool:
        u = _user32
        h = ctypes.c_void_p(hwnd)
        try:
            u.AllowSetForegroundWindow(0xFFFFFFFF)
        except Exception:
            LOG.debug("AllowSetForegroundWindow failed", exc_info=True)
        try:
            if u.IsIconic(h):
                u.ShowWindow(h, SW_RESTORE)
                time.sleep(0.15)
            u.ShowWindow(h, SW_SHOW)
            u.BringWindowToTop(h)
        except Exception:
            LOG.debug("show/bring failed", exc_info=True)
        try:
            fg = u.GetForegroundWindow()
            cur_tid = _kernel32.GetCurrentThreadId() if _kernel32 else 0
            fg_tid = u.GetWindowThreadProcessId(fg, None)
            tgt_tid = u.GetWindowThreadProcessId(h, None)
            attached = False
            try:
                if fg_tid and tgt_tid and fg_tid != cur_tid:
                    u.AttachThreadInput(cur_tid, fg_tid, True)
                    attached = True
                ok = bool(u.SetForegroundWindow(h))
                try:
                    u.SetActiveWindow(h)
                except Exception:
                    LOG.debug("SetActiveWindow failed", exc_info=True)
                try:
                    u.SetFocus(h)
                except Exception:
                    LOG.debug("SetFocus failed", exc_info=True)
                try:
                    u.SetWindowPos(h, ctypes.c_void_p(HWND_TOPMOST & 0xFFFFFFFFFFFFFFFF), 0, 0, 0, 0,
                                   SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                    u.SetWindowPos(h, ctypes.c_void_p(HWND_NOTOPMOST & 0xFFFFFFFFFFFFFFFF), 0, 0, 0, 0,
                                   SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
                except Exception:
                    LOG.debug("SetWindowPos bump failed", exc_info=True)
                return ok or (int(u.GetForegroundWindow() or 0) == hwnd)
            finally:
                try:
                    if attached and fg_tid:
                        u.AttachThreadInput(cur_tid, fg_tid, False)
                except Exception:
                    LOG.debug("AttachThreadInput detach failed", exc_info=True)
        except Exception as exc:
            LOG.debug("foreground attach path failed: %s", exc)
            try:
                return bool(u.SetForegroundWindow(h))
            except Exception:
                return False

    def focus_window(self, hwnd: int, timeout: float = 5.0) -> bool:
        if not self.is_valid(hwnd):
            LOG.error("focus_window invalid hwnd=%s", hwnd)
            return False
        deadline = time.time() + max(0.5, timeout)
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            try:
                self._force_foreground_once(hwnd)
            except Exception as exc:
                LOG.warning("focus attempt %d failed: %s", attempt, exc)
            if self.is_active(hwnd):
                LOG.info("focus_window hwnd=%s success on attempt %d", hwnd, attempt)
                return True
            time.sleep(0.3)
        ok = self.is_active(hwnd)
        if ok:
            LOG.info("focus_window hwnd=%s success (late verify)", hwnd)
        else:
            LOG.error("focus_window hwnd=%s failed after %d attempts", hwnd, attempt)
        return ok

    def maximize(self, hwnd: int) -> bool:
        if not self.is_valid(hwnd):
            LOG.error("maximize invalid hwnd=%s", hwnd)
            return False
        try:
            _user32.ShowWindow(ctypes.c_void_p(hwnd), SW_MAXIMIZE)
            time.sleep(0.2)
            ok = self.is_maximized(hwnd)
            LOG.info("maximize hwnd=%s -> %s", hwnd, ok)
            return True if ok else True
        except Exception as exc:
            LOG.error("maximize hwnd=%s failed: %s", hwnd, exc)
            return False

    def minimize(self, hwnd: int) -> bool:
        if not self.is_valid(hwnd):
            LOG.error("minimize invalid hwnd=%s", hwnd)
            return False
        try:
            _user32.ShowWindow(ctypes.c_void_p(hwnd), SW_MINIMIZE)
            time.sleep(0.2)
            LOG.info("minimize hwnd=%s -> %s", hwnd, self.is_minimized(hwnd))
            return True
        except Exception as exc:
            LOG.error("minimize hwnd=%s failed: %s", hwnd, exc)
            return False

    def restore(self, hwnd: int) -> bool:
        if not self.is_valid(hwnd):
            LOG.error("restore invalid hwnd=%s", hwnd)
            return False
        try:
            _user32.ShowWindow(ctypes.c_void_p(hwnd), SW_RESTORE)
            time.sleep(0.2)
            LOG.info("restore hwnd=%s", hwnd)
            return True
        except Exception as exc:
            LOG.error("restore hwnd=%s failed: %s", hwnd, exc)
            return False

    def close(self, hwnd: int, timeout: float = 5.0) -> bool:
        if not self.is_valid(hwnd):
            LOG.error("close invalid hwnd=%s", hwnd)
            return False
        try:
            _user32.PostMessageW(ctypes.c_void_p(hwnd), WM_CLOSE, ctypes.c_void_p(0), ctypes.c_void_p(0))
            deadline = time.time() + max(0.5, timeout)
            while time.time() < deadline:
                if not self.is_valid(hwnd):
                    LOG.info("close hwnd=%s success", hwnd)
                    return True
                time.sleep(0.25)
            gone = not self.is_valid(hwnd)
            LOG.info("close hwnd=%s -> %s", hwnd, gone)
            return gone
        except Exception as exc:
            LOG.error("close hwnd=%s failed: %s", hwnd, exc)
            return False


def _send_input(inputs: List[INPUT]) -> bool:
    if not _require_win32() or not inputs:
        return False
    try:
        arr = (INPUT * len(inputs))(*inputs)
        n = int(_user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT)))
        if n != len(inputs):
            LOG.warning("SendInput partial %d/%d %s", n, len(inputs), _last_error())
            return n > 0
        return True
    except Exception as exc:
        LOG.warning("SendInput failed: %s", exc)
        return False


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    mi = MOUSEINPUT(dx=int(dx), dy=int(dy), mouseData=int(data),
                    dwFlags=int(flags), time=0, dwExtraInfo=_ULONG_PTR(0))
    union = _INPUT_I(mi=mi)
    return INPUT(type=INPUT_MOUSE, I=union)


def _kb_input(vk: int, scan: int, flags: int) -> INPUT:
    ki = KEYBDINPUT(wVk=int(vk), wScan=int(scan), dwFlags=int(flags),
                    time=0, dwExtraInfo=_ULONG_PTR(0))
    union = _INPUT_I(ki=ki)
    return INPUT(type=INPUT_KEYBOARD, I=union)


def _unicode_inputs(text: str, key_up: bool = False) -> List[INPUT]:
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if key_up else 0)
    return [_kb_input(0, ord(ch), flags) for ch in text]


def _cursor_pos() -> Tuple[int, int]:
    if _require_win32():
        try:
            pt = POINT()
            if _user32.GetCursorPos(ctypes.byref(pt)):
                return int(pt.x), int(pt.y)
        except Exception:
            LOG.debug("GetCursorPos failed", exc_info=True)
    if _HAS_PYAUTOGUI and _pag is not None:
        try:
            p = _pag.position()
            return int(p[0]), int(p[1])
        except Exception:
            LOG.debug("pyautogui position failed", exc_info=True)
    return 0, 0


def _bezier(p0: Tuple[float, float], p1: Tuple[float, float],
            p2: Tuple[float, float], p3: Tuple[float, float], t: float) -> Tuple[float, float]:
    u = 1.0 - t
    x = u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0]
    y = u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]
    return x, y


def _human_path(sx: int, sy: int, ex: int, ey: int, steps: int) -> List[Tuple[int, int]]:
    dx, dy = ex - sx, ey - sy
    dist = math.hypot(dx, dy)
    if steps <= 1 or dist < 2:
        return [(ex, ey)]
    spread = max(8.0, min(120.0, dist * 0.18))
    nx, ny = (-dy / dist, dx / dist) if dist else (0.0, 0.0)
    off1 = random.uniform(-spread, spread)
    off2 = random.uniform(-spread, spread)
    p0 = (float(sx), float(sy))
    p3 = (float(ex), float(ey))
    p1 = (sx + dx * 0.3 + nx * off1, sy + dy * 0.3 + ny * off1)
    p2 = (sx + dx * 0.7 + nx * off2, sy + dy * 0.7 + ny * off2)
    pts: List[Tuple[int, int]] = []
    for i in range(1, steps + 1):
        t = i / steps
        te = t * t * (3 - 2 * t)
        x, y = _bezier(p0, p1, p2, p3, te)
        jx = random.uniform(-1.2, 1.2) if 0 < i < steps else 0.0
        jy = random.uniform(-1.2, 1.2) if 0 < i < steps else 0.0
        pts.append((int(round(x + jx)), int(round(y + jy))))
    pts[-1] = (ex, ey)
    return pts


class AlphaEngine:
    engine_name = "alpha"

    def __init__(self, mapper: CoordinateMapper, cfg: UIEngineConfig) -> None:
        self.mapper = mapper
        self.cfg = cfg
        self._lock = threading.Lock()

    def available(self) -> bool:
        return _require_win32()

    def _set_pos(self, x: int, y: int) -> bool:
        x, y = self.mapper.clamp(int(x), int(y))
        if _require_win32():
            try:
                if _user32.SetCursorPos(int(x), int(y)):
                    return True
                LOG.warning("SetCursorPos(%d,%d) failed %s", x, y, _last_error())
            except Exception as exc:
                LOG.warning("SetCursorPos raised: %s", exc)
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                _pag.moveTo(x, y, duration=0.01)
                return True
            except Exception as exc:
                LOG.warning("beta fallback moveTo failed: %s", exc)
        return False

    def get_position(self) -> Tuple[int, int]:
        return _cursor_pos()

    def smooth_move(self, x: int, y: int, duration: Optional[float] = None) -> bool:
        x, y = self.mapper.clamp(int(x), int(y))
        sx, sy = _cursor_pos()
        if (sx, sy) == (x, y):
            return True
        dur = self.cfg.move_duration if duration is None else max(0.01, duration)
        dist = math.hypot(x - sx, y - sy)
        steps = max(4, min(80, int(dist / 9) + int(dur * 45)))
        pts = _human_path(sx, sy, x, y, steps)
        per = dur / len(pts)
        try:
            with self._lock:
                for px, py in pts:
                    self._set_pos(px, py)
                    time.sleep(per * random.uniform(0.7, 1.3))
                self._set_pos(x, y)
            LOG.debug("alpha smooth_move (%d,%d)->(%d,%d) steps=%d", sx, sy, x, y, len(pts))
            return True
        except Exception as exc:
            LOG.warning("smooth_move failed: %s", exc)
            return self._set_pos(x, y)

    def move(self, x: int, y: int, smooth: Optional[bool] = None) -> bool:
        use_smooth = self.cfg.smooth_move if smooth is None else smooth
        if use_smooth:
            return self.smooth_move(x, y)
        return self._set_pos(x, y)

    def _down_up(self, down_flag: int, up_flag: int, x: Optional[int],
                 y: Optional[int], hold: float) -> bool:
        try:
            with self._lock:
                if x is not None and y is not None:
                    if self.cfg.smooth_move:
                        self.smooth_move(x, y)
                    else:
                        self._set_pos(x, y)
                    time.sleep(random.uniform(0.015, 0.05))
                if not _send_input([_mouse_input(down_flag)]):
                    if _require_win32():
                        _user32.mouse_event(down_flag, 0, 0, 0, 0)
                    else:
                        return False
                time.sleep(max(0.01, hold))
                if not _send_input([_mouse_input(up_flag)]):
                    if _require_win32():
                        _user32.mouse_event(up_flag, 0, 0, 0, 0)
                    else:
                        return False
            return True
        except Exception as exc:
            LOG.warning("click failed flags=%s: %s", (down_flag, up_flag), exc)
            return False

    def left_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        ok = self._down_up(MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, x, y, self.cfg.click_delay)
        LOG.debug("alpha left_click(%s,%s) -> %s", x, y, ok)
        return ok

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        ok = self._down_up(MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP, x, y, self.cfg.click_delay)
        LOG.debug("alpha right_click(%s,%s) -> %s", x, y, ok)
        return ok

    def middle_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        ok = self._down_up(MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP, x, y, self.cfg.click_delay)
        LOG.debug("alpha middle_click(%s,%s) -> %s", x, y, ok)
        return ok

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        try:
            interval = 0.09
            if _require_win32():
                try:
                    interval = max(0.03, min(0.5, int(_user32.GetDoubleClickTime()) / 1000.0 / 2.2))
                except Exception:
                    LOG.debug("GetDoubleClickTime failed", exc_info=True)
            if x is not None and y is not None:
                if self.cfg.smooth_move:
                    self.smooth_move(x, y)
                else:
                    self._set_pos(x, y)
                time.sleep(random.uniform(0.02, 0.06))
            ok1 = _send_input([_mouse_input(MOUSEEVENTF_LEFTDOWN)]) or True
            time.sleep(random.uniform(0.02, 0.05))
            ok2 = _send_input([_mouse_input(MOUSEEVENTF_LEFTUP)]) or True
            time.sleep(interval)
            ok3 = _send_input([_mouse_input(MOUSEEVENTF_LEFTDOWN)]) or True
            time.sleep(random.uniform(0.02, 0.05))
            ok4 = _send_input([_mouse_input(MOUSEEVENTF_LEFTUP)]) or True
            ok = all([ok1, ok2, ok3, ok4])
            LOG.debug("alpha double_click(%s,%s) -> %s", x, y, ok)
            return ok
        except Exception as exc:
            LOG.warning("double_click failed: %s", exc)
            return False

    def mouse_down(self, button: str = "left") -> bool:
        flag = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
                "middle": MOUSEEVENTF_MIDDLEDOWN}.get(button.lower(), MOUSEEVENTF_LEFTDOWN)
        ok = _send_input([_mouse_input(flag)])
        LOG.debug("alpha mouse_down %s -> %s", button, ok)
        return ok

    def mouse_up(self, button: str = "left") -> bool:
        flag = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
                "middle": MOUSEEVENTF_MIDDLEUP}.get(button.lower(), MOUSEEVENTF_LEFTUP)
        ok = _send_input([_mouse_input(flag)])
        LOG.debug("alpha mouse_up %s -> %s", button, ok)
        return ok

    def drag(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.6,
             button: str = "left") -> bool:
        down_f = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
                  "middle": MOUSEEVENTF_MIDDLEDOWN}.get(button.lower(), MOUSEEVENTF_LEFTDOWN)
        up_f = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
                "middle": MOUSEEVENTF_MIDDLEUP}.get(button.lower(), MOUSEEVENTF_LEFTUP)
        try:
            sx, sy = self.mapper.clamp(sx, sy)
            ex, ey = self.mapper.clamp(ex, ey)
            self.smooth_move(sx, sy, duration=0.25)
            time.sleep(random.uniform(0.05, 0.12))
            with self._lock:
                _send_input([_mouse_input(down_f)])
                time.sleep(random.uniform(0.05, 0.12))
                pts = _human_path(sx, sy, ex, ey, max(8, min(60, int(math.hypot(ex - sx, ey - sy) / 8) + 10)))
                per = max(0.004, duration / len(pts))
                for px, py in pts:
                    self._set_pos(px, py)
                    time.sleep(per * random.uniform(0.8, 1.2))
                self._set_pos(ex, ey)
                time.sleep(random.uniform(0.04, 0.1))
                _send_input([_mouse_input(up_f)])
            LOG.debug("alpha drag (%d,%d)->(%d,%d) ok", sx, sy, ex, ey)
            return True
        except Exception as exc:
            LOG.warning("drag failed: %s", exc)
            try:
                _send_input([_mouse_input(up_f)])
            except Exception:
                LOG.debug("drag release failed", exc_info=True)
            return False

    def scroll(self, clicks: int, horizontal: bool = False) -> bool:
        try:
            data = int(clicks * WHEEL_DELTA)
            flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
            with self._lock:
                ok = _send_input([_mouse_input(flag, data=data)])
            LOG.debug("alpha scroll clicks=%d horiz=%s -> %s", clicks, horizontal, ok)
            return ok
        except Exception as exc:
            LOG.warning("scroll failed: %s", exc)
            return False


class BetaEngine:
    engine_name = "beta"

    def __init__(self, mapper: CoordinateMapper, cfg: UIEngineConfig) -> None:
        self.mapper = mapper
        self.cfg = cfg
        self._lock = threading.Lock()

    def available(self) -> bool:
        if _HAS_PYAUTOGUI and _pag is not None:
            return True
        return _require_win32()

    def _pag_move(self, x: int, y: int, duration: float) -> bool:
        assert _pag is not None
        _pag.moveTo(int(x), int(y), duration=max(0.01, duration))
        return True

    def move(self, x: int, y: int, smooth: Optional[bool] = None) -> bool:
        x, y = self.mapper.clamp(int(x), int(y))
        use_smooth = self.cfg.smooth_move if smooth is None else smooth
        dur = self.cfg.move_duration if use_smooth else 0.02
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                with self._lock:
                    self._pag_move(x, y, dur)
                return True
            except Exception as exc:
                LOG.warning("beta moveTo failed: %s", exc)
        if _require_win32():
            try:
                with self._lock:
                    _user32.SetCursorPos(int(x), int(y))
                return True
            except Exception as exc:
                LOG.warning("beta SetCursorPos failed: %s", exc)
                return False
        return False

    def smooth_move(self, x: int, y: int, duration: Optional[float] = None) -> bool:
        return self.move(x, y, smooth=True)

    def _beta_click(self, x: Optional[int], y: Optional[int], button: str, clicks: int) -> bool:
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                with self._lock:
                    if x is not None and y is not None:
                        cx, cy = self.mapper.clamp(int(x), int(y))
                        _pag.moveTo(cx, cy, duration=0.08)
                        time.sleep(random.uniform(0.01, 0.04))
                    _pag.click(button=button, clicks=int(clicks),
                               interval=random.uniform(0.05, 0.12))
                LOG.debug("beta click %s x%s", button, clicks)
                return True
            except Exception as exc:
                LOG.warning("beta pyautogui click failed: %s", exc)
        if _require_win32():
            try:
                down = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
                        "middle": MOUSEEVENTF_MIDDLEDOWN}[button]
                up = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
                      "middle": MOUSEEVENTF_MIDDLEUP}[button]
                with self._lock:
                    if x is not None and y is not None:
                        cx, cy = self.mapper.clamp(int(x), int(y))
                        _user32.SetCursorPos(cx, cy)
                        time.sleep(0.02)
                    for _i in range(max(1, clicks)):
                        _user32.mouse_event(down, 0, 0, 0, 0)
                        time.sleep(random.uniform(0.03, 0.08))
                        _user32.mouse_event(up, 0, 0, 0, 0)
                        time.sleep(random.uniform(0.04, 0.1))
                return True
            except Exception as exc:
                LOG.warning("beta mouse_event click failed: %s", exc)
                return False
        return False

    def left_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        return self._beta_click(x, y, "left", 1)

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        return self._beta_click(x, y, "right", 1)

    def middle_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        return self._beta_click(x, y, "middle", 1)

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        return self._beta_click(x, y, "left", 2)

    def mouse_down(self, button: str = "left") -> bool:
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                _pag.mouseDown(button=button)
                return True
            except Exception as exc:
                LOG.warning("beta mouseDown failed: %s", exc)
        flag = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
                "middle": MOUSEEVENTF_MIDDLEDOWN}.get(button.lower(), MOUSEEVENTF_LEFTDOWN)
        if _require_win32():
            try:
                _user32.mouse_event(flag, 0, 0, 0, 0)
                return True
            except Exception as exc:
                LOG.warning("beta mouse_event down failed: %s", exc)
                return False
        return False

    def mouse_up(self, button: str = "left") -> bool:
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                _pag.mouseUp(button=button)
                return True
            except Exception as exc:
                LOG.warning("beta mouseUp failed: %s", exc)
        flag = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
                "middle": MOUSEEVENTF_MIDDLEUP}.get(button.lower(), MOUSEEVENTF_LEFTUP)
        if _require_win32():
            try:
                _user32.mouse_event(flag, 0, 0, 0, 0)
                return True
            except Exception as exc:
                LOG.warning("beta mouse_event up failed: %s", exc)
                return False
        return False

    def drag(self, sx: int, sy: int, ex: int, ey: int, duration: float = 0.6,
             button: str = "left") -> bool:
        sx, sy = self.mapper.clamp(sx, sy)
        ex, ey = self.mapper.clamp(ex, ey)
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                with self._lock:
                    _pag.moveTo(sx, sy, duration=0.15)
                    _pag.dragTo(ex, ey, duration=max(0.1, duration), button=button)
                return True
            except Exception as exc:
                LOG.warning("beta dragTo failed: %s", exc)
        if _require_win32():
            try:
                down = {"left": MOUSEEVENTF_LEFTDOWN, "right": MOUSEEVENTF_RIGHTDOWN,
                        "middle": MOUSEEVENTF_MIDDLEDOWN}.get(button.lower(), MOUSEEVENTF_LEFTDOWN)
                up = {"left": MOUSEEVENTF_LEFTUP, "right": MOUSEEVENTF_RIGHTUP,
                      "middle": MOUSEEVENTF_MIDDLEUP}.get(button.lower(), MOUSEEVENTF_LEFTUP)
                with self._lock:
                    _user32.SetCursorPos(sx, sy)
                    time.sleep(0.08)
                    _user32.mouse_event(down, 0, 0, 0, 0)
                    time.sleep(0.06)
                    steps = max(6, int(math.hypot(ex - sx, ey - sy) / 12))
                    for i in range(1, steps + 1):
                        px = int(sx + (ex - sx) * i / steps)
                        py = int(sy + (ey - sy) * i / steps)
                        _user32.SetCursorPos(px, py)
                        time.sleep(duration / steps)
                    _user32.mouse_event(up, 0, 0, 0, 0)
                return True
            except Exception as exc:
                LOG.warning("beta drag fallback failed: %s", exc)
                return False
        return False

    def scroll(self, clicks: int, horizontal: bool = False) -> bool:
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                if horizontal:
                    try:
                        _pag.hscroll(int(clicks))
                    except Exception:
                        _pag.scroll(0, 0)
                else:
                    _pag.scroll(int(clicks))
                return True
            except Exception as exc:
                LOG.warning("beta scroll failed: %s", exc)
        if _require_win32():
            try:
                flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
                _user32.mouse_event(flag, 0, 0, int(clicks * WHEEL_DELTA), 0)
                return True
            except Exception as exc:
                LOG.warning("beta scroll fallback failed: %s", exc)
                return False
        return False


class KeyboardController:
    def __init__(self, cfg: UIEngineConfig) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()

    def available(self) -> bool:
        return _require_win32() or (_HAS_PYAUTOGUI and _pag is not None)

    def _jitter_sleep(self) -> None:
        time.sleep(random.uniform(self.cfg.type_min_delay, self.cfg.type_max_delay))

    def key_down(self, vk: int) -> bool:
        if _require_win32():
            try:
                with self._lock:
                    ok = _send_input([_kb_input(int(vk), _scan_for_vk(int(vk)), 0)])
                LOG.debug("key_down vk=%s -> %s", vk, ok)
                return ok
            except Exception as exc:
                LOG.warning("key_down vk=%s failed: %s", vk, exc)
                return False
        return False

    def key_up(self, vk: int) -> bool:
        if _require_win32():
            try:
                with self._lock:
                    ok = _send_input([_kb_input(int(vk), _scan_for_vk(int(vk)), KEYEVENTF_KEYUP)])
                LOG.debug("key_up vk=%s -> %s", vk, ok)
                return ok
            except Exception as exc:
                LOG.warning("key_up vk=%s failed: %s", vk, exc)
                return False
        return False

    def tap_key(self, name: str) -> bool:
        try:
            vk = vk_from_name(name)
        except ValueError as exc:
            LOG.error("tap_key bad key %r: %s", name, exc)
            return False
        if _require_win32():
            try:
                scan = _scan_for_vk(vk)
                with self._lock:
                    _send_input([_kb_input(vk, scan, 0)])
                    time.sleep(random.uniform(0.02, 0.06))
                    _send_input([_kb_input(vk, scan, KEYEVENTF_KEYUP)])
                LOG.debug("tap_key %r ok", name)
                return True
            except Exception as exc:
                LOG.warning("tap_key %r failed: %s", name, exc)
                return False
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                _pag.press(str(name).lower())
                return True
            except Exception as exc:
                LOG.warning("beta tap_key failed: %s", exc)
                return False
        LOG.error("tap_key no backend for %r", name)
        return False

    def type_text(self, text: str, jitter: bool = True) -> bool:
        if not text:
            LOG.warning("type_text empty string")
            return True
        if _require_win32():
            try:
                typed = 0
                with self._lock:
                    for i, ch in enumerate(text):
                        _send_input(_unicode_inputs(ch))
                        typed += 1
                        if jitter:
                            self._jitter_sleep()
                            if ch in ".!?\n" or (i > 0 and i % 37 == 0):
                                time.sleep(random.uniform(0.05, self.cfg.type_burst_pause))
                LOG.info("typed %d chars via SendInput-unicode", typed)
                return True
            except Exception as exc:
                LOG.warning("type_text SendInput failed, trying beta: %s", exc)
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                _pag.typewrite(str(text), interval=random.uniform(
                    self.cfg.type_min_delay, self.cfg.type_max_delay))
                LOG.info("typed %d chars via pyautogui", len(text))
                return True
            except Exception as exc:
                LOG.error("type_text beta failed: %s", exc)
                return False
        LOG.error("type_text no backend available")
        return False

    def press_hotkey(self, *keys: str) -> bool:
        if not keys:
            LOG.error("press_hotkey needs keys")
            return False
        if len(keys) == 1 and "+" in keys[0]:
            keys = tuple(p.strip() for p in keys[0].split("+") if p.strip())
        try:
            mod_vks, main_vk = split_hotkey(tuple(keys))
        except ValueError as exc:
            LOG.error("press_hotkey bad combo %s: %s", keys, exc)
            return False
        if _require_win32():
            try:
                with self._lock:
                    for mv in mod_vks:
                        _send_input([_kb_input(mv, _scan_for_vk(mv), 0)])
                        time.sleep(random.uniform(0.015, 0.04))
                    main_scan = _scan_for_vk(main_vk)
                    _send_input([_kb_input(main_vk, main_scan, 0)])
                    time.sleep(random.uniform(0.03, 0.07))
                    _send_input([_kb_input(main_vk, main_scan, KEYEVENTF_KEYUP)])
                    time.sleep(random.uniform(0.015, 0.04))
                    for mv in reversed(mod_vks):
                        _send_input([_kb_input(mv, _scan_for_vk(mv), KEYEVENTF_KEYUP)])
                        time.sleep(random.uniform(0.01, 0.03))
                LOG.info("hotkey sent: %s", "+".join(keys))
                return True
            except Exception as exc:
                LOG.warning("press_hotkey SendInput failed: %s", exc)
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                _pag.hotkey(*[str(k).lower() for k in keys])
                LOG.info("hotkey sent via beta: %s", "+".join(keys))
                return True
            except Exception as exc:
                LOG.error("press_hotkey beta failed: %s", exc)
                return False
        LOG.error("press_hotkey no backend")
        return False


class GlobalHotkeyDispatcher:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._regs: Dict[int, Tuple[int, int, Callable[[], None], str]] = {}
        self._next_id = 1
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._running = False

    def _mods_flag(self, modifiers: List[str]) -> int:
        flag = 0
        for m in modifiers:
            ml = m.strip().lower()
            if ml not in MOD_NAME_TO_FLAG:
                raise ValueError(f"bad modifier: {m!r}")
            flag |= MOD_NAME_TO_FLAG[ml]
        return flag

    def register(self, modifiers: List[str], key: str, callback: Callable[[], None],
                 name: Optional[str] = None) -> int:
        if not _require_win32():
            LOG.error("global hotkey register needs Windows")
            return 0
        try:
            mods_flag = self._mods_flag(modifiers)
            vk = vk_from_name(key)
        except ValueError as exc:
            LOG.error("register hotkey bad combo: %s", exc)
            return 0
        with self._lock:
            hid = self._next_id
            self._next_id += 1
            label = name or f"{'+'.join(modifiers)}+{key}"
            if self._running:
                try:
                    if not _user32.RegisterHotKey(None, hid, mods_flag, vk):
                        LOG.error("RegisterHotKey failed %s %s", label, _last_error())
                        return 0
                except Exception as exc:
                    LOG.error("RegisterHotKey raised %s: %s", label, exc)
                    return 0
            self._regs[hid] = (mods_flag, vk, callback, label)
            LOG.info("hotkey registered id=%d %s", hid, label)
            return hid

    def unregister(self, hid: int) -> bool:
        with self._lock:
            if hid not in self._regs:
                LOG.warning("unregister unknown hotkey id=%d", hid)
                return False
            label = self._regs[hid][3]
            if self._running and _require_win32():
                try:
                    _user32.UnregisterHotKey(None, hid)
                except Exception as exc:
                    LOG.warning("UnregisterHotKey id=%d failed: %s", hid, exc)
            del self._regs[hid]
            LOG.info("hotkey unregistered id=%d %s", hid, label)
            return True

    def _loop(self) -> None:
        assert _require_win32()
        with self._lock:
            regs = list(self._regs.items())
        for hid, (mods, vk, _cb, label) in regs:
            try:
                if not _user32.RegisterHotKey(None, hid, mods, vk):
                    LOG.error("RegisterHotKey failed at start id=%d %s %s", hid, label, _last_error())
            except Exception as exc:
                LOG.error("RegisterHotKey raised id=%d: %s", hid, exc)
        msg = MSG()
        LOG.info("global hotkey loop started (%d hotkeys)", len(regs))
        while not self._stop.is_set():
            try:
                rc = _user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            except Exception as exc:
                LOG.warning("GetMessageW failed: %s", exc)
                time.sleep(0.05)
                continue
            if rc == 0 or rc == -1:
                break
            try:
                if msg.message == WM_HOTKEY:
                    hid = int(msg.wParam) if not isinstance(msg.wParam, int) else msg.wParam
                    with self._lock:
                        entry = self._regs.get(hid)
                    if entry:
                        _mods, _vk, cb, label = entry
                        LOG.info("hotkey fired id=%d %s", hid, label)
                        threading.Thread(target=self._safe_fire, args=(cb, hid, label), daemon=True).start()
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))
            except Exception as exc:
                LOG.warning("hotkey dispatch failed: %s", exc)
        with self._lock:
            ids = list(self._regs.keys())
        for hid in ids:
            try:
                _user32.UnregisterHotKey(None, hid)
            except Exception:
                LOG.debug("cleanup UnregisterHotKey %d failed", hid, exc_info=True)
        LOG.info("global hotkey loop stopped")

    def _safe_fire(self, cb: Callable[[], None], hid: int, label: str) -> None:
        try:
            cb()
        except Exception as exc:
            LOG.error("hotkey callback id=%d %s failed: %s", hid, label, exc)

    def start(self) -> bool:
        if not _require_win32():
            LOG.error("hotkey dispatcher needs Windows")
            return False
        with self._lock:
            if self._running:
                LOG.warning("hotkey dispatcher already running")
                return True
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="jarvis-hotkeys", daemon=True)
            self._running = True
        try:
            assert self._thread is not None
            self._thread.start()
            LOG.info("hotkey dispatcher started")
            return True
        except Exception as exc:
            LOG.error("hotkey dispatcher start failed: %s", exc)
            with self._lock:
                self._running = False
            return False

    def stop(self) -> bool:
        with self._lock:
            if not self._running:
                return True
            self._stop.set()
            thr = self._thread
        try:
            if thr is not None and thr.is_alive():
                thr.join(timeout=3.0)
        except Exception as exc:
            LOG.warning("hotkey stop join failed: %s", exc)
        with self._lock:
            self._running = False
            self._thread = None
        LOG.info("hotkey dispatcher stopped")
        return True

    def registered(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [{"id": hid, "label": label} for hid, (_m, _v, _c, label) in self._regs.items()]


class ScreenshotEngine:
    def __init__(self, monitors: MonitorManager, cfg: UIEngineConfig) -> None:
        self.monitors = monitors
        self.cfg = cfg

    def _pil_to_b64(self, img: Any, max_width: Optional[int],
                    quality: Optional[int], fmt: Optional[str]) -> Tuple[bool, str]:
        try:
            assert _PILImage is not None
            mw = self.cfg.screenshot_max_width if max_width is None else max_width
            q = self.cfg.screenshot_quality if quality is None else quality
            f = (self.cfg.screenshot_format if fmt is None else fmt).upper()
            if f not in ("JPEG", "JPG", "PNG", "WEBP"):
                f = "JPEG"
            if f == "JPG":
                f = "JPEG"
            if img.mode in ("RGBA", "LA", "P") and f == "JPEG":
                bg = _PILImage.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1] if img.mode in ("RGBA", "LA") else None)
                img = bg
            elif img.mode != "RGB" and f in ("JPEG", "WEBP"):
                img = img.convert("RGB")
            if mw and img.width > mw:
                ratio = mw / img.width
                nh = max(1, int(img.height * ratio))
                img = img.resize((mw, nh), _PILImage.LANCZOS)
            buf = io.BytesIO()
            save_kw: Dict[str, Any] = {}
            if f == "JPEG":
                save_kw = {"quality": max(10, min(95, int(q))), "optimize": True}
            img.save(buf, format=f, **save_kw)
            return True, base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception as exc:
            LOG.error("encode b64 failed: %s", exc)
            return False, ""

    def _grab_mss(self, region: Dict[str, int]) -> Optional[Any]:
        if not _HAS_MSS or _mss_mod is None or not _HAS_PIL or _PILImage is None:
            return None
        try:
            with _mss_mod.mss() as sct:
                shot = sct.grab(region)
                img = _PILImage.frombytes("RGB", shot.size, shot.rgb)
                return img
        except Exception as exc:
            LOG.warning("mss grab failed %s: %s", region, exc)
            return None

    def _grab_pil(self, region: Tuple[int, int, int, int]) -> Optional[Any]:
        if not _HAS_PIL or _PILGrab is None:
            return None
        try:
            l, t, w, h = region
            img = _PILGrab.grab(bbox=(l, t, l + w, t + h), all_screens=True)
            return img
        except Exception as exc:
            LOG.warning("ImageGrab failed: %s", exc)
            return None

    def capture_region(self, x: int, y: int, w: int, h: int,
                       max_width: Optional[int] = None,
                       quality: Optional[int] = None,
                       fmt: Optional[str] = None) -> Tuple[bool, str]:
        if w <= 0 or h <= 0:
            LOG.error("capture_region bad size w=%d h=%d", w, h)
            return False, ""
        vx, vy, vw, vh = self.monitors.virtual_rect()
        x = max(vx, min(x, vx + vw - 1))
        y = max(vy, min(y, vy + vh - 1))
        w = max(1, min(w, vx + vw - x))
        h = max(1, min(h, vy + vh - y))
        img = self._grab_mss({"left": int(x), "top": int(y), "width": int(w), "height": int(h)})
        if img is None:
            img = self._grab_pil((int(x), int(y), int(w), int(h)))
        if img is None:
            LOG.error("capture_region no backend produced image")
            return False, ""
        ok, b64 = self._pil_to_b64(img, max_width, quality, fmt)
        LOG.info("capture_region (%d,%d %dx%d) -> %s bytes_b64=%d", x, y, w, h, ok, len(b64))
        return ok, b64

    def capture_monitor(self, index: int = 0, **kw: Any) -> Tuple[bool, str]:
        mons = self.monitors.list()
        if not mons:
            LOG.error("capture_monitor no monitors")
            return False, ""
        index = max(0, min(index, len(mons) - 1))
        m = mons[index]
        return self.capture_region(m.left, m.top, m.width, m.height, **kw)

    def capture_full(self, **kw: Any) -> Tuple[bool, str]:
        vx, vy, vw, vh = self.monitors.virtual_rect()
        return self.capture_region(vx, vy, vw, vh, **kw)

    def capture_window(self, hwnd: int, **kw: Any) -> Tuple[bool, str, Optional[Tuple[int, int, int, int]]]:
        if not hwnd:
            LOG.error("capture_window hwnd=0")
            return False, "", None
        rect: Optional[Tuple[int, int, int, int]] = None
        if _require_win32():
            try:
                rc = RECT()
                if _user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rc)):
                    rect = (int(rc.left), int(rc.top),
                            int(rc.right - rc.left), int(rc.bottom - rc.top))
            except Exception as exc:
                LOG.warning("capture_window GetWindowRect failed: %s", exc)
        if rect is None and _HAS_PYWINAUTO:
            try:
                from pywinauto import Desktop as _D
                for win in _D(backend="uia").windows():
                    try:
                        if int(win.handle) == hwnd:
                            r = win.rectangle()
                            rect = (int(r.left), int(r.top),
                                    int(r.right - r.left), int(r.bottom - r.top))
                            break
                    except Exception:
                        LOG.debug("pywinauto rect read failed", exc_info=True)
            except Exception as exc:
                LOG.warning("capture_window pywinauto failed: %s", exc)
        if rect is None:
            LOG.error("capture_window cannot resolve rect hwnd=%s", hwnd)
            return False, "", None
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            LOG.error("capture_window degenerate rect %s", rect)
            return False, "", rect
        ok, b64 = self.capture_region(x, y, w, h, **kw)
        full_rect = (x, y, x + w, y + h)
        return ok, b64, full_rect

    def save_region(self, path: str, x: int, y: int, w: int, h: int) -> bool:
        ok, b64 = self.capture_region(x, y, w, h, fmt="PNG")
        if not ok:
            return False
        try:
            with open(path, "wb") as fh:
                fh.write(base64.b64decode(b64))
            LOG.info("saved screenshot %s", path)
            return True
        except Exception as exc:
            LOG.error("save_region %s failed: %s", path, exc)
            return False


class UIEngine:
    def __init__(self, config: Optional[UIEngineConfig] = None) -> None:
        self.config = config or UIEngineConfig()
        global LOG
        LOG = get_logger(self.config.log_level, self.config.log_file)
        if _HAS_PYAUTOGUI and _pag is not None:
            try:
                _pag.FAILSAFE = False
                _pag.PAUSE = 0.02
            except Exception:
                LOG.debug("pyautogui tune failed", exc_info=True)
        self.monitors = MonitorManager()
        self.mapper = CoordinateMapper(self.monitors)
        self.windows = WindowManager(self.monitors)
        self.alpha = AlphaEngine(self.mapper, self.config)
        self.beta = BetaEngine(self.mapper, self.config)
        self.keyboard = KeyboardController(self.config)
        self.hotkeys = GlobalHotkeyDispatcher()
        self.shots = ScreenshotEngine(self.monitors, self.config)
        self._engine = self._resolve_engine(self.config.engine)
        self._history: List[ActionRecord] = []
        self._hist_lock = threading.Lock()
        LOG.info("UIEngine init engine=%s dpi_mode=%s mss=%s pil=%s pag=%s pywinauto=%s",
                 self._engine, _DPI_MODE, _HAS_MSS, _HAS_PIL, _HAS_PYAUTOGUI, _HAS_PYWINAUTO)

    def _resolve_engine(self, name: str) -> str:
        n = (name or "auto").lower()
        if n == "alpha":
            return "alpha"
        if n == "beta":
            return "beta"
        if self.alpha.available():
            return "alpha"
        if self.beta.available():
            return "beta"
        return "alpha"

    @property
    def engine(self) -> str:
        return self._engine

    def set_engine(self, name: str) -> bool:
        n = name.strip().lower()
        if n not in ("alpha", "beta", "auto"):
            LOG.error("set_engine bad name %r", name)
            return False
        self._engine = self._resolve_engine(n)
        LOG.info("engine switched to %s", self._engine)
        return True

    def _mouse(self):
        primary = self.alpha if self._engine == "alpha" else self.beta
        fallback = self.beta if self._engine == "alpha" else self.alpha
        return primary, fallback

    def _record(self, action: str, success: bool, details: str, t0: float) -> None:
        rec = ActionRecord(action=action, success=success, details=details,
                           duration_ms=round((time.perf_counter() - t0) * 1000, 2))
        with self._hist_lock:
            self._history.append(rec)
            if len(self._history) > 500:
                del self._history[:len(self._history) - 500]
        if success:
            LOG.info("%s OK :: %s (%.1fms)", action, details, rec.duration_ms)
        else:
            LOG.error("%s FAIL :: %s (%.1fms)", action, details, rec.duration_ms)

    def history(self, last: int = 20) -> List[ActionRecord]:
        with self._hist_lock:
            return list(self._history[-max(1, last):])

    def _run_mouse(self, action: str, fn_name: str, *args: Any, **kw: Any) -> bool:
        t0 = time.perf_counter()
        primary, fallback = self._mouse()
        try:
            ok = bool(getattr(primary, fn_name)(*args, **kw))
            if ok:
                self._record(action, True, f"{primary.engine_name}:{fn_name}{args}", t0)
                return True
            detail = f"{primary.engine_name}:{fn_name} returned False"
        except Exception as exc:
            detail = f"{primary.engine_name}:{fn_name} raised {exc}"
            LOG.warning(detail)
        if self.config.failover_enabled:
            try:
                ok2 = bool(getattr(fallback, fn_name)(*args, **kw))
                self._record(action, ok2, f"failover {fallback.engine_name}:{fn_name} :: {detail}", t0)
                return ok2
            except Exception as exc:
                self._record(action, False, f"both engines failed :: {detail} :: failover raised {exc}", t0)
                return False
        self._record(action, False, detail, t0)
        return False

    def get_monitors(self, refresh: bool = False) -> List[Dict[str, Any]]:
        try:
            mons = self.monitors.list(refresh=refresh)
            LOG.info("get_monitors -> %d", len(mons))
            return [m.to_dict() for m in mons]
        except Exception as exc:
            LOG.error("get_monitors failed: %s", exc)
            return []

    def mouse_position(self) -> Tuple[int, int]:
        return _cursor_pos()

    def move_mouse(self, x: int, y: int, smooth: Optional[bool] = None) -> bool:
        return self._run_mouse("move_mouse", "move", int(x), int(y), smooth=smooth)

    def click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        if x is None or y is None:
            return self._run_mouse("click", "left_click", x, y)
        return self._run_mouse("click", "left_click", int(x), int(y))

    def right_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        a = None if x is None else int(x)
        b = None if y is None else int(y)
        return self._run_mouse("right_click", "right_click", a, b)

    def double_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        a = None if x is None else int(x)
        b = None if y is None else int(y)
        return self._run_mouse("double_click", "double_click", a, b)

    def middle_click(self, x: Optional[int] = None, y: Optional[int] = None) -> bool:
        a = None if x is None else int(x)
        b = None if y is None else int(y)
        return self._run_mouse("middle_click", "middle_click", a, b)

    def mouse_down(self, button: str = "left") -> bool:
        return self._run_mouse("mouse_down", "mouse_down", str(button))

    def mouse_up(self, button: str = "left") -> bool:
        return self._run_mouse("mouse_up", "mouse_up", str(button))

    def drag_drop(self, sx: int, sy: int, ex: int, ey: int,
                  duration: float = 0.6, button: str = "left") -> bool:
        return self._run_mouse("drag_drop", "drag", int(sx), int(sy),
                               int(ex), int(ey), float(duration), str(button))

    def scroll(self, clicks: int, horizontal: bool = False) -> bool:
        return self._run_mouse("scroll", "scroll", int(clicks), bool(horizontal))

    def scroll_at(self, x: int, y: int, clicks: int, horizontal: bool = False) -> bool:
        t0 = time.perf_counter()
        if not self.move_mouse(x, y):
            self._record("scroll_at", False, f"move to ({x},{y}) failed", t0)
            return False
        ok = self.scroll(clicks, horizontal)
        self._record("scroll_at", ok, f"({x},{y}) clicks={clicks}", t0)
        return ok

    def click_window_center(self, hwnd: int, button: str = "left") -> bool:
        t0 = time.perf_counter()
        rect = self.windows.get_rect(hwnd)
        if rect is None:
            self._record("click_window_center", False, f"hwnd={hwnd} no rect", t0)
            return False
        cx, cy = self.mapper.window_center(rect)
        ok = self.click(cx, cy) if button.lower() == "left" else (
            self.right_click(cx, cy) if button.lower() == "right" else self.middle_click(cx, cy))
        self._record("click_window_center", ok, f"hwnd={hwnd} ({cx},{cy}) btn={button}", t0)
        return ok

    def type_text(self, text: str, jitter: bool = True) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.keyboard.type_text(str(text), jitter=bool(jitter))
            self._record("type_text", ok, f"len={len(text)}", t0)
            return ok
        except Exception as exc:
            self._record("type_text", False, f"raised {exc}", t0)
            return False

    def press_key(self, name: str) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.keyboard.tap_key(str(name))
            self._record("press_key", ok, str(name), t0)
            return ok
        except Exception as exc:
            self._record("press_key", False, f"{name} raised {exc}", t0)
            return False

    def press_hotkey(self, *keys: str) -> bool:
        t0 = time.perf_counter()
        try:
            flat: List[str] = []
            for k in keys:
                flat.extend([p.strip() for p in str(k).split("+") if p.strip()] if "+" in str(k) and len(keys) == 1 else [str(k)])
            ok = self.keyboard.press_hotkey(*flat)
            self._record("press_hotkey", ok, "+".join(flat), t0)
            return ok
        except Exception as exc:
            self._record("press_hotkey", False, f"{keys} raised {exc}", t0)
            return False

    def find_window(self, title_regex: str, class_regex: Optional[str] = None,
                    timeout: float = 5.0) -> int:
        t0 = time.perf_counter()
        try:
            hwnd = self.windows.find_window(title_regex, class_regex, True, timeout)
            self._record("find_window", hwnd != 0, f"{title_regex!r} -> {hwnd}", t0)
            return hwnd
        except Exception as exc:
            self._record("find_window", False, f"{title_regex!r} raised {exc}", t0)
            return 0

    def list_windows(self, visible_only: bool = True) -> List[Dict[str, Any]]:
        try:
            infos = self.windows.list_windows(visible_only)
            LOG.info("list_windows -> %d", len(infos))
            return [{"hwnd": w.hwnd, "title": w.title, "cls": w.cls,
                     "rect": w.rect, "visible": w.visible, "active": w.active} for w in infos]
        except Exception as exc:
            LOG.error("list_windows failed: %s", exc)
            return []

    def focus_window(self, hwnd: int, timeout: float = 5.0) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.windows.focus_window(int(hwnd), timeout)
            self._record("focus_window", ok, f"hwnd={hwnd}", t0)
            return ok
        except Exception as exc:
            self._record("focus_window", False, f"hwnd={hwnd} raised {exc}", t0)
            return False

    def focus_and_click(self, title_regex: str, x_off: int = 0, y_off: int = 0) -> bool:
        t0 = time.perf_counter()
        hwnd = self.find_window(title_regex)
        if not hwnd:
            self._record("focus_and_click", False, f"no window {title_regex!r}", t0)
            return False
        if not self.focus_window(hwnd):
            self._record("focus_and_click", False, f"focus failed hwnd={hwnd}", t0)
            return False
        rect = self.windows.get_rect(hwnd)
        if rect is None:
            self._record("focus_and_click", False, f"no rect hwnd={hwnd}", t0)
            return False
        cx, cy = self.mapper.window_center(rect)
        ok = self.click(cx + int(x_off), cy + int(y_off))
        self._record("focus_and_click", ok, f"hwnd={hwnd} ({cx + x_off},{cy + y_off})", t0)
        return ok

    def maximize_window(self, hwnd: int) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.windows.maximize(int(hwnd))
            self._record("maximize_window", ok, f"hwnd={hwnd}", t0)
            return ok
        except Exception as exc:
            self._record("maximize_window", False, f"hwnd={hwnd} raised {exc}", t0)
            return False

    def minimize_window(self, hwnd: int) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.windows.minimize(int(hwnd))
            self._record("minimize_window", ok, f"hwnd={hwnd}", t0)
            return ok
        except Exception as exc:
            self._record("minimize_window", False, f"hwnd={hwnd} raised {exc}", t0)
            return False

    def restore_window(self, hwnd: int) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.windows.restore(int(hwnd))
            self._record("restore_window", ok, f"hwnd={hwnd}", t0)
            return ok
        except Exception as exc:
            self._record("restore_window", False, f"hwnd={hwnd} raised {exc}", t0)
            return False

    def close_window(self, hwnd: int, timeout: float = 5.0) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.windows.close(int(hwnd), timeout)
            self._record("close_window", ok, f"hwnd={hwnd}", t0)
            return ok
        except Exception as exc:
            self._record("close_window", False, f"hwnd={hwnd} raised {exc}", t0)
            return False

    def is_window_active(self, hwnd: int) -> bool:
        try:
            ok = self.windows.is_active(int(hwnd))
            LOG.debug("is_window_active hwnd=%s -> %s", hwnd, ok)
            return ok
        except Exception as exc:
            LOG.error("is_window_active hwnd=%s failed: %s", hwnd, exc)
            return False

    def get_window_rect(self, hwnd: int) -> Optional[Tuple[int, int, int, int]]:
        try:
            return self.windows.get_rect(int(hwnd))
        except Exception as exc:
            LOG.error("get_window_rect hwnd=%s failed: %s", hwnd, exc)
            return None

    def wait_for_window(self, title_regex: str, timeout: float = 15.0) -> int:
        return self.find_window(title_regex, timeout=timeout)

    def screenshot(self, max_width: Optional[int] = None,
                   quality: Optional[int] = None) -> Tuple[bool, str]:
        t0 = time.perf_counter()
        try:
            ok, b64 = self.shots.capture_full(max_width=max_width, quality=quality)
            self._record("screenshot", ok, f"b64_len={len(b64)}", t0)
            return ok, b64
        except Exception as exc:
            self._record("screenshot", False, f"raised {exc}", t0)
            return False, ""

    def screenshot_monitor(self, index: int = 0, **kw: Any) -> Tuple[bool, str]:
        t0 = time.perf_counter()
        try:
            ok, b64 = self.shots.capture_monitor(int(index), **kw)
            self._record("screenshot_monitor", ok, f"mon={index} b64_len={len(b64)}", t0)
            return ok, b64
        except Exception as exc:
            self._record("screenshot_monitor", False, f"raised {exc}", t0)
            return False, ""

    def screenshot_region(self, x: int, y: int, w: int, h: int, **kw: Any) -> Tuple[bool, str]:
        t0 = time.perf_counter()
        try:
            ok, b64 = self.shots.capture_region(int(x), int(y), int(w), int(h), **kw)
            self._record("screenshot_region", ok, f"({x},{y} {w}x{h}) b64_len={len(b64)}", t0)
            return ok, b64
        except Exception as exc:
            self._record("screenshot_region", False, f"raised {exc}", t0)
            return False, ""

    def screenshot_window(self, hwnd: int, **kw: Any) -> Tuple[bool, str]:
        t0 = time.perf_counter()
        try:
            ok, b64, _rect = self.shots.capture_window(int(hwnd), **kw)
            self._record("screenshot_window", ok, f"hwnd={hwnd} b64_len={len(b64)}", t0)
            return ok, b64
        except Exception as exc:
            self._record("screenshot_window", False, f"hwnd={hwnd} raised {exc}", t0)
            return False, ""

    def register_hotkey(self, modifiers: List[str], key: str,
                        callback: Callable[[], None]) -> int:
        t0 = time.perf_counter()
        try:
            hid = self.hotkeys.register(modifiers, key, callback)
            self._record("register_hotkey", hid != 0,
                         f"{'+'.join(modifiers)}+{key} id={hid}", t0)
            return hid
        except Exception as exc:
            self._record("register_hotkey", False, f"raised {exc}", t0)
            return 0

    def unregister_hotkey(self, hid: int) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.hotkeys.unregister(int(hid))
            self._record("unregister_hotkey", ok, f"id={hid}", t0)
            return ok
        except Exception as exc:
            self._record("unregister_hotkey", False, f"id={hid} raised {exc}", t0)
            return False

    def hotkeys_start(self) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.hotkeys.start()
            self._record("hotkeys_start", ok, "", t0)
            return ok
        except Exception as exc:
            self._record("hotkeys_start", False, f"raised {exc}", t0)
            return False

    def hotkeys_stop(self) -> bool:
        t0 = time.perf_counter()
        try:
            ok = self.hotkeys.stop()
            self._record("hotkeys_stop", ok, "", t0)
            return ok
        except Exception as exc:
            self._record("hotkeys_stop", False, f"raised {exc}", t0)
            return False

    def shutdown(self) -> bool:
        t0 = time.perf_counter()
        try:
            self.hotkeys.stop()
            self._record("shutdown", True, "hotkeys stopped", t0)
            return True
        except Exception as exc:
            self._record("shutdown", False, f"raised {exc}", t0)
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    ui = UIEngine(UIEngineConfig(engine="auto", smooth_move=False))
    print("engine:", ui.engine, "dpi:", _DPI_MODE)
    print("monitors:", ui.get_monitors(refresh=True))
    print("mouse:", ui.mouse_position())
    ok, b64 = ui.screenshot()
    print("screenshot ok:", ok, "b64_len:", len(b64))
    wins = ui.list_windows()[:5]
    for w in wins:
        print(w["hwnd"], repr(w["title"])[:80])
