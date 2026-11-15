"""One command for the live demo: the mounted C922 and, when the iPhone is connected, the body cam.

    python -m cv.demo                  # both cameras, everyone reported in room-2
    python -m cv.demo --room room-1
    python -m cv.demo --no-bodycam     # fixed camera only
    python -m cv.demo --no-display     # no windows on the laptop, dashboard tiles only

Each camera runs as its own ``cv.main`` process (``cv/config/demo.yaml`` and
``cv/config/bodycam.yaml``) with signs sent to the backend and its view served
for a dashboard tile. Paste each tile's link once; the browser remembers it.
A camera that is not connected is skipped. Ctrl+C stops both.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Sequence

from cv.camera import describe_cameras, find_camera, list_cameras
from cv.settings import CONFIG_DIR, CV_DIR, load_settings


@dataclass(frozen=True)
class Feed:
    name: str       # what the operator calls it
    config: str     # file in cv/config
    port: int
    tile: str       # dashboard tile that shows it

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/stream.mjpg"


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


def command(feed: Feed, room: str, display: bool) -> list[str]:
    argv = [sys.executable, "-m", "cv.main", "--config", str(CONFIG_DIR / feed.config),
            "--room", room, "--send-signs", "--stream-port", str(feed.port)]
    return argv if display else [*argv, "--no-display"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--room", default="room-2", help="room everyone is reported in (default: room-2)")
    parser.add_argument("--no-bodycam", action="store_true", help="fixed camera only")
    parser.add_argument("--no-display", action="store_true", help="no camera windows on the laptop")
    args = parser.parse_args(argv)

    feeds, skipped = plan(list_cameras(), bodycam=not args.no_bodycam)
    for line in skipped:
        print(line, flush=True)
    if not feeds:
        print("No demo camera connected.")
        return 1

    running = [(feed, subprocess.Popen(command(feed, args.room, not args.no_display), cwd=CV_DIR.parent))
               for feed in feeds]
    print(f"\nPeople are reported in {args.room}. Dashboard tiles (paste once, the browser remembers):")
    for feed in feeds:
        print(f"  {feed.tile:<16} {feed.url}")
    print("Ctrl+C stops the cameras.\n", flush=True)

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
