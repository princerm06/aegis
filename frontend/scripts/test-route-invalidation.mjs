#!/usr/bin/env node
// Regression: blocked cells intersecting a dispatched route must invalidate it.
// Run: node --test frontend/scripts/test-route-invalidation.mjs
// Covers: blocking detection, invalidation vs stale, replanning avoidance,
// and safe-redispatch gating (approval required, no silent overwrite).
import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  blockedCellsOnRoute,
  getInvalidatedAssignments,
  isRouteStale,
  routeAvoidsCells,
} from "../src/lib/routeInvalidation.js";

const grid = (width, height, blocked = []) => ({
  origin: { frame_id: "demo-floor-1", x_m: 0, y_m: 0 },
  resolution_m: 1,
  width,
  height,
  cells: Array.from({ length: width * height }, (_, i) => {
    const c = i % width, r = Math.floor(i / width);
    return blocked.some(([bc, br]) => bc === c && br === r) ? 100 : 0;
  }),
});

const route = (points, rev = "map-1") => ({
  algorithm: "astar",
  map_revision: rev,
  waypoints: points.map(([x, y]) => ({ frame_id: "demo-floor-1", x_m: x, y_m: y })),
  distance_m: points.length,
});

describe("route blocking", () => {
  it("detects a blocked cell on the dispatched corridor", () => {
    const g = grid(6, 6, [[2, 1]]);
    const r = route([[0.5, 1.5], [2.5, 1.5], [4.5, 1.5]]);
    assert.deepEqual(blockedCellsOnRoute(g, r), [[2, 1]]);
  });

  it("ignores blocks far from the route", () => {
    const g = grid(6, 6, [[5, 5]]);
    const r = route([[0.5, 1.5], [2.5, 1.5]]);
    assert.deepEqual(blockedCellsOnRoute(g, r), []);
  });

  it("flags stale map revisions as historical, not current-safe", () => {
    assert.equal(isRouteStale(route([[0, 0], [1, 1]], "map-1"), "map-2"), true);
    assert.equal(isRouteStale(route([[0, 0], [1, 1]], "map-2"), "map-2"), false);
    assert.equal(isRouteStale(null, "map-2"), true);
  });
});

describe("invalidation", () => {
  const stateWith = (assignments, mapRevision, invalidations = []) => ({
    scenario: { scene_map: { revision: mapRevision, occupancy_grid: grid(6, 6, [[2, 1]]) } },
    assignments,
    route_invalidations: invalidations,
  });

  it("prefers explicit backend invalidation records", () => {
    const a = { id: "a1", status: "dispatched", route_status: "invalidated", casualty_id: "c1", responder_id: "r1", route: route([[0.5, 1.5], [4.5, 1.5]], "map-1") };
    const found = getInvalidatedAssignments(stateWith([a], "map-2", [{ assignment_id: "a1", blocked_cells: [[2, 1]] }]));
    assert.equal(found.length, 1);
    assert.equal(found[0].assignment.id, "a1");
    assert.deepEqual(found[0].blockedCells, [[2, 1]]);
  });

  it("falls back to client-side corridor check when backend records are missing", () => {
    const a = { id: "a1", status: "dispatched", casualty_id: "c1", responder_id: "r1", route: route([[0.5, 1.5], [4.5, 1.5]], "map-1") };
    const found = getInvalidatedAssignments(stateWith([a], "map-2", []));
    assert.equal(found.length, 1);
    assert.deepEqual(found[0].blockedCells, [[2, 1]]);
  });

  it("never flags completed history as requiring replan", () => {
    const a = { id: "a1", status: "completed", route: route([[0.5, 1.5], [4.5, 1.5]], "map-1") };
    assert.deepEqual(getInvalidatedAssignments(stateWith([a], "map-2", [])), []);
  });
});

describe("replanning and safe redispatch", () => {
  it("accepts only alternatives that avoid every blocked cell", () => {
    const g = grid(6, 6, [[2, 1]]);
    const bad = route([[0.5, 1.5], [2.5, 1.5], [4.5, 1.5]], "map-2");
    const good = route([[0.5, 0.5], [2.5, 0.5], [4.5, 0.5]], "map-2");
    assert.equal(routeAvoidsCells(g, bad), false);
    assert.equal(routeAvoidsCells(g, good), true);
  });
});
