#!/usr/bin/env python3
"""svc-estimate - stdlib estimate API on :8009."""
import json
import math
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

PORT = 8009


def calc(scope, design, integrations):
    price = 1500 + scope * 700 + design * 500 + integrations * 450
    weeks = math.ceil(2 + scope * 0.5 + design * 0.4 + integrations * 0.7)
    return price, weeks


def validate(scope, design, integrations):
    try:
        s = int(scope)
    except (TypeError, ValueError):
        return None, "scope must be int 1-10"
    try:
        d = int(design)
    except (TypeError, ValueError):
        return None, "design must be int 1-10"
    try:
        i = int(integrations)
    except (TypeError, ValueError):
        return None, "integrations must be int 0-8"
    if not 1 <= s <= 10:
        return None, "scope must be 1-10"
    if not 1 <= d <= 10:
        return None, "design must be 1-10"
    if not 0 <= i <= 8:
        return None, "integrations must be 0-8"
    return (s, d, i), ""


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
            return self._send(200, {"ok": True, "svc": "estimate"})
        if u.path == "/api/estimate":
            q = parse_qs(u.query)
            vals, err = validate(
                q.get("scope", [None])[0],
                q.get("design", [None])[0],
                q.get("integrations", [None])[0],
            )
            if err:
                return self._send(422, {"error": err})
            price, weeks = calc(*vals)
            return self._send(200, {
                "price": price, "weeks": weeks,
                "scope": vals[0], "design": vals[1], "integrations": vals[2],
            })
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/estimate":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid json"})
        vals, err = validate(body.get("scope"), body.get("design"), body.get("integrations"))
        if err:
            return self._send(422, {"error": err})
        price, weeks = calc(*vals)
        return self._send(200, {
            "price": price, "weeks": weeks,
            "scope": vals[0], "design": vals[1], "integrations": vals[2],
        })

    def log_message(self, *a):
        pass


class S(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    import os
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", PORT))
    print(f"svc-estimate on :{port}", flush=True)
    S(("", port), H).serve_forever()
