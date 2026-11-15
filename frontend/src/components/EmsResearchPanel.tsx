"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Button, PanelHeader } from "./ui";

type Advisory = {
  status: "disabled" | "available" | "unavailable";
  prediction_seconds: number | null;
  model_id: string | null;
  model_sha256: string | null;
  warnings: string[];
  limitations: string[];
  error_code: string | null;
  benchmark: { test?: { mae_seconds?: number; rmse_seconds?: number }; baseline?: { mae_seconds?: number; rmse_seconds?: number } } | null;
};

const ENDPOINT = "/aegis-api/api/v1/simulations/advisories/ems-dispatch";
const EXAMPLE = {
  incident_datetime: "2023-03-20T12:00:00",
  initial_call_type: "RESPIR",
  initial_severity_level_code: "4",
  borough: "BRONX",
};
type Inputs = typeof EXAMPLE;

const UNREACHABLE = "Advisory service unreachable — the rest of the dashboard is unaffected.";

/**
 * GET the advisory. The backend answers 200 (disabled/available) or 503 with a JSON body (unavailable).
 * Anything else — proxy errors, HTML error pages, validation errors — becomes a readable message.
 */
async function fetchAdvisory(query: string, signal?: AbortSignal): Promise<Advisory> {
  let response: Response;
  try {
    response = await fetch(query ? `${ENDPOINT}?${query}` : ENDPOINT, { cache: "no-store", signal });
  } catch (err) {
    if (signal?.aborted) throw err;
    throw new Error(UNREACHABLE);
  }
  let body: Partial<Advisory> | null = null;
  try {
    body = (await response.json()) as Partial<Advisory>;
  } catch {
    body = null;
  }
  const known = body?.status === "disabled" || body?.status === "available" || body?.status === "unavailable";
  if (known && (response.ok || body?.status === "unavailable")) return body as Advisory;
  if (response.status === 422) throw new Error("Inputs rejected by the advisory API — check the timestamp format (YYYY-MM-DDTHH:MM:SS).");
  throw new Error(UNREACHABLE);
}

/** Research-only historical prediction. Never reads casualties or sends control requests. */
export function EmsResearchPanel({ scrollIntoViewOnMount = false }: { scrollIntoViewOnMount?: boolean }) {
  const [availability, setAvailability] = useState<Advisory | null>(null);
  const [result, setResult] = useState<Advisory | null>(null);
  const [inputs, setInputs] = useState<Inputs>(EXAMPLE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLElement>(null);

  useEffect(() => {
    if (scrollIntoViewOnMount) ref.current?.scrollIntoView({ block: "start" });
    // Only on mount: the header shortcut opens the drawer scrolled to this panel.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchAdvisory("", controller.signal)
      .then(setAvailability)
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : UNREACHABLE);
      });
    return () => controller.abort();
  }, []);

  const predict = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      // Blank optional fields are omitted so the model uses its explicit "missing" category (the API rejects empty strings).
      const params = new URLSearchParams(Object.entries(inputs).filter(([, v]) => v.trim() !== "").map(([k, v]) => [k, v.trim()]));
      const body = await fetchAdvisory(params.toString());
      setResult(body);
      setAvailability(body); // keep the status badge and notices in step with the latest backend answer
    } catch (err) {
      setError(err instanceof Error ? err.message : "Historical advisory unavailable");
    } finally {
      setLoading(false);
    }
  }, [inputs]);

  const current = result ?? availability;
  const unreachable = !current && !!error;
  const canPredict = availability?.status === "available" && !loading && inputs.incident_datetime.trim() !== "";
  const update = (key: keyof Inputs, value: string) => {
    setInputs((prev) => ({ ...prev, [key]: value }));
    setResult(null); // a shown prediction must always match the inputs on screen
  };
  const statusLabel = current?.status ?? (unreachable ? "unreachable" : "Checking…");

  return (
    <section ref={ref} id="ems-research" className="panel border border-line" aria-label="Historical NYC EMS research advisory" data-ems-status={statusLabel}>
      <PanelHeader
        title="NYC EMS · Historical XGBoost"
        sub={<span className="mono text-[10px] text-muted">RESEARCH ONLY · NO DISPATCH AUTHORITY</span>}
        right={<Badge tone={current?.status === "available" ? "ok" : current?.status === "disabled" ? "neutral" : "warn"}>{statusLabel}</Badge>}
      />
      <div className="space-y-3 p-3 text-xs">
        <div className="sim-stripe rounded border border-warn/50 px-2 py-1 text-[11px] font-semibold text-warn">
          HISTORICAL · ILLUSTRATIVE — 2023 NYC EMS research material, not live emergency intelligence. Not a medical assessment or a dispatch recommendation.
        </div>
        <p className="text-muted leading-relaxed">
          Trained on 2023 NYC EMS history. Estimates <b>call creation → first assignment</b> duration for an
          illustrative NYC input. This is <b>not</b> an indoor travel time, patient outcome, live EMS forecast,
          or AEGIS responder assignment.
        </p>
        {current?.status === "disabled" && (
          <div className="rounded border border-line bg-panel-2 p-2 text-muted">
            Predictions are disabled. Research inference is off by default; launch the backend with{" "}
            <code>AEGIS_EMS_ADVISORY_ENABLED=true</code> and the optional <code>ems</code> dependency group to activate the model, then reload.
          </div>
        )}
        {current?.status === "unavailable" && (
          <div role="alert" className="rounded border border-warn/40 bg-warn/10 p-2 text-warn">
            Model unavailable ({current.error_code ?? "unknown"}). No prediction is shown. Check dependencies and checkpoint.
          </div>
        )}
        {error && <div role="alert" className="rounded border border-danger/40 p-2 text-danger">{error}</div>}
        <div className="grid grid-cols-2 gap-2">
          <label className="space-y-1">
            <span className="text-muted">NYC incident timestamp (historical)</span>
            <input aria-label="NYC incident timestamp" className="w-full rounded border border-line-strong bg-bg p-1.5 mono text-[11px]" value={inputs.incident_datetime} onChange={(e) => update("incident_datetime", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-muted">Initial call type</span>
            <input aria-label="Initial NYC call type" className="w-full rounded border border-line-strong bg-bg p-1.5 mono text-[11px]" value={inputs.initial_call_type} onChange={(e) => update("initial_call_type", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-muted">Initial NYC severity code</span>
            <input aria-label="Initial NYC severity code" className="w-full rounded border border-line-strong bg-bg p-1.5 mono text-[11px]" value={inputs.initial_severity_level_code} onChange={(e) => update("initial_severity_level_code", e.target.value)} />
          </label>
          <label className="space-y-1">
            <span className="text-muted">NYC borough</span>
            <input aria-label="NYC borough" className="w-full rounded border border-line-strong bg-bg p-1.5 mono text-[11px]" value={inputs.borough} onChange={(e) => update("borough", e.target.value)} />
          </label>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-[10px] text-muted">Example fields are illustrative; not a recorded source incident.</span>
          <Button size="sm" disabled={!canPredict} onClick={() => void predict()}>{loading ? "Calculating…" : "Run trained model"}</Button>
        </div>
        {result?.status === "available" && result.prediction_seconds !== null && (
          <div className="rounded border border-accent/40 bg-panel-2 p-3" role="status" data-ems-prediction={result.prediction_seconds}>
            <div className="label">Historical-style predicted dispatch duration</div>
            <div className="mono text-2xl font-bold text-accent">{result.prediction_seconds.toFixed(1)} sec</div>
            <div className="mt-1 text-[10px] text-muted">
              Frozen XGBoost weights · simulated input · not validated for live incidents or scene routing
            </div>
            {result.model_id && (
              <div className="mono mt-1 text-[10px] text-faint" title={result.model_sha256 ?? undefined}>
                {result.model_id}
                {result.model_sha256 ? ` · sha256 ${result.model_sha256.slice(0, 12)}…` : ""}
              </div>
            )}
          </div>
        )}
        {result?.warnings.map((warning, i) => <p key={i} className="text-warn">⚠ {warning}</p>)}
        {current?.benchmark?.test && current.benchmark.baseline && (
          <p className="text-[10px] text-muted">
            Historical holdout MAE: XGBoost {current.benchmark.test.mae_seconds?.toFixed(1)}s vs baseline {current.benchmark.baseline.mae_seconds?.toFixed(1)}s.
            Gains were concentrated in unusual severity-8 incidents; typical cases did not reliably improve.
          </p>
        )}
        <p className="border-t border-line pt-2 text-[10px] text-muted">
          Separate research advisory. Cannot set casualty priority, modify OR-Tools objectives, approve plans,
          alter routes, issue dispatches or communicate with hardware.
        </p>
      </div>
    </section>
  );
}
