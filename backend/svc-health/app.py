#!/usr/bin/env python3
"""svc-health — stdlib aggregator on :8012. No writes, only reads.

GET /api/health -> {ok, services:[{name,port,status,code,ms}]} (1s timeout each, parallel)
GET /api/stats  -> {ok, databases:[{svc,file,tables,total}]} (sqlite read-only)
"""
import glob
import json
import os
import sqlite3
import sys
import time
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", "8012"))
TIMEOUT = 1.0

# port -> svc name (gateway ROUTES + BACKEND-README mapping, 8001-8011)
SERVICES = [
    ("auth", 8001),
    ("contact", 8022),
    ("portfolio", 8023),
    ("shop", 8004),
    ("chat", 8005),
    ("checkout", 8006),
    ("files", 8007),
    ("ai", 8008),
    ("estimate", 8009),
    ("news", 8010),
    ("scores", 8011),
]


def _open(url):
    """Localhost checks must not go through env proxies (adds latency)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(url, timeout=TIMEOUT)


def probe(name, port):
    """Reachable (any HTTP status) -> up; 2xx on a health path -> healthy.

    One 1s timeout per service: /health is only tried when /api/health
    answered 404 (service alive, different path). Connection errors -> down.
    """
    t0 = time.monotonic()
    code = None
    try:
        with _open(f"http://127.0.0.1:{port}/api/health") as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code  # process alive
        if e.code == 404:
            try:
                with _open(f"http://127.0.0.1:{port}/health") as r:
                    code = r.status
            except urllib.error.HTTPError as e2:
                code = e2.code
            except Exception:
                pass  # keep original 404: alive but no health path
    except Exception:
        code = None
    ms = round((time.monotonic() - t0) * 1000, 1)
    up = code is not None
    return {"name": name, "port": port, "status": "up" if up else "down",
            "code": code, "ms": ms}


def check_all():
    with ThreadPoolExecutor(max_workers=len(SERVICES)) as ex:
        rows = list(ex.map(lambda s: probe(*s), SERVICES))
    rows.sort(key=lambda r: r["port"])
    return rows


def count_db(path):
    """Open read-only (mode=ro creates nothing), count rows per user table."""
    con = sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        counts = {}
        for t in tables:
            try:
                counts[t] = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.Error as e:
                counts[t] = f"error: {e}"
        return {"tables": counts, "total": sum(v for v in counts.values() if isinstance(v, int))}
    finally:
        con.close()


def stats_all():
    pattern = os.path.join(BASE, "..", "*", "data", "*.db")
    out = []
    for path in sorted(glob.glob(pattern)):
        svc = os.path.basename(os.path.dirname(os.path.dirname(path)))
        rel = os.path.relpath(path, os.path.join(BASE, "..")).replace(os.sep, "/")
        try:
            info = count_db(path)
            out.append({"svc": svc, "file": rel, **info})
        except sqlite3.Error as e:
            out.append({"svc": svc, "file": rel, "error": str(e)})
    return out


class H(BaseHTTPRequestHandler):
    server_version = "svc-health/1.0"

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/api/health", "/health"):
            services = check_all()
            ok = all(s["status"] == "up" for s in services)
            return self._send(200 if ok else 503, {"ok": ok, "services": services})
        if path == "/api/stats":
            return self._send(200, {"ok": True, "databases": stats_all()})
        if path == "/":
            return self._send(200, {"ok": True, "svc": "health"})
        return self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"svc-health on :{PORT} (read-only aggregator, no db writes)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
