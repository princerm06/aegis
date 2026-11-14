import re
from types import SimpleNamespace

import numpy as np
import pytest

from cv.monitor import (BOTTOM_MARGIN, BOX_H, BOX_W, MAX_VIEW_W, TOP_BAR, VIEW_H, Callout, CalloutLayout, Scene,
                        box_scale, build_scene, identity_color, lines_for, render)
from cv.observations import PostureEstimate
from cv.tests.test_signs import BBOX, CONF, KEYPOINTS, body_image, make_estimator, make_track

FINDINGS = {
    "posture": {"value": "lying", "confidence": 0.8},
    "movement": {"value": "still", "confidence": 0.9, "still_for_s": 95.0},
    "response": {"value": "no_response", "confidence": 0.7},
    "eyes": {"value": "closed", "confidence": 0.75},
    "breathing": {"value": "no_motion_detected", "confidence": 0.45},
    "visible_blood": {"value": "detected", "confidence": 0.65, "regions": ["torso", "left_leg"]},
}
UNKNOWN = {
    "posture": {"value": "unknown", "confidence": 0.3},
    "movement": {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"},
    "response": {"value": "not_tested", "confidence": 0.0, "reason": "camera_moving"},
    "eyes": {"value": "not_testable", "confidence": 0.0},
    "breathing": {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"},
    "visible_blood": {"value": "not_testable", "confidence": 0.0},
}


def texts(lines):
    return [lines.movement.text, lines.eyes.text, lines.response.text, lines.blood.text, lines.breathing.text]


def test_findings_are_amber_and_everything_reads_as_plain_words():
    lines = lines_for(FINDINGS)
    assert texts(lines) == ["STILL 1:35 min", "EYES CLOSED", "NO RESPONSE", "BLOOD: TORSO, L LEG",
                            "NO CHEST MOTION ?"]                   # 0.45 confidence gets the "?"
    assert {line.state for line in (lines.eyes, lines.response, lines.blood, lines.breathing)} == {"attention"}
    assert lines.movement.state == "observed" and lines.eye_icon == "closed"
    assert not lines.breathing_rate and not lines.show_trace


def test_untestable_signs_are_grey_and_say_what_is_needed():
    lines = lines_for(UNKNOWN)
    assert texts(lines) == ["CAMERA MOVING", "EYES NOT VISIBLE", "NOT TESTED (CAMERA MOVING)",
                            "BLOOD: BODY NOT VISIBLE", "CAMERA MUST BE STILL"]
    assert {line.state for line in (lines.movement, lines.eyes, lines.response, lines.blood, lines.breathing)} \
        == {"unknown"}
    still = {**UNKNOWN, "movement": {"value": "still", "confidence": 0.9, "still_for_s": 3.0},
             "response": {"value": "not_tested", "confidence": 0.0},
             "breathing": {"value": "not_testable", "confidence": 0.0}}
    assert lines_for(still).breathing.text == "CHEST NOT VISIBLE"
    assert lines_for(still).response.text == "RESPONSE NOT TESTED"
    measuring = lines_for(still, {"breathing_remaining_s": 6.2})
    assert measuring.breathing.text == "MEASURING 6 s" and measuring.show_trace
    assert lines_for(still, {"breathing_remaining_s": 0.0}).breathing.text == "NO CLEAR RHYTHM"
    moving = {**still, "movement": {"value": "moving", "confidence": 0.7, "still_for_s": 0.0}}
    assert lines_for(moving).breathing.text == "PERSON MUST BE STILL" and lines_for(moving).movement.text == "MOVING"


def test_breathing_rate_and_listening_countdown():
    signs = {**FINDINGS, "breathing": {"value": "motion_detected", "confidence": 0.7, "rate_bpm": 14.2},
             "eyes": {"value": "open", "confidence": 0.9}}
    lines = lines_for(signs, {"listening_left_s": 6.0, "command_window_s": 8.0})
    assert lines.breathing.text == "14" and lines.breathing_rate and lines.show_trace
    assert lines.breathing.state == "observed"               # a rate is never coloured by its value
    assert lines.response.text == "LISTENING 6 s" and lines.response.state == "active"
    assert lines.eyes.text == "EYES OPEN" and lines.eye_icon == "open"


def test_no_clinical_words_no_heart_rate_and_ascii_only():
    rate = {**FINDINGS, "breathing": {"value": "motion_detected", "confidence": 0.7, "rate_bpm": 31.0}}
    for signs in (FINDINGS, UNKNOWN, rate):
        for text in texts(lines_for(signs)):
            assert text.isascii()
            assert not re.search(r"severity|critical|immediate|diagnos|triage|priority|heart|pulse|bpm", text, re.I)


def callout(number, signs=FINDINGS, in_view=True, context=None):
    return Callout(f"s1-t{number}", number, identity_color(number), "LYING", in_view, lines_for(signs, context),
                   blood_regions=("torso", "left_leg"))


def track(number, detected=True, dx=0.0, dy=0.0):
    shift = np.array([dx, dy])
    x1, y1, x2, y2 = BBOX
    return SimpleNamespace(track_id=f"s1-t{number}", short_id=f"t{number}", detected=detected,
                           bbox=(x1 + dx, y1 + dy, x2 + dx, y2 + dy), keypoints=KEYPOINTS + shift,
                           keypoint_conf=CONF, last_seen=0.0)


def overlap(a, b):
    return max(0, min(a[2], b[2]) - max(a[0], b[0])) * max(0, min(a[3], b[3]) - max(a[1], b[1]))


@pytest.mark.parametrize("size, people", [((480, 640), 0), ((720, 1280), 2), ((1080, 1920), 4),
                                          ((240, 640), 7), ((1280, 720), 14)])
def test_render_fits_any_frame_and_any_number_of_people(size, people):
    image = np.full((*size, 3), 90, np.uint8)
    scene = Scene([callout(n, in_view=n % 3 != 0) for n in range(1, people + 1)],
                  {"s1-t1": np.sin(np.linspace(0, 12, 150))}, hidden=2)
    canvas = render(image, [track(n) for n in range(1, people + 1)], scene, None,
                    ["room-cam-1", "ROOM room-2"], ["UNCALIBRATED - not publishing"], "C command given")
    height, width = size
    factor = min(VIEW_H / height, MAX_VIEW_W / width)
    assert canvas.shape == (round(height * factor), round(width * factor), 3)      # the picture only, no side panel
    assert canvas.dtype == np.uint8 and canvas[:TOP_BAR].mean() < 80               # top bar drawn


def test_boxes_sit_beside_their_person_inside_the_frame_and_apart():
    frame = (1280, 720)
    size = (BOX_W, BOX_H)
    people = [("a", (200, 200, 320, 600), (260, 260), (240, 200, 280, 240)),
              ("b", (420, 220, 540, 620), (480, 280), (460, 220, 500, 260)),
              ("c", (900, 150, 1040, 640), (970, 220), None)]
    placed = CalloutLayout().place(people, size, frame)
    rects = {key: (x, y, x + BOX_W, y + BOX_H) for key, (x, y) in placed.items()}
    for key, body, anchor, _ in people:
        x1, y1, x2, y2 = rects[key]
        assert x1 >= 0 and y1 >= TOP_BAR and x2 <= frame[0] and y2 <= frame[1] - BOTTOM_MARGIN
        gap = np.hypot(anchor[0] - min(max(anchor[0], x1), x2), anchor[1] - min(max(anchor[1], y1), y2))
        assert gap < 260                                         # near its own person
    keys = list(rects)
    assert all(overlap(rects[a], rects[b]) == 0 for i, a in enumerate(keys) for b in keys[i + 1:])
    assert all(overlap(rect, face) == 0 for rect in rects.values() for *_, face in people if face)


def test_a_box_follows_its_person_smoothly_and_is_forgotten_when_they_leave():
    layout = CalloutLayout()
    person = lambda x: [("a", (x, 200, x + 120, 600), (x + 60, 260), None)]
    first = layout.place(person(300), (BOX_W, BOX_H), (1280, 720))["a"]
    assert layout.place(person(300), (BOX_W, BOX_H), (1280, 720))["a"] == first     # steady when the person is
    moved = layout.place(person(400), (BOX_W, BOX_H), (1280, 720))["a"]
    assert 0 < moved[0] - first[0] < 100                         # part of the way, not a jump
    for _ in range(40):
        moved = layout.place(person(400), (BOX_W, BOX_H), (1280, 720))["a"]
    assert moved[0] - first[0] == 100                            # and it arrives
    layout.place([], (BOX_W, BOX_H), (1280, 720))
    assert not layout.positions and not layout.sides


def test_boxes_shrink_for_crowds_and_narrow_pictures():
    assert box_scale(1, 1280) == 1.0 and box_scale(3, 1280) == 0.85 and box_scale(9, 1280) == 0.72
    assert box_scale(1, 405) == pytest.approx(0.73)
    assert box_scale(1, 200) == 0.6                              # never unreadably small


def test_the_box_is_drawn_next_to_the_person_and_moves_with_them():
    image = np.full((720, 1280, 3), 90, np.uint8)
    scene = Scene([callout(1)])
    plain = render(image, [], Scene())
    here = np.argwhere((render(image, [track(1)], scene) != plain).any(axis=2))
    there = np.argwhere((render(image, [track(1, dx=500)], scene) != plain).any(axis=2))
    assert 400 < there[:, 1].mean() - here[:, 1].mean() < 600
    x1, y1, x2, y2 = BBOX
    assert here[:, 1].min() > x1 - BOX_W - 60 and here[:, 1].max() < x2 + BOX_W + 60


def test_scene_comes_from_the_live_signs_and_hides_people_long_out_of_view():
    estimator = make_estimator()
    image = body_image((300, 200, 340, 250))
    seen = make_track()
    seen.short_id, seen.zone_id, seen.published_xy, seen.last_seen = "t1", "room-2", None, 5.9
    for i in range(60):
        estimator.update(image, [seen], i / 10)
    gone = SimpleNamespace(**{**vars(seen), "track_id": "s1-t2", "short_id": "t2", "detected": False,
                              "last_seen": 0.0, "posture": PostureEstimate("sitting", 0.6, "keypoints")})
    estimator.states["s1-t2"] = estimator.states["s1-t1"]
    scene = build_scene([gone, seen], estimator, 6.0)
    assert scene.hidden == 1 and [c.number for c in scene.callouts] == [1]
    (shown,) = scene.callouts
    assert (shown.posture, shown.blood_regions) == ("LYING", ("torso",))
    assert shown.lines.eyes.text.startswith("EYES CLOSED") and shown.lines.blood.text.startswith("BLOOD: TORSO")
    gone.last_seen = 3.0                                       # out of view for 3 s: still shown, dimmed
    scene = build_scene([gone, seen], estimator, 6.0)
    assert scene.hidden == 0 and [c.in_view for c in scene.callouts] == [True, False]
    canvas = render(image, [gone, seen], scene)
    assert canvas.shape[0] == VIEW_H
