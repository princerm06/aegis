import math
import re
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from cv.clock import Timebase
from cv.observations import PostureEstimate
from cv.publisher import Publisher
from cv.settings import PublisherSettings, SignsSettings
from cv.signs import SCHEMA, SignsEstimator, describe
from cv.signs.blood import BloodEstimator, BloodState
from cv.signs.body import L_ELBOW, L_EYE, L_WRIST, NOSE, R_EYE, region_masks
from cv.signs.breathing import BreathingEstimator, BreathingState, estimate_rate, torso_roi
from cv.signs.eyes import CROP_PX, EyeEstimator, EyeState, combine_blinks, face_crop, head_yaw_deg
from cv.signs.motion import MotionEstimator, MotionState
from cv.tests.test_publisher import FakeClock, FakeTransport, report

SETTINGS = SignsSettings(enabled=True)
BBOX = (240.0, 70.0, 400.0, 470.0)
# Standing person facing the camera (COCO-17 order); torso length 140 px.
KEYPOINTS = np.array([
    (320, 100), (330, 90), (310, 90), (340, 95), (300, 95),        # nose, eyes, ears
    (360, 150), (280, 150), (380, 220), (260, 220), (390, 280), (250, 280),  # arms
    (350, 290), (290, 290), (350, 380), (290, 380), (350, 460), (290, 460),  # legs
], dtype=float)
CONF = np.full(17, 0.9)


class FakeScorer:
    available = True

    def __init__(self, score):
        self.score = score

    def closed_score(self, crop):
        return self.score


def run_motion(estimator, state, frames):
    for t, keypoints in frames:
        estimator.update(state, estimator.sample(BBOX, keypoints, CONF, t))


def test_still_person_reports_still_for_and_a_raised_hand_is_movement():
    estimator, state = MotionEstimator(SETTINGS), MotionState()
    run_motion(estimator, state, [(i / 10, KEYPOINTS) for i in range(31)])
    still = estimator.movement(state, 3.0)
    assert still["value"] == "still" and still["still_for_s"] >= 2.0
    raised = KEYPOINTS.copy()
    raised[[L_ELBOW, L_WRIST]] -= (0, 90)          # one arm only: 2 of 8 limb points
    run_motion(estimator, state, [(3.0 + i / 10, raised) for i in range(1, 12)])
    assert estimator.movement(state, 4.1)["value"] == "moving"


def test_jitter_is_not_movement():
    estimator, state = MotionEstimator(SETTINGS), MotionState()
    rng = np.random.default_rng(0)
    run_motion(estimator, state, [(i / 10, KEYPOINTS + rng.normal(0, 2.0, KEYPOINTS.shape))
                                  for i in range(40)])
    assert estimator.movement(state, 3.9)["value"] == "still"


def test_response_to_command_from_one_raised_hand():
    estimator, state = MotionEstimator(SETTINGS), MotionState()
    run_motion(estimator, state, [(i / 10, KEYPOINTS) for i in range(20)])
    estimator.mark_command(state, 2.0)
    assert estimator.response(state, 2.5)["value"] == "not_tested"      # still listening
    raised = KEYPOINTS.copy()
    raised[[L_ELBOW, L_WRIST]] -= (0, 70)          # 70 / 280 px body scale = 0.25
    run_motion(estimator, state, [(2.0 + i / 10, raised) for i in range(1, 10)])
    result = estimator.response(state, 3.0)
    assert result["value"] == "responded" and result["confidence"] >= 0.6


def test_no_response_only_when_the_person_was_watched():
    estimator, state = MotionEstimator(SETTINGS), MotionState()
    run_motion(estimator, state, [(i / 10, KEYPOINTS) for i in range(20)])
    estimator.mark_command(state, 2.0)
    run_motion(estimator, state, [(2.0 + i / 10, KEYPOINTS) for i in range(1, 90)])
    assert estimator.response(state, 11.0)["value"] == "no_response"

    unseen = MotionState()
    run_motion(estimator, unseen, [(i / 10, KEYPOINTS) for i in range(20)])
    estimator.mark_command(unseen, 2.0)                       # then the person left view
    assert estimator.response(unseen, 11.0)["value"] == "not_tested"


def test_region_masks_cover_the_body_and_do_not_overlap():
    (x0, y0), masks = region_masks(BBOX, KEYPOINTS, CONF, image_size=(640, 480))
    assert (x0, y0) == (240, 70)
    assert set(masks) == {"head", "torso", "left_arm", "right_arm", "left_leg", "right_leg"}
    assert not (masks["torso"] & masks["left_leg"]).any()
    assert masks["torso"][220 - y0, 320 - x0]                  # torso centre
    assert not masks["head"][220 - y0, 320 - x0]


def body_image(red_box=None, color=(0, 0, 200)):
    image = np.full((480, 640, 3), 128, np.uint8)
    if red_box is not None:
        x1, y1, x2, y2 = red_box
        image[y1:y2, x1:x2] = color
    return image


def blood_value(image, checks=3):
    estimator, state = BloodEstimator(SETTINGS), BloodState()
    for i in range(checks):
        estimator.update(state, image, BBOX, KEYPOINTS, CONF, i * 0.4)
    return estimator.value(state, (checks - 1) * 0.4)


def test_fake_blood_on_the_torso_is_detected_in_that_region():
    result = blood_value(body_image((300, 200, 340, 250)))
    assert result["value"] == "detected" and result["regions"] == ["torso"]
    assert result["method"] == "color"


def test_no_red_and_skin_tones_are_not_blood():
    assert blood_value(body_image())["value"] == "not_detected"
    skin = body_image((300, 200, 340, 250), color=(140, 170, 220))   # BGR, low saturation
    assert blood_value(skin)["value"] == "not_detected"
    # Measured: webcam skin in dim warm light, HSV saturation ~145 but G/R ~ B/R ~ 0.46.
    dim_skin = body_image((300, 200, 340, 250), color=(38, 38, 83))
    assert blood_value(dim_skin)["value"] == "not_detected"


def test_pink_clothing_is_not_blood():
    for bgr in ((60, 10, 180), (147, 20, 255), (120, 0, 200)):     # deep pink, hot pink, magenta
        assert blood_value(body_image((300, 200, 340, 250), color=bgr))["value"] == "not_detected"


def test_dark_stage_blood_is_detected():
    result = blood_value(body_image((300, 200, 340, 250), color=(12, 10, 95)))
    assert result["value"] == "detected"


def test_blood_needs_two_checks():
    assert blood_value(body_image((300, 200, 340, 250)), checks=1)["value"] == "not_testable"


def textured(seed=1):
    rng = np.random.default_rng(seed)
    noise = rng.integers(0, 255, (480, 640), dtype=np.uint8)
    return cv2.cvtColor(cv2.GaussianBlur(noise, (0, 0), 3), cv2.COLOR_GRAY2BGR)


def run_breathing(bpm, amplitude_px, seconds=15.0, fps=20):
    estimator, state = BreathingEstimator(SETTINGS), BreathingState()
    base = textured()
    for i in range(int(seconds * fps) + 1):
        t = i / fps
        dy = amplitude_px * math.sin(2 * math.pi * bpm / 60 * t)
        frame = cv2.warpAffine(base, np.float32([[1, 0, 0], [0, 1, dy]]), (640, 480))
        estimator.update(state, frame, KEYPOINTS, CONF, still=True, t=t)
    return estimator, state, seconds


def test_breathing_rate_from_torso_motion():
    estimator, state, t = run_breathing(bpm=15, amplitude_px=2.0)
    result = estimator.value(state, t)
    assert result["value"] == "motion_detected"
    assert result["rate_bpm"] == pytest.approx(15, abs=1.5)


def test_no_chest_motion_is_weak_evidence():
    estimator, state, t = run_breathing(bpm=15, amplitude_px=0.0)
    result = estimator.value(state, t)
    assert result["value"] == "no_motion_detected" and result["confidence"] < 0.5


def test_chest_roi_without_hips_extends_away_from_the_head():
    conf = CONF.copy()
    conf[[11, 12]] = 0.1                                     # hips hidden
    x1, y1, x2, y2 = torso_roi(KEYPOINTS, conf, (640, 480), 0.5)
    assert y1 < 150 < y2 and y2 > 190                        # below the shoulders (y=150)
    lying = KEYPOINTS.copy()                                 # head to the left of the shoulders
    lying[NOSE], lying[5], lying[6] = (100, 240), (160, 200), (160, 280)
    x1, y1, x2, y2 = torso_roi(lying, conf, (640, 480), 0.5)
    assert x1 < 160 < x2 and x2 > 200


def test_breathing_needs_a_still_window():
    estimator, state = BreathingEstimator(SETTINGS), BreathingState()
    image = textured()
    for i in range(100):
        estimator.update(state, image, KEYPOINTS, CONF, still=True, t=i / 20)
    assert estimator.value(state, 5.0)["value"] == "not_testable"      # 5 s < 60% of 15 s
    estimator.update(state, image, KEYPOINTS, CONF, still=False, t=5.05)
    assert state.roi is None and state.times == []


def test_estimate_rate_rejects_drift_and_jitter():
    times = np.arange(0, 15, 0.05)
    false_calls = 0
    for seed in range(100):
        rng = np.random.default_rng(seed)
        drift = np.cumsum(rng.normal(0, 0.05, (len(times), 2)), axis=0)
        jitter = rng.normal(0, 0.3, (len(times), 2))
        false_calls += sum(estimate_rate(times, series, SETTINGS)["value"] == "motion_detected"
                           for series in (drift, jitter, drift + jitter))
    assert false_calls <= 1                       # of 300 noise-only windows


@pytest.mark.parametrize("seed", range(10))
def test_estimate_rate_finds_breathing_under_noise(seed):
    rng = np.random.default_rng(seed)
    times = np.arange(0, 15, 0.05)
    chest = 1.5 * np.sin(2 * np.pi * 20 / 60 * times)
    series = np.column_stack([np.zeros_like(times), chest]) + rng.normal(0, 0.15, (len(times), 2))
    series += np.cumsum(rng.normal(0, 0.02, (len(times), 2)), axis=0)
    result = estimate_rate(times, series, SETTINGS)
    assert result["value"] == "motion_detected"
    assert result["rate_bpm"] == pytest.approx(20, abs=1.5)


def test_face_crop_levels_a_sideways_face():
    image = np.zeros((480, 640, 3), np.uint8)
    keypoints = KEYPOINTS.copy()
    # Lying on the side: eye line vertical, subject's left eye below the right.
    keypoints[NOSE], keypoints[L_EYE], keypoints[R_EYE] = (300, 200), (290, 215), (290, 185)
    for point in (keypoints[L_EYE], keypoints[R_EYE]):
        cv2.circle(image, tuple(point.astype(int)), 3, (255, 255, 255), -1)
    crop = face_crop(image, keypoints, CONF, 0.5, min_face_px=20)
    assert crop.shape == (CROP_PX, CROP_PX, 3)
    count, _, _, centers = cv2.connectedComponentsWithStats((crop[..., 0] > 128).astype(np.uint8))
    blobs = sorted(centers[1:], key=lambda c: c[0])
    assert count - 1 == 2
    assert abs(blobs[0][1] - blobs[1][1]) < 3                   # level
    assert face_crop(image, keypoints, CONF, 0.5, min_face_px=200) is None   # too small


def eyes_value(score):
    estimator, state = EyeEstimator(SETTINGS, FakeScorer(score)), EyeState()
    image = textured()
    for i in range(4):
        estimator.update(state, image, KEYPOINTS, CONF, i * 0.4)
    return estimator.value(state, 1.2)


def test_turned_heads_use_the_higher_eye_score_and_profiles_are_not_testable():
    assert combine_blinks(0.6, 0.5, yaw_deg=5, turned_deg=25, max_yaw_deg=60) == pytest.approx(0.55)
    # Measured on a sleeping profile: visible eye 0.59, hidden eye 0.17 (mean 0.38 read "open").
    assert combine_blinks(0.59, 0.17, yaw_deg=-50, turned_deg=25, max_yaw_deg=60) == 0.59
    assert combine_blinks(0.59, 0.17, yaw_deg=75, turned_deg=25, max_yaw_deg=60) is None
    assert head_yaw_deg(np.eye(4)) == pytest.approx(0.0)


def test_small_head_wound_counts_with_the_head_threshold():
    estimator = BloodEstimator(SETTINGS)
    assert estimator._min_fraction("head") == SETTINGS.blood_min_fraction_head < SETTINGS.blood_min_fraction
    assert estimator._min_fraction("torso") == SETTINGS.blood_min_fraction


def test_eye_state_from_blink_scores():
    assert eyes_value(0.9)["value"] == "closed"
    assert eyes_value(0.1)["value"] == "open"
    assert eyes_value(None)["value"] == "not_testable"


def make_estimator():
    timebase = Timebase(monotonic=lambda: 0.0, wall=lambda: datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc))
    return SignsEstimator(SETTINGS, timebase.iso, face_scorer=FakeScorer(0.8))


def make_track(keypoints=KEYPOINTS):
    return SimpleNamespace(track_id="s1-t1", detected=True, bbox=BBOX, keypoints=keypoints,
                           keypoint_conf=CONF, posture=PostureEstimate("lying", 0.82, "keypoints"))


def test_snapshot_has_every_sign_and_no_clinical_fields():
    estimator = make_estimator()
    image = body_image((300, 200, 340, 250))
    track = make_track()
    for i in range(60):
        estimator.update(image, [track], i / 10)
    estimator.mark_command(6.0)
    signs = estimator.snapshot(track, 6.0)
    assert signs["schema"] == SCHEMA
    assert set(signs) == {"schema", "window_s", "posture", "movement", "response", "eyes",
                          "breathing", "visible_blood", "fall", "walking", "limbs", "waving", "face_down",
                          "person_nearby", "blood_spread", "changes", "pulse"}
    assert signs["fall"]["value"] == "not_seen" and signs["walking"]["value"] == "not_seen"
    assert signs["posture"] == {"value": "lying", "confidence": 0.82}
    assert signs["movement"]["value"] == "still"
    assert signs["response"]["command_at"].startswith("2026-11-14T05:00:06")
    assert signs["eyes"]["value"] == "closed"
    assert signs["visible_blood"]["regions"] == ["torso"]
    assert not re.search(r"severity|injury|diagnos|triage|priority|heart", str(signs), re.I)
    assert describe(signs, estimator.listening("s1-t1"))[0].endswith("listening...")


def test_tracks_that_leave_are_forgotten():
    estimator = make_estimator()
    estimator.update(textured(), [make_track()], 0.0)
    estimator.update(textured(), [], 0.1)
    assert estimator.states == {}


def signs_publisher(send_signs, send_history=False):
    clock = FakeClock()
    timebase = Timebase(monotonic=clock, wall=lambda: datetime(2026, 11, 14, 5, 0, tzinfo=timezone.utc))
    transport = FakeTransport()
    publisher = Publisher(PublisherSettings(send_signs=send_signs, send_history=send_history),
                          frame_id="demo-floor-1",
                          calibration_ref="cv-demo-floor-1-abc", session_id="s1", timebase=timebase,
                          transport=transport, clock=clock)
    return publisher, transport, clock


SIGNS = {"schema": SCHEMA, "movement": {"value": "still", "confidence": 0.9, "still_for_s": 4.0},
         "eyes": {"value": "closed", "confidence": 0.7}}


def test_signs_are_sent_only_when_enabled_and_visible():
    publisher, transport, clock = signs_publisher(send_signs=True)
    publisher.submit([replace(report(100.0), signs=SIGNS)])
    publisher.step()
    clock.t = 100.6
    publisher.submit([replace(report(100.6, state="occluded", last_seen=100.0), signs=SIGNS)])
    publisher.step()
    visible, occluded = transport.posts()
    assert visible["signs"] == SIGNS and "signs" not in occluded

    publisher, transport, _ = signs_publisher(send_signs=False)
    publisher.submit([replace(report(100.0), signs=SIGNS)])
    publisher.step()
    assert "signs" not in transport.posts()[0]


def test_history_signs_stay_off_the_wire_until_the_backend_accepts_them():
    history = {**SIGNS, "fall": {"value": "seen", "confidence": 0.8, "at": "2026-11-14T05:00:01.000Z"},
               "changes": [{"sign": "eyes", "from": "open", "to": "closed", "at": "2026-11-14T05:00:02.000Z"}]}
    publisher, transport, _ = signs_publisher(send_signs=True)
    publisher.submit([replace(report(100.0), signs=history)])
    publisher.step()
    assert transport.posts()[0]["signs"] == SIGNS                  # the current backend rejects unknown keys
    publisher, transport, _ = signs_publisher(send_signs=True, send_history=True)
    publisher.submit([replace(report(100.0), signs=history)])
    publisher.step()
    assert transport.posts()[0]["signs"] == history


def test_a_changed_sign_value_is_sent_before_the_heartbeat():
    publisher, _, clock = signs_publisher(send_signs=True)
    assert publisher.submit([replace(report(100.0), signs=SIGNS)]) == 1
    clock.t = 100.2
    timer_only = {**SIGNS, "movement": {**SIGNS["movement"], "still_for_s": 4.2}}
    assert publisher.submit([replace(report(100.2), signs=timer_only)]) == 0
    opened = {**SIGNS, "eyes": {"value": "open", "confidence": 0.7}}
    assert publisher.submit([replace(report(100.2), signs=opened)]) == 1


def test_an_unreachable_eye_model_download_disables_eyes_instead_of_hanging(tmp_path, monkeypatch):
    import urllib.request

    from cv.signs.eyes import FaceScorer

    calls = []

    def offline(url, timeout=None):
        calls.append(timeout)
        raise TimeoutError("no internet on this hotspot")

    monkeypatch.setattr(urllib.request, "urlopen", offline)
    scorer = FaceScorer(tmp_path / "face_landmarker.task")
    assert scorer.prepare() is False and not scorer.available
    assert calls and calls[0] is not None                  # the download always has a timeout
    assert scorer.closed_score(np.zeros((64, 64, 3), np.uint8)) is None
    assert len(calls) == 1                                 # and is not retried on every frame
