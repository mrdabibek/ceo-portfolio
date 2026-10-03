"""svc-files: stdlib-only upload microservice (port 8007)."""
import json
import mimetypes
import os
import re
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import unquote

BASE = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

PORT = int(os.environ.get("PORT", "8007"))
MAX_SIZE = 5 * 1024 * 1024
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def send_json(h, code, obj):
    body = json.dumps(obj).encode()
    h.send_response(code)
    h.send_header("Content-Type", "application/json")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Access-Control-Allow-Origin", "*")
    h.end_headers()
    h.wfile.write(body)


def safe_path(name):
    name = unquote(name)
    if "/" in name or "\\" in name or not SAFE_NAME.match(name):
        return None
    p = os.path.normpath(os.path.join(UPLOAD_DIR, name))
    if not p.startswith(os.path.normpath(UPLOAD_DIR) + os.sep):
        return None
    return p


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        path = unquote(self.path.split("?", 1)[0])
        if path == "/" or path == "/api/health":
            return send_json(self, 200, {"service": "svc-files", "status": "ok"})
        if path == "/api/files":
            out = []
            for n in sorted(os.listdir(UPLOAD_DIR)):
                p = os.path.join(UPLOAD_DIR, n)
                if os.path.isfile(p):
                    out.append({"name": n, "url": f"/files/{n}", "size": os.path.getsize(p)})
            return send_json(self, 200, {"files": out})
        if path.startswith("/files/"):
            name = path[len("/files/"):]
            p = safe_path(name)
            if not p or not os.path.isfile(p):
                return send_json(self, 404, {"error": "not found"})
            ctype = mimetypes.guess_type(p)[0] or "application/octet-stream"
            try:
                with open(p, "rb") as f:
                    data = f.read()
            except OSError:
                return send_json(self, 404, {"error": "not found"})
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.cors()
            self.end_headers()
            self.wfile.write(data)
            return
        return send_json(self, 404, {"error": "not found"})

    def do_POST(self):
        path = unquote(self.path.split("?", 1)[0])
        if path != "/api/upload":
            return send_json(self, 404, {"error": "not found"})
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return send_json(self, 400, {"error": "expected multipart/form-data"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length > MAX_SIZE + 1024 * 1024 or length <= 0:
            return send_json(self, 413, {"error": "file too large (max 5MB)"})
        body = self.rfile.read(length)
        # manual multipart parse (cgi removed in Py3.13+)
        bnd = None
        for p in ctype.split(";"):
            p = p.strip()
            if p.startswith("boundary="):
                bnd = p[len("boundary="):].strip().strip('"')
        if not bnd:
            return send_json(self, 400, {"error": "bad multipart body"})
        sep = ("--" + bnd).encode()
        parts = body.split(sep)
        filename, data = None, None
        for part in parts:
            if b'filename="' not in part:
                continue
            try:
                fn = part.split(b'filename="', 1)[1].split(b'"', 1)[0].decode("utf-8", "ignore")
            except Exception:
                continue
            idx = part.find(b"\r\n\r\n")
            if idx < 0:
                continue
            content = part[idx + 4:]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            filename, data = fn, content
            break
        if filename is None or data is None:
            return send_json(self, 400, {"error": "no file field"})
        if len(data) > MAX_SIZE:
            return send_json(self, 413, {"error": "file too large (max 5MB)"})
        if not data:
            return send_json(self, 400, {"error": "empty file"})
        orig = os.path.basename(filename or "")
        ext = os.path.splitext(orig)[1].lower()
        if ext == ".jpe":
            ext = ".jpg"
        if ext not in ALLOWED_EXT:
            return send_json(self, 415, {"error": "only png/jpg/webp allowed"})
        name = f"{uuid.uuid4().hex}{ext}"
        dest = safe_path(name)
        with open(dest, "wb") as f:
            f.write(data)
        return send_json(self, 201, {"url": f"/files/{name}", "name": name, "size": len(data)})


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
