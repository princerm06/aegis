import type {
  ActionResponse,
  EventPage,
  ExplanationResponse,
  Position,
  ResponderStatus,
  SceneMap,
  Severity,
  SimulationApproval,
  SimulationState,
  NeuralComparisonReport,
} from "./types";

/**
 * All HTTP goes through the same-origin Next.js rewrite (`/aegis-api/*`, see
 * next.config.ts), so the backend needs no CORS. The backend runs on loopback.
 */
export const SCENARIO_ID = process.env.NEXT_PUBLIC_AEGIS_SCENARIO ?? "demo";
const HTTP_BASE = `/aegis-api/api/v1/simulations/${SCENARIO_ID}`;

export function wsUrl(): string {
  const configured = process.env.NEXT_PUBLIC_AEGIS_WS_URL;
  const base =
    configured ?? `ws://${typeof window === "undefined" ? "127.0.0.1" : window.location.hostname}:8000`;
  return `${base}/api/v1/simulations/${SCENARIO_ID}/ws`;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(`${status}: ${detail}`);
  }
}

function detailText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((d) => (d && typeof d === "object" && "msg" in d ? String((d as { msg: unknown }).msg) : JSON.stringify(d)))
      .join("; ");
  }
  return JSON.stringify(detail);
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  idempotencyKey?: string,
): Promise<T> {
  const headers: Record<string, string> = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  let res: Response;
  try {
    res = await fetch(`${HTTP_BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Backend unreachable");
  }
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    throw new ApiError(res.status, json ? detailText(json.detail) : res.statusText);
  }
  return json as T;
}

const nowIso = () => new Date().toISOString();

export const api = {
  state: () => request<SimulationState>("GET", "/state"),

  events: (afterSequence = 0, limit = 1000) =>
    request<EventPage>("GET", `/events?after_sequence=${afterSequence}&limit=${limit}`),

  reset: () => request<ActionResponse>("POST", "/reset", { mode: "simulation" }),

  assess: (casualtyId: string, severity: Severity, actor: string) =>
    request<ActionResponse>("POST", `/casualties/${encodeURIComponent(casualtyId)}/assessment`, {
      severity,
      source: "human",
      assessed_by: actor,
      assessed_at: nowIso(),
    }),

  updateResponder: (responderId: string, status: ResponderStatus) =>
    request<ActionResponse>("PATCH", `/responders/${encodeURIComponent(responderId)}`, {
      mode: "simulation",
      status,
      reported_at: nowIso(),
    }),

  /** Operator-reported position (e.g. confirmed on the bodycam). */
  moveResponder: (responderId: string, position: Position) =>
    request<ActionResponse>("PATCH", `/responders/${encodeURIComponent(responderId)}`, {
      mode: "simulation",
      position,
      reported_at: nowIso(),
    }),

  associate: (trackId: string, casualtyId: string, createCasualty: boolean, actor: string) =>
    request<ActionResponse>("POST", "/associations", {
      mode: "simulation",
      track_id: trackId,
      casualty_id: casualtyId,
      create_casualty: createCasualty,
      actor,
    }),

  updateMap: (map: SceneMap) => request<ActionResponse>("PUT", "/map", map),

  replan: (actor: string) =>
    request<ActionResponse>("POST", "/replan", {
      mode: "simulation",
      actor,
      solver: "ortools",
      reason: "operator_request",
    }),

  explanation: (proposalId: string) =>
    request<ExplanationResponse>("GET", `/proposals/${encodeURIComponent(proposalId)}/explanation`),

  /** Human decision on the EXACT proposal revision. Key is stable per proposal
   *  revision so a double click replays instead of recording twice. */
  decide: (proposalId: string, revision: number, decision: "approve" | "reject", actor: string, epoch: number) =>
    request<ActionResponse<SimulationApproval>>(
      "POST",
      `/proposals/${encodeURIComponent(proposalId)}/decision`,
      { mode: "simulation", proposal_revision: revision, decision, actor },
      `ui-e${epoch}-${proposalId}-r${revision}-${decision}`,
    ),

  /** Separate, explicit action after approval. Never called automatically. */
  dispatch: (proposalId: string, revision: number, approvalId: string, actor: string, epoch: number) =>
    request<ActionResponse>(
      "POST",
      "/dispatches",
      { mode: "simulation", proposal_id: proposalId, proposal_revision: revision, approval_id: approvalId, actor },
      `ui-e${epoch}-dispatch-${approvalId}`,
    ),

  /** SIMULATION-only stand-in for the Arduino bridge (rehearsal without hardware). */
  simulateDevice: (assignmentId: string, kind: "delivered" | "acknowledged" | "completed") =>
    request<ActionResponse>("POST", `/assignments/${encodeURIComponent(assignmentId)}/simulate-device-event`, {
      mode: "simulation",
      kind,
    }),

  neuralComparison: () => request<NeuralComparisonReport>("GET", "/neural-comparison"),
};
