"""Observed signs over time for a video file: per-person timeline and agreement.

    python -m cv.tools.signs_report --source cv/recordings/sleeping.mp4 \
        --expect movement=still --expect breathing=motion_detected --expect eyes=closed
    python -m cv.tools.signs_report --source clip.mp4 --command-at 4.0 --save-video cv/recordings/out.mp4

Runs the same detector, tracker and ``cv.signs`` as ``cv.main --signs`` on every
frame, timed by the clip (not wall time). Signs are sampled every ``--step``
seconds per track; the report gives, per track, the share of samples with each
value, when each value first appeared, and the value changes. ``--expect
sign=value`` scores agreement on the main track (the one seen longest): the
share of samples with that value, and of the testable samples with it.
``--command-at`` marks a spoken command at that clip time, as pressing c does.

Only numbers are written (``cv/eval/signs/``); ``--save-video`` writes an
annotated copy for review, which must stay out of git (use cv/recordings/).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from cv.clock import iso_utc
from cv.settings import CV_DIR, DEFAULT_CONFIG, load_settings

log = logging.getLogger("cv.signs_report")
OUT_DIR = CV_DIR / "eval" / "signs"
SIGNS = ("posture", "movement", "response", "eyes", "breathing", "visible_blood", "camera_motion")
UNTESTABLE = {"not_testable", "not_tested", "unknown"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observed signs over time for a video file")
    parser.add_argument("--source", required=True, help="video file")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--step", type=float, default=1.0, help="sampling interval per track (s)")
    parser.add_argument("--start", type=float, default=0.0, help="skip to this clip time (s)")
    parser.add_argument("--max-s", type=float, default=0.0, help="stop after this much clip time (0 = all)")
    parser.add_argument("--command-at", type=float, action="append", default=[],
                        help="clip time of a spoken command (repeatable)")
    parser.add_argument("--expect", action="append", default=[], metavar="SIGN=VALUE",
                        help="expected value on the main track, e.g. eyes=closed (repeatable)")
    parser.add_argument("--min-seen-s", type=float, default=2.0, help="ignore tracks seen less than this")
    parser.add_argument("--moving-camera", action="store_true",
                        help="handheld / body-worn clip (config: signs.moving_camera)")
    parser.add_argument("--label", help="name in the report (default: file name)")
    parser.add_argument("--save-video", help="annotated copy for review (keep out of git)")
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    parser.add_argument("--no-save", action="store_true", help="print only")
    return parser


def parse_expect(items: Sequence[str]) -> dict[str, str]:
    expected = {}
    for item in items:
        sign, _, value = item.partition("=")
        if sign not in SIGNS or not value:
            raise ValueError(f"--expect {item!r}: use SIGN=VALUE with SIGN in {', '.join(SIGNS)}")
        expected[sign] = value
    return expected


def summarize(samples: list[tuple[float, dict]], expected: dict[str, str]) -> dict:
    """Value shares, first appearance, changes and expectation agreement for one track."""
    result: dict = {"samples": len(samples), "seen_from_s": round(samples[0][0], 1),
                    "seen_to_s": round(samples[-1][0], 1), "signs": {}}
    for sign in SIGNS:
        values = [(t, s[sign]["value"]) for t, s in samples if sign in s]
        if not values:
            continue
        counts = Counter(v for _, v in values)
        first: dict[str, float] = {}
        changes = []
        previous = None
        for t, value in values:
            first.setdefault(value, round(t, 1))
            if value != previous:
                changes.append([round(t, 1), value])
                previous = value
        entry = {"share_pct": {v: round(100 * n / len(values), 1) for v, n in counts.most_common()},
                 "first_at_s": first, "changes": changes}
        rates = [s[sign].get("rate_bpm") for _, s in samples if sign in s and s[sign].get("rate_bpm")]
        if rates:
            entry["rate_bpm_median"] = sorted(rates)[len(rates) // 2]
        regions = Counter(r for _, s in samples if sign in s for r in s[sign].get("regions", []))
        if regions:
            entry["regions"] = dict(regions.most_common())
        if sign in expected:
            want = expected[sign]
            testable = [v for _, v in values if v not in UNTESTABLE]
            entry["expected"] = want
            entry["agreement_pct"] = round(100 * counts[want] / len(values), 1)
            entry["agreement_testable_pct"] = (round(100 * testable.count(want) / len(testable), 1)
                                               if testable else None)
            entry["testable_pct"] = round(100 * len(testable) / len(values), 1)
        result["signs"][sign] = entry
    return result


@dataclass
class Collected:
    samples: dict[str, list[tuple[float, dict]]]   # track_id -> (clip time, signs snapshot)
    seen_s: Counter                                 # track_id -> seconds detected
    frames: int
    fps: float
    size: tuple[int, int]
    processing_s: float
    eye_model_available: bool


def collect(source: str, settings, *, step: float = 1.0, start: float = 0.0, max_s: float = 0.0,
            command_at: Sequence[float] = (), moving_camera: bool = False,
            save_video: str | None = None) -> Collected:
    """Run detector, tracker and signs on every frame of a clip, timed by the clip."""
    import cv2

    from cv.clock import Timebase
    from cv.detector import Detector
    from cv.monitor import CalloutLayout, LastSeen, build_scene, render
    from cv.signs import SignsEstimator
    from cv.tracker import TrackManager

    settings = replace(settings, signs=replace(settings.signs, enabled=True,
                                               moving_camera=settings.signs.moving_camera or moving_camera))
    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise FileNotFoundError(f"Cannot open {source}")
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    if fps <= 1 or fps > 240:
        fps = 30.0
    width, height = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)), int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if start:
        capture.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
    detector = Detector(settings.detector)
    detector.warmup(width or settings.camera.width, height or settings.camera.height)
    tracker = TrackManager(settings.tracking, "clip")
    timebase = Timebase(monotonic=lambda: 0.0, wall=lambda: datetime(2000, 1, 1, tzinfo=timezone.utc))
    signs = SignsEstimator(settings.signs, timebase.iso, settings.tracking.keypoint_min_conf)
    signs.prepare()
    writer, layout, last_seen = None, CalloutLayout(), LastSeen()
    commands = sorted(command_at)
    samples: dict[str, list[tuple[float, dict]]] = {}
    seen_s: Counter = Counter()
    next_sample: dict[str, float] = {}
    index = 0
    started = time.perf_counter()
    while True:
        ok, image = capture.read()
        if not ok:
            break
        t = start + index / fps
        if max_s and t - start > max_s:
            break
        while commands and commands[0] <= t:
            signs.mark_command(commands.pop(0))
        detections, _ = detector(image)
        tracks = tracker.update(detections, t)
        signs.update(image, tracks, t)
        for track in tracks:
            if not track.detected or track.track_id not in signs.states:
                continue
            seen_s[track.track_id] += 1.0 / fps
            if t >= next_sample.get(track.track_id, 0.0):
                next_sample[track.track_id] = t + step
                samples.setdefault(track.track_id, []).append((t, signs.snapshot(track, t)))
        if save_video:
            canvas = render(image, tracks, build_scene(tracks, signs, t, last_seen), layout, [f"t = {t:.1f} s"],
                            keypoint_min_conf=settings.tracking.keypoint_min_conf)
            if writer is None:
                writer = cv2.VideoWriter(save_video, cv2.VideoWriter_fourcc(*"mp4v"), fps,
                                         (canvas.shape[1], canvas.shape[0]))
            writer.write(canvas)
        index += 1
    capture.release()
    if writer is not None:
        writer.release()
    if not index:
        raise ValueError(f"No frames read from {source}")
    return Collected(samples, seen_s, index, fps, (width, height), time.perf_counter() - started,
                     signs.eyes.scorer.available)


def run(args: argparse.Namespace) -> dict:
    expected = parse_expect(args.expect)
    got = collect(args.source, load_settings(args.config), step=args.step, start=args.start, max_s=args.max_s,
                  command_at=args.command_at, moving_camera=args.moving_camera, save_video=args.save_video)
    kept = [tid for tid, s in got.seen_s.most_common() if s >= args.min_seen_s and got.samples.get(tid)]
    tracks_out = {tid.split("-")[-1]: {"seen_s": round(got.seen_s[tid], 1), **summarize(got.samples[tid], expected)}
                  for tid in kept}
    main_track = kept[0].split("-")[-1] if kept else None
    return {
        "run_at_utc": iso_utc(datetime.now(timezone.utc)),
        "label": args.label or Path(args.source).stem,
        "source": Path(args.source).name,
        "frames": got.frames,
        "duration_s": round(got.frames / got.fps, 1),
        "fps": round(got.fps, 2),
        "size": list(got.size),
        "commands_at_s": sorted(args.command_at),
        "expected": expected,
        "main_track": main_track,
        "tracks": tracks_out,
        "tracks_ignored": len(got.seen_s) - len(kept),
        "eye_model_available": got.eye_model_available,
        "processing_fps": round(got.frames / got.processing_s, 1) if got.processing_s > 0 else None,
    }


def print_report(result: dict) -> None:
    print(f"\n=== Signs: {result['label']} ({result['source']}, {result['duration_s']} s, "
          f"{result['size'][0]}x{result['size'][1]}, {result['processing_fps']} FPS) ===")
    if not result["tracks"]:
        print("  no person tracked long enough")
    for track_id, track in result["tracks"].items():
        marker = " (main)" if track_id == result["main_track"] else ""
        print(f"  {track_id}{marker}: seen {track['seen_s']} s ({track['seen_from_s']}-{track['seen_to_s']} s)")
        for sign, entry in track["signs"].items():
            shares = ", ".join(f"{v} {p:.0f}%" for v, p in entry["share_pct"].items())
            extra = ""
            if "rate_bpm_median" in entry:
                extra += f" | rate {entry['rate_bpm_median']}/min"
            if "regions" in entry:
                extra += " | regions " + ",".join(entry["regions"])
            if "expected" in entry:
                testable = entry["agreement_testable_pct"]
                extra += (f" | expected {entry['expected']}: {entry['agreement_pct']:.0f}% of samples, "
                          f"{'n/a' if testable is None else f'{testable:.0f}%'} of testable")
            print(f"    {sign:13s} {shares}{extra}")
    if result["tracks_ignored"]:
        print(f"  ({result['tracks_ignored']} short tracks ignored)")


def save(result: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = result["run_at_utc"].replace(":", "").replace("-", "").split(".")[0]
    path = out_dir / f"{result['label']}-{stamp}.json"
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print_report(result)
    if not args.no_save:
        print(f"\n  saved {save(result, Path(args.out_dir))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
