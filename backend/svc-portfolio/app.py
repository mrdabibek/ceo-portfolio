#!/usr/bin/env python3
"""svc-portfolio — stdlib HTTP + SQLite portfolio catalog. Port 8003."""
import json
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs, unquote

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "portfolio.db")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "changeme")
PORT = int(os.environ.get("PORT", "8003"))


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute(
        "CREATE TABLE IF NOT EXISTS projects"
        "(slug TEXT PRIMARY KEY,title TEXT NOT NULL,cat TEXT NOT NULL,"
        "price REAL NOT NULL DEFAULT 0,demo TEXT NOT NULL DEFAULT '')"
    )
    return c


class H(BaseHTTPRequestHandler):
    server_version = "svc-portfolio/1.0"

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,X-Admin-Token,Authorization")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(200, {"ok": True})

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            return self._send(200, {"ok": True, "svc": "portfolio"})
        if u.path == "/api/projects":
            cat = parse_qs(u.query).get("cat", [None])[0]
            c = db()
            rows = (
                c.execute("SELECT slug,title,cat,price,demo FROM projects WHERE cat=? ORDER BY slug", (cat,)).fetchall()
                if cat
                else c.execute("SELECT slug,title,cat,price,demo FROM projects ORDER BY slug").fetchall()
            )
            c.close()
            return self._send(200, [dict(r) for r in rows])
        if u.path.startswith("/api/projects/"):
            slug = unquote(u.path[len("/api/projects/"):].split("/")[0])
            c = db()
            r = c.execute("SELECT slug,title,cat,price,demo FROM projects WHERE slug=?", (slug,)).fetchone()
            c.close()
            if not r:
                return self._send(404, {"error": "not found"})
            return self._send(200, dict(r))
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if u.path != "/api/projects":
            return self._send(404, {"error": "not found"})
        token = self.headers.get("X-Admin-Token") or self.headers.get("Authorization", "").replace("Bearer ", "")
        if token != ADMIN_TOKEN:
            return self._send(401, {"error": "admin only"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            p = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        if not all(k in p for k in ("slug", "title", "cat")):
            return self._send(400, {"error": "slug,title,cat required"})
        c = db()
        try:
            c.execute(
                "INSERT OR REPLACE INTO projects(slug,title,cat,price,demo) VALUES(?,?,?,?,?)",
                (p["slug"], p["title"], p["cat"], float(p.get("price", 0)), p.get("demo", "")),
            )
            c.commit()
        except Exception as e:
            c.close()
            return self._send(400, {"error": str(e)})
        r = c.execute("SELECT slug,title,cat,price,demo FROM projects WHERE slug=?", (p["slug"],)).fetchone()
        c.close()
        return self._send(201, dict(r))

    def log_message(self, *a):
        pass


class S(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    db().close()
    print(f"svc-portfolio on :{PORT} (db={DB})")
    S(("0.0.0.0", PORT), H).serve_forever()
