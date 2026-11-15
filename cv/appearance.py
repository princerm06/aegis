"""Clothing colour of a detected person: recognises someone who comes back into view somewhere else.

A colour histogram of the torso (shoulders to hips when the pose shows them,
else the middle of the box). Pose-independent enough to survive standing up or
lying down, cheap enough to run on every detection. Only used to re-attach a new
tracker ID to a person currently out of view; never an identity.
"""

from __future__ import annotations

import cv2
import numpy as np

from cv.detector import Detection

# Hue and saturation only, coarse: brightness and fine shades change with pose and
# lighting. On the mounted C922 takes the same person scored 0.11-0.34, two people 0.47+.
CHANNELS = [0, 1]
BINS = [4, 3]
RANGES = [0, 180, 0, 256]
MIN_SIDE_PX = 12
TORSO = (5, 6, 11, 12)                # COCO shoulders and hips


def torso_box(detection: Detection, min_conf: float = 0.5) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = detection.bbox
    kp, conf = detection.keypoints, detection.keypoint_conf
    if kp is not None and conf is not None and all(conf[i] >= min_conf for i in TORSO):
        xs, ys = [kp[i][0] for i in TORSO], [kp[i][1] for i in TORSO]
        return int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
    w, h = x2 - x1, y2 - y1
    return int(x1 + 0.25 * w), int(y1 + 0.2 * h), int(x2 - 0.25 * w), int(y1 + 0.6 * h)


def appearance(image: np.ndarray, detection: Detection, min_conf: float = 0.5) -> np.ndarray | None:
    """Normalised HSV histogram of the torso, or None when it is too small or off the frame."""
    height, width = image.shape[:2]
    x1, y1, x2, y2 = torso_box(detection, min_conf)
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(width, x2), min(height, y2)
    if x2 - x1 < MIN_SIDE_PX or y2 - y1 < MIN_SIDE_PX:
        return None
    hsv = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], CHANNELS, None, BINS, RANGES).flatten()
    total = float(hist.sum())
    return (hist / total).astype(np.float32) if total > 0 else None


def distance(a: np.ndarray, b: np.ndarray) -> float:
    """Bhattacharyya distance: 0 identical, 1 nothing in common."""
    return float(cv2.compareHist(a, b, cv2.HISTCMP_BHATTACHARYYA))
