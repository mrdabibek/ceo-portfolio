"""WINA :: config.py :: MODULE 1
Omnipotent Autonomous Desktop Agent — central configuration.

Covers: hardware params, display / DPI handling, timeouts, screenshot
buffer paths, execution throttle constants, model endpoint configs
(Ollama / local models / LLM API proxies / multi-providers), daemon
queue settings, env-var overrides, logging, and filesystem bootstrap.

Python 3.11+, Windows 10 & 11 primary. Degrades gracefully on other OSes
for development (DPI/monitor detection falls back to sane defaults).

Env override prefix: ``WINA_`` (e.g. ``WINA_OLLAMA_HOST``).
Legacy ``JARVIS_`` variables are still honoured as fallback for
backward compatibility: ``WINA_`` wins when both are set.
"""

from __future__ import annotations

import ctypes
import json
import logging
import logging.handlers
import os
import platform
import random
import socket
import sys
import time
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Mapping

__all__ = [
    "APP_NAME",
    "APP_VERSION",
    "BASE_DPI",
    "REFERENCE_RESOLUTION",
    "MAX_RETRIES",
    "RETRY_BACKOFF_BASE_S",
    "RETRY_BACKOFF_MAX_S",
    "VERIFY_TIMEOUT_S",
    "ACTION_TIMEOUT_S",
    "AppPaths",
    "DisplayMonitor",
    "DisplaySettings",
    "DpiSettings",
    "TimeoutSettings",
    "ThrottleSettings",
    "ScreenshotSettings",
    "HardwareConfig",
    "OllamaConfig",
    "LocalModelConfig",
    "LlmProxyConfig",
    "ProvidersConfig",
    "ProviderConfig",
    "DaemonConfig",
    "LoggingConfig",
    "JarvisConfig",
    "WinaConfig",
    "get_config",
    "reload_config",
    "get_system_dpi",
    "get_scaling_multiplier",
    "get_monitors",
    "get_primary_resolution",
    "adapt_point_to_current",
    "adapt_point_to_reference",
    "scale_value_for_dpi",
    "configure_logging",
    "ensure_directories",
    "throttle_delay_with_jitter",
    "next_screenshot_path",
]

# ---------------------------------------------------------------------------
# Identity / global constants
# ---------------------------------------------------------------------------

APP_NAME: Final[str] = "WINA"
APP_VERSION: Final[str] = "1.0.0"
ENV_PREFIX: Final[str] = "WINA_"
LEGACY_ENV_PREFIX: Final[str] = "JARVIS_"

BASE_DPI: Final[int] = 96
REFERENCE_RESOLUTION: Final[tuple[int, int]] = (1920, 1080)

# Retry / verification policy (hard requirement: 3 retries).
MAX_RETRIES: Final[int] = 3
RETRY_BACKOFF_BASE_S: Final[float] = 0.5
RETRY_BACKOFF_MAX_S: Final[float] = 8.0

# Verify / action timeouts (seconds).
VERIFY_TIMEOUT_S: Final[float] = 10.0
ACTION_TIMEOUT_S: Final[float] = 30.0
SCREENSHOT_TIMEOUT_S: Final[float] = 8.0
LLM_REQUEST_TIMEOUT_S: Final[float] = 120.0
LLM_STREAM_TIMEOUT_S: Final[float] = 300.0
WINDOW_SETTLE_TIMEOUT_S: Final[float] = 5.0
APP_LAUNCH_TIMEOUT_S: Final[float] = 25.0
NETWORK_PROBE_TIMEOUT_S: Final[float] = 6.0

# Execution throttle constants.
MIN_ACTION_INTERVAL_MS: Final[int] = 180
MAX_ACTIONS_PER_MINUTE: Final[int] = 120
DEFAULT_JITTER_MS: Final[int] = 90
MAX_JITTER_MS: Final[int] = 350
FAILURE_COOLDOWN_S: Final[float] = 2.0
BURST_LIMIT: Final[int] = 8
BURST_WINDOW_S: Final[float] = 10.0

_IS_WINDOWS: Final[bool] = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Env helpers (WINA_ primary, JARVIS_ legacy fallback)
# ---------------------------------------------------------------------------

def _lookup_env(key: str) -> str | None:
    """Return raw env value for ``key``: ``WINA_`` first, then ``JARVIS_``."""
    val = os.environ.get(f"{ENV_PREFIX}{key}")
    if val is not None and val.strip():
        return val
    legacy = os.environ.get(f"{LEGACY_ENV_PREFIX}{key}")
    if legacy is not None and legacy.strip():
        return legacy
    return None


def _env(key: str, default: str = "") -> str:
    raw = _lookup_env(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip() or default


def _env_str(key: str, default: str) -> str:
    val = _lookup_env(key)
    if val is None or not val.strip():
        return default
    return val.strip()


def _env_int(key: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = _lookup_env(key)
    if raw is None or not raw.strip():
        return default
    try:
        val = int(raw.strip())
    except ValueError:
        return default
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def _env_float(key: str, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    raw = _lookup_env(key)
    if raw is None or not raw.strip():
        return default
    try:
        val = float(raw.strip())
    except ValueError:
        return default
    if minimum is not None:
        val = max(minimum, val)
    if maximum is not None:
        val = min(maximum, val)
    return val


def _env_bool(key: str, default: bool) -> bool:
    raw = _lookup_env(key)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on", "enable", "enabled")


def _env_path(key: str, default: Path) -> Path:
    raw = _lookup_env(key)
    if raw is None or not raw.strip():
        return default
    return Path(os.path.expandvars(raw.strip())).expanduser()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _default_base_dir() -> Path:
    """Resolve the canonical per-user data directory (WINA)."""
    override = _lookup_env("BASE_DIR")
    if override is not None and override.strip():
        return Path(os.path.expandvars(override.strip())).expanduser()
    if _IS_WINDOWS:
        local_app = os.environ.get("LOCALAPPDATA", "").strip()
        if local_app:
            return Path(local_app) / "WinaCore"
        return Path.home() / "AppData" / "Local" / "WinaCore"
    return Path.home() / ".wina-core"


@dataclass(slots=True)
class AppPaths:
    """All on-disk locations owned by WINA. Created on bootstrap."""

    base_dir: Path = field(default_factory=_default_base_dir)
    logs_dir: Path = field(init=False)
    screenshots_dir: Path = field(init=False)
    screenshot_buffer_dir: Path = field(init=False)
    models_dir: Path = field(init=False)
    cache_dir: Path = field(init=False)
    db_dir: Path = field(init=False)
    tmp_dir: Path = field(init=False)
    profiles_dir: Path = field(init=False)
    queue_dir: Path = field(init=False)
    done_dir: Path = field(init=False)
    database_file: Path = field(init=False)
    config_file: Path = field(init=False)
    log_file: Path = field(init=False)
    pid_file: Path = field(init=False)

    def __post_init__(self) -> None:
        base = self.base_dir.expanduser()
        object.__setattr__(self, "base_dir", base)
        object.__setattr__(self, "logs_dir", base / "logs")
        object.__setattr__(self, "screenshots_dir", base / "screenshots")
        object.__setattr__(self, "screenshot_buffer_dir", base / "screenshots" / "_buffer")
        object.__setattr__(self, "models_dir", base / "models")
        object.__setattr__(self, "cache_dir", base / "cache")
        object.__setattr__(self, "db_dir", base / "db")
        object.__setattr__(self, "tmp_dir", base / "tmp")
        object.__setattr__(self, "profiles_dir", base / "profiles")
        object.__setattr__(self, "queue_dir", base / "queue")
        object.__setattr__(self, "done_dir", base / "done")
        object.__setattr__(self, "database_file", base / "db" / "wina.sqlite3")
        object.__setattr__(self, "config_file", base / "wina.json")
        object.__setattr__(self, "log_file", base / "logs" / "wina.log")
        object.__setattr__(self, "pid_file", base / "wina.pid")

    def all_directories(self) -> list[Path]:
        """Return every directory that must exist (files excluded)."""
        return [
            self.base_dir,
            self.logs_dir,
            self.screenshots_dir,
            self.screenshot_buffer_dir,
            self.models_dir,
            self.cache_dir,
            self.db_dir,
            self.tmp_dir,
            self.profiles_dir,
            self.queue_dir,
            self.done_dir,
        ]


def ensure_directories(paths: AppPaths) -> AppPaths:
    """Create every directory in :class:`AppPaths` (idempotent)."""
    for directory in paths.all_directories():
        directory.mkdir(parents=True, exist_ok=True)
    return paths


# ---------------------------------------------------------------------------
# Display / DPI
# ---------------------------------------------------------------------------

@dataclass(slots=True, frozen=True)
class DisplayMonitor:
    """Single physical/logical monitor description."""

    index: int
    name: str
    left: int
    top: int
    right: int
    bottom: int
    is_primary: bool
    dpi_x: int = BASE_DPI
    dpi_y: int = BASE_DPI

    @property
    def width(self) -> int:
        """Monitor width in physical pixels."""
        return max(0, self.right - self.left)

    @property
    def height(self) -> int:
        """Monitor height in physical pixels."""
        return max(0, self.bottom - self.top)

    @property
    def scaling(self) -> float:
        """DPI scaling multiplier relative to 96 DPI."""
        return round(self.dpi_x / BASE_DPI, 4) if self.dpi_x else 1.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable mapping."""
        return {
            "index": self.index,
            "name": self.name,
            "left": self.left,
            "top": self.top,
            "right": self.right,
            "bottom": self.bottom,
            "width": self.width,
            "height": self.height,
            "is_primary": self.is_primary,
            "dpi_x": self.dpi_x,
            "dpi_y": self.dpi_y,
            "scaling": self.scaling,
        }


@dataclass(slots=True)
class DpiSettings:
    """DPI awareness and scaling policy."""

    base_dpi: int = BASE_DPI
    system_dpi: int = BASE_DPI
    awareness_mode: str = "per_monitor_v2"  # system | per_monitor | per_monitor_v2
    max_supported_scaling: float = 3.0
    min_supported_scaling: float = 1.0
    round_scaling_to: float = 0.25

    @property
    def scaling_multiplier(self) -> float:
        """System-wide scaling multiplier clamped and quantised."""
        raw = self.system_dpi / self.base_dpi if self.base_dpi else 1.0
        raw = min(max(raw, self.min_supported_scaling), self.max_supported_scaling)
        step = self.round_scaling_to
        if step and step > 0:
            raw = round(round(raw / step) * step, 4)
        return raw


@dataclass(slots=True)
class DisplaySettings:
    """Multi-monitor defaults and resolution-adapter policy."""

    reference_width: int = REFERENCE_RESOLUTION[0]
    reference_height: int = REFERENCE_RESOLUTION[1]
    assume_primary_if_detection_fails: bool = True
    fallback_resolution: tuple[int, int] = (1920, 1080)
    virtual_desktop_origin: tuple[int, int] = (0, 0)
    coordinate_clamp_to_union: bool = True
    monitors: list[DisplayMonitor] = field(default_factory=list)

    def primary(self) -> DisplayMonitor | None:
        """Return the primary monitor, or the first one, or None."""
        if not self.monitors:
            return None
        for mon in self.monitors:
            if mon.is_primary:
                return mon
        return self.monitors[0]

    def union_rect(self) -> tuple[int, int, int, int]:
        """Return (left, top, right, bottom) covering all monitors."""
        if not self.monitors:
            w, h = self.fallback_resolution
            return (0, 0, w, h)
        left = min(m.left for m in self.monitors)
        top = min(m.top for m in self.monitors)
        right = max(m.right for m in self.monitors)
        bottom = max(m.bottom for m in self.monitors)
        return (left, top, right, bottom)


def _set_process_dpi_awareness() -> None:
    """Best-effort opt-in to per-monitor V2 DPI awareness on Windows."""
    if not _IS_WINDOWS:
        return
    try:
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        # PROCESS_PER_MONITOR_DPI_AWARE_V2 = 2
        shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
    except Exception:
        pass


def get_system_dpi() -> int:
    """Return the system DPI (96 = 100%). Falls back to 96 on failure."""
    if _IS_WINDOWS:
        try:
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            try:
                get_dpi = user32.GetDpiForSystem
                get_dpi.restype = ctypes.c_uint
                dpi = int(get_dpi())
                if 48 <= dpi <= 576:
                    return dpi
            except Exception:
                pass
            hdc = user32.GetDC(None)
            if hdc:
                try:
                    gdi32 = ctypes.windll.gdi32  # type: ignore[attr-defined]
                    LOGPIXELSX = 88
                    dpi = int(gdi32.GetDeviceCaps(hdc, LOGPIXELSX))
                    if 48 <= dpi <= 576:
                        return dpi
                finally:
                    user32.ReleaseDC(None, hdc)
        except Exception:
            pass
    env_dpi = _env_int("SYSTEM_DPI", 0, minimum=48, maximum=576)
    if env_dpi:
        return env_dpi
    return BASE_DPI


def get_scaling_multiplier(dpi: int | None = None) -> float:
    """Convert a DPI value to a scaling multiplier (1.0 == 96 DPI)."""
    value = dpi if dpi is not None else get_system_dpi()
    if value <= 0:
        return 1.0
    return round(value / BASE_DPI, 4)


def _get_monitor_dpi_windows(handle: int) -> tuple[int, int]:
    """Query per-monitor DPI via Shcore; fall back to system DPI."""
    default = get_system_dpi()
    try:
        shcore = ctypes.windll.shcore  # type: ignore[attr-defined]
        MDT_EFFECTIVE_DPI = 0
        dpi_x = ctypes.c_uint(0)
        dpi_y = ctypes.c_uint(0)
        rc = shcore.GetDpiForMonitor(
            ctypes.c_void_p(handle), ctypes.c_int(MDT_EFFECTIVE_DPI),
            ctypes.byref(dpi_x), ctypes.byref(dpi_y),
        )
        if rc == 0 and dpi_x.value and dpi_y.value:
            return (int(dpi_x.value), int(dpi_y.value))
    except Exception:
        pass
    return (default, default)


def get_monitors() -> list[DisplayMonitor]:
    """Enumerate monitors. Always returns >= 1 entry (fallback included)."""
    if _IS_WINDOWS:
        try:
            return _enum_monitors_windows()
        except Exception:
            pass
    fallback_w, fallback_h = (1920, 1080)
    env_res = _env_str("FALLBACK_RESOLUTION", "")
    if env_res and "x" in env_res.lower():
        try:
            w_s, h_s = env_res.lower().split("x", 1)
            fallback_w, fallback_h = max(800, int(w_s)), max(600, int(h_s))
        except ValueError:
            pass
    dpi = get_system_dpi()
    return [
        DisplayMonitor(
            index=0, name="Primary", left=0, top=0,
            right=fallback_w, bottom=fallback_h,
            is_primary=True, dpi_x=dpi, dpi_y=dpi,
        )
    ]


def _enum_monitors_windows() -> list[DisplayMonitor]:
    """Enumerate real monitors with Win32 EnumDisplayMonitors."""
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT),
                    ("rcWork", RECT), ("dwFlags", ctypes.c_ulong),
                    ("szDevice", ctypes.c_wchar * 32)]

    MONITORINFOF_PRIMARY = 1
    found: list[DisplayMonitor] = []

    MONITORENUMPROC = ctypes.WINFUNCTYPE(  # type: ignore[attr-defined]
        ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.POINTER(RECT), ctypes.c_void_p,
    )

    def _callback(hmon: int, _hdc: int, _rect: Any, _data: int) -> int:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(info)):
            dpi_x, dpi_y = _get_monitor_dpi_windows(hmon)
            found.append(DisplayMonitor(
                index=len(found),
                name=info.szDevice or f"Display{len(found) + 1}",
                left=int(info.rcMonitor.left), top=int(info.rcMonitor.top),
                right=int(info.rcMonitor.right), bottom=int(info.rcMonitor.bottom),
                is_primary=bool(info.dwFlags & MONITORINFOF_PRIMARY),
                dpi_x=dpi_x, dpi_y=dpi_y,
            ))
        return 1

    proc = MONITORENUMPROC(_callback)
    if not user32.EnumDisplayMonitors(None, None, proc, 0):
        raise OSError("EnumDisplayMonitors failed")
    if not found:
        raise OSError("No monitors enumerated")
    # Guarantee exactly one primary.
    if not any(m.is_primary for m in found):
        first = found[0]
        found[0] = replace(first, is_primary=True)
    return found


def get_primary_resolution(monitors: list[DisplayMonitor] | None = None) -> tuple[int, int]:
    """Return (width, height) of the primary monitor."""
    mons = monitors if monitors is not None else get_monitors()
    for mon in mons:
        if mon.is_primary:
            return (mon.width, mon.height)
    if mons:
        return (mons[0].width, mons[0].height)
    return (1920, 1080)


def scale_value_for_dpi(value: float, dpi: int | None = None, *, base_dpi: int = BASE_DPI) -> float:
    """Scale a pixel value from base-DPI space to the target DPI."""
    target = dpi if dpi is not None else get_system_dpi()
    if base_dpi <= 0:
        return float(value)
    return float(value) * (target / base_dpi)


def adapt_point_to_current(
    x: float, y: float,
    *,
    from_resolution: tuple[int, int] = REFERENCE_RESOLUTION,
    to_resolution: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Map a point authored for ``from_resolution`` onto the live display."""
    target = to_resolution or get_primary_resolution()
    fw, fh = from_resolution
    tw, th = target
    if fw <= 0 or fh <= 0:
        return (int(round(x)), int(round(y)))
    nx = x / fw * tw
    ny = y / fh * th
    return (int(round(nx)), int(round(ny)))


def adapt_point_to_reference(
    x: float, y: float,
    *,
    from_resolution: tuple[int, int] | None = None,
    to_resolution: tuple[int, int] = REFERENCE_RESOLUTION,
) -> tuple[int, int]:
    """Map a live-display point back into reference (model) coordinates."""
    source = from_resolution or get_primary_resolution()
    return adapt_point_to_current(x, y, from_resolution=source, to_resolution=to_resolution)


# ---------------------------------------------------------------------------
# Timeouts / throttle / screenshots / hardware
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class TimeoutSettings:
    """Every blocking wait in the agent, in seconds."""

    action: float = ACTION_TIMEOUT_S
    verify: float = VERIFY_TIMEOUT_S
    screenshot: float = SCREENSHOT_TIMEOUT_S
    llm_request: float = LLM_REQUEST_TIMEOUT_S
    llm_stream: float = LLM_STREAM_TIMEOUT_S
    window_settle: float = WINDOW_SETTLE_TIMEOUT_S
    app_launch: float = APP_LAUNCH_TIMEOUT_S
    network_probe: float = NETWORK_PROBE_TIMEOUT_S

    def __post_init__(self) -> None:
        for f in ("action", "verify", "screenshot", "llm_request",
                  "llm_stream", "window_settle", "app_launch", "network_probe"):
            val = float(getattr(self, f))
            if not (0.1 <= val <= 3600.0):
                raise ValueError(f"timeout {f}={val!r} out of range [0.1, 3600]")


@dataclass(slots=True)
class ThrottleSettings:
    """Execution pacing: rate limits + jitter + backoff."""

    min_action_interval_ms: int = MIN_ACTION_INTERVAL_MS
    max_actions_per_minute: int = MAX_ACTIONS_PER_MINUTE
    default_jitter_ms: int = DEFAULT_JITTER_MS
    max_jitter_ms: int = MAX_JITTER_MS
    failure_cooldown_s: float = FAILURE_COOLDOWN_S
    burst_limit: int = BURST_LIMIT
    burst_window_s: float = BURST_WINDOW_S
    max_retries: int = MAX_RETRIES
    retry_backoff_base_s: float = RETRY_BACKOFF_BASE_S
    retry_backoff_max_s: float = RETRY_BACKOFF_MAX_S

    @property
    def min_action_interval_s(self) -> float:
        """Minimum gap between two executed actions, in seconds."""
        return self.min_action_interval_ms / 1000.0

    def backoff_for_attempt(self, attempt: int) -> float:
        """Exponential backoff (with cap) for 1-based retry attempt."""
        attempt = max(1, int(attempt))
        delay = self.retry_backoff_base_s * (2.0 ** (attempt - 1))
        return min(delay, self.retry_backoff_max_s)


def throttle_delay_with_jitter(base_ms: int, jitter_ms: int = DEFAULT_JITTER_MS) -> float:
    """Return a sleep duration (seconds): base + uniform random jitter."""
    base = max(0, int(base_ms))
    jitter = max(0, min(int(jitter_ms), MAX_JITTER_MS))
    extra = random.uniform(0, jitter) if jitter else 0.0
    return (base + extra) / 1000.0


@dataclass(slots=True)
class ScreenshotSettings:
    """Screenshot capture and ring-buffer policy."""

    format: str = "PNG"
    scale: float = 1.0
    max_buffer_files: int = 24
    buffer_prefix: str = "wina_shot_"
    jpeg_quality: int = 85
    capture_cursor: bool = True
    timeout_s: float = SCREENSHOT_TIMEOUT_S

    def buffer_path_for(self, buffer_dir: Path, timestamp: float | None = None) -> Path:
        """Build a unique, sortable buffer file path."""
        ts = timestamp if timestamp is not None else time.time()
        stem = f"{self.buffer_prefix}{int(ts * 1000):013d}"
        ext = ".jpg" if self.format.upper() == "JPEG" else ".png"
        return buffer_dir / f"{stem}{ext}"

    def prune_buffer(self, buffer_dir: Path) -> list[Path]:
        """Delete oldest files beyond ``max_buffer_files``; return survivors."""
        try:
            files = sorted(buffer_dir.glob(f"{self.buffer_prefix}*"))
        except OSError:
            return []
        keep = max(0, int(self.max_buffer_files))
        for stale in files[:-keep] if keep < len(files) else []:
            try:
                stale.unlink(missing_ok=True)  # type: ignore[call-arg]
            except OSError:
                continue
        try:
            return sorted(buffer_dir.glob(f"{self.buffer_prefix}*"))
        except OSError:
            return []


def next_screenshot_path(buffer_dir: Path, settings: ScreenshotSettings | None = None) -> Path:
    """Convenience: next buffer path using default or supplied settings."""
    cfg = settings or ScreenshotSettings()
    buffer_dir.mkdir(parents=True, exist_ok=True)
    return cfg.buffer_path_for(buffer_dir)


@dataclass(slots=True)
class HardwareConfig:
    """Detected (or overridden) machine capabilities."""

    os_name: str = platform.system()
    os_release: str = platform.release()
    os_version: str = platform.version()
    machine: str = platform.machine()
    hostname: str = socket.gethostname()
    cpu_logical: int = 8
    cpu_physical: int = 4
    ram_total_gb: float = 16.0
    gpu_names: tuple[str, ...] = ()
    max_parallel_workers: int = 4

    def __post_init__(self) -> None:
        object.__setattr__(self, "cpu_logical", max(1, int(self.cpu_logical)))
        object.__setattr__(self, "cpu_physical", max(1, int(self.cpu_physical)))
        object.__setattr__(self, "ram_total_gb", max(0.5, float(self.ram_total_gb)))
        object.__setattr__(self, "max_parallel_workers", max(1, int(self.max_parallel_workers)))


def detect_hardware() -> HardwareConfig:
    """Detect CPU/RAM/GPU with stdlib + optional psutil; never raises."""
    import multiprocessing

    cpu_logical = os.cpu_count() or 8
    cpu_physical = max(1, cpu_logical // 2)
    ram_gb = 16.0
    try:
        import psutil  # type: ignore[import-not-found]

        ram_gb = round(psutil.virtual_memory().total / (1024.0 ** 3), 2)
        physical = psutil.cpu_count(logical=False)
        if physical:
            cpu_physical = int(physical)
        logical = psutil.cpu_count(logical=True)
        if logical:
            cpu_logical = int(logical)
    except Exception:
        try:
            cpu_logical = multiprocessing.cpu_count()
            cpu_physical = max(1, cpu_logical // 2)
        except Exception:
            pass
    gpu_names: tuple[str, ...] = ()
    if _IS_WINDOWS:
        try:
            import subprocess

            proc = subprocess.run(
                ["wmic", "path", "win32_VideoController", "get", "name", "/format:list"],
                capture_output=True, text=True, timeout=8, check=False,
            )
            names = []
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.lower().startswith("name="):
                    name = line.split("=", 1)[1].strip()
                    if name:
                        names.append(name)
            gpu_names = tuple(names[:4])
        except Exception:
            gpu_names = ()
    workers = max(1, min(cpu_logical - 1, 8)) if cpu_logical > 1 else 1
    return HardwareConfig(
        os_name=platform.system(),
        os_release=platform.release(),
        os_version=platform.version(),
        machine=platform.machine(),
        hostname=socket.gethostname(),
        cpu_logical=cpu_logical,
        cpu_physical=cpu_physical,
        ram_total_gb=ram_gb,
        gpu_names=gpu_names,
        max_parallel_workers=_env_int("MAX_WORKERS", workers, minimum=1, maximum=32),
    )


# ---------------------------------------------------------------------------
# Model endpoints
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class OllamaConfig:
    """Ollama vision + chat endpoint (local inference server)."""

    enabled: bool = True
    host: str = "http://127.0.0.1:11434"
    chat_model: str = "qwen2.5-vl:7b"
    vision_model: str = "qwen2.5-vl:7b"
    embed_model: str = "nomic-embed-text:v1.5"
    request_timeout_s: float = LLM_REQUEST_TIMEOUT_S
    stream_timeout_s: float = LLM_STREAM_TIMEOUT_S
    max_retries: int = MAX_RETRIES
    keep_alive: str = "30m"
    num_ctx: int = 32768
    temperature: float = 0.2

    @property
    def api_base(self) -> str:
        """Base URL without trailing slash."""
        return self.host.rstrip("/")

    @property
    def chat_url(self) -> str:
        """Full ``/api/chat`` URL."""
        return f"{self.api_base}/api/chat"

    @property
    def generate_url(self) -> str:
        """Full ``/api/generate`` URL."""
        return f"{self.api_base}/api/generate"

    @property
    def tags_url(self) -> str:
        """Full ``/api/tags`` URL."""
        return f"{self.api_base}/api/tags"


@dataclass(slots=True)
class LocalModelConfig:
    """On-disk / locally-served model policy (llama.cpp, transformers)."""

    enabled: bool = False
    models_dir: Path = field(default_factory=lambda: _default_base_dir() / "models")
    vision_model_path: Path = field(default_factory=lambda: _default_base_dir() / "models" / "qwen2.5-vl-7b-q4_k_m.gguf")
    llm_model_path: Path = field(default_factory=lambda: _default_base_dir() / "models" / "qwen2.5-7b-q4_k_m.gguf")
    n_gpu_layers: int = 24
    n_ctx: int = 32768
    n_threads: int = 0  # 0 == auto
    verbose: bool = False

    def resolved_threads(self, hardware: HardwareConfig | None = None) -> int:
        """Thread count: explicit value or hardware-derived default."""
        if self.n_threads and self.n_threads > 0:
            return self.n_threads
        hw = hardware or detect_hardware()
        return max(2, min(hw.cpu_logical - 1, 12))


@dataclass(slots=True)
class LlmProxyConfig:
    """OpenAI-compatible LLM API proxy (cloud fallback / planner)."""

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    chat_model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o-mini"
    request_timeout_s: float = LLM_REQUEST_TIMEOUT_S
    max_retries: int = MAX_RETRIES
    max_tokens: int = 4096
    temperature: float = 0.2

    @property
    def api_key(self) -> str:
        """API key resolved from the configured environment variable."""
        return os.environ.get(self.api_key_env, "")

    @property
    def is_configured(self) -> bool:
        """True when enabled and a non-empty API key is present."""
        return self.enabled and bool(self.api_key.strip())

    @property
    def chat_completions_url(self) -> str:
        """Full ``/chat/completions`` URL."""
        return f"{self.base_url.rstrip('/')}/chat/completions"


# ---------------------------------------------------------------------------
# Multi-provider configs (Groq / DeepSeek / Mistral / Gemini)
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS: Final[tuple[str, ...]] = ("groq", "deepseek", "mistral", "gemini")


@dataclass(slots=True)
class ProviderConfig:
    """Single cloud provider entry: model + endpoint. Key lives in env only."""

    name: str = "groq"
    model: str = ""
    base_url: str = ""
    api_key_env: str = ""

    @property
    def api_key(self) -> str:
        """Secret resolved from the environment; never stored on disk."""
        if not self.api_key_env:
            return ""
        return os.environ.get(self.api_key_env, "")

    @property
    def is_configured(self) -> bool:
        """True when a non-empty API key is present in the environment."""
        return bool(self.api_key.strip())

    def to_redacted_dict(self) -> dict[str, Any]:
        """JSON-safe snapshot with the secret replaced by REDACTED/empty."""
        key = self.api_key
        return {
            "name": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "api_key": "REDACTED" if key else "",
            "is_configured": self.is_configured,
        }


@dataclass(slots=True)
class ProvidersConfig:
    """Multi-provider routing. Secrets are env-only and always redacted."""

    default_provider: str = "groq"
    groq: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="groq",
        model="llama-3.3-70b-versatile",
        base_url="https://api.groq.com/openai/v1",
        api_key_env="GROQ_API_KEY",
    ))
    deepseek: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
    ))
    mistral: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="mistral",
        model="mistral-large-latest",
        base_url="https://api.mistral.ai/v1",
        api_key_env="MISTRAL_API_KEY",
    ))
    gemini: ProviderConfig = field(default_factory=lambda: ProviderConfig(
        name="gemini",
        model="gemini-2.0-flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key_env="GEMINI_API_KEY",
    ))
    request_timeout_s: float = LLM_REQUEST_TIMEOUT_S
    max_retries: int = MAX_RETRIES

    def __post_init__(self) -> None:
        name = (self.default_provider or "groq").strip().lower()
        if name not in SUPPORTED_PROVIDERS:
            name = "groq"
        object.__setattr__(self, "default_provider", name)

    def get(self, name: str) -> ProviderConfig:
        """Return the provider entry for ``name`` (case-insensitive)."""
        key = (name or "").strip().lower()
        if key == "deepseek":
            return self.deepseek
        if key == "mistral":
            return self.mistral
        if key == "gemini":
            return self.gemini
        return self.groq

    @property
    def active(self) -> ProviderConfig:
        """Currently selected provider entry."""
        return self.get(self.default_provider)

    @property
    def active_model(self) -> str:
        """Model id of the active provider."""
        return self.active.model

    @property
    def active_base_url(self) -> str:
        """Base URL of the active provider."""
        return self.active.base_url

    @property
    def active_api_key(self) -> str:
        """API key of the active provider (env-only, never persisted)."""
        return self.active.api_key

    def api_key_for(self, name: str) -> str:
        """Return the env-resolved API key for ``name``."""
        return self.get(name).api_key

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe snapshot; every secret is REDACTED or empty."""
        return {
            "default_provider": self.default_provider,
            "request_timeout_s": self.request_timeout_s,
            "max_retries": self.max_retries,
            "groq": self.groq.to_redacted_dict(),
            "deepseek": self.deepseek.to_redacted_dict(),
            "mistral": self.mistral.to_redacted_dict(),
            "gemini": self.gemini.to_redacted_dict(),
            "active": self.active.to_redacted_dict(),
        }


# ---------------------------------------------------------------------------
# Daemon / queue config
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class DaemonConfig:
    """Headless daemon: pid file, task queue dirs, polling policy."""

    pid_file: Path = field(default_factory=lambda: _default_base_dir() / "wina.pid")
    queue_dir: Path = field(default_factory=lambda: _default_base_dir() / "queue")
    done_dir: Path = field(default_factory=lambda: _default_base_dir() / "done")
    poll_interval_s: float = 5.0
    hidden_console: bool = True

    def __post_init__(self) -> None:
        interval = float(self.poll_interval_s)
        if not (0.5 <= interval <= 300.0):
            raise ValueError(f"poll_interval_s={interval!r} out of range [0.5, 300]")
        object.__setattr__(self, "pid_file", Path(self.pid_file).expanduser())
        object.__setattr__(self, "queue_dir", Path(self.queue_dir).expanduser())
        object.__setattr__(self, "done_dir", Path(self.done_dir).expanduser())

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe snapshot."""
        return {
            "pid_file": str(self.pid_file),
            "queue_dir": str(self.queue_dir),
            "done_dir": str(self.done_dir),
            "poll_interval_s": self.poll_interval_s,
            "hidden_console": self.hidden_console,
        }


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LoggingConfig:
    """Logging policy; applied via :func:`configure_logging`."""

    level: str = "INFO"
    format: str = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"
    to_console: bool = True
    to_file: bool = True
    max_file_mb: int = 10
    backup_count: int = 5
    capture_warnings: bool = True


def configure_logging(paths: AppPaths, cfg: LoggingConfig | None = None) -> logging.Logger:
    """Configure root + ``wina`` loggers with console and rotating file."""
    config = cfg or LoggingConfig()
    ensure_directories(paths)
    level = getattr(logging, config.level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    formatter = logging.Formatter(config.format, datefmt=config.date_format)

    if config.to_console:
        console = logging.StreamHandler(sys.stdout)
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    if config.to_file:
        file_handler = logging.handlers.RotatingFileHandler(
            str(paths.log_file),
            maxBytes=max(1, config.max_file_mb) * 1024 * 1024,
            backupCount=max(0, config.backup_count),
            encoding="utf-8",
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    if config.capture_warnings:
        logging.captureWarnings(True)

    logger = logging.getLogger("wina")
    logger.debug(
        "logging configured level=%s file=%s",
        logging.getLevelName(level),
        str(paths.log_file) if config.to_file else "<disabled>",
    )
    return logger


# ---------------------------------------------------------------------------
# Aggregate config
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class JarvisConfig:
    """Root configuration object — build once, share everywhere."""

    app_name: str = APP_NAME
    app_version: str = APP_VERSION
    debug: bool = False
    dry_run: bool = False
    paths: AppPaths = field(default_factory=AppPaths)
    dpi: DpiSettings = field(default_factory=DpiSettings)
    display: DisplaySettings = field(default_factory=DisplaySettings)
    timeouts: TimeoutSettings = field(default_factory=TimeoutSettings)
    throttle: ThrottleSettings = field(default_factory=ThrottleSettings)
    screenshots: ScreenshotSettings = field(default_factory=ScreenshotSettings)
    hardware: HardwareConfig = field(default_factory=detect_hardware)
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    local_model: LocalModelConfig = field(default_factory=LocalModelConfig)
    llm_proxy: LlmProxyConfig = field(default_factory=LlmProxyConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot (API keys redacted)."""
        data = asdict(self)
        for key in ("base_dir", "logs_dir", "screenshots_dir", "screenshot_buffer_dir",
                    "models_dir", "cache_dir", "db_dir", "tmp_dir", "profiles_dir",
                    "queue_dir", "done_dir",
                    "database_file", "config_file", "log_file", "pid_file"):
            try:
                data["paths"][key] = str(data["paths"][key])
            except KeyError:
                pass
        try:
            data["local_model"]["models_dir"] = str(data["local_model"]["models_dir"])
            data["local_model"]["vision_model_path"] = str(data["local_model"]["vision_model_path"])
            data["local_model"]["llm_model_path"] = str(data["local_model"]["llm_model_path"])
        except KeyError:
            pass
        # Providers: never leak secrets — replace with redacted snapshot.
        try:
            providers_cfg = self.providers
            data["providers"] = providers_cfg.to_dict()
        except Exception:
            pass
        # Daemon paths must be plain strings for JSON.
        try:
            data["daemon"]["pid_file"] = str(self.daemon.pid_file)
            data["daemon"]["queue_dir"] = str(self.daemon.queue_dir)
            data["daemon"]["done_dir"] = str(self.daemon.done_dir)
        except Exception:
            pass
        return data

    def save(self, path: Path | None = None) -> Path:
        """Persist snapshot JSON to disk; return the file written."""
        target = path or self.paths.config_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target


def _apply_env_overrides(cfg: JarvisConfig) -> JarvisConfig:
    """Mutate ``cfg`` from ``WINA_*`` (fallback ``JARVIS_*``) environment variables."""
    cfg.debug = _env_bool("DEBUG", cfg.debug)
    cfg.dry_run = _env_bool("DRY_RUN", cfg.dry_run)

    # Paths
    cfg.paths.base_dir = _env_path("BASE_DIR", cfg.paths.base_dir)
    cfg.paths.__post_init__()
    cfg.paths.queue_dir = _env_path("QUEUE_DIR", cfg.paths.queue_dir)
    cfg.paths.done_dir = _env_path("DONE_DIR", cfg.paths.done_dir)
    cfg.paths.pid_file = _env_path("PID_FILE", cfg.paths.pid_file)
    ensure_directories(cfg.paths)

    # DPI / display
    cfg.dpi.system_dpi = _env_int("SYSTEM_DPI", cfg.dpi.system_dpi, minimum=48, maximum=576)
    cfg.dpi.awareness_mode = _env_str("DPI_AWARENESS", cfg.dpi.awareness_mode)
    cfg.display.fallback_resolution = (
        _env_int("FALLBACK_W", cfg.display.fallback_resolution[0], minimum=800, maximum=7680),
        _env_int("FALLBACK_H", cfg.display.fallback_resolution[1], minimum=600, maximum=4320),
    )

    # Timeouts
    cfg.timeouts.action = _env_float("TIMEOUT_ACTION", cfg.timeouts.action, minimum=0.1, maximum=3600)
    cfg.timeouts.verify = _env_float("TIMEOUT_VERIFY", cfg.timeouts.verify, minimum=0.1, maximum=3600)
    cfg.timeouts.screenshot = _env_float("TIMEOUT_SCREENSHOT", cfg.timeouts.screenshot, minimum=0.1, maximum=300)
    cfg.timeouts.llm_request = _env_float("TIMEOUT_LLM", cfg.timeouts.llm_request, minimum=1, maximum=3600)
    cfg.timeouts.llm_stream = _env_float("TIMEOUT_LLM_STREAM", cfg.timeouts.llm_stream, minimum=1, maximum=3600)
    cfg.timeouts.window_settle = _env_float("TIMEOUT_SETTLE", cfg.timeouts.window_settle, minimum=0.1, maximum=120)
    cfg.timeouts.app_launch = _env_float("TIMEOUT_LAUNCH", cfg.timeouts.app_launch, minimum=1, maximum=600)

    # Throttle
    cfg.throttle.min_action_interval_ms = _env_int("MIN_ACTION_MS", cfg.throttle.min_action_interval_ms, minimum=0, maximum=10000)
    cfg.throttle.max_actions_per_minute = _env_int("MAX_ACTIONS_PM", cfg.throttle.max_actions_per_minute, minimum=1, maximum=3600)
    cfg.throttle.default_jitter_ms = _env_int("JITTER_MS", cfg.throttle.default_jitter_ms, minimum=0, maximum=MAX_JITTER_MS)
    cfg.throttle.max_retries = _env_int("MAX_RETRIES", cfg.throttle.max_retries, minimum=0, maximum=10)

    # Screenshots
    cfg.screenshots.format = _env_str("SHOT_FORMAT", cfg.screenshots.format).upper()
    if cfg.screenshots.format not in ("PNG", "JPEG"):
        cfg.screenshots.format = "PNG"
    cfg.screenshots.max_buffer_files = _env_int("SHOT_BUFFER_N", cfg.screenshots.max_buffer_files, minimum=1, maximum=500)
    cfg.screenshots.scale = _env_float("SHOT_SCALE", cfg.screenshots.scale, minimum=0.25, maximum=1.0)

    # Ollama
    cfg.ollama.enabled = _env_bool("OLLAMA_ENABLED", cfg.ollama.enabled)
    cfg.ollama.host = _env_str("OLLAMA_HOST", cfg.ollama.host)
    cfg.ollama.chat_model = _env_str("OLLAMA_CHAT_MODEL", cfg.ollama.chat_model)
    cfg.ollama.vision_model = _env_str("OLLAMA_VISION_MODEL", cfg.ollama.vision_model)
    cfg.ollama.embed_model = _env_str("OLLAMA_EMBED_MODEL", cfg.ollama.embed_model)
    cfg.ollama.request_timeout_s = _env_float("OLLAMA_TIMEOUT", cfg.ollama.request_timeout_s, minimum=1, maximum=3600)
    cfg.ollama.keep_alive = _env_str("OLLAMA_KEEP_ALIVE", cfg.ollama.keep_alive)
    cfg.ollama.temperature = _env_float("OLLAMA_TEMP", cfg.ollama.temperature, minimum=0.0, maximum=2.0)

    # Local model
    cfg.local_model.enabled = _env_bool("LOCAL_MODEL_ENABLED", cfg.local_model.enabled)
    cfg.local_model.models_dir = _env_path("LOCAL_MODELS_DIR", cfg.local_model.models_dir)
    cfg.local_model.n_gpu_layers = _env_int("LOCAL_N_GPU", cfg.local_model.n_gpu_layers, minimum=0, maximum=128)
    cfg.local_model.n_ctx = _env_int("LOCAL_N_CTX", cfg.local_model.n_ctx, minimum=1024, maximum=262144)

    # Proxy
    cfg.llm_proxy.enabled = _env_bool("PROXY_ENABLED", cfg.llm_proxy.enabled)
    cfg.llm_proxy.base_url = _env_str("PROXY_URL", cfg.llm_proxy.base_url)
    cfg.llm_proxy.api_key_env = _env_str("PROXY_KEY_ENV", cfg.llm_proxy.api_key_env)
    cfg.llm_proxy.chat_model = _env_str("PROXY_MODEL", cfg.llm_proxy.chat_model)
    cfg.llm_proxy.vision_model = _env_str("PROXY_VISION_MODEL", cfg.llm_proxy.vision_model)
    cfg.llm_proxy.max_tokens = _env_int("PROXY_MAX_TOKENS", cfg.llm_proxy.max_tokens, minimum=16, maximum=128000)

    # Logging
    cfg.logging.level = _env_str("LOG_LEVEL", cfg.logging.level).upper()
    if cfg.logging.level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        cfg.logging.level = "INFO"
    cfg.logging.to_console = _env_bool("LOG_CONSOLE", cfg.logging.to_console)
    cfg.logging.to_file = _env_bool("LOG_FILE", cfg.logging.to_file)

    # Hardware worker override
    cfg.hardware.max_parallel_workers = _env_int(
        "MAX_WORKERS", cfg.hardware.max_parallel_workers, minimum=1, maximum=32
    )

    # Providers (models + endpoints; keys stay in env, never persisted)
    cfg.providers.default_provider = _env_str("DEFAULT_PROVIDER", cfg.providers.default_provider).lower()
    if cfg.providers.default_provider not in SUPPORTED_PROVIDERS:
        cfg.providers.default_provider = "groq"
    cfg.providers.groq.model = _env_str("GROQ_MODEL", cfg.providers.groq.model)
    cfg.providers.groq.base_url = _env_str("GROQ_BASE_URL", cfg.providers.groq.base_url)
    cfg.providers.groq.api_key_env = _env_str("GROQ_KEY_ENV", cfg.providers.groq.api_key_env)
    cfg.providers.deepseek.model = _env_str("DEEPSEEK_MODEL", cfg.providers.deepseek.model)
    cfg.providers.deepseek.base_url = _env_str("DEEPSEEK_BASE_URL", cfg.providers.deepseek.base_url)
    cfg.providers.deepseek.api_key_env = _env_str("DEEPSEEK_KEY_ENV", cfg.providers.deepseek.api_key_env)
    cfg.providers.mistral.model = _env_str("MISTRAL_MODEL", cfg.providers.mistral.model)
    cfg.providers.mistral.base_url = _env_str("MISTRAL_BASE_URL", cfg.providers.mistral.base_url)
    cfg.providers.mistral.api_key_env = _env_str("MISTRAL_KEY_ENV", cfg.providers.mistral.api_key_env)
    cfg.providers.gemini.model = _env_str("GEMINI_MODEL", cfg.providers.gemini.model)
    cfg.providers.gemini.base_url = _env_str("GEMINI_BASE_URL", cfg.providers.gemini.base_url)
    cfg.providers.gemini.api_key_env = _env_str("GEMINI_KEY_ENV", cfg.providers.gemini.api_key_env)
    cfg.providers.request_timeout_s = _env_float(
        "PROVIDERS_TIMEOUT", cfg.providers.request_timeout_s, minimum=1, maximum=3600)
    cfg.providers.max_retries = _env_int(
        "PROVIDERS_RETRIES", cfg.providers.max_retries, minimum=0, maximum=10)

    # Daemon (queue runner)
    cfg.daemon.pid_file = _env_path("PID_FILE", cfg.daemon.pid_file)
    cfg.daemon.queue_dir = _env_path("QUEUE_DIR", cfg.daemon.queue_dir)
    cfg.daemon.done_dir = _env_path("DONE_DIR", cfg.daemon.done_dir)
    cfg.daemon.poll_interval_s = _env_float(
        "POLL_INTERVAL_S", cfg.daemon.poll_interval_s, minimum=0.5, maximum=300.0)
    cfg.daemon.hidden_console = _env_bool("HIDDEN_CONSOLE", cfg.daemon.hidden_console)
    # Keep AppPaths and DaemonConfig in sync when env did not override one side.
    if str(cfg.paths.queue_dir) != str(cfg.daemon.queue_dir):
        if _lookup_env("QUEUE_DIR") is None:
            cfg.daemon.queue_dir = cfg.paths.queue_dir
        else:
            cfg.paths.queue_dir = cfg.daemon.queue_dir
    if str(cfg.paths.done_dir) != str(cfg.daemon.done_dir):
        if _lookup_env("DONE_DIR") is None:
            cfg.daemon.done_dir = cfg.paths.done_dir
        else:
            cfg.paths.done_dir = cfg.daemon.done_dir
    if str(cfg.paths.pid_file) != str(cfg.daemon.pid_file):
        if _lookup_env("PID_FILE") is None:
            cfg.daemon.pid_file = cfg.paths.pid_file
        else:
            cfg.paths.pid_file = cfg.daemon.pid_file
    ensure_directories(cfg.paths)
    return cfg


_CONFIG_SINGLETON: JarvisConfig | None = None

# Backward/forward-compatible alias: WINA's canonical config name.
WinaConfig = JarvisConfig


def _build_config() -> JarvisConfig:
    """Construct, detect, override, and bootstrap a fresh config."""
    _set_process_dpi_awareness()
    cfg = JarvisConfig()
    cfg.dpi.system_dpi = get_system_dpi()
    try:
        cfg.display.monitors = get_monitors()
    except Exception:
        dpi = cfg.dpi.system_dpi
        cfg.display.monitors = [
            DisplayMonitor(index=0, name="Primary", left=0, top=0,
                           right=1920, bottom=1080, is_primary=True,
                           dpi_x=dpi, dpi_y=dpi)
        ]
    _apply_env_overrides(cfg)
    # Local model defaults follow the (possibly overridden) base dir.
    if _lookup_env("LOCAL_MODELS_DIR") is None:
        cfg.local_model.models_dir = cfg.paths.models_dir
        cfg.local_model.vision_model_path = cfg.paths.models_dir / cfg.local_model.vision_model_path.name
        cfg.local_model.llm_model_path = cfg.paths.models_dir / cfg.local_model.llm_model_path.name
    # Daemon defaults follow the base dir unless explicitly overridden.
    if _lookup_env("QUEUE_DIR") is None:
        cfg.daemon.queue_dir = cfg.paths.queue_dir
    if _lookup_env("DONE_DIR") is None:
        cfg.daemon.done_dir = cfg.paths.done_dir
    if _lookup_env("PID_FILE") is None:
        cfg.daemon.pid_file = cfg.paths.pid_file
    ensure_directories(cfg.paths)
    return cfg


def get_config() -> JarvisConfig:
    """Return the process-wide singleton config (built on first call)."""
    global _CONFIG_SINGLETON
    if _CONFIG_SINGLETON is None:
        _CONFIG_SINGLETON = _build_config()
    return _CONFIG_SINGLETON


def reload_config() -> JarvisConfig:
    """Rebuild the singleton from current environment + live detection."""
    global _CONFIG_SINGLETON
    _CONFIG_SINGLETON = _build_config()
    return _CONFIG_SINGLETON


def _diagnostic() -> int:
    """Print a JSON diagnostic snapshot; used as ``python config.py``."""
    cfg = get_config()
    payload: dict[str, Any] = cfg.to_dict()
    payload["runtime"] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "scaling_multiplier": cfg.dpi.scaling_multiplier,
        "primary_resolution": list(get_primary_resolution(cfg.display.monitors)),
        "monitors": [m.to_dict() for m in cfg.display.monitors],
    }
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_diagnostic())
