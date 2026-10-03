import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "auth.db")
SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-me").encode()
PORT = int(os.environ.get("PORT", "8001"))
EXP = 7 * 24 * 3600


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, email TEXT UNIQUE, pass_hash TEXT, salt TEXT, created TEXT)")
    return c


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign(msg: str) -> str:
    return b64e(hmac.new(SECRET, msg.encode(), hashlib.sha256).digest())


def token(uid: int, email: str) -> str:
    h = b64e(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    p = b64e(json.dumps({"sub": uid, "email": email, "exp": int(time.time()) + EXP}).encode())
    return f"{h}.{p}.{sign(h + '.' + p)}"


def verify(t: str):
    try:
        h, p, s = t.split(".")
        if not hmac.compare_digest(sign(h + "." + p), s):
            return None
        d = json.loads(b64d(p))
        if d.get("exp", 0) < time.time():
            return None
        return d
    except Exception:
        return None


def pw(passwd: str, salt: str) -> str:
    return hashlib.sha256((salt + passwd).encode()).hexdigest()


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/api/auth/me":
            a = self.headers.get("Authorization", "")
            if not a.startswith("Bearer "):
                return self._send(401, {"error": "missing token"})
            d = verify(a[7:])
            if not d:
                return self._send(401, {"error": "invalid token"})
            c = db()
            r = c.execute("SELECT id,email,created FROM users WHERE id=?", (d["sub"],)).fetchone()
            c.close()
            if not r:
                return self._send(401, {"error": "invalid token"})
            return self._send(200, {"id": r[0], "email": r[1], "created": r[2]})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/auth/register", "/api/auth/login"):
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return self._send(400, {"error": "bad json"})
        email = str(body.get("email", "")).strip().lower()
        passwd = str(body.get("pass", ""))
        if "@" not in email or len(passwd) < 4:
            return self._send(400, {"error": "email/pass invalid"})
        c = db()
        if path == "/api/auth/register":
            salt = secrets.token_hex(8)
            try:
                cur = c.execute(
                    "INSERT INTO users(email,pass_hash,salt,created) VALUES(?,?,?,?)",
                    (email, pw(passwd, salt), salt, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())),
                )
                c.commit()
                uid = cur.lastrowid
            except sqlite3.IntegrityError:
                c.close()
                return self._send(409, {"error": "email exists"})
            r = c.execute("SELECT id,email FROM users WHERE id=?", (uid,)).fetchone()
            c.close()
            return self._send(201, {"token": token(r[0], r[1]), "user": {"id": r[0], "email": r[1]}})
        r = c.execute("SELECT id,email,pass_hash,salt FROM users WHERE email=?", (email,)).fetchone()
        c.close()
        if not r or not hmac.compare_digest(pw(passwd, r[3]), r[2]):
            return self._send(401, {"error": "bad credentials"})
        return self._send(200, {"token": token(r[0], r[1]), "user": {"id": r[0], "email": r[1]}})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    db().close()
    print(f"svc-auth :{PORT}")
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
