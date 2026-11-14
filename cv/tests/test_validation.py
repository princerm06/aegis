import json
import math
from collections import Counter

import numpy as np
import pytest

from cv.camera import Frame
from cv.tools import record, validate
from cv.tools.validate import TAKES, Check, Cues, compare, load_benchmark, recorded_takes, run_clip, score


def breathing(value, rate=None):
    return {"breathing": {"value": value, "confidence": 0.7, "rate_bpm": rate}}


def test_the_voice_paces_breathing_and_gives_each_cue_once():
    events = TAKES["breathe-12"].events()
    ins = [t for t, text in events if text == "in"]
    outs = [t for t, text in events if text == "out"]
    assert len(ins) == len(outs) == 15                    # 12 per minute for 75 s
    assert ins[1] - ins[0] == pytest.approx(5.0) and outs[0] - ins[0] == pytest.approx(2.0)
    hold = TAKES["breath-hold"].events()
    assert max(t for t, text in hold if text == "in") < 20 and (20, "Breathe out, and hold") in hold
    assert (20, "Raise your hand if you hear me") in TAKES["response-yes"].events()

    spoken = []
    cues = Cues(TAKES["eyes-open-closed"], speak=spoken.append)
    assert cues.update(10) == "" and cues.update(23) == "CLOSE YOUR EYES" and cues.update(30) == "CLOSE YOUR EYES"
    assert spoken == ["Close your eyes"]


def test_every_take_checks_a_window_inside_the_recording():
    for take in TAKES.values():
        assert take.checks and take.setup
        for check in take.checks:
            assert 0 <= check.start_s < take.duration_s and (math.isinf(check.end_s) or check.end_s <= take.duration_s)
        assert all(t <= take.duration_s for t, _ in take.events())


def test_rate_scores_only_rates_close_to_the_pace_inside_the_window():
    samples = [(t, breathing("not_testable")) for t in range(0, 20)]
    samples += [(t, breathing("motion_detected", 12.5)) for t in range(20, 40)]
    samples += [(t, breathing("motion_detected", 16.0)) for t in range(40, 50)]
    samples += [(t, breathing("no_motion_detected")) for t in range(50, 60)]
    result = score(Check("rate", "breathing", start_s=20, pace_bpm=12), samples)
    assert result["score"] == pytest.approx(0.5) and result["rate_share"] == 0.75
    assert result["rate_median"] == 12.5 and result["no_motion_share"] == 0.25
    held = score(Check("no_rate", "breathing", start_s=40, end_s=59), samples)
    assert held["score"] == pytest.approx(0.5) and held["no_motion_share"] == 0.5
    assert score(Check("rate", "breathing", start_s=70, pace_bpm=12), samples) == {"score": None, "samples": 0}


def test_blood_checks_score_hits_and_false_alarms():
    def blood(*regions):
        return {"visible_blood": {"value": "detected" if regions else "not_detected", "regions": list(regions)}}
    samples = [(0, blood("left_arm")), (1, blood("left_arm", "torso")), (2, blood()), (3, blood("right_arm"))]
    hit = score(Check("region", "visible_blood", "left_arm"), samples)
    assert hit["score"] == 0.5 and hit["regions_seen"] == ["left_arm", "right_arm", "torso"]
    assert score(Check("no_blood", "visible_blood"), samples)["score"] == 0.25
    eyes = [(t, {"eyes": {"value": v}}) for t, v in enumerate(["open", "closed", "closed", "not_testable"])]
    assert score(Check("value", "eyes", "closed"), eyes)["score"] == 0.5
    assert score(Check("reaches", "eyes", "open"), eyes)["score"] == 1.0


def test_unknown_check_kinds_and_rates_without_a_pace_are_rejected():
    with pytest.raises(ValueError):
        Check("guess", "eyes")
    with pytest.raises(ValueError):
        Check("rate", "breathing")


def test_a_drop_since_the_previous_run_is_flagged():
    results = [{"name": "a", "checks": [{"check": "x", "score": 0.7}, {"check": "y", "score": 0.9},
                                        {"check": "new", "score": 0.2}]}]
    compare(results, {"clips": [{"name": "a", "checks": [{"check": "x", "score": 0.8}, {"check": "y", "score": 0.92}]}]})
    x, y, new = results[0]["checks"]
    assert x["regressed"] and not y["regressed"] and "previous" not in new


def test_the_benchmark_file_loads_and_points_into_recordings():
    clips = load_benchmark()
    assert len(clips) >= 10 and all(c.source.is_relative_to(validate.RECORDINGS) for c in clips)
    assert {c.kind for clip in clips for c in clip.checks} <= set(validate.KINDS)


def test_reaches_looks_at_every_track_and_other_checks_at_the_main_one(tmp_path):
    video = tmp_path / "fall.mp4"
    video.write_bytes(b"")
    long = [(t, {"posture": {"value": "standing"}, "eyes": {"value": "open"}}) for t in range(10)]
    short = [(8, {"posture": {"value": "lying"}, "eyes": {"value": "closed"}})]

    def collect(*_, **__):
        return type("Got", (), {"samples": {"t1": long, "t2": short}, "seen_s": Counter(t1=10, t2=1),
                                "frames": 300, "fps": 30.0})
    clip = validate.Clip("fall", video, (Check("reaches", "posture", "lying"), Check("value", "eyes", "closed")))
    reached, eyes = run_clip(clip, None, collect)["checks"]
    assert reached["score"] == 1.0 and eyes["score"] == 0.0
    assert run_clip(validate.Clip("gone", tmp_path / "x.mp4", ()), None, collect)["skipped"]


class FakeCamera:
    def __init__(self, *_args, **_kwargs):
        self.t, self.index, self.reconnects, self.label = 0.0, 0, 0, "camera:fake"
        self.device_index, self.device_name = 0, "Fake Cam"

    def read(self):
        self.t += 1 / 30
        self.index += 1
        return Frame(np.full((48, 64, 3), 80, np.uint8), self.t, self.index)

    def release(self):
        pass


def test_record_take_saves_the_take_in_the_sidecar_and_scoring_finds_it(tmp_path, monkeypatch):
    spoken = []
    monkeypatch.setattr(record, "Camera", FakeCamera)
    monkeypatch.setattr(validate, "say", spoken.append)
    output = tmp_path / "breathe-12-test.mp4"
    assert record.main(["--take", "breathe-12", "--duration", "6", "--countdown", "0.5", "--no-display",
                        "--output", str(output)]) == 0
    sidecar = json.loads(output.with_suffix(".json").read_text())
    assert sidecar["scenario"] == "breathe-12" and sidecar["take"]["name"] == "breathe-12"
    assert sidecar["camera"]["name"] == "Fake Cam" and 5.9 <= sidecar["duration_s"] <= 6.1
    assert spoken[0].startswith("Lie on your back") and spoken[1:4] == ["in", "out", "in"] and spoken[-1] == "Done"
    (clip,) = recorded_takes(tmp_path)
    assert clip.source == output and clip.checks == TAKES["breathe-12"].checks


def test_absent_counts_false_findings_and_reports_the_rates_seen():
    samples = [(t, breathing("motion_detected", 10.0 + t)) for t in range(8)]
    samples += [(t, breathing("no_motion_detected")) for t in range(8, 10)]
    absent = score(Check("absent", "breathing", "no_motion_detected"), samples)
    assert absent["score"] == pytest.approx(0.8) and absent["rate_median"] == 13.5
    assert absent["rate_p10_p90"] == [10.0, 16.0]
