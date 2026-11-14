"""Perception payloads against the backend's own models and routes.

Skipped unless the backend dependencies are installed
(``pip install -r cv/requirements-dev.txt``).
"""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pydantic")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import create_app  # noqa: E402
from backend.app.models import CVObservation  # noqa: E402
from backend.app.simulation.models import CameraObservation, CameraRegistration  # noqa: E402
from cv.clock import Timebase  # noqa: E402
from cv.publisher import Publisher  # noqa: E402
from cv.settings import PublisherSettings  # noqa: E402
from cv.tracker import TrackReport  # noqa: E402


FIRST_SEEN: dict[str, float] = {}


@pytest.fixture(autouse=True)
def _fresh_tracks():
    FIRST_SEEN.clear()


def make_publisher(transport, endpoint="camera"):
    timebase = Timebase()
    publisher = Publisher(PublisherSettings(endpoint=endpoint, heartbeat_s=0.0),
                          frame_id="demo-floor-1", calibration_ref="cv-demo-floor-1-test",
                          session_id="s1", timebase=timebase, transport=transport)
    return publisher, timebase


def report(now, state="visible", x=4.0, y=8.0, conf=0.85, track="s1-t1", last_seen=None,
           first_seen=None):
    return TrackReport(track_id=track, state=state, observed_t=now,
                       first_seen_t=FIRST_SEEN.setdefault(track, now) if first_seen is None else first_seen,
                       last_seen_t=now if last_seen is None else last_seen,
                       x_m=x, y_m=y, confidence=conf, capture_t=now)


def collect():
    calls = []

    def transport(method, path, body):
        calls.append((method, path, body))
        return (200 if method == "PUT" else 201), {}

    return calls, transport


def test_camera_payloads_validate_against_backend_models():
    calls, transport = collect()
    publisher, timebase = make_publisher(transport)
    now = timebase.now()
    publisher.submit([report(now)], now)
    publisher.step()
    publisher.submit([report(now + 1, state="occluded", last_seen=now)], now + 1)
    publisher.step()
    (_, _, registration), *posts = calls
    CameraRegistration.model_validate(registration)
    for _, _, body in posts:
        CameraObservation.model_validate(body)


def test_legacy_payload_validates_as_contract_cv_observation():
    calls, transport = collect()
    publisher, timebase = make_publisher(transport, endpoint="legacy")
    publisher.submit([report(timebase.now())])
    publisher.step()
    observation = CVObservation.model_validate(calls[0][2])
    assert observation.track.track_id == "logitech-1-s1-t1"


def test_full_track_lifecycle_is_accepted_by_the_running_backend():
    client = TestClient(create_app())
    assert client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"}).status_code == 201

    def transport(method, path, body):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    publisher, timebase = make_publisher(transport)
    t0 = timebase.now() - 3.0
    steps = [
        [report(t0, x=4.0, y=8.0), report(t0, track="s1-t2", x=12.0, y=5.0)],
        [report(t0 + 0.5, x=4.2, y=8.1, conf=0.7), report(t0 + 0.5, track="s1-t2", x=12.0, y=5.0)],
        [report(t0 + 1.0, state="occluded", x=9.9, y=9.9, last_seen=t0 + 0.6)],  # values replaced
        [report(t0 + 1.5, state="occluded", last_seen=t0 + 0.6)],
        [report(t0 + 2.0, state="lost", last_seen=t0 + 0.6)],
    ]
    for reports in steps:
        publisher.submit(reports, reports[0].observed_t)
        publisher.step()

    assert publisher.stats.rejected == 0, publisher.stats.last_error
    assert publisher.stats.accepted == 7
    state = client.get("/api/v1/simulations/demo/state").json()
    tracks = {t["camera"]["local_track_id"]: t for t in state["tracks"]}
    assert tracks["s1-t1"]["latest_observation"]["track"]["state"] == "lost"
    assert tracks["s1-t1"]["latest_observation"]["position"]["x_m"] == 4.2
    assert tracks["s1-t2"]["camera"]["calibration_ref"] == "cv-demo-floor-1-test"
    assert tracks["s1-t2"]["camera"]["location_basis"] == "calibrated_world"
    assert all(t["casualty_id"] is None for t in state["tracks"])  # association is human-only


def test_phone_zone_camera_lifecycle_is_accepted_by_the_running_backend():
    from cv.zones import parse_zones

    client = TestClient(create_app())
    client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})
    # Prince's --prepare-only registration; ours is identical, so no 409.
    prepared = client.put("/api/v1/simulations/demo/cameras/phone-1", json={
        "mode": "simulation", "actor": "live-demo-operator", "mapping_mode": "predefined_zones",
        "zones": {"zone-b": {"frame_id": "demo-floor-1", "x_m": 3, "y_m": 2}}})
    assert prepared.status_code == 200

    def transport(method, path, body):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    timebase = Timebase()
    zones = parse_zones([{"id": "zone-b", "x_m": 3.0, "y_m": 2.0}], "demo-floor-1")
    publisher = Publisher(PublisherSettings(camera_id="phone-1", heartbeat_s=0.0),
                          frame_id="demo-floor-1", calibration_ref=None, session_id="s1",
                          timebase=timebase, transport=transport, zones=zones)
    CameraRegistration.model_validate(publisher.registration_body())
    t0 = timebase.now() - 2.0

    def zone_report(t, state="visible", last_seen=None):
        return TrackReport(track_id="s1-t1", state=state, observed_t=t, first_seen_t=t0,
                           last_seen_t=t if last_seen is None else last_seen, x_m=3.0, y_m=2.0,
                           confidence=0.8, capture_t=t, zone_id="zone-b")

    for item in [zone_report(t0), zone_report(t0 + 0.5),
                 zone_report(t0 + 1.0, "occluded", last_seen=t0 + 0.5),
                 zone_report(t0 + 1.5, "lost", last_seen=t0 + 0.5)]:
        publisher.submit([item], item.observed_t)
        publisher.step()
    assert publisher.stats.rejected == 0, publisher.stats.last_error
    assert publisher.stats.accepted == 4
    (track,) = client.get("/api/v1/simulations/demo/state").json()["tracks"]
    assert track["track_id"] == "sim-camera:7:phone-1:s1-t1"
    assert track["camera"]["location_basis"] == "zone_estimate"
    assert track["camera"]["zone_id"] == "zone-b"
    assert track["latest_observation"]["track"]["state"] == "lost"


def test_backend_reset_clears_the_camera_and_publisher_recovers():
    client = TestClient(create_app())
    client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})

    def transport(method, path, body):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    publisher, timebase = make_publisher(transport)
    now = timebase.now() - 1.0
    publisher.submit([report(now)], now)
    publisher.step()
    client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})
    publisher.submit([report(now + 0.5, x=5.0)], now + 0.5)
    publisher.step()   # 404 -> marks unregistered
    publisher.step()   # re-registers and resends
    assert publisher.stats.registrations == 2 and publisher.stats.accepted == 2


def test_operator_priority_and_dispatch_read_back_for_the_responder_view():
    from cv.assignments import marks_from_state

    client = TestClient(create_app())
    client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})
    base, operator, keys = "/api/v1/simulations/demo", "test-operator", iter(range(100))

    def post(path, body):
        response = client.post(base + path, json=body, headers={"Idempotency-Key": f"marks-{next(keys)}"})
        assert response.status_code in (200, 201), response.text
        return response.json()

    def transport(method, path, body):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    publisher, timebase = make_publisher(transport)
    now = timebase.now()
    publisher.submit([report(now)], now)
    publisher.step()
    state = client.get(base + "/state").json()
    assert marks_from_state(state, "logitech-1") == {}            # seen, not linked: no band
    (track,) = state["tracks"]
    casualty = state["scenario"]["casualties"][0]["id"]
    post("/associations", {"track_id": track["track_id"], "casualty_id": casualty, "create_casualty": False,
                           "actor": operator})
    state = client.get(base + "/state").json()
    assert marks_from_state(state, "logitech-1")["s1-t1"].priority is None
    assert marks_from_state(state, "phone-1") == {}               # another camera's tracks only

    post(f"/casualties/{casualty}/assessment", {"severity": "immediate", "source": "human",
                                                "assessed_by": operator, "assessed_at": timebase.iso(now)})
    state = post("/replan", {"actor": operator, "reason": "test", "solver": "ortools"})["state"]
    mark = marks_from_state(state, "logitech-1")["s1-t1"]
    assert (mark.casualty_id, mark.priority, mark.status, mark.waiting) == (casualty, "immediate", "proposed", False)
    assert mark.responder

    proposal = next(p for p in state["proposals"] if p["id"] == state["current_proposal_id"])
    approval = post(f"/proposals/{proposal['id']}/decision", {
        "proposal_revision": proposal["revision"], "decision": "approve", "actor": operator,
        "mode": "simulation"})["result"]
    body = post("/dispatches", {"proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
                                "approval_id": approval["id"], "actor": operator, "mode": "simulation"})
    mark = marks_from_state(body["state"], "logitech-1")["s1-t1"]
    assert mark.status == "dispatched" and mark.waiting
    assignment = next(a for a in body["state"]["assignments"] if a["casualty_id"] == casualty)
    state = post(f"/assignments/{assignment['id']}/simulate-device-event", {"kind": "acknowledged"})["state"]
    mark = marks_from_state(state, "logitech-1")["s1-t1"]
    assert mark.status == "acknowledged" and not mark.waiting


def test_real_signs_with_history_are_accepted_and_the_pulse_stays_on_the_camera():
    from dataclasses import replace

    from cv.tests.test_signs import body_image, make_estimator, make_track

    estimator, track = make_estimator(), make_track()
    image = body_image((300, 200, 340, 250))
    for i in range(400):                                    # 40 s: every history sign becomes testable
        estimator.update(image, [track], i / 10)
    estimator.states["s1-t1"].history.changes.append(("eyes", "open", "closed", 39.0))
    signs = estimator.snapshot(track, 40.0)
    assert "pulse" in signs and signs["changes"]

    client = TestClient(create_app())
    client.post("/api/v1/simulations/demo/reset", json={"mode": "simulation"})

    def transport(method, path, body):
        response = client.request(method, path, json=body)
        return response.status_code, response.json()

    timebase = Timebase()
    publisher = Publisher(PublisherSettings(endpoint="camera", heartbeat_s=0.0, send_signs=True, send_history=True),
                          frame_id="demo-floor-1", calibration_ref="cv-demo-floor-1-test",
                          session_id="s1", timebase=timebase, transport=transport)
    now = timebase.now()
    publisher.submit([replace(report(now), signs=signs)], now)
    publisher.step()
    assert publisher.stats.rejected == 0, publisher.stats.last_error
    (stored,) = [t["latest_observation"]["signs"] for t in client.get("/api/v1/simulations/demo/state").json()["tracks"]]
    for name in ("fall", "walking", "limbs", "waving", "face_down", "person_nearby", "blood_spread"):
        assert stored[name]["value"] == signs[name]["value"], name
    assert stored["changes"][0]["from"] == "open"
    assert "pulse" not in stored
