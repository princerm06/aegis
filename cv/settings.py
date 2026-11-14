"""Typed pipeline settings, loaded from ``cv/config/pipeline.yaml``.

Unknown keys are rejected so a typo cannot silently fall back to a default.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import Any

CV_DIR = Path(__file__).resolve().parent
CONFIG_DIR = CV_DIR / "config"
MODELS_DIR = CV_DIR / "models"
DEFAULT_CONFIG = CONFIG_DIR / "pipeline.yaml"


@dataclass(frozen=True)
class CameraSettings:
    index: int = 0
    name: str = ""                  # if set, the index is looked up by this name whenever the camera opens
    width: int = 1280
    height: int = 720
    fps: int = 30
    rotate: int = 0                 # 0 / 90 / 180 / 270 clockwise, e.g. 180 for a camera hung upside down
    reconnect_after_s: float = 2.0
    retry_interval_s: float = 1.0


@dataclass(frozen=True)
class DetectorSettings:
    model: str = "yolo11n-pose.pt"
    conf: float = 0.25
    imgsz: int = 640
    device: str = "auto"
    tracker: str = "bytetrack.yaml"


@dataclass(frozen=True)
class MappingSettings:
    mode: str = "calibrated"            # calibrated (homography) | zones (predefined zones)
    homography: str = "homography.json"
    frame_id: str = "demo-floor-1"
    zones: tuple = ()                   # used when mode is zones; see cv/zones.py
    zone_select: str = "image"          # image (zone polygons) | keys (current room, keys 1-9)


@dataclass(frozen=True)
class TrackingSettings:
    occlusion_grace_s: float = 0.5
    stale_after_s: float = 2.0
    lost_after_s: float = 30.0
    position_alpha: float = 0.35
    confidence_alpha: float = 0.3
    deadband_m: float = 0.15
    posture_window: int = 15
    keypoint_min_conf: float = 0.5
    lying_max_torso_m: float = 0.75
    reattach: bool = True
    reattach_radius_m: float = 0.6
    reattach_radius_frac: float = 0.08  # uncalibrated: image distance / frame diagonal
    reattach_body_frac: float = 0.6     # uncalibrated: or this share of the person's box, if larger
    zone_switch_frames: int = 5         # zones mode: frames in a new zone before switching


@dataclass(frozen=True)
class SignsSettings:
    """Observed signs (cv/signs/). Observations with confidence, never a diagnosis."""

    enabled: bool = False               # compute and show signs (sending: publisher.send_signs)
    window_s: float = 10.0              # smoothing window reported as signs.window_s
    moving_camera: bool = False         # bodycam / handheld: measure camera motion (signs/camera_motion.py)
    camera_window_s: float = 1.0
    camera_still_px_s: float = 10.0     # background px/s at 1280 width; fixed cameras measured 2-10
    camera_steady_px_s: float = 80.0    # handheld stock clips 10-60; a walking camera goes past 100
    motion_lag_s: float = 1.0           # keypoints compared with this long ago
    move_threshold: float = 0.06        # displacement / body size that counts as movement
    still_after_s: float = 2.0          # no movement for this long = still
    command_window_s: float = 8.0       # time to respond after a marked command
    response_threshold: float = 0.15    # limb displacement / body size that counts as a response
    eyes: bool = True
    eyes_model: str = "face_landmarker.task"
    eyes_hz: float = 3.0
    eyes_window_s: float = 3.0
    eyes_closed_threshold: float = 0.4  # CEW: 95% accuracy (0.5 missed 15% of closed eyes)
    eyes_min_face_px: int = 36
    eyes_turned_deg: float = 25.0       # head turned more: use the higher eye score
    eyes_max_yaw_deg: float = 60.0      # head turned more: not_testable
    blood: bool = True
    blood_hz: float = 3.0
    blood_min_fraction: float = 0.04    # red share of a body region to call it detected
    blood_min_fraction_head: float = 0.02  # head disc is mostly hair/background; wounds are small
    blood_min_pixels: int = 60
    blood_hue_max: int = 10             # red hue (OpenCV 0..180): <= this (orange side) ...
    blood_hue_min_wrap: int = 174       # ... or >= this (magenta side; pink is ~160-172)
    blood_max_gb_ratio: float = 0.35    # green and blue <= this x red; skin is ~0.45
    blood_val_min: int = 40             # minimum red channel (0..255)
    breathing: bool = True
    breathing_window_s: float = 15.0
    breathing_min_bpm: float = 6.0
    breathing_max_bpm: float = 40.0
    breathing_min_snr: float = 40.0     # velocity-spectrum peak over the rest of the band
    breathing_min_amplitude_px: float = 0.3
    pulse: bool = True                  # pulse estimate from skin colour (signs/pulse.py), display only
    pulse_window_s: float = 15.0
    pulse_min_s: float = 10.0           # still, face in view, this long before a first estimate
    pulse_min_eye_px: float = 14.0      # eye distance; smaller faces are not_testable
    pulse_min_snr_db: float = 3.0       # peak and harmonic over the rest of the 42-180 /min band
    blood_spread_window_s: float = 30.0  # red area now vs earlier in this window
    blood_spread_ratio: float = 1.3     # this much more (or 1/x less) red area = growing (shrinking)
    history: bool = True                # fall, walking, limbs, waving, changes (signs/history.py),
                                        # face down, person nearby (signs/position.py)
    fall_min_drop: float = 0.5          # head drop / upright body size ...
    fall_drop_s: float = 1.0            # ... within this long, then lying = fall seen
    fall_confirm_s: float = 2.5         # then lying this soon after the drop started (falls: 0.6-1.9 s; kneeling first: 3 s)
    walk_window_s: float = 4.0
    walk_min_distance: float = 0.8      # ground point travel / body size while standing
    limb_window_s: float = 30.0
    limb_move_threshold: float = 0.12   # hand or foot displacement / body size, relative to the torso
    change_hold_s: float = 3.0          # a new value must hold this long to count as a change
    change_keep_s: float = 300.0
    waving_window_s: float = 3.0        # 4 s caught a sleeping woman adjusting her pillow (stock clip)
    waving_min_swing: float = 0.2       # hand travel / body size between direction reversals
    waving_min_reversals: int = 4       # within the window: a calm wave is a bit over one sweep a second
    waving_arm_out: float = 0.2         # lying: hand this far from the shoulder / body size
    face_down_hold_s: float = 3.0
    face_down_shoulder_ratio: float = 0.45   # shoulder width / torso length; narrower = on the side
    nearby_radius_m: float = 1.5        # calibrated floor distance
    nearby_body_frac: float = 0.6       # uncalibrated: feet within this many body sizes of the box
    nearby_hold_s: float = 2.0


@dataclass(frozen=True)
class PublisherSettings:
    base_url: str = "http://127.0.0.1:8000"
    scenario_id: str = "demo"
    endpoint: str = "camera"
    camera_id: str = "logitech-1"
    actor: str = "cv-askar"
    rate_hz: float = 5.0
    heartbeat_s: float = 1.0
    timeout_s: float = 1.5
    max_backoff_s: float = 5.0
    mock_log: str = "artifacts/mock_{camera_id}.jsonl"
    send_signs: bool = False            # needs the backend change in cv/CONTRACT_PROPOSAL.md
    send_history: bool = True           # also the history and position signs (accepted since backend PR #12)


@dataclass(frozen=True)
class Settings:
    camera: CameraSettings = field(default_factory=CameraSettings)
    detector: DetectorSettings = field(default_factory=DetectorSettings)
    mapping: MappingSettings = field(default_factory=MappingSettings)
    tracking: TrackingSettings = field(default_factory=TrackingSettings)
    signs: SignsSettings = field(default_factory=SignsSettings)
    publisher: PublisherSettings = field(default_factory=PublisherSettings)


def _merge(instance: Any, data: Any, prefix: str) -> Any:
    if not isinstance(data, dict):
        raise ValueError(f"Setting {prefix.rstrip('.') or '<root>'} must be a mapping")
    known = {item.name for item in fields(instance)}
    updates = {}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"Unknown setting {prefix}{key}")
        current = getattr(instance, key)
        if is_dataclass(current):
            updates[key] = _merge(current, value, f"{prefix}{key}.")
        elif isinstance(current, bool) != isinstance(value, bool) or (
            isinstance(current, (int, float)) and not isinstance(value, (int, float))
        ):
            raise ValueError(f"Setting {prefix}{key} has the wrong type: {value!r}")
        else:
            updates[key] = type(current)(value) if isinstance(current, float) else value
    return replace(instance, **updates)


def load_settings(path: str | Path | None = DEFAULT_CONFIG) -> Settings:
    settings = Settings()
    if path is None:
        return settings
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    import yaml

    return _merge(settings, yaml.safe_load(path.read_text(encoding="utf-8")) or {}, "")


def resolve(path: str | Path, base: Path = CONFIG_DIR) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else base / candidate


def resolve_model(name: str) -> str:
    """Bare model names live in cv/models/ (downloaded on first use)."""
    candidate = Path(name)
    if candidate.exists() or candidate.parent != Path("."):
        return name
    return str(MODELS_DIR / name)
