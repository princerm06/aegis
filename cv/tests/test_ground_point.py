import numpy as np
import pytest

from cv.homography import ground_point
from cv.observations import PostureEstimate, classify_posture, vote_posture
from cv.tests.helpers import keypoints, pinhole_calibration, world_to_pixel

BOX = (100.0, 200.0, 300.0, 600.0)


def test_upright_people_stand_on_the_bottom_center():
    for posture in ("standing", "sitting"):
        assert ground_point(BOX, posture) == ((200.0, 600.0), "bbox_bottom_center")


def test_lying_uses_the_hip_midpoint_when_confident():
    xy, kc = keypoints({11: (180, 400), 12: (220, 420)})
    assert ground_point(BOX, "lying", xy, kc) == ((200.0, 410.0), "hip_midpoint")


def test_lying_with_one_confident_hip_uses_that_hip():
    xy, kc = keypoints({11: (180, 400), 12: (220, 420)})
    kc[12] = 0.2
    assert ground_point(BOX, "lying", xy, kc) == ((180.0, 400.0), "hip_midpoint")


def test_lying_without_confident_hips_uses_the_box_center_not_the_bottom():
    xy, kc = keypoints({11: (180, 400), 12: (220, 420)}, conf=0.3)
    assert ground_point(BOX, "lying", xy, kc) == ((200.0, 400.0), "bbox_center")
    assert ground_point(BOX, "lying") == ((200.0, 400.0), "bbox_center")


def test_unknown_posture_uses_the_box_center():
    assert ground_point(BOX, "unknown") == ((200.0, 400.0), "bbox_center")


def test_horizontal_torso_is_lying():
    xy, kc = keypoints({5: (100, 300), 6: (100, 320), 11: (250, 305), 12: (250, 325)})
    estimate = classify_posture((80, 280, 400, 360), xy, kc)
    assert (estimate.label, estimate.method) == ("lying", "torso_angle")


def test_vertical_torso_with_vertical_thighs_is_standing():
    xy, kc = keypoints({5: (90, 100), 6: (110, 100), 11: (92, 200), 12: (108, 200),
                        13: (92, 290), 14: (108, 290)})
    assert classify_posture((70, 80, 130, 380), xy, kc).label == "standing"


def test_vertical_torso_with_horizontal_thighs_is_sitting():
    xy, kc = keypoints({5: (90, 100), 6: (110, 100), 11: (92, 200), 12: (108, 200),
                        13: (170, 210), 14: (186, 210)})
    assert classify_posture((70, 80, 200, 300), xy, kc).label == "sitting"


def test_person_lying_along_the_view_axis_is_lying_via_floor_length():
    """Torso looks vertical in the image, but shoulders and hips are both on the floor."""
    calibration = pinhole_calibration()
    shoulders = world_to_pixel(1.5, 2.0, 0.15)
    hips = world_to_pixel(1.5, 1.5, 0.15)
    xy, kc = keypoints({5: (shoulders[0] - 15, shoulders[1]), 6: (shoulders[0] + 15, shoulders[1]),
                        11: (hips[0] - 12, hips[1]), 12: (hips[0] + 12, hips[1])})
    box = (hips[0] - 40, shoulders[1] - 30, hips[0] + 40, hips[1] + 80)
    without_map = classify_posture(box, xy, kc)
    with_map = classify_posture(box, xy, kc, to_floor=calibration.point_to_world)
    assert without_map.label != "lying"
    assert (with_map.label, with_map.method) == ("lying", "torso_floor_length")


def test_standing_person_is_not_lying_via_floor_length():
    calibration = pinhole_calibration()
    shoulders = world_to_pixel(1.5, 2.0, 1.4)
    hips = world_to_pixel(1.5, 2.0, 0.9)
    knees = world_to_pixel(1.5, 2.0, 0.5)
    xy, kc = keypoints({5: (shoulders[0] - 15, shoulders[1]), 6: (shoulders[0] + 15, shoulders[1]),
                        11: (hips[0] - 10, hips[1]), 12: (hips[0] + 10, hips[1]),
                        13: (knees[0] - 10, knees[1]), 14: (knees[0] + 10, knees[1])})
    feet = world_to_pixel(1.5, 2.0, 0.0)
    head = world_to_pixel(1.5, 2.0, 1.7)
    box = (feet[0] - 30, head[1], feet[0] + 30, feet[1])
    assert classify_posture(box, xy, kc, to_floor=calibration.point_to_world).label == "standing"


def test_box_aspect_fallback_without_keypoints():
    assert classify_posture((0, 0, 300, 100)).label == "lying"
    assert classify_posture((0, 0, 50, 200)).label == "standing"
    assert classify_posture((0, 0, 100, 120)).label == "unknown"


def test_a_close_up_of_head_and_shoulders_is_not_lying():
    # Live C922, Nov 14: sitting 1 m from the camera, hips out of the picture, the wide box read as lying.
    xy, kc = np.zeros((17, 2)), np.zeros(17)
    xy[[0, 5, 6]] = [(640, 300), (480, 560), (800, 570)]
    kc[[0, 5, 6]] = 0.9
    box = (300, 250, 980, 720)                                   # much wider than tall
    assert classify_posture(box, xy, kc).label == "unknown"
    xy[0] = (300, 565)                                           # head beside the shoulders: lying, hips hidden
    assert classify_posture(box, xy, kc).label == "lying"


def test_posture_vote_takes_the_majority_and_scales_confidence():
    history = [PostureEstimate("lying", 0.8, "a")] * 3 + [PostureEstimate("unknown", 0.3, "b")]
    voted = vote_posture(history)
    assert voted.label == "lying"
    assert voted.confidence == pytest.approx(0.6)
