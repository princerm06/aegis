"""What happened to a person over time: a fall, walking, limbs that did not move, changes.

All of these are observations of events, never an injury or a priority:

- ``fall``: the head dropped fast (``fall_min_drop`` body sizes within
  ``fall_drop_s``) from an upright posture, and the person was lying within
  ``fall_confirm_s``. Lying down slowly is not a fall.
- ``walking``: upright while the ground point travelled ``walk_min_distance``
  body sizes within ``walk_window_s`` (START triage asks "can they walk?").
- ``limbs``: per arm and leg, whether the hand or foot moved relative to the
  torso in the last ``limb_window_s``. ``some_not_moved`` is only reported when
  another limb clearly moved, so a still person is ``none_moved``.
- ``changes``: the last few changes of a sign's value that held for
  ``change_hold_s`` (e.g. eyes open -> closed), with the time they started.

A moving camera (bodycam) pauses fall, walking and limbs: image motion is then
not the person's.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cv.signs.body import (L_ANKLE, L_ELBOW, L_KNEE, L_SHOULDER, L_WRIST, NOSE, R_ANKLE, R_ELBOW, R_KNEE,
                           R_SHOULDER, R_WRIST)
from cv.signs.motion import IMAGE_FRAME, Sample, body_frame

UPRIGHT = ("standing", "sitting")
LIMB_ENDS = {"left_arm": (L_ELBOW, L_WRIST), "right_arm": (R_ELBOW, R_WRIST),
             "left_leg": (L_KNEE, L_ANKLE), "right_leg": (R_KNEE, R_ANKLE)}
TRACKED_SIGNS = ("posture", "movement", "response", "eyes", "breathing", "visible_blood")
UNINFORMATIVE = ("not_testable", "not_tested", "unknown")
MAX_CHANGES = 5
LIMB_MOVED_SAMPLES = 5        # moved in at least this many samples of the window
LIMB_MAX_CONFIDENCE = 0.6     # keypoint jitter on limbs is high


@dataclass
class _Point:
    t: float
    posture: str
    head_y: float | None
    ground: np.ndarray
    scale: float
    flat: bool = False        # box wider than tall: on the floor, whatever the posture vote says


@dataclass
class _Change:
    stable: str | None = None
    candidate: str | None = None
    since: float = 0.0


@dataclass
class HistoryState:
    first_t: float | None = None
    points: deque = field(default_factory=deque)          # _Point, last few seconds
    drops: deque = field(default_factory=deque)           # (t, drop in body sizes, posture before)
    fall_t: float | None = None
    fall_drop: float = 0.0
    walk_t: float | None = None
    walk_distance: float = 0.0
    limbs: dict = field(default_factory=lambda: {name: deque() for name in LIMB_ENDS})   # (t, moved)
    paused_t: float | None = None                          # last time the camera was moving
    signs: dict = field(default_factory=lambda: {name: _Change() for name in TRACKED_SIGNS})
    changes: deque = field(default_factory=lambda: deque(maxlen=MAX_CHANGES))       # (sign, from, to, t)
    last_change_check_t: float = -1e9


def _head_y(sample: Sample, min_conf: float) -> float | None:
    conf, points = sample.keypoint_conf, sample.keypoints
    if points is None or conf is None:
        return None
    shoulders = [i for i in (L_SHOULDER, R_SHOULDER) if conf[i] >= min_conf]
    if conf[NOSE] >= min_conf:
        return float(points[NOSE][1])
    if shoulders:
        return float(points[shoulders].mean(axis=0)[1])
    return None


class HistoryEstimator:
    def __init__(self, settings, min_conf: float = 0.5):
        self.settings = settings
        self.min_conf = min_conf

    # ------------------------------------------------------------------ update
    def update(self, state: HistoryState, sample: Sample, reference: Sample | None, posture: str,
               ground, bbox) -> None:
        settings, t = self.settings, sample.t
        state.first_t = t if state.first_t is None else state.first_t
        if sample.camera not in IMAGE_FRAME:
            state.paused_t = t
            state.points.clear()
            state.drops.clear()
            return
        x1, y1, x2, y2 = bbox
        ground = np.asarray(ground if ground is not None else ((x1 + x2) / 2, y2), dtype=float)
        head = _head_y(sample, self.min_conf)
        state.points.append(_Point(t, posture, head if head is not None else float(y1), ground, sample.scale,
                                   x2 - x1 > 1.2 * (y2 - y1)))
        keep = max(settings.walk_window_s, settings.fall_drop_s) + 0.5
        while state.points and t - state.points[0].t > keep:
            state.points.popleft()
        self._update_fall(state, t, posture)
        self._update_walk(state, t)
        if reference is not None:
            self._update_limbs(state, sample, reference)

    def _update_fall(self, state: HistoryState, t: float, posture: str) -> None:
        settings = self.settings
        now = state.points[-1]
        before = next((p for p in state.points if now.t - p.t <= settings.fall_drop_s and p.posture in UPRIGHT),
                      None)
        if before is not None:
            upright = max(p.scale for p in state.points if p.t >= before.t)
            drop = (now.head_y - before.head_y) / max(upright, 1.0)
            if drop >= settings.fall_min_drop:
                state.drops.append((before.t, drop))
        while state.drops and t - state.drops[0][0] > settings.fall_confirm_s:
            state.drops.popleft()
        down = posture == "lying" or (posture == "unknown" and now.flat)
        if down and state.drops:
            start, drop = state.drops[0][0], max(d for _, d in state.drops)
            if state.fall_t is None or start - state.fall_t > settings.fall_confirm_s:
                state.fall_t, state.fall_drop = start, drop
            state.drops.clear()

    def _update_walk(self, state: HistoryState, t: float) -> None:
        settings = self.settings
        window = [p for p in state.points if t - p.t <= settings.walk_window_s]
        if len(window) < 3 or window[-1].t - window[0].t < settings.walk_window_s * 0.5:
            return
        if sum(p.posture == "standing" for p in window) < 0.8 * len(window):
            return
        scale = float(np.mean([p.scale for p in window]))
        distance = float(np.linalg.norm(window[-1].ground - window[0].ground)) / max(scale, 1.0)
        if distance >= settings.walk_min_distance:
            state.walk_t, state.walk_distance = t, distance

    def _update_limbs(self, state: HistoryState, sample: Sample, reference: Sample) -> None:
        settings, t = self.settings, sample.t
        if sample.keypoints is None or reference.keypoints is None:
            return
        try:
            now, then = body_frame(sample, self.min_conf), body_frame(reference, self.min_conf)
        except (IndexError, ValueError):
            return
        scale = max(sample.scale, reference.scale, 1.0)
        for name, joints in LIMB_ENDS.items():
            seen = [i for i in joints if sample.keypoint_conf[i] >= self.min_conf
                    and reference.keypoint_conf[i] >= self.min_conf]
            history = state.limbs[name]
            if seen:
                moved = max(float(np.linalg.norm(now[i] - then[i])) for i in seen) / scale
                history.append((t, moved >= settings.limb_move_threshold))
            while history and t - history[0][0] > settings.limb_window_s:
                history.popleft()

    def track_changes(self, state: HistoryState, values: dict[str, str], t: float) -> None:
        """Feed the current sign values (at most a few times a second)."""
        if t - state.last_change_check_t < 0.25:
            return
        state.last_change_check_t = t
        for name, value in values.items():
            change = state.signs.get(name)
            if change is None or value in UNINFORMATIVE:
                continue
            if value != change.candidate:
                change.candidate, change.since = value, t
            elif value != change.stable and t - change.since >= self.settings.change_hold_s:
                if change.stable is not None:
                    state.changes.append((name, change.stable, value, change.since))
                change.stable = value

    # ------------------------------------------------------------------ values
    def _paused(self, state: HistoryState, t: float) -> bool:
        return state.paused_t is not None and t - state.paused_t < 1.0

    def fall(self, state: HistoryState, t: float, iso) -> dict:
        if state.fall_t is not None:
            return {"value": "seen", "confidence": round(min(0.9, 0.5 + 0.3 * state.fall_drop), 2),
                    "at": iso(state.fall_t)}
        if self._paused(state, t):
            return {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"}
        return {"value": "not_seen", "confidence": 0.5}

    def walking(self, state: HistoryState, t: float, iso) -> dict:
        if state.walk_t is not None:
            return {"value": "seen", "confidence": round(min(0.9, 0.5 + 0.2 * state.walk_distance), 2),
                    "at": iso(state.walk_t)}
        if self._paused(state, t):
            return {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"}
        if state.first_t is None or t - state.first_t < self.settings.walk_window_s:
            return {"value": "not_testable", "confidence": 0.0}
        return {"value": "not_seen", "confidence": 0.5}

    def limbs(self, state: HistoryState, t: float) -> dict:
        settings = self.settings
        result = {"value": "not_testable", "confidence": 0.0, "window_s": settings.limb_window_s}
        if self._paused(state, t):
            return {**result, "reason": "camera_moving"}
        if state.first_t is None or t - state.first_t < settings.limb_window_s * 0.5:
            return result
        moved, still = [], []
        for name, history in state.limbs.items():
            recent = [hit for ts, hit in history if t - ts <= settings.limb_window_s]
            if len(recent) < 10:
                continue
            count = sum(recent)
            if count >= LIMB_MOVED_SAMPLES:
                moved.append(name)
            elif count <= 1:
                still.append(name)
        if not moved and not still:
            return result
        if not moved:
            return {**result, "value": "none_moved", "confidence": 0.5}
        if not still:
            return {**result, "value": "all_moved", "confidence": LIMB_MAX_CONFIDENCE}
        return {**result, "value": "some_not_moved", "confidence": 0.45, "not_moved": sorted(still)}

    def changes(self, state: HistoryState, t: float, iso) -> list[dict]:
        keep = self.settings.change_keep_s
        return [{"sign": name, "from": old, "to": new, "at": iso(at)}
                for name, old, new, at in state.changes if t - at <= keep]
