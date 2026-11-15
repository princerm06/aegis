/**
 * Route-invalidation helpers (pure, framework-free).
 *
 * A dispatched route is HISTORICAL once its map revision is stale, and
 * INVALIDATED when its corridor crosses a blocked/unknown grid cell on the
 * current map. Invalidated routes need a human-approved replan; they are never
 * safe navigation instructions and never auto-replaced.
 */

/** Grid cell (col, row) containing a world point. */
export function gridCell(grid, x, y) {
  return [
    Math.floor((x - grid.origin.x_m) / grid.resolution_m),
    Math.floor((y - grid.origin.y_m) / grid.resolution_m),
  ];
}

/** All grid cells traversed by a route, interpolating every resolution/4. */
export function traversedCells(grid, route) {
  const cells = new Set();
  const waypoints = route?.waypoints ?? [];
  for (let i = 0; i + 1 < waypoints.length; i++) {
    const a = waypoints[i], b = waypoints[i + 1];
    const dist = Math.hypot(b.x_m - a.x_m, b.y_m - a.y_m);
    const steps = Math.max(1, Math.ceil(dist / (grid.resolution_m / 4)));
    for (let s = 0; s <= steps; s++) {
      const f = s / steps;
      const [c, r] = gridCell(grid, a.x_m + f * (b.x_m - a.x_m), a.y_m + f * (b.y_m - a.y_m));
      cells.add(`${c},${r}`);
    }
  }
  return [...cells].map((key) => key.split(",").map(Number));
}

/** Sorted blocked cells ([col, row]) on a route; out-of-bounds counts as blocked. */
export function blockedCellsOnRoute(grid, route) {
  if (!grid || !route?.waypoints?.length) return [];
  const blocked = [];
  for (const [c, r] of traversedCells(grid, route)) {
    if (c < 0 || r < 0 || c >= grid.width || r >= grid.height) {
      blocked.push([c, r]);
      continue;
    }
    if (grid.cells[r * grid.width + c] !== 0) blocked.push([c, r]);
  }
  return blocked.sort((a, b) => a[1] - b[1] || a[0] - b[0]);
}

/** True when a route is missing or from an older map revision (historical). */
export function isRouteStale(route, currentMapRevision) {
  if (!route) return true;
  return route.map_revision !== currentMapRevision;
}

/** True when a route exists and crosses no blocked cell on the grid. */
export function routeAvoidsCells(grid, route) {
  if (!grid || !route) return false;
  return blockedCellsOnRoute(grid, route).length === 0;
}

/**
 * Active assignments whose dispatched corridor is blocked on the current map.
 * Prefers explicit backend records (`route_status` / `route_invalidations`),
 * falling back to a client-side corridor check so the banner never misses.
 * Completed assignments are history and never require replan.
 */
export function getInvalidatedAssignments(state) {
  const grid = state?.scenario?.scene_map?.occupancy_grid ?? null;
  const assignments = state?.assignments ?? [];
  const records = new Map((state?.route_invalidations ?? []).map((r) => [r.assignment_id, r]));
  const out = [];
  for (const assignment of assignments) {
    if (assignment.status === "completed" || !assignment.route) continue;
    const record = records.get(assignment.id);
    const flagged = assignment.route_status === "invalidated" || record;
    if (flagged) {
      const blockedCells =
        record?.blocked_cells ??
        assignment.invalidated_blocks ??
        (grid ? blockedCellsOnRoute(grid, assignment.route) : []);
      out.push({ assignment, blockedCells, alternativeRoute: record?.alternative_route ?? null });
      continue;
    }
    if (!grid) continue;
    const blockedCells = blockedCellsOnRoute(grid, assignment.route);
    if (blockedCells.length) out.push({ assignment, blockedCells, alternativeRoute: null });
  }
  return out;
}
