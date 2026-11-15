import { FLOOR_OUTLINES, type FloorOutline } from "./floorOutlines";
import type { SimulationState } from "./types";

/**
 * Building floors for the scene map (frontend-only, test branch).
 *
 * The backend contract has no floor field yet: every position shares one `frame_id`, and the
 * occupancy grid / A* routes describe a single floor. Until the backend ships a floor (see
 * BACKEND_REQUESTS.md), everything is placed on ROUTING_FLOOR. The PREVIEW flag `multiFloor`
 * spreads people across floors with clearly labelled mock assignments so the picker can be tested.
 */
export interface FloorDef {
  id: string;
  level: number;
  short: string;
  name: string;
  /** Simplified building outline (from the floor-plan drawings), or null when we have no drawing yet. */
  outline: FloorOutline | null;
}

export const FLOORS: FloorDef[] = [
  { id: "A", level: 0, short: "A", name: "Campus · A level", outline: FLOOR_OUTLINES.A },
  { id: "L1", level: 1, short: "L1", name: "Ground · Entry", outline: FLOOR_OUTLINES.L1 },
  { id: "L2", level: 2, short: "L2", name: "Level 2", outline: FLOOR_OUTLINES.L2 },
  { id: "L3", level: 3, short: "L3", name: "Level 3", outline: FLOOR_OUTLINES.L3 },
];

/** The floor the backend grid, obstacles and routes belong to. */
export const ROUTING_FLOOR = "L1";

export const floorById = (id: string) => FLOORS.find((f) => f.id === id) ?? FLOORS[0];

/** Mock spread used only by the PREVIEW flag: cycle people through the floors. */
const PREVIEW_CYCLE = ["L1", "L2", "L3", "A"];

/** entity id (casualty, track, responder) → floor id. */
export function floorAssignments(state: SimulationState | null, preview: boolean): Record<string, string> {
  const out: Record<string, string> = {};
  if (!state) return out;
  const { casualties, responders } = state.scenario;
  const pick = (i: number) => (preview ? PREVIEW_CYCLE[i % PREVIEW_CYCLE.length] : ROUTING_FLOOR);
  [...casualties].sort((a, b) => a.id.localeCompare(b.id)).forEach((c, i) => (out[c.id] = pick(i)));
  [...responders].sort((a, b) => a.id.localeCompare(b.id)).forEach((r, i) => (out[r.id] = pick(i)));
  let u = 0;
  for (const t of state.tracks) {
    // A linked track is the same person as its casualty, so it shares the casualty's floor.
    out[t.track_id] = t.casualty_id && out[t.casualty_id] ? out[t.casualty_id] : pick(u++);
  }
  return out;
}

export interface FloorSummary {
  /** Confirmed victims still on scene (not resolved). */
  victims: number;
  /** Camera detections not yet confirmed or linked to a victim. */
  detections: number;
  responders: number;
  /** Something needs attention here: an unassessed/immediate victim or an unconfirmed detection. */
  attention: boolean;
}

export function floorSummaries(state: SimulationState | null, floorOf: Record<string, string>): Record<string, FloorSummary> {
  const out: Record<string, FloorSummary> = Object.fromEntries(
    FLOORS.map((f) => [f.id, { victims: 0, detections: 0, responders: 0, attention: false }]),
  );
  if (!state) return out;
  const resolved = new Set(state.resolved_casualty_ids);
  for (const c of state.scenario.casualties) {
    const s = out[floorOf[c.id] ?? ROUTING_FLOOR];
    if (!s || resolved.has(c.id)) continue;
    s.victims++;
    const sev = c.medical_assessment?.severity;
    if (!sev || sev === "immediate") s.attention = true;
  }
  for (const t of state.tracks) {
    if (t.casualty_id) continue;
    const s = out[floorOf[t.track_id] ?? ROUTING_FLOOR];
    if (!s) continue;
    s.detections++;
    s.attention = true;
  }
  for (const r of state.scenario.responders) {
    const s = out[floorOf[r.id] ?? ROUTING_FLOOR];
    if (s) s.responders++;
  }
  return out;
}
