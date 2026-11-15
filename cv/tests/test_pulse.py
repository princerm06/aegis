import math

import numpy as np
import pytest

from cv.settings import SignsSettings
from cv.signs.body import L_EYE, NOSE, R_EYE
from cv.signs.pulse import PulseEstimator, PulseState

SKIN_BGR = np.array([120.0, 150.0, 200.0])      # YCrCb inside the skin range
PULSE_BGR = np.array([0.15, 0.6, 0.3])          # a heartbeat changes green most, under one grey level


def face(eye_px=20.0, angle_deg=0.0, center=(160.0, 120.0)):
    """Pose keypoints of a face: eyes level when angle is 0, turned for a person lying down."""
    keypoints, conf = np.zeros((17, 2)), np.zeros(17)
    a = math.radians(angle_deg)
    across, down = np.array([math.cos(a), math.sin(a)]), np.array([-math.sin(a), math.cos(a)])
    middle = np.array(center)
    keypoints[R_EYE], keypoints[L_EYE] = middle - across * eye_px / 2, middle + across * eye_px / 2
    keypoints[NOSE] = middle + down * eye_px * 0.6
    conf[[R_EYE, L_EYE, NOSE]] = 0.9
    return keypoints, conf, middle + down * eye_px * 0.3, eye_px * 1.6


def run(bpm=72.0, amplitude=1.0, eye_px=20.0, angle_deg=0.0, seconds=15.0, fps=15.0, still=True, seed=1):
    rng = np.random.default_rng(seed)
    keypoints, conf, skin_center, radius = face(eye_px, angle_deg)
    yy, xx = np.mgrid[:240, :320]
    skin = (xx - skin_center[0]) ** 2 + (yy - skin_center[1]) ** 2 <= radius ** 2
    estimator, state = PulseEstimator(SignsSettings()), PulseState()
    t = 0.0
    for i in range(int(seconds * fps)):
        t = i / fps
        image = np.full((240, 320, 3), 70.0)
        image[skin] = SKIN_BGR + amplitude * PULSE_BGR * math.sin(2 * math.pi * bpm / 60 * t)
        image += rng.normal(0, 3.0, image.shape)
        estimator.update(state, np.clip(image, 0, 255).astype(np.uint8), keypoints, conf, still, t)
    return estimator.value(state, t), estimator.measured_s(state)


@pytest.mark.parametrize("bpm", [55, 72, 110])
def test_the_rate_of_a_faint_colour_pulse_is_found(bpm):
    result, _ = run(bpm=bpm)
    assert result["value"] == "estimated"
    assert result["rate_bpm"] == pytest.approx(bpm, abs=3)


def test_a_lying_person_with_a_sideways_face_works_too():
    result, _ = run(bpm=80, angle_deg=90)
    assert result["value"] == "estimated" and result["rate_bpm"] == pytest.approx(80, abs=3)


def test_no_pulse_in_the_colour_gives_no_number():
    result, _ = run(amplitude=0.0)
    assert result["value"] == "not_testable" and result["reason"] == "no_clear_rhythm"
    assert "rate_bpm" not in result


def test_small_faces_moving_people_and_short_runs_are_not_testable():
    assert run(eye_px=10)[0]["reason"] == "face_too_small"
    assert run(still=False)[0]["reason"] == "moving"
    early, measured = run(seconds=6)
    assert early["reason"] == "measuring" and measured == pytest.approx(5.9, abs=0.1)
