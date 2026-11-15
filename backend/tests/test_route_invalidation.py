"""Regression: blocking a cell on a dispatched route must invalidate it.

Covers: route blocking detection, invalidation as historical record,
A*/OR-Tools replanning that avoids newly blocked cells, and safe redispatch
requiring fresh human approval without silent overwrites or device effects.
"""
import math

from backend.app.simulation.store import SimulationStore
from test_simulation_support import (
    BASE, approve_and_dispatch, current, make_client, reset, state,
)


def _grid_cell(grid, pos):
    return (
        math.floor((pos["x_m"] - grid["origin"]["x_m"]) / grid["resolution_m"]),
        math.floor((pos["y_m"] - grid["origin"]["y_m"]) / grid["resolution_m"]),
    )


def _traversed(grid, route):
    cells = set()
    for first, second in zip(route["waypoints"], route["waypoints"][1:]):
        dist = math.hypot(second["x_m"] - first["x_m"], second["y_m"] - first["y_m"])
        steps = max(1, math.ceil(dist / (grid["resolution_m"] / 4)))
        for step in range(steps + 1):
            f = step / steps
            cells.add(_grid_cell(grid, {
                "x_m": first["x_m"] + f * (second["x_m"] - first["x_m"]),
                "y_m": first["y_m"] + f * (second["y_m"] - first["y_m"]),
            }))
    return cells


def _block_interior_cell(snapshot, route):
    grid = snapshot["scenario"]["scene_map"]["occupancy_grid"]
    endpoints = set()
    for entity in (*snapshot["scenario"]["casualties"], *snapshot["scenario"]["responders"]):
        endpoints.add(_grid_cell(grid, entity["position"]))
    for wp in (route["waypoints"][0], route["waypoints"][-1]):
        endpoints.add(_grid_cell(grid, wp))
    for col, row in sorted(_traversed(grid, route)):
        if ((col, row) not in endpoints
                and 0 < col < grid["width"] - 1 and 0 < row < grid["height"] - 1
                and grid["cells"][row * grid["width"] + col] == 0):
            return (col, row)
    raise AssertionError("no blockable interior cell on route")


def test_blocked_dispatched_route_is_invalidated_but_preserved():
    with make_client(SimulationStore()) as client:
        snapshot = reset(client)
        created = approve_and_dispatch(client, snapshot)
        historical = {a["id"]: a["route"] for a in created["result"]["assignments"]}
        target = created["result"]["assignments"][0]
        after_dispatch = created["state"]
        grid = after_dispatch["scenario"]["scene_map"]["occupancy_grid"]
        assert _traversed(grid, target["route"]), "dispatched route must traverse cells"

        blocked = _block_interior_cell(after_dispatch, target["route"])
        scene_map = after_dispatch["scenario"]["scene_map"]
        scene_map["revision"] = "sim-demo-map-blocked-1"
        cells = scene_map["occupancy_grid"]["cells"][:]
        cells[blocked[1] * grid["width"] + blocked[0]] = 100
        scene_map["occupancy_grid"]["cells"] = cells

        updated = client.put(f"{BASE}/map", json=scene_map)
        assert updated.status_code == 200, updated.text
        snapshot = updated.json()["state"]

        # Historical route bytes are preserved, never silently rewritten.
        current_target = next(a for a in snapshot["assignments"] if a["id"] == target["id"])
        assert current_target["route"] == historical[target["id"]]
        assert current_target["route"]["map_revision"] == "sim-demo-map-1"

        # The affected dispatched route is flagged, not shown as current-safe.
        assert current_target.get("route_status") == "invalidated"
        assert "blocked" in (current_target.get("invalidated_reason") or "")
        invalidations = snapshot.get("route_invalidations", [])
        entry = next(i for i in invalidations if i["assignment_id"] == target["id"])
        assert entry["map_revision"] == "sim-demo-map-blocked-1"
        assert [blocked[0], blocked[1]] in entry["blocked_cells"]

        # An unaffected dispatched route on another corridor stays current.
        other = created["result"]["assignments"][1]
        other_now = next(a for a in snapshot["assignments"] if a["id"] == other["id"])
        if blocked not in _traversed(grid, other["route"]):
            assert other_now.get("route_status", "current") == "current"

        # Solver warnings flag the blocked historical route.
        reasons = snapshot["last_recommendation"]["reasons"][target["casualty_id"]]
        assert "active_route_blocked" in reasons

        # Operators get an explicit event; nothing is auto-dispatched.
        types = [e["type"] for e in client.get(
            f"{BASE}/events", params={"after_sequence": 0, "limit": 1000}).json()["events"]]
        assert "simulation.route_invalidated" in types
        assert len(snapshot["dispatches"]) == 1


def test_replan_avoids_blocked_cell_and_requires_fresh_approval():
    with make_client(SimulationStore()) as client:
        snapshot = reset(client)
        created = approve_and_dispatch(client, snapshot)
        target = created["result"]["assignments"][0]
        after_dispatch = created["state"]
        blocked = _block_interior_cell(after_dispatch, target["route"])

        scene_map = after_dispatch["scenario"]["scene_map"]
        scene_map["revision"] = "sim-demo-map-blocked-2"
        cells = scene_map["occupancy_grid"]["cells"][:]
        grid = scene_map["occupancy_grid"]
        cells[blocked[1] * grid["width"] + blocked[0]] = 100
        scene_map["occupancy_grid"]["cells"] = cells
        snapshot = client.put(f"{BASE}/map", json=scene_map).json()["state"]
        new_grid = snapshot["scenario"]["scene_map"]["occupancy_grid"]

        # Advisory alternative (same A* router) must avoid the blocked cell.
        entry = next(i for i in snapshot.get("route_invalidations", [])
                     if i["assignment_id"] == target["id"])
        alternative = entry.get("alternative_route")
        if alternative is not None:
            assert alternative["map_revision"] == "sim-demo-map-blocked-2"
            assert blocked not in _traversed(new_grid, alternative)

        # Free the responder via explicit simulated completion, then replan.
        record = next(a for a in snapshot["assignments"] if a["id"] == target["id"])
        done = client.post(
            f"{BASE}/assignments/{record['id']}/simulate-device-event",
            json={"kind": "completed"})
        assert done.status_code == 201, done.text
        snapshot = done.json()["state"]
        assert next(a for a in snapshot["assignments"]
                    if a["id"] == record["id"])["status"] == "completed"

        replanned = client.post(
            f"{BASE}/replan", json={"actor": "operator-1"}).json()["state"]
        proposal = current(replanned)
        assert proposal["revision"] == replanned["scene_revision"]
        for assignment in proposal["assignments"]:
            assert assignment["route"]["map_revision"] == "sim-demo-map-blocked-2"
            assert blocked not in _traversed(new_grid, assignment["route"])

        # Stale approval cannot dispatch; fresh approval + explicit dispatch only.
        stale = client.post(f"{BASE}/dispatches", json={
            "proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
            "approval_id": "missing-approval", "actor": "operator-1"},
            headers={"Idempotency-Key": "stale-dispatch"})
        assert stale.status_code in (404, 409)
        approval = client.post(
            f"{BASE}/proposals/{proposal['id']}/decision",
            json={"proposal_revision": proposal["revision"], "decision": "approve",
                  "actor": "operator-1"},
            headers={"Idempotency-Key": "fresh-approval"}).json()["result"]
        assert approval["status"] == "active"
        dispatched = client.post(f"{BASE}/dispatches", json={
            "proposal_id": proposal["id"], "proposal_revision": proposal["revision"],
            "approval_id": approval["id"], "actor": "operator-1"},
            headers={"Idempotency-Key": "fresh-dispatch"})
        assert dispatched.status_code == 201, dispatched.text
        assert dispatched.json()["result"]["dispatch"]["real_device_effect"] is False
        # Original historical route bytes still untouched after redispatch.
        final = state(client)
        assert next(a for a in final["assignments"]
                    if a["id"] == target["id"])["route"] == target["route"]


def test_non_intersecting_block_leaves_dispatched_routes_current():
    with make_client(SimulationStore()) as client:
        snapshot = reset(client)
        created = approve_and_dispatch(client, snapshot)
        after_dispatch = created["state"]
        grid = after_dispatch["scenario"]["scene_map"]["occupancy_grid"]
        traversed = set()
        for assignment in created["result"]["assignments"]:
            traversed |= _traversed(grid, assignment["route"])
        # Pick a free cell far from every dispatched corridor and endpoint.
        endpoints = {_grid_cell(grid, e["position"]) for e in (
            *after_dispatch["scenario"]["casualties"],
            *after_dispatch["scenario"]["responders"])}
        free = next((c, r) for r in range(grid["height"]) for c in range(grid["width"])
                    if (c, r) not in traversed and (c, r) not in endpoints
                    and grid["cells"][r * grid["width"] + c] == 0)
        scene_map = after_dispatch["scenario"]["scene_map"]
        scene_map["revision"] = "sim-demo-map-blocked-3"
        cells = scene_map["occupancy_grid"]["cells"][:]
        cells[free[1] * grid["width"] + free[0]] = 100
        scene_map["occupancy_grid"]["cells"] = cells
        snapshot = client.put(f"{BASE}/map", json=scene_map).json()["state"]
        assert snapshot.get("route_invalidations", []) == []
        assert all(a.get("route_status", "current") == "current"
                   for a in snapshot["assignments"] if a["status"] != "completed")
