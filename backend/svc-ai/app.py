import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8008

PROMPTS = [
    "Narxlar qanday? / What are your prices?",
    "Qanday texnologiyalar ishlatasiz? / What stack do you use?",
    "Muddat qancha? / What is the timeline?",
    "Salom! / Hi!",
]

def brain(q: str) -> str:
    t = (q or "").lower()
    if any(k in t for k in ("narx", "price", "cost", "narxi", "pul")):
        return "Narxlar loyiha hajmiga bog'liq — landing $100 dan, full-stack $500 dan. Prices depend on scope — landing from $100, full-stack from $500."
    if any(k in t for k in ("stack", "texnolog", "tech", "dastur", "til", "language", "framework")):
        return "Stack: Python, JavaScript, Flutter, HTML/CSS. Stack: Python, JavaScript, Flutter, HTML/CSS."
    if any(k in t for k in ("muddat", "vaqt", "timeline", "deadline", "qancha vaqt", "how long", "duration")):
        return "Muddat: landing 3-5 kun, full-stack 2-4 hafta. Timeline: landing 3-5 days, full-stack 2-4 weeks."
    if any(k in t for k in ("salom", "assalom", "hello", "hi", "privet")):
        return "Salom! Savolingizni yozing — narx, stack yoki muddat haqida so'rang. Hi! Ask me about price, stack, or timeline."
    return "Tushundim. Narx, stack yoki muddat haqida so'rang. Got it. Ask about price, stack, or timeline."

class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/api/ai/prompts":
            self._send(200, {"prompts": PROMPTS})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/ai":
            return self._send(404, {"error": "not found"})
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self._send(400, {"error": "invalid json"})
        self._send(200, {"a": brain(data.get("q", ""))})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
