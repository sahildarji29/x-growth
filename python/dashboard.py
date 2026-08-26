"""
XActions Growth Bot — live activity dashboard server.

Serves an interactive web UI that shows what the bot is doing in real time:
a live event feed, progress toward daily targets, and an actions-over-time
chart. Events come from data/activity.jsonl (written by growth_bot.py via
xeepy/activity_feed.py) and stream to the browser over Server-Sent Events.

Zero dependencies — Python standard library only. Run it next to the bot:

    python dashboard.py                 # http://127.0.0.1:8787
    python dashboard.py --port 9000
    python dashboard.py --host 0.0.0.0  # expose on the network (trusted LANs only)

The server binds to localhost by default on purpose: the feed contains your
account's activity, so only expose it beyond this machine deliberately.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).parent

# Load activity_feed by file path — importing the xeepy package would pull in
# its heavy dependency chain (loguru, playwright), which this server doesn't need.
_spec = importlib.util.spec_from_file_location(
    "activity_feed", ROOT / "xeepy" / "activity_feed.py"
)
_activity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_activity)
feed_path, read_recent = _activity.feed_path, _activity.read_recent

HTML_FILE = ROOT / "dashboard.html"
SSE_POLL_S = 0.5          # how often the tail loop checks for new lines
SSE_HEARTBEAT_S = 15      # keep-alive comment interval


class DashboardHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # ── routing ──────────────────────────────────────────────────────────
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        route = urlparse(self.path).path
        try:
            if route in ("/", "/index.html"):
                self._serve_page()
            elif route == "/api/history":
                self._serve_history()
            elif route == "/events":
                self._serve_events()
            else:
                self._send(404, "text/plain; charset=utf-8", b"not found")
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser tab closed mid-response — normal for SSE

    # ── responses ────────────────────────────────────────────────────────
    def _send(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_page(self) -> None:
        if not HTML_FILE.exists():
            self._send(500, "text/plain; charset=utf-8",
                       b"dashboard.html not found next to dashboard.py")
            return
        self._send(200, "text/html; charset=utf-8", HTML_FILE.read_bytes())

    def _serve_history(self) -> None:
        qs = parse_qs(urlparse(self.path).query)
        try:
            limit = min(int(qs.get("n", ["500"])[0]), 2000)
        except ValueError:
            limit = 500
        body = json.dumps(read_recent(limit)).encode("utf-8")
        self._send(200, "application/json; charset=utf-8", body)

    def _serve_events(self) -> None:
        """SSE stream: tail the feed file from its current end."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        # SSE is an open-ended stream — no Content-Length; close when done.
        self.send_header("Connection", "close")
        self.end_headers()

        path = feed_path()
        pos = path.stat().st_size if path.exists() else 0
        last_beat = time.time()

        while True:
            sent = False
            if path.exists():
                size = path.stat().st_size
                if size < pos:
                    pos = 0  # feed file was trimmed/rotated — restart tail
                if size > pos:
                    with path.open("r", encoding="utf-8", errors="replace") as fh:
                        fh.seek(pos)
                        chunk = fh.read()
                        pos = fh.tell()
                    for line in chunk.splitlines():
                        line = line.strip()
                        if line:
                            self.wfile.write(f"data: {line}\n\n".encode("utf-8"))
                            sent = True
            if sent:
                self.wfile.flush()
                last_beat = time.time()
            elif time.time() - last_beat > SSE_HEARTBEAT_S:
                self.wfile.write(b": ping\n\n")
                self.wfile.flush()
                last_beat = time.time()
            time.sleep(SSE_POLL_S)

    def log_message(self, fmt: str, *args) -> None:
        pass  # keep the terminal quiet; the UI is the output


def main() -> None:
    ap = argparse.ArgumentParser(description="Growth bot live dashboard")
    ap.add_argument("--host", default=os.environ.get("DASHBOARD_HOST", "127.0.0.1"),
                    help="Bind address (default 127.0.0.1 — localhost only)")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("DASHBOARD_PORT", "8787")))
    args = ap.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"📊 Growth bot dashboard → http://{args.host}:{args.port}")
    print(f"   feed: {feed_path()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped.")


if __name__ == "__main__":
    main()
