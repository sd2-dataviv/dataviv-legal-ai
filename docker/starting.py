"""Answers on the app's port while the container waits for its database, migrates and seeds.

Hosting platforms (and load balancers) probe a new container straight away, sometimes before, or without,
the database next to it. Staying silent reads as "never became reachable", so this stdlib-only server
answers instead: GET /health -> 200 {"status": "starting"}, any other page -> a short page that refreshes
itself, anything else -> 503 with Retry-After. The entrypoint stops it and starts the real app on the same
port once the database is ready.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = b"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="refresh" content="5">
<title>Dataviv AI OS - starting</title>
<style>body{font:16px/1.5 system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;margin:0;
background:#f6f5f2;color:#222}main{text-align:center;padding:16px}p{color:#666}</style></head>
<body><main><h1>Dataviv AI OS is starting</h1>
<p>Connecting to the database and preparing the workspace. This page refreshes itself.</p></main></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "dataviv-starting"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if code == 503:
            self.send_header("Retry-After", "5")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path.rstrip("/") in ("/health", "/healthz", "/ready"):
            self._send(
                200, json.dumps({"status": "starting"}).encode(), "application/json"
            )
        elif path.startswith(("/v1/", "/api/")):
            self._send(
                503,
                json.dumps({"detail": "starting, try again shortly"}).encode(),
                "application/json",
            )
        else:
            self._send(200, PAGE, "text/html; charset=utf-8")

    do_HEAD = do_GET

    def _busy(self) -> None:
        self._send(
            503,
            json.dumps({"detail": "starting, try again shortly"}).encode(),
            "application/json",
        )

    do_POST = do_PUT = do_PATCH = do_DELETE = _busy

    def log_message(
        self, fmt: str, *args: object
    ) -> None:  # keep container logs for the real app
        pass


def main() -> None:
    port = int(os.environ.get("DATAVIV_PORT", "8010"))
    ThreadingHTTPServer.allow_reuse_address = True
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    srv.daemon_threads = True

    def stop(*_: object) -> None:
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    srv.serve_forever(poll_interval=0.2)
    srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
