"use client";

import { useEffect, useState } from "react";
import type { RestorePlan, WallsPlan } from "@/lib/buildingMap";
import { Button } from "./ui";

export interface WallsConfirm {
  /** Operator accepted replacing restrictions that cannot be carried over. */
  replace: boolean;
  /** Operator accepted discarding map edits made on top of the walls. */
  discardEdits: boolean;
  /** Responders to move to their nearest walkable point (demo correction). */
  snap: string[];
}

type Props = {
  floorLabel: string;
  nameOf: (id: string) => string;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (c: WallsConfirm) => void;
} & ({ mode: "loading" } | { mode: "on"; plan: WallsPlan } | { mode: "off"; restore: RestorePlan });

/**
 * Confirmation for publishing or removing building walls. Both change the SHARED routing map for
 * everyone on the scenario, so nothing happens until the operator confirms here.
 */
export function WallsDialog(props: Props) {
  const { floorLabel, nameOf, busy, onCancel, onConfirm } = props;
  const [replace, setReplace] = useState(false);
  const [discardEdits, setDiscardEdits] = useState(false);
  const [snap, setSnap] = useState<string[]>([]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onCancel();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  let title = "Building walls";
  let body: React.ReactNode = <p className="text-muted">Checking the live map and its history…</p>;
  let canConfirm = false;
  let confirmLabel = "Confirm";

  if (props.mode === "on") {
    const { plan } = props;
    const responders = plan.onWalls.filter((p) => p.kind === "responder");
    const casualties = plan.onWalls.filter((p) => p.kind === "casualty");
    title = `Route around ${floorLabel} building walls?`;
    confirmLabel = "Publish walls";
    canConfirm = !plan.incompatible || replace;
    body = (
      <>
        <p>
          Publishes the {floorLabel} walls (0.25 m grid, doors open) as the <b>shared routing map</b> for everyone on this scenario. The planner
          then replans; any new recommendation still needs approval.
        </p>
        {plan.incompatible ? (
          <div className="rounded border border-danger/60 bg-tone-danger p-2 text-danger-text" data-testid="walls-incompatible">
            <p className="font-semibold">The current routing grid can&apos;t be carried over: {plan.incompatible}.</p>
            <p>
              Publishing replaces it and drops {plan.dropped.blockedCells} blocked and {plan.dropped.unknownCells} unknown cells
              {plan.baseRevision ? ` from map ${plan.baseRevision}` : ""}. {plan.preserved.obstacles} obstacle polygon(s) are kept.
            </p>
            <label className="mt-1 flex items-center gap-1.5">
              <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} data-testid="walls-replace" />
              Replace the existing restrictions
            </label>
          </div>
        ) : (
          <p data-testid="walls-preserved">
            Keeps every existing restriction{plan.baseRevision ? ` from map ${plan.baseRevision}` : ""}: {plan.preserved.blockedCells} blocked
            cell(s), {plan.preserved.unknownCells} unknown cell(s), {plan.preserved.obstacles} obstacle(s).
          </p>
        )}
        {plan.invalidates.length > 0 && (
          <p className="text-warn" data-testid="walls-invalidates">
            {plan.invalidates.length} dispatched route(s) cross the new walls and will be flagged <b>invalidated</b>: a replan and fresh approval are
            needed.
          </p>
        )}
        {plan.onWalls.length > 0 && (
          <div className="rounded border border-warn/60 bg-tone-warn p-2" data-testid="walls-on-wall">
            <p className="font-semibold text-warn">Standing on a wall line — no route can start or end there:</p>
            <ul className="mt-1 space-y-1">
              {casualties.map((p) => (
                <li key={p.id} data-on-wall={p.id}>
                  {nameOf(p.id)} — stays unreachable until its position is corrected (casualty positions come from cameras or the scenario).
                  {p.snapDistance_m != null && <span className="text-faint"> Nearest walkable point {p.snapDistance_m} m away.</span>}
                </li>
              ))}
              {responders.map((p) => (
                <li key={p.id} data-on-wall={p.id}>
                  {p.snap ? (
                    <label className="flex items-start gap-1.5">
                      <input
                        type="checkbox"
                        checked={snap.includes(p.id)}
                        onChange={(e) => setSnap((s) => (e.target.checked ? [...s, p.id] : s.filter((x) => x !== p.id)))}
                        data-testid={`walls-snap-${p.id}`}
                      />
                      <span>
                        Move {nameOf(p.id)} {p.snapDistance_m} m to the nearest walkable spot{" "}
                        <span className="sim-stripe rounded-[2px] px-1 text-[10px] font-bold text-warn">DEMO CORRECTION</span>
                        <span className="block text-faint">Updates the marked position, like Mark position. Left unticked, {nameOf(p.id)} gets no route.</span>
                      </span>
                    </label>
                  ) : (
                    <>{nameOf(p.id)} — no walkable spot within 2 m; mark the position again.</>
                  )}
                </li>
              ))}
            </ul>
            <p className="mt-1 text-faint">Walls are never opened under people.</p>
          </div>
        )}
      </>
    );
  } else if (props.mode === "off") {
    const { restore } = props;
    title = "Turn off building walls?";
    if (!restore.ok) {
      body = (
        <div className="rounded border border-danger/60 bg-tone-danger p-2 text-danger-text" data-testid="walls-restore-blocked">
          <p className="font-semibold">Can&apos;t restore safely.</p>
          <p>{restore.reason}</p>
        </div>
      );
    } else {
      confirmLabel = "Restore map";
      canConfirm = restore.laterEdits.length === 0 || discardEdits;
      body = (
        <>
          <p data-testid="walls-restore-source">
            Restores map <b>{restore.map.revision}</b>, the one in use before walls were applied, verified from{" "}
            {restore.source === "server-history" ? "the shared event log" : "this browser's backup (matched to the walls revision)"}. Planner then
            replans; dispatched routes crossing restored restrictions are flagged invalidated.
          </p>
          {restore.laterEdits.length > 0 && (
            <div className="rounded border border-warn/60 bg-tone-warn p-2" data-testid="walls-later-edits">
              <p className="font-semibold text-warn">
                The map was changed {restore.laterEdits.length} time(s) after walls were applied (e.g. Block route by another operator).
              </p>
              <p>Restoring discards those changes: {restore.laterEdits.join(", ")}.</p>
              <label className="mt-1 flex items-center gap-1.5">
                <input type="checkbox" checked={discardEdits} onChange={(e) => setDiscardEdits(e.target.checked)} data-testid="walls-discard" />
                Discard those map changes
              </label>
            </div>
          )}
        </>
      );
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onCancel}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid="walls-dialog"
        data-mode={props.mode}
        className="flex w-full max-w-lg flex-col gap-2 rounded-md border border-line-strong bg-panel p-3 text-[12px] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="text-[13px] font-semibold">{title}</h2>
        {body}
        <div className="mt-1 flex justify-end gap-2">
          <Button size="sm" onClick={onCancel} data-testid="walls-cancel">
            {props.mode === "off" && !props.restore.ok ? "Close" : "Cancel"}
          </Button>
          {props.mode !== "loading" && !(props.mode === "off" && !props.restore.ok) && (
            <Button size="sm" variant="primary" disabled={!canConfirm || busy} onClick={() => onConfirm({ replace, discardEdits, snap })} data-testid="walls-confirm">
              {confirmLabel}
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
