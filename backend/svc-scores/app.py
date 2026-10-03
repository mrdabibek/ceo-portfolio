#!/usr/bin/env python3
"""svc-scores — stdlib game-score API on :8011."""
import json
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

BASE = Path(__file__).resolve().parent
DB = BASE / "data" / "scores.db"
GAMES = ("2048", "platformer", "racer", "reflex")
MAX_VALUE = 10000


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS scores(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      game TEXT NOT NULL, name TEXT NOT NULL,
      value INTEGER NOT NULL, created TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_scores_game_value ON scores(game, value DESC)")
    return c


def check_body(game, name, value):
    if game not in GAMES:
        return f"game must be one of {', '.join(GAMES)}"
    if not name or len(name) > 32:
        return "name required, max 32 chars"
    if isinstance(value, bool) or value is None:
        return "value must be a positive integer 1..10000"
    try:
        v = int(value)
    except (ValueError, TypeError):
        return "value must be a positive integer 1..10000"
    if isinstance(value, float) and not value.is_integer():
        return "value must be a positive integer 1..10000"
    if not 1 <= v <= MAX_VALUE:
        return "value must be a positive integer 1..10000"
    return None


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
            return self._send(200, {"ok": True, "svc": "scores"})
        game = None
        if u.path.startswith("/api/scores/"):
            game = unquote(u.path[len("/api/scores/"):].split("/")[0])
        elif u.path == "/api/scores":
            game = parse_qs(u.query).get("game", [""])[0]
        if game or u.path.startswith("/api/scores"):
            if game not in GAMES:
                return self._send(404, {"error": f"unknown game, use one of {', '.join(GAMES)}"})
            q = parse_qs(u.query)
            try:
                limit = min(max(int(q.get("limit", ["10"])[0]), 1), 100)
            except ValueError:
                limit = 10
            con = db()
            rows = con.execute(
                "SELECT id,game,name,value,created FROM scores WHERE game=? ORDER BY value DESC,id ASC LIMIT ?",
                (game, limit)).fetchall()
            con.close()
            return self._send(200, {"game": game, "items": [dict(r) for r in rows]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/scores":
            return self._send(404, {"error": "not found"})
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 8192)
        except ValueError:
            n = 0
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid json"})
        game = str(body.get("game", "")).strip()
        name = str(body.get("name", "")).strip()
        err = check_body(game, name, body.get("value"))
        if err:
            return self._send(422, {"error": err})
        con = db()
        cur = con.execute(
            "INSERT INTO scores(game,name,value,created) VALUES(?,?,?,?)",
            (game, name[:32], int(body["value"]),
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        con.commit()
        row = con.execute(
            "SELECT id,game,name,value,created FROM scores WHERE id=?", (cur.lastrowid,)).fetchone()
        con.close()
        return self._send(201, dict(row))

    def log_message(self, *a):
        pass


class S(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8011
    print(f"svc-scores on :{port}", flush=True)
    S(("", port), H).serve_forever()
