"use client";

import { useState } from "react";
import { SEVERITY_META, meters, responderName, shortId } from "@/lib/format";
import type {
  NeuralComparisonReport,
  SimulationState,
} from "@/lib/types";
import type { LinkStatus } from "@/lib/useSimulation";
import { Badge, Button, PanelHeader } from "./ui";

interface Props {
  state: SimulationState;
  link: LinkStatus;
  comparison: NeuralComparisonReport | null;
  loading: boolean;
  error: string | null;
  isStale: boolean;
  onRefresh?: () => void;
  className?: string;
}

export function NeuralIntelligencePanel({
  state,
  link,
  comparison,
  loading,
  error,
  isStale,
  onRefresh,
  className = "",
}: Props) {
  const [collapsed, setCollapsed] = useState(false);
  const [showTechnicalDetails, setShowTechnicalDetails] = useState(false);

  const casualty = (id: string) => state.scenario.casualties.find((c) => c.id === id);

  // Status computation
  let statusBadgeTone: "ok" | "warn" | "danger" | "neutral" | "info" = "neutral";
  let statusBadgeLabel = "Offline";

  if (link === "offline" || error) {
    statusBadgeTone = "danger";
    statusBadgeLabel = "Backend Unavailable";
  } else if (loading && !comparison) {
    statusBadgeTone = "info";
    statusBadgeLabel = "Loading Shadow Advisory…";
  } else if (!comparison) {
    statusBadgeTone = "neutral";
    statusBadgeLabel = "Awaiting Proposal";
  } else if (comparison.status === "disabled") {
    statusBadgeTone = "neutral";
    statusBadgeLabel = "Shadow Mode Disabled";
  } else if (comparison.status === "checkpoints_unavailable") {
    statusBadgeTone = "danger";
    statusBadgeLabel = "Checkpoints Unavailable";
  } else if (comparison.status === "no_proposal") {
    statusBadgeTone = "neutral";
    statusBadgeLabel = "Waiting For Proposal";
  } else if (isStale || comparison.status === "stale" || comparison.context === "stale") {
    statusBadgeTone = "warn";
    statusBadgeLabel = `Stale (Rev ${comparison.scene_revision})`;
  } else if (comparison.status === "available") {
    statusBadgeTone = "ok";
    statusBadgeLabel = `Live Shadow Synced (Rev ${comparison.scene_revision})`;
  } else {
    statusBadgeTone = "warn";
    statusBadgeLabel = comparison.status;
  }

  const ortools = comparison?.ortools_summary;
  const mlp = comparison?.mlp_prediction;
  const gnn = comparison?.gnn_prediction;
  const disagreement = comparison?.disagreement_analysis;

  return (
    <section className={`panel flex flex-col border border-accent/40 bg-panel shadow-sm ${className}`}>
      <PanelHeader
        title="Neural Optimization Intelligence"
        sub={
          <span className="mono text-[10px] text-muted">
            SIMULATION ONLY · Read-Only Shadow
          </span>
        }
        right={
          <div className="flex items-center gap-1.5">
            {loading && <span className="mono text-[10px] text-accent animate-pulse">syncing…</span>}
            <Badge tone={statusBadgeTone}>{statusBadgeLabel}</Badge>
            <button
              onClick={() => setCollapsed((v) => !v)}
              className="mono text-[11px] text-muted hover:text-text px-1"
              aria-label={collapsed ? "Expand panel" : "Collapse panel"}
              title={collapsed ? "Expand panel" : "Collapse panel"}
            >
              {collapsed ? "▾" : "▴"}
            </button>
          </div>
        }
      />

      {!collapsed && (
        <div className="space-y-3 p-3 text-xs">
          {/* Status Alert Banners */}
          {link === "offline" && (
            <div className="rounded border border-danger/40 bg-danger/10 p-2 text-[11px] text-danger-text">
              <b>Backend Unavailable:</b> Cannot connect to simulation service. Neural shadow comparisons are paused.
            </div>
          )}

          {error && link !== "offline" && (
            <div className="rounded border border-danger/40 bg-danger/10 p-2 text-[11px] text-danger-text">
              <b>Inference Service Warning:</b> {error}
            </div>
          )}

          {comparison?.status === "disabled" && (
            <div className="rounded border border-line-strong bg-panel-2 p-2.5 text-[11.5px] leading-relaxed text-muted">
              <div className="font-semibold text-text mb-0.5">Shadow Advisory Mode Disabled</div>
              Neural shadow inference is disabled by environment flag (<code>AEGIS_NEURAL_SHADOW_ENABLED=false</code>).
              The authoritative OR-Tools solver continues operating normally. Set <code>AEGIS_NEURAL_SHADOW_ENABLED=true</code> to activate live side-by-side inference.
            </div>
          )}

          {comparison?.status === "checkpoints_unavailable" && (
            <div className="rounded border border-warn/40 bg-warn/10 p-2.5 text-[11.5px] text-warn">
              <div className="font-semibold text-text mb-0.5">Model Checkpoints Not Provisioned</div>
              PyTorch checkpoint weights were not found at <code>experiments/neural_assignment/weights/</code>.
              OR-Tools continues as sole authoritative solver.
            </div>
          )}

          {comparison?.status === "no_proposal" && (
            <div className="rounded border border-line bg-panel-2 p-2 text-[11px] text-muted text-center">
              No active optimization proposal pending for scene rev {state.scene_revision}. Trigger a recommendation to compute neural shadow predictions.
            </div>
          )}

          {(isStale || comparison?.status === "stale" || comparison?.context === "stale") && (
            <div className="rounded border border-warn/40 bg-tone-warn p-2 text-[11px] text-warn">
              <div className="flex items-center justify-between">
                <span className="font-semibold">⚠ Comparison Stale: Scene State Mutated</span>
                {onRefresh && (
                  <Button size="sm" variant="ghost" onClick={onRefresh} disabled={loading}>
                    Re-sync
                  </Button>
                )}
              </div>
              <p className="mt-1 text-[10.5px] text-muted">
                Evaluated at scene rev {comparison?.scene_revision ?? "—"}, but live scene is at rev {state.scene_revision}.
                {comparison?.stale_reasons && comparison.stale_reasons.length > 0
                  ? ` Reasons: ${comparison.stale_reasons.join(", ")}.`
                  : " Recompute recommendations to generate fresh advisory."}
              </p>
            </div>
          )}

          {/* Three Side-by-Side Model Columns */}
          {ortools && (
            <div className="grid grid-cols-1 md:grid-cols-3 gap-2.5">
              {/* Column 1: OR-Tools Authoritative */}
              <div className="rounded border border-accent/40 bg-panel-2 p-2.5 flex flex-col justify-between space-y-2">
                <div>
                  <div className="flex items-center justify-between gap-1 border-b border-line pb-1.5">
                    <div>
                      <div className="font-bold text-[12px] text-text">OR-Tools CP-SAT</div>
                      <div className="text-[9.5px] text-muted">Authoritative Solver</div>
                    </div>
                    <Badge tone="ok">AUTHORITATIVE</Badge>
                  </div>

                  <div className="grid grid-cols-2 gap-1.5 py-2 mono text-[10px]">
                    <StatBox label="Tasks" value={`${ortools.assigned_count} assigned`} />
                    <StatBox label="Priority" value={`${ortools.priority_points} pts`} />
                    <StatBox label="Travel" value={meters(ortools.travel_distance_m)} />
                    <StatBox label="Solve Time" value={`${ortools.runtime_ms.toFixed(1)} ms`} />
                  </div>

                  <div className="mt-1 space-y-1">
                    <div className="label !text-[9.5px] text-faint uppercase tracking-wider">Proposed Pairs</div>
                    <ul className="space-y-1">
                      {ortools.selected_pairs.map(([rId, cId]) => {
                        const c = casualty(cId);
                        const sev = c?.medical_assessment?.severity ?? "unassessed";
                        const m = SEVERITY_META[sev];
                        return (
                          <li
                            key={`${rId}-${cId}`}
                            className="flex items-center justify-between rounded bg-bg/80 px-1.5 py-1 text-[11px] border border-line"
                          >
                            <span className="font-semibold truncate max-w-[90px]">{responderName(state, rId)}</span>
                            <span className="text-accent">→</span>
                            <span className="mono font-semibold">{shortId(cId)}</span>
                            <span className="mono text-[8.5px] font-bold uppercase" style={{ color: m.color }}>
                              {m.short}
                            </span>
                          </li>
                        );
                      })}
                      {ortools.selected_pairs.length === 0 && (
                        <li className="text-[10.5px] text-muted italic">0 assignments proposed</li>
                      )}
                    </ul>
                  </div>
                </div>

                <div className="pt-2 border-t border-line text-[10px]">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">Hard Constraints:</span>
                    <span className="mono font-semibold text-ok">
                      {ortools.hard_constraint_validation.valid ? "✓ 100% Feasible (0 violations)" : "Violation"}
                    </span>
                  </div>
                  <div className="mt-1 text-[9px] text-faint leading-tight">
                    Sole engine with dispatch authority.
                  </div>
                </div>
              </div>

              {/* Column 2: Pairwise MLP Neural Advisory */}
              <div className="rounded border border-line bg-panel-2 p-2.5 flex flex-col justify-between space-y-2">
                <div>
                  <div className="flex items-center justify-between gap-1 border-b border-line pb-1.5">
                    <div>
                      <div className="font-bold text-[12px] text-text">Pairwise MLP</div>
                      <div className="text-[9.5px] text-muted">Neural Feedforward</div>
                    </div>
                    {mlp?.comparison_vs_ortools?.matches_ortools_pairs ? (
                      <Badge tone="ok">Agrees</Badge>
                    ) : (
                      <Badge tone="accent">Diverges</Badge>
                    )}
                  </div>

                  {mlp?.available ? (
                    <>
                      <div className="grid grid-cols-2 gap-1.5 py-2 mono text-[10px]">
                        <StatBox
                          label="Tasks"
                          value={`${mlp.assigned_count} pairs`}
                          delta={mlp.comparison_vs_ortools ? `${mlp.comparison_vs_ortools.count_diff >= 0 ? "+" : ""}${mlp.comparison_vs_ortools.count_diff}` : undefined}
                        />
                        <StatBox
                          label="Priority"
                          value={`${mlp.priority_points} pts`}
                          delta={mlp.comparison_vs_ortools ? `${mlp.comparison_vs_ortools.priority_points_diff >= 0 ? "+" : ""}${mlp.comparison_vs_ortools.priority_points_diff}` : undefined}
                        />
                        <StatBox
                          label="Travel"
                          value={meters(mlp.travel_distance_m)}
                          delta={mlp.comparison_vs_ortools ? `${mlp.comparison_vs_ortools.distance_diff_m >= 0 ? "+" : ""}${mlp.comparison_vs_ortools.distance_diff_m.toFixed(1)}m` : undefined}
                        />
                        <StatBox
                          label="Inference"
                          value={`${mlp.inference_duration_ms.toFixed(2)} ms`}
                          sub={`total ${mlp.total_duration_ms.toFixed(1)}ms`}
                        />
                      </div>

                      <div className="mt-1 space-y-1">
                        <div className="label !text-[9.5px] text-faint uppercase tracking-wider">Predicted Pairs</div>
                        <ul className="space-y-1">
                          {mlp.selected_pairs.map(([rId, cId]) => {
                            const c = casualty(cId);
                            const sev = c?.medical_assessment?.severity ?? "unassessed";
                            const m = SEVERITY_META[sev];
                            const otPairMatch = ortools.selected_pairs.some(([orR, orC]) => orR === rId && orC === cId);
                            return (
                              <li
                                key={`${rId}-${cId}`}
                                className={`flex items-center justify-between rounded px-1.5 py-1 text-[11px] border ${
                                  otPairMatch ? "bg-bg/80 border-line" : "bg-accent/15 border-accent/40"
                                }`}
                              >
                                <span className="font-semibold truncate max-w-[90px]">{responderName(state, rId)}</span>
                                <span className="text-accent">→</span>
                                <span className="mono font-semibold">{shortId(cId)}</span>
                                <span className="mono text-[8.5px] font-bold uppercase" style={{ color: m.color }}>
                                  {m.short}
                                </span>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    </>
                  ) : (
                    <div className="py-4 text-center text-muted text-[11px]">
                      {mlp?.error ?? "MLP prediction unavailable"}
                    </div>
                  )}
                </div>

                <div className="pt-2 border-t border-line text-[10px]">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">Hard Gate:</span>
                    <span className={`mono font-semibold ${mlp?.hard_constraint_validation?.valid ? "text-ok" : "text-danger"}`}>
                      {mlp?.hard_constraint_validation?.valid
                        ? "✓ Feasible (0 violations)"
                        : `✗ ${mlp?.hard_constraint_validation?.violation_count ?? 1} violations`}
                    </span>
                  </div>
                  <div className="mt-1 text-[9px] text-faint leading-tight">
                    Advisory only · Cannot authorize dispatch.
                  </div>
                </div>
              </div>

              {/* Column 3: Bipartite GNN Graph Neural Advisory */}
              <div className="rounded border border-line bg-panel-2 p-2.5 flex flex-col justify-between space-y-2">
                <div>
                  <div className="flex items-center justify-between gap-1 border-b border-line pb-1.5">
                    <div>
                      <div className="font-bold text-[12px] text-text">Bipartite GNN</div>
                      <div className="text-[9.5px] text-muted">Graph Relational Net</div>
                    </div>
                    {gnn?.comparison_vs_ortools?.matches_ortools_pairs ? (
                      <Badge tone="ok">Agrees</Badge>
                    ) : (
                      <Badge tone="warn">Alt Plan</Badge>
                    )}
                  </div>

                  {gnn?.available ? (
                    <>
                      <div className="grid grid-cols-2 gap-1.5 py-2 mono text-[10px]">
                        <StatBox
                          label="Tasks"
                          value={`${gnn.assigned_count} pairs`}
                          delta={gnn.comparison_vs_ortools ? `${gnn.comparison_vs_ortools.count_diff >= 0 ? "+" : ""}${gnn.comparison_vs_ortools.count_diff}` : undefined}
                        />
                        <StatBox
                          label="Priority"
                          value={`${gnn.priority_points} pts`}
                          delta={gnn.comparison_vs_ortools ? `${gnn.comparison_vs_ortools.priority_points_diff >= 0 ? "+" : ""}${gnn.comparison_vs_ortools.priority_points_diff}` : undefined}
                        />
                        <StatBox
                          label="Travel"
                          value={meters(gnn.travel_distance_m)}
                          delta={gnn.comparison_vs_ortools ? `${gnn.comparison_vs_ortools.distance_diff_m >= 0 ? "+" : ""}${gnn.comparison_vs_ortools.distance_diff_m.toFixed(1)}m` : undefined}
                        />
                        <StatBox
                          label="Inference"
                          value={`${gnn.inference_duration_ms.toFixed(2)} ms`}
                          sub={`total ${gnn.total_duration_ms.toFixed(1)}ms`}
                        />
                      </div>

                      <div className="mt-1 space-y-1">
                        <div className="label !text-[9.5px] text-faint uppercase tracking-wider">Predicted Pairs</div>
                        <ul className="space-y-1">
                          {gnn.selected_pairs.map(([rId, cId]) => {
                            const c = casualty(cId);
                            const sev = c?.medical_assessment?.severity ?? "unassessed";
                            const m = SEVERITY_META[sev];
                            const otPairMatch = ortools.selected_pairs.some(([orR, orC]) => orR === rId && orC === cId);
                            return (
                              <li
                                key={`${rId}-${cId}`}
                                className={`flex items-center justify-between rounded px-1.5 py-1 text-[11px] border ${
                                  otPairMatch ? "bg-bg/80 border-line" : "bg-warn/15 border-warn/40"
                                }`}
                              >
                                <span className="font-semibold truncate max-w-[90px]">{responderName(state, rId)}</span>
                                <span className="text-accent">→</span>
                                <span className="mono font-semibold">{shortId(cId)}</span>
                                <span className="mono text-[8.5px] font-bold uppercase" style={{ color: m.color }}>
                                  {m.short}
                                </span>
                              </li>
                            );
                          })}
                        </ul>
                      </div>
                    </>
                  ) : (
                    <div className="py-4 text-center text-muted text-[11px]">
                      {gnn?.error ?? "GNN prediction unavailable"}
                    </div>
                  )}
                </div>

                <div className="pt-2 border-t border-line text-[10px]">
                  <div className="flex items-center justify-between">
                    <span className="text-muted">Hard Gate:</span>
                    <span className={`mono font-semibold ${gnn?.hard_constraint_validation?.valid ? "text-ok" : "text-danger"}`}>
                      {gnn?.hard_constraint_validation?.valid
                        ? "✓ Feasible (0 violations)"
                        : `✗ ${gnn?.hard_constraint_validation?.violation_count ?? 1} violations`}
                    </span>
                  </div>
                  <div className="mt-1 text-[9px] text-faint leading-tight">
                    Advisory only · Cannot authorize dispatch.
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Grounded Disagreement Explanation Card */}
          {disagreement && !disagreement.models_agree_with_ortools && (
            <div className="rounded border border-accent/40 bg-accent-dim/15 p-2.5 space-y-1.5">
              <div className="flex items-center gap-1.5">
                <span className="mono text-[10px] font-bold uppercase tracking-wider text-accent">
                  ⚡ Model Divergence Analysis
                </span>
                <Badge tone="accent" className="text-[9px]">Grounded Metrics</Badge>
              </div>
              <p className="text-[11.5px] text-text leading-snug font-medium">
                {comparison.voice_summary ?? disagreement.summary}
              </p>

              {disagreement.differing_assignments.length > 0 && (
                <div className="mt-2 overflow-x-auto">
                  <table className="w-full text-left text-[10.5px] border-collapse">
                    <thead>
                      <tr className="border-b border-line text-faint uppercase text-[9px]">
                        <th className="py-1 pr-2">Casualty</th>
                        <th className="py-1 pr-2">OR-Tools (Auth)</th>
                        <th className="py-1 pr-2">MLP Advisory</th>
                        <th className="py-1">GNN Advisory</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-line/40 mono">
                      {disagreement.differing_assignments.map((diff) => (
                        <tr key={diff.casualty_id}>
                          <td className="py-1 font-semibold text-text">{shortId(diff.casualty_id)}</td>
                          <td className="py-1 text-ok">{diff.ortools_responder_id ? responderName(state, diff.ortools_responder_id) : "—"}</td>
                          <td className="py-1 text-muted">{diff.mlp_responder_id ? responderName(state, diff.mlp_responder_id) : "—"}</td>
                          <td className="py-1 text-warn font-semibold">{diff.gnn_responder_id ? responderName(state, diff.gnn_responder_id) : "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <p className="text-[9.5px] text-faint leading-tight mt-1">
                Reported differences reflect verifiable mathematical metrics (distance, priority sum, assignment pairs). No speculative causal attribution is inferred.
              </p>
            </div>
          )}

          {/* Technical Details Toggle */}
          {comparison && (
            <div>
              <button
                onClick={() => setShowTechnicalDetails((v) => !v)}
                className="label flex items-center gap-1 text-[10.5px] text-muted hover:!text-text"
              >
                {showTechnicalDetails ? "▾" : "▸"} Checkpoints & Model Metadata
              </button>
              {showTechnicalDetails && (
                <div className="mt-1.5 rounded border border-line bg-panel-2 p-2 font-mono text-[10px] space-y-1 text-muted">
                  <div><b>Authoritative Engine:</b> OR-Tools CP-SAT (Integer Programming)</div>
                  {mlp?.available && (
                    <div>
                      <b>MLP Checkpoint:</b> {mlp.checkpoint_file} · <span className="text-faint">sha256:{mlp.checkpoint_digest.slice(0, 12)}…</span>
                    </div>
                  )}
                  {gnn?.available && (
                    <div>
                      <b>GNN Checkpoint:</b> {gnn.checkpoint_file} · <span className="text-faint">sha256:{gnn.checkpoint_digest.slice(0, 12)}…</span>
                    </div>
                  )}
                  <div>
                    <b>Evaluated At:</b> {comparison.evaluated_at} (Proposal: <code>{comparison.proposal_id ? shortId(comparison.proposal_id) : "none"}</code>)
                  </div>
                  {comparison.limitations.length > 0 && (
                    <div className="text-[9px] text-faint pt-1 border-t border-line">
                      {comparison.limitations.join(" ")}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Operator Safety Notice */}
          <div className="rounded border border-line/60 bg-panel px-2.5 py-1.5 text-[9.5px] text-faint leading-tight">
            <b className="text-text">OPERATOR SAFETY POLICY:</b> Neural advisory models are strictly read-only and unprivileged.
            Approval, rejection, and dispatch operate exclusively on the authoritative OR-Tools proposal and exact scene revision.
          </div>
        </div>
      )}
    </section>
  );
}

function StatBox({
  label,
  value,
  delta,
  sub,
}: {
  label: string;
  value: string;
  delta?: string;
  sub?: string;
}) {
  return (
    <div className="rounded border border-line bg-panel px-2 py-1">
      <div className="text-[8.5px] uppercase tracking-wider text-faint flex items-center justify-between">
        <span>{label}</span>
        {delta && <span className={delta.startsWith("+") ? "text-warn" : delta.startsWith("-") ? "text-ok" : "text-muted"}>{delta}</span>}
      </div>
      <div className="font-semibold truncate text-text text-[11px]">{value}</div>
      {sub && <div className="text-[8.5px] text-faint truncate">{sub}</div>}
    </div>
  );
}
