import numpy as np

from cv.settings import TrackingSettings
from cv.tests.helpers import det, scale_calibration, standing_box
from cv.tracker import TrackManager

SETTINGS = TrackingSettings(occlusion_grace_s=0.5, stale_after_s=2.0, lost_after_s=10.0,
                            position_alpha=1.0, deadband_m=0.15, reattach_radius_m=0.6)


def manager(**overrides):
    settings = TrackingSettings(**{**SETTINGS.__dict__, **overrides})
    return TrackManager(settings, "s1", scale_calibration())


def test_new_track_has_session_scoped_id_and_floor_position():
    tracks = manager()
    [track] = tracks.update([det(4, standing_box(120, 220))], t=0.0)
    assert track.track_id == "s1-t1"
    assert track.published_xy == (1.2, 2.2)        # bottom-center (120, 220) px at 100 px/m
    assert track.ground_method == "bbox_bottom_center"
    assert (track.detected, track.state, track.stale, track.is_occluded) == (True, "visible", False, False)


def test_first_seen_is_stable_and_frames_are_counted():
    tracks = manager()
    for t in (0.0, 0.1, 0.2):
        [track] = tracks.update([det(4, standing_box(120, 220))], t=t)
    assert (track.first_seen, track.last_seen, track.frames_seen) == (0.0, 0.2, 3)


def test_occluded_then_stale_then_lost_and_reported_once():
    tracks = manager()
    tracks.update([det(1, standing_box(120, 220))], t=0.0)

    [track] = tracks.update([], t=0.3)
    assert track.is_occluded and track.state == "visible"  # missed frame, still within grace

    [track] = tracks.update([], t=0.6)
    assert (track.state, track.stale) == ("occluded", False)
    assert track.last_seen == 0.0                          # never advanced while unseen

    [track] = tracks.update([], t=2.5)
    assert (track.state, track.stale) == ("occluded", True)

    assert tracks.update([], t=10.5) == []
    [report] = tracks.reports(10.5)
    assert (report.track_id, report.state, report.last_seen_t) == ("s1-t1", "lost", 0.0)
    assert tracks.reports(10.6) == []


def test_reappearing_detection_resets_occlusion():
    tracks = manager()
    tracks.update([det(1, standing_box(120, 220))], t=0.0)
    tracks.update([], t=1.0)
    [track] = tracks.update([det(1, standing_box(120, 220))], t=1.1)
    assert (track.state, track.detected, track.stale) == ("visible", True, False)


def test_retired_ids_are_never_reused():
    tracks = manager()
    tracks.update([det(1, standing_box(120, 220))], t=0.0)
    tracks.update([], t=11.0)                               # retired as lost
    [track] = tracks.update([det(1, standing_box(120, 220))], t=11.1)
    assert track.track_id == "s1-t2"


def test_new_tracker_id_near_an_occluded_track_reattaches():
    tracks = manager()
    tracks.update([det(1, standing_box(120, 220))], t=0.0)
    tracks.update([], t=1.0)
    [track] = tracks.update([det(9, standing_box(130, 225))], t=1.5)  # 0.11 m away
    assert (track.track_id, track.reattached, track.first_seen) == ("s1-t1", 1, 0.0)


def test_new_tracker_id_far_away_is_a_new_track():
    tracks = manager()
    tracks.update([det(1, standing_box(120, 220))], t=0.0)
    tracks.update([], t=1.0)
    result = tracks.update([det(9, standing_box(300, 220))], t=1.5)   # 1.8 m away
    assert sorted(t.track_id for t in result) == ["s1-t1", "s1-t2"]


def test_without_calibration_a_fall_with_a_new_tracker_id_stays_the_same_person():
    tracks = TrackManager(SETTINGS, "s1")
    tracks.update([det(1, standing_box(200, 400))], t=0.0)
    tracks.update([], t=0.3)
    tracks.update([det(5, (130, 365, 270, 400))], t=0.5)    # lying where they stood, new raw ID
    assert list(tracks.tracks) == ["s1-t1"] and tracks.tracks["s1-t1"].reattached == 1
    tracks.update([], t=0.8)
    tracks.update([det(6, standing_box(600, 400))], t=1.0)  # someone else across the room
    assert sorted(tracks.tracks) == ["s1-t1", "s1-t2"]


def test_reattach_can_be_disabled():
    tracks = manager(reattach=False)
    tracks.update([det(1, standing_box(120, 220))], t=0.0)
    tracks.update([], t=1.0)
    result = tracks.update([det(9, standing_box(120, 220))], t=1.5)
    assert len(result) == 2


def colours(*weights):
    hist = np.zeros(12, dtype=np.float32)
    hist[:len(weights)] = weights
    return hist / hist.sum()


RED, BLUE, REDDISH = colours(8, 1, 1), colours(0, 1, 1, 8), colours(7, 1, 2)


def seen(raw_id, x, appearance, conf=0.9):
    detection = det(raw_id, standing_box(x, 220), conf)
    detection.appearance = appearance
    return detection


def walk(tracks, detections_at, start, frames=12):
    for i in range(frames):
        tracks.update(detections_at(), t=start + 0.1 * i)


def test_someone_back_in_view_elsewhere_resumes_their_track_by_clothes():
    tracks = manager()
    walk(tracks, lambda: [seen(1, 120, RED)], 0.0)
    tracks.update([], t=2.0)                                  # walked out of view
    tracks.update([seen(7, 800, REDDISH)], t=3.0)             # back, 6.8 m away
    assert tracks.reports(3.0)[-1].track_id == "s1-t1"        # new ID held back while its clothes are learnt
    walk(tracks, lambda: [seen(7, 800, REDDISH)], 3.1)
    assert list(tracks.tracks) == ["s1-t1"] and tracks.merged_total == 1 and tracks.created_total == 1
    assert [r.track_id for r in tracks.reports(4.5)] == ["s1-t1"]


def test_someone_in_other_clothes_is_a_new_person_reported_once_checked():
    tracks = manager()
    walk(tracks, lambda: [seen(1, 120, RED)], 0.0)
    tracks.update([], t=2.0)
    tracks.update([seen(7, 800, BLUE)], t=3.0)
    assert [r.track_id for r in tracks.reports(3.0)] == ["s1-t1"]
    walk(tracks, lambda: [seen(7, 800, BLUE)], 3.1)
    assert sorted(r.track_id for r in tracks.reports(4.5)) == ["s1-t1", "s1-t2"]


def test_two_lookalikes_out_of_view_are_not_guessed_between():
    tracks = manager()
    walk(tracks, lambda: [seen(1, 120, RED), seen(2, 400, RED)], 0.0)
    tracks.update([], t=2.0)
    walk(tracks, lambda: [seen(9, 800, RED)], 3.0)
    assert sorted(tracks.tracks) == ["s1-t1", "s1-t2", "s1-t3"]


def test_a_new_id_gone_before_it_is_checked_is_never_reported():
    tracks = manager()
    tracks.update([seen(1, 120, RED)], t=0.0)
    tracks.update([], t=11.0)
    assert tracks.reports(11.0) == []


def test_visible_track_is_not_stolen_by_a_new_id():
    tracks = manager()
    result = tracks.update([det(1, standing_box(120, 220)), det(2, standing_box(125, 220))], t=0.0)
    assert len(result) == 2


def test_deadband_keeps_published_position_stable_under_jitter():
    tracks = manager()
    tracks.update([det(1, standing_box(120, 220))], t=0.0)
    for i, dx in enumerate([3, -4, 5, -2]):                # <= 5 px = 5 cm jitter
        [track] = tracks.update([det(1, standing_box(120 + dx, 220))], t=0.1 * (i + 1))
        assert track.published_xy == (1.2, 2.2)
    [track] = tracks.update([det(1, standing_box(150, 220))], t=1.0)   # 30 cm move
    assert track.published_xy == (1.5, 2.2)


def test_duplicate_raw_ids_keep_the_strongest_and_untracked_are_counted():
    tracks = manager()
    result = tracks.update([det(1, standing_box(120, 220), 0.5), det(1, standing_box(400, 220), 0.9),
                            det(None, standing_box(600, 220), 0.7)], t=0.0)
    assert len(result) == 1
    assert result[0].published_xy == (4.0, 2.2)
    assert tracks.untracked == 1


def test_without_calibration_nothing_is_publishable():
    tracks = TrackManager(SETTINGS, "s1", calibration=None)
    [track] = tracks.update([det(1, standing_box(120, 220))], t=0.0)
    assert track.published_xy is None and not track.mapping_valid
    assert tracks.reports(0.0) == []


def test_reports_carry_timestamps_in_contract_order():
    tracks = manager()
    tracks.update([det(1, standing_box(120, 220))], t=0.0)
    tracks.update([det(1, standing_box(120, 220))], t=0.4)
    [report] = tracks.reports(0.5)
    assert report.first_seen_t <= report.last_seen_t <= report.observed_t
    assert (report.first_seen_t, report.last_seen_t, report.observed_t) == (0.0, 0.4, 0.5)
