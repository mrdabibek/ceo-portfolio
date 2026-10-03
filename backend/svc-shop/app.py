import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "data", "shop.db")
PORT = int(os.environ.get("PORT", "8004"))


def db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, name TEXT NOT NULL, price REAL NOT NULL, stock INTEGER NOT NULL DEFAULT 0)")
    c.execute("CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, items TEXT NOT NULL, total REAL NOT NULL, created TEXT NOT NULL)")
    return c


def row_product(r):
    return {"id": r[0], "name": r[1], "price": r[2], "stock": r[3]}


def now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PATCH,OPTIONS")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            if not n:
                return {}
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def do_OPTIONS(self):
        self._send(204, {})

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            return self._send(200, {"ok": True})
        if path == "/api/products":
            c = db()
            rows = c.execute("SELECT id,name,price,stock FROM products ORDER BY id").fetchall()
            c.close()
            return self._send(200, [row_product(r) for r in rows])
        if path.startswith("/api/products/"):
            try:
                pid = int(path.split("/")[3])
            except (IndexError, ValueError):
                return self._send(404, {"error": "not found"})
            c = db()
            r = c.execute("SELECT id,name,price,stock FROM products WHERE id=?", (pid,)).fetchone()
            c.close()
            if not r:
                return self._send(404, {"error": "not found"})
            return self._send(200, row_product(r))
        if path == "/api/orders":
            c = db()
            rows = c.execute("SELECT id,items,total,created FROM orders ORDER BY id DESC").fetchall()
            c.close()
            return self._send(200, [{"id": r[0], "items": json.loads(r[1]), "total": r[2], "created": r[3]} for r in rows])
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/api/products":
            body = self._body()
            if body is None:
                return self._send(400, {"error": "bad json"})
            name = str(body.get("name", "")).strip()
            try:
                price = float(body.get("price"))
                stock = int(body.get("stock", 0))
            except (TypeError, ValueError):
                return self._send(400, {"error": "price/stock invalid"})
            if not name or price < 0 or stock < 0:
                return self._send(400, {"error": "name/price/stock invalid"})
            c = db()
            cur = c.execute("INSERT INTO products(name,price,stock) VALUES(?,?,?)", (name, price, stock))
            c.commit()
            pid = cur.lastrowid
            r = c.execute("SELECT id,name,price,stock FROM products WHERE id=?", (pid,)).fetchone()
            c.close()
            return self._send(201, row_product(r))
        if path == "/api/orders":
            body = self._body()
            if body is None:
                return self._send(400, {"error": "bad json"})
            items = body.get("items")
            if not isinstance(items, list) or not items:
                return self._send(400, {"error": "items required"})
            norm = []
            for it in items:
                if not isinstance(it, dict):
                    return self._send(400, {"error": "bad item"})
                pid = it.get("id", it.get("product_id", it.get("productId")))
                qty = it.get("qty", it.get("quantity", it.get("qnt", 1)))
                try:
                    pid = int(pid)
                    qty = int(qty)
                except (TypeError, ValueError):
                    return self._send(400, {"error": "bad item"})
                if qty <= 0:
                    return self._send(400, {"error": "bad qty"})
                norm.append({"id": pid, "qty": qty})
            c = db()
            total = 0.0
            for it in norm:
                r = c.execute("SELECT price,stock FROM products WHERE id=?", (it["id"],)).fetchone()
                if not r:
                    c.close()
                    return self._send(404, {"error": f"product {it['id']} not found"})
                if r[1] < it["qty"]:
                    c.close()
                    return self._send(409, {"error": f"insufficient stock for {it['id']}"})
                total += r[0] * it["qty"]
            for it in norm:
                c.execute("UPDATE products SET stock=stock-? WHERE id=?", (it["qty"], it["id"]))
            created = now()
            cur = c.execute("INSERT INTO orders(items,total,created) VALUES(?,?,?)", (json.dumps(norm), total, created))
            c.commit()
            oid = cur.lastrowid
            c.close()
            return self._send(201, {"id": oid, "items": norm, "total": total, "created": created})
        return self._send(404, {"error": "not found"})

    def do_PATCH(self):
        path = urlparse(self.path).path
        parts = path.split("/")
        if len(parts) == 5 and parts[1] == "api" and parts[2] == "products" and parts[4] == "stock":
            try:
                pid = int(parts[3])
            except ValueError:
                return self._send(404, {"error": "not found"})
            body = self._body()
            if body is None:
                return self._send(400, {"error": "bad json"})
            c = db()
            r = c.execute("SELECT id,name,price,stock FROM products WHERE id=?", (pid,)).fetchone()
            if not r:
                c.close()
                return self._send(404, {"error": "not found"})
            if "stock" in body:
                try:
                    stock = int(body["stock"])
                except (TypeError, ValueError):
                    c.close()
                    return self._send(400, {"error": "stock invalid"})
                if stock < 0:
                    c.close()
                    return self._send(400, {"error": "stock invalid"})
                c.execute("UPDATE products SET stock=? WHERE id=?", (stock, pid))
            elif "delta" in body:
                try:
                    delta = int(body["delta"])
                except (TypeError, ValueError):
                    c.close()
                    return self._send(400, {"error": "delta invalid"})
                if r[3] + delta < 0:
                    c.close()
                    return self._send(409, {"error": "insufficient stock"})
                c.execute("UPDATE products SET stock=stock+? WHERE id=?", (delta, pid))
            else:
                c.close()
                return self._send(400, {"error": "stock|delta required"})
            c.commit()
            r = c.execute("SELECT id,name,price,stock FROM products WHERE id=?", (pid,)).fetchone()
            c.close()
            return self._send(200, row_product(r))
        return self._send(404, {"error": "not found"})

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    db().close()
    print(f"svc-shop :{PORT}")
    HTTPServer(("0.0.0.0", PORT), H).serve_forever()
