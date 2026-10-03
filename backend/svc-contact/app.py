#!/usr/bin/env python3
"""svc-contact — stdlib contact-lead API on :8002."""
import json
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent
DB = BASE / "data" / "contact.db"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS leads(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, email TEXT NOT NULL,
      budget TEXT DEFAULT '', msg TEXT NOT NULL,
      created TEXT NOT NULL)""")
    return c

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/api/health", "/health"):
            return self._send(200, {"ok": True, "svc": "contact"})
        if u.path == "/api/contact":
            q = parse_qs(u.query)
            try:
                limit = min(max(int(q.get("limit", ["50"])[0]), 1), 200)
            except ValueError:
                limit = 50
            con = db()
            rows = con.execute(
                "SELECT * FROM leads ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            con.close()
            return self._send(200, {"items": [dict(r) for r in rows]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/contact":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid json"})
        name = str(body.get("name", "")).strip()
        email = str(body.get("email", "")).strip()
        msg = str(body.get("msg", "")).strip()
        budget = str(body.get("budget", "")).strip()[:100]
        if not name:
            return self._send(422, {"error": "name required"})
        if not EMAIL_RE.match(email):
            return self._send(422, {"error": "valid email required"})
        if not msg:
            return self._send(422, {"error": "msg required"})
        con = db()
        cur = con.execute(
            "INSERT INTO leads(name,email,budget,msg,created) VALUES(?,?,?,?,?)",
            (name[:200], email[:320], budget, msg[:5000],
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        con.commit()
        lid = cur.lastrowid
        con.close()
        return self._send(201, {"id": lid})

    def log_message(self, *a):
        pass

class S(ThreadingMixIn, HTTPServer):
    daemon_threads = True

if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8002
    print(f"svc-contact on :{port}", flush=True)
    S(("", port), H).serve_forever()
