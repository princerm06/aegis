import { responderName, shortId } from "./format";
import type { AStarRoute, CameraSigns, SimulatedAssignment, SimulationProposal, SimulationState } from "./types";

/* ───────────── Camera signs (aegis-cv-signs/0.1) ─────────────
 * Wording follows docs/CAMERA_SIGNS_INTEGRATION.md: literal measurements only
 * ("chest motion not detected", "red-color pattern detected"), never clinical
 * labels such as "not breathing" or "hemorrhage". */

export type SignTone = "attention" | "neutral" | "untestable";

export interface SignLine {
  key: string;
  text: string;
  confidence: number | null;
  tone: SignTone;
}

const REGION: Record<string, string> = {
  head: "head",
  torso: "torso",
  left_arm: "left arm",
  right_arm: "right arm",
  left_leg: "left leg",
  right_leg: "right leg",
};

export function signLines(s: CameraSigns): SignLine[] {
  const out: SignLine[] = [];
  const b = s.visible_blood;
  if (b) {
    const where = b.regions?.length ? ` (${b.regions.map((r) => REGION[r] ?? r).join(", ")})` : "";
    out.push({
      key: "blood",
      text:
        b.value === "detected"
          ? `red-color pattern detected${where}`
          : b.value === "not_detected"
            ? "red-color pattern not detected"
            : "red-color check not testable",
      confidence: b.confidence,
      tone: b.value === "detected" ? "attention" : b.value === "not_testable" ? "untestable" : "neutral",
    });
  }
  const br = s.breathing;
  if (br) {
    out.push({
      key: "breathing",
      text:
        br.value === "motion_detected"
          ? `chest motion detected${br.rate_bpm ? ` · ~${Math.round(br.rate_bpm)}/min` : ""}`
          : br.value === "no_motion_detected"
            ? "chest motion not detected"
            : "chest motion not testable",
      confidence: br.confidence,
      tone: br.value === "no_motion_detected" ? "attention" : br.value === "not_testable" ? "untestable" : "neutral",
    });
  }
  const mv = s.movement;
  out.push({
    key: "movement",
    text: mv.value === "still" ? `still${mv.still_for_s ? ` ${Math.round(mv.still_for_s)}s` : ""}` : mv.value === "moving" ? "moving" : "movement not testable",
    confidence: mv.confidence,
    tone: mv.value === "not_testable" ? "untestable" : mv.value === "still" ? "attention" : "neutral",
  });
  out.push({
    key: "response",
    text: s.response.value === "responded" ? "responded to command" : s.response.value === "no_response" ? "no response to command" : "response not tested",
    confidence: s.response.confidence,
    tone: s.response.value === "no_response" ? "attention" : s.response.value === "not_tested" ? "untestable" : "neutral",
  });
  out.push({
    key: "posture",
    text: `posture ${s.posture.value}`,
    confidence: s.posture.confidence,
    tone: s.posture.value === "unknown" ? "untestable" : "neutral",
  });
  if (s.eyes) {
    out.push({
      key: "eyes",
      text: s.eyes.value === "not_testable" ? "eyes not testable" : `eyes ${s.eyes.value}`,
      confidence: s.eyes.confidence,
      tone: s.eyes.value === "not_testable" ? "untestable" : "neutral",
    });
  }
  return out;
}

/* ───────────── Acknowledgement provenance ─────────────
 * PROVISIONAL heuristic until the backend exposes an explicit source field:
 * the backend's simulate-device-event convenience route always uses the event
 * id `sim-device-event-<assignment>-<kind>`; any other id was posted to
 * /device-events by an external bridge (e.g. the Arduino serial bridge). */
export type AckSource = "simulated" | "device_bridge" | null;

export function milestoneSource(a: SimulatedAssignment, kind: "delivered" | "acknowledged" | "completed"): AckSource {
  const reached =
    kind === "delivered" ? a.delivered_at : kind === "acknowledged" ? a.acknowledged_at : a.completed_at;
  if (!reached) return null;
  return a.device_event_ids.includes(`sim-device-event-${a.id}-${kind}`) ? "simulated" : "device_bridge";
}

/* ───────────── What changed after a replan ───────────── */

export interface DiffItem {
  casualtyId: string;
  text: string;
  kind: "new" | "dropped" | "reassigned" | "rerouted";
}

export interface ProposalDiff {
  previous: SimulationProposal;
  trigger: string | null;
  items: DiffItem[];
  ghostRoutes: AStarRoute[];
}

/** Compare the current proposal with the one it replaced in the same epoch. */
export function proposalDiff(state: SimulationState, current: SimulationProposal | null): ProposalDiff | null {
  if (!current) return null;
  // Never compare across a dispatch: work before it is now active, not "replaced".
  const lastDispatch = state.dispatches.reduce((m, d) => (d.dispatched_at > m ? d.dispatched_at : m), "");
  const earlier = state.proposals
    .filter((p) => p.created_at > lastDispatch)
    // Only proposals replaced without being executed; dispatched work is active, not "dropped".
    .filter(
      (p) =>
        p.epoch === current.epoch &&
        p.id !== current.id &&
        p.created_at <= current.created_at &&
        (p.status === "superseded" || p.status === "expired" || p.status === "rejected"),
    )
    .sort((a, b) => (a.created_at < b.created_at ? 1 : -1));
  const previous = earlier[0];
  if (!previous) return null;

  const before = new Map(previous.assignments.map((a) => [a.casualty_id, a]));
  const after = new Map(current.assignments.map((a) => [a.casualty_id, a]));
  const items: DiffItem[] = [];
  const ghostRoutes: AStarRoute[] = [];

  for (const [cid, a] of after) {
    const b = before.get(cid);
    const who = responderName(state, a.responder_id);
    if (!b) {
      items.push({ casualtyId: cid, kind: "new", text: `${who} now assigned to ${shortId(cid)}` });
    } else if (b.responder_id !== a.responder_id) {
      items.push({ casualtyId: cid, kind: "reassigned", text: `${shortId(cid)}: ${responderName(state, b.responder_id)} → ${who}` });
      if (b.route) ghostRoutes.push(b.route);
    } else if (a.route && b.route && (Math.abs(a.route.distance_m - b.route.distance_m) > 0.05 || a.route.map_revision !== b.route.map_revision)) {
      const d = a.route.distance_m - b.route.distance_m;
      items.push({
        casualtyId: cid,
        kind: "rerouted",
        text: `${who} → ${shortId(cid)} rerouted ${d >= 0 ? "+" : ""}${d.toFixed(1)} m${a.route.map_revision !== b.route.map_revision ? " (map changed)" : ""}`,
      });
      ghostRoutes.push(b.route);
    }
  }
  for (const [cid, b] of before) {
    if (!after.has(cid)) {
      const why = current.reasons[cid]?.[0];
      items.push({
        casualtyId: cid,
        kind: "dropped",
        text: `${shortId(cid)} no longer proposed${why ? ` · ${why.replace(/_/g, " ")}` : ""}`,
      });
      if (b.route) ghostRoutes.push(b.route);
    }
  }
  const trigger = previous.closed_reason ?? null;
  return { previous, trigger, items, ghostRoutes };
}
