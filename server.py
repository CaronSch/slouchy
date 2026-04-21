"""Minimal embedded HTTP server for the Slouchy dashboard and preferences API."""

import json
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer

from preferences import Preferences, save_preferences

PORT = 47832


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/":
            body = self.server.get_dashboard_html().encode("utf-8")
            self._respond(200, "text/html; charset=utf-8", body)
        elif self.path == "/api/preferences":
            with self.server.prefs_lock:
                data = asdict(self.server.prefs)
            body = json.dumps(data).encode("utf-8")
            self._respond(200, "application/json", body)
        else:
            self._respond(404, "text/plain", b"Not found")

    def do_POST(self):
        if self.path == "/api/preferences":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                incoming = json.loads(raw)
            except json.JSONDecodeError:
                self._respond(400, "text/plain", b"Bad JSON")
                return
            with self.server.prefs_lock:
                prefs = self.server.prefs
                if "sound_enabled" in incoming:
                    prefs.sound_enabled = bool(incoming["sound_enabled"])
                if "text_notifications_enabled" in incoming:
                    prefs.text_notifications_enabled = bool(incoming["text_notifications_enabled"])
                if "active_hours_enabled" in incoming:
                    prefs.active_hours_enabled = bool(incoming["active_hours_enabled"])
                if "active_hours_start" in incoming:
                    v = int(incoming["active_hours_start"])
                    if 0 <= v <= 1439:
                        prefs.active_hours_start = v
                if "active_hours_end" in incoming:
                    v = int(incoming["active_hours_end"])
                    if 0 <= v <= 1439:
                        prefs.active_hours_end = v
                save_preferences(prefs)
            self._respond(200, "application/json", b'{"ok": true}')
        else:
            self._respond(404, "text/plain", b"Not found")

    def _respond(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def start_server(
    prefs: Preferences,
    prefs_lock: threading.Lock,
    get_dashboard_html,
) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", PORT), DashboardHandler)
    server.prefs = prefs
    server.prefs_lock = prefs_lock
    server.get_dashboard_html = get_dashboard_html
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server
