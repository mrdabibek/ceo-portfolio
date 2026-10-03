"""WINA brain — multi-provider LLM interface + AAP task planner.

Providers (keys ONLY from environment, loaded from sibling .env):
  groq     — https://api.groq.com/openai/v1/chat/completions (llama-3.3-70b-versatile)
  deepseek — https://api.deepseek.com/chat/completions (deepseek-chat)
  mistral  — https://api.mistral.ai/v1/chat/completions (mistral-large-latest)
  gemini   — https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent (gemini-2.0-flash)

Stdlib only (urllib). No third-party dependencies.
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("wina.brain")

ALLOWED_PRIMITIVES = frozenset({
    "launch_app",
    "launch_or_focus",
    "wait_window",
    "focus_window",
    "screenshot",
    "hotkey",
    "press_key",
    "click",
    "type_text",
    "wait",
    "verify_window",
    "verify_ocr_contains",
    "verify_chat_contains",
    "execute_terminal",
    "read_file",
    "write_file",
    "list_dir",
    "fetch_url",
})

_PROVIDER_ORDER = ("groq", "deepseek", "mistral", "gemini")

_MODELS = {
    "groq": "llama-3.3-70b-versatile",
    "deepseek": "deepseek-chat",
    "mistral": "mistral-small-latest",
    "gemini": "gemini-2.5-flash",
}

_URLS = {
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
}

_ENV_KEYS = {
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

_PLANNER_SYSTEM = """You are WINA planner. Convert the user's natural-language task into an Atomic Action Primitives (AAP) plan.

Allowed primitives (use ONLY these values in the "primitive" field). Params + one example step each:
- launch_app {app}: launch an app. Ex: {"id": 1, "description": "Open Notepad", "primitive": "launch_app", "app": "notepad.exe"}
- launch_or_focus {app_name, executable_path, window_title_pattern}: open app or focus it if already running. Ex: {"id": 1, "description": "Open or focus Chrome", "primitive": "launch_or_focus", "app_name": "Chrome", "executable_path": "C:\\\\Program Files\\\\Google\\\\Chrome\\\\Application\\\\chrome.exe", "window_title_pattern": "Chrome"}
- wait_window {title_substring, timeout_s}: wait for a window to appear. Ex: {"id": 2, "description": "Wait for Notepad window", "primitive": "wait_window", "title_substring": "Notepad", "timeout_s": 15}
- focus_window {title_substring}: bring window to front. Ex: {"id": 3, "description": "Focus Notepad", "primitive": "focus_window", "title_substring": "Notepad"}
- screenshot {label}: capture screen. Ex: {"id": 4, "description": "Capture editor state", "primitive": "screenshot", "label": "before_edit"}
- hotkey {keys[]}: press key combination, list of key names. Ex: {"id": 5, "description": "Save file", "primitive": "hotkey", "keys": ["ctrl", "s"]}
- press_key {key}: press one key. Ex: {"id": 6, "description": "Confirm dialog", "primitive": "press_key", "key": "enter"}
- click {x, y}: left-click at screen coordinates. Ex: {"id": 7, "description": "Click Save button", "primitive": "click", "x": 850, "y": 420}
- type_text {text}: type text into focused control. Ex: {"id": 8, "description": "Type hello", "primitive": "type_text", "text": "hello"}
- wait {seconds}: pause. Ex: {"id": 9, "description": "Let UI settle", "primitive": "wait", "seconds": 2}
- verify_window {window}: assert a window exists/open. Ex: {"id": 10, "description": "Verify Notepad open", "primitive": "verify_window", "window": "Notepad"}
- verify_ocr_contains {contains, retries}: assert screen OCR text contains string. Ex: {"id": 11, "description": "Verify hello visible", "primitive": "verify_ocr_contains", "contains": "hello", "retries": 3}
- verify_chat_contains {contains}: assert chat/LLM answer contains string. Ex: {"id": 12, "description": "Verify answer mentions report", "primitive": "verify_chat_contains", "contains": "report"}
- execute_terminal {command, timeout_s}: run shell command. Ex: {"id": 13, "description": "List work dir", "primitive": "execute_terminal", "command": "dir", "timeout_s": 30}
- read_file {path}: read file content. Ex: {"id": 14, "description": "Read notes", "primitive": "read_file", "path": "C:\\\\Work\\\\notes.txt"}
- write_file {path, content, append}: write/append file. Ex: {"id": 15, "description": "Save draft", "primitive": "write_file", "path": "C:\\\\Work\\\\draft.txt", "content": "hello", "append": false}
- list_dir {path, pattern}: list directory with glob. Ex: {"id": 16, "description": "List PDFs", "primitive": "list_dir", "path": "C:\\\\Work", "pattern": "*.pdf"}
- fetch_url {url}: HTTP GET a URL. Ex: {"id": 17, "description": "Fetch site status", "primitive": "fetch_url", "url": "https://example.com"}

Rules:
- Output ONLY a JSON array, no prose, no markdown outside the JSON (a single ```json fence is tolerated).
- Every step MUST be an object with required keys: "id" (integer, starting at 1, sequential), "description" (non-empty string), "primitive" (one of the allowed primitives). Extra keys are that primitive's params.
- Plan length MUST be 3-15 steps: never 1-2 steps, never more than 15. Split or merge steps to fit.
- Before any GUI typing/clicking (type_text, click, hotkey, press_key), first focus the target with focus_window (or launch_or_focus). Never type into an unfocused window.
- verify_* steps ONLY against a result that was read or produced earlier in the plan (read_file/write_file/fetch_url/execute_terminal output, screenshot OCR, visible window). Never verify invented text.
- File and internet jobs (read_file, write_file, list_dir, fetch_url, execute_terminal) run WITHOUT GUI: do not open Notepad/browser just to read a file or URL.
- FORBIDDEN destructive commands in execute_terminal: format, mkfs, dd, diskpart, "rm -rf /", "rm -rf /*", "rm -rf ~", rmdir /s, "del /f /s /q C:\\\\Windows", any command deleting system roots or user home. If the task asks for one, refuse that step and plan a safe alternative.

Example (file task — note: NO Notepad, NO browser, direct primitives only):
[
  {"id": 1, "description": "Write draft file", "primitive": "write_file", "path": "C:\\\\Work\\\\draft.txt", "content": "hello", "append": false},
  {"id": 2, "description": "Read back the file", "primitive": "read_file", "path": "C:\\\\Work\\\\draft.txt"},
  {"id": 3, "description": "Verify content written", "primitive": "verify_ocr_contains", "contains": "hello", "retries": 2}
]

HARD RULES (violating any of these invalidates the whole plan):
- To create/edit/read a file use ONLY write_file/read_file. NEVER launch Notepad or any editor to handle file content.
- To read a web page use ONLY fetch_url. NEVER open a browser for reading.
- GUI primitives (click/type_text/hotkey/press_key/launch_app) are ONLY for tasks that explicitly name a desktop application (e.g. Telegram, Discord, Spotify).
- Paths: "joriy papka", "current directory", "shu yerda" mean the working directory itself — use the bare file name (e.g. "WINA_DEMO.txt"), NEVER create a subfolder literally named "Joriy" or "current".
"""


def load_env() -> dict[str, str]:
    """Load sibling .env (same dir as this file) into os.environ. Returns loaded mapping."""
    env_path = Path(__file__).resolve().parent / ".env"
    loaded: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        log.debug("wina.brain: no .env at %s", env_path)
        return loaded
    except OSError as exc:
        log.warning("wina.brain: cannot read .env %s: %s", env_path, exc)
        return loaded
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            log.debug("wina.brain: skipping malformed .env line %d", lineno)
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            log.debug("wina.brain: skipping bad key on .env line %d", lineno)
            continue
        loaded[key] = value
        if key not in os.environ:
            os.environ[key] = value
    log.debug("wina.brain: loaded %d vars from %s", len(loaded), env_path)
    return loaded


def _provider_order() -> list[str]:
    preferred = (os.environ.get("WINA_DEFAULT_PROVIDER") or "").strip().lower()
    order = list(_PROVIDER_ORDER)
    if preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    elif preferred:
        log.warning("wina.brain: unknown WINA_DEFAULT_PROVIDER=%r, ignoring", preferred)
    return order


def _available_providers() -> list[tuple[str, str]]:
    load_env()
    out: list[tuple[str, str]] = []
    for name in _provider_order():
        key = (os.environ.get(_ENV_KEYS[name]) or "").strip().strip("'\"")
        if key:
            out.append((name, key))
        else:
            log.debug("wina.brain: provider %s skipped (no %s)", name, _ENV_KEYS[name])
    return out


def _post_json(url: str, payload: dict, headers: dict[str, str], timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            body = ""
        raise RuntimeError(f"HTTP {exc.code} {exc.reason} {body}".strip()) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"network error: {exc.reason}") from exc
    except TimeoutError as exc:
        raise RuntimeError("request timed out") from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON response: {raw[:300]}") from exc


def _chat_openai_compatible(provider: str, key: str, url: str, prompt: str,
                             system: str | None, timeout: int) -> str:
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload = {"model": _MODELS[provider], "messages": messages, "temperature": 0.2}
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = _post_json(url, payload, headers, timeout)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected response shape: {str(body)[:300]}") from exc
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"empty content in response: {str(body)[:300]}")
    return content.strip()


def _chat_gemini(key: str, prompt: str, system: str | None, timeout: int) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={key}"
    combined = f"{system}\n\n{prompt}" if system else prompt
    payload = {"contents": [{"parts": [{"text": combined}]}]}
    headers = {"Content-Type": "application/json"}
    body = _post_json(url, payload, headers, timeout)
    try:
        parts = body["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"unexpected gemini response shape: {str(body)[:300]}") from exc
    if not text.strip():
        raise RuntimeError(f"empty content in gemini response: {str(body)[:300]}")
    return text.strip()


def _chat_single(provider: str, key: str, prompt: str, system: str | None, timeout: int) -> str:
    if provider == "gemini":
        return _chat_gemini(key, prompt, system, timeout)
    return _chat_openai_compatible(provider, key, _URLS[provider], prompt, system, timeout)


def chat(prompt: str, system: str | None = None, timeout: int = 60) -> str:
    """Send prompt via first working provider (WINA_DEFAULT_PROVIDER preferred). Failover on error."""
    providers = _available_providers()
    if not providers:
        raise RuntimeError(
            "wina.brain: no provider API key found (need GROQ_API_KEY, DEEPSEEK_API_KEY, "
            "MISTRAL_API_KEY or GEMINI_API_KEY in env/.env)"
        )
    errors: list[str] = []
    for name, key in providers:
        log.info("wina.brain: trying provider %s (model %s)", name, _MODELS[name])
        try:
            text = _chat_single(name, key, prompt, system, timeout)
        except Exception as exc:
            msg = f"[{name}] {exc}"
            log.warning("wina.brain: provider %s failed: %s", name, exc)
            errors.append(msg)
            continue
        log.info("wina.brain: provider %s succeeded (%d chars)", name, len(text))
        return text
    raise RuntimeError("wina.brain: all providers failed: " + " | ".join(errors))


def _extract_json(text: str) -> object:
    """Parse ```json fenced block or raw JSON from LLM output."""
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    candidate = m.group(1).strip() if m else text.strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    # Fallback: largest [...] or {...} substring.
    for opener, closer in (("[", "]"), ("{", "}")):
        start = candidate.find(opener)
        end = candidate.rfind(closer)
        if 0 <= start < end:
            try:
                return json.loads(candidate[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no valid JSON found in: {text[:300]!r}")


_DANGEROUS_CMD_PATTERNS = (
    r"\bformat\s+[a-z]:",
    r"\bmkfs\b",
    r"\bdd\s+",
    r"\bdiskpart\b",
    r"rm\s+-rf\s+/(?!\S)",
    r"rm\s+-rf\s+/\*",
    r"rm\s+-rf\s+~",
    r"rmdir\s+/s",
    r"del\s+/f\s+/s\s+/q\s+c:\\windows",
)


def _is_dangerous_command(cmd: str) -> bool:
    c = cmd.lower()
    return any(re.search(p, c) for p in _DANGEROUS_CMD_PATTERNS)


_REQUIRED_PARAMS: dict[str, tuple[str, ...]] = {
    "type_text": ("text",),
    "press_key": ("key",),
    "hotkey": ("keys",),
    "write_file": ("path", "content"),
    "read_file": ("path",),
    "fetch_url": ("url",),
    "execute_terminal": ("command",),
    "launch_app": ("app",),
    "wait": ("seconds",),
    "verify_ocr_contains": ("contains",),
    "verify_chat_contains": ("contains",),
}

_NONEMPTY_PARAMS: tuple[str, ...] = ("text", "key", "path", "content", "url", "command", "app")


def _step_params(step: dict) -> dict:
    params = step.get("params")
    merged: dict = dict(params) if isinstance(params, dict) else {}
    for k, v in step.items():
        if k not in merged and k not in ("id", "primitive", "description", "params", "verify", "timeout_s"):
            merged[k] = v
    return merged


def _validate_plan(obj: object) -> list[dict]:
    if isinstance(obj, dict):
        for wrapper in ("steps", "plan", "actions"):
            inner = obj.get(wrapper)
            if isinstance(inner, list):
                obj = inner
                break
        else:
            raise ValueError("plan JSON must be an array of steps (or {\"steps\": [...]})")
    if not isinstance(obj, list) or not obj:
        raise ValueError("plan JSON must be a non-empty array of steps")
    if not 3 <= len(obj) <= 15:
        raise ValueError(f"plan must have 3-15 steps, got {len(obj)}")
    plan: list[dict] = []
    for i, step in enumerate(obj, 1):
        if not isinstance(step, dict):
            raise ValueError(f"step {i} must be an object, got {type(step).__name__}")
        if "id" not in step:
            raise ValueError(f"step {i} missing required key 'id'")
        if "description" not in step or not isinstance(step["description"], str) or not step["description"].strip():
            raise ValueError(f"step {step.get('id', i)!r} missing required non-empty 'description'")
        if "primitive" not in step:
            raise ValueError(f"step {step.get('id', i)!r} missing required key 'primitive'")
        prim = step["primitive"]
        if not isinstance(prim, str) or prim not in ALLOWED_PRIMITIVES:
            raise ValueError(
                f"step {step.get('id', i)!r} has invalid primitive {prim!r}; "
                f"allowed: {sorted(ALLOWED_PRIMITIVES)}"
            )
        if prim == "execute_terminal":
            cmd = str(_step_params(step).get("command", ""))
            if _is_dangerous_command(cmd):
                raise ValueError(f"step {step.get('id', i)!r} forbidden destructive command: {cmd[:80]!r}")
        required = _REQUIRED_PARAMS.get(prim, ())
        if required:
            have = _step_params(step)
            missing = [k for k in required if k not in have]
            if missing:
                raise ValueError(
                    f"step {step.get('id', i)!r} primitive {prim!r} missing required param(s): {missing}"
                )
            empty = [k for k in required if k in _NONEMPTY_PARAMS and not str(have.get(k, "")).strip()]
            if empty:
                raise ValueError(
                    f"step {step.get('id', i)!r} primitive {prim!r} has empty required param(s): {empty}"
                )
        plan.append(dict(step))
    ids = [s["id"] for s in plan]
    if any(not isinstance(v, int) for v in ids):
        raise ValueError("'id' of every step must be an integer")
    return plan


def plan_task(task: str) -> list[dict]:
    """Convert natural-language task into validated AAP plan. Retries once on parse error."""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must be a non-empty string")
    prompt = f"Task: {task.strip()}\nReturn the AAP plan as a JSON array now."
    last_error: Exception | None = None
    previous: str | None = None
    for attempt in (1, 2):
        try:
            if attempt == 1:
                raw = chat(prompt, system=_PLANNER_SYSTEM, timeout=60)
            else:
                fix = (
                    "Your previous response was NOT valid. "
                    f"Error: {last_error}. "
                    f"Previous response:\n{previous}\n\n"
                    "Reply with ONLY a valid JSON array of 3-15 steps. "
                    "Every step needs integer 'id', non-empty 'description', "
                    "'primitive' from the allowed set. No prose."
                )
                raw = chat(fix, system=_PLANNER_SYSTEM, timeout=60)
            plan = _validate_plan(_extract_json(raw))
            log.info("wina.brain: planned %d steps for task %r", len(plan), task[:60])
            return plan
        except (ValueError, RuntimeError) as exc:
            log.warning("wina.brain: plan_task attempt %d failed: %s", attempt, exc)
            last_error = exc
            try:
                previous = raw  # type: ignore[possibly-undefined]
            except UnboundLocalError:
                previous = None
            if attempt == 2:
                raise RuntimeError(f"wina.brain: plan_task failed after retry: {exc}") from exc
    raise RuntimeError("wina.brain: plan_task failed after retry")  # unreachable
