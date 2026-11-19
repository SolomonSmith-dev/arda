"""Stand-in for the Cloudflare edge in front of the demo.

Forwards to the app on 127.0.0.1:5001 with a Cf-Connecting-Ip header, and,
while the rule flag file exists, applies the Free-plan rate-limit rule from
docs/deploy-demo.md step 18: more than 5 POSTs to /demo/ask from one address
in 10 s gets a 10 s block, answered as a 429 HTML page (Cloudflare's 1015).

Usage: python edge.py PORT LOG_FILE RULE_FLAG_FILE
"""

from __future__ import annotations

import http.server
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque

UPSTREAM = "http://127.0.0.1:5001"
VISITOR_IP = "203.0.113.7"
PORT, LOG_PATH, RULE_FLAG = int(sys.argv[1]), sys.argv[2], sys.argv[3]

_hits: deque[float] = deque()
_blocked_until = 0.0
_lock = threading.Lock()
_log = open(LOG_PATH, "a", buffering=1)  # noqa: SIM115 - lives as long as the process


def _edge_blocks(path: str) -> bool:
    global _blocked_until
    if path != "/demo/ask" or not os.path.exists(RULE_FLAG):
        return False
    now = time.monotonic()
    with _lock:
        if now < _blocked_until:
            return True
        _hits.append(now)
        while _hits and now - _hits[0] > 10:
            _hits.popleft()
        if len(_hits) > 5:
            _blocked_until = now + 10
            _hits.clear()
            return True
    return False


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self, method: str) -> None:
        length = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(length) if length else None
        path = self.path.split("?")[0]
        if _edge_blocks(path):
            data = b"<html><body>error code: 1015 You are being rate limited</body></html>"
            self._reply(429, "text/html; charset=UTF-8", data)
            _log.write(f"EDGE-429 {method} {path}\n")
            return
        req = urllib.request.Request(UPSTREAM + self.path, data=body, method=method)
        for key, value in self.headers.items():
            if key.lower() not in ("host", "content-length", "connection"):
                req.add_header(key, value)
        req.add_header("Cf-Connecting-Ip", VISITOR_IP)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                code, ctype, data = resp.status, resp.headers.get("content-type"), resp.read()
        except urllib.error.HTTPError as err:
            code, ctype, data = err.code, err.headers.get("content-type"), err.read()
        except OSError:
            code, ctype, data = 502, "text/html", b"<html>error code: 502</html>"
        self._reply(code, ctype or "text/plain", data)
        _log.write(f"{code} {method} {path}\n")

    def _reply(self, code: int, ctype: str, data: bytes) -> None:
        self.send_response(code)
        self.send_header("content-type", ctype)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._proxy("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        self._proxy("POST")

    def log_message(self, *args: object) -> None:
        pass


class Server(http.server.ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


Server(("127.0.0.1", PORT), Handler).serve_forever()
