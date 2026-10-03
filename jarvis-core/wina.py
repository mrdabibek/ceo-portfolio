"""Wina — autonomous desktop agent. CLI + background daemon (Windows 10/11, Python 3.11+).

Usage:
    python wina.py run "Telegramda Fazliddinga salom yoz" [--provider X]
    python wina.py telegram --contact X --message Y
    python wina.py daemon start [--hidden] | daemon stop | daemon status
    python wina.py queue "topshiriq" [--provider X]
    python wina.py list

All paths are relative to this file. stdlib only (+ agent_core, brain).
"""
from __future__ import annotations

import argparse
import ctypes
import datetime
import getpass
import hashlib
import inspect
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

QUEUE_DIR = BASE_DIR / "queue"
LOCK_FILE = BASE_DIR / ".wina_lock"
DONE_DIR = QUEUE_DIR / "done"
PID_FILE = BASE_DIR / "daemon.pid"
LOG_FILE = BASE_DIR / "wina_daemon.log"
WINA_FILE = Path(__file__).resolve()

DEFAULT_CONTACT = "Fazliddin"
DEFAULT_MESSAGE = "Loyihani ko'rib chiqdingmi?"
POLL_INTERVAL_S = 5.0

try:
    import agent_core as _agent_core
except Exception as exc:
    print(f"Wina xatosi: agent_core import qilinmadi ({exc}). "
          f"wina.py agent_core.py bilan bir papkada bo'lishi shart.", file=sys.stderr)
    sys.exit(2)

try:
    import brain as _brain
    _BRAIN_ERROR = ""
except Exception as exc:
    _brain = None
    _BRAIN_ERROR = f"{type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# PID helpers (no psutil)
# ---------------------------------------------------------------------------

def _read_pid() -> Optional[int]:
    try:
        raw = PID_FILE.read_text(encoding="utf-8").strip().split()[0]
        pid = int(raw)
        return pid if pid > 0 else None
    except (OSError, ValueError, IndexError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_LIMITED = 0x1000
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _terminate_pid(pid: int) -> bool:
    if os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            PROCESS_TERMINATE = 0x0001
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                try:
                    ok = kernel32.TerminateProcess(handle, 0)
                    return bool(ok)
                finally:
                    kernel32.CloseHandle(handle)
        except Exception:
            pass
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, timeout=15)
            return not _pid_alive(pid)
        except Exception:
            return False
    try:
        os.kill(pid, 15)
        return True
    except Exception:
        return False


def _pending_tasks() -> List[Path]:
    if not QUEUE_DIR.is_dir():
        return []
    return sorted(p for p in QUEUE_DIR.glob("*.json") if p.is_file())


# ---------------------------------------------------------------------------
# Natural-language -> telegram params (fallback when brain.py missing)
# ---------------------------------------------------------------------------

_DATIVE_SUFFIXES = ("ning", "ga", "ka", "qa", "ni", "na")


def _strip_dative(name: str) -> str:
    low = name.lower()
    for suf in sorted(_DATIVE_SUFFIXES, key=len, reverse=True):
        if low.endswith(suf) and len(name) > len(suf) + 2:
            return name[: -len(suf)]
    return name


def _parse_telegram_task(text: str) -> Optional[Tuple[str, str]]:
    t = (text or "").strip()
    if not t:
        return None
    if "telegram" not in t.lower() and "tg" not in t.lower().split():
        return None
    quoted = re.search(r'["«»“”‘’\'](.+?)["«»“”‘’\']', t)
    message = quoted.group(1).strip() if quoted else ""
    m = re.search(r"telegram\w*\s*(?:da\s+)?([A-Za-z\u0400-\u04FF']+)", t, re.IGNORECASE)
    contact = ""
    rest = t
    if m:
        raw_contact = m.group(1).strip().strip(",.:;!?")
        contact = _strip_dative(raw_contact)
        rest = t[m.end():].strip()
    if not message:
        message = re.sub(r"^(ga|ka|qa|ni|na|uchun|jon|aka|uka)\b[\s,.:;]*",
                         "", rest, flags=re.IGNORECASE).strip()
        message = re.sub(r"\b(salom\s+yoz|xabar\s+yoz|yoz|yubor|jo['`]?nat)\b[\s,.:;]*",
                         "", message, flags=re.IGNORECASE).strip(" ,.:;")
        if not message:
            message = t
    if not contact:
        contact = DEFAULT_CONTACT
    if not message:
        message = t
    return contact, message


# ---------------------------------------------------------------------------
# Task execution: brain.plan_task -> agent_core ReAct
# ---------------------------------------------------------------------------

def _call_brain_plan(task: str, provider: Optional[str]) -> Any:
    fn = getattr(_brain, "plan_task")
    try:
        sig = inspect.signature(fn)
        names = list(sig.parameters.keys())
    except (TypeError, ValueError):
        names = []
    if names and "provider" in names:
        try:
            return fn(task, provider=provider) if provider else fn(task)
        except TypeError:
            return fn(task)
    for attempt in (
        lambda: fn(task, **({"provider": provider} if provider else {})),
        lambda: fn(task),
        lambda: fn(task=task),
        lambda: fn(prompt=task),
        lambda: fn(text=task),
    ):
        try:
            return attempt()
        except TypeError:
            continue
    return fn(task)


def _run_scenario(contact: str, message: str) -> Dict[str, Any]:
    t0 = time.time()
    try:
        res = _agent_core.run_telegram_scenario(contact=contact, message=message)
        return {
            "ok": bool(getattr(res, "ok", False)),
            "error": str(getattr(res, "error", "") or ""),
            "duration_s": round(float(getattr(res, "duration_s", time.time() - t0)), 2),
            "evidence": [str(e) for e in (getattr(res, "evidence", []) or [])],
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.time() - t0, 2), "evidence": []}


def _run_step_plan(steps: List[Any]) -> Dict[str, Any]:
    t0 = time.time()
    try:
        orch_cls = getattr(_agent_core, "ReActOrchestrator")
        orch = orch_cls()
        res = orch.run_plan(steps)
        return {
            "ok": bool(getattr(res, "ok", False)),
            "error": str(getattr(res, "error", "") or ""),
            "duration_s": round(float(getattr(res, "duration_s", time.time() - t0)), 2),
            "evidence": [str(e) for e in (getattr(res, "evidence", []) or [])],
        }
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                "duration_s": round(time.time() - t0, 2), "evidence": []}


def _steps_from_dicts(items: List[Any]) -> List[Any]:
    ActionStep = getattr(_agent_core, "ActionStep", None)
    if ActionStep is None:
        raise RuntimeError("agent_core.ActionStep topilmadi")
    out = []
    for i, item in enumerate(items):
        if hasattr(item, "primitive"):
            out.append(item)
        elif isinstance(item, dict):
            kw = {k: item[k] for k in ("id", "primitive", "description", "params",
                                       "verify", "timeout_s") if k in item}
            kw.setdefault("id", f"brain-{i + 1:02d}")
            kw.setdefault("primitive", "wait")
            kw.setdefault("description", f"brain step {i + 1}")
            kw.setdefault("params", {})
            kw.setdefault("verify", {})
            if not isinstance(kw["params"], dict):
                kw["params"] = {}
            # LLM params'ni flat yozadi (path/content yonida) — merge qilamiz,
            # aks holda write_file/read_file bo'sh params bilan yuguradi.
            for k, v in item.items():
                if k not in kw["params"] and k not in ("id", "primitive", "description",
                                                       "params", "verify", "timeout_s"):
                    kw["params"][k] = v
            out.append(ActionStep(**kw))
        else:
            raise ValueError(f"brain plan step {i} noma'lum format: {type(item).__name__}")
    return out


def _execute_plan(plan: Any, task: str) -> Dict[str, Any]:
    if plan is None:
        raise ValueError("brain.plan_task None qaytardi")
    if isinstance(plan, dict):
        if isinstance(plan.get("steps"), list):
            return _run_step_plan(_steps_from_dicts(plan["steps"]))
        if "contact" in plan or "message" in plan:
            return _run_scenario(str(plan.get("contact") or DEFAULT_CONTACT),
                                 str(plan.get("message") or task))
        if "intent" in plan:
            orch = getattr(_agent_core, "ReActOrchestrator")()
            params = plan.get("params", {}) or {}
            if not isinstance(params, dict):
                params = {"message": str(params)}
            steps = orch.planner.plan(str(plan["intent"]), params)
            return _run_step_plan(steps)
        raise ValueError(f"brain plan dict formati noma'lum: {sorted(plan.keys())}")
    if isinstance(plan, (list, tuple)):
        items = list(plan)
        if items and (hasattr(items[0], "primitive")
                      or (isinstance(items[0], dict) and "primitive" in items[0])):
            return _run_step_plan(_steps_from_dicts(items))
        raise ValueError("brain plan list formati noma'lum (step primitive kutilgan)")
    for attr in ("steps",):
        if hasattr(plan, attr):
            seq = getattr(plan, attr)
            if isinstance(seq, (list, tuple)):
                return _run_step_plan(_steps_from_dicts(list(seq)))
    contact = getattr(plan, "contact", None)
    message = getattr(plan, "message", None)
    if contact is not None or message is not None:
        return _run_scenario(str(contact or DEFAULT_CONTACT), str(message or task))
    if isinstance(plan, str) and plan.strip():
        return _run_scenario(DEFAULT_CONTACT, plan.strip())
    raise ValueError(f"brain plan tipi noma'lum: {type(plan).__name__}")


def run_task_text(task: str, provider: Optional[str] = None) -> Dict[str, Any]:
    t0 = time.time()
    task = (task or "").strip()
    if not task:
        return {"ok": False, "error": "bo'sh topshiriq", "duration_s": 0.0, "evidence": []}
    if _brain is not None and hasattr(_brain, "plan_task"):
        try:
            plan = _call_brain_plan(task, provider)
        except Exception as exc:
            return {"ok": False, "error": f"brain.plan_task xatosi: {type(exc).__name__}: {exc}",
                    "duration_s": round(time.time() - t0, 2), "evidence": []}
        try:
            out = _execute_plan(plan, task)
            return out
        except Exception as exc:
            return {"ok": False, "error": f"brain plan bajarish xatosi: {type(exc).__name__}: {exc}",
                    "duration_s": round(time.time() - t0, 2), "evidence": []}
    parsed = _parse_telegram_task(task)
    if parsed is None:
        hint = f" ({_BRAIN_ERROR})" if _BRAIN_ERROR else ""
        return {"ok": False,
                "error": ("brain.py topilmadi yoki plan_task yo'q" + hint +
                          "; 'run' hozir faqat telegram topshiriqlarni bajara oladi "
                          "(masalan: 'Telegramda Fazliddinga salom yoz')."),
                "duration_s": round(time.time() - t0, 2), "evidence": []}
    contact, message = parsed
    return _run_scenario(contact, message)


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

def cmd_queue_add(task: str, provider: Optional[str]) -> int:
    task = (task or "").strip()
    if not task:
        print("Xato: bo'sh topshiriq", file=sys.stderr)
        return 2
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    task_id = f"{stamp}_{uuid.uuid4().hex[:6]}"
    payload = {"id": task_id, "task": task,
               "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
    if provider:
        payload["provider"] = provider
    path = QUEUE_DIR / f"{task_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"queued {task_id}")
    print(str(path))
    return 0


def cmd_list() -> int:
    pending = _pending_tasks()
    done_n = len(list(DONE_DIR.glob("*.result.json"))) if DONE_DIR.is_dir() else 0
    print(f"pending: {len(pending)}, done: {done_n}")
    for p in pending:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            print(f"  - {data.get('id', p.stem)}: {str(data.get('task', ''))[:100]}")
        except Exception:
            print(f"  - {p.name}: (o'qib bo'lmadi)")
    return 0


# ---------------------------------------------------------------------------
# Daemon lifecycle
# ---------------------------------------------------------------------------

def cmd_daemon_start(hidden: bool) -> int:
    existing = _read_pid()
    if existing is not None and _pid_alive(existing):
        print(f"already running PID {existing}")
        return 0
    if existing is not None:
        try:
            PID_FILE.unlink()
        except OSError:
            pass
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    exe = sys.executable
    flags = 0
    if os.name == "nt":
        DETACHED_PROCESS = 0x00000008
        CREATE_NO_WINDOW = 0x08000000
        flags = DETACHED_PROCESS | (CREATE_NO_WINDOW if hidden else 0)
        if hidden:
            cand = Path(sys.executable).parent / "pythonw.exe"
            if cand.exists():
                exe = str(cand)
    cmd = [exe, str(WINA_FILE), "daemon", "_run"]
    try:
        logf = open(LOG_FILE, "a", encoding="utf-8")
    except OSError as exc:
        print(f"daemon log ochilmadi: {exc}", file=sys.stderr)
        return 1
    kwargs: Dict[str, Any] = {"cwd": str(BASE_DIR), "stdin": subprocess.DEVNULL,
                              "stdout": logf, "stderr": subprocess.STDOUT, "close_fds": True}
    if os.name == "nt":
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except Exception as exc:
        logf.close()
        print(f"daemon start failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    logf.close()
    time.sleep(0.8)
    pid = proc.pid
    if not _pid_alive(pid):
        print(f"daemon start failed: process {pid} tezda to'xtadi; {LOG_FILE} ni tekshiring",
              file=sys.stderr)
        return 1
    try:
        PID_FILE.write_text(str(pid), encoding="utf-8")
    except OSError as exc:
        print(f"daemon.pid yozilmadi: {exc}", file=sys.stderr)
        return 1
    print(f"daemon started PID {pid}")
    return 0


def cmd_daemon_stop() -> int:
    pid = _read_pid()
    if pid is None:
        print("daemon not running")
        return 0
    if not _pid_alive(pid):
        try:
            PID_FILE.unlink()
        except OSError:
            pass
        print("daemon not running (stale PID removed)")
        return 0
    _terminate_pid(pid)
    deadline = time.time() + 5.0
    while time.time() < deadline and _pid_alive(pid):
        time.sleep(0.2)
    if _pid_alive(pid):
        print(f"daemon stop failed: PID {pid} hali tirik", file=sys.stderr)
        return 1
    try:
        PID_FILE.unlink()
    except OSError:
        pass
    print(f"daemon stopped PID {pid}")
    return 0


def cmd_daemon_status() -> int:
    pid = _read_pid()
    pending = len(_pending_tasks())
    if pid is not None and _pid_alive(pid):
        print(f"running PID {pid}, pending {pending}")
    else:
        print(f"stopped, pending {pending}")
    return 0


def _drain_once() -> None:
    for path in _pending_tasks():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            err = {"id": path.stem, "task": "", "ok": False,
                   "error": f"queue JSON o'qilmadi: {type(exc).__name__}: {exc}",
                   "duration_s": 0.0, "evidence": [],
                   "finished_at": datetime.datetime.now().isoformat(timespec="seconds")}
            DONE_DIR.mkdir(parents=True, exist_ok=True)
            (DONE_DIR / f"{path.stem}.result.json").write_text(
                json.dumps(err, ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                path.unlink()
            except OSError:
                pass
            continue
        task_id = str(data.get("id") or path.stem)
        task = str(data.get("task") or "")
        provider = data.get("provider")
        out = run_task_text(task, provider)
        result = {"id": task_id, "task": task, "ok": bool(out.get("ok")),
                  "error": str(out.get("error") or ""),
                  "duration_s": float(out.get("duration_s") or 0.0),
                  "evidence": list(out.get("evidence") or []),
                  "finished_at": datetime.datetime.now().isoformat(timespec="seconds")}
        DONE_DIR.mkdir(parents=True, exist_ok=True)
        (DONE_DIR / f"{task_id}.result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            path.unlink()
        except OSError:
            pass


def daemon_loop() -> int:
    existing = _read_pid()
    if existing is not None and existing != os.getpid() and _pid_alive(existing):
        print(f"already running PID {existing}", file=sys.stderr)
        return 1
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    except OSError as exc:
        print(f"daemon.pid yozilmadi: {exc}", file=sys.stderr)
        return 1
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as lf:
            lf.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                     f"daemon_loop started PID {os.getpid()}\n")
    except OSError:
        pass
    try:
        while True:
            try:
                _drain_once()
            except Exception as exc:
                try:
                    with open(LOG_FILE, "a", encoding="utf-8") as lf:
                        lf.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} "
                                 f"drain xatosi: {type(exc).__name__}: {exc}\n")
                except OSError:
                    pass
            time.sleep(POLL_INTERVAL_S)
    except KeyboardInterrupt:
        return 0
    finally:
        try:
            if _read_pid() == os.getpid():
                PID_FILE.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="wina", description="Wina — autonomous desktop agent")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="tabiiy tildagi topshiriqni bajarish")
    p_run.add_argument("task", help="tabiiy tildagi topshiriq")
    p_run.add_argument("--provider", default=None, help="brain provayder tanlash (ixtiyoriy)")

    p_tg = sub.add_parser("telegram", help="tayyor telegram ssenariy")
    p_tg.add_argument("--contact", default=DEFAULT_CONTACT, help="Telegram kontakt nomi")
    p_tg.add_argument("--message", default=DEFAULT_MESSAGE, help="Xabar matni")

    p_dm = sub.add_parser("daemon", help="background daemon boshqaruvi")
    dm_sub = p_dm.add_subparsers(dest="daemon_cmd", required=True)
    p_start = dm_sub.add_parser("start", help="daemon ishga tushirish")
    p_start.add_argument("--hidden", action="store_true",
                         help="konsolsiz rejim (pythonw + CREATE_NO_WINDOW)")
    dm_sub.add_parser("stop", help="daemon to'xtatish")
    dm_sub.add_parser("status", help="daemon holati")
    dm_sub.add_parser("_run", help=argparse.SUPPRESS)

    p_q = sub.add_parser("queue", help="navbatga topshiriq qo'shish")
    p_q.add_argument("task", help="tabiiy tildagi topshiriq")
    p_q.add_argument("--provider", default=None, help="brain provayder tanlash (ixtiyoriy)")

    sub.add_parser("list", help="navbat holati")

    p_lock = sub.add_parser("lock", help="shaxsiy qulf (faqat siz ishlatasiz)")
    lock_sub = p_lock.add_subparsers(dest="lock_cmd", required=True)
    lock_sub.add_parser("set", help="parol o'rnatish")
    lock_sub.add_parser("off", help="qulfni olib tashlash")
    lock_sub.add_parser("status", help="qulf holati")
    return p


def _lock_hash(secret: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 200_000).hex()


def cmd_lock_set() -> int:
    try:
        first = getpass.getpass("Wina paroli (yangi): ")
        second = getpass.getpass("Takrorlang: ")
    except (EOFError, KeyboardInterrupt):
        print("bekor qilindi")
        return 2
    if first != second:
        print("mos kelmadi — bekor qilindi")
        return 2
    if len(first) < 4:
        print("juda qisqa (min 4 belgi)")
        return 2
    salt = secrets.token_bytes(16)
    LOCK_FILE.write_text(json.dumps({"salt": salt.hex(), "hash": _lock_hash(first, salt)}),
                         encoding="utf-8")
    os.environ["WINA_PASS"] = first
    print("qulf o'rnatildi — endi har bir buyruq parol so'raydi (yoki WINA_PASS env)")
    return 0


def cmd_lock_off() -> int:
    if not LOCK_FILE.exists():
        print("qulf yo'q")
        return 0
    if not _lock_check(interactive=True):
        print("noto'g'ri parol")
        return 3
    try:
        LOCK_FILE.unlink()
    except OSError as exc:
        print(f"o'chirishda xato: {exc}")
        return 1
    os.environ.pop("WINA_PASS", None)
    print("qulf olib tashlandi")
    return 0


def _lock_check(interactive: bool = True) -> bool:
    """True if no lock file or passphrase verifies. Verified secret is
    cached into os.environ so daemon child processes inherit it."""
    if not LOCK_FILE.exists():
        return True
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        salt = bytes.fromhex(data["salt"])
        want = data["hash"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"qulf fayli buzilgan: {exc}")
        return False
    secret = os.environ.get("WINA_PASS", "")
    if not secret and interactive and sys.stdin.isatty():
        try:
            secret = getpass.getpass("Wina paroli: ")
        except (EOFError, KeyboardInterrupt):
            return False
    if secret and _lock_hash(secret, salt) == want:
        os.environ["WINA_PASS"] = secret
        return True
    return False


def main(argv: Optional[List[str]] = None) -> int:
    try:
        if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None and hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    if args.cmd == "lock":
        if args.lock_cmd == "set":
            return cmd_lock_set()
        if args.lock_cmd == "off":
            return cmd_lock_off()
        print("qulf: " + ("YOQILGAN" if LOCK_FILE.exists() else "o'chiq"))
        return 0
    if not _lock_check(interactive=True):
        print("ruxsat yo'q: noto'g'ri parol yoki WINA_PASS o'rnatilmagan")
        return 3
    if args.cmd == "run":
        out = run_task_text(args.task, args.provider)
        status = "OK" if out.get("ok") else "FAIL"
        print(f"[{status}] {out.get('error') or 'bajarildi'} "
              f"({out.get('duration_s', 0):.1f}s)")
        for e in out.get("evidence") or []:
            print(f"  - {e}")
        return 0 if out.get("ok") else 1
    if args.cmd == "telegram":
        out = _run_scenario(args.contact, args.message)
        status = "OK" if out.get("ok") else "FAIL"
        print(f"[{status}] {out.get('error') or 'yuborildi'} "
              f"({out.get('duration_s', 0):.1f}s)")
        for e in out.get("evidence") or []:
            print(f"  - {e}")
        return 0 if out.get("ok") else 1
    if args.cmd == "daemon":
        if args.daemon_cmd == "start":
            return cmd_daemon_start(bool(args.hidden))
        if args.daemon_cmd == "stop":
            return cmd_daemon_stop()
        if args.daemon_cmd == "status":
            return cmd_daemon_status()
        return daemon_loop()
    if args.cmd == "queue":
        return cmd_queue_add(args.task, args.provider)
    if args.cmd == "list":
        return cmd_list()
    return 2


if __name__ == "__main__":
    sys.exit(main())
