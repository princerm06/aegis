"""The MJPEG stream for the dashboard tile, and commands posted from the dashboard."""

import threading
import time
import urllib.request

import numpy as np

from cv.stream import BOUNDARY, FrameStream


def keep_publishing(stream, stop):
    canvas = np.full((72, 128, 3), 40, np.uint8)
    while not stop.is_set():
        stream.publish(canvas)
        time.sleep(0.02)


def test_frames_and_the_stream_are_served_only_while_watched_and_commands_are_counted():
    stream = FrameStream("127.0.0.1", 0, fps=50)
    stream.start()
    stop = threading.Event()
    publisher = threading.Thread(target=keep_publishing, args=(stream, stop), daemon=True)
    try:
        assert not stream.watched
        stream.publish(np.zeros((8, 8, 3), np.uint8))
        assert stream._jpeg is None                         # nobody watching: nothing encoded
        publisher.start()
        base = f"http://127.0.0.1:{stream.port}"
        with urllib.request.urlopen(base + "/frame.jpg", timeout=5) as response:
            assert response.headers["Content-Type"] == "image/jpeg"
            assert response.headers["Access-Control-Allow-Origin"] == "*"
            assert response.read()[:2] == b"\xff\xd8"
        with urllib.request.urlopen(base + "/stream.mjpg", timeout=5) as response:
            assert BOUNDARY in response.headers["Content-Type"]
            head = response.read(400)
            assert head.startswith(f"--{BOUNDARY}".encode()) and b"\xff\xd8" in head
        preflight = urllib.request.Request(base + "/command", method="OPTIONS")
        with urllib.request.urlopen(preflight, timeout=5) as response:
            assert "POST" in response.headers["Access-Control-Allow-Methods"]
        assert stream.take_commands() == 0
        request = urllib.request.Request(base + "/command", method="POST")
        for _ in range(2):
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 204
        assert stream.take_commands() == 2 and stream.take_commands() == 0
    finally:
        stop.set()
        stream.stop()
