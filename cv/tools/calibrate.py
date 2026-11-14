"""Floor calibration: click reference points on a frozen frame, enter their
real-world coordinates in meters, save the homography.

    python -m cv.tools.calibrate --camera 0

Live view: SPACE freezes the frame (q quits).
Frozen frame: left-click to add a point, right-click or Backspace to undo,
r to reset, Enter when done (4 minimum; 5-6 give a real error estimate).
Then type each point's "x y" in meters in the terminal.

Enter coordinates in the shared scenario frame (``--frame-id``), +x right/east,
+y up/north. ``--offset X Y`` is added to every entered point, so a tape-measured
local square can be placed at a known spot of the scene map.

Before saving, the staged area (hull of the points) is checked against the
running backend's scene map: it must lie inside the map on walkable cells.
``--no-map-check`` skips this; with ``--yes`` a failed check does not save.

Non-interactive (scripts / tests):
    python -m cv.tools.calibrate --image frame.png \
        --points "100,600;1180,600;1000,300;280,300" --world "0,0;3,0;3,3;0,3" --yes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

import numpy as np

from cv.homography import Calibration, CalibrationError, build_calibration
from cv.settings import DEFAULT_CONFIG, load_settings, resolve

WINDOW = "AEGIS calibration"


def parse_pairs(text: str) -> list[tuple[float, float]]:
    pairs = []
    for chunk in text.replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.replace(",", " ").split()
        if len(parts) != 2:
            raise ValueError(f"Expected 'a,b' but got {chunk!r}")
        pairs.append((float(parts[0]), float(parts[1])))
    return pairs


def grab_frame(args: argparse.Namespace, settings) -> np.ndarray | None:
    import cv2

    if args.image:
        image = cv2.imread(args.image)
        if image is None:
            print(f"ERROR: cannot read {args.image}", file=sys.stderr)
        return image

    from cv.camera import Camera

    camera = Camera(settings.camera)
    print("Live view: SPACE to freeze the frame, q to quit.")
    frozen = None
    try:
        while frozen is None:
            frame = camera.read()
            if frame is None:
                if camera.status == "reconnecting" and camera.reconnects >= 3:
                    print("ERROR: camera not available. Check the index and macOS camera "
                          "permission for this terminal app.", file=sys.stderr)
                    return None
                continue
            preview = frame.image.copy()
            cv2.putText(preview, "SPACE = freeze, q = quit", (12, 30), cv2.FONT_HERSHEY_SIMPLEX,
                        0.8, (0, 255, 255), 2, cv2.LINE_AA)
            cv2.imshow(WINDOW, preview)
            key = cv2.waitKey(1) & 0xFF
            if key == ord(" "):
                frozen = frame.image.copy()
            elif key in (ord("q"), 27):
                return None
    finally:
        camera.release()
    return frozen


def draw_points(image: np.ndarray, points: Sequence[tuple[float, float]]) -> np.ndarray:
    import cv2

    canvas = image.copy()
    for i, (u, v) in enumerate(points, start=1):
        cv2.circle(canvas, (int(u), int(v)), 6, (0, 255, 255), -1)
        cv2.putText(canvas, str(i), (int(u) + 8, int(v) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (0, 0, 0), 4, cv2.LINE_AA)
        cv2.putText(canvas, str(i), (int(u) + 8, int(v) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.9,
                    (0, 255, 255), 2, cv2.LINE_AA)
    cv2.putText(canvas, f"{len(points)} points | click=add  right/backspace=undo  r=reset  Enter=done",
                (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2, cv2.LINE_AA)
    return canvas


def click_points(image: np.ndarray) -> list[tuple[float, float]] | None:
    import cv2

    points: list[tuple[float, float]] = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((float(x), float(y)))
        elif event == cv2.EVENT_RBUTTONDOWN and points:
            points.pop()

    cv2.namedWindow(WINDOW)
    cv2.setMouseCallback(WINDOW, on_mouse)
    while True:
        cv2.imshow(WINDOW, draw_points(image, points))
        key = cv2.waitKey(30) & 0xFF
        if key in (13, 10) and len(points) >= 4:
            return points
        if key in (8, 127) and points:
            points.pop()
        elif key == ord("r"):
            points.clear()
        elif key in (ord("q"), 27):
            return None


def prompt_world(count: int) -> list[tuple[float, float]]:
    print("\nEnter the real-world floor position of each numbered point, in meters, as 'x y'.")
    world = []
    for i in range(1, count + 1):
        while True:
            raw = input(f"  point {i}: ").strip()
            try:
                world.append(parse_pairs(raw)[0])
                break
            except (ValueError, IndexError):
                print("    please type two numbers, e.g. 1.5 0")
    return world


def draw_verification(image: np.ndarray, calibration: Calibration, step_m: float = 0.5) -> np.ndarray:
    """Project a floor grid back into the image; lines should sit on the floor."""
    import cv2

    canvas = draw_points(image, [tuple(p) for p in calibration.image_points])
    lo = calibration.world_points.min(axis=0)
    hi = calibration.world_points.max(axis=0)
    xs = np.arange(np.floor(lo[0] / step_m) * step_m, hi[0] + 1e-9, step_m)
    ys = np.arange(np.floor(lo[1] / step_m) * step_m, hi[1] + 1e-9, step_m)
    segments = [[(x, lo[1]), (x, hi[1])] for x in xs] + [[(lo[0], y), (hi[0], y)] for y in ys]
    for start, end in segments:
        line = np.linspace(start, end, 20)
        pixels, valid = calibration.world_to_image(line)
        pixels = pixels[valid].astype(np.int32)
        if len(pixels) >= 2:
            cv2.polylines(canvas, [pixels], False, (255, 160, 0), 1, cv2.LINE_AA)
    for (u, v), (x, y) in zip(calibration.image_points, calibration.world_points):
        cv2.putText(canvas, f"({x:g},{y:g})", (int(u) + 8, int(v) + 22), cv2.FONT_HERSHEY_SIMPLEX,
                    0.55, (255, 255, 255), 2, cv2.LINE_AA)
    return canvas


def report(calibration: Calibration) -> None:
    from cv.homography import reprojection_errors

    errors = reprojection_errors(calibration.homography, calibration.image_points, calibration.world_points)
    print("\n point |   image (px)    |  world (m)     | reprojection error")
    for i, ((u, v), (x, y), err) in enumerate(zip(calibration.image_points, calibration.world_points, errors), 1):
        print(f" {i:5d} | {u:7.1f} {v:7.1f} | {x:6.2f} {y:6.2f} | {err * 100:6.2f} cm")
    print(f"\n RMS {calibration.rms_error_m * 100:.2f} cm | max {calibration.max_error_m * 100:.2f} cm "
          f"| frame {calibration.frame_id} | {calibration.calibration_ref}")
    if len(calibration.image_points) == 4:
        print(" NOTE: with exactly 4 points the fit is exact, so the error is 0 by construction.\n"
              "       Add a 5th/6th point (or run the mapping evaluation) for a real accuracy check.")


def map_check(calibration: Calibration, base_url: str, scenario_id: str) -> bool | None:
    """True/False = walkable or not; None = could not check (no backend or no map)."""
    from cv.scene_map import check_area, describe, fetch_scene_map

    try:
        scene_map = fetch_scene_map(base_url, scenario_id)
    except Exception as error:
        print(f"\n Map check skipped: cannot read the {scenario_id!r} scene map from {base_url} "
              f"({type(error).__name__}). Start the backend to verify the staged area.")
        return None
    if not scene_map:
        print(f"\n Map check skipped: scenario {scenario_id!r} has no scene map "
              "(reset it with add_demo_map: true).")
        return None
    if scene_map.get("frame_id") != calibration.frame_id:
        print(f"\n NOT WALKABLE: the map uses frame {scene_map.get('frame_id')!r}, "
              f"the calibration {calibration.frame_id!r}.")
        return False
    check = check_area(scene_map, calibration.world_points)
    print("\n " + describe(check))
    return check.walkable


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AEGIS floor calibration")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--camera", help="camera index or name, e.g. 0 or C922 (overrides config)")
    parser.add_argument("--image", help="calibrate on an image file instead of the live camera")
    parser.add_argument("--points", help='image points "u,v;u,v;..." (skips clicking)')
    parser.add_argument("--world", help='world points in meters "x,y;x,y;..." (skips prompts)')
    parser.add_argument("--offset", type=float, nargs=2, default=(0.0, 0.0), metavar=("X", "Y"),
                        help="added to every world point")
    parser.add_argument("--frame-id", help="scene frame (default from config mapping.frame_id)")
    parser.add_argument("--output", help="default: config mapping.homography")
    parser.add_argument("--backend-url", help="backend for the map check (default: config)")
    parser.add_argument("--scenario", help="scenario for the map check (default: config)")
    parser.add_argument("--no-map-check", action="store_true",
                        help="do not check the staged area against the scene map")
    parser.add_argument("--yes", action="store_true", help="save without asking / no windows")
    args = parser.parse_args(argv)

    settings = load_settings(args.config)
    if args.camera is not None:
        from dataclasses import replace

        from cv.camera import camera_override

        settings = replace(settings, camera=camera_override(settings.camera, args.camera))
    frame_id = args.frame_id or settings.mapping.frame_id
    output = Path(args.output) if args.output else resolve(settings.mapping.homography)
    interactive = not args.yes

    image = grab_frame(args, settings)
    if image is None:
        return 2
    height, width = image.shape[:2]

    try:
        image_points = parse_pairs(args.points) if args.points else click_points(image)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    if image_points is None:
        print("Cancelled.")
        return 1
    if interactive:
        import cv2

        cv2.imshow(WINDOW, draw_points(image, image_points))
        cv2.waitKey(1)
    try:
        world_points = parse_pairs(args.world) if args.world else prompt_world(len(image_points))
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    world_points = [(x + args.offset[0], y + args.offset[1]) for x, y in world_points]

    try:
        calibration = build_calibration(image_points, world_points, frame_id, (width, height))
    except CalibrationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    report(calibration)
    walkable = None
    if not args.no_map_check:
        walkable = map_check(calibration, args.backend_url or settings.publisher.base_url,
                             args.scenario or settings.publisher.scenario_id)
        if walkable is False and not interactive:
            print("Not saved: the staged area is not walkable on the scene map "
                  "(fix --offset / the points, or pass --no-map-check).", file=sys.stderr)
            return 3

    if interactive:
        import cv2

        cv2.imshow(WINDOW, draw_verification(image, calibration))
        print("\nCheck the window: the blue 0.5 m grid should lie flat on the floor.")
        cv2.waitKey(1)
        warning = "The area is NOT walkable. " if walkable is False else ""
        answer = input(f"{warning}Save to {output}? [y/N] ").strip().lower()
        cv2.destroyAllWindows()
        if answer != "y":
            print("Not saved.")
            return 1
    calibration.save(output)
    print(f"Saved {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
