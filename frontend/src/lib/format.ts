import type { EventEnvelope, Severity, SimulationState } from "./types";

export const SEVERITIES: Severity[] = ["immediate", "delayed", "minor", "expectant", "deceased"];

/** Display tokens only. Labels are human-entered; the UI never infers them. */
export const SEVERITY_META: Record<Severity | "unassessed", { label: string; short: string; color: string }> = {
  immediate: { label: "Immediate", short: "IMM", color: "var(--sev-immediate)" },
  delayed: { label: "Delayed", short: "DEL", color: "var(--sev-delayed)" },
  minor: { label: "Minor", short: "MIN", color: "var(--sev-minor)" },
  expectant: { label: "Expectant", short: "EXP", color: "var(--sev-expectant)" },
  deceased: { label: "Deceased", short: "DEC", color: "var(--sev-deceased)" },
  unassessed: { label: "Unassessed", short: "—", color: "var(--sev-unassessed)" },
};

export function shortId(id: string): string {
  if (id.includes(":")) return id.split(":").slice(-2).join("/"); // sim-camera:8:logitech:track-1 → logitech/track-1
  const m = id.match(/(\d+)$/);
  if (id.startsWith("casualty-") && m) return `C-${m[1]}`;
  if (id.startsWith("responder-") && m) return `R-${m[1]}`;
  return id.length > 18 ? `…${id.slice(-14)}` : id;
}

export function responderName(state: SimulationState | null, id: string): string {
  const r = state?.scenario.responders.find((x) => x.id === id);
  return r ? r.display_name.replace(/^Mock Responder /, "") : shortId(id);
}

export function humanize(code: string): string {
  return code.replace(/_/g, " ");
}

export function clock(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ageSeconds(iso: string, now: number): number {
  return Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
}

export function meters(n: number): string {
  return `${n.toFixed(1)} m`;
}

type Tone = "neutral" | "accent" | "warn" | "ok" | "danger";

/** One-line, factual description of a backend event for the incident timeline. */
export function describeEvent(e: EventEnvelope): { title: string; detail?: string; tone: Tone } {
  const p = e.payload as Record<string, unknown>;
  const s = (k: string) => (p[k] == null ? "" : String(p[k]));
  switch (e.type) {
    case "simulation.reset":
      return { title: "Scenario reset", detail: `epoch ${s("epoch")}`, tone: "neutral" };
    case "simulation.observation_received": {
      const obs = (p.observation ?? p) as Record<string, unknown>;
      const conf = typeof obs.confidence === "number" ? ` · conf ${(obs.confidence as number).toFixed(2)}` : "";
      const tr = (obs.track ?? {}) as Record<string, unknown>;
      return { title: "CV detection received", detail: `${shortId(String(tr.track_id ?? obs.id ?? ""))}${conf}`, tone: "neutral" };
    }
    case "simulation.camera_registered":
      return { title: "Camera registered", detail: s("camera_id"), tone: "neutral" };
    case "simulation.track_associated":
      return { title: "Track linked to casualty", detail: `${shortId(s("track_id"))} → ${shortId(s("casualty_id"))}${p.created ? " (new)" : ""}`, tone: "accent" };
    case "simulation.casualty_created":
      return { title: "New casualty confirmed", detail: shortId(s("casualty_id") || s("id")), tone: "warn" };
    case "simulation.assessment_recorded": {
      const a = (p.medical_assessment ?? p.assessment ?? {}) as Record<string, unknown>;
      return {
        title: "Priority entered by human",
        detail: `${shortId(s("casualty_id"))} → ${String(a.severity ?? s("severity")).toUpperCase()}`,
        tone: "accent",
      };
    }
    case "simulation.responder_updated": {
      const r = (p.responder ?? p) as Record<string, unknown>;
      return { title: "Responder updated", detail: `${shortId(String(r.id ?? s("responder_id")))} · ${String(r.status ?? "")}`, tone: "neutral" };
    }
    case "simulation.map_updated":
      return {
        title: "Scene map changed",
        detail: `rev ${String((p.scene_map as Record<string, unknown> | undefined)?.revision ?? "")}`,
        tone: "warn",
      };
    case "simulation.capabilities_updated":
      return { title: "Required capabilities updated", detail: shortId(s("casualty_id")), tone: "neutral" };
    case "simulation.replan_requested":
      return { title: "Replan requested", detail: `trigger: ${humanize(s("trigger"))}`, tone: "neutral" };
    case "simulation.recommendation_computed":
      return {
        title: "Optimizer finished",
        detail: `${s("engine") || "no engine"}${p.proposal_id ? "" : " · nothing new to propose"}`,
        tone: "neutral",
      };
    case "simulation.proposal_created": {
      const n = Array.isArray(p.assignments) ? p.assignments.length : 0;
      return { title: "Recommendation awaiting approval", detail: `${s("id")} · rev ${s("revision")} · ${n} task(s)`, tone: "warn" };
    }
    case "simulation.proposal_superseded":
      return { title: "Proposal superseded", detail: s("proposal_id") || s("id"), tone: "neutral" };
    case "simulation.proposal_expired":
      return { title: "Proposal expired", detail: s("proposal_id") || s("id"), tone: "neutral" };
    case "simulation.decision_recorded":
      return {
        title: s("decision") === "approve" ? "Command APPROVED proposal" : "Command rejected proposal",
        detail: `${s("proposal_id")} rev ${s("proposal_revision")} · by ${s("approved_by")}`,
        tone: s("decision") === "approve" ? "ok" : "danger",
      };
    case "simulation.approval_invalidated":
      return { title: "Approval invalidated", detail: humanize(s("reason") || s("invalidated_reason")), tone: "danger" };
    case "simulation.dispatch_created": {
      const d = (p.dispatch ?? p) as Record<string, unknown>;
      return { title: "Dispatched (simulation)", detail: String(d.id ?? ""), tone: "ok" };
    }
    case "simulation.device_delivered":
    case "simulation.device_acknowledged":
    case "simulation.device_completed": {
      const d = (p.device_event ?? p) as Record<string, unknown>;
      const who = `${shortId(String(d.responder_id ?? ""))} → ${shortId(String(d.casualty_id ?? ""))}`;
      if (e.type.endsWith("delivered")) return { title: "Device received task", detail: who, tone: "accent" };
      if (e.type.endsWith("acknowledged")) return { title: "Responder ACKNOWLEDGED", detail: who, tone: "ok" };
      return { title: "Task reported complete", detail: who, tone: "ok" };
    }
    case "simulation.responder_released":
      return { title: "Responder released", detail: shortId(s("responder_id")), tone: "neutral" };
    default:
      return { title: humanize(e.type.replace(/^simulation\./, "")), tone: "neutral" };
  }
}
