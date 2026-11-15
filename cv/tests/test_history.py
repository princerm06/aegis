"""History signs: fall, walking, limbs, changes, blood spreading. Observations, never injuries."""

import numpy as np

from cv.settings import SignsSettings
from cv.signs.blood import BloodEstimator, BloodState
from cv.signs.body import R_ELBOW, R_WRIST, body_scale
from cv.signs.history import HistoryEstimator, HistoryState
from cv.signs.motion import Sample
from cv.tests.test_signs import BBOX, CONF, KEYPOINTS, body_image

SETTINGS = SignsSettings(enabled=True)
SCALE = body_scale(BBOX, KEYPOINTS, CONF)          # 280 px
FPS = 15


def iso(t):
    return f"t={t:.1f}"


def sample(t, keypoints):
    return Sample(t, keypoints, CONF, keypoints.mean(axis=0), SCALE)


def run(frames, state=None, estimator=None):
    """frames: (t, keypoints, posture, ground or None). Feeds the estimator like SignsEstimator does."""
    estimator = estimator or HistoryEstimator(SETTINGS)
    state = state or HistoryState()
    samples = []
    for t, keypoints, posture, ground in frames:
        reference = next((s for s in reversed(samples) if t - s.t >= SETTINGS.motion_lag_s), None)
        current = sample(t, keypoints)
        samples.append(current)
        x1, y1, x2, y2 = BBOX
        dy = keypoints[0][1] - KEYPOINTS[0][1]
        estimator.update(state, current, reference, posture, ground, (x1, y1 + dy, x2, y2 + dy))
    return estimator, state


def times(start, end):
    return np.arange(start, end, 1 / FPS)


def test_a_fast_drop_from_standing_to_lying_is_a_fall_and_lying_down_slowly_is_not():
    def going_down(duration):
        frames = [(t, KEYPOINTS, "standing", None) for t in times(0, 2)]
        for t in times(2, 2 + duration):
            frames.append((t, KEYPOINTS + (0, 250 * (t - 2) / duration), "standing", None))
        frames += [(t, KEYPOINTS + (0, 250), "lying", None) for t in times(2 + duration, 5 + duration)]
        return frames

    estimator, state = run(going_down(0.6))
    fall = estimator.fall(state, 6.0, iso)
    assert fall["value"] == "seen" and 0.5 <= fall["confidence"] <= 0.9
    assert 1.0 <= float(fall["at"][2:]) <= 2.6                     # when the drop started
    estimator, state = run(going_down(5.0))                        # lying down on purpose
    assert estimator.fall(state, 11.0, iso)["value"] == "not_seen"


def test_walking_is_seen_only_when_upright_and_travelling():
    walk = [(t, KEYPOINTS + (75 * t, 0), "standing", (320 + 75 * t, 460)) for t in times(0, 5)]
    estimator, state = run(walk)
    assert estimator.walking(state, 5.0, iso)["value"] == "seen"
    stand = [(t, KEYPOINTS, "standing", (320, 460)) for t in times(0, 5)]
    estimator, state = run(stand)
    assert estimator.walking(state, 5.0, iso)["value"] == "not_seen"
    crawl = [(t, KEYPOINTS + (75 * t, 0), "lying", (320 + 75 * t, 460)) for t in times(0, 5)]
    estimator, state = run(crawl)
    assert estimator.walking(state, 5.0, iso)["value"] == "not_seen"
    estimator, state = run(stand[:10])
    assert estimator.walking(state, 0.6, iso)["value"] == "not_testable"     # not watched long enough


def test_limbs_that_did_not_move_are_named_only_when_another_limb_moved():
    def waving(t):
        keypoints = KEYPOINTS.copy()
        keypoints[[R_ELBOW, R_WRIST]] += (0, 60 * np.sin(2 * np.pi * 0.7 * t))
        return keypoints

    estimator, state = run([(t, waving(t), "lying", None) for t in times(0, 30)])
    limbs = estimator.limbs(state, 30.0)
    assert limbs["value"] == "some_not_moved" and limbs["window_s"] == 30.0
    assert limbs["not_moved"] == ["left_arm", "left_leg", "right_leg"] and limbs["confidence"] <= 0.6
    estimator, state = run([(t, KEYPOINTS, "lying", None) for t in times(0, 30)])
    assert estimator.limbs(state, 30.0)["value"] == "none_moved"
    estimator, state = run([(t, KEYPOINTS, "lying", None) for t in times(0, 5)])
    assert estimator.limbs(state, 5.0)["value"] == "not_testable"


def test_waving_is_a_raised_hand_swinging_and_not_a_walking_arm_or_a_held_up_hand():
    def arm(t, wrist_y, swing):
        keypoints = KEYPOINTS.copy()
        keypoints[R_ELBOW] = (265, (150 + wrist_y) / 2)
        keypoints[R_WRIST] = (250 + swing * np.sin(2 * np.pi * 1.5 * t), wrist_y)
        return keypoints

    estimator, state = run([(t, arm(t, 60, 40), "standing", None) for t in times(0, 4)])
    waving = estimator.waving(state, 4.0, iso)
    assert waving["value"] == "seen" and waving["confidence"] <= 0.6 and float(waving["at"][2:]) >= 3.0
    estimator, state = run([(t, arm(t, 280, 40), "standing", None) for t in times(0, 4)])   # arm swing, hand low
    assert estimator.waving(state, 4.0, iso)["value"] == "not_seen"
    estimator, state = run([(t, arm(t, 60, 0), "standing", None) for t in times(0, 4)])     # hand up, still
    assert estimator.waving(state, 4.0, iso)["value"] == "not_seen"
    flips = [(t, arm(0.25 if int(t * 3) % 2 else 0.75, 60, 60), "standing", None) for t in times(0, 4)]
    estimator, state = run(flips)                                  # keypoint jumping between two places
    assert estimator.waving(state, 4.0, iso)["value"] == "not_seen"
    estimator, state = run([(t, arm(t, 60, 40), "standing", None) for t in times(0, 1)])
    assert estimator.waving(state, 1.0, iso)["value"] == "not_testable"


def test_a_calm_wave_is_still_waving():
    """Measured on the demo-two take: a bit over one sweep a second."""
    def arm(t):
        keypoints = KEYPOINTS.copy()
        keypoints[R_ELBOW] = (265, 105)
        keypoints[R_WRIST] = (250 + 70 * np.sin(2 * np.pi * 0.7 * t), 60)
        return keypoints

    estimator, state = run([(t, arm(t), "standing", None) for t in np.arange(0, 8, 1 / 30)])
    assert estimator.waving(state, 8.0, iso)["value"] == "seen"


def test_a_change_counts_once_it_held_and_flicker_does_not():
    estimator, state = HistoryEstimator(SETTINGS), HistoryState()
    timeline = [(t, "open") for t in np.arange(0, 10, 0.5)] + [(t, "closed") for t in np.arange(10, 12, 0.5)]
    timeline += [(t, "open") for t in np.arange(12, 20, 0.5)]           # 2 s closed: a blink-like flicker
    timeline += [(t, "closed") for t in np.arange(20, 30, 0.5)]
    for t, eyes in timeline:
        estimator.track_changes(state, {"eyes": eyes, "movement": "still", "breathing": "not_testable"}, t)
    assert estimator.changes(state, 30.0, iso) == [{"sign": "eyes", "from": "open", "to": "closed", "at": "t=20.0"}]
    assert estimator.changes(state, 400.0, iso) == []                  # older than change_keep_s


def test_a_growing_red_area_on_the_body_is_spreading_and_a_constant_one_is_stable():
    def spread(grow):
        estimator, state = BloodEstimator(SETTINGS), BloodState()
        for i, t in enumerate(np.arange(0, 31, 1 / 3)):
            half = 12 + (18 * t / 30 if grow else 0)
            image = body_image((int(320 - half), int(230 - half), int(320 + half), int(230 + half)))
            estimator.update(state, image, BBOX, KEYPOINTS, CONF, t)
        return estimator, state

    estimator, state = spread(grow=True)
    growing = estimator.spread(state, 30.0, detected=True)
    assert growing["value"] == "growing" and growing["change_pct"] > 30
    estimator, state = spread(grow=False)
    assert estimator.spread(state, 30.0, detected=True)["value"] == "stable"
    assert estimator.spread(state, 30.0, detected=False)["value"] == "not_testable"   # no blood seen


def test_a_real_snapshot_fits_the_proposed_backend_models_and_says_nothing_clinical():
    import re

    import pytest

    history_contract = pytest.importorskip("cv.history_contract")
    from cv.tests.test_signs import make_estimator, make_track

    estimator, track = make_estimator(), make_track()
    image = body_image((300, 200, 340, 250))
    for i in range(400):                                    # 40 s: limbs and blood spread become testable
        estimator.update(image, [track], i / 10)
    signs = estimator.snapshot(track, 40.0)
    estimator.states["s1-t1"].history.changes.append(("eyes", "open", "closed", 39.0))
    signs = estimator.snapshot(track, 40.0)
    from cv.publisher import DISPLAY_ONLY_SIGNS

    assert "pulse" in signs and "pulse" in DISPLAY_ONLY_SIGNS                # shown, never sent
    signs = {name: sign for name, sign in signs.items() if name not in DISPLAY_ONLY_SIGNS}
    model = history_contract.CameraSignsWithHistory.model_validate(signs)
    assert model.limbs.value == "none_moved" and model.blood_spread.value == "stable"
    assert model.changes[0].from_value == "open"
    assert not re.search(r"severity|injur|fractur|hemorrh|diagnos|triage|priority|heart", str(signs), re.I)


def test_a_moving_camera_pauses_fall_walking_and_limbs():
    estimator, state = HistoryEstimator(SETTINGS), HistoryState()
    moving = Sample(1.0, KEYPOINTS, CONF, KEYPOINTS.mean(axis=0), SCALE, camera="moving")
    estimator.update(state, moving, None, "standing", None, BBOX)
    for value in (estimator.fall(state, 1.2, iso), estimator.walking(state, 1.2, iso), estimator.limbs(state, 1.2)):
        assert value["value"] == "not_testable" and value["reason"] == "camera_moving"
