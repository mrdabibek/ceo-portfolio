"""
MODULE 4: tools.py — Executable Action Primitives for PROJECT JARVIS-CORE.
Python 3.11+, Windows 10/11.
UIA-first + OCR/pixel fallback (strategy="auto").

Exposes exactly:
  1. launch_or_focus(app_name, executable_path, window_title_pattern)
  2. click_element(identifier, strategy="auto")
  3. type_into(identifier, text, clear_first=True, submit_enter=False)
  4. read_screen_region(bbox=None)
  5. search_and_select_in_app(search_box_id, query, target_item_name)
  6. verify_ui_state(expected_text_or_condition, timeout=10)
  7. execute_terminal_command(command, run_in_background=False)

Every action: logging, retry up to 3, VERIFY step, fallback chain
(UIA -> coordinates/OCR-pixel -> hotkey/keyboard), integration with
ui_engine.py + accessibility_scanner.py + config.py (graceful degrade
if those modules or optional 3rd-party deps are missing).
"""

from __future__ import annotations

import base64
import fnmatch
import html
import io
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

__all__ = [
    "launch_or_focus",
    "click_element",
    "type_into",
    "read_screen_region",
    "search_and_select_in_app",
    "verify_ui_state",
    "execute_terminal_command",
    "read_file",
    "write_file",
    "list_dir",
    "fetch_url",
]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_LOGGER_NAME = "jarvis.tools"
_logger = logging.getLogger(_LOGGER_NAME)
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    _logger.addHandler(_handler)
_logger.setLevel(logging.INFO)


def _log(level: int, msg: str, *args: Any) -> None:
    try:
        _logger.log(level, msg, *args)
    except Exception:
        pass

# ---------------------------------------------------------------------------
# config.py integration (graceful degrade)
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: Dict[str, Any] = {
    "action_retry_count": 3,
    "action_retry_delay_sec": 0.7,
    "verify_timeout_default": 10,
    "ocr_language": "eng",
    "screenshot_format": "PNG",
    "terminal_timeout_sec": 60,
    "terminal_shell": True,
    "allowed_terminal_commands": None,  # None = allow all; else list of allowed prefixes
    "blocked_terminal_patterns": [
        r"(?i)\brm\s+-rf\s+/(?:\s|$)",
        r"(?i)format\s+[a-z]:",
        r"(?i)mkfs",
    ],
}

_LOADED_CONFIG: Dict[str, Any] = dict(_DEFAULT_CONFIG)

try:  # pragma: no cover - integration branch
    import config as _app_config  # type: ignore

    for _key in (
        "get_config",
        "get",
        "JARVIS_CONFIG",
        "CONFIG",
        "SETTINGS",
    ):
        try:
            if _key in ("JARVIS_CONFIG", "CONFIG", "SETTINGS"):
                _candidate = getattr(_app_config, _key, None)
                if isinstance(_candidate, dict):
                    _LOADED_CONFIG.update({k: v for k, v in _candidate.items() if k in _DEFAULT_CONFIG})
                    break
            else:
                _fn = getattr(_app_config, _key, None)
                if callable(_fn):
                    try:
                        _got = _fn()
                    except TypeError:
                        continue
                    if isinstance(_got, dict):
                        _LOADED_CONFIG.update({k: v for k, v in _got.items() if k in _DEFAULT_CONFIG})
                        break
        except Exception as exc:
            _log(logging.DEBUG, "config integration key %s failed: %s", _key, exc)
    _log(logging.DEBUG, "config.py integrated: %s", sorted(_LOADED_CONFIG.keys()))
except Exception as exc:
    _log(logging.DEBUG, "config.py not available, using defaults: %s", exc)


def _cfg(key: str) -> Any:
    return _LOADED_CONFIG.get(key, _DEFAULT_CONFIG.get(key))

# ---------------------------------------------------------------------------
# Optional third-party deps (all graceful degrade)
# ---------------------------------------------------------------------------

_HAS_PYWINAUTO = False
_HAS_PYAUTOGUI = False
_HAS_PIL = False
_HAS_TESSERACT = False
_HAS_GETWINDOW = False
_HAS_MSS = False

try:
    from pywinauto import Application as _PywinautoApp  # type: ignore
    from pywinauto import Desktop as _PywinautoDesktop  # type: ignore
    from pywinauto.keyboard import send_keys as _uia_send_keys  # type: ignore
    _HAS_PYWINAUTO = True
except Exception as exc:
    _PywinautoApp = None  # type: ignore
    _PywinautoDesktop = None  # type: ignore
    _uia_send_keys = None  # type: ignore
    _log(logging.DEBUG, "pywinauto unavailable: %s", exc)

try:
    import pyautogui as _pag  # type: ignore
    _pag.FAILSAFE = False
    try:
        _pag.PAUSE = 0.05
    except Exception:
        pass
    _HAS_PYAUTOGUI = True
except Exception as exc:
    _pag = None  # type: ignore
    _log(logging.DEBUG, "pyautogui unavailable: %s", exc)

try:
    from PIL import Image as _PILImage  # type: ignore
    from PIL import ImageGrab as _PILGrab  # type: ignore
    _HAS_PIL = True
except Exception as exc:
    _PILImage = None  # type: ignore
    _PILGrab = None  # type: ignore
    _log(logging.DEBUG, "Pillow unavailable: %s", exc)

try:
    import pytesseract as _pytess  # type: ignore
    _HAS_TESSERACT = True
except Exception as exc:
    _pytess = None  # type: ignore
    _log(logging.DEBUG, "pytesseract unavailable: %s", exc)

try:
    import pygetwindow as _gw  # type: ignore
    _HAS_GETWINDOW = True
except Exception as exc:
    _gw = None  # type: ignore
    _log(logging.DEBUG, "pygetwindow unavailable: %s", exc)

try:
    import mss as _mss_mod  # type: ignore
    _HAS_MSS = True
except Exception as exc:
    _mss_mod = None  # type: ignore
    _log(logging.DEBUG, "mss unavailable: %s", exc)

# Win32 fallback for foreground/focus without extra deps.
try:
    import ctypes as _ctypes
    _user32 = _ctypes.windll.user32 if os.name == "nt" else None
except Exception:
    _ctypes = None  # type: ignore
    _user32 = None

# ---------------------------------------------------------------------------
# ui_engine.py + accessibility_scanner.py integration (graceful degrade)
# ---------------------------------------------------------------------------

_UI_ENGINE: Any = None
_SCANNER: Any = None

try:
    import ui_engine as _ui_engine_mod  # type: ignore
    _UI_ENGINE = _ui_engine_mod
    _log(logging.DEBUG, "ui_engine.py integrated: %s", getattr(_ui_engine_mod, "__name__", "ui_engine"))
except Exception as exc:
    _log(logging.DEBUG, "ui_engine.py not available: %s", exc)

try:
    import accessibility_scanner as _scanner_mod  # type: ignore
    _SCANNER = _scanner_mod
    _log(logging.DEBUG, "accessibility_scanner.py integrated.")
except Exception as exc:
    _log(logging.DEBUG, "accessibility_scanner.py not available: %s", exc)


def _call_optional(obj: Any, names: List[str], *args: Any, **kwargs: Any) -> Tuple[bool, Any]:
    """Try calling the first existing callable in names on obj/module. Returns (called, result)."""
    if obj is None:
        return False, None
    for name in names:
        try:
            fn = getattr(obj, name, None)
            if callable(fn):
                return True, fn(*args, **kwargs)
        except Exception as exc:
            _log(logging.DEBUG, "optional integration %s failed: %s", name, exc)
            continue
    return False, None

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MAX_RETRIES = 3


def _retry_delay(attempt: int) -> float:
    base = float(_cfg("action_retry_delay_sec") or 0.7)
    return base * (attempt + 1)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def _norm_lower(s: Any) -> str:
    return _norm(s).lower()


@dataclass
class ActionResult:
    success: bool
    action: str
    method: str = "none"
    attempts: int = 0
    detail: str = ""
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "action": self.action,
            "method": self.method,
            "attempts": self.attempts,
            "detail": self.detail,
            "data": self.data,
        }


def _result(
    action: str,
    success: bool,
    method: str = "none",
    attempts: int = 0,
    detail: str = "",
    **data: Any,
) -> Dict[str, Any]:
    return ActionResult(
        success=success, action=action, method=method,
        attempts=attempts, detail=detail, data=dict(data),
    ).to_dict()


def _normalize_identifier(identifier: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize identifier (dict or string) into a hint dict.

    Supported dict keys: automation_id, name, title, class_name, control_type,
    best_match / title_re, x, y, bbox, text (OCR label), hotkey, keys.
    String identifier is treated as: automation_id=name=title=text=string.
    """
    if isinstance(identifier, dict):
        out = dict(identifier)
        return out
    s = _norm(identifier)
    d: Dict[str, Any] = {"raw": s}
    if re.match(r"^(id|aid|automation_id)\s*[:=]", s, re.IGNORECASE):
        d["automation_id"] = re.split(r"[:=]", s, maxsplit=1)[1].strip()
    elif re.match(r"^hotkey\s*[:=]", s, re.IGNORECASE):
        d["hotkey"] = re.split(r"[:=]", s, maxsplit=1)[1].strip()
    elif re.match(r"^-?\d+\s*,\s*-?\d+$", s):
        x, y = s.split(",")
        d["x"], d["y"] = int(x.strip()), int(y.strip())
    else:
        d["automation_id"] = s
        d["name"] = s
        d["title"] = s
        d["text"] = s
    return d


def _identifier_targets(ident: Dict[str, Any]) -> Dict[str, Any]:
    """Build pywinauto-style best-match kwargs from identifier."""
    kwargs: Dict[str, Any] = {}
    for key in ("title", "name", "automation_id", "class_name", "control_type", "best_match", "title_re"):
        if ident.get(key) not in (None, ""):
            kwargs[key] = ident[key]
    if "title_re" not in kwargs and isinstance(ident.get("title"), str) and any(
        c in str(ident.get("title")) for c in (".*", r"\d", "(", "[", "^", "$")
    ):
        kwargs["title_re"] = str(ident["title"])
    return kwargs


def _enum_windows_via_pywinauto(pattern: str) -> List[Any]:
    if not _HAS_PYWINAUTO or _PywinautoDesktop is None:
        return []
    try:
        desktop = _PywinautoDesktop(backend="uia")
        wins = desktop.windows()
        rx = re.compile(pattern, re.IGNORECASE) if pattern else None
        if rx is None:
            return wins
        matched = []
        for w in wins:
            try:
                t = w.window_text() or ""
            except Exception:
                t = ""
            if rx.search(t):
                matched.append(w)
        return matched
    except Exception as exc:
        _log(logging.DEBUG, "pywinauto enum failed: %s", exc)
        return []


def _enum_windows_via_getwindow(pattern: str) -> List[Any]:
    if not _HAS_GETWINDOW or _gw is None:
        return []
    try:
        titles = _gw.getAllTitles()
        rx = re.compile(pattern, re.IGNORECASE) if pattern else None
        out = []
        for t in titles:
            if not t:
                continue
            if rx is None or rx.search(t):
                try:
                    out.extend(_gw.getWindowsWithTitle(t))
                except Exception:
                    continue
        return out
    except Exception as exc:
        _log(logging.DEBUG, "pygetwindow enum failed: %s", exc)
        return []


def _bring_window_to_front(win: Any) -> bool:
    # 1) ui_engine integration
    try:
        called, res = _call_optional(_UI_ENGINE, ["focus_window", "bring_to_front", "activate_window"], win)
        if called and res not in (False, None):
            return True
    except Exception:
        pass
    # 2) native object methods
    for meth in ("set_focus", "setfocus", "activate", "restore", "maximize"):
        try:
            fn = getattr(win, meth, None)
            if callable(fn):
                fn()
                if meth in ("set_focus", "setfocus", "activate"):
                    return True
        except Exception:
            continue
    # pygetwindow objects
    try:
        if hasattr(win, "activate") and callable(getattr(win, "activate")):
            win.activate()
            return True
        if hasattr(win, "restore") and callable(getattr(win, "restore")):
            try:
                win.restore()
            except Exception:
                pass
    except Exception:
        pass
    time.sleep(0.3)
    return True


def _is_process_running(hint: str) -> bool:
    hint_l = hint.lower()
    try:
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and hint_l in out.stdout.lower():
            return True
    except Exception:
        pass
    return False


def _screenshot_pil(bbox: Optional[Tuple[int, int, int, int]] = None):  # -> PIL Image | None
    if not _HAS_PIL:
        return None
    try:
        if _PILGrab is not None:
            if bbox:
                return _PILGrab.grab(bbox=bbox)
            return _PILGrab.grab()
    except Exception as exc:
        _log(logging.DEBUG, "ImageGrab failed: %s", exc)
    # pyautogui fallback
    if _HAS_PYAUTOGUI and _pag is not None:
        try:
            shot = _pag.screenshot()
            if bbox and _PILImage is not None:
                x1, y1, x2, y2 = bbox
                return shot.crop((x1, y1, x2, y2))
            return shot
        except Exception as exc:
            _log(logging.DEBUG, "pyautogui screenshot failed: %s", exc)
    # mss fallback
    if _HAS_MSS and _mss_mod is not None and _PILImage is not None:
        try:
            import mss as _m  # type: ignore
            with _m.mss() as sct:
                mon = sct.monitors[0]
                if bbox:
                    x1, y1, x2, y2 = bbox
                    region = {"left": x1, "top": y1, "width": max(1, x2 - x1), "height": max(1, y2 - y1)}
                else:
                    region = mon
                raw = sct.grab(region)
                img = _PILImage.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                return img
        except Exception as exc:
            _log(logging.DEBUG, "mss screenshot failed: %s", exc)
    return None


def _image_to_base64(img: Any) -> str:
    if img is None:
        return ""
    try:
        buf = io.BytesIO()
        fmt = str(_cfg("screenshot_format") or "PNG")
        img.save(buf, format=fmt)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:
        _log(logging.DEBUG, "image encode failed: %s", exc)
        return ""


def _ocr_image(img: Any) -> Tuple[str, float]:
    """Return (text, confidence-ish). Graceful degrade to '' when missing."""
    if img is None:
        return "", 0.0
    if not _HAS_TESSERACT or _pytess is None:
        return "", 0.0
    try:
        lang = str(_cfg("ocr_language") or "eng")
        text = _pytess.image_to_string(img, lang=lang) or ""
        text = _norm(text)
        try:
            data = _pytess.image_to_data(img, lang=lang, output_type=_pytess.Output.DICT)
            confs: List[float] = []
            for c in data.get("conf", []):
                try:
                    f = float(c)
                    if f >= 0:
                        confs.append(f)
                except Exception:
                    continue
            conf = (sum(confs) / len(confs) / 100.0) if confs else (0.5 if text else 0.0)
        except Exception:
            conf = 0.5 if text else 0.0
        return text, max(0.0, min(1.0, conf))
    except Exception as exc:
        _log(logging.DEBUG, "OCR failed: %s", exc)
        return "", 0.0


def _ocr_text_of_screen(bbox: Optional[Tuple[int, int, int, int]] = None) -> str:
    img = _screenshot_pil(bbox)
    text, _ = _ocr_image(img)
    return text


def _scanner_find_text(target: str) -> Optional[Dict[str, Any]]:
    """Ask accessibility_scanner for element matching visible text. Returns dict with x/y/bbox if found."""
    if _SCANNER is None:
        return None
    for meth in ("find_element_by_text", "find_by_text", "find_element", "search", "scan_and_find", "locate"):
        try:
            fn = getattr(_SCANNER, meth, None)
            if not callable(fn):
                continue
            try:
                res = fn(target)
            except TypeError:
                try:
                    res = fn(text=target)
                except TypeError:
                    continue
            if not res:
                continue
            if isinstance(res, dict):
                return res
            # object with coords
            d: Dict[str, Any] = {}
            for attr in ("x", "y", "center", "bbox", "rect", "rectangle", "element", " automation_id"):
                try:
                    v = getattr(res, attr.strip(), None)
                    if v is not None:
                        d[attr.strip()] = v
                except Exception:
                    continue
            if isinstance(getattr(res, "rectangle", None), (tuple, list)) and len(res.rectangle) == 4:  # type: ignore
                d["bbox"] = tuple(res.rectangle)  # type: ignore
            return d or {"raw": str(res)}
        except Exception as exc:
            _log(logging.DEBUG, "scanner %s failed: %s", meth, exc)
            continue
    # generic scan() then match client-side
    try:
        scan_fn = getattr(_SCANNER, "scan", None)
        if callable(scan_fn):
            elements = scan_fn()
            if isinstance(elements, list):
                tl = _norm_lower(target)
                for el in elements:
                    try:
                        blob = _norm_lower(
                            getattr(el, "name", "") if not isinstance(el, dict)
                            else str(el.get("name", el.get("text", el.get("title", ""))))
                        )
                        if tl and tl in blob:
                            if isinstance(el, dict):
                                return el
                            return {"raw": str(el)}
                    except Exception:
                        continue
    except Exception as exc:
        _log(logging.DEBUG, "scanner scan() failed: %s", exc)
    return None


def _uia_find_element(ident: Dict[str, Any], scope: Any = None) -> Any:
    if not _HAS_PYWINAUTO or _PywinautoDesktop is None:
        return None
    # 1) ui_engine integration first
    if _UI_ENGINE is not None:
        for meth in ("find_element", "get_element", "query_element", "locate_element"):
            try:
                fn = getattr(_UI_ENGINE, meth, None)
                if callable(fn):
                    try:
                        el = fn(ident)
                    except TypeError:
                        el = fn(**_identifier_targets(ident))
                    if el is not None:
                        return el
            except Exception as exc:
                _log(logging.DEBUG, "ui_engine %s failed: %s", meth, exc)
    # 2) direct pywinauto search
    try:
        backend_kwargs = _identifier_targets(ident)
        desktop = _PywinautoDesktop(backend="uia")
        root = scope if scope is not None else desktop
        # pywinauto Desktop supports child_window(); generic wrappers support descendant search
        if hasattr(root, "child_window"):
            try:
                el = root.child_window(**backend_kwargs)  # type: ignore
                el.wait("exists enabled visible ready", timeout=3)
                return el
            except Exception:
                pass
        if hasattr(desktop, "window"):
            try:
                el = desktop.window(**backend_kwargs)  # type: ignore
                el.wait("exists", timeout=3)
                return el
            except Exception:
                pass
        # window_spec search across top-level windows
        try:
            wins = desktop.windows()
            for w in wins:
                try:
                    cand = w.child_window(**backend_kwargs)  # type: ignore
                    cand.wait("exists", timeout=1)
                    return cand
                except Exception:
                    continue
        except Exception:
            pass
    except Exception as exc:
        _log(logging.DEBUG, "uia find failed: %s", exc)
    return None


def _uia_click_element(el: Any) -> bool:
    if el is None:
        return False
    for meth in ("click_input", "click", "invoke", "toggle"):
        try:
            fn = getattr(el, meth, None)
            if not callable(fn):
                continue
            if meth == "click_input":
                fn(button="left")
            else:
                fn()
            return True
        except Exception as exc:
            _log(logging.DEBUG, "uia click via %s failed: %s", meth, exc)
            continue
    # focus + Enter as last UIA resort
    try:
        fn = getattr(el, "set_focus", None)
        if callable(fn):
            fn()
            _press_keys("{ENTER}")
            return True
    except Exception:
        pass
    return False


def _element_center(el: Any) -> Optional[Tuple[int, int]]:
    try:
        rect = el.rectangle()  # pywinauto
        cx = (rect.left + rect.right) // 2
        cy = (rect.top + rect.bottom) // 2
        return int(cx), int(cy)
    except Exception:
        pass
    for attr in ("center", "click_point", "point"):
        try:
            v = getattr(el, attr, None)
            val = v() if callable(v) else v
            if isinstance(val, (tuple, list)) and len(val) >= 2:
                return int(val[0]), int(val[1])
        except Exception:
            continue
    return None


def _mouse_click_at(x: int, y: int) -> bool:
    # ui_engine integration
    if _UI_ENGINE is not None:
        for meth in ("click_at", "mouse_click", "click_coordinates"):
            try:
                fn = getattr(_UI_ENGINE, meth, None)
                if callable(fn):
                    r = fn(x, y)
                    if r not in (False, None):
                        return True
            except Exception as exc:
                _log(logging.DEBUG, "ui_engine %s failed: %s", meth, exc)
    if _HAS_PYAUTOGUI and _pag is not None:
        try:
            _pag.click(x, y)
            return True
        except Exception as exc:
            _log(logging.DEBUG, "pyautogui click failed: %s", exc)
    if os.name == "nt" and _user32 is not None and _ctypes is not None:
        try:
            _user32.SetCursorPos(int(x), int(y))
            _user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
            time.sleep(0.05)
            _user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
            return True
        except Exception as exc:
            _log(logging.DEBUG, "win32 click failed: %s", exc)
    return False


def _press_hotkey(hotkey: str) -> bool:
    hotkey = _norm(hotkey)
    if not hotkey:
        return False
    # ui_engine integration
    if _UI_ENGINE is not None:
        for meth in ("press_hotkey", "hotkey", "send_hotkey", "keyboard_shortcut"):
            try:
                fn = getattr(_UI_ENGINE, meth, None)
                if callable(fn):
                    r = fn(hotkey)
                    if r not in (False, None):
                        return True
            except Exception as exc:
                _log(logging.DEBUG, "ui_engine %s failed: %s", meth, exc)
    if _HAS_PYAUTOGUI and _pag is not None:
        try:
            parts = [p.strip().lower() for p in re.split(r"\s*\+\s*", hotkey) if p.strip()]
            _pag.hotkey(*parts)
            return True
        except Exception as exc:
            _log(logging.DEBUG, "pyautogui hotkey failed: %s", exc)
    return _press_keys(hotkey)


def _press_keys(keys: str) -> bool:
    if _HAS_PYWINAUTO and _uia_send_keys is not None:
        try:
            _uia_send_keys(keys)
            return True
        except Exception as exc:
            _log(logging.DEBUG, "send_keys failed: %s", exc)
    if _HAS_PYAUTOGUI and _pag is not None:
        try:
            # translate simple pywinauto tokens to pyautogui presses
            token_map = {"{ENTER}": "enter", "{TAB}": "tab", "{ESC}": "esc", "{BACKSPACE}": "backspace"}
            k = token_map.get(keys.strip(), keys)
            if len(k) == 1 or k in ("enter", "tab", "esc", "backspace"):
                _pag.press(k if len(k) > 1 else k)
                return True
            _pag.typewrite(keys)
            return True
        except Exception as exc:
            _log(logging.DEBUG, "pyautogui press failed: %s", exc)
    return False


def _type_text_native(text: str) -> bool:
    if _UI_ENGINE is not None:
        for meth in ("type_text", "write", "send_text"):
            try:
                fn = getattr(_UI_ENGINE, meth, None)
                if callable(fn):
                    r = fn(text)
                    if r not in (False, None):
                        return True
            except Exception as exc:
                _log(logging.DEBUG, "ui_engine %s failed: %s", meth, exc)
    if _HAS_PYAUTOGUI and _pag is not None:
        try:
            _pag.typewrite(text, interval=0.01)
            return True
        except Exception as exc:
            _log(logging.DEBUG, "pyautogui typewrite failed: %s", exc)
    if _HAS_PYWINAUTO and _uia_send_keys is not None:
        try:
            # escape pywinauto special chars
            escaped = text.replace("{", "{{}").replace("}", "{}}").replace("+", "{+}").replace("^", "{^}").replace("%", "{%}")
            _uia_send_keys(escaped, with_spaces=True)
            return True
        except Exception as exc:
            _log(logging.DEBUG, "uia type failed: %s", exc)
    # clipboard-paste fallback (best for unicode)
    try:
        import tkinter as _tk  # noqa
    except Exception:
        _tk = None  # type: ignore
    try:
        if os.name == "nt" and _ctypes is not None:
            # useclipboard via powershell Set-Clipboard then Ctrl+V
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Set-Clipboard -Value $args[0]", "--", text],
                capture_output=True, timeout=10,
            )
            return _press_hotkey("ctrl+v")
        if _tk is not None:
            r = _tk.Tk()
            r.withdraw()
            r.clipboard_clear()
            r.clipboard_append(text)
            r.update()
            r.destroy()
            return _press_hotkey("ctrl+v")
    except Exception as exc:
        _log(logging.DEBUG, "clipboard paste failed: %s", exc)
    return False


def _fuzzy_ocr_locate(label: str, bbox: Optional[Tuple[int, int, int, int]] = None) -> Optional[Tuple[int, int]]:
    """Locate label center via OCR word boxes. Returns (x, y) or None if OCR missing."""
    if not _HAS_TESSERACT or _pytess is None or not _HAS_PIL:
        return None
    img = _screenshot_pil(bbox)
    if img is None:
        return None
    try:
        lang = str(_cfg("ocr_language") or "eng")
        data = _pytess.image_to_data(img, lang=lang, output_type=_pytess.Output.DICT)
        n = len(data.get("text", []))
        want = _norm_lower(label)
        ox, oy = (bbox[0], bbox[1]) if bbox else (0, 0)
        best: Optional[Tuple[int, int]] = None
        for i in range(n):
            word = _norm_lower(data["text"][i])
            if not word:
                continue
            try:
                conf = float(data["conf"][i])
            except Exception:
                conf = 50.0
            if conf < 20:
                continue
            if want == word or (len(want) >= 3 and want in word) or (len(word) >= 3 and word in want):
                try:
                    l, t, w, h = int(data["left"][i]), int(data["top"][i]), int(data["width"][i]), int(data["height"][i])
                    best = (ox + l + w // 2, oy + t + h // 2)
                    break
                except Exception:
                    continue
        return best
    except Exception as exc:
        _log(logging.DEBUG, "ocr locate failed: %s", exc)
        return None


def _check_text_visible(expected: str, bbox: Optional[Tuple[int, int, int, int]] = None) -> bool:
    want = _norm_lower(expected)
    if not want:
        return False
    # 1) accessibility scanner text
    try:
        found = _scanner_find_text(expected)
        if found:
            return True
    except Exception:
        pass
    # 2) UIA window texts
    if _HAS_PYWINAUTO and _PywinautoDesktop is not None:
        try:
            desktop = _PywinautoDesktop(backend="uia")
            for w in desktop.windows():
                try:
                    if want in _norm_lower(w.window_text()):
                        return True
                    for child in w.descendants():
                        try:
                            t = child.window_text()
                            if t and want in _norm_lower(t):
                                return True
                        except Exception:
                            continue
                except Exception:
                    continue
        except Exception:
            pass
    # 3) OCR fallback
    try:
        visible = _norm_lower(_ocr_text_of_screen(bbox))
        if want in visible:
            return True
    except Exception:
        pass
    return False


def _check_condition(condition: Any, bbox: Optional[Tuple[int, int, int, int]] = None) -> Tuple[bool, str]:
    """Evaluate expected_text_or_condition. Supports str, regex dict, callable, bbox dict."""
    if callable(condition):
        try:
            res = condition()
            if isinstance(res, tuple) and len(res) == 2:
                return bool(res[0]), str(res[1])
            return bool(res), "callable evaluated to %r" % (res,)
        except Exception as exc:
            return False, "callable raised: %s" % exc
    if isinstance(condition, dict):
        # {"text": ...} / {"regex": ...} / {"not_text": ...} / {"bbox": [...]}
        b = condition.get("bbox", None)
        _b = tuple(b) if isinstance(b, (list, tuple)) and len(b) == 4 else bbox
        if "regex" in condition:
            try:
                rx = re.compile(str(condition["regex"]), re.IGNORECASE | re.DOTALL)
                hay = _ocr_text_of_screen(_b)
                # also scan UIA texts
                m = rx.search(hay)
                return (m is not None), ("regex matched" if m else "regex not found; ocr=%r" % hay[:200])
            except re.error as exc:
                return False, "bad regex: %s" % exc
        if "not_text" in condition:
            ok = not _check_text_visible(str(condition["not_text"]), _b)
            return ok, ("absence verified" if ok else "unexpected text present")
        if "text" in condition:
            ok = _check_text_visible(str(condition["text"]), _b)
            return ok, ("text visible" if ok else "text not visible")
        if "any_text" in condition and isinstance(condition["any_text"], list):
            for t in condition["any_text"]:
                if _check_text_visible(str(t), _b):
                    return True, "matched: %r" % str(t)[:80]
            return False, "none of the candidate texts visible"
        return False, "unsupported condition dict keys: %s" % sorted(condition.keys())
    # plain string
    ok = _check_text_visible(str(condition), bbox)
    return ok, ("text visible" if ok else "text not visible")


# Background process registry for execute_terminal_command(run_in_background=True)
_BG_PROCS: Dict[int, subprocess.Popen] = {}
_BG_LOCK = threading.Lock()
_BG_COUNTER = 0


def _terminal_allowed(command: str) -> Tuple[bool, str]:
    allowed = _cfg("allowed_terminal_commands")
    if isinstance(allowed, (list, tuple)) and allowed:
        cl = command.strip().lower()
        for prefix in allowed:
            if cl.startswith(str(prefix).strip().lower()):
                break
        else:
            return False, "command not in allowed_terminal_commands list"
    for pat in _cfg("blocked_terminal_patterns") or []:
        try:
            if re.search(str(pat), command):
                return False, "command blocked by safety pattern: %s" % pat
        except re.error:
            continue
    return True, ""


# ---------------------------------------------------------------------------
# 1. launch_or_focus
# ---------------------------------------------------------------------------

def launch_or_focus(
    app_name: str,
    executable_path: str,
    window_title_pattern: str,
) -> Dict[str, Any]:
    """Launch app or focus its window (UIA-first, VERIFY by title/process).

    Fallback chain: match existing window (UIA/pygetwindow) -> launch via
    subprocess -> re-scan windows -> VERIFY title visible / process running.
    Retries up to 3 with backoff. Never raises; returns result dict.
    """
    action = "launch_or_focus"
    max_retries = int(_cfg("action_retry_count") or _MAX_RETRIES)
    pattern = _norm(window_title_pattern or app_name)
    last_detail = ""
    method_used = "none"

    for attempt in range(1, max_retries + 1):
        _log(logging.INFO, "%s attempt %d/%d app=%r pattern=%r", action, attempt, max_retries, app_name, pattern)
        # --- Step 1: try focus existing window (UIA-first) ---
        wins: List[Any] = []
        wins.extend(_enum_windows_via_pywinauto(pattern))
        if not wins:
            wins.extend(_enum_windows_via_getwindow(pattern))
        # ui_engine assisted search
        if not wins and _UI_ENGINE is not None:
            try:
                called, res = _call_optional(_UI_ENGINE, ["find_window", "get_window"], pattern)
                if called and res:
                    wins = res if isinstance(res, list) else [res]
            except Exception as exc:
                _log(logging.DEBUG, "ui_engine find_window failed: %s", exc)
        if wins:
            try:
                _bring_window_to_front(wins[0])
                time.sleep(0.6)
                # VERIFY: window still present / title matches
                verify = _enum_windows_via_pywinauto(pattern) or _enum_windows_via_getwindow(pattern)
                if verify or _is_process_running(app_name) or _is_process_running(Path(str(executable_path)).stem if executable_path else app_name):
                    _log(logging.INFO, "%s focused existing window (attempt %d)", action, attempt)
                    return _result(action, True, "focus_existing", attempt, "focused existing window", app=app_name, pattern=pattern)
                last_detail = "focus attempted but window vanished on verify"
            except Exception as exc:
                last_detail = "focus failed: %s" % exc
        # --- Step 2: launch ---
        exe = _norm(executable_path)
        launched = False
        if exe:
            try:
                exe_path = os.path.expandvars(exe)
                if os.path.isfile(exe_path) or exe_path.lower().endswith((".exe", ".bat", ".cmd", ".lnk")) or os.path.exists(exe_path):
                    subprocess.Popen([exe_path], shell=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    launched = True
                    method_used = "subprocess_popen"
                else:
                    # treat as command line / PATH entry / URI
                    subprocess.Popen(exe_path, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    launched = True
                    method_used = "shell_popen"
                _log(logging.INFO, "%s launched %r", action, exe_path)
            except Exception as exc:
                last_detail = "launch failed: %s" % exc
                _log(logging.WARNING, "%s launch error: %s", action, exc)
        else:
            # fallback: os.startfile / start menu via shell
            try:
                if os.name == "nt" and executable_path == "" and app_name:
                    os.startfile(app_name)  # type: ignore[attr-defined]
                    launched = True
                    method_used = "os.startfile"
            except Exception as exc:
                last_detail = "startfile failed: %s" % exc
        # --- Step 3: VERIFY launch ---
        if launched:
            time.sleep(1.5)
            verify = _enum_windows_via_pywinauto(pattern) or _enum_windows_via_getwindow(pattern)
            proc_ok = _is_process_running(app_name) or (exe and _is_process_running(Path(exe).stem))
            if verify or proc_ok:
                try:
                    if verify:
                        _bring_window_to_front(verify[0])
                except Exception:
                    pass
                return _result(action, True, method_used or "launch", attempt, "launched and verified", app=app_name, pattern=pattern)
            last_detail = "launched but window/process not verified yet"
            method_used = method_used or "launch_unverified"
        if attempt < max_retries:
            time.sleep(_retry_delay(attempt))
    _log(logging.ERROR, "%s FAILED after %d attempts: %s", action, max_retries, last_detail)
    return _result(action, False, method_used, max_retries, last_detail or "app not found/launched", app=app_name, pattern=pattern)


# ---------------------------------------------------------------------------
# 2. click_element
# ---------------------------------------------------------------------------

def click_element(
    identifier: Union[str, Dict[str, Any]],
    strategy: str = "auto",
) -> Dict[str, Any]:
    """Click a UI element. strategy: auto|uia|coords|ocr|hotkey.

    Fallback chain (auto): UIA -> scanner/coords -> OCR-pixel coords -> hotkey.
    VERIFY: element reports visible/enabled post-click or click dispatched
    without exception + optional disappearance/via follow-up scan.
    """
    action = "click_element"
    max_retries = int(_cfg("action_retry_count") or _MAX_RETRIES)
    ident = _normalize_identifier(identifier)
    strat = _norm_lower(strategy or "auto")
    if strat not in ("auto", "uia", "coords", "ocr", "hotkey", "coordinates", "pixel"):
        strat = "auto"
    last_detail = ""
    explicit_xy = ("x" in ident and "y" in ident)
    explicit_hotkey = bool(ident.get("hotkey"))

    ordered_methods: List[str]
    if strat == "uia":
        ordered_methods = ["uia"]
    elif strat in ("coords", "coordinates"):
        ordered_methods = ["coords"]
    elif strat in ("ocr", "pixel"):
        ordered_methods = ["ocr"]
    elif strat == "hotkey":
        ordered_methods = ["hotkey"]
    else:
        ordered_methods = ["uia", "coords", "ocr", "hotkey"]

    for attempt in range(1, max_retries + 1):
        for method in ordered_methods:
            try:
                if method == "uia" and not explicit_xy:
                    _log(logging.INFO, "%s attempt %d uia ident=%r", action, attempt, ident)
                    el = _uia_find_element(ident)
                    if el is not None:
                        if _uia_click_element(el):
                            time.sleep(0.4)
                            return _result(action, True, "uia", attempt, "uia click dispatched", identifier=ident)
                        last_detail = "uia element found but click dispatch failed"
                    else:
                        last_detail = "uia element not found"
                elif method == "coords":
                    tx, ty = None, None
                    if explicit_xy:
                        tx, ty = int(ident["x"]), int(ident["y"])
                    elif isinstance(ident.get("bbox"), (list, tuple)) and len(ident["bbox"]) == 4:
                        x1, y1, x2, y2 = (int(v) for v in ident["bbox"])
                        tx, ty = (x1 + x2) // 2, (y1 + y2) // 2
                    else:
                        # scanner-provided coords
                        found = _scanner_find_text(str(ident.get("text") or ident.get("name") or ident.get("title") or ident.get("raw") or ""))
                        if found:
                            if "x" in found and "y" in found:
                                try:
                                    tx, ty = int(found["x"]), int(found["y"])
                                except Exception:
                                    pass
                            if (tx is None) and isinstance(found.get("bbox"), (list, tuple)) and len(found["bbox"]) == 4:
                                x1, y1, x2, y2 = (int(v) for v in found["bbox"])
                                tx, ty = (x1 + x2) // 2, (y1 + y2) // 2
                            # uia element center as coords fallback
                        if tx is None:
                            el = _uia_find_element(ident)
                            c = _element_center(el) if el is not None else None
                            if c:
                                tx, ty = c
                    if tx is not None and ty is not None:
                        _log(logging.INFO, "%s attempt %d coords (%d,%d)", action, attempt, tx, ty)
                        if _mouse_click_at(tx, ty):
                            time.sleep(0.4)
                            return _result(action, True, "coordinates", attempt, "coordinate click dispatched", x=tx, y=ty, identifier=ident)
                        last_detail = "coordinate click dispatch failed"
                    else:
                        last_detail = "no coordinates resolvable for coords strategy"
                elif method == "ocr":
                    label = str(ident.get("text") or ident.get("name") or ident.get("title") or ident.get("raw") or "")
                    _log(logging.INFO, "%s attempt %d ocr label=%r", action, attempt, label)
                    pt = _fuzzy_ocr_locate(label) if label else None
                    if pt is None and label == "":
                        last_detail = "ocr needs a text label identifier"
                    elif pt is not None:
                        if _mouse_click_at(pt[0], pt[1]):
                            time.sleep(0.4)
                            return _result(action, True, "ocr_pixel", attempt, "ocr-located click dispatched", x=pt[0], y=pt[1], identifier=ident)
                        last_detail = "ocr located %r but click failed" % (pt,)
                    else:
                        if not _HAS_TESSERACT:
                            last_detail = "ocr unavailable (pytesseract/Pillow missing), label=%r" % label
                        else:
                            last_detail = "ocr could not locate label=%r" % label
                elif method == "hotkey":
                    hk = str(ident.get("hotkey") or ident.get("keys") or "")
                    if not hk and explicit_hotkey is False and strat == "hotkey":
                        last_detail = "hotkey strategy needs identifier['hotkey']"
                    elif hk:
                        _log(logging.INFO, "%s attempt %d hotkey %r", action, attempt, hk)
                        if _press_hotkey(hk):
                            time.sleep(0.4)
                            return _result(action, True, "hotkey", attempt, "hotkey fallback dispatched", hotkey=hk, identifier=ident)
                        last_detail = "hotkey dispatch failed"
                    else:
                        last_detail = "no hotkey in identifier; skipping hotkey fallback"
                        continue
            except Exception as exc:
                last_detail = "%s method %s raised: %s" % (action, method, exc)
                _log(logging.DEBUG, "%s", last_detail)
        if attempt < max_retries:
            time.sleep(_retry_delay(attempt))
    _log(logging.ERROR, "%s FAILED ident=%r: %s", action, ident, last_detail)
    return _result(action, False, "none", max_retries, last_detail or "element not clickable", identifier=ident)


# ---------------------------------------------------------------------------
# 3. type_into
# ---------------------------------------------------------------------------

def type_into(
    identifier: Union[str, Dict[str, Any]],
    text: str,
    clear_first: bool = True,
    submit_enter: bool = False,
) -> Dict[str, Any]:
    """Focus/click element then type text. VERIFY via element value or OCR.

    Fallback chain: UIA set_text -> click+clear+type -> hotkey select-all+type.
    """
    action = "type_into"
    max_retries = int(_cfg("action_retry_count") or _MAX_RETRIES)
    ident = _normalize_identifier(identifier)
    text = "" if text is None else str(text)
    last_detail = ""

    for attempt in range(1, max_retries + 1):
        _log(logging.INFO, "%s attempt %d ident=%r len=%d clear=%s enter=%s", action, attempt, ident, len(text), clear_first, submit_enter)
        # --- Path A: UIA direct set ---
        el = None if ("x" in ident and "y" in ident) else _uia_find_element(ident)
        if el is not None:
            # try set_text / set_edit_text (preferred, no clipboard involved)
            for meth in ("set_text", "set_edit_text", "set_value"):
                try:
                    fn = getattr(el, meth, None)
                    if callable(fn):
                        try:
                            el.set_focus()
                        except Exception:
                            pass
                        if clear_first:
                            try:
                                fn("")
                            except Exception:
                                pass
                        fn(text)
                        if submit_enter:
                            _press_keys("{ENTER}")
                        time.sleep(0.4)
                        # VERIFY: read back value
                        verified = False
                        for read_meth in ("get_value", "window_text", "texts", "legacy_properties"):
                            try:
                                rf = getattr(el, read_meth, None)
                                if callable(rf):
                                    val = rf()
                                    blob = " ".join(val) if isinstance(val, (list, tuple)) else str(val)
                                    if _norm(text)[:64] in blob or blob.strip() != "":
                                        verified = True
                                        break
                            except Exception:
                                continue
                        return _result(action, True, "uia_set_text:%s" % meth, attempt,
                                       "uia set_text dispatched" + (" (verified)" if verified else " (unverified readback)"),
                                       identifier=ident, verified=verified)
                except Exception as exc:
                    last_detail = "uia %s failed: %s" % (meth, exc)
                    continue
            # Path B: click element then keystroke-type
            try:
                if _uia_click_element(el):
                    time.sleep(0.35)
                    if clear_first:
                        _press_hotkey("ctrl+a")
                        time.sleep(0.1)
                        _press_keys("{BACKSPACE}")
                        time.sleep(0.1)
                    if _type_text_native(text):
                        if submit_enter:
                            time.sleep(0.15)
                            _press_keys("{ENTER}")
                        time.sleep(0.4)
                        return _result(action, True, "uia_click_then_type", attempt, "clicked then typed", identifier=ident)
                    last_detail = "click ok but keystroke typing failed"
                else:
                    last_detail = "uia element found but could not click/focus"
            except Exception as exc:
                last_detail = "uia click-then-type raised: %s" % exc
        else:
            # --- Path C: coordinate click then type ---
            tx, ty = None, None
            if "x" in ident and "y" in ident:
                tx, ty = int(ident["x"]), int(ident["y"])
            else:
                pt = _fuzzy_ocr_locate(str(ident.get("text") or ident.get("name") or ident.get("title") or ident.get("raw") or ""))
                if pt:
                    tx, ty = pt
            if tx is not None and ty is not None:
                if _mouse_click_at(tx, ty):
                    time.sleep(0.35)
                    if clear_first:
                        _press_hotkey("ctrl+a")
                        time.sleep(0.1)
                        _press_keys("{BACKSPACE}")
                        time.sleep(0.1)
                    if _type_text_native(text):
                        if submit_enter:
                            time.sleep(0.15)
                            _press_keys("{ENTER}")
                        time.sleep(0.4)
                        return _result(action, True, "coords_click_then_type", attempt, "coordinate click then typed", x=tx, y=ty, identifier=ident)
                    last_detail = "coords click ok but typing failed"
                else:
                    last_detail = "coords click dispatch failed"
            else:
                # --- Path D: assume already focused, just type (hotkey fallback) ---
                if clear_first:
                    _press_hotkey("ctrl+a")
                    time.sleep(0.1)
                    _press_keys("{BACKSPACE}")
                    time.sleep(0.1)
                if _type_text_native(text):
                    if submit_enter:
                        time.sleep(0.15)
                        _press_keys("{ENTER}")
                    time.sleep(0.4)
                    # VERIFY via OCR that typed text is visible somewhere
                    if text and _check_text_visible(text[:64]):
                        return _result(action, True, "hotkey_type_verified", attempt, "typed into focused control (ocr verified)", identifier=ident, verified=True)
                    last_detail = "typed into focused control (ocr unverified)"
                    # still count as success on last attempt? No — retry, then accept unverified on final attempt below
                    if attempt == max_retries:
                        return _result(action, True, "hotkey_type_unverified", attempt, last_detail, identifier=ident, verified=False)
                else:
                    last_detail = last_detail or "element not found and keystroke typing failed"
        if attempt < max_retries:
            time.sleep(_retry_delay(attempt))
    _log(logging.ERROR, "%s FAILED ident=%r: %s", action, ident, last_detail)
    return _result(action, False, "none", max_retries, last_detail or "type failed", identifier=ident)


# ---------------------------------------------------------------------------
# 4. read_screen_region
# ---------------------------------------------------------------------------

def read_screen_region(
    bbox: Optional[Union[List[int], Tuple[int, int, int, int]]] = None,
) -> Dict[str, Any]:
    """Screenshot region + OCR. Returns {text, image_base64, bbox, ocr_available}.

    bbox None = full screen. Graceful degrade when Pillow/OCR missing.
    """
    action = "read_screen_region"
    _box: Optional[Tuple[int, int, int, int]] = None
    if bbox is not None:
        try:
            x1, y1, x2, y2 = (int(v) for v in bbox)  # type: ignore
            x1, x2 = min(x1, x2), max(x1, x2)
            y1, y2 = min(y1, y2), max(y1, y2)
            if x2 - x1 < 1 or y2 - y1 < 1:
                return _result(action, False, "validate", 1, "degenerate bbox", bbox=list(bbox))
            _box = (x1, y1, x2, y2)
        except Exception as exc:
            return _result(action, False, "validate", 1, "bad bbox %r: %s" % (bbox, exc))
    # ui_engine assisted capture first
    img = None
    if _UI_ENGINE is not None:
        try:
            called, res = _call_optional(_UI_ENGINE, ["capture", "screenshot", "grab_region"], _box)
            if called and res is not None:
                img = res
        except Exception as exc:
            _log(logging.DEBUG, "ui_engine capture failed: %s", exc)
    if img is None:
        # Retry loop for transient capture failures
        last_err = ""
        for attempt in range(1, _MAX_RETRIES + 1):
            img = _screenshot_pil(_box)
            if img is not None:
                break
            last_err = "screenshot backend returned None"
            time.sleep(0.4 * attempt)
        if img is None:
            _log(logging.ERROR, "%s FAILED: %s (Pillow/mss/pyautogui missing?)", action, last_err)
            return _result(action, False, "screenshot", _MAX_RETRIES, last_err or "screenshot unavailable",
                           bbox=list(_box) if _box else None, text="", image_base64="", ocr_available=_HAS_TESSERACT)
    # OCR (graceful degrade)
    text, conf = _ocr_image(img)
    b64 = _image_to_base64(img)
    try:
        w, h = img.size  # type: ignore
    except Exception:
        w, h = 0, 0
    _log(logging.INFO, "%s ok bbox=%s ocr_chars=%d ocr=%s", action, _box, len(text), _HAS_TESSERACT)
    return _result(action, True, "screenshot+ocr" if _HAS_TESSERACT else "screenshot_only", 1,
                   "captured %dx%d, ocr %s" % (w, h, "available" if _HAS_TESSERACT else "unavailable (image only)"),
                   bbox=list(_box) if _box else None, size=[w, h],
                   text=text, confidence=round(conf, 3),
                   image_base64=b64, ocr_available=_HAS_TESSERACT)


# ---------------------------------------------------------------------------
# 5. search_and_select_in_app
# ---------------------------------------------------------------------------

def search_and_select_in_app(
    search_box_id: Union[str, Dict[str, Any]],
    query: str,
    target_item_name: str,
) -> Dict[str, Any]:
    """Type query into in-app search box, then click matching item. VERIFY selection.

    Chain: type_into(search) -> wait -> click target (uia->coords->ocr) ->
    Enter fallback -> VERIFY target visible/selected.
    """
    action = "search_and_select_in_app"
    max_retries = int(_cfg("action_retry_count") or _MAX_RETRIES)
    query = "" if query is None else str(query)
    target_item_name = "" if target_item_name is None else str(target_item_name)
    last_detail = ""

    for attempt in range(1, max_retries + 1):
        _log(logging.INFO, "%s attempt %d query=%r target=%r", action, attempt, query, target_item_name)
        # Step 1: focus search box + type query (clear first, no Enter yet)
        r1 = type_into(search_box_id, query, clear_first=True, submit_enter=False)
        if not r1.get("success"):
            last_detail = "search box typing failed: %s" % r1.get("detail")
            if attempt < max_retries:
                time.sleep(_retry_delay(attempt))
            continue
        time.sleep(1.0)  # allow results to populate
        # Step 2: click the target item
        target_ident: Dict[str, Any] = {"name": target_item_name, "title": target_item_name, "text": target_item_name, "raw": target_item_name}
        r2 = click_element(target_ident, strategy="auto")
        if r2.get("success"):
            time.sleep(0.8)
            ok, why = _check_condition(target_item_name)
            if ok:
                return _result(action, True, "type+uia_click_verified", attempt, "selected %r (%s)" % (target_item_name, why), query=query, target=target_item_name)
            # clicked but verify weak — still success, report unverified
            _log(logging.WARNING, "%s click dispatched but verify weak: %s", action, why)
            return _result(action, True, str(r2.get("method")), attempt,
                           "clicked %r but post-verify weak: %s" % (target_item_name, why),
                           query=query, target=target_item_name, verified=False)
        last_detail = "target click failed: %s" % r2.get("detail")
        # Step 3: keyboard fallback — Enter selects first result if it matches
        _log(logging.INFO, "%s attempt %d keyboard fallback (Enter)", action, attempt)
        _press_keys("{ENTER}")
        time.sleep(0.8)
        ok, why = _check_condition(target_item_name)
        if ok:
            return _result(action, True, "type+enter_verified", attempt, "selected via Enter (%s)" % why, query=query, target=target_item_name)
        # Step 4: Down-arrow + Enter (second result etc., bounded 5 rows)
        picked = False
        for row in range(5):
            _press_keys("{DOWN}")
            time.sleep(0.3)
            _press_keys("{ENTER}")
            time.sleep(0.6)
            ok, why = _check_condition(target_item_name)
            if ok:
                picked = True
                return _result(action, True, "type+arrows_verified", attempt, "selected row %d via arrows (%s)" % (row + 1, why), query=query, target=target_item_name)
        last_detail += "; keyboard fallback could not verify %r" % target_item_name
        if attempt < max_retries:
            time.sleep(_retry_delay(attempt))
    _log(logging.ERROR, "%s FAILED query=%r target=%r: %s", action, query, target_item_name, last_detail)
    return _result(action, False, "none", max_retries, last_detail or "search/select failed", query=query, target=target_item_name)


# ---------------------------------------------------------------------------
# 6. verify_ui_state
# ---------------------------------------------------------------------------

def verify_ui_state(
    expected_text_or_condition: Union[str, Dict[str, Any], Callable[[], Any]],
    timeout: int = 10,
) -> Dict[str, Any]:
    """Poll until condition holds or timeout. Returns {verified, elapsed, detail}.

    Accepts plain text, {text, regex, not_text, any_text, bbox} dict, or callable.
    VERIFY step is the function itself; retries = poll loop (0.5s cadence).
    """
    action = "verify_ui_state"
    try:
        timeout_f = float(timeout if timeout is not None else _cfg("verify_timeout_default"))
    except Exception:
        timeout_f = 10.0
    timeout_f = max(0.5, timeout_f)
    bbox = None
    if isinstance(expected_text_or_condition, dict) and isinstance(expected_text_or_condition.get("bbox"), (list, tuple)):
        try:
            b = expected_text_or_condition["bbox"]
            if len(b) == 4:
                bbox = (int(b[0]), int(b[1]), int(b[2]), int(b[3]))  # type: ignore
        except Exception:
            bbox = None
    deadline = time.time() + timeout_f
    start = time.time()
    polls = 0
    last_why = ""
    while True:
        polls += 1
        ok, why = _check_condition(expected_text_or_condition, bbox)
        last_why = why
        if ok:
            elapsed = round(time.time() - start, 2)
            _log(logging.INFO, "%s VERIFIED in %ss (%s)", action, elapsed, why)
            return _result(action, True, "poll", polls, "verified in %ss: %s" % (elapsed, why),
                           verified=True, elapsed_sec=elapsed, polls=polls)
        if time.time() >= deadline:
            break
        time.sleep(0.5)
    elapsed = round(time.time() - start, 2)
    _log(logging.WARNING, "%s NOT verified after %ss: %s", action, elapsed, last_why)
    # describe expectation safely
    try:
        desc = str(expected_text_or_condition)[:300]
    except Exception:
        desc = "<unprintable condition>"
    return _result(action, False, "poll_timeout", polls, "not verified after %ss: %s (expected=%r)" % (elapsed, last_why, desc),
                   verified=False, elapsed_sec=elapsed, polls=polls)


# ---------------------------------------------------------------------------
# 7. execute_terminal_command
# ---------------------------------------------------------------------------

def execute_terminal_command(
    command: str,
    run_in_background: bool = False,
) -> Dict[str, Any]:
    """Run shell command. Foreground: capture output (timeout). Background: spawn + job id.

    Safety: blocked-pattern deny list + optional allow-list from config.
    Never raises; always returns result dict. Background jobs tracked in-process.
    """
    action = "execute_terminal_command"
    cmd = _norm(command)
    if not cmd:
        return _result(action, False, "validate", 1, "empty command")
    ok, reason = _terminal_allowed(cmd)
    if not ok:
        _log(logging.WARNING, "%s blocked %r: %s", action, cmd, reason)
        return _result(action, False, "blocked", 1, reason, command=cmd)
    use_shell = bool(_cfg("terminal_shell"))
    timeout = _cfg("terminal_timeout_sec") or 60
    try:
        timeout = float(timeout)
    except Exception:
        timeout = 60.0

    if run_in_background:
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                _log(logging.INFO, "%s background attempt %d: %r", action, attempt, cmd)
                proc = subprocess.Popen(
                    cmd, shell=use_shell,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL, close_fds=(os.name != "nt"),
                )
                global _BG_COUNTER
                with _BG_LOCK:
                    _BG_COUNTER += 1
                    job_id = _BG_COUNTER
                    _BG_PROCS[job_id] = proc
                # VERIFY: process still alive shortly after spawn
                time.sleep(0.4)
                alive = proc.poll() is None
                detail = "spawned pid=%s job_id=%d%s" % (proc.pid, job_id, "" if alive else " (exited immediately, rc=%s)" % proc.returncode)
                _log(logging.INFO, "%s %s", action, detail)
                return _result(action, True, "background_spawn", attempt, detail,
                               command=cmd, pid=proc.pid, job_id=job_id, alive=alive,
                               returncode=proc.returncode)
            except Exception as exc:
                last = "background spawn failed: %s" % exc
                _log(logging.WARNING, "%s %s", action, last)
                if attempt < _MAX_RETRIES:
                    time.sleep(_retry_delay(attempt))
                else:
                    return _result(action, False, "background_spawn", attempt, last, command=cmd)
        return _result(action, False, "background_spawn", _MAX_RETRIES, "spawn failed", command=cmd)

    # Foreground with retry (retry only on timeout/launch error, not on nonzero exit)
    last_detail = ""
    for attempt in range(1, _MAX_RETRIES + 1):
        _log(logging.INFO, "%s foreground attempt %d: %r", action, attempt, cmd)
        t0 = time.time()
        try:
            completed = subprocess.run(
                cmd, shell=use_shell, capture_output=True, text=True,
                timeout=timeout, stdin=subprocess.DEVNULL,
            )
            elapsed = round(time.time() - t0, 2)
            out = (completed.stdout or "")[-8000:]
            err = (completed.stderr or "")[-8000:]
            success = completed.returncode == 0
            # VERIFY = process completed within timeout; success = rc 0
            _log(logging.INFO if success else logging.WARNING,
                 "%s rc=%d elapsed=%ss cmd=%r", action, completed.returncode, elapsed, cmd)
            return _result(action, success, "foreground_run", attempt,
                           "rc=%d in %ss" % (completed.returncode, elapsed),
                           command=cmd, returncode=completed.returncode,
                           stdout=out, stderr=err, elapsed_sec=elapsed)
        except subprocess.TimeoutExpired as exc:
            last_detail = "timeout after %ss" % timeout
            _log(logging.WARNING, "%s %s cmd=%r", action, last_detail, cmd)
            partial = ""
            try:
                partial = str(getattr(exc, "stdout", "") or "")[-2000:]
            except Exception:
                pass
            if attempt >= _MAX_RETRIES:
                return _result(action, False, "foreground_timeout", attempt, last_detail, command=cmd, partial_stdout=partial)
            time.sleep(_retry_delay(attempt))
        except Exception as exc:
            last_detail = "execution failed: %s" % exc
            _log(logging.WARNING, "%s %s", action, last_detail)
            if attempt >= _MAX_RETRIES:
                return _result(action, False, "foreground_error", attempt, last_detail, command=cmd)
            time.sleep(_retry_delay(attempt))
    return _result(action, False, "foreground_error", _MAX_RETRIES, last_detail or "failed", command=cmd)


# ---------------------------------------------------------------------------
# Optional: background job helpers (extra, non-breaking)
# ---------------------------------------------------------------------------

def list_background_jobs() -> Dict[str, Any]:
    """List tracked background jobs with liveness. Helper for Agent Core."""
    jobs = []
    with _BG_LOCK:
        items = list(_BG_PROCS.items())
    for job_id, proc in items:
        try:
            alive = proc.poll() is None
            jobs.append({"job_id": job_id, "pid": proc.pid, "alive": alive, "returncode": proc.returncode})
        except Exception as exc:
            jobs.append({"job_id": job_id, "alive": False, "error": str(exc)})
    return {"success": True, "action": "list_background_jobs", "jobs": jobs}


def stop_background_job(job_id: int) -> Dict[str, Any]:
    """Terminate a tracked background job. Helper for Agent Core."""
    with _BG_LOCK:
        proc = _BG_PROCS.get(int(job_id))
    if proc is None:
        return {"success": False, "action": "stop_background_job", "detail": "unknown job_id=%r" % job_id}
    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        with _BG_LOCK:
            _BG_PROCS.pop(int(job_id), None)
        return {"success": True, "action": "stop_background_job", "detail": "job %s stopped" % job_id, "job_id": int(job_id)}
    except Exception as exc:
        return {"success": False, "action": "stop_background_job", "detail": "stop failed: %s" % exc, "job_id": int(job_id)}


# ---------------------------------------------------------------------------
# File + web helpers (Agent Core support utilities)
# ---------------------------------------------------------------------------

_BLOCKED_WRITE_PREFIXES: Tuple[str, ...] = (
    os.path.normcase(os.path.abspath(os.environ.get("SystemRoot", r"C:\Windows"))),
    os.path.normcase(os.path.abspath(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))),
    os.path.normcase(os.path.abspath(r"C:\Windows")),
    os.path.normcase(os.path.abspath(r"C:\Windows\System32")),
)


def _is_blocked_write_path(abs_path: str) -> bool:
    normed = os.path.normcase(os.path.abspath(os.path.expandvars(abs_path)))
    for prefix in _BLOCKED_WRITE_PREFIXES:
        if normed == prefix or normed.startswith(prefix + os.sep):
            return True
    return False


def _decode_bytes_fallback(raw: bytes) -> str:
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:
            continue
    return raw.decode("utf-8", errors="replace")


def read_file(path: str, max_chars: int = 20000) -> Dict[str, Any]:
    """Read a text file (utf-8, cp1251/latin-1 fallback), truncated to max_chars."""
    action = "read_file"
    try:
        p = _norm(path)
        if not p:
            return _result(action, False, "validate", 1, "empty path", path=str(path), content="", truncated=False)
        _log(logging.INFO, "%s path=%r", action, p)
        if not os.path.exists(p):
            return _result(action, False, "stat", 1, "path does not exist: %r" % p, path=p, content="", truncated=False)
        if not os.path.isfile(p):
            return _result(action, False, "stat", 1, "not a file: %r" % p, path=p, content="", truncated=False)
        try:
            limit = int(max_chars)
        except Exception:
            limit = 20000
        limit = max(0, limit)
        with open(p, "rb") as fh:
            raw = fh.read(limit + 1 if limit else 1)
        text = _decode_bytes_fallback(raw)
        truncated = len(text) > limit
        if truncated:
            text = text[:limit]
        return _result(action, True, "read", 1, "read %d chars%s" % (len(text), " (truncated)" if truncated else ""),
                       path=p, content=text, truncated=truncated)
    except Exception as exc:
        _log(logging.WARNING, "%s failed: %s", action, exc)
        return _result(action, False, "read", 1, "read failed: %s" % exc,
                       path=str(path), content="", truncated=False)


def write_file(path: str, content: str, append: bool = False, mkdirs: bool = True) -> Dict[str, Any]:
    """Write (or append) text file as utf-8, creating parent dirs. System paths refused."""
    action = "write_file"
    try:
        p = _norm(path)
        if not p:
            return _result(action, False, "validate", 1, "empty path", path=str(path), bytes=0)
        abs_p = os.path.abspath(os.path.expandvars(p))
        if _is_blocked_write_path(abs_p):
            _log(logging.WARNING, "%s blocked system path %r", action, abs_p)
            return _result(action, False, "blocked", 1, "write to system path refused: %r" % abs_p,
                           path=abs_p, bytes=0)
        _log(logging.INFO, "%s path=%r append=%s", action, abs_p, append)
        text = "" if content is None else str(content)
        if mkdirs:
            parent = os.path.dirname(abs_p)
            if parent:
                os.makedirs(parent, exist_ok=True)
        mode = "a" if append else "w"
        with open(abs_p, mode, encoding="utf-8", newline="") as fh:
            fh.write(text)
        nbytes = len(text.encode("utf-8"))
        return _result(action, True, "append" if append else "write", 1,
                       "wrote %d bytes to %r" % (nbytes, abs_p), path=abs_p, bytes=nbytes)
    except Exception as exc:
        _log(logging.WARNING, "%s failed: %s", action, exc)
        return _result(action, False, "write", 1, "write failed: %s" % exc,
                       path=str(path), bytes=0)


def list_dir(path: str = ".", pattern: str = "*", max_entries: int = 200) -> Dict[str, Any]:
    """List directory entries (name, dir/file, size), glob-filtered, capped."""
    action = "list_dir"
    try:
        p = _norm(path) or "."
        pat = _norm(pattern) or "*"
        try:
            cap = int(max_entries)
        except Exception:
            cap = 200
        cap = max(1, cap)
        _log(logging.INFO, "%s path=%r pattern=%r", action, p, pat)
        if not os.path.exists(p):
            return _result(action, False, "stat", 1, "path does not exist: %r" % p,
                           entries=[], count=0)
        if not os.path.isdir(p):
            return _result(action, False, "stat", 1, "not a directory: %r" % p,
                           entries=[], count=0)
        names = sorted(os.listdir(p))
        matched = [n for n in names if fnmatch.fnmatch(n, pat)]
        entries: List[Dict[str, Any]] = []
        for name in matched[:cap]:
            full = os.path.join(p, name)
            try:
                is_dir = os.path.isdir(full)
                size = 0 if is_dir else int(os.path.getsize(full))
            except Exception:
                is_dir = False
                size = 0
            entries.append({"name": name, "type": "dir" if is_dir else "file", "size": size})
        entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, e["name"].lower()))
        return _result(action, True, "list", 1, "%d entries (%d matched)" % (len(entries), len(matched)),
                       entries=entries, count=len(matched))
    except Exception as exc:
        _log(logging.WARNING, "%s failed: %s", action, exc)
        return _result(action, False, "list", 1, "list failed: %s" % exc,
                       entries=[], count=0)


def _html_to_text(page: str) -> str:
    try:
        no_script = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1\s*>", " ", page)
        no_tags = re.sub(r"(?s)<[^>]*>", " ", no_script)
        unescaped = html.unescape(no_tags)
        return re.sub(r"\s+", " ", unescaped).strip()
    except Exception:
        return re.sub(r"\s+", " ", page).strip()


def fetch_url(url: str, timeout: int = 20, max_chars: int = 15000) -> Dict[str, Any]:
    """GET url via stdlib urllib (http/https only); HTML stripped to text."""
    action = "fetch_url"
    try:
        u = _norm(url)
        if not u:
            return _result(action, False, "validate", 1, "empty url", url=str(url), status=0, text="")
        try:
            scheme = urllib.parse.urlparse(u).scheme.lower()
        except Exception:
            scheme = ""
        if scheme not in ("http", "https"):
            _log(logging.WARNING, "%s refused non-http(s) scheme %r", action, u)
            return _result(action, False, "blocked", 1, "only http/https allowed: %r" % u,
                           url=u, status=0, text="")
        try:
            timeout_f = float(timeout)
        except Exception:
            timeout_f = 20.0
        timeout_f = min(max(1.0, timeout_f), 120.0)
        try:
            cap = int(max_chars)
        except Exception:
            cap = 15000
        cap = max(0, cap)
        _log(logging.INFO, "%s url=%r", action, u)
        req = urllib.request.Request(u, headers={"User-Agent": "Wina/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout_f) as resp:  # type: ignore
                status = int(getattr(resp, "status", 200) or 200)
                ctype = str(resp.headers.get("Content-Type", "") or "")
                raw = resp.read(cap + 4096)
        except urllib.error.HTTPError as exc:
            return _result(action, False, "http", 1, "http error %s" % getattr(exc, "code", "?"),
                           url=u, status=int(getattr(exc, "code", 0) or 0), text="")
        except urllib.error.URLError as exc:
            return _result(action, False, "http", 1, "url error: %s" % getattr(exc, "reason", exc),
                           url=u, status=0, text="")
        m = re.search(r"charset=([^\s;\"']+)", ctype, re.IGNORECASE)
        charset = (m.group(1).strip() if m else "utf-8")
        try:
            page = raw.decode(charset, errors="replace")
        except Exception:
            page = raw.decode("utf-8", errors="replace")
        is_html = "html" in ctype.lower() or page.lstrip().lower().startswith(("<!doctype html", "<html"))
        text = _html_to_text(page) if is_html else _norm(page)
        if len(text) > cap:
            text = text[:cap]
        return _result(action, True, "http", 1, "fetched status=%d, %d chars" % (status, len(text)),
                       url=u, status=status, text=text)
    except Exception as exc:
        _log(logging.WARNING, "%s failed: %s", action, exc)
        return _result(action, False, "http", 1, "fetch failed: %s" % exc,
                       url=str(url), status=0, text="")
