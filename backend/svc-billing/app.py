#!/usr/bin/env python3
"""svc-billing — stdlib invoice API on :8006."""
import json
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from pathlib import Path
from urllib.parse import urlparse

BASE = Path(__file__).resolve().parent
DB = BASE / "data" / "billing.db"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Tariflar (USD): sprint MVP, product full-stack, partner oylik retainer.
PLANS = {"sprint": 1500, "product": 9800, "partner": 3000}

# STRIPE-READY (izohda):
# - Real rejim: STRIPE_SECRET_KEY env dan olinadi.
# - POST /api/checkout ichida stripe.checkout.Session.create(
#     payment_method_types=["card"], line_items=[{"price_data":{
#     "currency":"usd","unit_amount":amount*100,
#     "product_data":{"name":plan}}}], mode="payment",
#     success_url=..., cancel_url=...) chaqiriladi,
#     pay_url = session.url bo'ladi. Hozir mock qaytadi.
# - POST /api/stripe/webhook (stripe-signature tekshirib)
#   checkout.session.completed da status='paid' qiladi.


def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS invoices(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      plan TEXT NOT NULL, amount INTEGER NOT NULL,
      email TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
      created TEXT NOT NULL)""")
    return c


def pay_url(iid: int) -> str:
    return f"https://pay.mock/inv_{iid}"


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
        p = urlparse(self.path).path
        if p in ("/api/health", "/health"):
            return self._send(200, {"ok": True, "svc": "billing"})
        if p.startswith("/api/invoices/"):
            try:
                iid = int(p.rsplit("/", 1)[1])
            except ValueError:
                return self._send(404, {"error": "not found"})
            con = db()
            r = con.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
            con.close()
            if not r:
                return self._send(404, {"error": "not found"})
            return self._send(200, {"invoice": dict(r)})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if urlparse(self.path).path != "/api/checkout":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "invalid json"})
        plan = str(body.get("plan", "")).strip().lower()
        email = str(body.get("email", "")).strip()
        if plan not in PLANS:
            return self._send(422, {"error": "plan must be sprint|product|partner"})
        if not EMAIL_RE.match(email):
            return self._send(422, {"error": "valid email required"})
        con = db()
        cur = con.execute(
            "INSERT INTO invoices(plan,amount,email,status,created) VALUES(?,?,?,?,?)",
            (plan, PLANS[plan], email[:320], "pending",
             time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
        con.commit()
        iid = cur.lastrowid
        r = con.execute("SELECT * FROM invoices WHERE id=?", (iid,)).fetchone()
        con.close()
        inv = dict(r)
        return self._send(201, {"invoice": inv, "pay_url": pay_url(iid)})

    def log_message(self, *a):
        pass


class S(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8006
    print(f"svc-billing on :{port}", flush=True)
    S(("", port), H).serve_forever()
