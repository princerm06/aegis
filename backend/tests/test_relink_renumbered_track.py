"""Regression tests for safe operator-confirmed relinking when a camera renumbers a track."""

from backend.app.simulation.store import SimulationStore
from test_simulation_cameras import CALIBRATED, position, post, register, report
from test_simulation_support import (
    BASE,
    FakeClock,
    assessment,
    current,
    decide,
    make_client,
    proposal,
    reset,
    state,
)


def _track_map(snapshot: dict) -> dict[str, dict]:
    return {t["track_id"]: t for t in snapshot["tracks"]}


def _casualty_map(snapshot: dict) -> dict[str, dict]:
    return {c["id"]: c for c in snapshot["scenario"]["casualties"]}


def test_relink_renumbered_camera_track_end_to_end():
    clock = FakeClock()
    with make_client(SimulationStore(clock=clock)) as client:
        reset(client)
        assert register(client, "logitech-1", CALIBRATED).status_code == 200

        # Lower existing demo casualties to minor so immediate casualty-actor-a is prioritized
        for cid in ("casualty-001", "casualty-002", "casualty-003"):
            resp = client.post(
                f"{BASE}/casualties/{cid}/assessment",
                json=assessment("minor", clock.iso()),
            )
            assert resp.status_code == 201

        # 1. Person detected as t1 and associated with casualty-actor-a
        t1_first_seen = clock.iso()
        obs_t1 = post(
            client,
            "logitech-1",
            report(
                clock,
                obs_id="o-t1-1",
                track_id="t1",
                first_seen=t1_first_seen,
                last_seen=t1_first_seen,
                position=position(4.0, 4.0),
            ),
        )
        assert obs_t1.status_code == 201, obs_t1.text
        t1_id = obs_t1.json()["result"]["track_id"]

        assoc_1 = client.post(
            f"{BASE}/associations",
            json={
                "track_id": t1_id,
                "casualty_id": "casualty-actor-a",
                "create_casualty": True,
                "actor": "operator-1",
            },
        )
        assert assoc_1.status_code == 201, assoc_1.text
        assert "replaced_track_id" not in assoc_1.json()["result"]

        # Operator assesses casualty-actor-a as immediate
        assessed_at = clock.iso()
        assess_resp = client.post(
            f"{BASE}/casualties/casualty-actor-a/assessment",
            json=assessment("immediate", assessed_at, by="medic-lead"),
        )
        assert assess_resp.status_code == 201, assess_resp.text
        snap_after_assess = assess_resp.json()["state"]
        prop_1 = current(snap_after_assess)
        assert any(
            a["casualty_id"] == "casualty-actor-a" for a in prop_1["assignments"]
        )

        # 2. New track t3 appears while t1 is still visible -> relinking must return HTTP 409
        clock.advance(1)
        t3_first_seen = clock.iso()
        obs_t3 = post(
            client,
            "logitech-1",
            report(
                clock,
                obs_id="o-t3-1",
                track_id="t3",
                first_seen=t3_first_seen,
                last_seen=t3_first_seen,
                position=position(4.2, 4.1),
            ),
        )
        assert obs_t3.status_code == 201, obs_t3.text
        t3_id = obs_t3.json()["result"]["track_id"]

        conflict_while_visible = client.post(
            f"{BASE}/associations",
            json={
                "track_id": t3_id,
                "casualty_id": "casualty-actor-a",
                "actor": "operator-1",
            },
        )
        assert conflict_while_visible.status_code == 409
        assert t1_id in conflict_while_visible.json()["detail"]
        tracks_now = _track_map(state(client))
        assert tracks_now[t1_id]["casualty_id"] == "casualty-actor-a"
        assert tracks_now[t3_id]["casualty_id"] is None

        # 3. t1 becomes occluded -> optimizer drops casualty-actor-a with track_occluded
        clock.advance(1)
        occluded_t1 = post(
            client,
            "logitech-1",
            report(
                clock,
                obs_id="o-t1-2",
                track_id="t1",
                state_name="occluded",
                first_seen=t1_first_seen,
                last_seen=t1_first_seen,
                position=position(4.0, 4.0),
            ),
        )
        assert occluded_t1.status_code == 201, occluded_t1.text
        snap_occluded = occluded_t1.json()["state"]
        assert "track_occluded" in snap_occluded["last_recommendation"]["reasons"]["casualty-actor-a"]
        assert "casualty-actor-a" in snap_occluded["last_recommendation"]["unassigned_casualty_ids"]

        # Approve current fallback proposal before relinking to verify approval invalidation safeguard
        prop_before_relink = current(snap_occluded)
        approval_before = decide(client, prop_before_relink, "approve-before-relink")
        assert approval_before.status_code == 201
        approval_id = approval_before.json()["result"]["id"]

        # Refresh t3 observation at current timestamp so it is fresh
        clock.advance(1)
        obs_t3_fresh = post(
            client,
            "logitech-1",
            report(
                clock,
                obs_id="o-t3-2",
                track_id="t3",
                first_seen=t3_first_seen,
                last_seen=clock.iso(),
                position=position(4.5, 4.5),
            ),
        )
        assert obs_t3_fresh.status_code == 201

        # 4. Operator explicitly relinks casualty-actor-a to t3
        relinked = client.post(
            f"{BASE}/associations",
            json={
                "track_id": t3_id,
                "casualty_id": "casualty-actor-a",
                "actor": "operator-1",
            },
        )
        assert relinked.status_code == 201, relinked.text
        relink_result = relinked.json()["result"]
        assert relink_result == {
            "track_id": t3_id,
            "casualty_id": "casualty-actor-a",
            "created": False,
            "replaced_track_id": t1_id,
        }

        # Verify audit event includes replaced_track_id
        events = client.get(f"{BASE}/events", params={"limit": 1000}).json()["events"]
        assoc_events = [e for e in events if e["type"] == "simulation.track_associated"]
        assert assoc_events[-1]["payload"] == {
            "track_id": t3_id,
            "casualty_id": "casualty-actor-a",
            "created": False,
            "replaced_track_id": t1_id,
            "actor": "operator-1",
        }

        # 5 & 6. Old track has no casualty association; new track has casualty-actor-a
        snap_relinked = relinked.json()["state"]
        tracks_relinked = _track_map(snap_relinked)
        assert tracks_relinked[t1_id]["casualty_id"] is None
        assert tracks_relinked[t3_id]["casualty_id"] == "casualty-actor-a"

        # 7. Casualty's original assessment and position are preserved/updated accurately
        casualty_a = _casualty_map(snap_relinked)["casualty-actor-a"]
        assert casualty_a["medical_assessment"] == {
            "severity": "immediate",
            "source": "human",
            "assessed_by": "medic-lead",
            "assessed_at": assessed_at,
        }
        assert casualty_a["position"] == position(4.5, 4.5)

        # 8. Casualty re-enters the eligible proposal and earlier approval was invalidated
        old_approval = next(
            a for a in snap_relinked["approvals"] if a["id"] == approval_id
        )
        assert old_approval["status"] == "invalidated"
        assert proposal(snap_relinked, prop_before_relink["id"])["status"] == "superseded"

        prop_relinked = current(snap_relinked)
        assert any(
            a["casualty_id"] == "casualty-actor-a" for a in prop_relinked["assignments"]
        )
        assert snap_relinked["last_recommendation"]["reasons"]["casualty-actor-a"] == [
            "assigned_feasible_route"
        ]

        # 9. Old track t1 becoming visible again does NOT steal the association back
        clock.advance(1)
        revived_t1 = post(
            client,
            "logitech-1",
            report(
                clock,
                obs_id="o-t1-3",
                track_id="t1",
                state_name="visible",
                first_seen=t1_first_seen,
                last_seen=clock.iso(),
                position=position(18.0, 18.0),
            ),
        )
        assert revived_t1.status_code == 201, revived_t1.text
        snap_after_revive = revived_t1.json()["state"]
        tracks_after_revive = _track_map(snap_after_revive)
        assert tracks_after_revive[t1_id]["casualty_id"] is None
        assert tracks_after_revive[t3_id]["casualty_id"] == "casualty-actor-a"
        assert _casualty_map(snap_after_revive)["casualty-actor-a"]["position"] == position(
            4.5, 4.5
        )
        assert snap_after_revive["scene_revision"] == snap_relinked["scene_revision"]


def test_relink_validation_failures_do_not_partially_mutate_old_association():
    clock = FakeClock()
    with make_client(SimulationStore(clock=clock)) as client:
        reset(client)
        register(client, "logitech-1", CALIBRATED)

        t1_first = clock.iso()
        t1_id = post(
            client,
            "logitech-1",
            report(clock, obs_id="o1", track_id="t1", first_seen=t1_first, position=position(2, 2)),
        ).json()["result"]["track_id"]
        t2_id = post(
            client,
            "logitech-1",
            report(clock, obs_id="o2", track_id="t2", first_seen=t1_first, position=position(6, 6)),
        ).json()["result"]["track_id"]

        assert (
            client.post(
                f"{BASE}/associations",
                json={"track_id": t1_id, "casualty_id": "casualty-001", "actor": "op"},
            ).status_code
            == 201
        )
        assert (
            client.post(
                f"{BASE}/associations",
                json={"track_id": t2_id, "casualty_id": "casualty-002", "actor": "op"},
            ).status_code
            == 201
        )

        # Make t1 lost
        clock.advance(1)
        assert (
            post(
                client,
                "logitech-1",
                report(
                    clock,
                    obs_id="o1-lost",
                    track_id="t1",
                    state_name="lost",
                    first_seen=t1_first,
                    last_seen=t1_first,
                    position=position(2, 2),
                ),
            ).status_code
            == 201
        )

        # Attempt to relink casualty-001 to a non-existent track -> 404, t1 stays linked
        assert (
            client.post(
                f"{BASE}/associations",
                json={"track_id": "missing-track", "casualty_id": "casualty-001", "actor": "op"},
            ).status_code
            == 404
        )
        assert _track_map(state(client))[t1_id]["casualty_id"] == "casualty-001"

        # Attempt to relink casualty-001 to t2 (already associated with casualty-002) -> 409, t1 stays linked
        assert (
            client.post(
                f"{BASE}/associations",
                json={"track_id": t2_id, "casualty_id": "casualty-001", "actor": "op"},
            ).status_code
            == 409
        )
        assert _track_map(state(client))[t1_id]["casualty_id"] == "casualty-001"
        assert _track_map(state(client))[t2_id]["casualty_id"] == "casualty-002"
