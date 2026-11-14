import math
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from cv.clock import Timebase
from cv.main import resolve_mapping, room_for_key
from cv.scene_map import check_area, describe as describe_area
from cv.settings import CONFIG_DIR, Settings, MappingSettings, load_settings
from cv.signs import SignsEstimator, describe
from cv.signs.body import L_ELBOW, L_WRIST
from cv.signs.breathing import BreathingEstimator, BreathingState
from cv.signs.camera_motion import CameraMotionEstimator, CameraMotionState
from cv.signs.motion import MotionEstimator, MotionState
from cv.tests.helpers import det, standing_box
from cv.tests.test_scene_map import demo_map, square
from cv.tests.test_signs import BBOX, CONF, KEYPOINTS, SETTINGS, FakeScorer, make_track, textured
from cv.tests.test_zones import HALVES, zone_tracker
from cv.zones import parse_zones

MOVING = replace(SETTINGS, moving_camera=True)


def shifted(image, dx, dy=0.0, angle_deg=0.0):
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle_deg, 1.0)
    matrix[:, 2] += (dx, dy)
    return cv2.warpAffine(image, matrix, (width, height), borderMode=cv2.BORDER_REFLECT)


def camera_state_for(frames, boxes=(), fps=30):
    estimator, state = CameraMotionEstimator(MOVING), CameraMotionState()
    for i, frame in enumerate(frames):
        estimator.update(state, frame, list(boxes), i / fps)
    return state


@pytest.mark.parametrize("px_per_frame, expected", [(0.0, "still"), (1.0, "steady"), (4.0, "moving")])
def test_camera_state_from_the_background_speed(px_per_frame, expected):
    base = textured()   # 640 px wide: 1 px/frame at 30 FPS = 60 px/s at 1280 width
    state = camera_state_for([shifted(base, px_per_frame * i) for i in range(30)])
    assert state.state == expected


def test_a_moving_person_does_not_count_as_camera_motion():
    base, rng = textured(), np.random.default_rng(3)
    frames = []
    for _ in range(30):
        frame = base.copy()
        frame[70:470, 240:400] = rng.integers(0, 255, (400, 160, 3), dtype=np.uint8)
        frames.append(frame)
    assert camera_state_for(frames, boxes=[BBOX]).state == "still"


def test_a_frame_without_background_texture_is_unknown_and_roll_adds_up():
    flat = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert camera_state_for([flat] * 10).state == "unknown"
    base = textured()
    state = camera_state_for([shifted(base, 0, 0, 0.2 * i) for i in range(30)])
    assert abs(state.roll_deg) == pytest.approx(0.2 * 29, abs=1.0)


def swayed(keypoints, t):
    """The same pose seen by a swaying camera: rotated and shifted in the image."""
    angle = math.radians(4 * math.sin(2 * math.pi * 0.5 * t))
    rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
    shift = np.array([25 * math.sin(2 * math.pi * 0.7 * t), 15 * math.cos(2 * math.pi * 0.4 * t)])
    return (keypoints - (320, 240)) @ rotation.T + (320, 240) + shift, math.degrees(angle)


def run_swaying(estimator, state, start, stop, keypoints=KEYPOINTS, camera="steady"):
    for i in range(int(start * 10), int(stop * 10)):
        t = i / 10
        points, roll = (keypoints, 0.0) if camera == "still" else swayed(keypoints, t)
        estimator.update(state, estimator.sample(BBOX, points, CONF, t, camera, roll))


def test_camera_sway_is_not_movement_relative_to_the_body():
    estimator = MotionEstimator(SETTINGS)
    steady, fixed = MotionState(), MotionState()
    run_swaying(estimator, steady, 0, 4)
    run_swaying(estimator, fixed, 0, 4, camera="fixed")
    result = estimator.movement(steady, 3.9)
    assert result["value"] == "still" and result["confidence"] <= 0.6
    assert estimator.movement(fixed, 3.9)["value"] == "moving"    # image positions would be fooled


def test_a_raised_hand_is_a_response_under_camera_sway():
    estimator, state = MotionEstimator(SETTINGS), MotionState()
    run_swaying(estimator, state, 0, 2)
    estimator.mark_command(state, 2.0)
    raised = KEYPOINTS.copy()
    raised[[L_ELBOW, L_WRIST]] -= (0, 70)
    run_swaying(estimator, state, 2.1, 3.0, raised)
    assert estimator.response(state, 3.0)["value"] == "responded"


def test_a_moving_camera_pauses_movement_and_response():
    estimator, state = MotionEstimator(SETTINGS), MotionState()
    run_swaying(estimator, state, 0, 3, camera="still")
    assert estimator.movement(state, 2.9)["still_for_s"] >= 2.0
    estimator.mark_command(state, 3.0)
    run_swaying(estimator, state, 3.0, 12.0, camera="moving")
    assert estimator.movement(state, 12.0)["value"] == "not_testable"
    assert estimator.response(state, 12.0)["value"] == "not_tested"     # not watched: no "no_response"
    run_swaying(estimator, state, 12.0, 15.0, camera="still")
    assert estimator.movement(state, 14.9)["still_for_s"] <= 3.0        # restarts after the move, not 14.9


def test_breathing_subtracts_the_camera_shift():
    def run(with_shift):
        estimator, state, base = BreathingEstimator(SETTINGS), BreathingState(), textured()
        previous = 0.0
        for i in range(301):
            t = i / 20
            sway = 2.0 * math.sin(2 * math.pi * 15 / 60 * t)   # camera sway at a breathing rate
            estimator.update(state, shifted(base, 0, sway), KEYPOINTS, CONF, True, t,
                             (0.0, sway - previous) if with_shift else None)
            previous = sway
        return estimator.value(state, 15.0)["value"]

    assert run(with_shift=False) == "motion_detected"     # sway alone would look like breathing
    assert run(with_shift=True) == "no_motion_detected"


def test_signs_say_when_the_camera_moves():
    timebase = Timebase(monotonic=lambda: 0.0, wall=lambda: datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc))
    estimator = SignsEstimator(MOVING, timebase.iso, face_scorer=FakeScorer(0.8))
    base, track = textured(), make_track()
    for i in range(40):
        estimator.update(shifted(base, 8.0 * i), [track], i / 10)
    signs = estimator.snapshot(track, 3.9)
    assert estimator.camera_motion == "moving" and signs["camera_motion"]["value"] == "moving"
    assert signs["movement"] == {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"}
    assert signs["breathing"]["reason"] == "camera_moving"
    assert signs["eyes"]["value"] == "closed"                 # judged per frame: still works
    assert "not testable (camera moves)" in describe(signs)[0]
    fixed = SignsEstimator(SETTINGS, timebase.iso, face_scorer=FakeScorer(0.8))
    fixed.update(base, [track], 0.0)
    assert fixed.camera_motion == "fixed" and "camera_motion" not in fixed.snapshot(track, 0.0)


def test_the_current_room_applies_to_everyone_at_once():
    tracker = zone_tracker()
    tracker.update([det(1, standing_box(200, 900))], 0.0)
    assert tracker.tracks["s1-t1"].zone_id == "left"          # image polygons by default
    tracker.set_current_zone("right")
    tracker.update([det(1, standing_box(200, 900)), det(2, standing_box(700, 900))], 0.1)
    assert {t.zone_id for t in tracker.tracks.values()} == {"right"}
    assert tracker.tracks["s1-t1"].published_xy == (6.0, 2.0)
    with pytest.raises(ValueError):
        tracker.set_current_zone("kitchen")


def test_number_keys_pick_rooms_in_config_order():
    zones = parse_zones(HALVES, "demo-floor-1")
    assert room_for_key(ord("1"), zones) == "left" and room_for_key(ord("2"), zones) == "right"
    assert room_for_key(ord("3"), zones) is None and room_for_key(ord("c"), zones) is None


@pytest.mark.parametrize("name", ["rooms.yaml", "bodycam.yaml", "demo.yaml"])
def test_room_configs_load_and_every_room_is_walkable(name):
    settings = load_settings(CONFIG_DIR / name)
    assert settings.mapping.zone_select == "keys"
    _, zones = resolve_mapping(settings, SimpleNamespace(homography=None), None)
    assert [z.zone_id for z in zones.zones] == ["room-1", "room-2", "room-3"]
    for zone in zones.zones:
        check = check_area(demo_map(), square(zone.x_m - 0.2, zone.y_m - 0.2, 0.4))
        assert check.walkable, describe_area(check)
    assert settings.signs.moving_camera == (name == "bodycam.yaml")
    if name == "demo.yaml":
        assert settings.signs.enabled and settings.tracking.lost_after_s >= 300
        assert settings.publisher.camera_id != load_settings(CONFIG_DIR / "bodycam.yaml").publisher.camera_id


def test_keys_room_selection_needs_zones():
    settings = Settings(mapping=MappingSettings(zone_select="keys"))
    with pytest.raises(ValueError, match="zone_select"):
        resolve_mapping(settings, SimpleNamespace(homography=None), None)
