"""How a person lies, and who is next to them: ``face_down`` and ``person_nearby``.

- ``face_down``: lying, the body clearly seen (both shoulders, a hip) and wide
  across the shoulders, the head inside the picture, and no nose or eyes for
  ``face_down_hold_s``. Narrow shoulders mean lying on the side facing away,
  which is not reported as face down. A face turned sideways on the floor can
  show the nose and then reads as not face down.
- ``person_nearby``: someone lying or sitting has another person who is not
  lying (standing, sitting, kneeling) within ``nearby_radius_m`` on the floor,
  or, without floor calibration, within ``nearby_body_frac`` body sizes of
  their box, for at least ``nearby_hold_s``. Someone whose box covers a good
  part of theirs also counts, in any posture: a helper bent over a person
  reads as lying or unknown from above, and hides them. Once someone was next
  to them, they count as still there while their own track is hidden (the
  detector often sees one person where a helper kneels over another), until
  they are seen again away from the person. Useful to see that
  someone is already with a person. It says nothing about who that is.

Observations only, never a cause, an injury or a priority.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cv.signs.body import L_EYE, L_HIP, L_SHOULDER, NOSE, R_EYE, R_HIP, R_SHOULDER, body_scale

DOWN = ("lying", "sitting")
UPRIGHT = ("standing", "sitting")   # "unknown" is often a person lying seen from above
FACE_POINTS = (NOSE, L_EYE, R_EYE)
FACE_DOWN_MAX_CONFIDENCE = 0.6
NEARBY_GAP_S = 1.0            # a helper missed by the detector this briefly is still there
NEARBY_COVER = 0.3            # of the smaller box; people lying side by side, even close, overlap less
NEARBY_SAME_BOX = 0.7         # overlap / union: a second box on the same person, not a helper


@dataclass
class PositionState:
    face: deque = field(default_factory=deque)   # (t, True face hidden / False face seen / None cannot tell)
    face_down_since: float | None = None
    near_since: float | None = None
    near_last_t: float | None = None
    near_with: str | None = None                 # track ID of the person last seen next to them
    down: bool = False


def face_evidence(posture: str, bbox, keypoints, keypoint_conf, image_size, min_conf: float,
                  min_shoulder_ratio: float) -> bool | None:
    """True: lying with the face hidden; False: lying with the face seen; None: cannot tell."""
    if posture != "lying" or keypoints is None or keypoint_conf is None:
        return None
    conf, points = np.asarray(keypoint_conf), np.asarray(keypoints, dtype=float)
    if max(conf[i] for i in FACE_POINTS) >= min_conf:
        return False
    hips = [i for i in (L_HIP, R_HIP) if conf[i] >= min_conf]
    if conf[L_SHOULDER] < min_conf or conf[R_SHOULDER] < min_conf or not hips:
        return None
    shoulders = (points[L_SHOULDER] + points[R_SHOULDER]) / 2
    torso = shoulders - points[hips].mean(axis=0)
    length = float(np.linalg.norm(torso))
    if length < 1.0:
        return None
    if float(np.linalg.norm(points[L_SHOULDER] - points[R_SHOULDER])) < min_shoulder_ratio * length:
        return None                                   # on the side, facing away: not face down
    head = shoulders + 0.6 * torso                    # where the head should be
    width, height = image_size
    if not (0 <= head[0] < width and 0 <= head[1] < height):
        return None                                   # head out of the picture: nothing to see
    return True


class PositionEstimator:
    def __init__(self, settings, min_conf: float = 0.5):
        self.settings = settings
        self.min_conf = min_conf

    def update_face(self, state: PositionState, track, image_size, t: float) -> None:
        settings = self.settings
        evidence = face_evidence(track.posture.label, track.bbox, track.keypoints, track.keypoint_conf,
                                 image_size, self.min_conf, settings.face_down_shoulder_ratio)
        state.face.append((t, evidence))
        while state.face and t - state.face[0][0] > settings.face_down_hold_s:
            state.face.popleft()
        recent = [e for _, e in state.face]
        held = (len(recent) >= 5 and state.face[-1][0] - state.face[0][0] >= 0.8 * settings.face_down_hold_s
                and sum(e is True for e in recent) >= 0.8 * len(recent))
        if not held:
            state.face_down_since = None
        elif state.face_down_since is None:
            state.face_down_since = state.face[0][0]

    def update_nearby(self, states: dict[str, PositionState], tracks, t: float) -> None:
        """All people in view at once: who has someone next to them."""
        settings = self.settings
        seen = [track for track in tracks if track.detected]
        hidden = {track.track_id: track for track in tracks if not track.detected}
        for track in seen:
            state = states.get(track.track_id)
            if state is None:
                continue
            if track.posture.label != "unknown":      # half hidden by a helper: keep the last posture
                state.down = track.posture.label in DOWN
            if not state.down:
                state.near_since = state.near_last_t = state.near_with = None
                continue
            helper = next((other for other in seen if other is not track and (
                (other.posture.label in UPRIGHT and self._near(track, other)) or self._covers(track, other))), None)
            gone = hidden.get(state.near_with)               # was next to them, now hidden on top of them
            hidden_here = gone is not None and (self._near(track, gone) or self._covers(track, gone))
            if helper is not None or hidden_here:
                if state.near_last_t is None or t - state.near_last_t > NEARBY_GAP_S:
                    state.near_since = t
                state.near_last_t = t
                if helper is not None:
                    state.near_with = helper.track_id
            elif state.near_last_t is not None and t - state.near_last_t > NEARBY_GAP_S:
                state.near_since = state.near_last_t = state.near_with = None

    def _near(self, person, other) -> bool:
        settings = self.settings
        here, there = getattr(person, "map_xy", None), getattr(other, "map_xy", None)
        if here is not None and there is not None:
            return math.dist(here, there) <= settings.nearby_radius_m
        x1, y1, x2, y2 = person.bbox
        ox1, oy1, ox2, oy2 = other.bbox
        foot = getattr(other, "ground_px", None) or ((ox1 + ox2) / 2, oy2)
        gap = math.hypot(max(x1 - foot[0], 0, foot[0] - x2), max(y1 - foot[1], 0, foot[1] - y2))
        scale = body_scale(person.bbox, person.keypoints, person.keypoint_conf, self.min_conf)
        return gap <= settings.nearby_body_frac * scale

    @staticmethod
    def _covers(person, other) -> bool:
        x1, y1, x2, y2 = person.bbox
        ox1, oy1, ox2, oy2 = other.bbox
        overlap = max(0.0, min(x2, ox2) - max(x1, ox1)) * max(0.0, min(y2, oy2) - max(y1, oy1))
        area, other_area = (x2 - x1) * (y2 - y1), (ox2 - ox1) * (oy2 - oy1)
        smaller, union = min(area, other_area), area + other_area - overlap
        return (smaller > 0 and overlap >= NEARBY_COVER * smaller
                and overlap < NEARBY_SAME_BOX * union)

    # ------------------------------------------------------------------ values
    def face_down(self, state: PositionState, t: float) -> dict:
        if state.face_down_since is not None:
            return {"value": "seen", "confidence": FACE_DOWN_MAX_CONFIDENCE,
                    "for_s": round(t - state.face_down_since, 1)}
        recent = [e for _, e in state.face if e is not None]
        if recent and sum(e is False for e in recent) >= 0.5 * len(recent):
            return {"value": "not_seen", "confidence": 0.5}
        return {"value": "not_testable", "confidence": 0.0}

    def person_nearby(self, state: PositionState, t: float) -> dict:
        if not state.down:
            return {"value": "not_testable", "confidence": 0.0}
        if state.near_since is not None and t - state.near_since >= self.settings.nearby_hold_s:
            return {"value": "seen", "confidence": 0.6, "for_s": round(t - state.near_since, 1)}
        return {"value": "not_seen", "confidence": 0.5}
