import json
import socket
import threading
import time

import pytest
import uvicorn

from backend.app.main import create_app
from scripts import live_demo
from scripts.live_demo import (
    FAR_ACTOR, LOCAL_TRACK, LOGITECH, NEAR_ACTOR, PHONE, SOLO_RESPONDER, run, two_actor_scenario,
)


@pytest.fixture(autouse=True)
def deterministic_advisory(monkeypatch):
    monkeypatch.delenv("AEGIS_ADVISORY_URL", raising=False)


def check_summary(summary):
    assert summary["mode"] == "simulation"
    assert summary["cameras"] == {LOGITECH: "calibrated_world", PHONE: "predefined_zones"}
    far, near = summary["canonical_tracks"][LOGITECH], summary["canonical_tracks"][PHONE]
    assert far != near and LOCAL_TRACK in far and LOCAL_TRACK in near
    evidence = summary["route_evidence"]
    assert evidence["before"]["algorithm"] == evidence["after"]["algorithm"] == "astar"
    assert evidence["before"]["map_revision"] != evidence["after"]["map_revision"]
    assert evidence["geometry_changed"] and evidence["blocked_cell_avoided"]
    assert evidence["before"]["waypoints"] != evidence["after"]["waypoints"]
    assert summary["pending_proposal"]["status"] == "pending_approval"
    assert summary["dispatches"] == 1 and summary["real_device_effects"] == 0
    assert summary["assignment_statuses"] == {"completed": 1}
    explanation = summary["grounded_explanation"]["explanation"]
    assert explanation["source"] == "deterministic"
    facts = {fact["id"]: fact for fact in explanation["facts"]}
    assert facts["approval-required"]["category"] == "procedural"
    assert any("zone_estimate" in fact["text"] for fact in explanation["facts"]), (
        "explanation must flag the phone position as a zone estimate")
    counts = summary["event_types"]
    assert counts["simulation.camera_registered"] == 2
    assert counts["simulation.track_associated"] == 2
    assert counts["simulation.proposal_superseded"] >= 1
    assert counts.get("simulation.casualty_created", 0) == 0


def test_two_actor_two_camera_rehearsal_over_real_http():
    summary = run()
    check_summary(summary)
    assert 404 in summary["http_statuses"] and 409 in summary["http_statuses"], "negative probes must run"
    assert set(summary["http_statuses"]) <= {200, 201, 404, 409, 422}


def test_two_available_responders_serve_both_actors():
    summary = run()
    assert summary["control_assignments"] == {
        FAR_ACTOR["casualty"]: "responder-002", NEAR_ACTOR["casualty"]: "responder-001"}


def test_priority_beats_proximity_with_one_responder():
    summary = run()
    evidence = summary["priority_evidence"]
    assert summary["priority_assignments"] == {FAR_ACTOR["casualty"]: SOLO_RESPONDER}
    assert evidence["near_minor"] == NEAR_ACTOR["casualty"]
    assert evidence["near_straight_distance_m"] < evidence["far_route_distance_m"]
    assert evidence["near_reasons"], "the unassigned near casualty must carry a reason"


def test_rehearsal_is_repeatable_on_one_app_and_deterministic():
    first, second = run(), run()
    assert first["route_evidence"]["blocked_grid_cell"] == second["route_evidence"]["blocked_grid_cell"]
    assert (first["route_evidence"]["before"]["waypoints"]
            == second["route_evidence"]["before"]["waypoints"])
    assert (first["route_evidence"]["after"]["waypoints"]
            == second["route_evidence"]["after"]["waypoints"])
    assert first["priority_assignments"] == second["priority_assignments"]


def test_scenario_is_mock_fixture_narrowed_to_two_unassessed_actors():
    scenario = two_actor_scenario()
    assert scenario["id"] == "demo" and scenario["synthetic"] is True
    assert [c["id"] for c in scenario["casualties"]] == ["casualty-actor-a", "casualty-actor-b"]
    assert all(c["medical_assessment"] is None for c in scenario["casualties"])
    assert [(c["position"]["x_m"], c["position"]["y_m"]) for c in scenario["casualties"]] == [
        (14.0, 8.0), (3.0, 2.0)]
    assert [r["id"] for r in scenario["responders"]] == ["responder-001", "responder-002"]


def test_cli_json_output(capsys):
    live_demo.main(["--json"])
    check_summary(json.loads(capsys.readouterr().out))


@pytest.fixture
def server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    instance = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not instance.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert instance.started, "test server did not start"
    yield f"http://127.0.0.1:{port}"
    instance.should_exit = True
    thread.join(timeout=10)


def test_base_url_mode_replays_against_a_running_server(server):
    check_summary(run(server))


def test_prepare_only_leaves_real_components_to_drive_the_state(server):
    summary = live_demo.prepare(server)
    assert summary["cameras"] == {LOGITECH: "pending_cv_registration", PHONE: "predefined_zones"}
    assert summary["casualties"] == ["casualty-actor-a", "casualty-actor-b"]
    with live_demo.UrlClient(server) as client:
        state = client.request("GET", live_demo.PREFIX + "/state").json()
    assert {camera["camera_id"] for camera in state["cameras"]} == {PHONE}
    assert state["tracks"] == [] and state["approvals"] == [] and state["dispatches"] == []
    assert state["assignments"] == []
    assert all(c["medical_assessment"] is None for c in state["scenario"]["casualties"])
    assert {r["status"] for r in state["scenario"]["responders"]} == {"available"}
    types = {event["type"] for event in state_events(server)}
    assert not types & {"simulation.observation_received", "simulation.track_associated",
                        "simulation.device_delivered", "simulation.approval_invalidated"}



def test_prepare_only_allows_explicit_real_calibration_reference(server):
    summary = live_demo.prepare(server, calibration_ref="taped-floor-real-v1")
    assert summary["cameras"] == {LOGITECH: "calibrated_world", PHONE: "predefined_zones"}
    with live_demo.UrlClient(server) as client:
        state = client.request("GET", live_demo.PREFIX + "/state").json()
    cameras = {camera["camera_id"]: camera for camera in state["cameras"]}
    assert cameras[LOGITECH]["calibration_ref"] == "taped-floor-real-v1"



def state_events(server):
    with live_demo.UrlClient(server) as client:
        return client.request("GET", live_demo.PREFIX + "/events?limit=1000").json()["events"]


def test_prepare_only_requires_base_url(capsys):
    with pytest.raises(SystemExit):
        live_demo.main(["--prepare-only"])
    assert "--prepare-only requires --base-url" in capsys.readouterr().err


def test_prepare_only_cli_json(server, capsys):
    live_demo.main(["--base-url", server, "--prepare-only", "--json"])
    assert json.loads(capsys.readouterr().out)["scenario_id"] == "demo"
