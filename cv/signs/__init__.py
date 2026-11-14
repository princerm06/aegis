"""Observed signs per track: posture, movement, command response, eyes, breathing, blood,
what happened over time: a fall, walking, limbs that did not move, waving, blood spreading,
changes of the other signs (signs/history.py), and lying face down or someone next to
them (signs/position.py).

Every sign is an observation with a confidence and an explicit ``not_testable``
/ ``not_tested``. Nothing here is a diagnosis, injury type, severity or
priority. The backend stores and shows the signs; only the medic-confirmed
priority is used for dispatch (see cv/CONTRACT_PROPOSAL.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

from cv.settings import MODELS_DIR, SignsSettings, resolve
from cv.signs.blood import BloodEstimator, BloodState
from cv.signs.breathing import BreathingEstimator, BreathingState, chest_signal
from cv.signs.camera_motion import CameraMotionEstimator, CameraMotionState
from cv.signs.eyes import EyeEstimator, EyeState, FaceScorer
from cv.signs.history import HistoryEstimator, HistoryState
from cv.signs.motion import IMAGE_FRAME, MotionEstimator, MotionState
from cv.signs.position import PositionEstimator, PositionState
from cv.signs.pulse import PulseEstimator, PulseState

SCHEMA = "aegis-cv-signs/0.1"


@dataclass
class _TrackSigns:
    motion: MotionState = field(default_factory=MotionState)
    blood: BloodState = field(default_factory=BloodState)
    eyes: EyeState = field(default_factory=EyeState)
    breathing: BreathingState = field(default_factory=BreathingState)
    history: HistoryState = field(default_factory=HistoryState)
    position: PositionState = field(default_factory=PositionState)
    pulse: PulseState = field(default_factory=PulseState)


class SignsEstimator:
    def __init__(self, settings: SignsSettings, iso: Callable[[float], str],
                 keypoint_min_conf: float = 0.5, face_scorer: FaceScorer | None = None):
        self.settings = settings
        self.iso = iso
        self.motion = MotionEstimator(settings, keypoint_min_conf)
        self.blood = BloodEstimator(settings, keypoint_min_conf)
        self.breathing = BreathingEstimator(settings, keypoint_min_conf)
        scorer = face_scorer or FaceScorer(resolve(settings.eyes_model, MODELS_DIR),
                                           settings.eyes_turned_deg, settings.eyes_max_yaw_deg)
        self.eyes = EyeEstimator(settings, scorer, keypoint_min_conf)
        self.history = HistoryEstimator(settings, keypoint_min_conf)
        self.position = PositionEstimator(settings, keypoint_min_conf)
        self.pulse = PulseEstimator(settings, keypoint_min_conf)
        self.camera = CameraMotionEstimator(settings)
        self.camera_state = CameraMotionState() if settings.moving_camera else None
        self.states: dict[str, _TrackSigns] = {}
        self.command_t: float | None = None

    def prepare(self) -> None:
        """Load the eye model before the first frame, so a download never stalls the live loop."""
        if self.settings.eyes:
            self.eyes.scorer.prepare()

    @property
    def camera_motion(self) -> str:
        """``fixed`` unless signs.moving_camera; else still / steady / moving / unknown."""
        return self.camera_state.state if self.camera_state is not None else "fixed"

    def mark_command(self, t: float) -> None:
        """The operator just gave a spoken command ("raise your hand if you hear me")."""
        self.command_t = t
        for state in self.states.values():
            self.motion.mark_command(state.motion, t)

    def update(self, image, tracks: Sequence, t: float) -> None:
        live = {track.track_id for track in tracks}
        for track_id in list(self.states):
            if track_id not in live:
                del self.states[track_id]
        camera, roll, shift = "fixed", 0.0, None
        if self.camera_state is not None:
            camera = self.camera.update(self.camera_state, image,
                                        [track.bbox for track in tracks if track.detected], t)
            roll, shift = self.camera_state.roll_deg, self.camera_state.last_shift_px
        for track in tracks:
            if not track.detected:
                continue
            state = self.states.setdefault(track.track_id, _TrackSigns())
            sample = self.motion.sample(track.bbox, track.keypoints, track.keypoint_conf, t, camera, roll)
            reference = next((s for s in reversed(state.motion.history)
                              if t - s.t >= self.settings.motion_lag_s), None)
            self.motion.update(state.motion, sample)
            if self.settings.history:
                self.history.update(state.history, sample, reference, track.posture.label,
                                    getattr(track, "ground_px", None), track.bbox)
            still = self.motion.movement(state.motion, t)["value"] == "still"
            if self.settings.breathing:
                # Chest motion is only separable from camera motion on a still camera.
                self.breathing.update(state.breathing, image, track.keypoints, track.keypoint_conf,
                                      still and camera in IMAGE_FRAME, t, shift)
            if self.settings.pulse:
                self.pulse.update(state.pulse, image, track.keypoints, track.keypoint_conf,
                                  still and camera in IMAGE_FRAME, t)
            if self.settings.eyes and self.eyes.due(state.eyes, t):
                self.eyes.update(state.eyes, image, track.keypoints, track.keypoint_conf, t)
            if self.settings.blood and self.blood.due(state.blood, t):
                self.blood.update(state.blood, image, track.bbox, track.keypoints, track.keypoint_conf, t)
            if self.settings.history:
                self.position.update_face(state.position, track, (image.shape[1], image.shape[0]), t)
                if t - state.history.last_change_check_t >= 0.25:
                    self.history.track_changes(state.history, self._values(track, state, t), t)
        if self.settings.history:
            self.position.update_nearby({k: s.position for k, s in self.states.items()}, tracks, t)

    def _values(self, track, state: _TrackSigns, t: float) -> dict[str, str]:
        values = {"posture": track.posture.label,
                  "movement": self.motion.movement(state.motion, t)["value"],
                  "response": self.motion.response(state.motion, t)["value"]}
        if self.settings.eyes:
            values["eyes"] = self.eyes.value(state.eyes, t)["value"]
        if self.settings.breathing:
            values["breathing"] = self.breathing.value(state.breathing, t)["value"]
        if self.settings.blood:
            values["visible_blood"] = self.blood.value(state.blood, t)["value"]
        return values

    def snapshot(self, track, t: float) -> dict | None:
        state = self.states.get(track.track_id)
        if state is None:
            return None
        signs = {
            "schema": SCHEMA,
            "window_s": self.settings.window_s,
            "posture": {"value": track.posture.label, "confidence": round(track.posture.confidence, 2)},
            "movement": self.motion.movement(state.motion, t),
            "response": self.motion.response(state.motion, t),
        }
        if state.motion.command_t is not None:
            signs["response"]["command_at"] = self.iso(state.motion.command_t)
        if self.settings.eyes:
            signs["eyes"] = self.eyes.value(state.eyes, t)
        if self.settings.breathing:
            signs["breathing"] = self.breathing.value(state.breathing, t)
        if self.settings.blood:
            signs["visible_blood"] = self.blood.value(state.blood, t)
        if self.settings.pulse:
            signs["pulse"] = self.pulse.value(state.pulse, t)
        if self.settings.history:
            signs["fall"] = self.history.fall(state.history, t, self.iso)
            signs["walking"] = self.history.walking(state.history, t, self.iso)
            signs["limbs"] = self.history.limbs(state.history, t)
            signs["waving"] = self.history.waving(state.history, t, self.iso)
            signs["face_down"] = self.position.face_down(state.position, t)
            signs["person_nearby"] = self.position.person_nearby(state.position, t)
            if self.settings.blood:
                signs["blood_spread"] = self.blood.spread(state.blood, t,
                                                          signs["visible_blood"]["value"] == "detected")
            signs["changes"] = self.history.changes(state.history, t, self.iso)
        if self.camera_state is not None:
            signs["camera_motion"] = self.camera.value(self.camera_state)
            camera = self.camera_state.state
            paused = ["breathing"] if camera not in IMAGE_FRAME else []
            if camera == "moving":
                paused += ["movement", "response"]
            for name in paused:
                if name in signs and signs[name]["value"] in ("not_testable", "not_tested"):
                    signs[name] = {**signs[name], "reason": "camera_moving"}
        return signs

    def display_context(self, track_id: str, t: float) -> dict:
        """Display-only detail for the responder view (cv/monitor.py); never sent."""
        state = self.states.get(track_id)
        if state is None:
            return {}
        context: dict = {"command_window_s": self.settings.command_window_s}
        if state.motion.response == "pending" and state.motion.command_t is not None:
            context["listening_left_s"] = max(0.0, self.settings.command_window_s
                                              - (t - state.motion.command_t))
        breathing = state.breathing
        if breathing.started_t is not None:
            needed = self.settings.breathing_window_s * 0.6
            context["breathing_remaining_s"] = max(0.0, needed - (t - breathing.started_t))
            context["breathing_needed_s"] = needed
            context["breathing_trace"] = chest_signal(breathing.times, breathing.displacement)
        if self.settings.pulse:
            context["pulse_measured_s"] = self.pulse.measured_s(state.pulse)
            context["pulse_needed_s"] = self.settings.pulse_min_s
        history = state.history
        if history.fall_t is not None:
            context["fall_ago_s"] = t - history.fall_t
        if history.walk_t is not None:
            context["walked_ago_s"] = t - history.walk_t
        if history.wave_t is not None:
            context["waved_ago_s"] = t - history.wave_t
        context["changes_ago_s"] = [t - at for *_, at in history.changes
                                    if t - at <= self.settings.change_keep_s]
        return context

    def listening(self, track_id: str) -> bool:
        state = self.states.get(track_id)
        return state is not None and state.motion.response == "pending"


def describe(signs: dict | None, listening: bool = False) -> list[str]:
    """Short overlay lines for one track's signs."""
    if not signs:
        return []
    def text(sign: dict) -> str:
        value = sign["value"].replace("_", " ")
        return f"{value} (camera moves)" if sign.get("reason") == "camera_moving" else value

    movement = signs["movement"]
    move = f"still {movement['still_for_s']:.0f}s" if movement["value"] == "still" else text(movement)
    response = "listening..." if listening else text(signs["response"])
    lines = [f"{move} | response: {response}"]
    parts = []
    if "eyes" in signs:
        parts.append(f"eyes {text(signs['eyes'])}")
    if "breathing" in signs:
        breathing = signs["breathing"]
        parts.append(f"breath {breathing['rate_bpm']:.0f}/min" if breathing.get("rate_bpm")
                     else f"breath {text(breathing)}")
    if parts:
        lines.append(" | ".join(parts))
    blood = signs.get("visible_blood")
    if blood:
        lines.append("blood: " + (", ".join(blood["regions"]) if blood["value"] == "detected"
                                  else blood["value"].replace("_", " ")))
    return lines
