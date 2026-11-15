"""Validation takes with known answers, and one command that scores them all.

    python -m cv.tools.validate --list                   # the takes and how to do each
    python -m cv.tools.record --take breathe-12          # record one; a voice paces breathing and gives cues
    python -m cv.tools.validate                          # score every take and every benchmark clip

A take is a short scripted recording: the voice (macOS ``say``) gives the
breathing pace, "hold", "close your eyes" or the spoken command, so the answer
is known for every second of the clip. ``record --take`` stores the take name
in the sidecar; scoring finds the clips in ``cv/recordings/validation/``.
``cv/eval/benchmark.yaml`` adds the public clips (``cv/recordings/datasets/``)
with what each should show.

Every check scores 0..1, higher is better, from the main track (the person seen
longest) and only inside the check's time window:

- ``rate``: share of samples with a breathing rate within 2/min of the pace.
- ``no_rate``: share without a breathing rate (breath held: any rate is false).
- ``value``: share with the expected value, e.g. eyes = closed.
- ``absent``: share without the value, e.g. no ``no_motion_detected`` on a
  person who is breathing (a false "no chest motion").
- ``region``: share with visible blood detected in that body region.
- ``no_blood``: share without visible blood detected (false alarms).
- ``reaches``: 1 if the value appears on any track, e.g. posture = lying after
  a fall (the person on the floor may get a new track ID).

Numbers only are written to ``cv/eval/validation/``; each run is compared with
the previous one and drops of more than 0.05 are flagged. Never commit the clips.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Callable, Sequence

from cv.clock import iso_utc
from cv.settings import CV_DIR, DEFAULT_CONFIG

log = logging.getLogger("cv.validate")
RECORDINGS = CV_DIR / "recordings"
TAKES_DIR = RECORDINGS / "validation"
BENCHMARK = CV_DIR / "eval" / "benchmark.yaml"
OUT_DIR = CV_DIR / "eval" / "validation"
RATE_TOLERANCE_BPM = 2.0
REGRESSION = 0.05
KINDS = ("rate", "no_rate", "value", "absent", "region", "no_blood", "reaches")


@dataclass(frozen=True)
class Check:
    kind: str
    sign: str = ""
    value: str = ""
    start_s: float = 0.0
    end_s: float = math.inf
    pace_bpm: float | None = None

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"check kind {self.kind!r}: use one of {', '.join(KINDS)}")
        if self.kind == "rate" and not self.pace_bpm:
            raise ValueError("a rate check needs pace_bpm")

    @property
    def label(self) -> str:
        if self.kind == "rate":
            what = f"rate {self.pace_bpm:g}/min"
        elif self.kind == "no_rate":
            what = "no rate (breath held)"
        elif self.kind == "region":
            what = f"blood on {self.value}"
        elif self.kind == "no_blood":
            what = "no false blood"
        elif self.kind == "absent":
            what = f"{self.sign} never {self.value}"
        else:
            what = f"{self.sign} {'reaches ' if self.kind == 'reaches' else ''}{self.value}"
        if self.start_s == 0 and math.isinf(self.end_s):
            return what
        return what + (f" from {self.start_s:g} s" if math.isinf(self.end_s) else
                       f" at {self.start_s:g}-{self.end_s:g} s")


@dataclass(frozen=True)
class Take:
    name: str
    duration_s: float
    setup: str
    checks: tuple[Check, ...]
    pace: tuple[float, float, float] | None = None   # (breaths per minute, from s, to s)
    cues: tuple[tuple[float, str], ...] = ()          # (s, spoken text)
    command_at_s: tuple[float, ...] = ()              # the spoken command, marked as pressing c does

    def events(self) -> list[tuple[float, str]]:
        """Everything the voice says, in time order (recording time, s)."""
        events = list(self.cues)
        events += [(t, "Raise your hand if you hear me") for t in self.command_at_s]
        if self.pace:
            bpm, start, end = self.pace
            period = 60.0 / bpm
            t = start
            while t + period <= end + 1e-6:
                events += [(round(t, 3), "in"), (round(t + 0.4 * period, 3), "out")]
                t += period
        return sorted(events)


LIE = "Lie on your back two to three metres from the camera, chest in view, and keep still. "
STAND = "Stand three to four metres from the camera, whole body in view. "
TAKES = {take.name: take for take in (
    Take("breathe-12", 75, LIE + "Breathe with the voice.", pace=(12, 0, 75),
         checks=(Check("rate", "breathing", start_s=20, pace_bpm=12), Check("value", "movement", "still", 10))),
    Take("breathe-20", 75, LIE + "Breathe with the voice.", pace=(20, 0, 75),
         checks=(Check("rate", "breathing", start_s=20, pace_bpm=20), Check("value", "movement", "still", 10))),
    Take("breathe-blanket", 75, LIE + "Blanket up to the chin. Breathe with the voice.", pace=(12, 0, 75),
         checks=(Check("rate", "breathing", start_s=20, pace_bpm=12),)),
    Take("breath-hold", 60, LIE + "Breathe with the voice. When it says hold, breathe out and hold; "
         "stop holding whenever you need to.", pace=(12, 0, 20),
         cues=((20, "Breathe out, and hold"), (45, "Breathe normally")),
         checks=(Check("no_rate", "breathing", start_s=32, end_s=45),
                 Check("value", "breathing", "no_motion_detected", 32, 45))),
    Take("eyes-open-closed", 45, LIE + "Face the camera with your eyes open until the voice says close them.",
         cues=((22, "Close your eyes"),),
         checks=(Check("value", "eyes", "open", 5, 20), Check("value", "eyes", "closed", 27))),
    Take("response-yes", 40, LIE + "When the voice gives a command, raise your hand.", command_at_s=(20,),
         checks=(Check("value", "response", "responded", 22, 36),)),
    Take("response-no", 40, LIE + "When the voice gives a command, do not move.", command_at_s=(20,),
         checks=(Check("value", "response", "no_response", 29),)),
    Take("blood-left-arm", 30, LIE + "Fake blood on your left forearm, arm in view.",
         checks=(Check("region", "visible_blood", "left_arm", 5),)),
    Take("clean", 30, LIE + "No fake blood and no red clothing.", checks=(Check("no_blood", "visible_blood", start_s=3),)),
    Take("red-clothing", 30, LIE + "Red or pink clothing, no fake blood.",
         checks=(Check("no_blood", "visible_blood", start_s=3),)),
    Take("led-grid", 30, LIE + "The Arduino LED matrix switched on next to you, no fake blood.",
         checks=(Check("no_blood", "visible_blood", start_s=3),)),
    Take("red-props", 30, LIE + "Red objects next to you, such as a bag, bottle or towel; no fake blood.",
         checks=(Check("no_blood", "visible_blood", start_s=3),)),
    Take("fall-to-mat", 35, STAND + "A thick mat next to you. When the voice says now, go down onto the mat "
         "in one continuous movement, knees then hands then onto your side, lying within two seconds, and "
         "stay lying. Do not pause on your knees, and do not throw yourself down.", cues=((15, "Now"),),
         checks=(Check("value", "fall", "seen", 22), Check("absent", "fall", "seen", 0, 14))),
    Take("lie-down-slowly", 40, STAND + "When the voice says lie down, lie down on the floor slowly.",
         cues=((15, "Lie down slowly"),), checks=(Check("absent", "fall", "seen"),)),
    Take("sit-down", 30, STAND + "A chair behind you. When the voice says sit, sit down on it normally.",
         cues=((15, "Sit down"),), checks=(Check("absent", "fall", "seen"),)),
    Take("walk", 30, STAND + "When the voice says walk, walk slowly across the view and back.",
         cues=((8, "Walk"),), checks=(Check("value", "walking", "seen", 16), Check("absent", "walking", "seen", 0, 7),
                                      Check("absent", "waving", "seen"))),
    Take("wave", 30, STAND + "When the voice says wave, wave your whole arm above your head, straight, from side to side, "
         "about once a second, until the end.",
         cues=((12, "Wave"),), checks=(Check("value", "waving", "seen", 18), Check("absent", "waving", "seen", 0, 11))),
    Take("wave-lying", 35, LIE + "When the voice says wave, raise one arm straight up and swing it from side "
         "to side, about once a second, until the end.",
         cues=((15, "Wave"),), checks=(Check("value", "waving", "seen", 21), Check("absent", "waving", "seen", 0, 14))),
    Take("face-down", 45, "Lie face down two to three metres from the camera, whole body in view, forehead on "
         "your folded arms. When the voice says turn, roll onto your back.", cues=((25, "Turn onto your back"),),
         checks=(Check("value", "face_down", "seen", 8, 24), Check("absent", "face_down", "seen", 32))),
    Take("side-facing-away", 30, "Lie on your side two to three metres from the camera, whole body in view, "
         "your back to the camera.", checks=(Check("absent", "face_down", "seen"),)),
    Take("helper-kneels", 40, LIE + "A second person waits out of view. When the voice says come in, they walk "
         "in and kneel next to you until the end.", cues=((15, "Come in"),),
         checks=(Check("value", "person_nearby", "seen", 25), Check("absent", "person_nearby", "seen", 0, 14))),
    Take("walk-past", 30, LIE + "A second person walks past you, about one metre away, without stopping, "
         "when the voice says go.", cues=((12, "Go"),), checks=(Check("absent", "person_nearby", "seen"),)),
    Take("demo-two", 60, "Two people half lying, propped on their elbows or leaning back, about two metres "
         "apart, two to three metres from the camera, whole bodies in view. Person A keeps the eyes closed and "
         "never moves. Person B keeps the eyes open, raises a hand at the command and, when told, waves one hand "
         "above the head.", command_at_s=(20,),
         cues=((38, "Person B, wave"),),
         checks=(Check("reaches", "response", "responded", 22, 36), Check("reaches", "response", "no_response", 29),
                 Check("reaches", "waving", "seen", 42), Check("absent", "person_nearby", "seen"))),
    Take("pulse-check", 50, "Sit or half lie facing the camera, one and a half to two metres away, face in even "
         "light, no glasses. Rest two fingers on the inside of your wrist, hands in your lap, and keep still. "
         "Count your pulse from start to stop, thirty seconds, then say the number. The count times two is "
         "compared by hand with the camera's estimate.",
         checks=(Check("reaches", "pulse", "estimated", 15),),
         cues=((15, "Start counting"), (45, "Stop. Say your count"))),
    Take("one-arm", 55, LIE + "When the voice says move, keep moving only your right arm until the end.",
         cues=((10, "Move your right arm, keep everything else still"),),
         checks=(Check("value", "limbs", "some_not_moved", 40),)),
    Take("blood-spreading", 60, LIE + "Fake blood on your left forearm, arm in view. When the voice says add, "
         "slowly add more fake blood around the same spot.", cues=((20, "Add more blood, slowly"),),
         checks=(Check("value", "blood_spread", "growing", 45), Check("region", "visible_blood", "left_arm", 5))),
)}


# --- scoring -----------------------------------------------------------------------------------

def score(check: Check, samples: Sequence[tuple[float, dict]]) -> dict:
    """Score 0..1 (None without samples in the window) and the numbers behind it."""
    window = [s for t, s in samples if check.start_s <= t <= check.end_s]
    if not window:
        return {"score": None, "samples": 0}
    sign = check.sign or "visible_blood"
    values = [s.get(sign, {}).get("value") for s in window]
    out: dict = {"samples": len(window),
                 "testable": round(sum(v not in ("not_testable", "not_tested", "unknown", None)
                                       for v in values) / len(window), 2)}
    if check.kind == "rate":
        rates = [s[sign].get("rate_bpm") for s in window if s.get(sign, {}).get("value") == "motion_detected"]
        rates = [r for r in rates if r]
        close = [r for r in rates if abs(r - check.pace_bpm) <= RATE_TOLERANCE_BPM]
        out.update(score=len(close) / len(window), rate_share=round(len(rates) / len(window), 2),
                   rate_median=round(median(rates), 1) if rates else None,
                   error_median=round(median(abs(r - check.pace_bpm) for r in rates), 1) if rates else None,
                   no_motion_share=round(values.count("no_motion_detected") / len(window), 2))
    elif check.kind == "no_rate":
        out.update(score=1 - values.count("motion_detected") / len(window),
                   no_motion_share=round(values.count("no_motion_detected") / len(window), 2))
    elif check.kind in ("value", "absent"):
        share = values.count(check.value) / len(window)
        out.update(score=share if check.kind == "value" else 1 - share)
        rates = sorted(s[sign]["rate_bpm"] for s in window if s.get(sign, {}).get("rate_bpm"))
        if rates:
            out.update(rate_median=round(median(rates), 1),
                       rate_p10_p90=[rates[int(0.1 * (len(rates) - 1))], rates[int(0.9 * (len(rates) - 1))]])
    elif check.kind == "reaches":
        out.update(score=1.0 if check.value in values else 0.0)
    else:
        detected = [s.get(sign, {}) for s in window if s.get(sign, {}).get("value") == "detected"]
        regions = sorted({r for d in detected for r in d.get("regions") or ()})
        if check.kind == "region":
            hits = sum(check.value in (d.get("regions") or ()) for d in detected)
            out.update(score=hits / len(window), regions_seen=regions)
        else:
            out.update(score=1 - len(detected) / len(window), regions_seen=regions)
    out["score"] = round(out["score"], 3)
    return out


def main_track(samples: dict[str, list], seen_s) -> str | None:
    kept = [tid for tid, _ in seen_s.most_common() if samples.get(tid)]
    return kept[0] if kept else None


# --- clips -------------------------------------------------------------------------------------

@dataclass
class Clip:
    name: str
    source: Path
    checks: tuple[Check, ...]
    command_at_s: tuple[float, ...] = ()
    moving_camera: bool = False
    start_s: float = 0.0
    max_s: float = 0.0
    origin: str = "benchmark"


def load_benchmark(path: Path = BENCHMARK) -> list[Clip]:
    import yaml

    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    clips = []
    for item in data.get("clips", []):
        checks = tuple(Check(**{k: (math.inf if k == "end_s" and v is None else v) for k, v in c.items()})
                       for c in item["checks"])
        clips.append(Clip(item["name"], RECORDINGS / item["source"], checks,
                          tuple(item.get("command_at_s", ())), bool(item.get("moving_camera", False)),
                          float(item.get("start_s", 0.0)), float(item.get("max_s", 0.0))))
    return clips


def recorded_takes(folder: Path = TAKES_DIR) -> list[Clip]:
    clips = []
    for sidecar in sorted(folder.glob("*.json")):
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        take = TAKES.get((meta.get("take") or {}).get("name", ""))
        if take is None:
            continue
        video = sidecar.with_name(meta.get("video", sidecar.with_suffix(".mp4").name))
        clips.append(Clip(video.stem, video, take.checks, take.command_at_s, origin=f"take {take.name}"))
    return clips


def run_clip(clip: Clip, settings, collect: Callable) -> dict:
    if not clip.source.exists():
        return {"name": clip.name, "origin": clip.origin, "skipped": f"missing {clip.source.name}"}
    got = collect(str(clip.source), settings, step=0.25, start=clip.start_s, max_s=clip.max_s,
                  command_at=clip.command_at_s, moving_camera=clip.moving_camera)
    track = main_track(got.samples, got.seen_s)
    samples = got.samples.get(track, []) if track else []
    everyone = sorted((item for items in got.samples.values() for item in items), key=lambda item: item[0])
    return {"name": clip.name, "origin": clip.origin, "source": clip.source.name,
            "duration_s": round(got.frames / got.fps, 1), "people": len(got.samples),
            "checks": [{"check": check.label, **score(check, everyone if check.kind == "reaches" else samples)}
                       for check in clip.checks]}


def compare(results: list[dict], previous: dict | None) -> None:
    """Adds ``previous`` and ``regressed`` to each check that the previous run also scored."""
    if not previous:
        return
    before = {(r["name"], c["check"]): c.get("score") for r in previous.get("clips", []) for c in r.get("checks", [])}
    for result in results:
        for check in result.get("checks", []):
            old = before.get((result["name"], check["check"]))
            if old is None or check["score"] is None:
                continue
            check["previous"] = old
            check["regressed"] = check["score"] < old - REGRESSION


def latest_run(out_dir: Path) -> dict | None:
    runs = sorted(out_dir.glob("run-*.json"))
    return json.loads(runs[-1].read_text(encoding="utf-8")) if runs else None


def print_results(results: list[dict]) -> int:
    regressions = 0
    for result in results:
        if "skipped" in result:
            print(f"  -    {result['name']:34s} skipped ({result['skipped']})")
            continue
        for check in result["checks"]:
            value = "  n/a" if check["score"] is None else f"{check['score']:5.2f}"
            extra = []
            if "previous" in check:
                extra.append(f"was {check['previous']:.2f}")
                if check["regressed"]:
                    extra.append("WORSE")
                    regressions += 1
            if check.get("rate_median") is not None:
                extra.append(f"median {check['rate_median']}/min")
            if check.get("regions_seen"):
                extra.append("seen " + ",".join(check["regions_seen"]))
            if check["score"] is None:
                extra.append("no person tracked in the window")
            print(f"  {value} {result['name']:34s} {check['check']:34s} {' | '.join(extra)}")
    return regressions


def list_takes() -> None:
    for take in TAKES.values():
        print(f"{take.name:17s} {take.duration_s:3.0f} s  {take.setup}")
        print(f"{'':23s}checks: {'; '.join(c.label for c in take.checks)}")
    print("\nRecord one:  python -m cv.tools.record --take <name>")


# --- the voice during recording ----------------------------------------------------------------

def say(text: str) -> None:
    """Speak without waiting (macOS ``say``); elsewhere print."""
    if sys.platform == "darwin" and shutil.which("say"):
        subprocess.Popen(["say", "-r", "190", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        print(f"  [voice] {text}")


class Cues:
    """Speaks a take's events at their times and gives the text to show on screen."""

    def __init__(self, take: Take, speak: Callable[[str], None] = say):
        self.take = take
        self.speak = speak
        self.events = take.events()
        self.next = 0
        self.shown = ""

    def update(self, elapsed: float) -> str:
        while self.next < len(self.events) and self.events[self.next][0] <= elapsed:
            text = self.events[self.next][1]
            self.speak(text)
            self.shown = {"in": "BREATHE IN", "out": "BREATHE OUT"}.get(text, text.upper())
            self.next += 1
        return self.shown


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score validation takes and benchmark clips")
    parser.add_argument("--list", action="store_true", help="print the takes and how to record them")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--only", help="score only clips whose name contains this")
    parser.add_argument("--no-benchmark", action="store_true", help="only the recorded takes")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        list_takes()
        return 0
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    from cv.settings import load_settings
    from cv.tools.signs_report import collect

    clips = recorded_takes() + ([] if args.no_benchmark else load_benchmark())
    if args.only:
        clips = [c for c in clips if args.only in c.name]
    if not clips:
        print("Nothing to score: record a take (python -m cv.tools.validate --list) or add cv/eval/benchmark.yaml")
        return 1
    settings = load_settings(args.config)
    out_dir = Path(args.out_dir)
    results = []
    for clip in clips:
        print(f"  ... {clip.name}", file=sys.stderr)
        results.append(run_clip(clip, settings, collect))
    compare(results, latest_run(out_dir))
    print(f"\n=== Validation: {sum('checks' in r for r in results)} clips (score 0..1, higher is better) ===")
    regressions = print_results(results)
    if regressions:
        print(f"\n  {regressions} check(s) dropped more than {REGRESSION} since the previous run")
    if not args.no_save:
        out_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        path = out_dir / f"run-{now.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps({"run_at_utc": iso_utc(now), "clips": results}, indent=2) + "\n",
                        encoding="utf-8")
        print(f"\n  saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
