import type { FieldNote } from "./fieldNotes";
import { SEVERITY_META, clock, describeEvent, humanize, responderName, shortId } from "./format";
import type { EventEnvelope, SimulationState } from "./types";

/** After-action summary (Markdown) from authoritative state, events and operator notes. */
export function buildReport(state: SimulationState, events: EventEnvelope[], note: (id: string) => FieldNote, operator: string): string {
  const now = new Date();
  const lastReset = events.reduce((m, e) => (e.type === "simulation.reset" ? Math.max(m, e.sequence) : m), 0);
  const epochEvents = events.filter((e) => e.sequence >= lastReset);
  const L: string[] = [];
  L.push(`# AEGIS incident summary — SIMULATION`, "");
  L.push(`Generated ${now.toISOString()} by ${operator}. Scenario \`${state.scenario_id}\`, epoch ${state.epoch}, scene revision ${state.scene_revision}.`);
  L.push(`Synthetic exercise only. Not clinical documentation. Approvals are local and unauthenticated.`, "");

  L.push(`## Victims (${state.scenario.casualties.length})`, "", `| ID | Priority (human) | Assessed by | Pulse | Bleeding | Wound | Breathing | Notes | Status |`, `|---|---|---|---|---|---|---|---|---|`);
  for (const c of state.scenario.casualties) {
    const n = note(c.id);
    const sev = c.medical_assessment ? SEVERITY_META[c.medical_assessment.severity].label : "Unassessed";
    const task = state.assignments.find((a) => a.casualty_id === c.id);
    const status = state.resolved_casualty_ids.includes(c.id) ? "complete" : task ? `${responderName(state, task.responder_id)} · ${task.status}` : "unassigned";
    L.push(
      `| ${shortId(c.id)} | ${sev} | ${c.medical_assessment?.assessed_by ?? "—"} | ${n.pulse ? `${n.pulse.bpm} bpm (${humanize(n.pulse.source)})` : "—"} | ${n.bleeding} | ${n.wound} | ${n.breathing} | ${n.woundNote.replace(/\|/g, "/") || "—"} | ${status} |`,
    );
  }
  L.push("", "Pulse/bleeding/wound/breathing are operator observations, not automated findings.", "");

  L.push(`## Responders`, "");
  for (const r of state.scenario.responders) L.push(`- ${r.display_name} (${shortId(r.id)}): ${r.status}`);
  L.push("");

  L.push(`## Decisions and dispatches`, "");
  for (const a of state.approvals)
    L.push(`- ${clock(a.decided_at)} — ${a.decision.toUpperCase()} ${a.proposal_id} rev ${a.proposal_revision} by ${a.approved_by} (${a.status})`);
  for (const d of state.dispatches) L.push(`- ${clock(d.dispatched_at)} — DISPATCH ${d.id}: ${d.assignment_ids.length} task(s), real device effect: ${d.real_device_effect}`);
  L.push("");

  L.push(`## Tasks`, "");
  for (const a of state.assignments) {
    const ms = [
      `sent ${clock(a.created_at)}`,
      a.delivered_at && `delivered ${clock(a.delivered_at)}`,
      a.acknowledged_at && `ack ${clock(a.acknowledged_at)}`,
      a.completed_at && `complete ${clock(a.completed_at)}`,
    ].filter(Boolean);
    L.push(`- ${responderName(state, a.responder_id)} → ${shortId(a.casualty_id)}: ${ms.join(", ")}${a.route ? ` · route ${a.route.distance_m.toFixed(1)} m` : ""}`);
  }
  L.push("");

  L.push(`## Timeline (${epochEvents.length} events)`, "");
  for (const e of epochEvents) {
    const d = describeEvent(e);
    L.push(`- ${clock(e.occurred_at)} — ${d.title}${d.detail ? ` (${d.detail})` : ""}`);
  }
  return L.join("\n") + "\n";
}

export function download(filename: string, text: string, type = "text/markdown") {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
