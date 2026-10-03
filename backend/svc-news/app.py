"""svc-news: stdlib news/posts API on :8010. SQLite at data/news.db."""
import json
import os
import sqlite3
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "news.db")
PORT = int(os.environ.get("PORT", "8010"))

SCHEMA = """CREATE TABLE IF NOT EXISTS posts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  created TEXT NOT NULL
)"""


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute(SCHEMA)
    c.commit()
    return c


def now():
    return datetime.now(timezone.utc).isoformat()


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(b)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/health":
            return self._send(200, {"ok": True})
        if p == "/api/news":
            c = db()
            rows = c.execute("SELECT id,title,body,created FROM posts ORDER BY id DESC").fetchall()
            c.close()
            return self._send(200, [dict(r) for r in rows])
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        p = urlparse(self.path).path
        if p != "/api/news":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid json"})
        title = (data.get("title") or "").strip()
        body = (data.get("body") or "").strip()
        if not title or not body:
            return self._send(400, {"error": "title and body required"})
        c = db()
        cur = c.execute("INSERT INTO posts (title,body,created) VALUES (?,?,?)", (title, body, now()))
        pid = cur.lastrowid
        c.commit()
        row = c.execute("SELECT id,title,body,created FROM posts WHERE id=?", (pid,)).fetchone()
        c.close()
        return self._send(201, dict(row))

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    db().close()
    print(f"svc-news on :{PORT} db=data/news.db", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
