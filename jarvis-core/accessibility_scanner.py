"""PROJECT JARVIS-CORE :: MODULE 3 : accessibility_scanner.py
Engine Alpha: UIA & Tree Reader (Windows 10/11, Python 3.11+).

Traverses the Windows UIA tree dynamically via pywinauto (UIA backend),
locates controls via multi-attribute queries, dumps a token-efficient
interactive-element tree for LLMs, and writes/clicks via UIA patterns
without moving the mouse.

Public API:
    scan_active_window, dump_tree, find_element, find_all,
    safe_click_uia, safe_set_value, get_texts, to_json
"""

from __future__ import annotations

import ctypes
import json
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple, Union

log = logging.getLogger("jarvis.accessibility_scanner")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(_h)
log.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Optional Windows / pywinauto imports (hard dependency on Windows, soft here
# so the module imports cleanly for docs / CI on other OSes).
# ---------------------------------------------------------------------------

_HAS_PYWINAUTO = False
_HAS_WIN32 = False
_HAS_COMTYPES = False

Desktop: Any = None
Application: Any = None
ElementNotFoundError: Any = Exception
COMError: Any = Exception

try:
    from pywinauto import Application as _App  # type: ignore
    from pywinauto import Desktop as _Desk  # type: ignore
    Desktop = _Desk
    Application = _App
    try:
        from pywinauto.findwindows import ElementNotFoundError as _ENF  # type: ignore
        ElementNotFoundError = _ENF
    except Exception:
        class ElementNotFoundError(Exception):  # type: ignore
            pass
    _HAS_PYWINAUTO = True
except Exception as _e:
    log.debug("pywinauto unavailable: %s", _e)

try:
    import win32gui  # type: ignore
    _HAS_WIN32 = True
except Exception:
    win32gui = None  # type: ignore

try:
    from comtypes import COMError as _COMError  # type: ignore
    COMError = _COMError
    _HAS_COMTYPES = True
except Exception:
    class COMError(Exception):  # type: ignore
        pass

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT: float = 5.0
DEFAULT_RETRIES: int = 3
DEFAULT_RETRY_DELAY: float = 0.25
DEFAULT_MAX_DEPTH: int = 8
DEFAULT_MAX_NODES: int = 600
NAME_TRUNCATE: int = 80

# Control types we consider "interactive" for LLM dumps.
INTERACTIVE_TYPES = frozenset({
    "Button", "CheckBox", "RadioButton", "SplitButton",
    "Edit", "Document", "ComboBox", "List", "ListItem",
    "DataItem", "DataGrid", "Table", "Tree", "TreeItem",
    "Tab", "TabItem", "Menu", "MenuBar", "MenuItem",
    "Hyperlink", "Spinner", "Slider", "ProgressBar",
    "ScrollBar", "Thumb", "Header", "HeaderItem",
    "ToolBar", "StatusBar", "Custom", "Group", "Pane",
    "Window", "TitleBar", "Calendar",
})

# Types whose text is worth harvesting in get_texts().
TEXTUAL_TYPES = frozenset({
    "Text", "Edit", "Document", "Static", "TitleBar",
    "Button", "CheckBox", "RadioButton", "MenuItem",
    "ListItem", "TreeItem", "TabItem", "Hyperlink",
    "ToolTip", "StatusBar", "HeaderItem", "ComboBox",
})

# UIA ControlTypeId -> name (UIAutomationClient.h)
_CONTROL_TYPE_ID_MAP: Dict[int, str] = {
    50000: "Button", 50001: "Calendar", 50002: "CheckBox",
    50003: "ComboBox", 50004: "Edit", 50005: "Hyperlink",
    50006: "Image", 50007: "ListItem", 50008: "List",
    50009: "Menu", 50010: "MenuBar", 50011: "MenuItem",
    50012: "ProgressBar", 50013: "RadioButton", 50014: "ScrollBar",
    50015: "Slider", 50016: "Spinner", 50017: "StatusBar",
    50018: "Tab", 50019: "TabItem", 50020: "Text",
    50021: "ToolBar", 50022: "ToolTip", 50023: "Tree",
    50024: "TreeItem", 50025: "Custom", 50026: "Group",
    50027: "Thumb", 50028: "DataGrid", 50029: "DataItem",
    50030: "Document", 50031: "SplitButton", 50032: "Window",
    50033: "Pane", 50034: "Header", 50035: "HeaderItem",
    50036: "Table", 50037: "TitleBar", 50038: "Separator",
    50039: "SemanticZoom", 50040: "AppBar",
}

Criteria = Dict[str, Any]
ElementLike = Any

__all__ = [
    "scan_active_window",
    "dump_tree",
    "find_element",
    "find_all",
    "safe_click_uia",
    "safe_set_value",
    "get_texts",
    "to_json",
    "control_type_name",
    "get_active_window",
    "resolve",
    "ScannerError",
    "ElementGoneError",
    "AccessDeniedError",
    "ElementInfo",
]


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ScannerError(RuntimeError):
    """Base scanner failure (wraps COM / stale-handle / timeout causes)."""


class ElementGoneError(ScannerError):
    """Control disappeared or handle went stale mid-operation."""


class AccessDeniedError(ScannerError):
    """Control or process denied UIA access (elevated / protected UI)."""


def _classify_native_error(exc: BaseException) -> Optional[ScannerError]:
    msg = f"{type(exc).__name__}: {exc}".lower()
    text = str(getattr(exc, "args", ""))
    blob = msg + " " + text.lower()
    denied_tokens = ("access is denied", "access denied", "0x80070005",
                     "uia_e_noaccess", "elementnotenabled", "not enabled")
    gone_tokens = ("stale", "invalid window handle", "0x80070578",
                   "uia_e_elementnotavailable", "elementnotavailable",
                   "element not found", "no such", "disconnected",
                   "0x80010108", "rpc_e_disconnected")
    if any(t in blob for t in denied_tokens):
        return AccessDeniedError(f"{type(exc).__name__}: {exc}")
    if any(t in blob for t in gone_tokens):
        return ElementGoneError(f"{type(exc).__name__}: {exc}")
    # COM HRESULT heuristics
    hresult = getattr(exc, "hresult", None)
    if hresult in (-2147024891, -2147220991):
        return AccessDeniedError(f"{type(exc).__name__}: {exc}")
    if hresult in (-2147180256, -2147417848, -2147023174):
        return ElementGoneError(f"{type(exc).__name__}: {exc}")
    return None


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class ElementInfo:
    """Token-efficient snapshot of one interactive element."""
    id: str
    name: str = ""
    control_type: str = ""
    rect: Tuple[int, int, int, int] = (0, 0, 0, 0)
    enabled: bool = True
    visible: bool = True
    # Extra locator hints (omitted from minimal JSON when empty).
    automation_id: str = ""
    class_name: str = ""
    depth: int = 0

    def minimal(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "control_type": self.control_type,
            "rect": list(self.rect),
            "enabled": self.enabled,
            "visible": self.visible,
        }
        if self.automation_id:
            d["automation_id"] = self.automation_id
        if self.class_name:
            d["class_name"] = self.class_name
        return d


# Cache: dump-id -> locator snapshot used by resolve().
_ID_CACHE: Dict[str, Dict[str, Any]] = {}
_ID_CACHE_LOCK = threading.Lock()
_DUMP_COUNTER = 0


def _cache_store(eid: str, snapshot: Dict[str, Any]) -> None:
    with _ID_CACHE_LOCK:
        _ID_CACHE[eid] = dict(snapshot)
        # Bound cache size.
        if len(_ID_CACHE) > 5000:
            for k in list(_ID_CACHE.keys())[:1000]:
                _ID_CACHE.pop(k, None)


def _cache_lookup(eid: str) -> Optional[Dict[str, Any]]:
    with _ID_CACHE_LOCK:
        v = _ID_CACHE.get(eid)
        return dict(v) if v else None


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _require_pywinauto() -> None:
    if not _HAS_PYWINAUTO:
        raise ScannerError(
            "pywinauto (UIA backend) is not installed. "
            "Install with: pip install pywinauto comtypes pywin32"
        )


def _sleep_poll(timeout: float, interval: float = 0.05) -> Iterable[float]:
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = deadline - time.monotonic()
        yield max(0.0, remaining)
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))


def _retry_call(fn: Callable[[], Any], tries: int = DEFAULT_RETRIES,
                delay: float = DEFAULT_RETRY_DELAY,
                backoff: float = 1.6) -> Any:
    last: BaseException | None = None
    d = delay
    for attempt in range(max(1, tries)):
        try:
            return fn()
        except (AccessDeniedError, ElementGoneError):
            raise
        except Exception as exc:  # noqa: BLE001 - classify below
            mapped = _classify_native_error(exc)
            if mapped is not None:
                mapped.__cause__ = exc
                raise mapped from exc
            last = exc
            if attempt >= tries - 1:
                break
            time.sleep(d)
            d *= backoff
    assert last is not None
    mapped = _classify_native_error(last)
    if mapped is not None:
        mapped.__cause__ = last
        raise mapped from last
    raise ScannerError(f"{type(last).__name__}: {last}") from last


def _truncate(s: str, limit: int = NAME_TRUNCATE) -> str:
    s = (s or "").strip().replace("\r", " ").replace("\n", " ")
    s = re.sub(r"\s{2,}", " ", s)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def control_type_name(ct: Any) -> str:
    """Normalize a pywinauto control_type (int id or str) to a clean name."""
    if ct is None:
        return ""
    if isinstance(ct, int):
        return _CONTROL_TYPE_ID_MAP.get(ct, f"Unknown({ct})")
    s = str(ct).strip()
    # pywinauto sometimes gives "Button" already; strip namespace junk.
    s = re.sub(r"^.*[#.:]", "", s)
    s = s.replace("Control", "").strip()
    if s.isdigit():
        try:
            return _CONTROL_TYPE_ID_MAP.get(int(s), s)
        except ValueError:
            return s
    return s or ""


def _safe_get(getter: Callable[[], Any], default: Any = None) -> Any:
    try:
        return getter()
    except Exception:
        return default


def _element_info_of(wrapper: Any) -> Any:
    ei = getattr(wrapper, "element_info", wrapper)
    return ei


def _rect_of(wrapper: Any) -> Tuple[int, int, int, int]:
    try:
        ei = _element_info_of(wrapper)
        r = getattr(ei, "rectangle", None)
        if r is None:
            return (0, 0, 0, 0)
        if isinstance(r, (tuple, list)) and len(r) == 4:
            return (int(r[0]), int(r[1]), int(r[2]), int(r[3]))
        # pywinauto RECT-like object
        left = int(getattr(r, "left", 0))
        top = int(getattr(r, "top", 0))
        right = int(getattr(r, "right", left))
        bottom = int(getattr(r, "bottom", top))
        return (left, top, right, bottom)
    except Exception:
        return (0, 0, 0, 0)


def _enabled_of(wrapper: Any) -> bool:
    ei = _element_info_of(wrapper)
    v = _safe_get(lambda: getattr(ei, "enabled", True), True)
    if isinstance(v, bool):
        return v
    try:
        # Some wrappers expose is_enabled()
        m = getattr(wrapper, "is_enabled", None)
        if callable(m):
            return bool(_safe_get(m, True))
    except Exception:
        pass
    return bool(v) if v is not None else True


def _visible_of(wrapper: Any) -> bool:
    ei = _element_info_of(wrapper)
    v = _safe_get(lambda: getattr(ei, "visible", True), True)
    if isinstance(v, bool):
        vis = v
    else:
        try:
            m = getattr(wrapper, "is_visible", None)
            vis = bool(_safe_get(m, True)) if callable(m) else True
        except Exception:
            vis = True
    if not vis:
        return False
    # Offscreen with zero-area rect is effectively invisible.
    l, t, r, b = _rect_of(wrapper)
    if r <= l or b <= t:
        # Keep visible=True for zero-rect containers? No: LLM should skip.
        # Window/Pane roots may legitimately report odd rects; caller decides.
        ct = control_type_name(_safe_get(lambda: getattr(ei, "control_type", ""), ""))
        if ct not in ("Window", "Pane", "TitleBar"):
            return False
    return True


def _name_of(wrapper: Any) -> str:
    ei = _element_info_of(wrapper)
    for attr in ("name", "title"):
        v = _safe_get(lambda a=attr: getattr(ei, a, ""), "")
        if v:
            return str(v)
    for meth in ("window_text", "texts"):
        m = getattr(wrapper, "window_text", None) if meth == "window_text" else getattr(wrapper, "texts", None)
        try:
            if callable(m):
                v = m()
                if isinstance(v, (list, tuple)):
                    v = " ".join([str(x) for x in v if x]).strip()
                if v:
                    return str(v)
        except Exception:
            continue
    return ""


def _aid_of(wrapper: Any) -> str:
    ei = _element_info_of(wrapper)
    return str(_safe_get(lambda: getattr(ei, "automation_id", ""), "") or "")


def _class_of(wrapper: Any) -> str:
    ei = _element_info_of(wrapper)
    return str(_safe_get(lambda: getattr(ei, "class_name", ""), "") or "")


def _runtime_id(wrapper: Any) -> str:
    ei = _element_info_of(wrapper)
    rid = _safe_get(lambda: getattr(ei, "runtime_id", None), None)
    try:
        if rid:
            return ".".join(str(int(x)) for x in list(rid))
    except Exception:
        pass
    h = _safe_get(lambda: getattr(ei, "handle", 0), 0) or 0
    return f"h{h}"


def _is_alive(wrapper: Any) -> bool:
    try:
        ei = _element_info_of(wrapper)
        # Touching a property forces a COM round-trip; stale handles raise.
        _ = getattr(ei, "control_type", None)
        _ = getattr(ei, "rectangle", None)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Window resolution
# ---------------------------------------------------------------------------

def _foreground_hwnd() -> int:
    if _HAS_WIN32 and win32gui is not None:
        try:
            return int(win32gui.GetForegroundWindow())
        except Exception:
            return 0
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        return int(user32.GetForegroundWindow())
    except Exception:
        return 0


def get_active_window(timeout: float = DEFAULT_TIMEOUT) -> Any:
    """Return the pywinauto UIA wrapper for the foreground top-level window."""
    _require_pywinauto()
    last: BaseException | None = None
    for _ in _sleep_poll(timeout):
        try:
            hwnd = _foreground_hwnd()
            if hwnd:
                try:
                    app = Application(backend="uia").connect(handle=hwnd)
                    w = app.window(handle=hwnd)
                    w.wait("exists ready", timeout=1)
                    if _is_alive(w):
                        return w
                except Exception:
                    pass  # fall through to Desktop fallback
            desk = Desktop(backend="uia")
            # active_only window query is the most direct pywinauto idiom.
            try:
                w = desk.window(active_only=True)
                if w is not None and _is_alive(w):
                    try:
                        w.wait("exists", timeout=1)
                    except Exception:
                        pass
                    return w
            except Exception:
                pass
            # Fallback: topmost visible window.
            try:
                wins = desk.windows(top_level_only=True, active_only=False)
                for w in wins:
                    try:
                        if _visible_of(w) and _is_alive(w):
                            return w
                    except Exception:
                        continue
            except Exception as exc:
                last = exc
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise ScannerError(
        f"Could not resolve active window within {timeout}s"
        + (f": {last}" if last else "")
    )


def _children_of(wrapper: Any) -> List[Any]:
    for meth in ("children", "descendants"):
        pass
    try:
        kids = wrapper.children()
        return list(kids or [])
    except Exception as exc:
        mapped = _classify_native_error(exc)
        if mapped is not None:
            raise mapped from exc
        # Some leaf controls raise on children(); treat as childless.
        return []


# ---------------------------------------------------------------------------
# Criteria matching (multi-attribute queries)
# ---------------------------------------------------------------------------

_CRITERIA_ALIASES: Dict[str, Tuple[str, ...]] = {
    "name": ("name", "title", "text"),
    "automation_id": ("automation_id", "auto_id", "aid"),
    "class_name": ("class_name", "class", "classname"),
    "control_type": ("control_type", "ctrl_type", "type"),
    "framework": ("framework_id", "framework"),
    "enabled": ("enabled",),
    "visible": ("visible",),
}


def _normalize_criteria(raw: Criteria) -> Dict[str, Any]:
    norm: Dict[str, Any] = {}
    for k, v in (raw or {}).items():
        kl = str(k).strip().lower()
        placed = False
        for canonical, aliases in _CRITERIA_ALIASES.items():
            if kl == canonical or kl in aliases:
                norm[canonical] = v
                placed = True
                break
        if not placed:
            norm[kl] = v
    return norm


def _matches(wrapper: Any, criteria: Dict[str, Any]) -> bool:
    if not criteria:
        return True
    try:
        name = _name_of(wrapper)
        aid = _aid_of(wrapper)
        cls = _class_of(wrapper)
        ct = control_type_name(_safe_get(
            lambda: getattr(_element_info_of(wrapper), "control_type", ""), ""))
    except Exception:
        return False

    for key, want in criteria.items():
        if want is None:
            continue
        if key == "name":
            if isinstance(want, re.Pattern):
                if not want.search(name or ""):
                    return False
            elif isinstance(want, str) and want.startswith("re:"):
                if not re.search(want[3:], name or "", re.IGNORECASE):
                    return False
            else:
                if str(want).lower() not in (name or "").lower():
                    # Exact-or-substring: substring match keeps LLM UX forgiving.
                    if str(want) != name:
                        return False
        elif key == "name_exact":
            if name != str(want):
                return False
        elif key == "name_regex":
            rx = want if isinstance(want, re.Pattern) else re.compile(str(want), re.IGNORECASE)
            if not rx.search(name or ""):
                return False
        elif key == "automation_id":
            if isinstance(want, re.Pattern):
                if not want.search(aid or ""):
                    return False
            else:
                if str(want) != aid:
                    # allow substring for convenience only when explicit wildcard
                    if str(want).lower() not in (aid or "").lower():
                        return False
                    if str(want) != aid and "*" not in str(want) and str(want).lower() == (aid or "").lower():
                        pass
                    elif str(want) != aid:
                        # strict: require exact unless case-insensitive equal
                        if str(want).lower() != (aid or "").lower():
                            return False
        elif key == "class_name":
            if str(want) != cls and str(want).lower() not in (cls or "").lower():
                return False
        elif key == "control_type":
            if isinstance(want, (list, tuple, set)):
                allowed = {control_type_name(x).lower() for x in want}
                if ct.lower() not in allowed:
                    return False
            else:
                if control_type_name(want).lower() != ct.lower():
                    return False
        elif key == "enabled":
            if _enabled_of(wrapper) != bool(want):
                return False
        elif key == "visible":
            if _visible_of(wrapper) != bool(want):
                return False
        elif key == "predicate":
            try:
                if callable(want) and not bool(want(wrapper)):
                    return False
            except Exception:
                return False
        else:
            # Unknown keys: compare against element_info attribute if present.
            try:
                actual = getattr(_element_info_of(wrapper), key, None)
                if callable(actual):
                    actual = actual()
                if str(actual) != str(want):
                    return False
            except Exception:
                return False
    return True


def _pywinauto_find_kwargs(criteria: Dict[str, Any]) -> Dict[str, Any]:
    """Translate normalized criteria to pywinauto child_window() kwargs."""
    kw: Dict[str, Any] = {}
    if "name" in criteria and isinstance(criteria["name"], str) and not criteria["name"].startswith("re:"):
        kw["title"] = criteria["name"]
    if "automation_id" in criteria and isinstance(criteria["automation_id"], str):
        kw["auto_id"] = criteria["automation_id"]
    if "class_name" in criteria and isinstance(criteria["class_name"], str):
        kw["class_name"] = criteria["class_name"]
    if "control_type" in criteria and isinstance(criteria["control_type"], str):
        kw["control_type"] = criteria["control_type"]
    return kw


# ---------------------------------------------------------------------------
# Core traversal / dump
# ---------------------------------------------------------------------------

def _iter_bfs(root: Any, max_depth: int, max_nodes: int,
              include_invisible: bool,
              control_filter: Optional[Iterable[str]]) -> Iterable[Tuple[Any, int]]:
    allowed = {c.lower() for c in control_filter} if control_filter else None
    queue: List[Tuple[Any, int]] = [(root, 0)]
    yielded = 0
    seen: set = set()
    while queue and yielded < max_nodes:
        node, depth = queue.pop(0)
        if depth > max_depth:
            continue
        try:
            marker = (id(node), _runtime_id(node))
        except Exception:
            marker = (id(node), "")
        if marker in seen:
            continue
        seen.add(marker)
        if depth > 0 or root is node:
            try:
                ct = control_type_name(_safe_get(
                    lambda: getattr(_element_info_of(node), "control_type", ""), ""))
            except Exception:
                continue
            vis = True
            try:
                vis = _visible_of(node)
            except Exception:
                vis = True
            if (include_invisible or vis) and (allowed is None or ct.lower() in allowed):
                yield node, depth
                yielded += 1
                if yielded >= max_nodes:
                    return
        if depth >= max_depth:
            continue
        try:
            kids = _children_of(node)
        except (AccessDeniedError, ElementGoneError):
            continue
        except Exception:
            continue
        for k in kids:
            queue.append((k, depth + 1))


def _snapshot(node: Any, eid: str, depth: int) -> ElementInfo:
    try:
        ct = control_type_name(_safe_get(
            lambda: getattr(_element_info_of(node), "control_type", ""), ""))
    except Exception:
        ct = ""
    try:
        name = _truncate(_name_of(node))
    except Exception:
        name = ""
    try:
        rect = _rect_of(node)
    except Exception:
        rect = (0, 0, 0, 0)
    try:
        enabled = _enabled_of(node)
    except Exception:
        enabled = True
    try:
        visible = _visible_of(node)
    except Exception:
        visible = True
    try:
        aid = _aid_of(node)
    except Exception:
        aid = ""
    try:
        cls = _class_of(node)
    except Exception:
        cls = ""
    return ElementInfo(id=eid, name=name, control_type=ct, rect=rect,
                       enabled=enabled, visible=visible,
                       automation_id=aid, class_name=cls, depth=depth)


def dump_tree(root: Optional[Any] = None,
              max_depth: int = DEFAULT_MAX_DEPTH,
              max_nodes: int = DEFAULT_MAX_NODES,
              include_invisible: bool = False,
              interactive_only: bool = True,
              control_filter: Optional[Iterable[str]] = None,
              timeout: float = DEFAULT_TIMEOUT) -> List[Dict[str, Any]]:
    """Recursively dump interactive elements under ``root`` (default: active window).

    Returns a list of token-efficient dicts with keys:
    id, name, control_type, rect, enabled, visible (+automation_id/class_name
    when non-empty). Dump ids (``e0``..``eN``) are cached to locator snapshots
    so :func:`resolve` can map them back to live controls.
    """
    _require_pywinauto()
    if root is None:
        root = get_active_window(timeout=timeout)
    if interactive_only and control_filter is None:
        control_filter = sorted(INTERACTIVE_TYPES)
    out: List[Dict[str, Any]] = []
    idx = 0
    for node, depth in _iter_bfs(root, max_depth=max_depth, max_nodes=max_nodes,
                                 include_invisible=include_invisible,
                                 control_filter=control_filter):
        # Skip the root window itself from interactive dumps unless asked.
        if depth == 0 and interactive_only:
            continue
        eid = f"e{idx}"
        try:
            info = _snapshot(node, eid, depth)
        except Exception as exc:
            log.debug("snapshot failed for node: %s", exc)
            continue
        if interactive_only and not info.visible and not include_invisible:
            continue
        # Drop nameless imageless leaves that waste LLM tokens, but keep
        # controls addressable by automation_id.
        if (interactive_only and not info.name and not info.automation_id
                and info.control_type in ("Image", "Separator", "Thumb", "Custom")):
            continue
        out.append(info.minimal())
        _cache_store(eid, {
            "name": info.name,
            "automation_id": info.automation_id,
            "class_name": info.class_name,
            "control_type": info.control_type,
            "rect": list(info.rect),
            "runtime": _safe_get(lambda: _runtime_id(node), ""),
        })
        idx += 1
    return out


def scan_active_window(max_depth: int = DEFAULT_MAX_DEPTH,
                       max_nodes: int = DEFAULT_MAX_NODES,
                       include_invisible: bool = False,
                       timeout: float = DEFAULT_TIMEOUT) -> Dict[str, Any]:
    """Dump the foreground window's interactive elements as LLM-ready JSON data.

    Returns ``{"window": {...meta...}, "elements": [...]}``.
    """
    _require_pywinauto()
    window = get_active_window(timeout=timeout)
    try:
        title = _truncate(_name_of(window), 120)
    except Exception:
        title = ""
    hwnd = int(_safe_get(lambda: getattr(_element_info_of(window), "handle", 0), 0) or 0)
    rect = _rect_of(window)
    try:
        proc = str(_safe_get(lambda: getattr(_element_info_of(window), "process_id", ""), "") or "")
    except Exception:
        proc = ""
    elements = dump_tree(root=window, max_depth=max_depth, max_nodes=max_nodes,
                         include_invisible=include_invisible,
                         interactive_only=True, timeout=timeout)
    return {
        "window": {
            "title": title,
            "hwnd": hwnd,
            "rect": list(rect),
            "process_id": proc,
            "element_count": len(elements),
        },
        "elements": elements,
    }


def to_json(payload: Union[Dict[str, Any], List[Any]]) -> str:
    """Serialize a scan/dump payload to compact JSON."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def find_all(root: Optional[Any] = None,
             criteria: Optional[Criteria] = None,
             max_depth: int = DEFAULT_MAX_DEPTH,
             max_nodes: int = DEFAULT_MAX_NODES,
             include_invisible: bool = True,
             timeout: float = DEFAULT_TIMEOUT,
             retries: int = DEFAULT_RETRIES) -> List[Any]:
    """Find all controls under ``root`` matching multi-attribute ``criteria``.

    Criteria keys (aliases accepted): name/title/text (substring or ``re:`` /
    compiled regex), name_exact, name_regex, automation_id/auto_id/aid,
    class_name, control_type (str or list), enabled, visible, predicate(callable).

    Fast path: tries pywinauto ``descendants(**kwargs)`` first, then falls back
    to a manual BFS with :func:`_matches` so regex/substring queries work.
    Never raises for "not found" — returns [].
    """
    _require_pywinauto()
    if root is None:
        try:
            root = get_active_window(timeout=timeout)
        except Exception as exc:
            log.warning("find_all: no active window: %s", exc)
            return []
    norm = _normalize_criteria(criteria or {})

    def _do() -> List[Any]:
        # Fast path via pywinauto native search for exact locator queries.
        kw = _pywinauto_find_kwargs(norm)
        if kw and not any(k in norm for k in ("name_regex", "predicate", "name_exact")):
            try:
                found = root.descendants(**kw)  # type: ignore[attr-defined]
                if found:
                    extra = {k: v for k, v in norm.items()
                             if k not in ("name", "automation_id", "class_name", "control_type")
                             or isinstance(v, re.Pattern)}
                    if extra:
                        return [w for w in found if _matches(w, norm)]
                    return list(found)
            except Exception as exc:
                mapped = _classify_native_error(exc)
                if isinstance(mapped, (AccessDeniedError, ElementGoneError)):
                    raise mapped from exc
                log.debug("native descendants search failed, BFS fallback: %s", exc)
        # Manual BFS fallback (handles regex / substring / mixed criteria).
        hits: List[Any] = []
        scanned = 0
        queue: List[Tuple[Any, int]] = [(root, 0)]
        seen: set = set()
        while queue and scanned < max_nodes:
            node, depth = queue.pop(0)
            scanned += 1
            try:
                marker = (id(node), _runtime_id(node))
            except Exception:
                marker = (id(node), "")
            if marker in seen:
                continue
            seen.add(marker)
            if depth > 0:
                try:
                    if _matches(node, norm):
                        if include_invisible or _visible_of(node):
                            hits.append(node)
                except Exception:
                    pass
            if depth < max_depth:
                try:
                    for k in _children_of(node):
                        queue.append((k, depth + 1))
                except (AccessDeniedError, ElementGoneError):
                    continue
                except Exception:
                    continue
        return hits

    try:
        return _retry_call(_do, tries=max(1, retries))
    except (AccessDeniedError, ElementGoneError) as exc:
        log.warning("find_all aborted: %s", exc)
        return []
    except ScannerError as exc:
        log.warning("find_all failed: %s", exc)
        return []


def find_element(root: Optional[Any] = None,
                 criteria: Optional[Criteria] = None,
                 timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES,
                 **kwargs: Any) -> Optional[Any]:
    """Find the first control matching ``criteria`` (dict and/or kwargs).

    Example: find_element({"control_type": "Button", "name": "Save"})
    """
    merged: Dict[str, Any] = dict(criteria or {})
    merged.update(kwargs)
    norm = _normalize_criteria(merged)
    # Fast path: single child_window lookup with wait.
    if root is not None:
        kw = _pywinauto_find_kwargs(norm)
        if kw and len(norm) == len(kw):
            try:
                def _fast() -> Any:
                    w = root.child_window(**kw)  # type: ignore[attr-defined]
                    w.wait("exists ready", timeout=min(timeout, 3.0))
                    return w
                cand = _retry_call(_fast, tries=max(1, retries))
                if cand is not None and _is_alive(cand):
                    return cand
            except Exception as exc:
                log.debug("child_window fast path missed: %s", exc)
    # Timed polling fallback so transient UI (menus, dialogs) can appear.
    deadline = time.monotonic() + max(0.1, timeout)
    last_list: List[Any] = []
    while True:
        last_list = find_all(root=root, criteria=norm, include_invisible=True,
                             timeout=1.0, retries=1)
        if last_list:
            best = _prefer_enabled_visible(last_list)
            return best
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.15)


def _prefer_enabled_visible(wrappers: List[Any]) -> Any:
    def _score(w: Any) -> int:
        s = 0
        try:
            if _enabled_of(w):
                s += 2
            if _visible_of(w):
                s += 1
        except Exception:
            pass
        return s
    return sorted(wrappers, key=_score, reverse=True)[0]


def resolve(target: Union[str, Criteria, Any],
            root: Optional[Any] = None,
            timeout: float = DEFAULT_TIMEOUT) -> Optional[Any]:
    """Resolve a dump-id (``e12``), criteria dict, or live wrapper to a wrapper."""
    _require_pywinauto()
    if target is None:
        return None
    # Already a wrapper?
    if hasattr(target, "element_info") or hasattr(target, "iface_value"):
        return target if _is_alive(target) else None
    if isinstance(target, str):
        snap = _cache_lookup(target)
        if snap is not None:
            crit: Criteria = {}
            if snap.get("automation_id"):
                crit["automation_id"] = snap["automation_id"]
            elif snap.get("name"):
                crit["name"] = snap["name"]
            if snap.get("control_type"):
                # Combine: automation_id + type is highly selective.
                if "automation_id" in crit:
                    crit["control_type"] = snap["control_type"]
            found = find_element(root=root, criteria=crit, timeout=min(timeout, 3.0))
            if found is not None:
                return found
            # Retry by rect proximity is out of scope; fall back to name-only.
            if snap.get("name"):
                return find_element(root=root, criteria={"name": snap["name"]},
                                    timeout=min(timeout, 2.0))
            return None
        # Plain string = name query.
        return find_element(root=root, criteria={"name": target}, timeout=timeout)
    if isinstance(target, dict):
        return find_element(root=root, criteria=target, timeout=timeout)
    return None


# ---------------------------------------------------------------------------
# Pattern-level actions (no mouse movement)
# ---------------------------------------------------------------------------

def _invoke_patterns(wrapper: Any) -> List[str]:
    tried: List[str] = []

    def _try(label: str, fn: Callable[[], Any]) -> bool:
        try:
            fn()
            tried.append(label + ":ok")
            return True
        except Exception as exc:
            mapped = _classify_native_error(exc)
            if isinstance(mapped, (AccessDeniedError, ElementGoneError)):
                raise mapped from exc
            tried.append(f"{label}:fail({type(exc).__name__})")
            return False

    # 1. InvokePattern
    try:
        iface = getattr(wrapper, "iface_invoke", None)
        if iface is not None:
            if _try("InvokePattern", lambda: iface.Invoke()):
                return tried
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 2. pywinauto-level invoke()
    try:
        inv = getattr(wrapper, "invoke", None)
        if callable(inv):
            if _try("wrapper.invoke", lambda: inv()):
                return tried
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 3. SelectionItemPattern
    try:
        si = getattr(wrapper, "iface_selection_item", None)
        if si is not None:
            if _try("SelectionItem.Select", lambda: si.Select()):
                return tried
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 4. TogglePattern
    try:
        tg = getattr(wrapper, "iface_toggle", None)
        if tg is not None:
            if _try("TogglePattern.Toggle", lambda: tg.Toggle()):
                return tried
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 5. ExpandCollapsePattern (menus / combos)
    try:
        ec = getattr(wrapper, "iface_expand_collapse", None)
        if ec is not None:
            def _expand() -> None:
                try:
                    ec.Expand()
                except Exception:
                    pass
            if _try("ExpandCollapse.Expand", _expand):
                return tried
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 6. LegacyIAccessible DoDefaultAction
    try:
        leg = getattr(wrapper, "iface_legacy_iaccessible", None)
        if leg is not None:
            if _try("LegacyIAccessible.DoDefaultAction", lambda: leg.DoDefaultAction()):
                return tried
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    return tried


def safe_click_uia(target: Union[str, Criteria, Any],
                   root: Optional[Any] = None,
                   timeout: float = DEFAULT_TIMEOUT,
                   retries: int = DEFAULT_RETRIES,
                   allow_mouse_fallback: bool = False) -> bool:
    """Click/activate a control via UIA patterns without moving the mouse.

    Returns True on success, False when the element is missing, disabled, or
    all patterns failed. Raises AccessDeniedError for protected UI.
    Mouse fallback (``click_input``) is disabled by default; opt in explicitly.
    """
    _require_pywinauto()
    el = resolve(target, root=root, timeout=timeout) if not hasattr(target, "element_info") else target
    if el is None:
        log.warning("safe_click_uia: element not found: %r", target if isinstance(target, (str, dict)) else "?")
        return False

    def _do() -> bool:
        if not _is_alive(el):
            raise ElementGoneError("element handle is stale")
        try:
            if not _enabled_of(el):
                log.warning("safe_click_uia: element disabled; refusing click")
                return False
        except Exception:
            pass
        # Ensure visible on screen; scroll into view when supported.
        try:
            scroll = getattr(el, "iface_scroll_item", None)
            if scroll is not None:
                try:
                    scroll.ScrollIntoView()
                except Exception:
                    pass
        except Exception:
            pass
        tried = _invoke_patterns(el)
        if any(s.endswith(":ok") for s in tried):
            # Verify enabled-state side effects where cheap (toggle flips).
            return True
        if allow_mouse_fallback:
            try:
                click_input = getattr(el, "click_input", None)
                if callable(click_input):
                    click_input(button="left", absolute=False)
                    log.warning("safe_click_uia: used mouse fallback click_input")
                    return True
            except Exception as exc:
                mapped = _classify_native_error(exc)
                if mapped is not None:
                    raise mapped from exc
                log.debug("mouse fallback failed: %s", exc)
        log.warning("safe_click_uia: all UIA patterns failed [%s]", " | ".join(tried) or "no patterns")
        return False

    try:
        return bool(_retry_call(_do, tries=max(1, retries)))
    except (AccessDeniedError, ElementGoneError):
        raise
    except ScannerError as exc:
        log.warning("safe_click_uia failed: %s", exc)
        return False


def _value_via_patterns(wrapper: Any, value: str) -> Tuple[bool, List[str]]:
    log_steps: List[str] = []

    # 1. ValuePattern
    try:
        iface = getattr(wrapper, "iface_value", None)
        if iface is not None:
            try:
                try:
                    readonly = bool(iface.IsReadOnly)
                except Exception:
                    readonly = False
                if readonly:
                    log_steps.append("ValuePattern:read-only")
                else:
                    iface.SetValue(str(value))
                    log_steps.append("ValuePattern:SetValue:ok")
                    return True, log_steps
            except Exception as exc:
                mapped = _classify_native_error(exc)
                if mapped is not None:
                    raise mapped from exc
                log_steps.append(f"ValuePattern:fail({type(exc).__name__})")
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 2. LegacyIAccessiblePattern
    try:
        leg = getattr(wrapper, "iface_legacy_iaccessible", None)
        if leg is not None:
            try:
                leg.SetValue(str(value))
                log_steps.append("LegacyIAccessible:SetValue:ok")
                return True, log_steps
            except Exception as exc:
                mapped = _classify_native_error(exc)
                if mapped is not None:
                    raise mapped from exc
                log_steps.append(f"LegacyIAccessible:fail({type(exc).__name__})")
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 3. pywinauto set_edit_text (UIA backend routes to ValuePattern; no mouse).
    try:
        setter = getattr(wrapper, "set_edit_text", None)
        if callable(setter):
            try:
                setter(str(value))
                log_steps.append("set_edit_text:ok")
                return True, log_steps
            except Exception as exc:
                mapped = _classify_native_error(exc)
                if mapped is not None:
                    raise mapped from exc
                log_steps.append(f"set_edit_text:fail({type(exc).__name__})")
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    # 4. RangeValue / Spinner fallback via iface_range_value
    try:
        rv = getattr(wrapper, "iface_range_value", None)
        if rv is not None:
            try:
                num = float(str(value).strip())
                try:
                    rv.SetValue(num)
                    log_steps.append("RangeValue:SetValue:ok")
                    return True, log_steps
                except Exception as exc:
                    mapped = _classify_native_error(exc)
                    if mapped is not None:
                        raise mapped from exc
                    log_steps.append(f"RangeValue:fail({type(exc).__name__})")
            except ValueError:
                log_steps.append("RangeValue:skip(non-numeric)")
    except (AccessDeniedError, ElementGoneError):
        raise
    except Exception:
        pass
    return False, log_steps


def safe_set_value(target: Union[str, Criteria, Any],
                   value: str,
                   root: Optional[Any] = None,
                   timeout: float = DEFAULT_TIMEOUT,
                   retries: int = DEFAULT_RETRIES,
                   verify: bool = True,
                   clear_first: bool = True) -> bool:
    """Set an Edit/Document/ComboBox value via ValuePattern (no mouse/keyboard).

    Returns True on success (and optional read-back verification). Returns
    False for missing/disabled/read-only controls instead of raising, except
    for access-denied / stale-handle conditions which raise.
    """
    _require_pywinauto()
    el = resolve(target, root=root, timeout=timeout) if not hasattr(target, "element_info") else target
    if el is None:
        log.warning("safe_set_value: element not found: %r", target if isinstance(target, (str, dict)) else "?")
        return False

    def _do() -> bool:
        if not _is_alive(el):
            raise ElementGoneError("element handle is stale")
        try:
            if not _enabled_of(el):
                log.warning("safe_set_value: element disabled; refusing write")
                return False
        except Exception:
            pass
        try:
            iface = getattr(el, "iface_value", None)
            if iface is not None and bool(_safe_get(lambda: iface.IsReadOnly, False)):
                log.warning("safe_set_value: control is read-only")
                return False
        except Exception:
            pass
        if clear_first:
            try:
                ok, _ = _value_via_patterns(el, "")
                if not ok:
                    # Best-effort select-all+clear via set path only; never send keys.
                    try:
                        sel = getattr(el, "iface_text", None)
                        _ = sel  # reserved: TextPattern selection not forced
                    except Exception:
                        pass
            except Exception:
                pass
        ok, steps = _value_via_patterns(el, str(value))
        if not ok:
            log.warning("safe_set_value: all write patterns failed [%s]", " | ".join(steps) or "no patterns")
            return False
        if verify:
            try:
                back = _read_value(el)
                if back is not None and back.strip() == str(value).strip():
                    return True
                # Lenient: value pattern accepted but read-back differs
                # (e.g. masked password boxes report bullets).
                ct = control_type_name(_safe_get(
                    lambda: getattr(_element_info_of(el), "control_type", ""), ""))
                cls = _class_of(el).lower()
                if "password" in cls or "password" in (_name_of(el) or "").lower() or ct == "Edit":
                    try:
                        is_pwd = bool(_safe_get(
                            lambda: getattr(_element_info_of(el), "password", False), False))
                    except Exception:
                        is_pwd = False
                    if is_pwd or back is not None:
                        log.info("safe_set_value: write accepted (read-back differs, likely masked)")
                        return True
                log.warning("safe_set_value: verify mismatch (wrote %r, read %r)", value, back)
                return False
            except Exception as exc:
                log.debug("safe_set_value verify skipped: %s", exc)
                return True
        return True

    try:
        return bool(_retry_call(_do, tries=max(1, retries)))
    except (AccessDeniedError, ElementGoneError):
        raise
    except ScannerError as exc:
        log.warning("safe_set_value failed: %s", exc)
        return False


def _read_value(wrapper: Any) -> Optional[str]:
    for getter in (
        lambda: getattr(wrapper, "iface_value", None) and getattr(wrapper.iface_value, "Value"),
        lambda: getattr(wrapper, "window_text", lambda: None)(),
        lambda: getattr(wrapper, "texts", lambda: None)(),
        lambda: _name_of(wrapper),
    ):
        try:
            v = getter()
            if isinstance(v, (list, tuple)):
                v = " ".join(str(x) for x in v if x)
            if v is not None and str(v) != "":
                return str(v)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# Text harvesting
# ---------------------------------------------------------------------------

def get_texts(root: Optional[Any] = None,
              max_depth: int = DEFAULT_MAX_DEPTH,
              max_nodes: int = DEFAULT_MAX_NODES,
              max_chars: int = 4000,
              include_invisible: bool = False,
              timeout: float = DEFAULT_TIMEOUT) -> List[str]:
    """Collect visible text strings (labels, edits, list/tab items) under root."""
    _require_pywinauto()
    if root is None:
        try:
            root = get_active_window(timeout=timeout)
        except Exception as exc:
            log.warning("get_texts: no active window: %s", exc)
            return []
    texts: List[str] = []
    used = 0
    for node, _depth in _iter_bfs(root, max_depth=max_depth, max_nodes=max_nodes,
                                  include_invisible=include_invisible,
                                  control_filter=None):
        try:
            ct = control_type_name(_safe_get(
                lambda: getattr(_element_info_of(node), "control_type", ""), ""))
        except Exception:
            continue
        if ct not in TEXTUAL_TYPES:
            continue
        try:
            t = (_read_value(node) or "").strip()
        except Exception:
            continue
        if not t:
            continue
        t = re.sub(r"\s{2,}", " ", t.replace("\r", " ").replace("\n", " ")).strip()
        if not t or t in texts:
            continue
        texts.append(t)
        used += len(t) + 1
        if used >= max_chars or len(texts) >= 300:
            break
    return texts


# ---------------------------------------------------------------------------
# Debug entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        payload = scan_active_window()
        print(to_json(payload)[:4000])
        print(f"\n... ({payload['window']['element_count']} elements)")
        print("sample texts:", get_texts()[:5])
    except ScannerError as _exc:
        log.error("scan failed: %s", _exc)
        raise SystemExit(1)
