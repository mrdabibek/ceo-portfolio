#!/usr/bin/env python3
"""
CEO Portfolio Hub — Local High-Performance Multi-threaded Server
Run with: python server.py
"""

import os
import sys
import socket
import webbrowser
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn

# Safe UTF-8 console output on Windows
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

class ThreadingSimpleServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class CustomRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "frame-ancestors 'self' *")
        super().end_headers()

    def guess_type(self, path):
        if path.endswith('.wasm'):
            return 'application/wasm'
        if path.endswith('.glb'):
            return 'model/gltf-binary'
        if path.endswith('.gltf'):
            return 'model/gltf+json'
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

def find_available_port(start=8080):
    for port in range(start, start + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(('localhost', port)) != 0:
                return port
    return start

def main():
    root_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(root_dir)

    port = find_available_port(8080)
    local_ip = get_local_ip()

    server = ThreadingSimpleServer(('0.0.0.0', port), CustomRequestHandler)
    local_url = f"http://localhost:{port}"
    network_url = f"http://{local_ip}:{port}"

    print("=" * 66)
    print("  [+] CEO PORTFOLIO HUB -- LOCAL SERVER ACTIVE")
    print("=" * 66)
    print(f"  * Local URL:    {local_url}")
    print(f"  * Network URL:  {network_url} (Test on Phone / iPad)")
    print(f"  * Root Path:    {root_dir}")
    print("  * Features:     Multi-threaded / CORS / 60fps Iframe Previews")
    print("=" * 66)
    print("  Press Ctrl + C to stop the server anytime.\n")

    try:
        webbrowser.open(local_url)
    except Exception:
        pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[+] Server stopped cleanly.")
        server.server_close()

if __name__ == '__main__':
    main()
