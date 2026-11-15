import { describe, expect, it } from "vitest";
import { fallbackState } from "../fallback";
import { FLOOR_OUTLINES } from "../floorOutlines";
import { isBlockedAt, isBuildingMap, planBuildingWalls, planRestore, type WallsBackup } from "../buildingMap";
import type { EventEnvelope, OccupancyGrid, Position, SceneMap, SimulatedAssignment, SimulationState } from "../types";

const FRAME = "demo-floor-1";
const P = (x_m: number, y_m: number): Position => ({ frame_id: FRAME, x_m, y_m });
const WALLS = FLOOR_OUTLINES.L1.walls; // rows from the north edge, 0.25 m cells over 24 x 20 m
const H = WALLS.length;
/** Is the 0.25 m wall cell (col, row-from-south) a drawn wall? */
const drawnWall = (c: number, r: number) => WALLS[H - 1 - r][c] === "#";

/** The backend's seeded demo map: 24 x 20 at 1 m, wall at x=8, y=2..12, plus the matching obstacle. */
function demoMap(revision = "sim-demo-map-1"): SceneMap {
  const cells = new Array(24 * 20).fill(0);
  for (let row = 2; row < 13; row++) cells[row * 24 + 8] = 100;
  return {
    frame_id: FRAME,
    revision,
    obstacles: [{ id: "sim-wall-a", vertices: [P(8, 2), P(9, 2), P(9, 13), P(8, 13)] }],
    occupancy_grid: { origin: P(0, 0), resolution_m: 1, width: 24, height: 20, cells },
  };
}

function stateWith(map: SceneMap | null, patch: Partial<SimulationState> = {}): SimulationState {
  const s = structuredClone(fallbackState());
  s.scenario.scene_map = map;
  return { ...s, ...patch };
}

/** Centre of some drawn wall cell well inside the building. */
function wallCellCentre(): Position {
  for (let r = 30; r < 50; r++) for (let c = 30; c < 60; c++) if (drawnWall(c, r)) return P((c + 0.5) * 0.25, (r + 0.5) * 0.25);
  throw new Error("no wall cell");
}

let seq = 0;
const ev = (type: string, payload: Record<string, unknown>): EventEnvelope => ({
  schema_version: "1.0",
  mode: "simulation",
  event_id: `e${++seq}`,
  type,
  scenario_id: "demo",
  occurred_at: "2026-11-15T00:00:00Z",
  sequence: seq,
  payload,
});
const resetEv = (map: SceneMap) => ev("simulation.reset", { epoch: 0, scenario: { scene_map: map } });
const mapEv = (map: SceneMap, previous: string | null) => ev("simulation.map_updated", { previous_revision: previous, scene_map: map });

describe("planBuildingWalls", () => {
  it("carries over every blocked cell and obstacle of the current map", () => {
    const plan = planBuildingWalls(stateWith(demoMap()), "L1")!;
    const g = plan.map.occupancy_grid!;
    expect(plan.incompatible).toBeNull();
    expect(plan.preserved).toEqual({ blockedCells: 11, unknownCells: 0, obstacles: 1 });
    expect(plan.map.obstacles.map((o) => o.id)).toEqual(["sim-wall-a"]);
    // The 1 m blocked cell (8, 5) becomes 4 x 4 blocked 0.25 m cells.
    for (let r = 20; r < 24; r++) for (let c = 32; c < 36; c++) expect(g.cells[r * g.width + c]).toBe(100);
    // Every drawn wall is blocked too.
    for (let r = 0; r < H; r++) for (let c = 0; c < 96; c++) if (drawnWall(c, r)) expect(g.cells[r * 96 + c]).toBe(100);
    expect(isBuildingMap(plan.map)).toBe(true);
    expect(plan.baseRevision).toBe("sim-demo-map-1");
  });

  it("keeps unknown cells unknown (never turns them into free space)", () => {
    const m = demoMap();
    m.occupancy_grid!.cells[0] = -1; // cell (0,0) at 1 m
    const g = planBuildingWalls(stateWith(m), "L1")!.map.occupancy_grid!;
    for (let r = 0; r < 4; r++) for (let c = 0; c < 4; c++) expect(g.cells[r * 96 + c]).not.toBe(0);
  });

  it("flags a grid it cannot carry over exactly and counts what replacing it drops", () => {
    const m = demoMap();
    m.occupancy_grid = { ...m.occupancy_grid!, origin: P(0.1, 0) } as OccupancyGrid; // offset
    const plan = planBuildingWalls(stateWith(m), "L1")!;
    expect(plan.incompatible).toMatch(/offset/);
    expect(plan.dropped).toEqual({ blockedCells: 11, unknownCells: 0 });
    expect(plan.preserved.obstacles).toBe(1); // polygons are resolution-independent and always kept

    const half = demoMap();
    half.occupancy_grid = { ...half.occupancy_grid!, width: 12, cells: half.occupancy_grid!.cells.slice(0, 240) };
    expect(planBuildingWalls(stateWith(half), "L1")!.incompatible).toMatch(/cover/);
  });

  it("never opens a wall under a person; reports them with the nearest walkable point", () => {
    const onWall = wallCellCentre();
    const s = stateWith(demoMap());
    s.scenario.casualties[0].position = onWall;
    s.scenario.responders[0].position = onWall;
    const plan = planBuildingWalls(s, "L1")!;
    const g = plan.map.occupancy_grid!;
    expect(isBlockedAt(g, onWall)).toBe(true); // still a wall
    const ids = plan.onWalls.map((p) => p.id);
    expect(ids).toContain("casualty-001");
    expect(ids).toContain("responder-001");
    for (const p of plan.onWalls.filter((x) => x.id === "responder-001" || x.id === "casualty-001")) {
      expect(p.snap).not.toBeNull();
      expect(isBlockedAt(g, p.snap!)).toBe(false);
      expect(p.snapDistance_m!).toBeLessThan(1);
    }
    // Same walls either way: people do not change the grid.
    const clean = planBuildingWalls(stateWith(demoMap()), "L1")!.map.occupancy_grid!;
    expect(g.cells).toEqual(clean.cells);
  });

  it("lists dispatched routes the new walls would invalidate", () => {
    const s = stateWith(demoMap());
    const crossing = { algorithm: "astar" as const, map_revision: "sim-demo-map-1", waypoints: [P(0.5, 0.5), P(23.5, 19.5)], distance_m: 30 };
    const a = { id: "asg-1", status: "dispatched", route: crossing, casualty_id: "casualty-001", responder_id: "responder-001" } as SimulatedAssignment;
    const done = { ...a, id: "asg-2", status: "completed" } as SimulatedAssignment;
    expect(planBuildingWalls({ ...s, assignments: [a, done] }, "L1")!.invalidates).toEqual(["asg-1"]);
  });

  it("gives different geometry a different revision", () => {
    const a = planBuildingWalls(stateWith(demoMap()), "L1")!.map.revision;
    const noObstacle = demoMap();
    noObstacle.obstacles = [];
    expect(planBuildingWalls(stateWith(noObstacle), "L1")!.map.revision).not.toBe(a);
    expect(planBuildingWalls(stateWith(demoMap()), "L1")!.map.revision).toBe(a);
  });
});

describe("planRestore", () => {
  const seeded = demoMap();
  const walls = planBuildingWalls(stateWith(seeded), "L1")!.map;
  const edited: SceneMap = { ...walls, revision: `${walls.revision}-ui-abc` };

  it("restores the pre-walls map from the shared event log", () => {
    const events = [resetEv(seeded), mapEv(walls, seeded.revision)];
    const r = planRestore(stateWith(walls), events, null);
    expect(r).toMatchObject({ ok: true, source: "server-history", activation: walls.revision, laterEdits: [], liveRevision: walls.revision });
    if (r.ok) expect(r.map).toEqual(seeded);
  });

  it("reports operator edits made on top of the walls (restoring would discard them)", () => {
    const events = [resetEv(seeded), mapEv(walls, seeded.revision), mapEv(edited, walls.revision)];
    const r = planRestore(stateWith(edited), events, null);
    expect(r).toMatchObject({ ok: true, laterEdits: [edited.revision], activation: walls.revision });
  });

  it("prefers the shared history over a browser backup, even a stale one", () => {
    const stale: WallsBackup = { map: demoMap("someone-elses-map"), buildingRevision: walls.revision, epoch: 0 };
    const r = planRestore(stateWith(walls), [resetEv(seeded), mapEv(walls, seeded.revision)], stale);
    expect(r.ok && r.source).toBe("server-history");
    if (r.ok) expect(r.map.revision).toBe(seeded.revision);
  });

  it("uses a matching backup only when the history no longer reaches back", () => {
    const backup: WallsBackup = { map: seeded, buildingRevision: walls.revision, epoch: 0 };
    // Log rolled over: the walls publish itself is gone, only a later edit remains.
    const r = planRestore(stateWith(edited), [mapEv(edited, walls.revision)], backup);
    expect(r).toMatchObject({ ok: true, source: "local-backup", activation: walls.revision, laterEdits: [edited.revision] });
    // ...and the same log with no backup cannot be verified.
    expect(planRestore(stateWith(edited), [mapEv(edited, walls.revision)], null).ok).toBe(false);
    // Log still has the activation but not the seeded map's content.
    const r2 = planRestore(stateWith(walls), [mapEv(walls, seeded.revision)], backup);
    expect(r2).toMatchObject({ ok: true, source: "local-backup" });
  });

  it("ignores an outdated or mismatched backup", () => {
    const events = [mapEv(walls, seeded.revision)]; // seeded content not in the log
    const otherEpoch: WallsBackup = { map: seeded, buildingRevision: walls.revision, epoch: 3 };
    const otherActivation: WallsBackup = { map: seeded, buildingRevision: "bldg-L1-other", epoch: 0 };
    const otherBase: WallsBackup = { map: demoMap("not-the-base"), buildingRevision: walls.revision, epoch: 0 };
    for (const b of [otherEpoch, otherActivation, otherBase]) expect(planRestore(stateWith(walls), events, b).ok).toBe(false);
  });

  it("refuses with a reason when nothing can be verified", () => {
    const r = planRestore(stateWith(walls), [], null);
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.reason).toMatch(/Nothing was changed/);
  });

  it("refuses when walls are not active", () => {
    expect(planRestore(stateWith(seeded), [resetEv(seeded)], null)).toMatchObject({ ok: false });
  });

  it("does not reach across a reset into an earlier epoch", () => {
    // Walls applied in epoch 0, sim reset, walls applied again: restore targets the new epoch's seed.
    const older = demoMap("old-epoch-map");
    const events = [resetEv(older), mapEv(walls, older.revision), resetEv(seeded), mapEv(walls, seeded.revision)];
    const r = planRestore(stateWith(walls), events, null);
    expect(r.ok && r.map.revision).toBe(seeded.revision);
  });
});

