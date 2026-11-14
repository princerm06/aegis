"""Face down and person nearby: how someone lies and who is next to them. Observations only."""

from types import SimpleNamespace

import numpy as np

from cv.observations import PostureEstimate
from cv.settings import SignsSettings
from cv.signs.body import L_HIP, L_SHOULDER, NOSE, R_HIP, R_SHOULDER
from cv.signs.position import PositionEstimator, PositionState

SETTINGS = SignsSettings(enabled=True)
IMAGE = (640, 480)


def lying(shoulder_width=80, face=0.1, x=200):
    """Lying along the image x axis, head to the left; torso 140 px."""
    keypoints = np.zeros((17, 2))
    conf = np.full(17, 0.9)
    keypoints[L_SHOULDER], keypoints[R_SHOULDER] = (x, 330 - shoulder_width / 2), (x, 330 + shoulder_width / 2)
    keypoints[L_HIP], keypoints[R_HIP] = (x + 140, 310), (x + 140, 350)
    keypoints[NOSE] = (x - 60, 330)
    conf[:3] = face
    return keypoints, conf


def person(track_id, posture, bbox, keypoints=None, conf=None, ground=None):
    return SimpleNamespace(track_id=track_id, detected=True, bbox=bbox, keypoints=keypoints, keypoint_conf=conf,
                           posture=PostureEstimate(posture, 0.8, "keypoints"), ground_px=ground, map_xy=None)


def face_down_after(keypoints, conf, seconds=4.0, posture="lying"):
    estimator, state = PositionEstimator(SETTINGS), PositionState()
    track = person("s1-t1", posture, (100, 280, 400, 380), keypoints, conf)
    for t in np.arange(0, seconds, 0.1):
        estimator.update_face(state, track, IMAGE, t)
    return estimator.face_down(state, seconds)


def test_face_down_needs_a_hidden_face_wide_shoulders_and_the_head_in_the_picture():
    down = face_down_after(*lying())
    assert down["value"] == "seen" and down["for_s"] >= 2.0 and down["confidence"] <= 0.6
    assert face_down_after(*lying(face=0.9))["value"] == "not_seen"                   # face seen
    assert face_down_after(*lying(shoulder_width=15))["value"] == "not_testable"      # on the side, facing away
    assert face_down_after(*lying(x=30))["value"] == "not_testable"                   # head out of the picture
    assert face_down_after(*lying(), seconds=1.5)["value"] == "not_testable"         # not held long enough
    assert face_down_after(*lying(), posture="sitting")["value"] == "not_testable"


def nearby_after(others, seconds=3.0, posture="lying"):
    estimator = PositionEstimator(SETTINGS)
    keypoints, conf = lying()
    down = person("s1-t1", posture, (100, 280, 400, 380), keypoints, conf)
    states = {"s1-t1": PositionState(), **{other.track_id: PositionState() for other in others}}
    for t in np.arange(0, seconds, 0.1):
        estimator.update_nearby(states, [down, *others], t)
    return estimator.person_nearby(states["s1-t1"], seconds)


def test_someone_standing_or_kneeling_next_to_a_person_down_is_seen_after_two_seconds():
    helper = person("s1-t2", "standing", (410, 140, 470, 380), ground=(440, 380))
    near = nearby_after([helper])
    assert near["value"] == "seen" and near["for_s"] >= 2.0
    assert nearby_after([helper], seconds=1.5)["value"] == "not_seen"                 # just walking past
    far = person("s1-t3", "standing", (560, 40, 620, 200), ground=(590, 200))
    assert nearby_after([far])["value"] == "not_seen"
    other_casualty = person("s1-t4", "lying", (410, 300, 640, 380), ground=(520, 380))
    assert nearby_after([other_casualty])["value"] == "not_seen"                      # two people down
    assert nearby_after([helper], posture="standing")["value"] == "not_testable"     # only for people down
