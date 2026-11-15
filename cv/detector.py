"""Person detection + ByteTrack via Ultralytics ``model.track(persist=True)``."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import numpy as np

from cv.settings import DetectorSettings, resolve, resolve_model

log = logging.getLogger(__name__)
PERSON_CLASS = 0


@dataclass
class Detection:
    raw_id: int | None                       # ByteTrack ID; None until the tracker confirms
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 in pixels
    confidence: float
    keypoints: np.ndarray | None = None      # (17, 2) COCO keypoints in pixels
    keypoint_conf: np.ndarray | None = None  # (17,)
    appearance: np.ndarray | None = None     # torso colour histogram (cv.appearance), set by the caller


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


def parse_result(result: Any) -> list[Detection]:
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []
    xyxy = boxes.xyxy.cpu().numpy()
    conf = boxes.conf.cpu().numpy()
    ids = boxes.id.cpu().numpy().astype(int) if boxes.id is not None else [None] * len(xyxy)
    keypoints = getattr(result, "keypoints", None)
    kp_xy = kp_conf = None
    if keypoints is not None and keypoints.xy is not None and len(keypoints.xy) == len(xyxy):
        kp_xy = keypoints.xy.cpu().numpy()
        kp_conf = keypoints.conf.cpu().numpy() if keypoints.conf is not None else None
    detections = []
    for i in range(len(xyxy)):
        detections.append(Detection(
            raw_id=None if ids[i] is None else int(ids[i]),
            bbox=tuple(float(v) for v in xyxy[i]),
            confidence=float(conf[i]),
            keypoints=kp_xy[i] if kp_xy is not None else None,
            keypoint_conf=kp_conf[i] if kp_conf is not None else None,
        ))
    return detections


class Detector:
    def __init__(self, settings: DetectorSettings):
        from ultralytics import YOLO

        self.settings = settings
        self.device = pick_device(settings.device)
        self.model = YOLO(resolve_model(settings.model))
        self.tracker_config = str(resolve(settings.tracker))
        self.has_keypoints = getattr(self.model, "task", "") == "pose"
        self.errors = 0

    def warmup(self, width: int, height: int) -> None:
        """First-call model/MPS setup takes ~2 s; do it before frames are timed.

        Uses ``predict`` so the tracker state is untouched.
        """
        blank = np.zeros((height, width, 3), dtype=np.uint8)
        self.model.predict(blank, classes=[PERSON_CLASS], imgsz=self.settings.imgsz,
                           device=self.device, verbose=False)

    def __call__(self, image) -> tuple[list[Detection], float]:
        """Detections for one frame and the inference time in seconds."""
        started = time.perf_counter()
        try:
            result = self.model.track(
                image, persist=True, tracker=self.tracker_config, classes=[PERSON_CLASS],
                conf=self.settings.conf, imgsz=self.settings.imgsz, device=self.device, verbose=False,
            )[0]
            detections = parse_result(result)
        except Exception as error:  # one bad frame must not stop the pipeline
            self.errors += 1
            log.warning("Detection failed on a frame: %s", error)
            detections = []
        return detections, time.perf_counter() - started
