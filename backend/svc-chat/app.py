#!/usr/bin/env python3
"""svc-chat — stdlib polling chat API + keyword bot. Port 8005."""
import json
import re
import sqlite3
import time
import urllib.parse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / "data" / "chat.db"
PORT = 8005
CHANNEL_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB))
    c.execute("""CREATE TABLE IF NOT EXISTS messages(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      channel TEXT NOT NULL, [user] TEXT NOT NULL,
      text TEXT NOT NULL, created TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ch_id ON messages(channel,id)")
    return c


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bot_reply(text):
    t = text.lower()
    if any(k in t for k in ("narx", "price", "cost", "pul", "summa")):
        return ("Narx loyiha hajmiga bog'liq: landing $150+, shop/SaaS $400+. "
                "Aniq smeta uchun TЗ yuboring — 1 soatda hisoblayman.")
    if any(k in t for k in ("stack", "texnolog", "fastapi", "react", "python", "flutter")):
        return ("Stack: Python stdlib backend + HTML/CSS/JS frontend. "
                "Kerak bo'lsa FastAPI/React/Flutter varianti ham bor.")
    if any(k in t for k in ("muddat", "timeline", "deadline", "vaqt", "qachon", "necha kun")):
        return ("Muddat: landing 2-3 kun, shop/SaaS 1-2 hafta. Shoshilinch bo'lsa +30% tezlashtiraman.")
    if any(k in t for k in ("salom", "hello", "assalom")):
        return "Salom! Savolingizni yozing (narx / stack / muddat)."
    return None


class H(BaseHTTPRequestHandler):
    server_version = "svc-chat/1.0"

    def _send(self, code, obj):
        b = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        if u.path in ("/api/health", "/health"):
            return self._send(200, {"ok": True, "svc": "chat", "time": now_iso()})
        if u.path == "/api/channels":
            c = db()
            rows = c.execute("SELECT DISTINCT channel FROM messages ORDER BY 1").fetchall()
            c.close()
            return self._send(200, {"channels": [r[0] for r in rows]})
        m = re.match(r"^/api/chat/([^/]+)$", u.path)
        if m:
            ch = urllib.parse.unquote(m.group(1))
            if not CHANNEL_RE.match(ch):
                return self._send(400, {"error": "bad channel"})
            try:
                limit = max(1, min(200, int(q.get("limit", ["50"])[0])))
            except ValueError:
                return self._send(400, {"error": "bad limit"})
            after = q.get("after", ["0"])[0]
            try:
                after = max(0, int(after))
            except ValueError:
                return self._send(400, {"error": "bad after"})
            c = db()
            rows = c.execute(
                "SELECT id,channel,[user],text,created FROM messages "
                "WHERE channel=? AND id>? ORDER BY id DESC LIMIT ?",
                (ch, after, limit)).fetchall()
            c.close()
            msgs = [{"id": r[0], "channel": r[1], "user": r[2],
                     "text": r[3], "created": r[4]} for r in reversed(rows)]
            return self._send(200, {"channel": ch, "messages": msgs})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        m = re.match(r"^/api/chat/([^/]+)$", urllib.parse.urlparse(self.path).path)
        if not m:
            return self._send(404, {"error": "not found"})
        ch = urllib.parse.unquote(m.group(1))
        if not CHANNEL_RE.match(ch):
            return self._send(400, {"error": "bad channel"})
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = 0
        if n > 16 * 1024:
            return self._send(413, {"error": "too large"})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad json"})
        user = str(body.get("user", "")).strip()[:32] or "anon"
        text = str(body.get("text", "")).strip()
        if not text or len(text) > 2000:
            return self._send(400, {"error": "text 1..2000 chars"})
        c = db()
        ts = now_iso()
        cur = c.execute(
            "INSERT INTO messages(channel,[user],text,created) VALUES(?,?,?,?)",
            (ch, user, text, ts))
        mid = cur.lastrowid
        msg = {"id": mid, "channel": ch, "user": user, "text": text, "created": ts}
        out = {"message": msg, "bot": None}
        rep = bot_reply(text)
        if rep:
            bts = now_iso()
            bid = c.execute(
                "INSERT INTO messages(channel,[user],text,created) VALUES(?,?,?,?)",
                (ch, "bot", rep, bts)).lastrowid
            c.commit()
            out["bot"] = {"id": bid, "channel": ch, "user": "bot",
                          "text": rep, "created": bts}
        else:
            c.commit()
        c.close()
        self._send(201, out)

    def log_message(self, f, *a):
        print("[chat] " + f % a, flush=True)


if __name__ == "__main__":
    db().close()
    print(f"svc-chat on :{PORT} db={DB}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
