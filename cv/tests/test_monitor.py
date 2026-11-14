import re
from types import SimpleNamespace

import numpy as np
import pytest

from cv.monitor import MAX_VIDEO_W, PANEL_W, VIEW_H, Card, build_cards, identity_color, render, rows_for
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
    "response": {"value": "not_tested", "confidence": 0.0},
    "eyes": {"value": "not_testable", "confidence": 0.0},
    "breathing": {"value": "not_testable", "confidence": 0.0, "reason": "camera_moving"},
    "visible_blood": {"value": "not_testable", "confidence": 0.0},
}


def by_label(rows):
    return {row.label: row for row in rows}


def test_rows_follow_the_responder_order_and_flag_findings_in_amber():
    rows = rows_for(FINDINGS)
    assert [row.label for row in rows] == ["BLOOD", "BREATHING", "RESPONSE", "EYES", "MOVEMENT"]
    row = by_label(rows)
    assert row["BLOOD"].text == "seen: torso, L leg" and row["BLOOD"].state == "attention"
    assert {row[name].state for name in ("BREATHING", "RESPONSE", "EYES")} == {"attention"}
    assert row["MOVEMENT"].text == "still 1 min 35 s" and row["MOVEMENT"].state == "observed"
    assert row["EYES"].confidence == 0.75


def test_untestable_signs_are_grey_and_say_what_is_needed():
    row = by_label(rows_for(UNKNOWN))
    assert all(r.state == "unknown" and r.confidence is None for r in row.values())
    assert row["BREATHING"].text == "camera must be still" and row["MOVEMENT"].text == "camera moving"
    assert row["RESPONSE"].text == "press C after a command" and row["EYES"].text == "face not visible"
    still = {**UNKNOWN, "movement": {"value": "still", "confidence": 0.9, "still_for_s": 3.0},
             "breathing": {"value": "not_testable", "confidence": 0.0}}
    assert by_label(rows_for(still))["BREATHING"].text == "chest not visible"
    assert by_label(rows_for(still, {"breathing_remaining_s": 6.2}))["BREATHING"].text == "measuring, 6 s"
    assert by_label(rows_for(still, {"breathing_remaining_s": 0.0}))["BREATHING"].text == "no clear rhythm"
    moving = {**still, "movement": {"value": "moving", "confidence": 0.7, "still_for_s": 0.0}}
    assert by_label(rows_for(moving))["BREATHING"].text == "person must be still"


def test_breathing_rate_and_listening_countdown():
    signs = {**FINDINGS, "breathing": {"value": "motion_detected", "confidence": 0.7, "rate_bpm": 14.2}}
    row = by_label(rows_for(signs, {"listening_left_s": 6.0, "command_window_s": 8.0}))
    assert row["BREATHING"].text == "14 /min" and row["BREATHING"].state == "observed"
    assert row["RESPONSE"].text == "listening, 6 s" and row["RESPONSE"].state == "active"
    assert row["RESPONSE"].progress == pytest.approx(0.75)


def test_no_clinical_words_no_heart_rate_and_ascii_only():
    texts = [row.text + row.label for signs in (FINDINGS, UNKNOWN) for row in rows_for(signs)]
    for text in texts:
        assert text.isascii()
        assert not re.search(r"severity|critical|immediate|diagnos|triage|priority|heart|pulse|bpm", text, re.I)


def card(number, signs=FINDINGS, in_view=True):
    return Card(f"s1-t{number}", number, identity_color(number), "LYING", "room-2", in_view, rows_for(signs),
                trace=np.sin(np.linspace(0, 12, 150)), blood_regions=("torso", "left_leg"))


def track(number, detected=True):
    return SimpleNamespace(track_id=f"s1-t{number}", short_id=f"t{number}", detected=detected, bbox=BBOX,
                           keypoints=KEYPOINTS, keypoint_conf=CONF, last_seen=0.0)


@pytest.mark.parametrize("size, people", [((480, 640), 0), ((720, 1280), 2), ((1080, 1920), 4),
                                          ((240, 640), 7), ((1280, 720), 14)])
def test_render_fits_any_frame_and_any_number_of_people(size, people):
    image = np.full((*size, 3), 90, np.uint8)
    canvas = render(image, [track(n) for n in range(1, people + 1)],
                    [card(n, in_view=n % 3 != 0) for n in range(1, people + 1)],
                    ["room-cam-1", "ROOM room-2"], ["UNCALIBRATED - not publishing"], "C command given", hidden=2)
    height, width = size
    assert canvas.shape[0] == VIEW_H
    assert canvas.shape[1] == round(width * min(VIEW_H / height, MAX_VIDEO_W / width)) + PANEL_W
    panel = canvas[:, -PANEL_W:]
    assert panel.std() > 5                                    # something is drawn
    if people:
        assert (panel == np.array(identity_color(1), np.uint8)).all(axis=2).any()


def test_cards_come_from_the_live_signs_and_hide_people_long_out_of_view():
    estimator = make_estimator()
    image = body_image((300, 200, 340, 250))
    seen = make_track()
    seen.short_id, seen.zone_id, seen.published_xy, seen.last_seen = "t1", "room-2", None, 5.9
    for i in range(60):
        estimator.update(image, [seen], i / 10)
    gone = SimpleNamespace(**{**vars(seen), "track_id": "s1-t2", "short_id": "t2", "detected": False,
                              "last_seen": 0.0, "posture": PostureEstimate("sitting", 0.6, "keypoints")})
    estimator.states["s1-t2"] = estimator.states["s1-t1"]
    cards, hidden = build_cards([gone, seen], estimator, 6.0)
    assert hidden == 1 and [c.number for c in cards] == [1]
    (shown,) = cards
    assert (shown.title, shown.location, shown.blood_regions) == ("LYING", "room-2", ("torso",))
    assert by_label(shown.rows)["EYES"].text == "closed"
    gone.last_seen = 3.0                                       # out of view for 3 s: still shown, dimmed
    cards, hidden = build_cards([gone, seen], estimator, 6.0)
    assert hidden == 0 and [c.in_view for c in cards] == [True, False]
