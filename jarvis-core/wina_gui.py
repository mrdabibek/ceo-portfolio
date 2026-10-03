"""WINA :: wina_gui.py :: dark chat GUI (customtkinter).

Windows 10/11, Python 3.11+. Barcha og'ir ish alohida thread'da —
UI hech qachon qotmaydi (worker -> queue.Queue -> app.after).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import queue
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

import customtkinter as ctk

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import agent_core  # noqa: E402

try:
    import brain  # noqa: E402,F401
    BRAIN_OK = True
except Exception:
    BRAIN_OK = False

try:
    import voice  # noqa: E402
    VOICE_OK = True
except Exception:
    voice = None  # type: ignore[assignment]
    VOICE_OK = False

# --- ranglar / konstanta -----------------------------------------------------
BG = "#0F1115"
PANEL = "#161A22"
BUBBLE_WINA = "#23262E"
ACCENT = "#7C5CFF"
ACCENT_HOVER = "#6A4DE6"
ERROR_BG = "#5A1F24"
TEXT_DIM = "#8B93A5"
PROVIDERS = ("groq", "deepseek", "mistral", "gemini")

QUEUE_DIR = BASE_DIR / "queue"
PID_FILE = BASE_DIR / "daemon.pid"
LOCK_FILE = BASE_DIR / ".wina_lock"
WINA_FILE = BASE_DIR / "wina.py"
LOG_FILE = BASE_DIR / "logs" / "wina_gui.log"

log = logging.getLogger("wina.gui")


def verify_lock_secret(secret: str) -> bool:
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        salt = bytes.fromhex(data["salt"])
        want = data["hash"]
    except Exception:
        return False
    got = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 200_000).hex()
    return bool(secret) and got == want


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def read_daemon_pid() -> int | None:
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        return pid if pid > 0 else None
    except Exception:
        return None


def queue_count() -> int:
    try:
        return len([p for p in QUEUE_DIR.glob("*.json") if p.is_file()])
    except Exception:
        return 0


class WinaApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        self.title("Wina")
        self.geometry("1100x700")
        self.configure(fg_color=BG)

        self.provider = os.environ.get("WINA_DEFAULT_PROVIDER", "groq").lower()
        if self.provider not in PROVIDERS:
            self.provider = "groq"
        self.voice_enabled = False
        self.busy = False
        self.ui_queue: queue.Queue = queue.Queue()
        self._speak_thread: threading.Thread | None = None

        self._build_layout()
        self._refresh_daemon_label()
        self.after(120, self._pump_queue)
        self.after(5000, self._poll_daemon)

    # --- layout --------------------------------------------------------------
    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        side = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=0, width=240)
        side.grid(row=0, column=0, sticky="nsw")
        side.grid_propagate(False)
        self._build_sidebar(side)

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=(12, 12), pady=(12, 0))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)

        self.chat = ctk.CTkScrollableFrame(main, fg_color=BG, corner_radius=12)
        self.chat.grid(row=0, column=0, sticky="nsew")
        self.chat.grid_columnconfigure(0, weight=1)

        bottom = ctk.CTkFrame(main, fg_color="transparent")
        bottom.grid(row=1, column=0, sticky="ew", pady=(10, 10))
        bottom.grid_columnconfigure(0, weight=1)

        self.entry = ctk.CTkEntry(
            bottom, placeholder_text="Wina'ga yozing...",
            fg_color=PANEL, border_color="#2A2F3A", corner_radius=18, height=42,
        )
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self.on_send())

        self.mic_btn = ctk.CTkButton(
            bottom, text="🎤", width=48, height=42, corner_radius=18,
            fg_color=PANEL, hover_color="#2A2F3A", command=self.on_mic,
        )
        self.mic_btn.grid(row=0, column=1, padx=(0, 8))

        self.speak_btn = ctk.CTkButton(
            bottom, text="🔊: off", width=76, height=42, corner_radius=18,
            fg_color=PANEL, hover_color="#2A2F3A", command=self.on_toggle_voice,
        )
        self.speak_btn.grid(row=0, column=2, padx=(0, 8))

        self.send_btn = ctk.CTkButton(
            bottom, text="Yuborish", width=110, height=42, corner_radius=18,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self.on_send,
        )
        self.send_btn.grid(row=0, column=3)

        self.status = ctk.CTkLabel(
            self, text="tayyor", anchor="w", text_color=TEXT_DIM,
            fg_color=PANEL, corner_radius=0, height=26,
        )
        self.status.grid(row=1, column=0, columnspan=2, sticky="ew")
        if not VOICE_OK:
            self.mic_btn.configure(state="disabled")
            self.speak_btn.configure(state="disabled")

    def _build_sidebar(self, side: ctk.CTkFrame) -> None:
        ctk.CTkLabel(side, text="✦ Wina", font=("", 20, "bold"),
                     text_color="white").pack(pady=(16, 2), padx=14, anchor="w")
        ctk.CTkLabel(side, text="desktop agent", font=("", 12),
                     text_color=TEXT_DIM).pack(padx=14, anchor="w")

        ctk.CTkLabel(side, text="Daemon", font=("", 13, "bold"),
                     text_color=TEXT_DIM).pack(pady=(16, 6), padx=14, anchor="w")
        self.daemon_lbl = ctk.CTkLabel(side, text="daemon: stopped",
                                       font=("", 12), text_color=TEXT_DIM)
        self.daemon_lbl.pack(padx=14, anchor="w")
        row = ctk.CTkFrame(side, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=6)
        ctk.CTkButton(row, text="Start", width=90, corner_radius=12,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self.on_daemon_start).pack(side="left", padx=(0, 6))
        ctk.CTkButton(row, text="Stop", width=90, corner_radius=12,
                      fg_color="#2A2F3A", command=self.on_daemon_stop).pack(side="left")
        self.queue_lbl = ctk.CTkLabel(side, text="Queue: 0",
                                      font=("", 12), text_color=TEXT_DIM)
        self.queue_lbl.pack(padx=14, pady=(2, 0), anchor="w")

        ctk.CTkLabel(side, text="Provayder", font=("", 13, "bold"),
                     text_color=TEXT_DIM).pack(pady=(14, 6), padx=14, anchor="w")
        self.provider_menu = ctk.CTkOptionMenu(
            side, values=list(PROVIDERS), corner_radius=12,
            fg_color="#2A2F3A", button_color=ACCENT, button_hover_color=ACCENT_HOVER,
            command=self.on_provider,
        )
        self.provider_menu.set(self.provider)
        self.provider_menu.pack(fill="x", padx=14)

        ctk.CTkLabel(side, text="Telegram", font=("", 13, "bold"),
                     text_color=TEXT_DIM).pack(pady=(14, 6), padx=14, anchor="w")
        self.tg_contact = ctk.CTkEntry(side, placeholder_text="Kontakt",
                                       fg_color=BG, border_color="#2A2F3A", corner_radius=12)
        self.tg_contact.pack(fill="x", padx=14, pady=(0, 6))
        self.tg_contact.insert(0, getattr(agent_core, "DEFAULT_CONTACT", "Fazliddin"))
        self.tg_msg = ctk.CTkEntry(side, placeholder_text="Xabar",
                                   fg_color=BG, border_color="#2A2F3A", corner_radius=12)
        self.tg_msg.pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkButton(side, text="Telegram Yuborish", corner_radius=12,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self.on_telegram).pack(fill="x", padx=14, pady=(0, 6))
        ctk.CTkButton(side, text="📷 Screenshot", corner_radius=12,
                      fg_color="#2A2F3A",
                      command=self.on_screenshot).pack(fill="x", padx=14, pady=(0, 6))

    # --- chat bubbles --------------------------------------------------------
    def add_message(self, who: str, text: str, kind: str = "wina") -> None:
        text = (text or "").strip() or "(bo'sh)"
        stamp = datetime.now().strftime("%H:%M")
        row = ctk.CTkFrame(self.chat, fg_color="transparent")
        row.pack(fill="x", padx=8, pady=4)
        if who == "user":
            bg, side_, anchor = ACCENT, "right", "e"
        elif kind == "error":
            bg, side_, anchor = ERROR_BG, "left", "w"
        else:
            bg, side_, anchor = BUBBLE_WINA, "left", "w"
        bubble = ctk.CTkFrame(row, fg_color=bg, corner_radius=14)
        bubble.pack(side=side_, padx=6)
        ctk.CTkLabel(bubble, text=text, wraplength=520, justify="left",
                     anchor="w", text_color="white").pack(padx=12, pady=(8, 0), anchor="w")
        ctk.CTkLabel(bubble, text=stamp, font=("", 10),
                     text_color="#C9CFDB").pack(padx=12, pady=(2, 8), anchor=anchor)
        self.chat._parent_canvas.yview_moveto(1.0)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    # --- thread pump ----------------------------------------------------------
    def _pump_queue(self) -> None:
        try:
            while True:
                msg = self.ui_queue.get_nowait()
                kind = msg[0]
                if kind == "wina":
                    _, text = msg
                    self.add_message("wina", text)
                    if self.voice_enabled:
                        self._speak(text)
                elif kind == "error":
                    _, text = msg
                    self.add_message("wina", text, kind="error")
                elif kind == "mic_text":
                    _, text = msg
                    self.entry.delete(0, "end")
                    self.entry.insert(0, text)
                    self.mic_btn.configure(text="🎤", state="normal")
                    self.on_send()
                elif kind == "mic_fail":
                    _, text = msg
                    self.mic_btn.configure(text="🎤", state="normal")
                    self.add_message("wina", text, kind="error")
                elif kind == "status":
                    _, text = msg
                    self.set_status(text)
                elif kind == "done":
                    self.busy = False
                    self.send_btn.configure(state="normal", text="Yuborish")
                    self._refresh_daemon_label()
        except queue.Empty:
            pass
        self.after(120, self._pump_queue)

    # --- chat send ------------------------------------------------------------
    def on_send(self) -> None:
        if self.busy:
            return
        task = self.entry.get().strip()
        if not task:
            return
        self.entry.delete(0, "end")
        self.add_message("user", task)
        self.busy = True
        self.send_btn.configure(state="disabled", text="...")
        self.ui_queue.put(("status", "ishlayapti..."))
        threading.Thread(target=self._run_task,
                         args=(task, self.provider), daemon=True).start()

    def _run_task(self, task: str, provider: str) -> None:
        try:
            res = agent_core.run_generic_task(task, provider=provider)
            lines = [f"{'✅' if res.ok else '❌'} {res.scenario}: "
                     f"{res.steps_passed}/{res.steps_total} ({res.duration_s:.1f}s)"]
            if res.error:
                lines.append(res.error)
            if res.evidence:
                lines.append("📎 " + ", ".join(res.evidence[:3]))
            self.ui_queue.put(("wina" if res.ok else "error", "\n".join(lines)))
            try:
                log.info("task done ok=%s steps=%s/%s", res.ok,
                         res.steps_passed, res.steps_total)
            except Exception:
                pass
        except Exception as exc:
            self.ui_queue.put(("error", f"Xato: {type(exc).__name__}: {exc}"))
        finally:
            self.ui_queue.put(("status", "tayyor"))
            self.ui_queue.put(("done",))

    # --- mic / voice ------------------------------------------------------------
    def on_mic(self) -> None:
        if not VOICE_OK:
            self.add_message("wina", "Mikrofon moduli topilmadi (voice.py yo'q).",
                             kind="error")
            return
        self.mic_btn.configure(text="Eshitmoqda...", state="disabled")
        self.ui_queue.put(("status", "eshitmoqda..."))
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self) -> None:
        try:
            text = voice.listen(6)  # type: ignore[union-attr]
            text = (text or "").strip()
            if not text:
                self.ui_queue.put(("mic_fail", "Eshitilmadi — qayta urinib ko'ring."))
            else:
                self.ui_queue.put(("mic_text", text))
            self.ui_queue.put(("status", "tayyor"))
        except Exception as exc:
            self.ui_queue.put(("mic_fail", f"Mikrofon xatosi: {type(exc).__name__}: {exc}"))

    def on_toggle_voice(self) -> None:
        if not VOICE_OK:
            return
        self.voice_enabled = not self.voice_enabled
        self.speak_btn.configure(text="🔊: on" if self.voice_enabled else "🔊: off")
        if not self.voice_enabled:
            self._stop_speaking()

    def _speak(self, text: str) -> None:
        self._stop_speaking()
        t = threading.Thread(target=self._speak_worker, args=(text,), daemon=True)
        self._speak_thread = t
        t.start()

    def _speak_worker(self, text: str) -> None:
        try:
            voice.speak(text)  # type: ignore[union-attr]
        except Exception as exc:
            self.ui_queue.put(("error", f"Ovoz xatosi: {type(exc).__name__}: {exc}"))

    def _stop_speaking(self) -> None:
        try:
            stop = getattr(voice, "stop", None)
            if callable(stop):
                stop()
        except Exception:
            pass
        self._speak_thread = None

    # --- daemon -----------------------------------------------------------------
    def _refresh_daemon_label(self) -> None:
        pid = read_daemon_pid()
        running = pid is not None and pid_alive(pid)
        n = queue_count()
        state = f"running PID {pid}" if running else "stopped"
        self.daemon_lbl.configure(text=f"daemon: {state}")
        self.queue_lbl.configure(text=f"Queue: {n}")
        if not self.busy:
            self.set_status(f"tayyor | daemon:{'running' if running else 'stopped'}")

    def _poll_daemon(self) -> None:
        try:
            self._refresh_daemon_label()
        finally:
            self.after(5000, self._poll_daemon)

    def _daemon_cmd(self, action: str) -> None:
        self.ui_queue.put(("status", f"daemon {action}..."))
        try:
            proc = subprocess.run(
                [sys.executable, str(WINA_FILE), "daemon", action],
                capture_output=True, text=True, timeout=30, cwd=str(BASE_DIR),
            )
            out = (proc.stdout or "").strip() or (proc.stderr or "").strip() or action
            self.ui_queue.put(("wina", f"Daemon {action}: {out}"))
        except Exception as exc:
            self.ui_queue.put(("error", f"Daemon {action} xatosi: {type(exc).__name__}: {exc}"))
        self.ui_queue.put(("status", "tayyor"))
        self.ui_queue.put(("done",))

    def on_daemon_start(self) -> None:
        threading.Thread(target=self._daemon_cmd, args=("start",), daemon=True).start()

    def on_daemon_stop(self) -> None:
        threading.Thread(target=self._daemon_cmd, args=("stop",), daemon=True).start()

    # --- telegram -----------------------------------------------------------------
    def on_telegram(self) -> None:
        contact = self.tg_contact.get().strip()
        message = self.tg_msg.get().strip()
        if not contact or not message:
            self.add_message("wina", "Kontakt va xabarni kiriting.", kind="error")
            return
        self.add_message("user", f"Telegram → {contact}: {message}")
        self.ui_queue.put(("status", "telegram yuborilmoqda..."))
        threading.Thread(target=self._run_telegram,
                         args=(contact, message), daemon=True).start()

    def _run_telegram(self, contact: str, message: str) -> None:
        try:
            res = agent_core.run_telegram_scenario(contact, message)
            if res.ok:
                self.ui_queue.put(("wina", f"✅ Telegram: '{contact}' ga yuborildi "
                                           f"({res.steps_passed}/{res.steps_total}, "
                                           f"{res.duration_s:.1f}s)"))
            else:
                self.ui_queue.put(("error", f"❌ Telegram: {res.error or 'muvaffaqiyatsiz'}"))
        except Exception as exc:
            self.ui_queue.put(("error", f"Telegram xatosi: {type(exc).__name__}: {exc}"))
        finally:
            self.ui_queue.put(("status", "tayyor"))

    # --- provider / screenshot ------------------------------------------------------
    def on_provider(self, value: str) -> None:
        value = (value or "").strip().lower()
        if value not in PROVIDERS:
            return
        self.provider = value
        os.environ["WINA_DEFAULT_PROVIDER"] = value
        self.add_message("wina", f"Provayder: {value}")

    def on_screenshot(self) -> None:
        self.ui_queue.put(("status", "screenshot..."))
        threading.Thread(target=self._take_shot, daemon=True).start()

    def _take_shot(self) -> None:
        try:
            path = agent_core.TOOLS.take_screenshot()
            self.ui_queue.put(("wina", f"📷 Screenshot: {path}"))
        except Exception as exc:
            self.ui_queue.put(("error", f"Screenshot xatosi: {type(exc).__name__}: {exc}"))
        finally:
            self.ui_queue.put(("status", "tayyor"))


def ask_lock(app: ctk.CTk) -> bool:
    dlg = ctk.CTkInputDialog(text="Wina paroli:", title="Wina — qulf")
    secret = dlg.get_input()
    if not secret:
        return False
    return verify_lock_secret(secret)


def main() -> int:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO,
                        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    app = WinaApp()
    # NOTE: CTk 6.x da withdraw()+deiconify() oynani qayta chiqarmaydi,
    # shuning uchun oynani yashirmaymiz — parol dialogi baribir modal.
    if LOCK_FILE.exists():
        import os as _os
        env_secret = _os.environ.get("WINA_PASS", "")
        ok = bool(env_secret) and verify_lock_secret(env_secret)
        if not ok:
            ok = ask_lock(app)
        if not ok:
            app.destroy()
            return 1
    app.lift()
    app.add_message("wina", "Salom! Men Wina. Nima qilishni buyurasiz?")
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
