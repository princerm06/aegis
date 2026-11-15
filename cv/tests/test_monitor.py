import re
from types import SimpleNamespace

import numpy as np
import pytest

from cv.monitor import UNKNOWN as UNKNOWN_VALUE
from cv.monitor import (BOTTOM_MARGIN, BOX_H, BOX_W, CHIP_H, CHIP_W, HINT_H, MAX_VIEW_W, MIN_VIEW_W, TOP_BAR, VIEW_H,
                        Callout, CalloutLayout, LastSeen, Scene, _anchor, _crosses, _inside, _leader_end,
                        _out_of_view, box_scale,
                        build_scene, card_for, identity_color, render)
from cv.observations import PostureEstimate
from cv.signs.body import L_HIP, L_SHOULDER, NOSE, R_HIP, R_SHOULDER
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


def values(card):
    return [card.movement.text] + [gauge.value for gauge in card.gauges]


def test_findings_are_amber_and_each_gauge_has_one_short_value():
    card = card_for(FINDINGS)
    assert values(card) == ["STILL 1:35", "NO MOTION?", UNKNOWN_VALUE, "CLOSED", "NO", "TORSO +1"]   # 0.45 gets "?"
    assert [g.label for g in card.gauges] == ["BREATHING", "PULSE EST.", "EYES", "RESPONSE", "BLOOD"]
    assert {g.state for g in card.gauges if g is not card.pulse} == {"attention"}
    assert card.pulse.state == "unknown" and card.movement.state == "observed"
    assert card.eyes.icon == "closed" and card.blood.icon == "filled" and card.hint == ""


def test_untestable_signs_show_a_dash_and_one_line_says_why():
    card = card_for(UNKNOWN)
    assert values(card) == ["CAMERA MOVING"] + [UNKNOWN_VALUE] * 5
    assert {g.state for g in card.gauges} == {"unknown"}
    assert card.hint == "Hold the camera still to measure breathing"
    still = {**UNKNOWN, "movement": {"value": "still", "confidence": 0.9, "still_for_s": 3.0},
             "response": {"value": "not_tested", "confidence": 0.0},
             "breathing": {"value": "not_testable", "confidence": 0.0}}
    assert card_for(still).hint == "Chest not visible" and card_for(still).movement.text == "STILL 3 s"
    assert (card_for(still).response.value, card_for(still).response.state) == ("NOT ASKED", "unknown")
    asked = {**still, "response": {"value": "not_tested", "confidence": 0.0, "command_at": "2026-11-14T12:00:00Z"}}
    assert card_for(asked).response.value == UNKNOWN_VALUE        # asked, but the person was not seen enough
    measuring = card_for(still, {"breathing_remaining_s": 6.2, "breathing_needed_s": 9.0})
    assert measuring.breathing.value == "6 s" and measuring.breathing.state == "active"
    assert measuring.breathing.progress == pytest.approx(0.31, abs=0.01) and measuring.hint == ""
    assert card_for(still, {"breathing_remaining_s": 0.0}).hint == "No clear breathing rhythm"
    moving = {**still, "movement": {"value": "moving", "confidence": 0.7, "still_for_s": 0.0}}
    assert card_for(moving).hint == "Breathing is measured while the person is still"
    assert card_for(moving).movement.text == "MOVING"


def test_breathing_rate_and_listening_countdown():
    signs = {**FINDINGS, "breathing": {"value": "motion_detected", "confidence": 0.7, "rate_bpm": 14.2},
             "eyes": {"value": "open", "confidence": 0.9}, "visible_blood": {"value": "not_detected", "confidence": 0.8}}
    card = card_for(signs, {"listening_left_s": 6.0, "command_window_s": 8.0})
    assert (card.breathing.value, card.breathing.unit, card.breathing.state) == ("14", "/min", "observed")
    assert card.response.value == "6 s" and card.response.state == "active"
    assert card.response.progress == pytest.approx(0.75)          # the ring drains while listening
    assert card.eyes.value == "OPEN" and card.blood.value == "NOT SEEN" and card.blood.state == "observed"
    responded = card_for({**signs, "response": {"value": "responded", "confidence": 0.9}})
    assert responded.response.value == "YES" and responded.response.state == "observed"


HISTORY = {
    "fall": {"value": "seen", "confidence": 0.8, "at": "2026-11-14T05:00:01.000Z"},
    "blood_spread": {"value": "growing", "confidence": 0.6, "window_s": 30.0, "change_pct": 45},
    "limbs": {"value": "some_not_moved", "confidence": 0.45, "window_s": 30.0, "not_moved": ["left_leg"]},
    "walking": {"value": "seen", "confidence": 0.7, "at": "2026-11-14T05:00:00.000Z"},
    "waving": {"value": "seen", "confidence": 0.6, "at": "2026-11-14T05:00:04.000Z"},
    "face_down": {"value": "seen", "confidence": 0.6, "for_s": 12.0},
    "person_nearby": {"value": "seen", "confidence": 0.6, "for_s": 42.0},
    "changes": [{"sign": "movement", "from": "moving", "to": "still", "at": "2026-11-14T05:00:02.000Z"},
                {"sign": "eyes", "from": "open", "to": "closed", "at": "2026-11-14T05:00:03.000Z"}],
}


def test_what_happened_shows_under_the_gauges_most_important_first():
    context = {"fall_ago_s": 80.0, "walked_ago_s": 95.0, "waved_ago_s": 30.0, "changes_ago_s": [79.0, 40.0]}
    card = card_for({**UNKNOWN, **HISTORY}, context)
    assert [line.text for line in card.notes] == [
        "Fall seen 1:20 ago", "Face down for 12 s", "Waved 30 s ago", "Blood area growing, +45% in 30 s",
        "Eyes closed 40 s ago, were open", "Not moved in 30 s: L LEG", "Someone next to them for 42 s",
        "Walked 1:35 ago"]
    assert card_for({**UNKNOWN, **HISTORY}, {**context, "waved_ago_s": 1.0}).notes[2].text == "Waving now"
    card = card_for({**UNKNOWN, **{k: v for k, v in HISTORY.items() if k not in ("face_down", "waving")}}, context)
    assert [line.text for line in card.lines] == ["Fall seen 1:20 ago", "Blood area growing, +45% in 30 s"]
    assert {line.state for line in card.lines} == {"attention"}          # the breathing hint gives way
    assert callout(1, {**UNKNOWN, **HISTORY}, context=context).size == (BOX_W, BOX_H + 2 * HINT_H)
    quiet = card_for({**UNKNOWN, "fall": {"value": "not_seen", "confidence": 0.5}, "changes": []})
    assert [line.text for line in quiet.lines] == ["Hold the camera still to measure breathing"]


def test_no_clinical_words_and_ascii_only():
    rate = {**FINDINGS, "breathing": {"value": "motion_detected", "confidence": 0.7, "rate_bpm": 31.0},
            "pulse": {"value": "estimated", "confidence": 0.7, "rate_bpm": 150}}
    for signs in (FINDINGS, UNKNOWN, rate, {**FINDINGS, **HISTORY}):
        card = card_for(signs, {"fall_ago_s": 5.0, "walked_ago_s": 9.0, "waved_ago_s": 1.0,
                                "changes_ago_s": [3.0, 2.0]})
        texts = values(card) + [g.label for g in card.gauges] + [g.unit for g in card.gauges] + [card.hint]
        texts += [line.text for line in card.notes]
        for text in texts:
            assert text.replace(UNKNOWN_VALUE, "").isascii()
            assert not re.search(r"severity|critical|immediate|diagnos|triage|priority|bpm", text, re.I)
    assert card_for(rate).breathing.state == card_for(rate).pulse.state == "observed"   # never colored by value


def callout(number, signs=FINDINGS, in_view=True, context=None):
    return Callout(f"s1-t{number}", number, identity_color(number), "LYING", in_view, card_for(signs, context),
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
    scene = Scene([callout(n, UNKNOWN if n % 2 else FINDINGS, in_view=n % 3 != 0) for n in range(1, people + 1)],
                  hidden=2)
    canvas = render(image, [track(n) for n in range(1, people + 1)], scene, None,
                    ["room-cam-1", "ROOM room-2"], ["UNCALIBRATED - not publishing"], "C command given")
    height, width = size
    factor = min(VIEW_H / height, MAX_VIEW_W / width)
    view_h, picture_w = round(height * factor), round(width * factor)
    expected_w = max(picture_w, MIN_VIEW_W) if view_h >= MIN_VIEW_W * 0.75 else picture_w   # portrait: margins
    assert canvas.shape == (view_h, expected_w, 3)                                     # no side panel
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
    assert box_scale(1, 600) == pytest.approx(0.73)
    assert box_scale(1, 200) == 0.6                              # never unreadably small


def test_the_box_is_drawn_next_to_the_person_and_moves_with_them():
    image = np.full((720, 1280, 3), 90, np.uint8)
    scene = Scene([callout(1)])
    plain = render(image, [], Scene())
    here = np.argwhere((render(image, [track(1)], scene) != plain).any(axis=2))
    there = np.argwhere((render(image, [track(1, dx=300)], scene) != plain).any(axis=2))
    assert 200 < there[:, 1].mean() - here[:, 1].mean() < 400
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
    assert shown.card.eyes.value.startswith("CLOSED") and shown.card.blood.value.startswith("TORSO")
    gone.last_seen = 3.0                                       # out of view for 3 s: still shown, dimmed
    scene = build_scene([gone, seen], estimator, 6.0)
    assert scene.hidden == 0 and [c.in_view for c in scene.callouts] == [True, False]
    canvas = render(image, [gone, seen], scene)
    assert canvas.shape[0] == VIEW_H


def test_people_who_left_are_listed_with_room_and_time_even_after_the_tracker_forgets_them():
    estimator = make_estimator()
    image = body_image((300, 200, 340, 250))
    seen = make_track()
    seen.short_id, seen.zone_id, seen.published_xy, seen.last_seen = "t1", "room-2", None, 0.0
    estimator.update(image, [seen], 0.0)
    last_seen = LastSeen()
    gone = SimpleNamespace(**{**vars(seen), "track_id": "s1-t3", "short_id": "t3", "detected": True})
    assert build_scene([gone, seen], estimator, 0.0, last_seen).out_of_view == []
    gone.detected = False
    assert build_scene([gone, seen], estimator, 3.0, last_seen).out_of_view == []      # card still shown
    scene = build_scene([seen], estimator, 130.0, last_seen)                          # retired by the tracker
    assert scene.out_of_view[0][:2] == (3, "ROOM 2") and _out_of_view(scene) == ("OUT OF VIEW  #3 ROOM 2, 2:10 ago",)
    assert build_scene([seen], estimator, 700.0, last_seen).out_of_view == []         # kept for 10 min


def test_a_card_grows_by_one_line_only_when_it_has_something_to_explain():
    assert callout(1, FINDINGS).size == (BOX_W, BOX_H)
    assert callout(1, UNKNOWN).size == (BOX_W, BOX_H + HINT_H)
    assert callout(1, UNKNOWN, in_view=False).size == (CHIP_W, CHIP_H)     # no empty card for someone unseen


def test_leaders_point_at_the_chest_even_from_weak_shoulders():
    weak = CONF.copy()
    weak[[L_SHOULDER, R_SHOULDER]] = 0.45                        # below the keypoint cutoff, still a pointer
    person = SimpleNamespace(**{**vars(track(1)), "keypoint_conf": weak})
    x, y = _anchor(person, 0.5)
    shoulders = KEYPOINTS[[L_SHOULDER, R_SHOULDER]].mean(axis=0)
    assert x == pytest.approx(shoulders[0], abs=25) and y > shoulders[1]           # below the shoulder line
    assert y < KEYPOINTS[[L_HIP, R_HIP]].mean(axis=0)[1]
    assert np.hypot(x - KEYPOINTS[NOSE][0], y - KEYPOINTS[NOSE][1]) > 20           # not the face


def test_leader_lines_do_not_cross_and_cards_leave_chests_free():
    # Three people lying side by side, as in an overhead shot: cards must not tangle their lines.
    frame, size = (1280, 720), (BOX_W, BOX_H)
    people = [(key, (300, y - 60, 900, y + 60), (600, y), None) for key, y in (("a", 160), ("b", 360), ("c", 560))]
    placed = CalloutLayout().place(people, size, frame)
    rects = {key: (x, y, x + BOX_W, y + BOX_H) for key, (x, y) in placed.items()}
    lines = [(_leader_end(rects[key], anchor), anchor) for key, _, anchor, _ in people]
    assert not any(_crosses(*lines[i], *lines[j]) for i in range(3) for j in range(i + 1, 3))
    assert not any(_inside(anchor, rect, pad=0) for *_, anchor, _ in people for rect in rects.values())


def test_a_card_never_glides_across_another_card():
    layout = CalloutLayout()
    size, frame = (BOX_W, BOX_H), (1280, 720)
    a = ("a", (500, 300, 620, 700), (560, 360), None)
    layout.place([a, ("b", (900, 300, 1020, 700), (960, 360), None)], size, frame)
    for step in range(12):                          # b walks over to a, its card has to get past a's card
        x = 900 - 40 * step
        placed = layout.place([a, ("b", (x, 300, x + 120, 700), (x + 60, 360), None)], size, frame)
        ra, rb = ((px, py, px + BOX_W, py + BOX_H) for px, py in (placed["a"], placed["b"]))
        assert overlap(ra, rb) == 0, step


def test_the_operators_priority_and_plan_ride_on_the_card_and_blink_until_acknowledged():
    from cv.assignments import Mark

    estimator = make_estimator()
    image = body_image((300, 200, 340, 250))
    seen = make_track()
    seen.short_id, seen.zone_id, seen.published_xy, seen.last_seen = "t1", "room-2", None, 5.9
    for i in range(60):
        estimator.update(image, [seen], i / 10)
    (plain,) = build_scene([seen], estimator, 6.0).callouts
    assert plain.priority is None and not plain.band and plain.size[1] == BOX_H + HINT_H * len(plain.card.lines)

    sent = {"s1-t1": Mark("casualty-a", "immediate", "responder-002", "dispatched")}
    on, off = (build_scene([seen], estimator, t, marks=sent).callouts[0] for t in (6.0, 6.5))
    assert (on.priority, on.task, on.waiting) == ("immediate", "RESPONDER-002 DISPATCHED", True)
    assert on.lit != off.lit and on.size[1] > plain.size[1]          # blinks; the band adds height
    acked = {"s1-t1": Mark("casualty-a", "immediate", "responder-002", "acknowledged")}
    steady = [build_scene([seen], estimator, t, marks=acked).callouts[0] for t in (6.0, 6.5)]
    assert all(c.lit and not c.waiting and c.task == "RESPONDER-002 ON THE WAY" for c in steady)
    planned = {"s1-t1": Mark("casualty-a", None, "responder-001", "proposed")}
    (unassessed,) = build_scene([seen], estimator, 6.0, marks=planned).callouts
    assert unassessed.band and unassessed.priority is None and unassessed.task.startswith("TO APPROVE: ")
    for scene in (Scene([on]), Scene([off]), Scene([unassessed])):
        assert render(image, [seen], scene).shape[0] == VIEW_H


def test_a_long_task_line_is_cut_to_fit_instead_of_running_over_the_band():
    from PIL import Image, ImageDraw

    from cv.monitor import _text

    draw = ImageDraw.Draw(Image.new("RGBA", (400, 40)))
    width = _text(draw, 380, 14, "TO APPROVE: Mock Responder Bravo of the second team", "label", 11, (255, 255, 255),
                  1.0, max_width=120, anchor="rm")
    assert width <= 120
