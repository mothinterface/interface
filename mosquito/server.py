"""Small authenticated local controller, also reachable over USB Ethernet."""

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

STATIC = Path(__file__).with_name("static")


def make_server(controller, token, host="127.0.0.1", port=8765, serve_ui=True):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(2)

        def log_message(self, *_):
            pass

        def reply(self, status, data, kind="application/json"):
            body = json.dumps(data, allow_nan=False).encode() if kind == "application/json" else data
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def authenticated(self):
            actual = self.headers.get("Authorization", "")
            return hmac.compare_digest(actual.encode(), f"Bearer {token}".encode())

        def do_GET(self):
            files = {"/": ("index.html", "text/html; charset=utf-8"),
                     "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if self.path in files:
                if not serve_ui:
                    return self.reply(404, {"error": "No browser interface; use the command line or API"})
                name, kind = files[self.path]
                return self.reply(200, (STATIC / name).read_bytes(), kind)
            if not self.authenticated():
                return self.reply(401, {"error": "Enter the controller token"})
            if self.path == "/api/state":
                return self.reply(200, controller.state())
            self.reply(404, {"error": "Not found"})

        def do_POST(self):
            if not self.authenticated():
                return self.reply(401, {"error": "Invalid token"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    raise ValueError("Request must be 1–8192 bytes")
                if self.headers.get_content_type() != "application/json":
                    raise ValueError("Expected application/json")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Expected an object")
                if self.path == "/api/sample":
                    controller.ingest(data)
                elif self.path == "/api/arm":
                    controller.arm()
                elif self.path == "/api/stop":
                    controller.stop()
                elif self.path == "/api/mode":
                    controller.set_mode(data.get("mode"))
                else:
                    return self.reply(404, {"error": "Not found"})
                self.reply(200, {"ok": True})
            except (ValueError, TypeError, KeyError, OverflowError) as error:
                self.reply(400, {"error": str(error)})

    return ThreadingHTTPServer((host, port), Handler)
