"""Visual observations derived from pose keypoints. Never clinical.

Posture is a geometric description of the body (upright or on the floor). It is
not an injury label, a severity score or a triage category. Confidence values
here are heuristic agreement scores, not calibrated probabilities.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

import numpy as np

SHOULDERS = (5, 6)
HIPS = (11, 12)
THIGHS = ((11, 13), (12, 14))  # (hip, knee) per side

FloorMapper = Callable[[tuple[float, float]], "tuple[float, float] | None"]


@dataclass(frozen=True)
class PostureEstimate:
    label: str          # standing / sitting / lying / unknown
    confidence: float
    method: str


UNKNOWN_POSTURE = PostureEstimate("unknown", 0.0, "none")


def _mean_point(keypoints, keypoint_conf, indices: Iterable[int], min_conf: float):
    points = [keypoints[i] for i in indices if keypoint_conf[i] >= min_conf]
    if not points:
        return None
    mean = np.mean(np.asarray(points, dtype=np.float64), axis=0)
    return float(mean[0]), float(mean[1])


def _angle_from_vertical(dx: float, dy: float) -> float:
    return math.degrees(math.atan2(abs(dx), abs(dy)))


def _head_up(keypoints, keypoint_conf, min_conf: float) -> bool:
    """Nose above level shoulders by a third of the shoulder width: an upright upper body."""
    nose, left, right = 0, *SHOULDERS
    if min(keypoint_conf[nose], keypoint_conf[left], keypoint_conf[right]) < min_conf:
        return False
    (nx, ny), (lx, ly), (rx, ry) = (keypoints[i] for i in (nose, left, right))
    width = math.hypot(lx - rx, ly - ry)
    level = abs(ly - ry) <= 0.35 * width
    return width > 0 and level and (ly + ry) / 2 - ny >= width / 3


def classify_posture(bbox, keypoints=None, keypoint_conf=None, *, min_conf: float = 0.5,
                     to_floor: FloorMapper | None = None,
                     lying_max_torso_m: float = 0.75) -> PostureEstimate:
    """Standing / sitting / lying / unknown from keypoints, else box shape.

    Cues, strongest first:
    1. Torso length on the floor plane (needs calibration). Shoulders and hips
       of a lying person are both near the floor, so their mapped distance is
       about the real torso length. An upright person's shoulders project far
       behind the hips. This also catches people lying along the camera's
       viewing direction, whose torso looks vertical in the image.
    2. Torso angle in the image (shoulder midpoint vs hip midpoint).
    3. Thigh angle to separate sitting from standing.
    4. Box aspect ratio when keypoints are missing.
    """
    x1, y1, x2, y2 = (float(value) for value in bbox)
    aspect = max(y2 - y1, 1e-6) / max(x2 - x1, 1e-6)

    if keypoints is not None and keypoint_conf is not None:
        shoulders = _mean_point(keypoints, keypoint_conf, SHOULDERS, min_conf)
        hips = _mean_point(keypoints, keypoint_conf, HIPS, min_conf)
        if shoulders and hips and math.dist(shoulders, hips) >= 2.0:
            angle = _angle_from_vertical(shoulders[0] - hips[0], shoulders[1] - hips[1])
            if to_floor is not None:
                a, b = to_floor(shoulders), to_floor(hips)
                if a is not None and b is not None and math.dist(a, b) <= lying_max_torso_m:
                    return PostureEstimate("lying", 0.8 if angle >= 45 else 0.65, "torso_floor_length")
            if angle >= 60:
                return PostureEstimate("lying", 0.75 if aspect < 1.0 else 0.6, "torso_angle")
            if angle <= 30:
                thigh_angles = [
                    _angle_from_vertical(keypoints[knee][0] - keypoints[hip][0],
                                         keypoints[knee][1] - keypoints[hip][1])
                    for hip, knee in THIGHS
                    if keypoint_conf[hip] >= min_conf and keypoint_conf[knee] >= min_conf
                ]
                if thigh_angles:
                    if float(np.mean(thigh_angles)) >= 50:
                        return PostureEstimate("sitting", 0.65, "thigh_angle")
                    return PostureEstimate("standing", 0.7, "thigh_angle")
                if aspect >= 1.8:
                    return PostureEstimate("standing", 0.5, "torso_angle_aspect")
                return PostureEstimate("sitting", 0.4, "torso_angle_aspect")
            return PostureEstimate("unknown", 0.3, "torso_angle_ambiguous")
        if shoulders and not hips and _head_up(keypoints, keypoint_conf, min_conf):
            # Head and shoulders close up, hips out of the picture: the box is wide, but
            # the upper body is upright, so its shape says nothing about lying.
            return PostureEstimate("unknown", 0.2, "upper_body_only")

    if aspect <= 0.75:
        return PostureEstimate("lying", 0.45, "bbox_aspect")
    if aspect >= 1.8:
        return PostureEstimate("standing", 0.45, "bbox_aspect")
    return PostureEstimate("unknown", 0.2, "bbox_aspect")


def vote_posture(history: Sequence[PostureEstimate]) -> PostureEstimate:
    """Majority label over recent frames; confidence scaled by agreement."""
    if not history:
        return UNKNOWN_POSTURE
    counts = Counter(item.label for item in history)
    # Ties resolve to the most recent of the tied labels.
    top = max(counts.values())
    label = next(item.label for item in reversed(history) if counts[item.label] == top)
    matching = [item for item in history if item.label == label]
    agreement = len(matching) / len(history)
    confidence = float(np.mean([item.confidence for item in matching])) * agreement
    return PostureEstimate(label, round(confidence, 2), matching[-1].method)
