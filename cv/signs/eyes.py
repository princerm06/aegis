"""Eyes open / closed from MediaPipe Face Landmarker blink scores.

The face is cropped around the pose model's face keypoints, rotated so the eyes
are level (people lying down have sideways faces, which the face detector
handles poorly), upscaled, and scored with ``eyeBlinkLeft`` / ``eyeBlinkRight``
(see ``combine_blinks`` for turned heads). Small faces, faces turned more than
``eyes_max_yaw_deg`` and faces not found are ``not_testable``. Measured on CEW
(2,423 labelled faces, ``cv.tools.eyes_eval``): 95% accuracy at threshold 0.4. A 720p webcam gives roughly
40-60 px faces at 2-3 m; use 1080p capture when eyes matter.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from cv.signs.body import FACE, L_EAR, L_EYE, NOSE, R_EAR, R_EYE

log = logging.getLogger(__name__)
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
CROP_PX = 256
DOWNLOAD_TIMEOUT_S = 20.0      # per network read; a venue hotspot without internet must not hang startup


def face_crop(image_bgr, keypoints, keypoint_conf, min_conf: float, min_face_px: int):
    """Upright, upscaled face crop, or None when the face is not testable."""
    import cv2

    if keypoints is None or keypoint_conf is None:
        return None
    if keypoint_conf[L_EYE] < min_conf or keypoint_conf[R_EYE] < min_conf or keypoint_conf[NOSE] < min_conf:
        return None
    keypoints = np.asarray(keypoints, dtype=np.float64)
    face = [i for i in FACE if keypoint_conf[i] >= min_conf]
    points = np.asarray(keypoints, dtype=np.float64)[face]
    left, right = np.asarray(keypoints[L_EYE], float), np.asarray(keypoints[R_EYE], float)
    eye_distance = float(np.linalg.norm(left - right))
    ears = [i for i in (L_EAR, R_EAR) if keypoint_conf[i] >= min_conf]
    spread = float(np.ptp(points, axis=0).max()) if len(points) > 1 else 0.0
    size = max(eye_distance * 3.2, spread * 1.6, 1.0)
    if ears and len(ears) == 2:
        size = max(size, float(np.linalg.norm(keypoints[L_EAR] - keypoints[R_EAR])) * 1.4)
    if size < min_face_px:
        return None
    center = points.mean(axis=0)
    # Subject's right eye appears on the image left when facing the camera.
    angle = math.degrees(math.atan2(left[1] - right[1], left[0] - right[0]))
    scale = CROP_PX / size
    matrix = cv2.getRotationMatrix2D((float(center[0]), float(center[1])), angle, scale)
    matrix[:, 2] += (CROP_PX / 2 - center[0], CROP_PX / 2 - center[1])
    return cv2.warpAffine(image_bgr, matrix, (CROP_PX, CROP_PX), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT)


def head_yaw_deg(matrix) -> float:
    """Head turn left/right from the Face Landmarker's facial transformation matrix."""
    rotation = np.asarray(matrix, dtype=float)[:3, :3]
    return float(math.degrees(math.atan2(-rotation[2, 0], math.hypot(rotation[0, 0], rotation[1, 0]))))


def combine_blinks(left: float, right: float, yaw_deg: float, turned_deg: float, max_yaw_deg: float):
    """One closed score from both eyes, or None when the face is too far turned.

    In a turned face the far eye's blink score is unreliable (measured: 0.17 on
    one side and 0.55 mirrored for the same closed eye), so the higher score is
    used, which errs toward "closed" (more attention, never less).
    """
    if abs(yaw_deg) > max_yaw_deg:
        return None
    if abs(yaw_deg) > turned_deg:
        return max(left, right)
    return (left + right) / 2


class FaceScorer:
    """Lazy MediaPipe Face Landmarker; disables itself if the model is unavailable."""

    def __init__(self, model_path: Path, turned_deg: float = 25.0, max_yaw_deg: float = 60.0):
        self.model_path = model_path
        self.turned_deg = turned_deg
        self.max_yaw_deg = max_yaw_deg
        self._landmarker = None
        self.available = True

    def prepare(self) -> bool:
        """Load (and if needed download) the model now, before the frame loop needs it."""
        return self._load() is not None

    def _load(self):
        if self._landmarker is not None or not self.available:
            return self._landmarker
        try:
            from mediapipe.tasks.python import BaseOptions, vision

            if not self.model_path.exists():
                self._download()
            options = vision.FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=str(self.model_path)),
                output_face_blendshapes=True, output_facial_transformation_matrixes=True,
                num_faces=1, running_mode=vision.RunningMode.IMAGE)
            self._landmarker = vision.FaceLandmarker.create_from_options(options)
        except Exception as error:  # missing package / no network: eyes become not_testable
            log.warning("Eye-state detector unavailable (%s); eyes will be not_testable", error)
            self.available = False
        return self._landmarker

    def _download(self) -> None:
        import urllib.request

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Downloading %s", MODEL_URL)
        partial = self.model_path.with_suffix(".part")
        with urllib.request.urlopen(MODEL_URL, timeout=DOWNLOAD_TIMEOUT_S) as response, open(partial, "wb") as out:
            while chunk := response.read(1 << 16):
                out.write(chunk)
        partial.rename(self.model_path)

    def closed_score(self, crop_bgr) -> float | None:
        landmarker = self._load()
        if landmarker is None:
            return None
        import cv2
        import mediapipe as mp

        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        result = landmarker.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb)))
        if not result.face_blendshapes:
            return None
        scores = {c.category_name: c.score for c in result.face_blendshapes[0]}
        if "eyeBlinkLeft" not in scores or "eyeBlinkRight" not in scores:
            return None
        yaw = head_yaw_deg(result.facial_transformation_matrixes[0]) if result.facial_transformation_matrixes else 0.0
        return combine_blinks(scores["eyeBlinkLeft"], scores["eyeBlinkRight"], yaw,
                              self.turned_deg, self.max_yaw_deg)


@dataclass
class EyeState:
    samples: deque = field(default_factory=deque)   # (t, closed score or None)
    last_check_t: float = -1e9


class EyeEstimator:
    def __init__(self, settings, scorer: FaceScorer, min_conf: float = 0.5):
        self.settings = settings
        self.scorer = scorer
        self.min_conf = min_conf

    def due(self, state: EyeState, t: float) -> bool:
        return self.scorer.available and t - state.last_check_t >= 1.0 / max(0.1, self.settings.eyes_hz)

    def update(self, state: EyeState, image_bgr, keypoints, keypoint_conf, t: float) -> None:
        state.last_check_t = t
        crop = face_crop(image_bgr, keypoints, keypoint_conf, self.min_conf * 0.6,
                         self.settings.eyes_min_face_px)
        state.samples.append((t, None if crop is None else self.scorer.closed_score(crop)))
        while state.samples and t - state.samples[0][0] > self.settings.eyes_window_s:
            state.samples.popleft()

    def value(self, state: EyeState, t: float) -> dict:
        scores = [s for ts, s in state.samples if s is not None and t - ts <= self.settings.eyes_window_s]
        if len(scores) < 2:
            return {"value": "not_testable", "confidence": 0.0}
        mean = float(np.mean(scores))
        threshold = self.settings.eyes_closed_threshold
        closed = mean >= threshold
        margin = abs(mean - threshold) / max(threshold, 1 - threshold)
        agreement = np.mean([(s >= threshold) == closed for s in scores])
        return {"value": "closed" if closed else "open",
                "confidence": round(float(min(1.0, 0.3 + 0.4 * margin + 0.3 * agreement)), 2)}
