import { FLOOR_OUTLINES } from "./floorOutlines";
import { blockedCellsOnRoute } from "./routeInvalidation";
import type { EventEnvelope, OccupancyGrid, Position, SceneMap, SimulationState } from "./types";

/**
 * Building walls as the routing grid (frontend, no contract change).
 *
 * The backend's A* plans on `scene_map.occupancy_grid` and treats any non-zero cell as blocked.
 * `planBuildingWalls` turns the routing floor's generated wall grid (room outlines + perimeter,
 * joined by doors — scripts/gen-floor-outlines.py) into a scene map an operator can publish through
 * the existing PUT /map. Rules:
 *  - Nothing here mutates anything; publishing is an explicit operator action (CommandCenter).
 *  - Existing restrictions are carried over: blocked/unknown cells of the current grid and every
 *    obstacle polygon. A grid that cannot be carried over exactly is reported as `incompatible`
 *    and may only be replaced after an explicit, warned confirmation.
 *  - Walls are never opened under people. Someone standing on a wall cell has no route until moved;
 *    the plan reports them with the nearest walkable point (a responder can be moved there as an
 *    opt-in, labelled demo correction; casualty positions cannot be changed from the UI).
 * `planRestore` finds the map that was in use before walls from the shared event log, so turning
 * walls off does not depend on what one browser happened to save.
 */
export const BUILDING_MAP_PREFIX = "bldg-";

export const isBuildingMap = (map: SceneMap | null | undefined) => !!map?.revision.startsWith(BUILDING_MAP_PREFIX);
const isBuildingRev = (rev: string | null | undefined) => !!rev?.startsWith(BUILDING_MAP_PREFIX);

const EPS = 1e-9;
const isMultiple = (v: number, step: number) => Math.abs(v / step - Math.round(v / step)) < 1e-6;

/** Cells (col, row) the backend checks for a position: its cell plus neighbours when it sits on a cell boundary. */
export function cellsAt(grid: OccupancyGrid, p: Position): [number, number][] {
  const ox = (p.x_m - grid.origin.x_m) / grid.resolution_m;
  const oy = (p.y_m - grid.origin.y_m) / grid.resolution_m;
  if (!(ox >= 0 && ox < grid.width && oy >= 0 && oy < grid.height)) return [];
  const c = Math.floor(ox);
  const r = Math.floor(oy);
  const cols = Number.isInteger(ox) && c > 0 ? [c, c - 1] : [c];
  const rows = Number.isInteger(oy) && r > 0 ? [r, r - 1] : [r];
  return cols.flatMap((cc) => rows.map((rr) => [cc, rr] as [number, number]));
}

/** A position the planner cannot start or end at (outside the grid or on a non-free cell). */
export function isBlockedAt(grid: OccupancyGrid, p: Position): boolean {
  const cells = cellsAt(grid, p);
  return cells.length === 0 || cells.some(([c, r]) => grid.cells[r * grid.width + c] !== 0);
}

function hash(text: string): string {
  let h = 2166136261;
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619);
  return (h >>> 0).toString(36);
}

export interface PersonOnWall {
  id: string;
  kind: "casualty" | "responder";
  /** Nearest walkable point (centre of the closest free cell), or null if none within 2 m. */
  snap: Position | null;
  snapDistance_m: number | null;
}

export interface WallsPlan {
  map: SceneMap;
  /** Revision of the live map the plan was built from (re-checked right before publishing). */
  baseRevision: string | null;
  preserved: { blockedCells: number; unknownCells: number; obstacles: number };
  /** Why the current grid cannot be carried over exactly; publishing then REPLACES it (explicit opt-in). */
  incompatible: string | null;
  /** Restrictions lost when an incompatible grid is replaced. */
  dropped: { blockedCells: number; unknownCells: number };
  onWalls: PersonOnWall[];
  /** Active dispatched assignments whose route crosses the new walls (backend will flag them invalidated). */
  invalidates: string[];
}

function wallGrid(floorId: string, frame: string): OccupancyGrid | null {
  const outline = FLOOR_OUTLINES[floorId];
  if (!outline) return null;
  const height = outline.walls.length;
  const width = outline.walls[0].length;
  const cells = new Array<number>(width * height).fill(0);
  // walls[] rows run from the north edge; grid rows advance in +y (from the south edge).
  outline.walls.forEach((line, i) => {
    const row = height - 1 - i;
    for (let c = 0; c < width; c++) if (line[c] === "#") cells[row * width + c] = 100;
  });
  return { origin: { frame_id: frame, x_m: 0, y_m: 0 }, resolution_m: outline.cell_m, width, height, cells };
}

/** Why `base` cannot be carried cell-for-cell onto `fine`, or null when it can. */
function incompatibility(base: OccupancyGrid, fine: OccupancyGrid): string | null {
  const step = fine.resolution_m;
  if (!isMultiple(base.resolution_m, step)) return `current grid resolution ${base.resolution_m} m is not a multiple of ${step} m`;
  if (!isMultiple(base.origin.x_m - fine.origin.x_m, step) || !isMultiple(base.origin.y_m - fine.origin.y_m, step))
    return "current grid is offset from the wall grid";
  const bx1 = base.origin.x_m + base.width * base.resolution_m;
  const by1 = base.origin.y_m + base.height * base.resolution_m;
  const fx1 = fine.origin.x_m + fine.width * step;
  const fy1 = fine.origin.y_m + fine.height * step;
  if (base.origin.x_m > fine.origin.x_m + EPS || base.origin.y_m > fine.origin.y_m + EPS || bx1 < fx1 - EPS || by1 < fy1 - EPS)
    return "current grid does not cover the whole building";
  return null;
}

/** Nearest free cell centre to `p` (Euclidean), searching outward up to `maxM` metres. */
function nearestWalkable(grid: OccupancyGrid, p: Position, maxM = 2): { pos: Position; d: number } | null {
  const res = grid.resolution_m;
  const c0 = Math.floor((p.x_m - grid.origin.x_m) / res);
  const r0 = Math.floor((p.y_m - grid.origin.y_m) / res);
  const reach = Math.ceil(maxM / res);
  let best: { pos: Position; d: number } | null = null;
  for (let dr = -reach; dr <= reach; dr++) {
    for (let dc = -reach; dc <= reach; dc++) {
      const c = c0 + dc;
      const r = r0 + dr;
      if (c < 0 || r < 0 || c >= grid.width || r >= grid.height || grid.cells[r * grid.width + c] !== 0) continue;
      const pos = { frame_id: p.frame_id, x_m: grid.origin.x_m + (c + 0.5) * res, y_m: grid.origin.y_m + (r + 0.5) * res };
      const d = Math.hypot(pos.x_m - p.x_m, pos.y_m - p.y_m);
      if (d <= maxM && (!best || d < best.d)) best = { pos, d };
    }
  }
  return best;
}

/** People (casualties + responders) standing where the planner cannot route on `grid`. */
export function peopleOnWalls(state: SimulationState, grid: OccupancyGrid): PersonOnWall[] {
  const out: PersonOnWall[] = [];
  const people = [
    ...state.scenario.casualties.map((e) => ({ e, kind: "casualty" as const })),
    ...state.scenario.responders.map((e) => ({ e, kind: "responder" as const })),
  ];
  for (const { e, kind } of people) {
    if (state.resolved_casualty_ids?.includes(e.id)) continue;
    if (!isBlockedAt(grid, e.position)) continue;
    const near = nearestWalkable(grid, e.position);
    out.push({ id: e.id, kind, snap: near?.pos ?? null, snapDistance_m: near ? Math.round(near.d * 100) / 100 : null });
  }
  return out;
}

/** What publishing building walls for `floorId` would do. Pure: publishes nothing. */
export function planBuildingWalls(state: SimulationState, floorId: string): WallsPlan | null {
  const frame = state.scenario.frame_id;
  const fine = wallGrid(floorId, frame);
  if (!fine) return null;
  const base = state.scenario.scene_map ?? null;
  const baseGrid = base?.occupancy_grid ?? null;
  const obstacles = base?.obstacles ?? [];
  const cells = fine.cells;
  const preserved = { blockedCells: 0, unknownCells: 0, obstacles: obstacles.length };
  const dropped = { blockedCells: 0, unknownCells: 0 };
  let incompatible: string | null = null;

  if (baseGrid) {
    incompatible = incompatibility(baseGrid, fine);
    if (incompatible) {
      for (const v of baseGrid.cells) {
        if (v === 100) dropped.blockedCells++;
        else if (v === -1) dropped.unknownCells++;
      }
    } else {
      const k = Math.round(baseGrid.resolution_m / fine.resolution_m);
      const offC = Math.round((fine.origin.x_m - baseGrid.origin.x_m) / fine.resolution_m);
      const offR = Math.round((fine.origin.y_m - baseGrid.origin.y_m) / fine.resolution_m);
      for (let br = 0; br < baseGrid.height; br++) {
        for (let bc = 0; bc < baseGrid.width; bc++) {
          const v = baseGrid.cells[br * baseGrid.width + bc];
          if (v === 0) continue;
          if (v === 100) preserved.blockedCells++;
          else preserved.unknownCells++;
          for (let dr = 0; dr < k; dr++) {
            for (let dc = 0; dc < k; dc++) {
              const c = bc * k + dc - offC;
              const r = br * k + dr - offR;
              if (c < 0 || r < 0 || c >= fine.width || r >= fine.height) continue;
              const i = r * fine.width + c;
              // A wall stays a wall (100); otherwise keep the original restriction (blocked or unknown).
              if (cells[i] !== 100) cells[i] = v;
            }
          }
        }
      }
    }
  }

  const map: SceneMap = {
    frame_id: frame,
    revision: `${BUILDING_MAP_PREFIX}${floorId}-${hash(cells.join("") + JSON.stringify(obstacles))}`,
    obstacles: structuredClone(obstacles),
    occupancy_grid: fine,
  };
  const invalidates = state.assignments
    .filter((a) => a.status !== "completed" && a.route && blockedCellsOnRoute(fine, a.route).length > 0)
    .map((a) => a.id);
  return { map, baseRevision: base?.revision ?? null, preserved, incompatible, dropped, onWalls: peopleOnWalls(state, fine), invalidates };
}

/* ── Restoring the map that was in use before walls ── */

export interface WallsBackup {
  /** The map that was live right before walls were published. */
  map: SceneMap;
  /** The building revision that publish created. */
  buildingRevision: string;
  epoch: number;
}

export type RestorePlan =
  | {
      ok: true;
      map: SceneMap;
      source: "server-history" | "local-backup";
      /** Building revision that turned walls on. */
      activation: string;
      /** Map revisions published on top of the walls (operator edits) that restoring would discard. */
      laterEdits: string[];
      /** Live revision the plan was built from (re-checked right before publishing). */
      liveRevision: string;
    }
  | { ok: false; reason: string };

/**
 * The map to go back to when walls are switched off, verified against the shared event log:
 * follow `simulation.map_updated.previous_revision` back from the live map (current epoch only)
 * to the first non-building revision, then take that map's content from the log (a map update or
 * the epoch's reset). A browser backup is only a fallback when the log no longer holds that far
 * back, and only if it matches the activation revision and epoch; the backend additionally
 * refuses a revision reused with different geometry in the same epoch.
 */
export function planRestore(state: SimulationState, events: EventEnvelope[], backup: WallsBackup | null): RestorePlan {
  const live = state.scenario.scene_map;
  if (!live || !isBuildingMap(live)) return { ok: false, reason: "Building walls are not active on the live map." };
  const sorted = [...events].sort((a, b) => a.sequence - b.sequence);
  let start = -1;
  for (let i = sorted.length - 1; i >= 0; i--) {
    if (sorted[i].type === "simulation.reset") {
      start = i;
      break;
    }
  }
  const scope = start >= 0 ? sorted.slice(start) : sorted;
  const maps = new Map<string, SceneMap>();
  const prevOf = new Map<string, string | null>();
  for (const ev of scope) {
    if (ev.type === "simulation.reset") {
      const sm = (ev.payload.scenario as { scene_map?: SceneMap | null } | undefined)?.scene_map;
      if (sm) maps.set(sm.revision, sm);
    } else if (ev.type === "simulation.map_updated") {
      const sm = ev.payload.scene_map as SceneMap | undefined;
      if (!sm) continue;
      maps.set(sm.revision, sm);
      prevOf.set(sm.revision, (ev.payload.previous_revision as string | null | undefined) ?? null);
    }
  }

  const chain: string[] = []; // building revisions from live back to the activation
  let rev: string | null = live.revision;
  let base: string | null | undefined;
  for (let guard = 0; guard < 500 && rev; guard++) {
    if (!isBuildingRev(rev)) {
      base = rev;
      break;
    }
    chain.push(rev);
    if (!prevOf.has(rev)) {
      base = undefined; // history does not reach back far enough
      break;
    }
    rev = prevOf.get(rev) ?? null;
    if (rev === null) base = null;
  }
  const backupUsable = (b: WallsBackup | null): b is WallsBackup =>
    !!b && b.epoch === state.epoch && b.map.frame_id === live.frame_id && chain.includes(b.buildingRevision);

  if (base === null) return { ok: false, reason: "There was no map before walls were applied, so there is nothing to restore. Reset returns to the seeded scenario." };
  if (base !== undefined) {
    const activation = chain[chain.length - 1];
    const laterEdits = chain.slice(0, -1).reverse();
    const fromLog = maps.get(base);
    if (fromLog) return { ok: true, map: fromLog, source: "server-history", activation, laterEdits, liveRevision: live.revision };
    if (backupUsable(backup) && backup.map.revision === base && backup.buildingRevision === activation)
      return { ok: true, map: backup.map, source: "local-backup", activation, laterEdits, liveRevision: live.revision };
    return { ok: false, reason: `The map in use before walls (rev ${base}) is no longer in the shared history and no matching backup exists. Nothing was changed.` };
  }
  // The log ends inside the building chain: only a backup recorded for one of these revisions can be trusted.
  if (backupUsable(backup)) {
    const at = chain.indexOf(backup.buildingRevision);
    return { ok: true, map: backup.map, source: "local-backup", activation: backup.buildingRevision, laterEdits: chain.slice(0, at).reverse(), liveRevision: live.revision };
  }
  return { ok: false, reason: "Can't verify which map was in use before walls: the shared history no longer reaches back that far and no matching backup exists. Nothing was changed." };
}
