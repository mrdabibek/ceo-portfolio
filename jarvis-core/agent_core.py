"""PROJECT WINA (ex JARVIS-CORE) — MODULE 5: agent_core.py
Cognitive Orchestrator & Autonomous ReAct loop (Wina).

State machine + OBSERVE->PLAN->ACT->VERIFY loop + Self-Healing (3 retries)
+ Atomic Action Primitives (AAP) planner + Telegram scenario runner
+ Generic brain-planned task runner (run_generic_task).

Python 3.11+, Windows 10/11. Dependencies: stdlib only for core loop.
Optional (guarded): tools.py, brain.py, pyautogui, pygetwindow, pytesseract, Pillow.
Missing optionals degrade gracefully with explicit errors — never silent.

CLI (legacy, kept): python agent_core.py --scenario telegram [--contact NAME] [--message TEXT]
Primary CLI now lives in wina.py; this module keeps prog="agent_core" for compat.
"""
from __future__ import annotations

import argparse
import ctypes
import dataclasses
import datetime
import enum
import functools
import importlib
import logging
import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import tools as _tools_module  # type: ignore
except Exception:
    _tools_module = None


# ---------------------------------------------------------------------------
# Paths & logging
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
SHOT_DIR = BASE_DIR / "screenshots"
LOG_DIR.mkdir(parents=True, exist_ok=True)
SHOT_DIR.mkdir(parents=True, exist_ok=True)

_LOGGER_CONFIGURED = False


def setup_logging(level: str = "INFO") -> logging.Logger:
    global _LOGGER_CONFIGURED
    logger = logging.getLogger("wina.agent_core")
    if _LOGGER_CONFIGURED:
        return logger
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    fh = logging.FileHandler(LOG_DIR / f"agent_core_{ts}.log", encoding="utf-8")
    fh.setFormatter(fmt)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.propagate = False
    _LOGGER_CONFIGURED = True
    return logger


LOG = setup_logging()


def utc_stamp() -> str:
    return datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")


# ---------------------------------------------------------------------------
# Error boundary — no silent failures
# ---------------------------------------------------------------------------

def boundary(stage: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                LOG.error("BOUNDARY[%s] %s failed: %s\n%s", stage, fn.__name__, exc, traceback.format_exc())
                raise
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Enums & data models
# ---------------------------------------------------------------------------

class AgentPhase(str, enum.Enum):
    OBSERVE = "OBSERVE"
    PLAN = "PLAN"
    ACT = "ACT"
    VERIFY = "VERIFY"
    DONE = "DONE"
    FAILED = "FAILED"


class StepStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    HEALED = "HEALED"
    SKIPPED = "SKIPPED"


class FailureType(str, enum.Enum):
    NONE = "NONE"
    WINDOW_FREEZE = "WINDOW_FREEZE"
    UNRESPONSIVE_CONTROL = "UNRESPONSIVE_CONTROL"
    MODAL_POPUP = "MODAL_POPUP"
    FOCUS_THEFT = "FOCUS_THEFT"
    ELEMENT_NOT_FOUND = "ELEMENT_NOT_FOUND"
    VERIFY_MISMATCH = "VERIFY_MISMATCH"
    APP_NOT_RUNNING = "APP_NOT_RUNNING"
    TIMEOUT = "TIMEOUT"
    UNKNOWN = "UNKNOWN"


class HealingAction(str, enum.Enum):
    NONE = "NONE"
    REFOCUS = "REFOCUS"
    DISMISS_MODAL = "DISMISS_MODAL"
    WAIT_RETRY = "WAIT_RETRY"
    RESTART_APP = "RESTART_APP"
    ESCALATE = "ESCALATE"


@dataclass
class ConversationTurn:
    role: str
    content: str
    phase: str = ""
    timestamp: str = field(default_factory=lambda: datetime.datetime.now().isoformat(timespec="seconds"))

    def to_dict(self) -> Dict[str, str]:
        return dataclasses.asdict(self)


@dataclass
class ScreenState:
    foreground_title: str = ""
    foreground_handle: int = 0
    target_title: str = ""
    target_handle: int = 0
    responsive: bool = True
    screenshot_path: str = ""
    width: int = 0
    height: int = 0
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class ActionStep:
    id: str
    primitive: str
    description: str
    params: Dict[str, Any] = field(default_factory=dict)
    verify: Dict[str, Any] = field(default_factory=dict)
    timeout_s: float = 20.0
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    last_error: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = dataclasses.asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class StepResult:
    ok: bool
    step_id: str
    message: str = ""
    evidence: List[str] = field(default_factory=list)
    failure: FailureType = FailureType.NONE
    healed: bool = False


@dataclass
class ScenarioResult:
    ok: bool
    scenario: str
    steps_total: int = 0
    steps_passed: int = 0
    steps_failed: int = 0
    heals: int = 0
    evidence: List[str] = field(default_factory=list)
    error: str = ""
    duration_s: float = 0.0


@dataclass
class AgentConfig:
    target_app: str = "Telegram"
    target_exe: str = "Telegram.exe"
    max_heal_retries: int = 3
    step_timeout_s: float = 20.0
    observe_timeout_s: float = 10.0
    poll_interval_s: float = 0.8
    screenshot_on_each_step: bool = True


# ---------------------------------------------------------------------------
# Win32 helpers (stdlib ctypes, no hard deps)
# ---------------------------------------------------------------------------

class Win32:
    @staticmethod
    def foreground_title() -> Tuple[int, str]:
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            return int(hwnd), buf.value or ""
        except Exception as exc:
            LOG.warning("Win32.foreground_title failed: %s", exc)
            return 0, ""

    @staticmethod
    def find_window_by_substring(substr: str) -> Tuple[int, str]:
        found: List[Tuple[int, str]] = []
        needle = (substr or "").lower()
        try:
            user32 = ctypes.windll.user32
            EnumWindows = user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            GetWindowTextW = user32.GetWindowTextW
            GetWindowTextLengthW = user32.GetWindowTextLengthW
            IsWindowVisible = user32.IsWindowVisible

            def cb(hwnd: Any, _lp: Any) -> bool:
                try:
                    if IsWindowVisible(hwnd):
                        n = GetWindowTextLengthW(hwnd)
                        if n > 0:
                            buf = ctypes.create_unicode_buffer(n + 1)
                            GetWindowTextW(hwnd, buf, n + 1)
                            title = buf.value or ""
                            if needle in title.lower():
                                found.append((int(hwnd), title))
                except Exception:
                    pass
                return True

            EnumWindows(EnumWindowsProc(cb), 0)
        except Exception as exc:
            LOG.warning("Win32.find_window_by_substring failed: %s", exc)
        if found:
            return found[0]
        return 0, ""

    @staticmethod
    def find_window_by_exe(exe_name: str) -> Tuple[int, str]:
        """Find first visible top-level window owned by process exe_name.

        Matches by executable file name (e.g. 'Telegram.exe') instead of
        window title, since chat-app titles change per open chat.
        Returns (hwnd, title) or (0, '').
        """
        want = (exe_name or "").lower().strip()
        if not want:
            return 0, ""
        if not want.endswith(".exe"):
            want += ".exe"
        found: List[Tuple[int, str]] = []
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            psapi = ctypes.windll.psapi
            EnumWindows = user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
            GetWindowTextW = user32.GetWindowTextW
            GetWindowTextLengthW = user32.GetWindowTextLengthW
            IsWindowVisible = user32.IsWindowVisible

            def _exe_of(hwnd: int) -> str:
                pid = ctypes.c_uint(0)
                try:
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    if not pid.value:
                        return ""
                    PROCESS_QUERY_LIMITED = 0x1000
                    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid.value)
                    if not h:
                        return ""
                    try:
                        buf = ctypes.create_unicode_buffer(512)
                        # QueryFullProcessImageNameW needs buffer-size pointer
                        size = ctypes.c_uint(512)
                        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                            full = buf.value or ""
                            return full.replace("/", "\\").split("\\")[-1].lower()
                        # Fallback: psapi GetModuleBaseNameW
                        n = psapi.GetModuleBaseNameW(h, None, buf, 512)
                        if n:
                            return (buf.value or "")[:n].lower()
                    finally:
                        kernel32.CloseHandle(h)
                except Exception:
                    pass
                return ""

            def cb(hwnd: Any, _lp: Any) -> bool:
                try:
                    h = int(hwnd)
                    if IsWindowVisible(h):
                        if _exe_of(h) == want:
                            n = GetWindowTextLengthW(h)
                            buf = ctypes.create_unicode_buffer(n + 1)
                            GetWindowTextW(h, buf, n + 1)
                            found.append((h, buf.value or ""))
                            return False  # stop enumeration
                except Exception:
                    pass
                return True

            EnumWindows(EnumWindowsProc(cb), 0)
        except Exception as exc:
            LOG.warning("Win32.find_window_by_exe failed: %s", exc)
        if found:
            return found[0]
        return 0, ""

    @staticmethod
    def get_rect(hwnd: int) -> Tuple[int, int, int, int]:
        """Return (left, top, right, bottom) of a window, or (0,0,0,0)."""
        try:
            from ctypes import byref, wintypes

            class _RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            rc = _RECT()
            if ctypes.windll.user32.GetWindowRect(hwnd, byref(rc)):
                return int(rc.left), int(rc.top), int(rc.right), int(rc.bottom)
        except Exception as exc:
            LOG.warning("Win32.get_rect failed: %s", exc)
        return (0, 0, 0, 0)

    @staticmethod
    def set_foreground(hwnd: int) -> bool:
        try:
            user32 = ctypes.windll.user32
            user32.SetForegroundWindow(hwnd)
            return True
        except Exception as exc:
            LOG.warning("Win32.set_foreground failed: %s", exc)
            return False

    @staticmethod
    def is_hung(hwnd: int) -> bool:
        try:
            if hwnd <= 0:
                return True
            hung = ctypes.windll.user32.IsHungAppWindow(hwnd)
            return bool(hung)
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Tools adapter — binds to tools.py when present, else stdlib fallbacks
# ---------------------------------------------------------------------------

class ToolsAdapter:
    def __init__(self) -> None:
        self._mod = _tools_module
        self._pyautogui: Any = None
        if self._mod is not None:
            LOG.info("ToolsAdapter: bound to tools.py (%s)", getattr(self._mod, "__file__", "unknown"))
        else:
            LOG.warning("ToolsAdapter: tools.py not importable — using built-in fallbacks")
        try:
            self._pyautogui = importlib.import_module("pyautogui")
        except Exception:
            self._pyautogui = None

    def has(self, name: str) -> bool:
        return self._mod is not None and hasattr(self._mod, name) and callable(getattr(self._mod, name))

    @boundary("tools")
    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        if self.has(name):
            fn = getattr(self._mod, name)
            LOG.info("tools.%s(args=%s kwargs=%s)", name, args, kwargs)
            out = fn(*args, **kwargs)
            LOG.info("tools.%s -> %r", name, str(out)[:500])
            return out
        raise AttributeError(f"tools.py has no function '{name}' and no override matched")

    # -- high-level ops with fallback chains --------------------------------

    @boundary("tools")
    def launch_application(self, app: str) -> bool:
        for candidate in ("launch_application", "launch_app", "open_app", "start_app"):
            if self.has(candidate):
                return bool(self.call(candidate, app))
        LOG.info("Fallback launch_application: %s", app)
        try:
            if os.path.isfile(app):
                os.startfile(app)  # type: ignore[attr-defined]
                return True
        except Exception as exc:
            LOG.error("os.startfile failed for %s: %s", app, exc)
        for exe in (app, "Telegram.exe"):
            try:
                subprocess.Popen(exe, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
                return True
            except FileNotFoundError:
                continue
            except Exception as exc:
                LOG.error("subprocess.Popen(%s) failed: %s", exe, exc)
                return False
        try:
            subprocess.Popen(["cmd", "/c", "start", "", app], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception as exc:
            LOG.error("Fallback launch failed for %s: %s", app, exc)
            raise RuntimeError(f"Cannot launch application: {app}: {exc}") from exc

    @boundary("tools")
    def find_window(self, title_substring: str) -> Tuple[int, str]:
        for candidate in ("find_window", "get_window", "find_window_by_title"):
            if self.has(candidate):
                out = self.call(candidate, title_substring)
                if isinstance(out, (tuple, list)) and len(out) >= 2:
                    return int(out[0]), str(out[1])
                if isinstance(out, dict):
                    return int(out.get("handle", 0)), str(out.get("title", ""))
                if isinstance(out, str):
                    return 1 if out else 0, out
        hwnd, title = Win32.find_window_by_substring(title_substring)
        if hwnd:
            return hwnd, title
        # Title changes per chat (e.g. Telegram shows chat name) — match by exe.
        return Win32.find_window_by_exe(title_substring)

    @boundary("tools")
    def focus_window(self, hwnd_or_title: Any) -> bool:
        for candidate in ("focus_window", "activate_window", "bring_to_front"):
            if self.has(candidate):
                return bool(self.call(candidate, hwnd_or_title))
        hwnd = hwnd_or_title
        if isinstance(hwnd, str):
            hwnd, _ = self.find_window(hwnd)
        try:
            hwnd = int(hwnd)
        except Exception:
            hwnd = 0
        if hwnd <= 0:
            LOG.error("focus_window: invalid handle for %r", hwnd_or_title)
            return False
        return Win32.set_foreground(hwnd)

    @boundary("tools")
    def take_screenshot(self, path: Optional[str] = None) -> str:
        for candidate in ("take_screenshot", "screenshot", "capture_screen"):
            if self.has(candidate):
                out = self.call(candidate, path) if path else self.call(candidate)
                if isinstance(out, str) and out:
                    return out
                if isinstance(out, dict) and out.get("path"):
                    return str(out["path"])
        dest = path or str(SHOT_DIR / f"shot_{utc_stamp()}.png")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        if self._pyautogui is not None:
            img = self._pyautogui.screenshot()
            img.save(dest)
            return dest
        try:
            from PIL import ImageGrab  # type: ignore
            img = ImageGrab.grab()
            img.save(dest)
            return dest
        except Exception as exc:
            LOG.error("Screenshot fallback failed: %s", exc)
            raise RuntimeError(f"Screenshot failed (install Pillow/pyautogui): {exc}") from exc

    @boundary("tools")
    def click(self, x: Optional[int] = None, y: Optional[int] = None, selector: str = "") -> bool:
        for candidate in ("click", "mouse_click", "click_at"):
            if self.has(candidate):
                try:
                    if x is not None and y is not None:
                        return bool(self.call(candidate, x, y))
                    return bool(self.call(candidate, selector or x))
                except Exception:
                    continue
        if x is None or y is None:
            raise ValueError(f"click requires x,y (got x={x} y={y} selector={selector!r})")
        if self._pyautogui is not None:
            self._pyautogui.click(int(x), int(y))
            return True
        LOG.error("click fallback unavailable: install pyautogui (x=%s y=%s)", x, y)
        raise RuntimeError("click unavailable: pyautogui not installed and tools.py has no click")

    @boundary("tools")
    def screenshot_window(self, hwnd: int, path: Optional[str] = None) -> str:
        """Screenshot only the given window's rect (kills false-positive OCR
        from other foreground windows). Falls back to full screenshot."""
        dest = path or str(SHOT_DIR / f"win_{utc_stamp()}.png")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        try:
            l, t, r, b = Win32.get_rect(hwnd)
            if r > l and b > t:
                from PIL import ImageGrab  # type: ignore

                img = ImageGrab.grab(bbox=(l, t, r, b))
                img.save(dest)
                return dest
        except Exception as exc:
            LOG.warning("screenshot_window failed, full-shot fallback: %s", exc)
        return self.take_screenshot(path)

    @boundary("tools")
    def type_text(self, text: str, interval: float = 0.02) -> bool:
        for candidate in ("type_text", "type", "write_text", "send_keys_text"):
            if self.has(candidate):
                return bool(self.call(candidate, text))
        if self._pyautogui is not None:
            self._pyautogui.write(text, interval=interval)
            return True
        raise RuntimeError("type_text unavailable: pyautogui not installed and tools.py has no type_text")

    @boundary("tools")
    def press_key(self, key: str) -> bool:
        for candidate in ("press_key", "press", "hotkey", "key_press"):
            if self.has(candidate):
                return bool(self.call(candidate, key))
        if self._pyautogui is not None:
            normalized = key.lower().replace(" ", "")
            mapping = {"enter": "enter", "esc": "esc", "escape": "esc", "ctrl+f": None, "ctrl+a": None}
            if normalized in ("ctrl+f", "ctrl+a"):
                combo = normalized.split("+")
                self._pyautogui.hotkey(*combo)
                return True
            self._pyautogui.press(mapping.get(normalized, key))
            return True
        raise RuntimeError("press_key unavailable: pyautogui not installed and tools.py has no press_key")

    @boundary("tools")
    def hotkey(self, *keys: str) -> bool:
        if self.has("hotkey"):
            return bool(self.call("hotkey", *keys))
        if self._pyautogui is not None:
            self._pyautogui.hotkey(*keys)
            return True
        raise RuntimeError("hotkey unavailable: pyautogui not installed")

    @boundary("tools")
    def wait(self, seconds: float) -> None:
        if self.has("wait"):
            self.call("wait", seconds)
            return
        if self.has("sleep"):
            self.call("sleep", seconds)
            return
        time.sleep(max(0.0, float(seconds)))

    @boundary("tools")
    def ocr_text(self, image_path: str) -> str:
        for candidate in ("ocr_text", "ocr", "read_text", "extract_text"):
            if self.has(candidate):
                out = self.call(candidate, image_path)
                return str(out or "")
        try:
            mod = importlib.import_module("pytesseract")
            # Ensure tesseract binary is reachable (winget default location).
            try:
                import shutil
                if shutil.which("tesseract") is None:
                    for cand in (
                        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
                    ):
                        if os.path.exists(cand):
                            mod.pytesseract.tesseract_cmd = cand
                            break
            except Exception:
                pass
            pil = importlib.import_module("PIL.Image")
            img = pil.open(image_path)
            return str(mod.image_to_string(img))
        except Exception as exc:
            LOG.warning("OCR unavailable for %s: %s", image_path, exc)
            return ""

    @boundary("tools")
    def get_chat_history(self, contact: str = "", limit: int = 20) -> str:
        for candidate in ("get_chat_history", "read_chat_history", "get_last_messages", "get_messages"):
            if self.has(candidate):
                try:
                    out = self.call(candidate, contact, limit)
                except TypeError:
                    out = self.call(candidate, contact)
                if isinstance(out, list):
                    return "\n".join(str(m) for m in out)
                return str(out or "")
        return ""

    @boundary("tools")
    def is_responsive(self, hwnd: int) -> bool:
        if self.has("is_window_responsive"):
            return bool(self.call("is_window_responsive", hwnd))
        if self.has("is_responsive"):
            return bool(self.call("is_responsive", hwnd))
        if hwnd and Win32.is_hung(hwnd):
            return False
        return True

    @boundary("tools")
    def kill_process(self, exe_name: str) -> bool:
        if self.has("kill_process"):
            return bool(self.call("kill_process", exe_name))
        try:
            subprocess.run(["taskkill", "/F", "/IM", exe_name], capture_output=True, timeout=15)
            return True
        except Exception as exc:
            LOG.error("kill_process(%s) failed: %s", exe_name, exc)
            return False

    @staticmethod
    def _reject_destructive_terminal_command(command: str) -> None:
        cmd = (command or "")
        if not cmd.strip():
            raise ValueError("execute_terminal requires non-empty 'command'")
        norm = " ".join(cmd.lower().split())
        blocked = (
            "rm -rf /",
            "rm -fr /",
            "rm --no-preserve-root",
            ":(){",
            "mkfs",
            "dd if=",
            "format c:",
            "format d:",
            "diskpart",
            "del /f /s /q c:",
            "rd /s /q c:",
            "del /s /q c:\\windows",
            "rd /s /q c:\\windows",
            ">\\\\.\\physicaldrive",
        )
        for pat in blocked:
            if pat in norm:
                raise RuntimeError(f"execute_terminal blocked destructive pattern '{pat}' in command: {cmd[:200]!r}")
        if "remove-item" in norm and "c:\\" in norm and ("-recurse" in norm or "-r " in norm or norm.rstrip().endswith("-r")):
            raise RuntimeError(f"execute_terminal blocked destructive Remove-Item on C:\\: {cmd[:200]!r}")

    @boundary("tools")
    def execute_terminal(self, command: str, timeout_s: float = 60.0) -> str:
        """Run a shell command. Prefers tools.execute_terminal_command, else subprocess fallback."""
        if self.has("execute_terminal_command"):
            try:
                out = self.call("execute_terminal_command", command, timeout_s)
            except TypeError:
                out = self.call("execute_terminal_command", command)
            if isinstance(out, dict):
                rc = int(out.get("returncode", out.get("return_code", 0)))
                text = str(out.get("stdout", "") or "") + str(out.get("stderr", "") or "")
                if not text:
                    text = str(out)
                if rc != 0:
                    raise RuntimeError(f"execute_terminal_command failed rc={rc}: {text[:2000]}")
                return text
            if isinstance(out, (tuple, list)) and len(out) >= 2:
                rc = int(out[0])
                text = str(out[1])
                if rc != 0:
                    raise RuntimeError(f"execute_terminal_command failed rc={rc}: {text[:2000]}")
                return text
            return str(out or "")
        self._reject_destructive_terminal_command(command)
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=max(1.0, float(timeout_s)),
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"execute_terminal timeout after {timeout_s}s: {command[:200]!r}") from exc
        combined = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            raise RuntimeError(f"execute_terminal failed rc={proc.returncode}: {combined[:2000]}")
        return combined

    @boundary("tools")
    def read_file(self, path: str, max_chars: int = 20000) -> str:
        """Read text file. Prefers tools.read_file, else stdlib fallback."""
        if self.has("read_file"):
            try:
                out = self.call("read_file", path, max_chars)
            except TypeError:
                out = self.call("read_file", path)
            if isinstance(out, dict):
                if not out.get("success", out.get("ok", True)):
                    raise RuntimeError(f"read_file failed: {str(out)[:2000]}")
                for k in ("content", "text", "data"):
                    if out.get(k) is not None:
                        return str(out[k])[: int(max_chars)]
                return str(out)[: int(max_chars)]
            text = str(out or "")
            if not text:
                raise RuntimeError(f"read_file empty/missing: {path!r}")
            return text[: int(max_chars)]
        p = Path(str(path))
        if not p.is_file():
            raise RuntimeError(f"read_file not found: {path!r}")
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            raise RuntimeError(f"read_file failed for {path!r}: {exc}") from exc
        return text[: int(max_chars)]

    @boundary("tools")
    def write_file(self, path: str, content: str = "", append: bool = False) -> str:
        """Write/append text file. Prefers tools.write_file, else stdlib fallback."""
        if self.has("write_file"):
            try:
                out = self.call("write_file", path, content, append)
            except TypeError:
                try:
                    out = self.call("write_file", path, content)
                except TypeError:
                    out = self.call("write_file", {"path": path, "content": content, "append": bool(append)})
            if isinstance(out, dict):
                if not out.get("success", out.get("ok", True)):
                    raise RuntimeError(f"write_file failed: {str(out)[:2000]}")
                return str(out.get("path", path))
            if isinstance(out, bool):
                if not out:
                    raise RuntimeError(f"write_file reported failure: {path!r}")
                return str(path)
            return str(out or path)
        p = Path(str(path))
        if not str(path).strip():
            raise RuntimeError("write_file requires non-empty 'path'")
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with open(p, mode, encoding="utf-8") as fh:
                fh.write(str(content or ""))
            return str(p)
        except Exception as exc:
            raise RuntimeError(f"write_file failed for {path!r}: {exc}") from exc

    @boundary("tools")
    def list_dir(self, path: str = ".", pattern: str = "*") -> str:
        """List directory. Prefers tools.list_dir, else stdlib fallback."""
        if self.has("list_dir"):
            try:
                out = self.call("list_dir", path, pattern)
            except TypeError:
                out = self.call("list_dir", path)
            if isinstance(out, dict):
                if not out.get("success", out.get("ok", True)) and not out.get("files") and not out.get("entries"):
                    raise RuntimeError(f"list_dir failed: {str(out)[:2000]}")
                items = out.get("files", out.get("entries", out))
                if isinstance(items, list):
                    return "\n".join(str(x) for x in items)
                return str(out)[:8000]
            if isinstance(out, list):
                if not out:
                    raise RuntimeError(f"list_dir empty: {path!r}")
                return "\n".join(str(x) for x in out)
            text = str(out or "")
            if not text:
                raise RuntimeError(f"list_dir empty: {path!r}")
            return text
        import fnmatch as _fnmatch

        p = Path(str(path or "."))
        if not p.is_dir():
            raise RuntimeError(f"list_dir not a directory: {path!r}")
        try:
            names = sorted(os.listdir(p))
        except Exception as exc:
            raise RuntimeError(f"list_dir failed for {path!r}: {exc}") from exc
        pat = str(pattern or "*")
        kept = [n for n in names if _fnmatch.fnmatch(n, pat)]
        if not kept:
            raise RuntimeError(f"list_dir no match: dir={path!r} pattern={pat!r}")
        return "\n".join(kept)

    @boundary("tools")
    def fetch_url(self, url: str, timeout_s: float = 20.0) -> str:
        """Fetch URL text. Prefers tools.fetch_url, else urllib fallback."""
        if self.has("fetch_url"):
            try:
                out = self.call("fetch_url", url, timeout_s)
            except TypeError:
                out = self.call("fetch_url", url)
            if isinstance(out, dict):
                if not out.get("success", out.get("ok", True)):
                    raise RuntimeError(f"fetch_url failed: {str(out)[:2000]}")
                for k in ("content", "text", "body", "data"):
                    if out.get(k):
                        return str(out[k])
                return str(out)[:20000]
            text = str(out or "")
            if not text:
                raise RuntimeError(f"fetch_url empty response: {url!r}")
            return text
        u = str(url or "").strip()
        if not (u.startswith("http://") or u.startswith("https://")):
            raise RuntimeError(f"fetch_url unsupported URL (http/https only): {url!r}")
        try:
            import urllib.request as _urlreq

            req = _urlreq.Request(u, headers={"User-Agent": "Wina/1.0"})
            with _urlreq.urlopen(req, timeout=max(1.0, float(timeout_s))) as resp:
                raw = resp.read()
                if not raw:
                    raise RuntimeError(f"fetch_url empty response: {url!r}")
                try:
                    return raw.decode("utf-8", errors="replace")[:20000]
                except Exception:
                    return str(raw)[:20000]
        except Exception as exc:
            raise RuntimeError(f"fetch_url failed for {url!r}: {exc}") from exc

    @boundary("tools")
    def launch_or_focus(self, app_name: str = "", executable_path: str = "",
                        window_title_pattern: str = "") -> str:
        """Launch-or-focus app. Prefers tools.launch_or_focus, else fallback chain."""
        if self.has("launch_or_focus"):
            try:
                out = self.call("launch_or_focus", app_name, executable_path, window_title_pattern)
            except TypeError:
                out = self.call("launch_or_focus", {"app_name": app_name, "executable_path": executable_path,
                                                   "window_title_pattern": window_title_pattern})
            if isinstance(out, dict):
                ok = out.get("ok", out.get("success", out.get("focused", out.get("launched"))))
                if ok in (None,):
                    ok = True if out else False
                if not ok:
                    raise RuntimeError(f"launch_or_focus failed: {str(out)[:2000]}")
                return str(out.get("detail", out.get("method", out)) or "ok")
            if isinstance(out, bool):
                if not out:
                    raise RuntimeError(f"launch_or_focus reported failure: {app_name!r}")
                return "ok"
            text = str(out or "")
            if not text:
                raise RuntimeError(f"launch_or_focus empty result: {app_name!r}")
            return text
        pattern = str(window_title_pattern or app_name)
        if pattern:
            hwnd, _ = self.find_window(pattern)
            if hwnd and self.focus_window(hwnd):
                return f"focused existing window pattern={pattern!r}"
        target = str(executable_path or app_name)
        if not target.strip():
            raise RuntimeError("launch_or_focus requires app_name or executable_path")
        if not self.launch_application(target):
            raise RuntimeError(f"launch_or_focus launch failed: {target!r}")
        self.wait(1.5)
        if pattern:
            hwnd, title = self.find_window(pattern)
            if hwnd:
                self.focus_window(hwnd)
                return f"launched and focused: {title!r}"
        return f"launched: {target!r}"


TOOLS = ToolsAdapter()


# ---------------------------------------------------------------------------
# State machine: history + screen state + execution stack
# ---------------------------------------------------------------------------

class StateMachine:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.history: List[ConversationTurn] = []
        self.screen = ScreenState(target_title=config.target_app)
        self.stack: List[ActionStep] = []
        self.phase: AgentPhase = AgentPhase.OBSERVE
        self.heal_count_total: int = 0

    @boundary("state")
    def say(self, role: str, content: str, phase: str = "") -> None:
        turn = ConversationTurn(role=role, content=content, phase=phase or self.phase.value)
        self.history.append(turn)
        LOG.info("[%s][%s] %s", turn.phase, role, content)

    @boundary("state")
    def push(self, step: ActionStep) -> None:
        self.stack.append(step)
        self.say("system", f"PUSH {step.id}: {step.description}", AgentPhase.PLAN.value)

    @boundary("state")
    def pop(self) -> Optional[ActionStep]:
        if not self.stack:
            return None
        step = self.stack.pop()
        self.say("system", f"POP {step.id} [{step.status.value}]", self.phase.value)
        return step

    @boundary("state")
    def peek(self) -> Optional[ActionStep]:
        return self.stack[-1] if self.stack else None

    @boundary("state")
    def set_phase(self, phase: AgentPhase) -> None:
        LOG.info("Phase %s -> %s", self.phase.value, phase.value)
        self.phase = phase

    @boundary("state")
    def update_screen(self, screen: ScreenState) -> None:
        self.screen = screen

    def transcript(self) -> List[Dict[str, str]]:
        return [t.to_dict() for t in self.history]


# ---------------------------------------------------------------------------
# AAP planner — intent -> atomic sequential steps
# ---------------------------------------------------------------------------

class AAPPlanner:
    PRIMITIVES = frozenset({
        "launch_app", "wait_window", "focus_window", "screenshot",
        "hotkey", "press_key", "click", "type_text", "wait",
        "verify_window", "verify_ocr_contains", "verify_chat_contains",
        "execute_terminal",
        "read_file", "write_file", "list_dir", "fetch_url", "launch_or_focus",
    })

    def __init__(self, state: StateMachine) -> None:
        self.state = state

    @boundary("plan")
    def validate(self, steps: List[ActionStep]) -> List[ActionStep]:
        for s in steps:
            if s.primitive not in self.PRIMITIVES:
                raise ValueError(f"Unknown primitive '{s.primitive}' in step {s.id}")
            if not s.id or not s.description:
                raise ValueError(f"Step missing id/description: {s!r}")
        return steps

    @boundary("plan")
    def plan_generic(self, steps_data: List[Dict[str, Any]]) -> List[ActionStep]:
        """Convert brain.plan_task dicts to validated ActionSteps.

        Expected keys per dict: id / primitive / description / params / verify / timeout_s.
        """
        if not isinstance(steps_data, list) or not steps_data:
            raise ValueError("plan_generic requires a non-empty list of step dicts")
        steps: List[ActionStep] = []
        for i, raw in enumerate(steps_data):
            if not isinstance(raw, dict):
                raise ValueError(f"plan_generic step #{i} must be a dict, got {type(raw).__name__}")
            sid = str(raw.get("id", f"g-{i + 1:02d}"))
            primitive = str(raw.get("primitive", ""))
            description = str(raw.get("description", primitive or sid))
            params = raw.get("params", {})
            verify = raw.get("verify", {})
            timeout_s = float(raw.get("timeout_s", 20.0))
            if not isinstance(params, dict):
                raise ValueError(f"plan_generic step {sid!r}: 'params' must be a dict")
            # LLM sometimes puts params flat (path/content/url next to
            # primitive) — merge them so nothing is silently dropped.
            for k, v in raw.items():
                if k not in params and k not in ("id", "primitive", "description", "params", "verify", "timeout_s"):
                    params[k] = v
            if not isinstance(verify, dict):
                raise ValueError(f"plan_generic step {sid!r}: 'verify' must be a dict")
            steps.append(ActionStep(sid, primitive, description, params, verify, timeout_s))
        self.validate(steps)
        self.state.say("planner", f"Planned {len(steps)} generic AAP steps from brain.plan_task")
        return steps

    @boundary("plan")
    def plan_telegram_send(self, contact: str, message: str) -> List[ActionStep]:
        contact = (contact or "").strip()
        message = (message or "").strip()
        if not contact:
            raise ValueError("contact must be non-empty")
        if not message:
            raise ValueError("message must be non-empty")
        cfg = self.state.config
        steps = [
            ActionStep("tg-01", "launch_app", f"Launch Telegram Desktop ({cfg.target_exe})",
                       {"app": cfg.target_exe}, {"window": "Telegram"}, 30.0),
            ActionStep("tg-02", "wait_window", "Wait for Telegram main window",
                       {"title_substring": "Telegram", "timeout_s": 30.0}, {"window": "Telegram"}, 30.0),
            ActionStep("tg-03", "focus_window", "Focus Telegram window",
                       {"title_substring": "Telegram"}, {"focused": "Telegram"}, 15.0),
            ActionStep("tg-04", "screenshot", "Baseline screenshot of Telegram",
                       {"label": "baseline"}, {}, 10.0),
            ActionStep("tg-05", "hotkey", "Focus search via Ctrl+F",
                       {"keys": ["ctrl", "f"]}, {}, 10.0),
            ActionStep("tg-06", "type_text", f"Type contact name '{contact}' in search",
                       {"text": contact}, {}, 10.0),
            ActionStep("tg-07", "wait", "Wait for search results",
                       {"seconds": 2.0}, {}, 10.0),
            ActionStep("tg-08", "press_key", "Open top search result with Enter",
                       {"key": "enter"}, {}, 10.0),
            ActionStep("tg-09", "wait", "Wait for chat to open",
                       {"seconds": 2.0}, {}, 10.0),
            ActionStep("tg-10", "verify_ocr_contains", f"Verify chat header shows '{contact}'",
                       {}, {"contains": contact, "retries": 3}, 20.0),
            ActionStep("tg-11", "type_text", "Type message body",
                       {"text": message}, {}, 15.0),
            ActionStep("tg-12", "press_key", "Send message with Enter",
                       {"key": "enter"}, {}, 10.0),
            ActionStep("tg-13", "wait", "Wait for message delivery",
                       {"seconds": 2.5}, {}, 10.0),
            ActionStep("tg-14", "verify_chat_contains", "Verify message appears in chat history",
                       {"contact": contact}, {"contains": message, "retries": 4}, 30.0),
            ActionStep("tg-15", "screenshot", "Final evidence screenshot",
                       {"label": "sent_ok"}, {}, 10.0),
        ]
        self.validate(steps)
        self.state.say("planner", f"Planned {len(steps)} AAP steps for telegram_send contact={contact!r}")
        return steps

    @boundary("plan")
    def plan(self, intent: str, params: Dict[str, Any]) -> List[ActionStep]:
        intent = (intent or "").strip().lower()
        if intent in ("telegram_send", "telegram", "send_telegram"):
            return self.plan_telegram_send(str(params.get("contact", "")), str(params.get("message", "")))
        raise ValueError(f"Unknown intent '{intent}'")


# ---------------------------------------------------------------------------
# Self-healing — classify + recover (max 3) before escalated failure
# ---------------------------------------------------------------------------

class SelfHealer:
    def __init__(self, state: StateMachine, tools: ToolsAdapter, config: AgentConfig) -> None:
        self.state = state
        self.tools = tools
        self.config = config

    @boundary("heal")
    def classify(self, error_text: str, screen: ScreenState, step: ActionStep) -> FailureType:
        t = (error_text or "").lower()
        fg = (screen.foreground_title or "").lower()
        if "hung" in t or "freeze" in t or "frozen" in t or "not responding" in t:
            return FailureType.WINDOW_FREEZE
        if "modal" in t or "dialog" in t or "popup" in t:
            return FailureType.MODAL_POPUP
        if "focus" in t or (screen.target_title and screen.target_title.lower() not in fg and fg):
            if step.primitive in ("type_text", "press_key", "hotkey", "click"):
                return FailureType.FOCUS_THEFT
        if "not found" in t or "no such window" in t or "element" in t:
            if step.primitive in ("click", "type_text"):
                return FailureType.UNRESPONSIVE_CONTROL
            return FailureType.ELEMENT_NOT_FOUND
        if "verify" in t or "mismatch" in t or "expected" in t:
            return FailureType.VERIFY_MISMATCH
        if "launch" in t or "not running" in t or "no window" in t:
            return FailureType.APP_NOT_RUNNING
        if "timeout" in t or "timed out" in t:
            return FailureType.TIMEOUT
        return FailureType.UNKNOWN

    @boundary("heal")
    def detect_live(self, step: ActionStep) -> FailureType:
        try:
            hwnd, fg = Win32.foreground_title()
            self.state.screen.foreground_title = fg
            self.state.screen.foreground_handle = hwnd
            tgt_hwnd, _ = self.tools.find_window(self.config.target_app)
            if tgt_hwnd and Win32.is_hung(tgt_hwnd):
                return FailureType.WINDOW_FREEZE
            if tgt_hwnd and not self.tools.is_responsive(tgt_hwnd):
                return FailureType.WINDOW_FREEZE
            low = fg.lower()
            modals = ("update", "error", "warning", "confirm", "alert", "login", "sign in", "proxy", "connection")
            if tgt_hwnd and fg and self.config.target_app.lower() not in low and any(m in low for m in modals):
                return FailureType.MODAL_POPUP
            if step.primitive in ("type_text", "press_key", "hotkey") and tgt_hwnd and hwnd and hwnd != tgt_hwnd:
                if self.config.target_app.lower() not in low:
                    return FailureType.FOCUS_THEFT
        except Exception as exc:
            LOG.warning("detect_live failed: %s", exc)
        return FailureType.NONE

    @boundary("heal")
    def recover(self, failure: FailureType, step: ActionStep, attempt: int) -> Tuple[bool, HealingAction]:
        LOG.warning("Healing %s (failure=%s attempt=%d/%d)", step.id, failure.value, attempt, self.config.max_heal_retries)
        self.state.say("healer", f"Recover {step.id}: {failure.value} attempt {attempt}")
        try:
            if failure == FailureType.FOCUS_THEFT:
                ok = self.tools.focus_window(self.config.target_app)
                self.tools.wait(1.0)
                return ok, HealingAction.REFOCUS if ok else HealingAction.ESCALATE
            if failure == FailureType.MODAL_POPUP:
                try:
                    self.tools.press_key("esc")
                    self.tools.wait(1.0)
                except Exception as exc:
                    LOG.warning("dismiss modal ESC failed: %s", exc)
                ok = self.tools.focus_window(self.config.target_app)
                return ok, HealingAction.DISMISS_MODAL if ok else HealingAction.ESCALATE
            if failure in (FailureType.WINDOW_FREEZE, FailureType.UNRESPONSIVE_CONTROL, FailureType.TIMEOUT):
                if attempt < self.config.max_heal_retries:
                    self.tools.wait(2.0)
                    self.tools.focus_window(self.config.target_app)
                    self.tools.wait(1.0)
                    return True, HealingAction.WAIT_RETRY
                self.tools.kill_process(self.config.target_exe)
                self.tools.wait(2.0)
                self.tools.launch_application(self.config.target_exe)
                self.tools.wait(4.0)
                return True, HealingAction.RESTART_APP
            if failure in (FailureType.APP_NOT_RUNNING, FailureType.ELEMENT_NOT_FOUND):
                if attempt <= 2:
                    self.tools.launch_application(self.config.target_exe)
                    self.tools.wait(4.0)
                    self.tools.focus_window(self.config.target_app)
                    return True, HealingAction.RESTART_APP
                return False, HealingAction.ESCALATE
            if failure == FailureType.VERIFY_MISMATCH:
                shot = self.tools.take_screenshot()
                step.evidence.append(shot)
                self.tools.wait(2.0)
                self.tools.focus_window(self.config.target_app)
                return True, HealingAction.WAIT_RETRY
            self.tools.wait(1.5)
            return True, HealingAction.WAIT_RETRY
        except Exception as exc:
            LOG.error("recover crashed: %s", exc)
            return False, HealingAction.ESCALATE


# ---------------------------------------------------------------------------
# ReAct orchestrator: OBSERVE -> PLAN -> ACT -> VERIFY
# ---------------------------------------------------------------------------

class ReActOrchestrator:
    def __init__(self, config: Optional[AgentConfig] = None) -> None:
        self.config = config or AgentConfig()
        self.state = StateMachine(self.config)
        self.planner = AAPPlanner(self.state)
        self.healer = SelfHealer(self.state, TOOLS, self.config)
        self.tools = TOOLS

    # -- OBSERVE ------------------------------------------------------------
    @boundary("observe")
    def observe(self, step: Optional[ActionStep] = None) -> ScreenState:
        self.state.set_phase(AgentPhase.OBSERVE)
        hwnd_fg, fg_title = Win32.foreground_title()
        tgt_hwnd, tgt_title = self.tools.find_window(self.config.target_app)
        responsive = True
        if tgt_hwnd:
            try:
                responsive = self.tools.is_responsive(tgt_hwnd)
            except Exception:
                responsive = not Win32.is_hung(tgt_hwnd)
        shot = ""
        try:
            if self.config.screenshot_on_each_step:
                shot = self.tools.take_screenshot()
                if step is not None:
                    step.evidence.append(shot)
        except Exception as exc:
            LOG.error("observe screenshot failed: %s", exc)
        screen = ScreenState(
            foreground_title=fg_title, foreground_handle=hwnd_fg,
            target_title=tgt_title or self.config.target_app, target_handle=tgt_hwnd,
            responsive=responsive, screenshot_path=shot,
            updated_at=datetime.datetime.now().isoformat(timespec="seconds"),
        )
        self.state.update_screen(screen)
        self.state.say("observer", f"fg={fg_title!r} target={tgt_title!r} hwnd={tgt_hwnd} responsive={responsive} shot={shot}")
        return screen

    # -- ACT primitives -----------------------------------------------------
    @boundary("act")
    def act_primitive(self, step: ActionStep) -> str:
        self.state.set_phase(AgentPhase.ACT)
        p, q = step.primitive, step.params
        LOG.info("ACT %s primitive=%s params=%s", step.id, p, q)
        if p == "launch_app":
            ok = self.tools.launch_application(str(q.get("app", self.config.target_exe)))
            if not ok:
                raise RuntimeError("launch_app reported failure")
            self.tools.wait(3.0)
            return f"launched {q.get('app')}"
        if p == "wait_window":
            handle, title = self.wait_for_window(str(q.get("title_substring", "Telegram")), float(q.get("timeout_s", 30.0)))
            return f"window present: {title} ({handle})"
        if p == "focus_window":
            ok = self.tools.focus_window(str(q.get("title_substring", self.config.target_app)))
            if not ok:
                raise RuntimeError(f"focus theft: cannot focus {q.get('title_substring')}")
            self.tools.wait(0.8)
            return "focused"
        if p == "screenshot":
            shot = self.tools.take_screenshot()
            step.evidence.append(shot)
            return f"screenshot {shot}"
        if p == "hotkey":
            keys = q.get("keys", [])
            if not isinstance(keys, (list, tuple)) or not keys:
                raise ValueError("hotkey requires non-empty keys list")
            if not self.tools.hotkey(*[str(k) for k in keys]):
                raise RuntimeError(f"hotkey {keys} failed")
            self.tools.wait(0.6)
            return f"hotkey {'+'.join(keys)}"
        if p == "press_key":
            if not self.tools.press_key(str(q.get("key", "enter"))):
                raise RuntimeError(f"press_key {q.get('key')} failed (unresponsive control?)")
            self.tools.wait(0.6)
            return f"pressed {q.get('key')}"
        if p == "click":
            if not self.tools.click(x=q.get("x"), y=q.get("y"), selector=str(q.get("selector", ""))):
                raise RuntimeError("click failed (unresponsive control?)")
            return "clicked"
        if p == "type_text":
            text = str(q.get("text", ""))
            if not text:
                raise ValueError("type_text requires non-empty text")
            if not self.tools.type_text(text):
                raise RuntimeError("type_text failed (unresponsive control?)")
            self.tools.wait(0.6)
            return f"typed {len(text)} chars"
        if p == "wait":
            self.tools.wait(float(q.get("seconds", 1.0)))
            return f"waited {q.get('seconds')}s"
        if p == "execute_terminal":
            command = str(q.get("command", ""))
            timeout_s = float(q.get("timeout_s", 60.0))
            output = self.tools.execute_terminal(command, timeout_s)
            step.evidence.append(f"terminal:{command[:120]}")
            LOG.info("execute_terminal output (%d chars)", len(output))
            return output[-4000:] if len(output) > 4000 else output
        if p == "read_file":
            path = str(q.get("path", ""))
            max_chars = int(q.get("max_chars", 20000))
            text = self.tools.read_file(path, max_chars)
            LOG.info("read_file %r (%d chars) preview: %.200s", path, len(text), text)
            return text[-4000:] if len(text) > 4000 else text
        if p == "write_file":
            path = str(q.get("path", ""))
            content = str(q.get("content", ""))
            append = bool(q.get("append", False))
            dest = self.tools.write_file(path, content, append)
            LOG.info("write_file %r -> %r (%d chars, append=%s)", path, dest, len(content), append)
            return f"wrote {len(content)} chars -> {dest}"
        if p == "list_dir":
            path = str(q.get("path", "."))
            pattern = str(q.get("pattern", "*"))
            listing = self.tools.list_dir(path, pattern)
            LOG.info("list_dir %r pattern=%r (%d chars)", path, pattern, len(listing))
            return listing[-4000:] if len(listing) > 4000 else listing
        if p == "fetch_url":
            url = str(q.get("url", ""))
            timeout_s = float(q.get("timeout_s", q.get("timeout", 20.0)))
            body = self.tools.fetch_url(url, timeout_s)
            LOG.info("fetch_url %r (%d chars)", url, len(body))
            return body[-4000:] if len(body) > 4000 else body
        if p == "launch_or_focus":
            res = self.tools.launch_or_focus(
                str(q.get("app_name", "")),
                str(q.get("executable_path", "")),
                str(q.get("window_title_pattern", "")),
            )
            self.tools.wait(0.8)
            return res
        if p in ("verify_window", "verify_ocr_contains", "verify_chat_contains"):
            return self.verify_primitive(step)
        raise ValueError(f"Unknown primitive '{p}'")

    @boundary("act")
    def wait_for_window(self, substr: str, timeout_s: float) -> Tuple[int, str]:
        deadline = time.time() + max(1.0, timeout_s)
        last: Tuple[int, str] = (0, "")
        while time.time() < deadline:
            last = self.tools.find_window(substr)
            if last[0]:
                self.tools.focus_window(substr)
                return last
            self.tools.wait(self.config.poll_interval_s)
        raise TimeoutError(f"timeout waiting for window '{substr}' (last={last})")

    # -- VERIFY -------------------------------------------------------------
    @boundary("verify")
    def verify_primitive(self, step: ActionStep) -> str:
        p, v = step.primitive, step.verify
        if p == "verify_window":
            want = str(v.get("window", self.config.target_app))
            hwnd, title = self.tools.find_window(want)
            if not hwnd:
                raise RuntimeError(f"verify mismatch: window '{want}' not found")
            return f"window ok: {title}"
        if p == "verify_ocr_contains":
            want = str(v.get("contains", ""))
            retries = int(v.get("retries", 3))
            return self.verify_ocr_contains(want, retries, step)
        if p == "verify_chat_contains":
            want = str(v.get("contains", ""))
            contact = str(step.params.get("contact", ""))
            retries = int(v.get("retries", 4))
            return self.verify_chat_contains(want, contact, retries, step)
        if p in ("launch_app", "wait_window", "focus_window"):
            want = str(v.get("window", "") or v.get("focused", ""))
            if want:
                hwnd, _ = self.tools.find_window(want)
                if not hwnd:
                    raise RuntimeError(f"verify mismatch: expected window '{want}' not present")
                return f"window '{want}' present"
        return "no postcondition"

    @boundary("verify")
    def verify_ocr_contains(self, want: str, retries: int, step: ActionStep) -> str:
        if not want:
            return "empty expectation — skipped"
        for i in range(max(1, retries)):
            hwnd = self.state.screen.target_handle or 0
            shot = self.tools.screenshot_window(hwnd) if hwnd else self.tools.take_screenshot()
            step.evidence.append(shot)
            text = self.tools.ocr_text(shot)
            if want.lower() in (text or "").lower():
                return f"ocr matched '{want}' in target window (try {i + 1})"
            hist = self.tools.get_chat_history(want)
            if want.lower() in (hist or "").lower():
                return f"chat-history matched '{want}' (try {i + 1})"
            LOG.warning("verify_ocr_contains retry %d/%d: '%s' not seen", i + 1, retries, want)
            self.tools.wait(2.0)
        raise RuntimeError(f"verify mismatch: expected text '{want}' not found on screen after {retries} tries")

    @boundary("verify")
    def verify_chat_contains(self, want: str, contact: str, retries: int, step: ActionStep) -> str:
        if not want:
            return "empty expectation — skipped"
        for i in range(max(1, retries)):
            hwnd = self.state.screen.target_handle or 0
            shot = self.tools.screenshot_window(hwnd) if hwnd else self.tools.take_screenshot()
            step.evidence.append(shot)
            hist = self.tools.get_chat_history(contact)
            if want.lower() in (hist or "").lower():
                return f"chat history matched for contact={contact!r} (try {i + 1})"
            ocr = self.tools.ocr_text(shot)
            if want.lower() in (ocr or "").lower():
                return f"ocr matched sent message in target window (try {i + 1})"
            LOG.warning("verify_chat_contains retry %d/%d for %r", i + 1, retries, want)
            self.tools.wait(2.5)
        raise RuntimeError(
            f"verify mismatch: message '{want}' not confirmed in chat history for '{contact}' after {retries} tries"
        )

    @boundary("react")
    def verify_step_postcondition(self, step: ActionStep) -> str:
        self.state.set_phase(AgentPhase.VERIFY)
        if not step.verify:
            return "no postcondition"
        return self.verify_primitive(step)

    # -- full O-P-A-V for one step (with self-heal) -------------------------
    @boundary("react")
    def run_step(self, step: ActionStep) -> StepResult:
        step.status = StepStatus.RUNNING
        self.state.say("agent", f"STEP {step.id}: {step.description} [{step.primitive}]")
        screen = self.observe(step)
        live = self.healer.detect_live(step)
        if live == FailureType.WINDOW_FREEZE:
            LOG.warning("Pre-ACT freeze detected before %s", step.id)
        attempt = 0
        while True:
            attempt += 1
            step.attempts = attempt
            try:
                # Pre-ACT focus guard: keystrokes/clicks must land in the
                # target window, never in whatever the user has foreground.
                if step.primitive in ("type_text", "press_key", "hotkey", "click"):
                    tgt = self.state.screen.target_handle or 0
                    if tgt and Win32.foreground_title()[0] != tgt:
                        LOG.warning("STEP %s: foreground lost, refocusing target hwnd=%s", step.id, tgt)
                        self.tools.focus_window(self.config.target_app)
                        self.tools.wait(0.8)
                        if Win32.foreground_title()[0] != tgt:
                            raise RuntimeError(
                                f"focus theft: target window hwnd={tgt} is not foreground "
                                "(user may be using the machine) — refusing to type/click blindly"
                            )
                self.act_primitive(step)
                msg = self.verify_step_postcondition(step)
                step.status = StepStatus.HEALED if attempt > 1 else StepStatus.PASSED
                self.state.say("agent", f"STEP {step.id} PASSED: {msg}" + (f" after {attempt} attempts" if attempt > 1 else ""))
                return StepResult(True, step.id, msg, list(step.evidence), FailureType.NONE, healed=attempt > 1)
            except Exception as exc:
                err = f"{type(exc).__name__}: {exc}"
                step.last_error = err
                LOG.error("STEP %s attempt %d failed: %s", step.id, attempt, err)
                failure = self.healer.classify(err, self.state.screen, step)
                self.state.say("agent", f"STEP {step.id} FAILED [{failure.value}]: {err}")
                if attempt > self.config.max_heal_retries:
                    try:
                        shot = self.tools.take_screenshot()
                        step.evidence.append(shot)
                    except Exception:
                        pass
                    step.status = StepStatus.FAILED
                    return StepResult(False, step.id, err, list(step.evidence), failure, healed=False)
                ok, action = self.healer.recover(failure, step, attempt)
                self.state.heal_count_total += 1
                self.state.say("healer", f"heal action={action.value} ok={ok}")
                if not ok and action == HealingAction.ESCALATE:
                    step.status = StepStatus.FAILED
                    return StepResult(False, step.id, f"{err} (escalated after heal {action.value})",
                                      list(step.evidence), failure, healed=False)
                screen = self.observe(step)

    @boundary("react")
    def run_plan(self, steps: List[ActionStep]) -> ScenarioResult:
        t0 = time.time()
        for s in steps:
            self.state.push(s)
        passed, failed, heals = 0, 0, 0
        evidence: List[str] = []
        for step in steps:
            res = self.run_step(step)
            evidence.extend(res.evidence)
            if res.ok:
                passed += 1
                if res.healed:
                    heals += 1
            else:
                failed += 1
                heals += step.attempts - 1 if step.attempts > 1 else 0
                dur = time.time() - t0
                return ScenarioResult(False, "plan", len(steps), passed, failed, heals, evidence, res.message, dur)
        dur = time.time() - t0
        return ScenarioResult(True, "plan", len(steps), passed, failed, heals, evidence, "", dur)


# ---------------------------------------------------------------------------
# Concrete scenario: Telegram Desktop -> Fazliddin -> "Loyihani ko'rib chiqdingmi?"
# ---------------------------------------------------------------------------

DEFAULT_CONTACT = "Fazliddin"
DEFAULT_MESSAGE = "Loyihani ko'rib chiqdingmi?"


@boundary("scenario")
def run_telegram_scenario(contact: str = DEFAULT_CONTACT,
                          message: str = DEFAULT_MESSAGE,
                          config: Optional[AgentConfig] = None) -> ScenarioResult:
    logger = logging.getLogger("wina.agent_core")
    contact = (contact or DEFAULT_CONTACT).strip()
    message = (message or DEFAULT_MESSAGE).strip()
    if not contact:
        raise ValueError("contact must be non-empty")
    if not message:
        raise ValueError("message must be non-empty")
    orch = ReActOrchestrator(config or AgentConfig())
    orch.state.say("user", f"Send Telegram message to '{contact}': '{message}'")
    t0 = time.time()
    try:
        orch.state.set_phase(AgentPhase.PLAN)
        steps = orch.planner.plan_telegram_send(contact, message)
        for s in steps:
            orch.state.push(s)
        passed = failed = heals = 0
        evidence: List[str] = []
        for step in steps:
            res = orch.run_step(step)
            evidence.extend(res.evidence)
            if res.ok:
                passed += 1
                if res.healed:
                    heals += 1
            else:
                failed += 1
                dur = time.time() - t0
                orch.state.set_phase(AgentPhase.FAILED)
                orch.state.say("agent", f"SCENARIO FAILED at {step.id}: {res.message}")
                logger.error("Telegram scenario FAILED at %s: %s", step.id, res.message)
                try:
                    final = TOOLS.take_screenshot(str(SHOT_DIR / f"telegram_FAIL_{utc_stamp()}.png"))
                    evidence.append(final)
                except Exception as exc:
                    logger.error("Failure screenshot failed: %s", exc)
                return ScenarioResult(False, "telegram", len(steps), passed, failed, heals, evidence, res.message, dur)
        dur = time.time() - t0
        orch.state.set_phase(AgentPhase.DONE)
        orch.state.say("agent", f"Telegram scenario DONE: message to '{contact}' verified. evidence={len(evidence)} shots")
        logger.info("Telegram scenario OK in %.1fs (%d steps, %d heals)", dur, len(steps), heals)
        return ScenarioResult(True, "telegram", len(steps), passed, failed, heals, evidence, "", dur)
    except Exception as exc:
        dur = time.time() - t0
        logger.error("Telegram scenario crashed: %s\n%s", exc, traceback.format_exc())
        try:
            shot = TOOLS.take_screenshot(str(SHOT_DIR / f"telegram_CRASH_{utc_stamp()}.png"))
            return ScenarioResult(False, "telegram", 0, 0, 1, 0, [shot], f"{type(exc).__name__}: {exc}", dur)
        except Exception:
            return ScenarioResult(False, "telegram", 0, 0, 1, 0, [], f"{type(exc).__name__}: {exc}", dur)


SCENARIOS: Dict[str, Callable[..., ScenarioResult]] = {
    "telegram": run_telegram_scenario,
}


@boundary("scenario")
def run_generic_task(task: str, provider: Optional[str] = None,
                     config: Optional[AgentConfig] = None) -> ScenarioResult:
    """Run a free-form Wina task planned by brain.plan_task via ReAct O-P-A-V.

    Flow: brain.load_env() -> brain.plan_task(task) -> planner.plan_generic()
    -> orchestrator.run_plan(). Telegram helpers are untouched.
    """
    logger = logging.getLogger("wina.agent_core")
    task = (task or "").strip()
    if not task:
        raise ValueError("task must be non-empty")
    orch = ReActOrchestrator(config or AgentConfig())
    orch.state.say("user", f"Generic task: {task!r} (provider={provider})")
    t0 = time.time()
    try:
        try:
            brain = importlib.import_module("brain")
        except Exception as exc:
            raise RuntimeError(f"brain module unavailable (needed for generic tasks): {exc}") from exc
        load_env = getattr(brain, "load_env", None)
        if callable(load_env):
            try:
                load_env()
            except TypeError:
                load_env(None)  # type: ignore[call-arg]
        plan_task = getattr(brain, "plan_task", None)
        if not callable(plan_task):
            raise RuntimeError("brain.plan_task(task) not found — cannot plan generic task")
        try:
            steps_data = plan_task(task, provider) if provider else plan_task(task)
        except TypeError:
            try:
                steps_data = plan_task(task, provider=provider)
            except TypeError:
                steps_data = plan_task({"task": task, "provider": provider})
        if isinstance(steps_data, dict) and "steps" in steps_data:
            steps_data = steps_data["steps"]
        orch.state.set_phase(AgentPhase.PLAN)
        steps = orch.planner.plan_generic(steps_data)
        result = orch.run_plan(steps)
        result.scenario = "generic"
        dur = time.time() - t0
        result.duration_s = dur
        orch.state.set_phase(AgentPhase.DONE if result.ok else AgentPhase.FAILED)
        orch.state.say("agent", f"Generic task {'DONE' if result.ok else 'FAILED'}: {result.steps_passed}/{result.steps_total}")
        logger.info("Generic task %s in %.1fs (%d steps, %d heals)",
                    "OK" if result.ok else "FAIL", dur, result.steps_total, result.heals)
        return result
    except Exception as exc:
        dur = time.time() - t0
        logger.error("Generic task crashed: %s\n%s", exc, traceback.format_exc())
        return ScenarioResult(False, "generic", 0, 0, 1, 0, [], f"{type(exc).__name__}: {exc}", dur)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent_core", description="Wina Module 5: Cognitive Orchestrator (ReAct O-P-A-V)")
    p.add_argument("--scenario", choices=sorted(SCENARIOS.keys()) + ["list"], default="telegram",
                   help="Scenario to run (default: telegram)")
    p.add_argument("--contact", default=DEFAULT_CONTACT, help="Telegram contact display name")
    p.add_argument("--message", default=DEFAULT_MESSAGE, help="Message text to send")
    p.add_argument("--target-app", default="Telegram", help="Target window title substring")
    p.add_argument("--target-exe", default="Telegram.exe", help="Target executable")
    p.add_argument("--max-retries", type=int, default=3, help="Max self-heal retries per step")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.log_level)
    if args.scenario == "list":
        print("Available scenarios:")
        for k in sorted(SCENARIOS.keys()):
            print(f"  - {k}")
        return 0
    if args.max_retries < 0 or args.max_retries > 10:
        print("ERROR: --max-retries must be 0..10", file=sys.stderr)
        return 2
    cfg = AgentConfig(target_app=args.target_app, target_exe=args.target_exe, max_heal_retries=args.max_retries)
    try:
        result = run_telegram_scenario(contact=args.contact, message=args.message, config=cfg)
    except Exception as exc:
        print(f"SCENARIO CRASH: {type(exc).__name__}: {exc}", file=sys.stderr)
        LOG.error("main crash: %s\n%s", exc, traceback.format_exc())
        return 1
    print("=" * 64)
    print(f"Scenario : {result.scenario}")
    print(f"Result   : {'PASS' if result.ok else 'FAIL'}")
    print(f"Steps    : {result.steps_passed}/{result.steps_total} passed, {result.steps_failed} failed, {result.heals} healed")
    print(f"Duration : {result.duration_s:.1f}s")
    if result.error:
        print(f"Error    : {result.error}")
    print(f"Evidence : {len(result.evidence)} screenshot(s)")
    for e in result.evidence:
        print(f"  - {e}")
    print(f"Logs     : {LOG_DIR}")
    print("=" * 64)
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
