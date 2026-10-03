#!/usr/bin/env python3
"""Stdlib reverse-proxy gateway: :8000 -> :8001-8012."""

import json
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn

TIMEOUT = 5

ROUTES = [
    (("/api/auth",), 8001),
    (("/api/contact",), 8022),
    (("/api/projects",), 8023),
    (("/api/products", "/api/orders"), 8004),
    (("/api/chat",), 8005),
    (("/api/checkout", "/api/invoices"), 8006),
    (("/api/upload", "/api/files"), 8007),
    (("/api/ai",), 8008),
    (("/api/estimate",), 8009),
    (("/api/news",), 8010),
    (("/api/scores",), 8011),
    (("/api/health", "/api/stats"), 8012),
]

PREFIX_MAP = {p: port for prefixes, port in ROUTES for p in prefixes}


def target_for(path):
    base = path.split("?", 1)[0]
    for prefix, port in PREFIX_MAP.items():
        if base == prefix or base.startswith(prefix + "/"):
            return port
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "Gateway/1.0"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        base = self.path.split("?", 1)[0]
        if base == "/api" or base == "/api/":
            routes = sorted(PREFIX_MAP.keys())
            return self._send_json(200, {"gateway": "ok", "routes": routes})
        self._proxy()

    do_POST = do_GET
    do_PUT = do_GET
    do_PATCH = do_GET
    do_DELETE = do_GET

    def _proxy(self):
        port = target_for(self.path)
        if port is None:
            return self._send_json(404, {"error": "no route for " + self.path})
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        url = f"http://127.0.0.1:{port}{self.path}"
        headers = {k: v for k, v in self.headers.items()
                   if k.lower() not in ("host", "content-length")}
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method=self.command)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                data = r.read()
                self.send_response(r.status)
                ctype = r.headers.get("Content-Type", "application/json")
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self._cors()
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            data = e.read()
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(data)))
            self._cors()
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self._send_json(502, {"error": f"upstream :{port} unreachable", "detail": str(e)})

    def log_message(self, fmt, *args):
        pass


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    srv = ThreadedHTTPServer(("0.0.0.0", 8090), Handler)
    print("gateway :8090 -> backend services")
    srv.serve_forever()
