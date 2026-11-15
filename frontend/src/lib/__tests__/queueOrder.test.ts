import { describe, expect, it } from "vitest";
import { fallbackState } from "../fallback";
import { queueOrder, victimOrder, warningSigns } from "../insights";
import type { CameraSigns, Casualty, Severity, SimulationState, TrackRecord } from "../types";

const NOW = Date.parse("2026-11-15T07:00:00Z");
const ago = (s: number) => new Date(NOW - s * 1000).toISOString();

function signs(still: boolean, noResponse: boolean): CameraSigns {
  return {
    schema: "aegis-cv-signs/0.1",
    window_s: 10,
    posture: { value: "lying", confidence: 0.9 },
    movement: { value: still ? "still" : "moving", confidence: 0.8, still_for_s: still ? 40 : null },
    response: { value: noResponse ? "no_response" : "responded", confidence: 0.7 },
  } as CameraSigns;
}

function track(id: string, seenAgo: number, s: CameraSigns | null, opts: { lost?: boolean; casualty?: string } = {}): TrackRecord {
  return {
    track_id: id,
    casualty_id: opts.casualty ?? null,
    latest_visible_signs: s,
    observation_count: 5,
    latest_observation: {
      id: `${id}-o`,
      source: "cv",
      observed_at: ago(seenAgo),
      position: { frame_id: "demo-floor-1", x_m: 14, y_m: 8 },
      kind: "person_detected",
      confidence: 0.9,
      track: { track_id: id, state: opts.lost ? "lost" : "visible", first_seen_at: ago(120), last_seen_at: ago(seenAgo) },
    },
  };
}

describe("camera warning signs", () => {
  it("counts only signs the dashboard already marks for attention", () => {
    expect(warningSigns(signs(true, true)).map((l) => l.key)).toEqual(["movement", "response"]);
    expect(warningSigns(signs(false, false))).toEqual([]);
    expect(warningSigns(null)).toEqual([]);
  });
});

describe("observation queue order", () => {
  it("puts people in view first, more warning signs first, and folds old or lost tracks away", () => {
    const tracks = [
      track("calm", 1, signs(false, false)),
      track("still-silent", 2, signs(true, true)),
      track("old-run", 600, signs(true, true)),
      track("gone", 3, null, { lost: true }),
      track("linked", 1, signs(true, true), { casualty: "casualty-001" }),
    ];
    const { current, older } = queueOrder(tracks, NOW);
    expect(current.map((t) => t.track_id)).toEqual(["still-silent", "calm"]);
    expect(older.map((t) => t.track_id)).toEqual(["gone", "old-run"]);
  });
});

describe("victim card order", () => {
  const casualty = (id: string, severity?: Severity): Casualty =>
    ({ id, position: { frame_id: "demo-floor-1", x_m: 14, y_m: 8 }, medical_assessment: severity ? { severity, source: "human", assessed_by: "op", assessed_at: ago(5) } : null }) as Casualty;

  function scene(casualties: Casualty[], tracks: TrackRecord[]): SimulationState {
    const base = fallbackState();
    return { ...base, scenario: { ...base.scenario, casualties }, tracks };
  }

  it("orders unassessed people by camera warning signs", () => {
    const state = scene([casualty("casualty-001"), casualty("casualty-002")], [
      track("a", 1, signs(false, false), { casualty: "casualty-001" }),
      track("b", 1, signs(true, true), { casualty: "casualty-002" }),
    ]);
    expect(victimOrder(state).map((c) => c.id)).toEqual(["casualty-002", "casualty-001"]);
  });

  it("lets the operator's priority win over camera signs", () => {
    const state = scene([casualty("casualty-001", "immediate"), casualty("casualty-002", "minor")], [
      track("a", 1, signs(false, false), { casualty: "casualty-001" }),
      track("b", 1, signs(true, true), { casualty: "casualty-002" }),
    ]);
    expect(victimOrder(state).map((c) => c.id)).toEqual(["casualty-001", "casualty-002"]);
  });
});
