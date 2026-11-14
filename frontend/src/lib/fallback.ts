import scenario from "./fallbackScenario.json";
import type { Scenario, SimulationState } from "./types";

/**
 * Reliability FALLBACK (allowed by the master context when clearly labelled):
 * a read-only snapshot built from the seeded shared fixture
 * (copy of shared/mock/scenario.demo.json). It is not live, has no proposals,
 * and the UI disables every action while it is shown.
 */
export function fallbackState(): SimulationState {
  const s = scenario as unknown as Scenario;
  return {
    mode: "simulation",
    schema_version: "1.0",
    scenario_id: s.id,
    epoch: 0,
    scene_revision: 1,
    sequence: 0,
    generated_at: s.created_at,
    scenario: s,
    required_capabilities: {},
    tracks: [],
    cameras: [],
    resolved_casualty_ids: [],
    current_proposal_id: null,
    proposals: [],
    approvals: [],
    dispatches: [],
    assignments: [],
    last_recommendation: null,
  };
}
