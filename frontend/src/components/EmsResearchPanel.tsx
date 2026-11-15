"use client";

import { useCallback, useEffect, useState } from "react";
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

/** Research-only historical prediction. Never reads casualties or sends control requests. */
export function EmsResearchPanel() {
  const [availability, setAvailability] = useState<Advisory | null>(null);
  const [result, setResult] = useState<Advisory | null>(null);
  const [inputs, setInputs] = useState(EXAMPLE);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch(ENDPOINT, { cache: "no-store", signal: controller.signal })
      .then(async (response) => {
        const body = (await response.json()) as Advisory;
        if (!response.ok && body.status !== "unavailable") throw new Error("Advisory status request failed");
        setAvailability(body);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(err instanceof Error ? err.message : "Advisory service unreachable");
      });
    return () => controller.abort();
  }, []);

  const predict = useCallback(async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const params = new URLSearchParams(inputs);
      const response = await fetch(`${ENDPOINT}?${params.toString()}`, { cache: "no-store" });
      const body = (await response.json()) as Advisory & { detail?: unknown };
      if (!response.ok && body.status !== "unavailable") throw new Error("Invalid inputs or advisory request failed");
      setResult(body);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Historical advisory unavailable");
    } finally {
      setLoading(false);
    }
  }, [inputs]);

  const current = result ?? availability;
  const canPredict = availability?.status === "available" && !loading;
  const update = (key: keyof typeof EXAMPLE, value: string) =>
    setInputs((prev) => ({ ...prev, [key]: value }));

  return (
    <section className="panel border border-line" aria-label="Historical NYC EMS research advisory">
      <PanelHeader
        title="NYC EMS · Historical XGBoost"
        sub={<span className="mono text-[10px] text-muted">REAL-DATA-TRAINED · RESEARCH ONLY · NO DISPATCH AUTHORITY</span>}
        right={<Badge tone={current?.status === "available" ? "ok" : current?.status === "disabled" ? "neutral" : "warn"}>{current?.status ?? "Checking…"}</Badge>}
      />
      <div className="space-y-3 p-3 text-xs">
        <p className="text-muted leading-relaxed">
          Trained on 2023 NYC EMS history. Estimates <b>call creation → first assignment</b> duration for an
          illustrative NYC input. This is <b>not</b> an indoor travel time, patient outcome, live EMS forecast,
          or AEGIS responder assignment.
        </p>
        {availability?.status === "disabled" && (
          <div className="rounded border border-line bg-panel-2 p-2 text-muted">
            Research inference is off by default. Launch the backend with <code>AEGIS_EMS_ADVISORY_ENABLED=true</code>
            and the optional <code>ems</code> dependency group to activate the model, then reload.
          </div>
        )}
        {availability?.status === "unavailable" && (
          <div className="rounded border border-warn/40 bg-warn/10 p-2 text-warn">
            Model unavailable ({availability.error_code ?? "unknown"}). Check dependencies and checkpoint.
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
          <div className="rounded border border-accent/40 bg-panel-2 p-3" role="status">
            <div className="label">Historical-style predicted dispatch duration</div>
            <div className="mono text-2xl font-bold text-accent">{result.prediction_seconds.toFixed(1)} sec</div>
            <div className="mt-1 text-[10px] text-muted">
              Frozen XGBoost weights · simulated input · not validated for live incidents or scene routing
            </div>
          </div>
        )}
        {result?.status === "unavailable" && <p role="alert" className="text-warn">Inference unavailable: {result.error_code ?? "unknown error"}</p>}
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
