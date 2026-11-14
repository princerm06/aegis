"""Body geometry from COCO-17 pose keypoints: scale, face points, region masks."""

from __future__ import annotations

import math

import numpy as np

NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SHOULDER, R_SHOULDER, L_ELBOW, R_ELBOW, L_WRIST, R_WRIST = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE = 11, 12, 13, 14, 15, 16
FACE = (NOSE, L_EYE, R_EYE, L_EAR, R_EAR)
TORSO = (L_SHOULDER, R_SHOULDER, R_HIP, L_HIP)
LIMBS = (L_ELBOW, R_ELBOW, L_WRIST, R_WRIST, L_KNEE, R_KNEE, L_ANKLE, R_ANKLE)
REGIONS = ("head", "torso", "left_arm", "right_arm", "left_leg", "right_leg")
_LIMB_CHAINS = {
    "left_arm": (L_SHOULDER, L_ELBOW, L_WRIST),
    "right_arm": (R_SHOULDER, R_ELBOW, R_WRIST),
    "left_leg": (L_HIP, L_KNEE, L_ANKLE),
    "right_leg": (R_HIP, R_KNEE, R_ANKLE),
}


LIMB_CHAINS = _LIMB_CHAINS


def confident(keypoint_conf, indices, min_conf: float) -> bool:
    return keypoint_conf is not None and all(keypoint_conf[i] >= min_conf for i in indices)


def body_scale(bbox, keypoints=None, keypoint_conf=None, min_conf: float = 0.5) -> float:
    """A size in pixels that does not depend on posture: torso length, else box diagonal."""
    if keypoints is not None and confident(keypoint_conf, TORSO, min_conf):
        shoulders = (keypoints[L_SHOULDER] + keypoints[R_SHOULDER]) / 2
        hips = (keypoints[L_HIP] + keypoints[R_HIP]) / 2
        torso = float(np.linalg.norm(shoulders - hips))
        if torso > 1.0:
            return torso * 2.0
    x1, y1, x2, y2 = bbox
    return max(1.0, math.hypot(x2 - x1, y2 - y1))


def region_masks(bbox, keypoints, keypoint_conf, min_conf: float = 0.5,
                 image_size: tuple[int, int] | None = None) -> tuple[tuple[int, int], dict[str, np.ndarray]]:
    """Boolean masks per body region, in a crop of ``bbox``. Returns (crop origin, masks).

    Limbs are thick segments, the torso a polygon, the head a disc. Regions whose
    keypoints are not confident are left out.
    """
    import cv2

    x1, y1, x2, y2 = (int(round(v)) for v in bbox)
    if image_size is not None:
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(image_size[0], x2), min(image_size[1], y2)
    width, height = max(1, x2 - x1), max(1, y2 - y1)
    if keypoints is None or keypoint_conf is None:
        return (x1, y1), {}
    points = np.asarray(keypoints, dtype=np.float64) - (x1, y1)
    scale = body_scale(bbox, keypoints, keypoint_conf, min_conf)
    limb = max(3, int(scale * 0.09))
    masks: dict[str, np.ndarray] = {}

    def blank():
        return np.zeros((height, width), dtype=np.uint8)

    if confident(keypoint_conf, TORSO, min_conf):
        mask = blank()
        cv2.fillPoly(mask, [points[list(TORSO)].astype(np.int32)], 1)
        masks["torso"] = mask
    for name, chain in _LIMB_CHAINS.items():
        joints = [i for i in chain if keypoint_conf[i] >= min_conf]
        if len(joints) < 2 or joints[0] != chain[0]:
            continue
        mask = blank()
        for a, b in zip(joints, joints[1:]):
            cv2.line(mask, tuple(points[a].astype(int)), tuple(points[b].astype(int)), 1, limb)
        masks[name] = mask
    face = [i for i in FACE if keypoint_conf[i] >= min_conf * 0.6]
    if face:
        center = points[face].mean(axis=0)
        radius = max(limb, int(scale * 0.12))
        mask = blank()
        cv2.circle(mask, tuple(center.astype(int)), radius, 1, -1)
        masks["head"] = mask
    for name in list(masks):  # a pixel belongs to one region; torso wins over limbs
        if name != "torso" and "torso" in masks:
            masks[name] = masks[name] & (1 - masks["torso"])
    return (x1, y1), {name: mask.astype(bool) for name, mask in masks.items() if mask.any()}
