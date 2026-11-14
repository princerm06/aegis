"""The responder view as an MJPEG stream, for the dashboard's camera tile.

    GET  /stream.mjpg   multipart JPEG stream (an <img src=...> shows it)
    GET  /frame.jpg     the latest frame
    GET  /              a bare page with the stream, for checking
    POST /command       same as pressing c: a spoken command was just given

Binds to 127.0.0.1 by default: the frames show the people in the room, so do
not expose them beyond the demo machine (``--stream-host 0.0.0.0`` only on a
private network). Frames are encoded only while someone is watching.
"""

from __future__ import annotations

import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

log = logging.getLogger("cv.stream")
BOUNDARY = "aegisframe"
PAGE = (b"<!doctype html><title>AEGIS CV</title><body style='margin:0;background:#111'>"
        b"<img src='/stream.mjpg' style='width:100%'></body>")


class FrameStream:
    def __init__(self, host: str = "127.0.0.1", port: int = 8090, fps: float = 12.0, quality: int = 80):
        self.host, self.port = host, port
        self.period = 1.0 / max(1.0, fps)
        self.quality = quality
        self._jpeg: bytes | None = None
        self._frame_id = 0
        self._ready = threading.Condition()
        self._clients = 0
        self._last_encode = 0.0
        self._commands = 0
        self._server: ThreadingHTTPServer | None = None
        self._lock = threading.Lock()

    @property
    def watched(self) -> bool:
        return self._clients > 0

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/stream.mjpg"

    def start(self) -> None:
        stream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def _headers(self, status: int, content_type: str, length: int | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                if length is not None:
                    self.send_header("Content-Length", str(length))
                self.end_headers()

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/stream.mjpg":
                    stream._serve(self)
                elif path == "/frame.jpg":
                    stream._watch(1)               # one encode for a single-frame request
                    jpeg = stream._wait(stream._frame_id, timeout=2.0)
                    stream._watch(-1)
                    if jpeg is None:
                        self._headers(503, "text/plain", 0)
                    else:
                        self._headers(200, "image/jpeg", len(jpeg))
                        self.wfile.write(jpeg)
                elif path == "/":
                    self._headers(200, "text/html", len(PAGE))
                    self.wfile.write(PAGE)
                else:
                    self._headers(404, "text/plain", 0)

            def do_POST(self):
                if self.path.split("?", 1)[0] != "/command":
                    self._headers(404, "text/plain", 0)
                    return
                with stream._lock:
                    stream._commands += 1
                self._headers(204, "text/plain")

            def do_OPTIONS(self):                  # browser preflight for a fetch with headers
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]          # port 0: the one the system picked
        threading.Thread(target=self._server.serve_forever, name="cv-stream", daemon=True).start()
        log.info("Responder view stream: %s (POST /command = key c)", self.url)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        with self._ready:
            self._ready.notify_all()

    def take_commands(self) -> int:
        """Commands posted since the last call (the main loop marks them like key c)."""
        with self._lock:
            count, self._commands = self._commands, 0
        return count

    def _watch(self, change: int) -> None:
        with self._lock:
            self._clients += change

    def publish(self, canvas, now: float | None = None) -> None:
        """Offer a rendered frame; encoded at most ``fps`` times a second, only while watched."""
        now = time.monotonic() if now is None else now
        if not self.watched or now - self._last_encode < self.period:
            return
        ok, buffer = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        if not ok:
            return
        self._last_encode = now
        with self._ready:
            self._jpeg, self._frame_id = buffer.tobytes(), self._frame_id + 1
            self._ready.notify_all()

    def _wait(self, after_id: int, timeout: float) -> bytes | None:
        with self._ready:
            self._ready.wait_for(lambda: self._frame_id > after_id or self._server is None, timeout)
            return self._jpeg if self._frame_id > after_id else None

    def _serve(self, handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.end_headers()
        self._watch(1)
        seen = self._frame_id - 1 if self._jpeg is not None else 0
        try:
            while self._server is not None:
                jpeg = self._wait(seen, timeout=5.0)
                if jpeg is None:
                    continue
                seen = self._frame_id
                handler.wfile.write(f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                                    f"Content-Length: {len(jpeg)}\r\n\r\n".encode() + jpeg + b"\r\n")
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            self._watch(-1)
