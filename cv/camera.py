"""Webcam or video-file frame source that never raises in the video loop.

A live camera that stops delivering frames is released and reopened every
``retry_interval_s``; ``read()`` returns ``None`` meanwhile. With
``camera.name`` set, the index is looked up by name on every open, so macOS
renumbering cameras (plugging one in, Continuity Camera waking up) cannot
switch to the wrong one; if no camera has that name, it keeps retrying. A file source is
paced to its FPS by default (``fps_override``, e.g. the measured FPS from a
recording's sidecar, wins over the container value) so time-based logic behaves
as it would live. ``loop`` restarts a file at its end, keeping the pacing continuous.
"""

from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Sequence

from cv.settings import CameraSettings

log = logging.getLogger(__name__)


@dataclass
class Frame:
    image: Any
    t: float        # monotonic capture time (s)
    index: int


def list_cameras() -> list[tuple[str, str]] | None:
    """(name, model) of each camera in OpenCV's index order, or None where that order is unknown.

    OpenCV's AVFoundation backend numbers cameras sorted by unique ID, not in
    the order ``devicesWithMediaType`` lists them: with a C922, an iPhone and
    the built-in camera, the listed order put the iPhone at the C922's index.
    ffmpeg's ``-list_devices`` uses yet another order (built-in first), so it
    must not be used to pick an index either.
    """
    if sys.platform != "darwin":
        return None
    try:
        import AVFoundation
    except ImportError:
        return None
    devices = AVFoundation.AVCaptureDevice.devicesWithMediaType_(AVFoundation.AVMediaTypeVideo)
    devices = sorted(devices, key=lambda device: str(device.uniqueID()))
    return [(str(device.localizedName()), str(device.modelID() or "")) for device in devices]


def find_camera(name: str, cameras: Sequence[tuple[str, str]]) -> int | None:
    """Index of the first camera whose name or model contains ``name`` (any case).

    Models make ``iPhone`` match a Continuity Camera whatever the phone is called.
    """
    wanted = name.casefold()
    return next((i for i, (label, model) in enumerate(cameras)
                 if wanted in label.casefold() or wanted in model.casefold()), None)


def rotate(image, degrees: int):
    """Turn a frame clockwise by 90, 180 or 270 degrees (a camera mounted sideways or upside down)."""
    import cv2

    codes = {90: cv2.ROTATE_90_CLOCKWISE, 180: cv2.ROTATE_180, 270: cv2.ROTATE_90_COUNTERCLOCKWISE}
    if degrees % 360 not in codes:
        raise ValueError(f"camera.rotate must be 0, 90, 180 or 270, not {degrees}")
    return cv2.rotate(image, codes[degrees % 360])


def camera_override(settings: CameraSettings, value: str | None) -> CameraSettings:
    """``--camera 1`` picks an index, ``--camera C922`` a name."""
    if value is None:
        return settings
    value = value.strip()
    if value.isdigit():
        return replace(settings, index=int(value), name="")
    return replace(settings, name=value)


def describe_cameras(cameras: Sequence[tuple[str, str]] | None) -> str:
    if cameras is None:
        return "camera names are not available here (macOS with pyobjc-framework-AVFoundation only)"
    return ", ".join(f"{i}: {label}" for i, (label, _) in enumerate(cameras)) or "no cameras connected"


class Camera:
    def __init__(self, settings: CameraSettings, source: str | None = None, *, pace: bool = True,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep,
                 opener: Callable[[], Any] | None = None,
                 fps_override: float | None = None, loop: bool = False,
                 lister: Callable[[], list[tuple[str, str]] | None] = list_cameras):
        self.settings = settings
        self._lister = lister
        self.device_index: int | None = None if settings.name else settings.index
        self.device_name: str | None = None
        self._missing_report: str | None = None
        self.source = source
        self.pace = pace
        self.fps_override = fps_override
        self.loop = loop
        self.loops = 0
        self._frames_this_pass = 0
        self._clock = clock
        self._sleep = sleep
        self._opener = opener or self._open_cv
        self._cap: Any = None
        self._failing_since: float | None = None
        self._next_retry = 0.0
        self._file_fps = 0.0
        self._file_start: float | None = None
        self.index = 0
        self.status = "closed"     # connected / reconnecting / finished / closed
        self.reconnects = 0
        self.resolution: tuple[int, int] | None = None

    @property
    def label(self) -> str:
        if self.source:
            return self.source
        if self.device_name:
            return f"camera:{self.device_index} ({self.device_name})"
        return f"camera:{self.settings.name or self.settings.index}"

    def resolve_index(self) -> int | None:
        """The index to open: by name when ``camera.name`` is set, else the configured index."""
        if not self.settings.name:
            return self.settings.index
        names = self._lister()
        if names is None:
            if self._missing_report != "unlisted":
                log.warning("Cannot list cameras by name here; using index %d for %r",
                            self.settings.index, self.settings.name)
                self._missing_report = "unlisted"
            self.device_index, self.device_name = self.settings.index, None
            return self.settings.index
        index = find_camera(self.settings.name, names)
        if index is None:
            report = describe_cameras(names)
            if report != self._missing_report:
                log.warning("No camera named %r yet; connected: %s", self.settings.name, report)
                self._missing_report = report
            return None
        label = names[index][0]
        if (index, label) != (self.device_index, self.device_name):
            log.info("Camera %r is index %d (%s)", self.settings.name, index, label)
        self._missing_report = None
        self.device_index, self.device_name = index, label
        return index

    @property
    def is_file(self) -> bool:
        return self.source is not None

    @property
    def finished(self) -> bool:
        return self.status == "finished"

    @property
    def connected(self) -> bool:
        return self.status == "connected"

    def _open_cv(self) -> Any:
        import cv2

        if self.source:
            return cv2.VideoCapture(self.source)
        index = self.resolve_index()
        if index is None:
            return None
        backend = cv2.CAP_AVFOUNDATION if sys.platform == "darwin" else cv2.CAP_ANY
        capture = cv2.VideoCapture(index, backend)
        capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.height)
        capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return capture

    def open(self) -> bool:
        self._release_capture()
        try:
            capture = self._opener()
        except Exception as error:  # a broken backend must not crash the loop
            log.warning("Opening %s failed: %s", self.label, error)
            capture = None
        if capture is None or not capture.isOpened():
            if capture is not None:
                capture.release()
            if self.is_file:
                log.error("Cannot open video file %s", self.source)
                self.status = "finished"
            else:
                self.status = "reconnecting"
                self._next_retry = self._clock() + self.settings.retry_interval_s
            return False
        self._cap = capture
        self.status = "connected"
        self._failing_since = None
        if self.is_file:
            import cv2

            self._frames_this_pass = 0
            fps = self.fps_override or float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
            self._file_fps = fps if 1.0 <= fps <= 240.0 else 30.0
        return True

    @property
    def file_fps(self) -> float:
        return self._file_fps

    def read(self) -> Frame | None:
        if self.finished:
            return None
        if self._cap is None:
            if self._clock() >= self._next_retry:
                if self.status == "reconnecting":
                    self.reconnects += 1
                self.open()
            if self._cap is None:
                self._sleep(0.02)
                return None
        try:
            ok, image = self._cap.read()
        except Exception as error:
            log.warning("Reading %s failed: %s", self.label, error)
            ok, image = False, None
        now = self._clock()
        if ok and image is not None:
            self._failing_since = None
            if not self.is_file and self.settings.rotate:
                image = rotate(image, self.settings.rotate)
            self.resolution = (int(image.shape[1]), int(image.shape[0]))
            if self.is_file and self.pace:
                if self._file_start is None:
                    self._file_start = now
                delay = self._file_start + self.index / self._file_fps - now
                if delay > 0:
                    self._sleep(delay)
                    now = self._clock()
            frame = Frame(image=image, t=now, index=self.index)
            self.index += 1
            self._frames_this_pass += 1
            return frame
        if self.is_file:
            self._release_capture()
            if self.loop and self._frames_this_pass > 0 and self.open():
                self.loops += 1
                return None
            self.status = "finished"
            return None
        if self._failing_since is None:
            self._failing_since = now
        if now - self._failing_since >= self.settings.reconnect_after_s:
            log.warning("%s stopped delivering frames; reconnecting", self.label)
            self._release_capture()
            self.status = "reconnecting"
            self._next_retry = now
        else:
            self._sleep(0.01)
        return None

    def _release_capture(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def release(self) -> None:
        self._release_capture()
        if not self.finished:
            self.status = "closed"
