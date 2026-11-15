import pytest

from backend.app.simulation.store import SimulationStore
from test_simulation_support import BASE, FRAME, FakeClock, assessment, make_client, reset, state

ZONES = {"mode": "simulation", "actor": "cv-askar", "mapping_mode": "predefined_zones",
         "zones": {"room-2": {"frame_id": FRAME, "x_m": 14.0, "y_m": 8.0}}}


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(clock, monkeypatch):
    monkeypatch.setenv("AEGIS_DEMO_CAMERA_ONLY", "true")
    with make_client(SimulationStore(clock=clock)) as test_client:
        yield test_client


def seen(clock, obs_id, track_id):
    stamp = clock.iso()
    return {"mode": "simulation", "id": obs_id, "observed_at": stamp, "confidence": 0.9,
            "track": {"track_id": track_id, "state": "visible", "first_seen_at": stamp, "last_seen_at": stamp},
            "zone_id": "room-2"}


def test_reset_starts_without_synthetic_casualties_but_keeps_responders(client):
    snapshot = reset(client)
    assert snapshot["scenario"]["casualties"] == []
    assert len(snapshot["scenario"]["responders"]) == 2
    assert state(client)["scenario"]["casualties"] == []          # first load of a fresh store too


def test_casualties_come_from_confirmed_camera_tracks_and_human_priority(client, clock):
    reset(client)
    assert client.put(f"{BASE}/cameras/room-cam-1", json=ZONES).status_code == 200
    for n in (1, 2):
        posted = client.post(f"{BASE}/cameras/room-cam-1/observations", json=seen(clock, f"o{n}", f"t{n}"))
        assert posted.status_code == 201
    tracks = [t["track_id"] for t in state(client)["tracks"]]
    for n, track_id in enumerate(tracks, start=1):
        linked = client.post(f"{BASE}/associations", json={
            "mode": "simulation", "track_id": track_id, "casualty_id": f"casualty-{n:03d}",
            "create_casualty": True, "actor": "operator-1"})
        assert linked.status_code == 201, linked.text
    assessed = client.post(f"{BASE}/casualties/casualty-002/assessment",
                           json=assessment("immediate", clock.iso()))
    assert assessed.status_code in (200, 201), assessed.text
    snapshot = state(client)
    assert [c["id"] for c in snapshot["scenario"]["casualties"]] == ["casualty-001", "casualty-002"]
    assert snapshot["scenario"]["casualties"][1]["medical_assessment"]["severity"] == "immediate"


def test_replan_with_no_casualties_does_not_fail(client):
    reset(client)
    response = client.post(f"{BASE}/replan", json={"mode": "simulation", "actor": "operator-1"})
    assert response.status_code == 201, response.text


def test_default_demo_is_unchanged(clock, monkeypatch):
    monkeypatch.delenv("AEGIS_DEMO_CAMERA_ONLY", raising=False)
    with make_client(SimulationStore(clock=clock)) as test_client:
        assert len(reset(test_client)["scenario"]["casualties"]) == 3
