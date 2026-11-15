"""One command for the live demo: the mounted C922 and, when the iPhone is connected, the body cam.

    python -m cv.demo                  # both cameras, everyone reported in room-2
    python -m cv.demo --room room-1
    python -m cv.demo --no-bodycam     # fixed camera only
    python -m cv.demo --bodycam-view-only   # body cam as a live view only, nothing sent from it
    python -m cv.demo --backend-url http://192.168.137.1:8000 --stream-host 0.0.0.0
                                       # shared backend; tiles viewable from other laptops
    python -m cv.demo --no-display     # no windows on the laptop, dashboard tiles only

Each camera runs as its own ``cv.main`` process (``cv/config/demo.yaml`` and
``cv/config/bodycam.yaml``) with signs sent to the backend and its view served
for a dashboard tile. Paste each tile's link once; the browser remembers it.
A camera that is not connected is skipped. Ctrl+C stops both.

The fixed camera is the one the operator links to casualties. With
``--bodycam-view-only`` the body cam still draws its cards and serves its tile
but sends no detections, so the same people do not appear twice on the map.
"""

from __future__ import annotations

import argparse
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlparse

from cv.camera import describe_cameras, find_camera, list_cameras
from cv.settings import CONFIG_DIR, CV_DIR, load_settings


@dataclass(frozen=True)
class Feed:
    name: str       # what the operator calls it
    config: str     # file in cv/config
    port: int
    tile: str       # dashboard tile that shows it

    def url(self, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}/stream.mjpg"


FIXED = Feed("Fixed camera", "demo.yaml", 8090, "BUILDING CAM")
BODYCAM = Feed("Body cam", "bodycam.yaml", 8091, "ALPHA · BODYCAM")


def plan(cameras: Sequence[tuple[str, str]] | None, *, bodycam: bool = True) -> tuple[list[Feed], list[str]]:
    """Feeds to start and why any were skipped. Unknown camera names (not macOS) start everything."""
    feeds, skipped = [], []
    for feed in (FIXED, BODYCAM) if bodycam else (FIXED,):
        name = load_settings(CONFIG_DIR / feed.config).camera.name
        if cameras is None or not name or find_camera(name, cameras) is not None:
            feeds.append(feed)
        else:
            skipped.append(f"{feed.name} skipped: no camera matching '{name}' ({describe_cameras(cameras)})")
    return feeds, skipped


def command(feed: Feed, room: str, display: bool, backend_url: str | None = None,
            stream_host: str = "127.0.0.1", view_only: bool = False) -> list[str]:
    argv = [sys.executable, "-m", "cv.main", "--config", str(CONFIG_DIR / feed.config),
            "--room", room, "--send-signs", "--stream-port", str(feed.port), "--stream-host", stream_host]
    if backend_url:
        argv += ["--backend-url", backend_url]
    if view_only:
        argv += ["--mock-backend", "--quiet-mock"]
    return argv if display else [*argv, "--no-display"]


def lan_address(backend_url: str | None) -> str:
    """This laptop's address on the network the backend is on, for tile links on other laptops."""
    target = urlparse(backend_url).hostname if backend_url else None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((target or "192.168.0.1", 80))      # UDP: picks the route, sends nothing
            return probe.getsockname()[0]
    except OSError:
        return socket.gethostname()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--room", default="room-2", help="room everyone is reported in (default: room-2)")
    parser.add_argument("--no-bodycam", action="store_true", help="fixed camera only")
    parser.add_argument("--bodycam-view-only", action="store_true",
                        help="body cam draws and streams but sends nothing to the backend")
    parser.add_argument("--backend-url", help="backend base URL (default: the configs', http://127.0.0.1:8000)")
    parser.add_argument("--stream-host", default="127.0.0.1",
                        help="0.0.0.0 lets dashboards on other laptops show the tiles")
    parser.add_argument("--no-display", action="store_true", help="no camera windows on the laptop")
    args = parser.parse_args(argv)

    feeds, skipped = plan(list_cameras(), bodycam=not args.no_bodycam)
    for line in skipped:
        print(line, flush=True)
    if not feeds:
        print("No demo camera connected.")
        return 1

    running = [(feed, subprocess.Popen(
        command(feed, args.room, not args.no_display, args.backend_url, args.stream_host,
                view_only=args.bodycam_view_only and feed is BODYCAM), cwd=CV_DIR.parent))
        for feed in feeds]
    host = "127.0.0.1" if args.stream_host == "127.0.0.1" else lan_address(args.backend_url)
    print(f"\nPeople are reported in {args.room} to {args.backend_url or 'the configs backend'}."
          " Dashboard tiles (paste once, the browser remembers):")
    for feed in feeds:
        sends = " (live view only, sends nothing)" if args.bodycam_view_only and feed is BODYCAM else ""
        print(f"  {feed.tile:<16} {feed.url(host)}{sends}")
    print("Ctrl+C stops the cameras.\n", flush=True)

    def stop(signum, frame):
        raise KeyboardInterrupt
    for signum in (signal.SIGTERM, signal.SIGHUP):      # kill or a closed terminal must not orphan the cameras
        signal.signal(signum, stop)

    try:
        while running:
            time.sleep(1)
            for feed, process in list(running):
                if process.poll() is not None:
                    print(f"{feed.name} stopped (exit {process.returncode}).")
                    running.remove((feed, process))
    except KeyboardInterrupt:
        pass
    finally:
        for _, process in running:
            process.terminate()
        for _, process in running:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
