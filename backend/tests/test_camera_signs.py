"""Contract tests for simulation-only visual signs (never clinical assessment)."""

from datetime import datetime

from backend.app.simulation.store import SimulationStore
from test_simulation_support import BASE, FakeClock, make_client, reset, state
from test_simulation_cameras import CALIBRATED, position, report, register, post


def signs():
    return {
        "schema": "aegis-cv-signs/0.1",
        "window_s": 10.0,
        "posture": {"value": "lying", "confidence": 0.82},
        "movement": {"value": "still", "confidence": 0.90, "still_for_s": 42.5},
        "response": {"value": "not_tested", "confidence": 0.0},
        "eyes": {"value": "closed", "confidence": 0.75},
        "breathing": {"value": "not_testable", "confidence": 0.0},
        "visible_blood": {"value": "not_testable", "confidence": 0.0, "method": "color"},
    }


def test_signs_are_visible_in_snapshot_and_events_without_assessment_or_revision_change():
    clock = FakeClock()
    with make_client(SimulationStore(clock=clock)) as client:
        reset(client)
        assert register(client, "webcam", CALIBRATED).status_code == 200
        first_seen = clock.iso()
        received = post(client, "webcam", report(
            clock, obs_id="s-1", first_seen=first_seen, position=position(10, 16),
            signs=signs(),
        ))
        assert received.status_code == 201, received.text
        track = received.json()["state"]["tracks"][0]
        assert track["latest_visible_signs"]["schema"] == "aegis-cv-signs/0.1"
        assert track["latest_visible_signs"]["posture"]["value"] == "lying"
        assert track["latest_observation"]["signs"]["posture"]["value"] == "lying"
        assert datetime.fromisoformat(track["signs_observed_at"].replace("Z", "+00:00")) == datetime.fromisoformat(first_seen)
        assert all(c["medical_assessment"] is None
                   for c in received.json()["state"]["scenario"]["casualties"])
        linked = client.post(f"{BASE}/associations", json={
            "track_id": received.json()["result"]["track_id"],
            "casualty_id": "casualty-001", "actor": "operator-1",
        })
        assert linked.status_code == 201, linked.text
        before = linked.json()["state"]
        clock.advance(1)
        changed = signs()
        changed["eyes"] = {"value": "open", "confidence": 0.91}
        next_report = post(client, "webcam", report(
            clock, obs_id="s-2", first_seen=first_seen,
            position=position(10, 16), signs=changed,
        ))
        assert next_report.status_code == 201, next_report.text
        after = next_report.json()["state"]
        assert after["scene_revision"] == before["scene_revision"]
        assert after["current_proposal_id"] == before["current_proposal_id"]
        assert after["tracks"][0]["latest_visible_signs"]["eyes"]["value"] == "open"
        event_list = client.get(f"{BASE}/events", params={"limit": 1000}).json()["events"]
        observation_events = [e for e in event_list
                              if e["type"] == "simulation.observation_received"]
        assert observation_events[-1]["payload"]["observation"]["signs"]["schema"] == "aegis-cv-signs/0.1"


def test_nonvisible_track_keeps_last_visible_signs_but_does_not_publish_new_signs():
    clock = FakeClock()
    with make_client(SimulationStore(clock=clock)) as client:
        reset(client)
        register(client, "webcam", CALIBRATED)
        first_seen = clock.iso()
        original = post(client, "webcam", report(
            clock, obs_id="s-1", first_seen=first_seen, position=position(10, 16),
            signs=signs(),
        ))
        assert original.status_code == 201
        clock.advance(1)
        lost = post(client, "webcam", report(
            clock, obs_id="s-2", first_seen=first_seen, last_seen=first_seen,
            state_name="occluded", position=position(10, 16),
        ))
        assert lost.status_code == 201, lost.text
        track = lost.json()["state"]["tracks"][0]
        assert track["latest_observation"]["signs"] is None
        assert track["latest_visible_signs"]["posture"]["value"] == "lying"
        assert track["signs_observed_at"] == first_seen
        bad = post(client, "webcam", report(
            clock, obs_id="s-3", first_seen=first_seen, last_seen=first_seen,
            state_name="occluded", position=position(10, 16), signs=signs(),
        ))
        assert bad.status_code == 422


def test_malformed_signs_are_rejected_without_state_changes():
    clock = FakeClock()
    with make_client(SimulationStore(clock=clock)) as client:
        reset(client)
        register(client, "webcam", CALIBRATED)
        before = state(client)
        bad_examples = []
        wrong_schema = signs()
        wrong_schema["schema"] = "other"
        bad_examples.append(wrong_schema)
        unknown = signs()
        unknown["clinical_priority"] = "immediate"
        bad_examples.append(unknown)
        wrong_confidence = signs()
        wrong_confidence["posture"]["confidence"] = 1.2
        bad_examples.append(wrong_confidence)
        invalid_rate = signs()
        invalid_rate["breathing"] = {
            "value": "no_motion_detected", "confidence": 0.3, "rate_bpm": 14.0
        }
        bad_examples.append(invalid_rate)
        too_many_changes = signs()
        too_many_changes["changes"] = [
            {"sign": "eyes", "from": "open", "to": "closed", "at": clock.iso()}
            for _ in range(6)
        ]
        bad_examples.append(too_many_changes)
        bad_limb_region = signs()
        bad_limb_region["limbs"] = {
            "value": "some_not_moved", "confidence": 0.45, "window_s": 30.0,
            "not_moved": ["head"],
        }
        bad_examples.append(bad_limb_region)
        bad_fall_value = signs()
        bad_fall_value["fall"] = {"value": "collapsed", "confidence": 0.7}
        bad_examples.append(bad_fall_value)
        bad_change_sign = signs()
        bad_change_sign["changes"] = [
            {"sign": "pulse", "from": "70", "to": "110", "at": clock.iso()}
        ]
        bad_examples.append(bad_change_sign)
        extra_nested = signs()
        extra_nested["face_down"] = {
            "value": "seen", "confidence": 0.6, "for_s": 12.0, "unexpected": True
        }
        bad_examples.append(extra_nested)
        for index, bad_signs in enumerate(bad_examples):
            response = post(client, "webcam", report(
                clock, obs_id=f"bad-{index}", position=position(10, 16), signs=bad_signs,
            ))
            assert response.status_code == 422, response.text
        after = state(client)
        assert after["scene_revision"] == before["scene_revision"]
        assert after["tracks"] == []


def history_signs(stamp: str) -> dict:
    return {
        **signs(),
        "fall": {"value": "seen", "confidence": 0.7, "at": stamp},
        "walking": {"value": "not_seen", "confidence": 0.5},
        "limbs": {
            "value": "some_not_moved",
            "confidence": 0.45,
            "window_s": 30.0,
            "not_moved": ["left_leg", "right_arm"],
        },
        "waving": {"value": "seen", "confidence": 0.6, "at": stamp},
        "face_down": {"value": "seen", "confidence": 0.6, "for_s": 12.0},
        "person_nearby": {"value": "seen", "confidence": 0.6, "for_s": 42.0},
        "blood_spread": {
            "value": "growing",
            "confidence": 0.6,
            "window_s": 30.0,
            "change_pct": 45,
        },
        "changes": [
            {"sign": "eyes", "from": "open", "to": "closed", "at": stamp},
            {"sign": "response", "from": "responded", "to": "no_response", "at": stamp},
        ],
    }


def test_history_signs_ingest_persist_and_serialize_without_affecting_solver_or_priority():
    clock = FakeClock()
    with make_client(SimulationStore(clock=clock)) as client:
        reset(client)
        assert register(client, "webcam", CALIBRATED).status_code == 200
        first_seen = clock.iso()
        payload = history_signs(first_seen)

        received = post(
            client,
            "webcam",
            report(
                clock,
                obs_id="hist-1",
                first_seen=first_seen,
                position=position(10, 16),
                signs=payload,
            ),
        )
        assert received.status_code == 201, received.text
        track = received.json()["state"]["tracks"][0]
        vis = track["latest_visible_signs"]
        obs_signs = track["latest_observation"]["signs"]

        for view in (vis, obs_signs):
            assert view["schema"] == "aegis-cv-signs/0.1"
            assert view["fall"]["value"] == "seen"
            assert view["walking"]["value"] == "not_seen"
            assert view["limbs"]["value"] == "some_not_moved"
            assert view["limbs"]["not_moved"] == ["left_leg", "right_arm"]
            assert view["waving"]["value"] == "seen"
            assert view["face_down"] == {"value": "seen", "confidence": 0.6, "for_s": 12.0, "reason": None}
            assert view["person_nearby"] == {"value": "seen", "confidence": 0.6, "for_s": 42.0, "reason": None}
            assert view["blood_spread"]["value"] == "growing"
            assert view["blood_spread"]["change_pct"] == 45
            assert len(view["changes"]) == 2
            assert view["changes"][0]["sign"] == "eyes"
            assert view["changes"][0]["from"] == "open"
            assert view["changes"][0]["to"] == "closed"
            assert "from_value" not in view["changes"][0]

        # Ensure no autonomous medical assessment was inferred
        assert all(
            c["medical_assessment"] is None
            for c in received.json()["state"]["scenario"]["casualties"]
        )

        # Associate with casualty-001 and verify updating history signs does not bump scene_revision
        linked = client.post(
            f"{BASE}/associations",
            json={
                "track_id": received.json()["result"]["track_id"],
                "casualty_id": "casualty-001",
                "actor": "operator-1",
            },
        )
        assert linked.status_code == 201
        before = linked.json()["state"]

        clock.advance(1)
        updated_payload = history_signs(clock.iso())
        updated_payload["blood_spread"] = {
            "value": "stable",
            "confidence": 0.5,
            "window_s": 30.0,
            "change_pct": 5,
        }
        updated_payload["changes"] = [
            {"sign": "movement", "from": "moving", "to": "still", "at": clock.iso()}
            for _ in range(5)
        ]
        next_report = post(
            client,
            "webcam",
            report(
                clock,
                obs_id="hist-2",
                first_seen=first_seen,
                position=position(10, 16),
                signs=updated_payload,
            ),
        )
        assert next_report.status_code == 201, next_report.text
        after = next_report.json()["state"]
        assert after["scene_revision"] == before["scene_revision"]
        assert after["current_proposal_id"] == before["current_proposal_id"]
        assert after["tracks"][0]["latest_visible_signs"]["blood_spread"]["value"] == "stable"
        assert len(after["tracks"][0]["latest_visible_signs"]["changes"]) == 5

        # Verify event serialization preserves "from" alias on changes
        event_list = client.get(f"{BASE}/events", params={"limit": 1000}).json()["events"]
        obs_events = [e for e in event_list if e["type"] == "simulation.observation_received"]
        last_event_signs = obs_events[-1]["payload"]["observation"]["signs"]
        assert last_event_signs["changes"][0]["from"] == "moving"
        assert "from_value" not in last_event_signs["changes"][0]

        # Verify occluded report retains latest_visible_signs with all 8 history fields
        clock.advance(1)
        occluded = post(
            client,
            "webcam",
            report(
                clock,
                obs_id="hist-3",
                first_seen=first_seen,
                last_seen=clock.iso(-1),
                state_name="occluded",
                position=position(10, 16),
            ),
        )
        assert occluded.status_code == 201, occluded.text
        occ_track = occluded.json()["state"]["tracks"][0]
        assert occ_track["latest_observation"]["signs"] is None
        assert occ_track["latest_visible_signs"]["fall"]["value"] == "seen"
        assert len(occ_track["latest_visible_signs"]["changes"]) == 5
