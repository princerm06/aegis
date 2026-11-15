import pytest

from backend.app.simulation.store import SimulationStore
from test_simulation_support import BASE, FRAME, FakeClock, make_client, observation, reset, state


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def client(clock):
    with make_client(SimulationStore(clock=clock)) as test_client:
        reset(test_client)
        yield test_client


def position(x, y, frame=FRAME):
    return {"frame_id": frame, "x_m": x, "y_m": y}


CALIBRATED = {"mode": "simulation", "actor": "operator-1", "mapping_mode": "calibrated_world",
              "calibration_ref": "lab-calibration-2026-11-13"}
ZONES = {"mode": "simulation", "actor": "operator-1", "mapping_mode": "predefined_zones",
         "zones": {"doorway": position(10.0, 16.0), "corridor": position(14.0, 3.0)}}


def register(client, camera_id, body):
    return client.put(f"{BASE}/cameras/{camera_id}", json=body)


def report(clock, obs_id="o1", track_id="t1", *, state_name="visible", confidence=0.9,
           first_seen=None, last_seen=None, **location):
    stamp = clock.iso()
    return {"mode": "simulation", "id": obs_id, "observed_at": stamp, "confidence": confidence,
            "track": {"track_id": track_id, "state": state_name,
                      "first_seen_at": first_seen or stamp, "last_seen_at": last_seen or stamp},
            **location}


def post(client, camera_id, body):
    return client.post(f"{BASE}/cameras/{camera_id}/observations", json=body)


def test_two_cameras_with_same_local_track_id_stay_distinct(client, clock):
    assert register(client, "cam-a", CALIBRATED).status_code == 200
    assert register(client, "cam-b", ZONES).status_code == 200
    a = post(client, "cam-a", report(clock, position=position(10.0, 16.0)))
    b = post(client, "cam-b", report(clock, zone_id="doorway"))
    assert a.status_code == b.status_code == 201
    a_track, b_track = a.json()["result"]["track_id"], b.json()["result"]["track_id"]
    assert a_track != b_track
    snapshot = b.json()["state"]
    assert len(snapshot["scenario"]["casualties"]) == 3
    tracks = {t["track_id"]: t for t in snapshot["tracks"]}
    assert set(tracks) == {a_track, b_track}
    assert all(t["casualty_id"] is None for t in tracks.values())
    assert tracks[a_track]["camera"] == {
        "camera_id": "cam-a", "local_observation_id": "o1", "local_track_id": "t1",
        "mapping_mode": "calibrated_world", "location_basis": "calibrated_world",
        "zone_id": None, "calibration_ref": "lab-calibration-2026-11-13"}
    zone = tracks[b_track]["camera"]
    assert (zone["location_basis"], zone["zone_id"]) == ("zone_estimate", "doorway")
    assert tracks[b_track]["latest_observation"]["position"] == position(10.0, 16.0)
    assert tracks[b_track]["latest_observation"]["source"] == "cv"
    assert {c["camera_id"] for c in snapshot["cameras"]} == {"cam-a", "cam-b"}
    events = client.get(f"{BASE}/events", params={"limit": 1000}).json()["events"]
    received = [e for e in events if e["type"] == "simulation.observation_received"]
    assert [e["payload"]["camera"]["camera_id"] for e in received] == ["cam-a", "cam-b"]
    assert "simulation.camera_registered" in {e["type"] for e in events}


def test_namespacing_is_collision_free(client, clock):
    register(client, "a", CALIBRATED)
    register(client, "a:1", CALIBRATED)
    first = post(client, "a", report(clock, obs_id="1:x", track_id="1:x", position=position(1, 1)))
    second = post(client, "a:1", report(clock, obs_id="x", track_id="x", position=position(2, 2)))
    assert first.status_code == second.status_code == 201
    assert first.json()["result"]["track_id"] != second.json()["result"]["track_id"]
    assert len(state(client)["tracks"]) == 2


def same_position_zones():
    return {**ZONES, "zones": {"a": position(10, 16), "b": position(10, 16)}}


def test_zone_change_at_same_point_invalidates_proposal_provenance(client, clock):
    register(client, "phone", same_position_zones())
    first_seen = clock.iso()
    received = post(client, "phone", report(clock, zone_id="a")).json()
    linked = client.post(f"{BASE}/associations", json={
        "track_id": received["result"]["track_id"], "casualty_id": "casualty-001",
        "actor": "operator-1"}).json()["state"]
    old_proposal = linked["current_proposal_id"]
    clock.advance(1)
    changed = post(client, "phone", report(clock, obs_id="o2", first_seen=first_seen,
                                          zone_id="b"))
    assert changed.status_code == 201
    snapshot = changed.json()["state"]
    assert snapshot["scene_revision"] > linked["scene_revision"]
    assert snapshot["current_proposal_id"] != old_proposal
    proposal = next(item for item in snapshot["proposals"]
                    if item["id"] == snapshot["current_proposal_id"])
    assert proposal["location_provenance"]["casualty-001"]["zone_id"] == "b"


def test_same_observation_id_cannot_relabel_zone_at_same_point(client, clock):
    register(client, "phone", same_position_zones())
    assert post(client, "phone", report(clock, zone_id="a")).status_code == 201
    assert post(client, "phone", report(clock, zone_id="b")).status_code == 409
    assert state(client)["tracks"][0]["camera"]["zone_id"] == "a"


@pytest.mark.parametrize("visibility", ["lost", "occluded"])
def test_lost_same_point_zone_change_cannot_replace_detection_provenance(client, clock, visibility):
    register(client, "phone", same_position_zones())
    first_seen = clock.iso()
    assert post(client, "phone", report(clock, zone_id="a")).status_code == 201
    clock.advance(1)
    changed = report(clock, obs_id="o2", state_name=visibility, first_seen=first_seen,
                     last_seen=first_seen, zone_id="b")
    assert post(client, "phone", changed).status_code == 409
    assert state(client)["tracks"][0]["camera"]["zone_id"] == "a"


def test_explicit_association_required_and_no_cross_camera_reid(client, clock):
    register(client, "cam-a", CALIBRATED)
    register(client, "cam-b", CALIBRATED)
    a = post(client, "cam-a", report(clock, position=position(10.0, 16.0))).json()["result"]
    b = post(client, "cam-b", report(clock, position=position(10.0, 16.0))).json()["result"]
    linked = client.post(f"{BASE}/associations", json={
        "track_id": a["track_id"], "casualty_id": "casualty-004", "create_casualty": True,
        "actor": "operator-1"})
    assert linked.status_code == 201
    tracks = {t["track_id"]: t for t in linked.json()["state"]["tracks"]}
    assert tracks[a["track_id"]]["casualty_id"] == "casualty-004"
    assert tracks[b["track_id"]]["casualty_id"] is None
    created = next(c for c in linked.json()["state"]["scenario"]["casualties"]
                   if c["id"] == "casualty-004")
    assert created["medical_assessment"] is None


@pytest.mark.parametrize("body", [
    {**CALIBRATED, "calibration_ref": None},
    {k: v for k, v in CALIBRATED.items() if k != "calibration_ref"},
    {**CALIBRATED, "calibration_ref": "   "},
    {**CALIBRATED, "zones": ZONES["zones"]},
    {**ZONES, "zones": {}},
    {k: v for k, v in ZONES.items() if k != "zones"},
    {**CALIBRATED, "mode": "real"},
    {k: v for k, v in CALIBRATED.items() if k != "mode"},
    {k: v for k, v in CALIBRATED.items() if k != "actor"},
    {**CALIBRATED, "homography": [1, 0, 0]},
    {**CALIBRATED, "stream_url": "javascript:alert(1)"},
    {**CALIBRATED, "stream_url": "file:///etc/passwd"},
    {**CALIBRATED, "stream_url": "http://user:secret@10.0.0.5:8090/stream.mjpg"},
    {**CALIBRATED, "stream_url": "http://10.0.0.5/" + "x" * 500},
])
def test_invalid_registrations_rejected(client, body):
    assert register(client, "cam-x", body).status_code == 422
    assert state(client)["cameras"] == []


def test_zone_positions_must_use_scene_frame(client):
    body = {**ZONES, "zones": {"doorway": position(1, 1, frame="cam-pixels")}}
    assert register(client, "cam-z", body).status_code == 422


def test_registration_is_immutable_within_epoch(client):
    assert register(client, "cam-a", CALIBRATED).status_code == 200
    replay = register(client, "cam-a", {**CALIBRATED, "actor": "operator-2"})
    assert replay.status_code == 200 and replay.json()["duplicate"] is True
    assert replay.json()["result"]["registered_by"] == "operator-1"
    assert register(client, "cam-a", {**CALIBRATED, "calibration_ref": "other"}).status_code == 409
    assert register(client, "cam-a", ZONES).status_code == 409


def test_stream_url_is_listed_and_follows_the_latest_registration(client):
    first = "http://192.168.137.145:8090/stream.mjpg"
    assert register(client, "cam-a", {**ZONES, "stream_url": first}).status_code == 200
    assert [c["stream_url"] for c in state(client)["cameras"]] == [first]
    replay = register(client, "cam-a", {**ZONES, "stream_url": first})
    assert replay.json()["duplicate"] is True

    moved = "https://askar-mac.example.ts.net/stream.mjpg"
    sequence = state(client)["sequence"]
    update = register(client, "cam-a", {**ZONES, "actor": "operator-2", "stream_url": moved})
    assert update.status_code == 200 and update.json()["duplicate"] is False
    assert update.json()["result"]["registered_by"] == "operator-1"     # the mapping record is kept
    after = state(client)
    assert [c["stream_url"] for c in after["cameras"]] == [moved] and after["sequence"] > sequence
    events = client.get(f"{BASE}/events", params={"limit": 1000}).json()["events"]
    assert [e["payload"]["stream_url"] for e in events if e["type"] == "simulation.camera_registered"] == [first, moved]

    assert register(client, "cam-a", ZONES).json()["result"]["stream_url"] is None   # stopped serving
    assert register(client, "cam-a", {**CALIBRATED, "stream_url": moved}).status_code == 409   # mapping still fixed
    assert register(client, "cam-b", CALIBRATED).json()["result"]["stream_url"] is None


def test_camera_observation_rejections(client, clock):
    register(client, "cam-a", CALIBRATED)
    register(client, "cam-b", ZONES)
    assert post(client, "cam-x", report(clock, position=position(1, 1))).status_code == 404
    assert post(client, "cam-a", report(clock, zone_id="doorway")).status_code == 422
    assert post(client, "cam-a", report(clock, position=position(1, 1, "cam-a-pixels"))).status_code == 422
    assert post(client, "cam-a", report(clock)).status_code == 422
    assert post(client, "cam-a", report(clock, position=position(1, 1),
                                        zone_id="doorway")).status_code == 422
    assert post(client, "cam-b", report(clock, position=position(1, 1))).status_code == 422
    assert post(client, "cam-b", report(clock, zone_id="roof")).status_code == 422
    assert post(client, "cam-a", {**report(clock, position=position(1, 1)),
                                  "mode": "real"}).status_code == 422
    assert post(client, "cam-a", {**report(clock, position=position(1, 1)),
                                  "severity": "immediate"}).status_code == 422
    body = report(clock, position=position(1, 1))
    del body["mode"]
    assert post(client, "cam-a", body).status_code == 422
    assert state(client)["tracks"] == []


def test_camera_replay_dedup_and_lost_rules(client, clock):
    register(client, "cam-b", ZONES)
    first_seen = clock.iso()
    body = report(clock, zone_id="doorway")
    assert post(client, "cam-b", body).status_code == 201
    assert post(client, "cam-b", body).json()["duplicate"] is True
    assert post(client, "cam-b", {**body, "zone_id": "corridor"}).status_code == 409
    clock.advance(2)
    moved_while_lost = report(clock, obs_id="o2", state_name="lost", first_seen=first_seen,
                              last_seen=first_seen, zone_id="corridor")
    assert post(client, "cam-b", moved_while_lost).status_code == 409
    honest = report(clock, obs_id="o3", state_name="lost", first_seen=first_seen,
                    last_seen=first_seen, zone_id="doorway")
    accepted = post(client, "cam-b", honest)
    assert accepted.status_code == 201
    track = accepted.json()["state"]["tracks"][0]
    assert track["latest_observation"]["track"]["state"] == "lost"
    assert track["camera"]["local_observation_id"] == "o3"


def test_reset_clears_cameras_and_allows_remapping(client, clock):
    register(client, "cam-a", CALIBRATED)
    post(client, "cam-a", report(clock, position=position(1, 1)))
    snapshot = reset(client)
    assert snapshot["cameras"] == [] and snapshot["tracks"] == []
    assert post(client, "cam-a", report(clock, position=position(1, 1))).status_code == 404
    assert register(client, "cam-a", ZONES).status_code == 200
    assert state(client)["cameras"][0]["mapping_mode"] == "predefined_zones"


def test_legacy_observations_unchanged_but_camera_namespace_reserved(client, clock):
    stamp = clock.iso()
    legacy = observation("obs-1", "trk-1", 1.0, 1.0, observed_at=stamp,
                         first_seen_at=stamp, last_seen_at=stamp)
    accepted = client.post(f"{BASE}/observations", json=legacy)
    assert accepted.status_code == 201
    assert accepted.json()["state"]["tracks"][0]["camera"] is None
    reserved = {**legacy, "id": "obs-2", "track": {**legacy["track"],
                                                    "track_id": "sim-camera:1:a:t"}}
    assert client.post(f"{BASE}/observations", json=reserved).status_code == 422


def test_camera_provenance_reaches_recommendations_and_explanations(client, clock):
    register(client, "cam-a", CALIBRATED)
    register(client, "cam-b", ZONES)
    a = post(client, "cam-a", report(clock, position=position(10.0, 16.0))).json()["result"]
    b = post(client, "cam-b", report(clock, zone_id="corridor")).json()["result"]
    client.post(f"{BASE}/associations", json={
        "track_id": a["track_id"], "casualty_id": "casualty-001", "actor": "operator-1"})
    linked = client.post(f"{BASE}/associations", json={
        "track_id": b["track_id"], "casualty_id": "casualty-002", "actor": "operator-1"})
    snapshot = linked.json()["state"]
    recommendation = snapshot["last_recommendation"]
    assert recommendation["location_provenance"]["casualty-002"]["location_basis"] == "zone_estimate"
    assert recommendation["location_provenance"]["casualty-001"]["location_basis"] == "calibrated_world"
    assert "casualty-003" not in recommendation["location_provenance"]
    zone_note, calibrated_note = (
        next(n for n in recommendation["location_rationale"] if cid in n)
        for cid in ("casualty-002", "casualty-001"))
    revision = f"scene revision {snapshot['scene_revision']}"
    assert revision in zone_note and "zone corridor" in zone_note
    assert "not a precise observed coordinate" in zone_note
    assert revision in calibrated_note and "does not compute or verify calibration" in calibrated_note
    prop = next(p for p in snapshot["proposals"] if p["id"] == snapshot["current_proposal_id"])
    assert prop["location_provenance"] == recommendation["location_provenance"]
    assert zone_note in prop["rationale"] and calibrated_note in prop["rationale"]
    facts = client.get(f"{BASE}/proposals/{prop['id']}/explanation").json()["explanation"]["facts"]
    assert any(fact["text"] == zone_note for fact in facts)


def test_legacy_tracks_add_no_location_provenance(client, clock):
    stamp = clock.iso()
    client.post(f"{BASE}/observations", json=observation(
        "obs-1", "trk-1", 4.0, 8.0, observed_at=stamp, first_seen_at=stamp, last_seen_at=stamp))
    linked = client.post(f"{BASE}/associations", json={
        "track_id": "trk-1", "casualty_id": "casualty-001", "actor": "operator-1"})
    recommendation = linked.json()["state"]["last_recommendation"]
    assert recommendation["location_provenance"] == {}
    assert recommendation["location_rationale"] == []
