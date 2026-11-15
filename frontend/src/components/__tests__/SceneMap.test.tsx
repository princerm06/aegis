import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { SceneMap } from "../SceneMap";
import type { AStarRoute, SimulationState } from "@/lib/types";

const now = Date.now();

function route(points: Array<[number, number]>, rev = "map-2"): AStarRoute {
  return {
    algorithm: "astar",
    map_revision: rev,
    waypoints: points.map(([x, y]) => ({ frame_id: "demo-floor-1", x_m: x, y_m: y })),
    distance_m: points.length,
  };
}

function stateWith(assignments: SimulationState["assignments"], route_invalidations: SimulationState["route_invalidations"] = []): SimulationState {
  return {
    scenario: {
      frame_id: "demo-floor-1",
      casualties: [{ id: "casualty-001", position: { frame_id: "demo-floor-1", x_m: 4, y_m: 1 }, medical_assessment: null }],
      responders: [
        { id: "responder-001", display_name: "Mock Responder Alpha", position: { frame_id: "demo-floor-1", x_m: 0, y_m: 1 }, status: "busy" },
      ],
      scene_map: {
        revision: "map-2",
        occupancy_grid: { origin: { frame_id: "demo-floor-1", x_m: 0, y_m: 0 }, resolution_m: 1, width: 6, height: 6, cells: Array(36).fill(0) },
        obstacles: [],
      },
    },
    tracks: [],
    assignments,
    resolved_casualty_ids: [],
    route_invalidations,
  } as unknown as SimulationState;
}

const assignment = (id: string, r: AStarRoute, extra = {}) => ({
  mode: "simulation",
  id,
  dispatch_id: "dispatch-1",
  casualty_id: "casualty-001",
  responder_id: "responder-001",
  status: "dispatched",
  route: r,
  ...extra,
});

function show(state: SimulationState) {
  return render(
    <SceneMap state={state} proposal={null} selectedId={null} onSelect={() => {}} editMode={false} onToggleCell={() => {}} now={now} />,
  );
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("SceneMap route-invalidation rendering", () => {
  it("renders an invalidated route as danger-dashed history with no navigation arrow", () => {
    const r = route([[0.5, 1.5], [4.5, 1.5]]);
    const { container } = show(stateWith([assignment("a-1", r, { route_status: "invalidated", invalidated_blocks: [[2, 1]] }) as never]));

    const danger = container.querySelector('path[stroke="var(--danger)"]');
    expect(danger).toBeTruthy();
    expect(danger?.getAttribute("stroke-dasharray")).toBe("0.35 0.25");
    expect(danger?.getAttribute("marker-end")).toBeNull(); // history, not safe navigation
    const overlay = container.querySelector('path[stroke="var(--map-mark-bg)"]');
    expect(overlay).toBeTruthy();
    expect(overlay?.getAttribute("d")).toBe(danger?.getAttribute("d")); // same corridor, struck through
    // Never rendered as a safe-current solid route.
    expect(container.querySelector('path[stroke="var(--warn)"]:not([stroke-dasharray])')).toBeNull();
  });

  it("renders a stale-revision route as dimmed dashed history", () => {
    const r = route([[0.5, 2.5], [4.5, 2.5]], "map-1"); // older than scene_map map-2
    const { container } = show(stateWith([assignment("a-1", r) as never]));

    const stale = container.querySelector('path[stroke="var(--warn)"][stroke-dasharray="0.4 0.3"]');
    expect(stale).toBeTruthy();
    expect(stale?.getAttribute("stroke-width")).toBe("0.12");
    expect(stale?.getAttribute("opacity")).toBe("0.55");
    expect(container.querySelector('path[stroke="var(--warn)"]:not([stroke-dasharray])')).toBeNull();
  });

  it("keeps a current route solid with an arrow", () => {
    const r = route([[0.5, 3.5], [4.5, 3.5]]);
    const { container } = show(stateWith([assignment("a-1", r, { route_status: "current" }) as never]));

    const current = container.querySelector('path[stroke="var(--warn)"]:not([stroke-dasharray])');
    expect(current).toBeTruthy();
    expect(current?.getAttribute("stroke-width")).toBe("0.16");
    expect(current?.getAttribute("opacity")).toBe("0.95");
    expect(current?.getAttribute("marker-end")).toBe("url(#arrow)");
    expect(container.querySelector('path[stroke="var(--danger)"]')).toBeNull();
    expect(container.querySelector('path[stroke="var(--info)"]')).toBeNull();
  });

  it("renders an advisory alternative route for an invalidated assignment", () => {
    const r = route([[0.5, 1.5], [4.5, 1.5]]);
    const alt = route([[0.5, 4.5], [4.5, 4.5]]);
    const { container } = show(
      stateWith([assignment("a-1", r, { route_status: "invalidated" }) as never], [
        {
          mode: "simulation",
          assignment_id: "a-1",
          casualty_id: "casualty-001",
          responder_id: "responder-001",
          map_revision: "map-2",
          blocked_cells: [[2, 1]],
          alternative_route: alt,
        },
      ]),
    );

    // Original corridor stays danger-dashed history…
    expect(container.querySelector('path[stroke="var(--danger)"][stroke-dasharray="0.35 0.25"]')).toBeTruthy();
    // …and the revised corridor renders as an info advisory alternative.
    const revised = container.querySelector('path[stroke="var(--info)"]');
    expect(revised).toBeTruthy();
    expect(revised?.getAttribute("stroke-dasharray")).toBe("0.5 0.35");
    expect(revised?.getAttribute("marker-end")).toBe("url(#arrow)");
  });
});
