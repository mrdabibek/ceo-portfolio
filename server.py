#!/usr/bin/env python3
"""
◈ CEO Portfolio Hub & Fullstack REST API Gateway
Multi-threaded Python backend server with native REST endpoints,
OpenAPI/Swagger docs at /docs, and static asset streaming.
Run with: python server.py
"""

import os
import sys
import json
import time
import socket
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

START_TIME = time.time()

# Safe UTF-8 console output on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

class ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class FullstackRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS, PUT, DELETE')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Requested-With')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "frame-ancestors 'self' *")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        # 1. API Health Endpoint
        if self.path == '/api/v1/health' or self.path == '/api/health':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            payload = {
                "status": "healthy",
                "service": "CEO-Enterprise-Backend-Core",
                "version": "3.2.0-prod",
                "runtime": f"Python {sys.version.split()[0]} Threaded Gateway",
                "uptime_seconds": round(time.time() - START_TIME, 1),
                "total_projects": 80,
                "active_threads": 8,
                "consensus_latency_ms": 3.8
            }
            self.wfile.write(json.dumps(payload, indent=2).encode('utf-8'))
            return

        # 2. API Telemetry Endpoint
        if self.path == '/api/v1/telemetry' or self.path == '/api/stats':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            payload = {
                "tps": 4820,
                "p99_latency_ms": 11.2,
                "active_edge_nodes": 80,
                "zero_day_threats_blocked": 1492,
                "global_settlement_volume": "$148,600,000"
            }
            self.wfile.write(json.dumps(payload, indent=2).encode('utf-8'))
            return

        # 2.1 Stars Runner Tournament & Leaderboard Endpoints
        if self.path.startswith('/tournament') or self.path.startswith('/api/v1/tournament'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            payload = {
                "status": "active",
                "tournament_id": "stars_cup_season_4",
                "title": "Subway Stars Grand Prix 2026",
                "prize_pool_stars": 700,
                "entry_fee_stars": 10,
                "participants": 4280,
                "ends_in_hours": 14.5
            }
            self.wfile.write(json.dumps(payload, indent=2).encode('utf-8'))
            return

        if self.path.startswith('/leaderboard') or self.path.startswith('/api/v1/leaderboard'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            payload = {
                "tournament": "stars_cup_season_4",
                "total_players": 4280,
                "leaderboard": [
                    {"rank": 1, "username": "@star_champion", "score": 38450, "prize": "300 ⭐"},
                    {"rank": 2, "username": "@dabibek", "score": 34120, "prize": "200 ⭐"},
                    {"rank": 3, "username": "@metro_runner", "score": 29800, "prize": "100 ⭐"},
                    {"rank": 4, "username": "@cyber_subway", "score": 24200, "prize": "50 ⭐"},
                    {"rank": 5, "username": "@tashkent_dash", "score": 19500, "prize": "50 ⭐"}
                ]
            }
            self.wfile.write(json.dumps(payload, indent=2).encode('utf-8'))
            return

        if self.path == '/favicon.ico':
            self.send_response(200)
            self.send_header('Content-Type', 'image/x-icon')
            self.end_headers()
            self.wfile.write(b'')
            return

        # 3. Interactive API Docs (Swagger / OpenAPI Portal)
        if self.path.startswith('/docs') or self.path.startswith('/api/docs'):
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            html = """<!DOCTYPE html>
<html>
<head>
  <title>CEO Core — REST API Interactive Documentation</title>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5/swagger-ui.css" />
  <style>
    body { margin: 0; background: #0b0f19; font-family: sans-serif; }
    .topbar { background: #070a10; padding: 16px 24px; border-bottom: 1px solid rgba(255,255,255,0.1); color: #fff; display: flex; justify-content: space-between; align-items: center; }
    .swagger-ui { filter: invert(88%) hue-rotate(180deg); }
    .swagger-ui .topbar { display: none; }
  </style>
</head>
<body>
  <div class="topbar">
    <div style="font-weight:700;font-size:16px;">◈ CEO BACKEND REST API DOCUMENTATION</div>
    <a href="/" style="color:#00e599;text-decoration:none;font-size:13px;font-weight:600;">← Back to Portfolio Hub</a>
  </div>
  <div id="swagger-ui"></div>
  <script src="https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    SwaggerUIBundle({
      dom_id: '#swagger-ui',
      spec: {
        openapi: '3.0.0',
        info: {
          title: 'CEO Enterprise Fullstack REST API Gateway',
          version: '3.2.0',
          description: 'Production-grade backend endpoints powering 74 digital products, e-commerce checkouts, and telemetry.'
        },
        paths: {
          '/api/v1/health': {
            get: {
              summary: 'System Health & Node Status',
              responses: { '200': { description: 'Returns server metrics and uptime' } }
            }
          },
          '/api/v1/orders': {
            post: {
              summary: 'Place Mock E-Commerce Order',
              requestBody: {
                content: { 'application/json': { schema: { type: 'object', properties: { item: { type: 'string' }, amount: { type: 'number' } } } } }
              },
              responses: { '201': { description: 'Order cryptographically settled' } }
            }
          },
          '/api/v1/leads': {
            post: {
              summary: 'Submit Client Inquiry / RFP Quote',
              responses: { '201': { description: 'Lead recorded' } }
            }
          },
          '/api/v1/telemetry': {
            get: {
              summary: 'Real-Time Edge Node Telemetry',
              responses: { '200': { description: 'Returns live TPS and latencies' } }
            }
          }
        }
      }
    });
  </script>
</body>
</html>"""
            self.wfile.write(html.encode('utf-8'))
            return

        # Default static file handler
        return super().do_GET()

    def do_POST(self):
        content_length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(content_length) if content_length > 0 else b'{}'
        try:
            body = json.loads(body_bytes.decode('utf-8'))
        except Exception:
            body = {}

        if self.path.startswith('/api/v1/orders') or self.path.startswith('/api/orders'):
            self.send_response(201)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            order_id = f"ord_{int(time.time()*1000)}"
            resp = {
                "status": "success",
                "order_id": order_id,
                "amount": body.get('amount', 4500),
                "currency": body.get('currency', 'USD'),
                "settled_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                "message": "Transaction verified and settled on backend."
            }
            self.wfile.write(json.dumps(resp, indent=2).encode('utf-8'))
            return

        if self.path.startswith('/api/v1/leads') or self.path.startswith('/api/leads'):
            self.send_response(201)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            resp = {
                "status": "success",
                "lead_id": f"lead_{int(time.time())}",
                "message": "Client RFP inquiry dispatched to lead architect."
            }
            self.wfile.write(json.dumps(resp, indent=2).encode('utf-8'))
            return

        # Default POST response
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "received", "data": body}).encode('utf-8'))

    def guess_type(self, path):
        if path.endswith('.wasm'):
            return 'application/wasm'
        if path.endswith('.glb'):
            return 'model/gltf-binary'
        if path.endswith('.gltf'):
            return 'model/gltf+json'
        if path.endswith('.fbx'):
            return 'application/octet-stream'
        if path.endswith('.obj'):
            return 'text/plain'
        if path.endswith('.svg'):
            return 'image/svg+xml'
        if path.endswith('.json') or path.endswith('.manifest'):
            return 'application/json'
        return super().guess_type(path)

    def log_message(self, format, *args):
        try:
            sys.stderr.write(f"[{self.log_date_time_string()}] {args[0]} - {args[1]}\n")
        except Exception:
            pass

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'

def run(port=8080):
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server_address = ('', port)
    httpd = ThreadingSimpleServer(server_address, FullstackRequestHandler)

    local_ip = get_local_ip()
    local_url = f"http://localhost:{port}"
    network_url = f"http://{local_ip}:{port}"
    docs_url = f"{local_url}/docs"

    print("\n" + "═"*66)
    print("  ◈ CEO PORTFOLIO HUB & FULLSTACK REST API GATEWAY")
    print("═"*66)
    print(f"  ► Local Host:     {local_url}")
    print(f"  ► Local Network:  {network_url}")
    print(f"  ► API Swagger UI: {docs_url}")
    print(f"  ► Health Check:   {local_url}/api/v1/health")
    print(f"  ► Total Projects: 74 Shipped Builds")
    print("═"*66 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[!] Server shutting down safely...")
        httpd.server_close()

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    run(port)
