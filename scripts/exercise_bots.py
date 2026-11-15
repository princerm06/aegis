"""Simulated people for a training exercise (SIMULATION ONLY).

Posts a handful of scripted "people who need help" to the simulation backend as
a clearly labelled synthetic camera, so the Command Center has casualties spread
across the map without needing actors in front of a real camera.

    uv run python -m scripts.exercise_bots --base-url http://127.0.0.1:8000
    uv run python -m scripts.exercise_bots --base-url http://127.0.0.1:8000 --count 6 --link

What it does, using only the existing camera API:

* registers a camera called ``exercise-sim-1`` (``calibrated_world``, calibration
  reference ``synthetic-exercise``), so every sighting is attributable to this
  script and never looks like a real camera;
* once a second, posts one sighting per simulated person at a fixed position
  with a fixed set of observed signs. The tracks stay ``visible`` and do not
  move, so a proposal is not replaced while the operator is approving it;
* with ``--link`` it also creates one casualty per person (the same request the
  dashboard's "Link" button sends). Without it, the operator links them.

It never assesses severity, approves, dispatches or sends device events: those
stay human decisions. Stop it with Ctrl+C; the sightings then go stale by
themselves after the backend's freshness window.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

CAMERA_ID = "exercise-sim-1"
ACTOR = "exercise-bots"
FRAME_ID = "demo-floor-1"
SIGNS_SCHEMA = "aegis-cv-signs/0.1"


@dataclass(frozen=True)
class Bot:
    """One simulated person: where they are and what a camera would observe."""

    name: str
    x_m: float
    y_m: float
    posture: str        # standing | sitting | lying | unknown
    movement: str       # moving | still | not_testable
    response: str       # responded | no_response | not_tested
    eyes: str | None = None            # open | closed | not_testable
    visible_blood: str | None = None   # detected | not_detected | not_testable
    waving: str | None = None          # seen | not_seen | not_testable
    confidence: float = 0.9
    note: str = ""


# Positions are spread over the 24 x 20 m demo floor, away from the responders'
# start points. The signs are what the operator reads before setting a priority.
BOTS: tuple[Bot, ...] = (
    Bot("collapsed", 15.0, 8.5, "lying", "still", "no_response", eyes="closed",
        note="lying still, eyes closed, no response to a spoken command"),
    Bot("bleeding", 21.0, 12.0, "lying", "moving", "responded", eyes="open", visible_blood="detected",
        note="lying, moving, visible blood"),
    Bot("trapped", 5.5, 12.0, "sitting", "moving", "responded", eyes="open", waving="seen",
        note="sitting up and waving for help"),
    Bot("dazed", 7.5, 15.0, "sitting", "still", "responded", eyes="open",
        note="sitting, still, responds"),
    Bot("walking", 20.0, 6.0, "standing", "moving", "responded", eyes="open",
        note="standing and walking"),
    Bot("unresponsive", 14.5, 11.0, "lying", "still", "no_response", eyes="not_testable",
        note="lying still, face not visible"),
    Bot("injured", 9.5, 2.5, "lying", "moving", "responded", eyes="open", visible_blood="not_detected",
        note="lying, moving, no blood seen"),
    Bot("bystander", 16.5, 3.5, "standing", "still", "responded", eyes="open",
        note="standing still"),
)


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Api:
    def __init__(self, base_url: str, scenario: str, timeout: float) -> None:
        self.base = f"{base_url.rstrip('/')}/api/v1/simulations/{scenario}"
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict | str]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method,
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
                return response.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")[:400]


def sign(value: str, confidence: float, reason: str) -> dict:
    return {"value": value, "confidence": confidence, "reason": reason}


def signs_for(bot: Bot) -> dict:
    why = "scripted exercise value"
    signs: dict = {
        "schema": SIGNS_SCHEMA,
        "window_s": 5.0,
        "posture": sign(bot.posture, bot.confidence, why),
        "movement": sign(bot.movement, bot.confidence, why),
        "response": sign(bot.response, bot.confidence, why),
    }
    if bot.eyes:
        signs["eyes"] = sign(bot.eyes, bot.confidence, why)
    if bot.visible_blood:
        signs["visible_blood"] = sign(bot.visible_blood, bot.confidence, why)
    if bot.waving:
        signs["waving"] = sign(bot.waving, bot.confidence, why)
    return signs


def observation(bot: Bot, session: str, index: int, sequence: int, first_seen: float, now: float,
                with_signs: bool) -> dict:
    body: dict = {
        "mode": "simulation",
        "id": f"{session}-o{sequence:06d}-{bot.name}",
        "observed_at": iso(now),
        "confidence": bot.confidence,
        "track": {
            "track_id": f"ex-{bot.name}",  # stable, so restarting the script continues the same person
            "state": "visible",
            "first_seen_at": iso(first_seen),
            "last_seen_at": iso(now),
        },
        "position": {"frame_id": FRAME_ID, "x_m": bot.x_m, "y_m": bot.y_m},
    }
    if with_signs:
        body["signs"] = signs_for(bot)
    return body


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulated people for a training exercise (SIMULATION ONLY).")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--scenario", default="demo")
    parser.add_argument("--count", type=int, default=4, help=f"how many simulated people (1-{len(BOTS)})")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between sightings")
    parser.add_argument("--duration", type=float, default=0.0, help="stop after N seconds (0 = until Ctrl+C)")
    parser.add_argument("--link", action="store_true",
                        help="also create one casualty per person, as the dashboard's Link button does")
    parser.add_argument("--no-signs", action="store_true", help="send positions only, no observed signs")
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)  # progress is visible when output goes to a log

    count = max(1, min(args.count, len(BOTS)))
    bots = BOTS[:count]
    api = Api(args.base_url, args.scenario, args.timeout)
    session = "s" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")

    print(f"=== AEGIS exercise bots (SIMULATION ONLY): {count} simulated people -> {api.base} ===")
    status, body = api.call("PUT", f"/cameras/{CAMERA_ID}", {
        "mode": "simulation", "actor": ACTOR, "mapping_mode": "calibrated_world",
        "calibration_ref": "synthetic-exercise"})
    if status >= 300:
        print(f"ERROR: could not register the synthetic camera (HTTP {status}): {body}", file=sys.stderr)
        return 1
    for index, bot in enumerate(bots):
        print(f"  {index + 1}. {bot.name:13s} at ({bot.x_m:4.1f}, {bot.y_m:4.1f})  {bot.note}")

    started = time.time()
    first_seen = started
    linked: set[int] = set()
    sequence = 0
    sent = failed = 0
    with_signs = not args.no_signs
    try:
        while True:
            now = time.time()
            sequence += 1
            for index, bot in enumerate(bots):
                status, body = api.call("POST", f"/cameras/{CAMERA_ID}/observations",
                                        observation(bot, session, index, sequence, first_seen, now, with_signs))
                if status < 300:
                    sent += 1
                    continue
                failed += 1
                if with_signs and status == 422:
                    # An older backend may not accept the signs block: carry on with positions only.
                    print("  the backend rejected the signs block (422); sending positions only", file=sys.stderr)
                    with_signs = False
                elif failed <= 3:
                    print(f"  sighting rejected (HTTP {status}): {body}", file=sys.stderr)
            if args.link and len(linked) < count and sequence >= 2:
                snapshot_status, snapshot = api.call("GET", "/state")
                tracks = snapshot.get("tracks", []) if snapshot_status < 300 and isinstance(snapshot, dict) else []
                for index, bot in enumerate(bots):
                    if index in linked:
                        continue
                    suffix = f":{CAMERA_ID}:ex-{bot.name}"
                    track = next((t for t in tracks if str(t.get("track_id", "")).endswith(suffix)), None)
                    if not track:
                        continue
                    if track.get("casualty_id"):
                        linked.add(index)
                        continue
                    status, body = api.call("POST", "/associations", {
                        "mode": "simulation", "track_id": track["track_id"],
                        "casualty_id": f"casualty-{101 + index}", "create_casualty": True, "actor": ACTOR})
                    if status < 300:
                        linked.add(index)
                        print(f"  linked {bot.name} -> casualty-{101 + index}")
                    elif status != 409:
                        print(f"  could not link {bot.name} (HTTP {status}): {body}", file=sys.stderr)
            if sequence % 15 == 0:
                print(f"  {int(now - started):4d}s  sightings sent {sent}, rejected {failed}")
            if args.duration and now - started >= args.duration:
                break
            time.sleep(max(0.05, args.interval - (time.time() - now)))
    except KeyboardInterrupt:
        pass
    print(f"Stopped. Sightings sent {sent}, rejected {failed}. They go stale by themselves.")
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
