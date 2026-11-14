"""Record the camera for replay and evaluation.

    python -m cv.tools.record --scenario lying_two --duration 30
    python -m cv.tools.record --config cv/config/phone.yaml --scenario phone_zone
    python -m cv.tools.record --take breathe-12     # scripted take with a known answer (cv.tools.validate)

Writes ``cv/recordings/<scenario>-<UTC time>.mp4`` and a ``.json`` sidecar with
the measured FPS, per-frame times, camera, mapping and the floor calibration
in use, so a replay behaves like the live run:

    python -m cv.main --source cv/recordings/<name>.mp4 [--loop]
    python -m cv.tools.evaluate --source cv/recordings/<name>.mp4 --truth <truth.yaml>

Recordings are local only (git-ignored). Never commit captured imagery.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from cv.camera import Camera, camera_override
from cv.clock import iso_utc
from cv.recording import write_sidecar
from cv.settings import CV_DIR, DEFAULT_CONFIG, load_settings, resolve

WINDOW = "AEGIS record (q / Esc to stop)"
FPS_PROBE_S = 1.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record the camera for replay and evaluation")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--camera", help="camera index or name, e.g. 0 or C922 (overrides config)")
    parser.add_argument("--camera-id", help="camera ID stored with the recording (overrides config)")
    parser.add_argument("--homography", help="calibration to store with the recording (overrides config)")
    parser.add_argument("--scenario", help="label, also used in the file name (default: unlabeled / the take)")
    parser.add_argument("--take", help="validation take, e.g. breathe-12 (list: python -m cv.tools.validate --list)")
    parser.add_argument("--duration", type=float, help="seconds to record (0 = until q; default 30 / the take)")
    parser.add_argument("--countdown", type=float,
                        help="seconds of preview before recording starts (default 0, 15 for a take: the setup is read aloud)")
    parser.add_argument("--output", help="video path (default cv/recordings/<scenario>-<time>.mp4)")
    parser.add_argument("--notes", default="", help="free text stored in the sidecar")
    parser.add_argument("--no-display", action="store_true")
    return parser


def mapping_info(settings, homography: str | None = None) -> dict:
    mapping = settings.mapping
    info = {"mode": mapping.mode, "frame_id": mapping.frame_id, "calibration": None, "zones": None}
    if mapping.mode == "zones":
        info["zones"] = list(mapping.zones)
        return info
    path = Path(homography) if homography else resolve(mapping.homography)
    if path.exists():
        from cv.homography import Calibration

        info["calibration"] = Calibration.load(path).to_dict()
    else:
        print(f"WARNING: no calibration at {path}; the recording will need --homography on replay.")
    return info


def main(argv: Sequence[str] | None = None) -> int:
    import cv2

    args = build_parser().parse_args(argv)
    take = cues = None
    if args.take:
        from cv.tools.validate import TAKES, TAKES_DIR, Cues, say

        if args.take not in TAKES:
            raise RuntimeError(f"unknown take {args.take!r}; takes: {', '.join(TAKES)}")
        take = TAKES[args.take]
        cues = Cues(take, speak=say)
    scenario = args.scenario or (take.name if take else "unlabeled")
    duration = args.duration if args.duration is not None else (take.duration_s if take else 30.0)
    countdown = args.countdown if args.countdown is not None else (15.0 if take else 0.0)
    settings = load_settings(args.config)
    settings = replace(settings, camera=camera_override(settings.camera, args.camera))
    camera_id = args.camera_id or settings.publisher.camera_id
    started_wall = datetime.now(timezone.utc)
    folder = TAKES_DIR if take else CV_DIR / "recordings"
    output = Path(args.output) if args.output else (
        folder / f"{scenario}-{started_wall.strftime('%Y%m%dT%H%M%SZ')}.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    mapping = mapping_info(settings, args.homography)

    camera = Camera(settings.camera)
    writer = None
    size = None
    buffered: list = []
    times: list[float] = []
    first_t = None
    countdown_until = None
    try:
        while True:
            frame = camera.read()
            if frame is None:
                if camera.reconnects >= 3 and not times:
                    print("ERROR: camera not available. Check the index and macOS camera "
                          "permission for this terminal app.", file=sys.stderr)
                    return 2
                continue
            if countdown_until is None:
                countdown_until = frame.t + max(0.0, countdown)
                if take:
                    print(f"{take.name}: {take.setup}")
                    say(f"{take.setup} Recording starts in {countdown:.0f} seconds.")
            if frame.t < countdown_until:
                if not args.no_display and not preview(cv2, frame.image,
                                                       f"starting in {countdown_until - frame.t:.0f} s"):
                    return 1
                continue
            if first_t is None:
                first_t = frame.t
                size = (frame.image.shape[1], frame.image.shape[0])
                if take and not take.events():
                    say("Recording")
                print(f"Recording {size[0]}x{size[1]} from {camera.label} to {output}")
            image = frame.image
            if (image.shape[1], image.shape[0]) != size:
                image = cv2.resize(image, size)
            elapsed = frame.t - first_t
            times.append(round(elapsed, 4))
            if writer is None:
                buffered.append(image)
                if elapsed >= FPS_PROBE_S:
                    writer = open_writer(cv2, output, measured_fps(times), size)
                    for item in buffered:
                        writer.write(item)
                    buffered.clear()
            else:
                writer.write(image)
            cue = cues.update(elapsed) if cues else ""
            if not args.no_display and not preview(cv2, frame.image, f"REC {elapsed:5.1f} s", cue):
                break
            if duration and elapsed >= duration:
                if take:
                    say("Done")
                break
    except KeyboardInterrupt:
        pass
    finally:
        camera.release()
        if not args.no_display:
            cv2.destroyAllWindows()

    if not times:
        print("Nothing recorded.", file=sys.stderr)
        return 1
    if writer is None:
        writer = open_writer(cv2, output, measured_fps(times) or settings.camera.fps, size)
        for item in buffered:
            writer.write(item)
    writer.release()
    fps = measured_fps(times) or float(settings.camera.fps)
    sidecar = write_sidecar(output, {
        "video": output.name,
        "scenario": scenario,
        "take": {"name": take.name, "events": take.events()} if take else None,
        "notes": args.notes,
        "started_at_utc": iso_utc(started_wall),
        "camera": {"index": camera.device_index, "name": camera.device_name, "camera_id": camera_id,
                   "width": size[0], "height": size[1], "requested_fps": settings.camera.fps,
                   "reconnects": camera.reconnects},
        "frames": len(times),
        "duration_s": times[-1],
        "fps": round(fps, 2),
        "frame_times_s": times,
        "mapping": mapping,
    })
    print(f"Saved {len(times)} frames, {times[-1]:.1f} s at {fps:.1f} FPS measured "
          f"(camera reconnects {camera.reconnects})")
    print(f"  video   {output}\n  sidecar {sidecar}")
    print(f"Replay:   python -m cv.main --source {output}")
    return 0


def measured_fps(times: list[float]) -> float | None:
    if len(times) < 2 or times[-1] <= 0:
        return None
    return (len(times) - 1) / times[-1]


def open_writer(cv2, path: Path, fps: float | None, size):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), float(fps or 30.0), size)
    if not writer.isOpened():
        raise RuntimeError(f"Cannot write {path}")
    return writer


def preview(cv2, image, label: str, cue: str = "") -> bool:
    canvas = image.copy()
    cv2.circle(canvas, (24, 26), 9, (0, 0, 230), -1)
    cv2.putText(canvas, label, (42, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 230), 2, cv2.LINE_AA)
    if cue:
        cv2.putText(canvas, cue, (24, canvas.shape[0] - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (0, 0, 0), 8,
                    cv2.LINE_AA)
        cv2.putText(canvas, cue, (24, canvas.shape[0] - 30), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (255, 255, 255), 3,
                    cv2.LINE_AA)
    cv2.imshow(WINDOW, canvas)
    return cv2.waitKey(1) & 0xFF not in (ord("q"), 27)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
