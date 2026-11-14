"""Two actors, two cameras: a SIMULATION rehearsal through the real HTTP routes.

Everything here is synthetic. The "Logitech" and "phone" cameras are reports we
post ourselves, and the delivered/acknowledged/completed device events are
backend-built surrogates for the Arduino LED and physical button. Nothing in
this script observes a real actor or drives real hardware.

Default: an isolated in-process app via ``TestClient``. With ``--base-url`` the
same sequence is replayed against a running server (it resets that server's
``demo`` scenario) so a dashboard can follow along.

Run from the repository root:
``uv run python -m scripts.live_demo [--base-url http://127.0.0.1:8000 [--prepare-only]] [--json]``
"""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from json import dumps
import math
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

from backend.app.services.scenarios import DEMO_SCENARIO_PATH

SCENARIO_ID = "demo"
PREFIX = f"/api/v1/simulations/{SCENARIO_ID}"
OPERATOR = "live-demo-operator"
FRAME = "demo-floor-1"

LOGITECH = "logitech-1"
PHONE = "phone-1"
CALIBRATION_REF = "external-calibration-demo-001"
LOCAL_TRACK = "person-1"

FAR_ACTOR = {"casualty": "casualty-actor-a", "camera": LOGITECH, "severity": "immediate", "xy": (14.0, 8.0)}
NEAR_ACTOR = {"casualty": "casualty-actor-b", "camera": PHONE, "severity": "minor", "xy": (3.0, 2.0)}
PHONE_ZONE = "zone-b"
OFFLINE_RESPONDER = "responder-002"
SOLO_RESPONDER = "responder-001"


def position(x, y):
    return {"frame_id": FRAME, "x_m": x, "y_m": y}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def grid_cell(grid, point):
    return (
        math.floor((point["x_m"] - grid["origin"]["x_m"]) / grid["resolution_m"]),
        math.floor((point["y_m"] - grid["origin"]["y_m"]) / grid["resolution_m"]),
    )


def endpoint_cells(grid, point):
    column, row = grid_cell(grid, point)
    offset_x = (point["x_m"] - grid["origin"]["x_m"]) / grid["resolution_m"]
    offset_y = (point["y_m"] - grid["origin"]["y_m"]) / grid["resolution_m"]
    columns = [column, column - 1] if offset_x.is_integer() else [column]
    rows = [row, row - 1] if offset_y.is_integer() else [row]
    return {(c, r) for c in columns for r in rows}


def traversed_cells(grid, route):
    cells = set()
    for first, second in zip(route["waypoints"], route["waypoints"][1:]):
        distance = math.hypot(second["x_m"] - first["x_m"], second["y_m"] - first["y_m"])
        steps = max(1, math.ceil(distance / (grid["resolution_m"] / 4)))
        for step in range(steps + 1):
            fraction = step / steps
            cells.add(grid_cell(grid, {
                "x_m": first["x_m"] + fraction * (second["x_m"] - first["x_m"]),
                "y_m": first["y_m"] + fraction * (second["y_m"] - first["y_m"]),
            }))
    return cells


def two_actor_scenario():
    """The mock fixture, narrowed to two unassessed casualties (far A, near B)."""
    scenario = json.loads(Path(DEMO_SCENARIO_PATH).read_text(encoding="utf-8"))
    scenario["name"] = "Synthetic two-actor two-camera rehearsal"
    scenario["casualties"] = [
        {"id": actor["casualty"], "position": position(*actor["xy"]), "medical_assessment": None}
        for actor in (FAR_ACTOR, NEAR_ACTOR)
    ]
    return scenario


class UrlClient:
    """Minimal stdlib HTTP client with the slice of the TestClient API this script uses."""

    class Response:
        def __init__(self, status_code, text):
            self.status_code, self.text = status_code, text
            self.is_success = 200 <= status_code < 300

        def json(self):
            return json.loads(self.text)

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def request(self, method, path, json=None, headers=None):
        data = None if json is None else dumps(json).encode()
        request = urllib.request.Request(self.base_url + path, data=data, method=method,
                                         headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=30) as reply:
                return self.Response(reply.status, reply.read().decode())
        except urllib.error.HTTPError as error:
            return self.Response(error.code, error.read().decode())


class Rehearsal:
    def __init__(self, client, narrate=None):
        self.client = client
        self.narrate = narrate or (lambda line: None)
        self.run_id = uuid.uuid4().hex[:8]
        self.calls = []
        self.first_seen = {}
        self.sequence = 0

    def step(self, title):
        self.narrate(f"\n== {title}")

    def note(self, text):
        self.narrate(f"   {text}")

    def request(self, method, path, payload=None, expected=(200, 201), keyed=True):
        self.sequence += 1
        headers = {"Idempotency-Key": f"live-{self.run_id}-{self.sequence}"} if keyed else {}
        response = self.client.request(method, PREFIX + path, json=payload, headers=headers)
        self.calls.append({"method": method, "path": PREFIX + path, "status": response.status_code})
        assert response.status_code in expected, (
            f"{method} {PREFIX + path}: expected {expected}, got {response.status_code}: {response.text}")
        body = response.json()
        if response.is_success:
            assert body["mode"] == "simulation", "response escaped simulation boundary"
        return body

    def state(self):
        return self.request("GET", "/state", keyed=False)


    def observation_payload(self, actor, instant):
        """Camera-local report: the same local track ID is used by both cameras."""
        first_seen = self.first_seen.setdefault(actor["camera"], instant)
        payload = {
            "mode": "simulation", "id": f"{actor['camera']}-report-{self.sequence + 1}",
            "observed_at": instant, "confidence": 0.9,
            "track": {"track_id": LOCAL_TRACK, "state": "visible",
                      "first_seen_at": first_seen, "last_seen_at": instant},
        }
        if actor["camera"] == LOGITECH:
            payload["position"] = position(*actor["xy"])
        else:
            payload["zone_id"] = PHONE_ZONE
        return payload

    def observe(self, actor):
        body = self.request("POST", f"/cameras/{actor['camera']}/observations",
                            self.observation_payload(actor, now_iso()))
        return body["result"]["track_id"], body

    def refresh(self, *actors):
        """Camera heartbeat: freshness is 30 s, so live rehearsals must keep reporting."""
        for actor in actors:
            self.observe(actor)


    def replan(self, reason):
        body = self.request("POST", "/replan", {"actor": OPERATOR, "reason": reason, "solver": "ortools"})
        return body["state"]

    @staticmethod
    def pending(state):
        proposal_id = state["current_proposal_id"]
        assert proposal_id is not None, "expected a pending proposal"
        proposal = next(item for item in state["proposals"] if item["id"] == proposal_id)
        assert proposal["status"] == "pending_approval"
        assert proposal["engine"] == "ortools", "rehearsal must exercise OR-Tools, not a fallback"
        for assignment in proposal["assignments"]:
            assert assignment["route"] is not None and assignment["route"]["algorithm"] == "astar"
            assert len(assignment["route"]["waypoints"]) >= 2
        return proposal

    def approve(self, proposal):
        payload = {"proposal_revision": proposal["revision"], "decision": "approve",
                   "actor": OPERATOR, "mode": "simulation"}
        path = f"/proposals/{proposal['id']}/decision"
        key = f"live-{self.run_id}-approve-{proposal['id']}"
        response = self.client.request("POST", PREFIX + path, json=payload, headers={"Idempotency-Key": key})
        assert response.status_code == 201, response.text
        replay = self.client.request("POST", PREFIX + path, json=payload, headers={"Idempotency-Key": key})
        self.calls += [{"method": "POST", "path": PREFIX + path, "status": response.status_code},
                       {"method": "POST", "path": PREFIX + path, "status": replay.status_code}]
        assert replay.json()["duplicate"] is True and replay.json()["result"]["id"] == response.json()["result"]["id"]
        approval = response.json()["result"]
        assert approval["status"] == "active" and approval["authenticated"] is False
        assert approval["proposal_revision"] == proposal["revision"]
        return approval

    def dispatch(self, proposal, approval):
        payload = {"proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
                   "approval_id": approval["id"], "actor": OPERATOR, "mode": "simulation"}
        body = self.request("POST", "/dispatches", payload)
        assert body["result"]["dispatch"]["real_device_effect"] is False
        return body["result"]["dispatch"], body["result"]["assignments"], body["state"]

    def device_event(self, assignment_id, kind):
        body = self.request("POST", f"/assignments/{assignment_id}/simulate-device-event", {"kind": kind})
        record = next(item for item in body["state"]["assignments"] if item["id"] == assignment_id)
        assert record["status"] == kind
        return body["state"]


def assigned(proposal):
    return {item["casualty_id"]: item["responder_id"] for item in proposal["assignments"]}


def run(base_url=None, narrate=None):
    with (UrlClient(base_url) if base_url else _in_process_client()) as client:
        return _rehearse(Rehearsal(client, narrate))


def _in_process_client():
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    return TestClient(create_app())


def reset_scene(rehearsal):
    """Step 1: two unassessed actors, two available responders, nothing approved."""
    rehearsal.step("1. Reset SIMULATION scene: two unassessed actors, two available responders")
    state = rehearsal.request("POST", "/reset", {
        "mode": "simulation", "scenario": two_actor_scenario(), "add_demo_map": True})["state"]
    assert [c["id"] for c in state["scenario"]["casualties"]] == [FAR_ACTOR["casualty"], NEAR_ACTOR["casualty"]]
    assert all(c["medical_assessment"] is None for c in state["scenario"]["casualties"])
    assert {r["id"]: r["status"] for r in state["scenario"]["responders"]} == {
        "responder-001": "available", "responder-002": "available"}
    assert state["cameras"] == [] and state["tracks"] == [] and not state["assignments"]
    rehearsal.note("fixture positions are trusted until a track is associated, so a first proposal "
                   "already exists; it is superseded later and nothing is approved")
    return state


def register_cameras(rehearsal, *, register_logitech=True, calibration_ref=CALIBRATION_REF):
    """Step 2: the two camera mappings; reports themselves come from the CV components."""
    rehearsal.step("2. Register both camera mappings")
    logitech = (rehearsal.request("PUT", f"/cameras/{LOGITECH}", {
        "mode": "simulation", "actor": OPERATOR, "mapping_mode": "calibrated_world",
        "calibration_ref": calibration_ref})["result"] if register_logitech else None)
    phone = rehearsal.request("PUT", f"/cameras/{PHONE}", {
        "mode": "simulation", "actor": OPERATOR, "mapping_mode": "predefined_zones",
        "zones": {PHONE_ZONE: position(*NEAR_ACTOR["xy"])}})["result"]
    if logitech is not None:
        assert logitech["mapping_mode"] == "calibrated_world" and logitech["calibration_ref"] == calibration_ref
    assert phone["mapping_mode"] == "predefined_zones" and phone["zones"][PHONE_ZONE]["x_m"] == NEAR_ACTOR["xy"][0]
    rehearsal.note(f"{LOGITECH}: " + (f"calibrated_world via {calibration_ref}" if logitech else "registration left to live CV") + f"; {PHONE}: predefined_zones {PHONE_ZONE}")
    return {LOGITECH: logitech["mapping_mode"] if logitech else "pending_cv_registration", PHONE: phone["mapping_mode"]}


def prepare(base_url, narrate=None, *, calibration_ref=None):
    """Reset and register mappings on a running server, then stop for live components to drive."""
    with UrlClient(base_url) as client:
        rehearsal = Rehearsal(client, narrate)
        state = reset_scene(rehearsal)
        cameras = register_cameras(rehearsal, register_logitech=calibration_ref is not None,
                                   calibration_ref=calibration_ref)
        return {"mode": state["mode"], "scenario_id": state["scenario_id"], "scene_revision": state["scene_revision"],
                "casualties": [c["id"] for c in state["scenario"]["casualties"]], "cameras": cameras,
                "http_requests": len(rehearsal.calls)}


def _rehearse(rehearsal):
    started = time.monotonic()
    far, near = FAR_ACTOR, NEAR_ACTOR
    away, close = far["casualty"], near["casualty"]

    reset_scene(rehearsal)
    register_cameras(rehearsal)

    rehearsal.step("3. Observe: same local track ID on both cameras must stay two separate tracks")
    far_track, _ = rehearsal.observe(far)
    near_track, _ = rehearsal.observe(near)
    assert far_track != near_track, "equal local track IDs merged across cameras"
    assert LOCAL_TRACK in far_track and LOCAL_TRACK in near_track
    state = rehearsal.state()
    tracks = {track["track_id"]: track for track in state["tracks"]}
    assert set(tracks) == {far_track, near_track}
    assert tracks[far_track]["camera"]["camera_id"] == LOGITECH
    assert tracks[far_track]["camera"]["location_basis"] == "calibrated_world"
    assert tracks[near_track]["camera"]["camera_id"] == PHONE
    assert tracks[near_track]["camera"]["location_basis"] == "zone_estimate"
    assert tracks[near_track]["camera"]["zone_id"] == PHONE_ZONE
    assert all(track["camera"]["local_track_id"] == LOCAL_TRACK for track in tracks.values())
    assert all(track["casualty_id"] is None for track in tracks.values()), "observation auto-associated"
    assert len(state["scenario"]["casualties"]) == 2, "a detection must never create a casualty"
    rehearsal.note(f"canonical tracks (from the response, not guessed): {far_track} / {near_track}")

    rehearsal.step("4. Operator associates each canonical track explicitly (no cross-camera matching)")
    rehearsal.request("POST", "/associations", {
        "track_id": LOCAL_TRACK, "casualty_id": away, "create_casualty": False, "actor": OPERATOR,
    }, expected=(404,))
    for track_id, actor in ((far_track, far), (near_track, near)):
        result = rehearsal.request("POST", "/associations", {
            "track_id": track_id, "casualty_id": actor["casualty"],
            "create_casualty": False, "actor": OPERATOR})["result"]
        assert result["casualty_id"] == actor["casualty"] and result["created"] is False
    state = rehearsal.state()
    assert {t["track_id"]: t["casualty_id"] for t in state["tracks"]} == {far_track: away, near_track: close}
    assert not state["assignments"] and not state["dispatches"], "nothing may dispatch without human approval"
    rehearsal.note("both casualties remain unassessed; no approval or dispatch exists yet")

    rehearsal.step("5. Human assessments (shared fields): A immediate, B minor")
    for actor in (far, near):
        response = rehearsal.request("POST", f"/casualties/{actor['casualty']}/assessment", {
            "severity": actor["severity"], "source": "human",
            "assessed_by": OPERATOR, "assessed_at": now_iso()})
        casualty = next(c for c in response["state"]["scenario"]["casualties"] if c["id"] == actor["casualty"])
        assert casualty["medical_assessment"]["source"] == "human"
        assert casualty["medical_assessment"]["severity"] == actor["severity"]

    rehearsal.step("6. Control: both responders available, so OR-Tools serves both actors")
    rehearsal.refresh(far, near)
    control = rehearsal.pending(rehearsal.replan("control: both responders available"))
    assert set(assigned(control)) == {away, close}, "two available responders must handle both casualties"
    assert len(set(assigned(control).values())) == 2
    rehearsal.note(f"assignments: {assigned(control)}")

    rehearsal.step("7. Priority over proximity: take the second responder offline, replan")
    rehearsal.request("PATCH", f"/responders/{OFFLINE_RESPONDER}", {"status": "offline"})
    rehearsal.refresh(far, near)
    first = rehearsal.pending(rehearsal.replan("one responder offline: priority over proximity"))
    assert first["id"] != control["id"] and first["revision"] > control["revision"]
    assert assigned(first) == {away: SOLO_RESPONDER}, "far immediate must outrank near minor"
    assert first["unassigned_casualty_ids"] == [close]
    route_a = first["assignments"][0]["route"]
    state = rehearsal.state()
    responders = {r["id"]: r for r in state["scenario"]["responders"]}
    near_straight = math.hypot(responders[SOLO_RESPONDER]["position"]["x_m"] - near["xy"][0],
                               responders[SOLO_RESPONDER]["position"]["y_m"] - near["xy"][1])
    assert route_a["distance_m"] > near_straight, "scenario no longer makes the near casualty cheaper"
    rehearsal.note("one responder: allocation priority decides (immediate=2 points, minor=0)")
    rehearsal.note(f"{SOLO_RESPONDER} -> {away}: {route_a['distance_m']:.1f} m route; "
                   f"{close} was only {near_straight:.1f} m away, yet stays unassigned")
    for reason in first["reasons"].get(close, []):
        rehearsal.note(f"{close} reason: {reason}")

    rehearsal.step("8. Approve the exact revision, dispatch, SYNTHETIC delivered + button-surrogate ack")
    rehearsal.request("POST", f"/proposals/{first['id']}/decision", {
        "proposal_revision": first["revision"] + 1, "decision": "approve",
        "actor": OPERATOR, "mode": "simulation"}, expected=(409, 422))
    approval = rehearsal.approve(first)
    dispatch, assignments, state = rehearsal.dispatch(first, approval)
    assert len(assignments) == 1 and assignments[0]["casualty_id"] == away
    rehearsal.note(f"approved revision {approval['proposal_revision']} as {approval['id']} "
                   f"(authenticated={approval['authenticated']}); dispatch {dispatch['id']}")
    assignment = assignments[0]
    assert next(a for a in state["approvals"] if a["id"] == approval["id"])["status"] == "consumed"
    rehearsal.device_event(assignment["id"], "delivered")
    state = rehearsal.device_event(assignment["id"], "acknowledged")
    assert next(r for r in state["scenario"]["responders"] if r["id"] == SOLO_RESPONDER)["status"] == "busy"
    assert not any(p["status"] == "pending_approval" and close in assigned(p) for p in state["proposals"]), (
        "no responder is free, so B must not be proposed")
    rehearsal.note(f"{SOLO_RESPONDER} is busy with {away}; real_device_effect={dispatch['real_device_effect']}")

    rehearsal.step("9. Complete A (surrogate), B becomes assignable to the freed responder")
    rehearsal.refresh(near)
    state = rehearsal.device_event(assignment["id"], "completed")
    assert away in state["resolved_casualty_ids"]
    assert next(r for r in state["scenario"]["responders"] if r["id"] == SOLO_RESPONDER)["status"] == "available"
    rehearsal.request("PATCH", f"/responders/{SOLO_RESPONDER}", {
        "position": position(*far["xy"])})
    rehearsal.refresh(near)
    pending = rehearsal.pending(rehearsal.replan("A complete, responder freed"))
    assert assigned(pending) == {close: SOLO_RESPONDER}
    before = pending["assignments"][0]["route"]
    assert len(before["waypoints"]) > 2, "B's route needs an interior waypoint to block"
    rehearsal.note(f"pending revision {pending['revision']}: {SOLO_RESPONDER} -> {close}, "
                   f"{len(before['waypoints'])} waypoints, {before['distance_m']:.1f} m")

    rehearsal.step("10. Block an actual interior waypoint of B's pending route (new map revision)")
    state = rehearsal.state()
    disrupted = state["scenario"]["scene_map"]
    grid = disrupted["occupancy_grid"]
    protected = set()
    for entity in [*state["scenario"]["casualties"], *state["scenario"]["responders"]]:
        protected |= endpoint_cells(grid, entity["position"])
    for endpoint in (before["waypoints"][0], before["waypoints"][-1]):
        protected |= endpoint_cells(grid, endpoint)
    candidates = []
    for point in before["waypoints"][1:-1]:
        column, row = grid_cell(grid, point)
        if (0 < column < grid["width"] - 1 and 0 < row < grid["height"] - 1
                and (column, row) not in protected and grid["cells"][row * grid["width"] + column] == 0):
            candidates.append((column, row))
    assert candidates, "route has no blockable interior waypoint away from every endpoint"
    blocked = candidates[len(candidates) // 2]
    assert blocked in traversed_cells(grid, before)
    grid["cells"][blocked[1] * grid["width"] + blocked[0]] = 100
    disrupted["revision"] = f"live-demo-blocked-{rehearsal.run_id}"
    rehearsal.refresh(near)
    response = rehearsal.request("PUT", "/map", disrupted)
    state = response["state"]
    assert state["scenario"]["scene_map"]["revision"] == disrupted["revision"]
    stale = next(p for p in state["proposals"] if p["id"] == pending["id"])
    assert stale["status"] == "superseded", "a map change must invalidate the pending proposal"
    rehearsal.request("POST", f"/proposals/{pending['id']}/decision", {
        "proposal_revision": pending["revision"], "decision": "approve",
        "actor": OPERATOR, "mode": "simulation"}, expected=(409,))
    rehearsal.note(f"cell {blocked} blocked; old proposal {pending['id']} superseded ({stale['closed_reason']})")

    rehearsal.step("11. Replan: route changes, stays feasible, and needs a NEW human approval")
    replanned = rehearsal.pending(rehearsal.replan("map revision blocked an interior waypoint"))
    assert replanned["id"] != pending["id"] and replanned["revision"] > pending["revision"]
    assert assigned(replanned) == {close: SOLO_RESPONDER}, "blocked cell made B infeasible"
    after = replanned["assignments"][0]["route"]
    assert after["map_revision"] == disrupted["revision"] != before["map_revision"]
    assert after["waypoints"] != before["waypoints"], "route did not change"
    assert blocked not in traversed_cells(grid, after), "new route still crosses the blocked cell"
    assert after["distance_m"] >= before["distance_m"] - 1e-8
    state = rehearsal.state()
    assert not any(a["proposal_id"] == replanned["id"] for a in state["approvals"]), "approval carried over"
    assert replanned["approval_id"] is None
    rehearsal.note(f"new revision {replanned['revision']}: {before['distance_m']:.1f} m -> {after['distance_m']:.1f} m, "
                   "awaiting human approval (left pending on purpose)")

    rehearsal.step("12. Grounded explanation and event evidence")
    explanation = rehearsal.request("GET", f"/proposals/{replanned['id']}/explanation", keyed=False)
    assert explanation["proposal_id"] == replanned["id"] and explanation["proposal_revision"] == replanned["revision"]
    facts = {fact["id"]: fact for fact in explanation["explanation"]["facts"]}
    assert facts["approval-required"]["category"] == "procedural"
    assert facts[f"assignment:{close}"]["category"] == "estimated"
    assert facts[f"assessment:{close}"]["category"] == "human_confirmed"
    for fact in explanation["explanation"]["facts"]:
        rehearsal.note(f"[{fact['category']}] {fact['text']}")
    events = rehearsal.request("GET", "/events?limit=1000", keyed=False)["events"]
    sequences = [event["sequence"] for event in events]
    assert sequences == sorted(set(sequences)), "events must be strictly sequenced"
    assert all(event["mode"] == "simulation" for event in events)
    counts = Counter(event["type"] for event in events)
    assert counts["simulation.camera_registered"] == 2
    assert counts["simulation.observation_received"] >= 2
    assert counts["simulation.track_associated"] == 2
    assert counts["simulation.casualty_created"] == 0, "no casualty may come from a detection"
    assert counts["simulation.device_delivered"] == counts["simulation.device_acknowledged"] == 1
    assert counts["simulation.device_completed"] == 1
    assert counts["simulation.map_updated"] == 1
    assert any(event["type"] == "simulation.proposal_superseded" and event["payload"]["proposal_id"] == pending["id"]
               and event["payload"]["reason"] == f"map:{disrupted['revision']}" for event in events)
    camera_events = [event["payload"]["camera"] for event in events if event["type"] == "simulation.observation_received"]
    assert {camera["camera_id"] for camera in camera_events} == {LOGITECH, PHONE}
    final = rehearsal.state()
    assert all(d["real_device_effect"] is False for d in final["dispatches"])

    return {
        "mode": final["mode"], "scenario_id": final["scenario_id"],
        "cameras": {camera["camera_id"]: camera["mapping_mode"] for camera in final["cameras"]},
        "canonical_tracks": {LOGITECH: far_track, PHONE: near_track},
        "control_assignments": assigned(control),
        "priority_assignments": assigned(first),
        "priority_evidence": {
            "far_immediate": away, "near_minor": close,
            "far_route_distance_m": route_a["distance_m"], "near_straight_distance_m": near_straight,
            "near_reasons": first["reasons"].get(close, []),
        },
        "dispatches": len(final["dispatches"]),
        "assignment_statuses": dict(Counter(a["status"] for a in final["assignments"])),
        "real_device_effects": sum(d["real_device_effect"] for d in final["dispatches"]),
        "route_evidence": {
            "casualty_id": close, "responder_id": SOLO_RESPONDER, "blocked_grid_cell": list(blocked),
            "before": before, "after": after,
            "geometry_changed": before["waypoints"] != after["waypoints"],
            "blocked_cell_avoided": blocked not in traversed_cells(grid, after),
        },
        "pending_proposal": {"id": replanned["id"], "revision": replanned["revision"],
                             "status": replanned["status"], "approval_required": True},
        "grounded_explanation": explanation,
        "event_types": dict(counts),
        "http_requests": len(rehearsal.calls),
        "http_statuses": dict(Counter(call["status"] for call in rehearsal.calls)),
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base-url", help="replay against a running server, e.g. http://127.0.0.1:8000 "
                                           "(resets its 'demo' scenario)")
    parser.add_argument("--prepare-only", action="store_true",
                        help="with --base-url: reset the two unassessed actors and register the two camera "
                             "mappings only, then exit so real components can drive the same state")
    parser.add_argument("--calibration-ref", help="with --prepare-only: explicitly register the Logitech camera using this external calibration reference; otherwise live CV registers it")
    parser.add_argument("--json", action="store_true", help="print only the JSON summary")
    arguments = parser.parse_args(argv)
    if arguments.prepare_only and not arguments.base_url:
        parser.error("--prepare-only requires --base-url")
    if arguments.calibration_ref is not None and not arguments.prepare_only:
        parser.error("--calibration-ref requires --prepare-only")
    narrate = None if arguments.json else print
    if narrate:
        print("SIMULATION ONLY: synthetic cameras and device events; no real actors, camera or Arduino effects.")
    if arguments.prepare_only:
        summary = prepare(arguments.base_url, narrate, calibration_ref=arguments.calibration_ref)
        if arguments.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"\nPrepared {summary['scenario_id']} (scene revision {summary['scene_revision']}): "
                  "no observations, assessments, approvals or device events were sent.")
        return
    summary = run(arguments.base_url, narrate)
    if arguments.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"\nOK in {summary['elapsed_seconds']}s: {summary['http_requests']} HTTP requests, "
              f"real_device_effects={summary['real_device_effects']}. "
              f"Pending approval left for revision {summary['pending_proposal']['revision']}.")


if __name__ == "__main__":
    sys.exit(main())
