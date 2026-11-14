"use client";

import Link from "next/link";
import { ThemeToggle } from "@/components/theme";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { fallbackState } from "@/lib/fallback";
import { useFieldNotes } from "@/lib/fieldNotes";
import { responderName } from "@/lib/format";
import { proposalDiff } from "@/lib/insights";
import { useDeviceStatus, usePreviewFlags, useSensorVitals } from "@/lib/preview";
import { buildReport, download } from "@/lib/report";
import type { ActionResponse, SceneMap as SceneMapT, SimulationApproval, SimulationProposal } from "@/lib/types";
import { useAlerts } from "@/lib/useAlerts";
import { useSimulation, type LinkStatus } from "@/lib/useSimulation";
import { CameraView, cameraFeeds } from "./CamGrid";
import { FeedFrame, useFeedOrder, useSlotRects } from "./FeedStage";
import { FEED_STATUS_LABEL, MAP_FEED_ID, type FeedStatus } from "@/lib/feeds";
import { CommandDock } from "./CommandDock";
import { CommsPanel } from "./CommsPanel";
import { LoopStrip } from "./LoopStrip";
import { AlertStack, SettingsMenu, ShortcutHelp } from "./Overlays";
import { ProposalPanel } from "./ProposalPanel";
import { SceneMap } from "./SceneMap";
import { TasksPanel } from "./TasksPanel";
import { Timeline } from "./Timeline";
import { Button } from "./ui";
import { VictimStrip } from "./VictimStrip";
import { VoiceAssistantPanel } from "./VoiceAssistantPanel";

const LINK: Record<LinkStatus, { label: string; color: string }> = {
  connecting: { label: "connecting", color: "var(--muted)" },
  live: { label: "live", color: "var(--ok)" },
  polling: { label: "polling", color: "var(--warn)" },
  offline: { label: "offline", color: "var(--danger)" },
};

type FloorPlan = { url: string; opacity: number } | null;

export function CommandCenter() {
  const sim = useSimulation();
  const { events, link, pending, toasts, run, toast } = sim;
  const [fallback, setFallback] = useState(false);
  const fb = useMemo(() => fallbackState(), []);
  const state = fallback ? fb : sim.state;
  const readOnly = fallback;

  const { get: note, update: setNote } = useFieldNotes(state?.epoch);
  const { flags, set: setFlags, anyOn: previewOn } = usePreviewFlags();
  const [now, setNow] = useState(() => Date.now());
  const sensor = useSensorVitals(state, flags.sensorVitals);
  const device = useDeviceStatus(state, flags.deviceStatus, now);

  const [selected, setSelected] = useState<string | null>(null);
  const [fighterId, setFighterId] = useState<string | null>(null);
  const [editMap, setEditMap] = useState(false);
  const [placing, setPlacing] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const [help, setHelp] = useState(false);
  const [floorPlan, setFloorPlanState] = useState<FloorPlan>(null);
  const [operator, setOperator] = useState("demo-command");

  const proposal = state?.current_proposal_id ? state.proposals.find((p) => p.id === state.current_proposal_id) ?? null : null;
  const diff = useMemo(() => (state ? proposalDiff(state, proposal) : null), [state, proposal]);
  const { alerts, dismiss, muted, toggleMute, flashing } = useAlerts(fallback ? null : sim.state, events, proposal, now);

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    try {
      const saved = localStorage.getItem("aegis.operator");
      if (saved) setOperator(saved);
      const fp = localStorage.getItem("aegis.floorplan");
      if (fp) setFloorPlanState(JSON.parse(fp));
    } catch {
      /* storage unavailable */
    }
    return () => clearInterval(t);
  }, []);

  const setFloorPlan = (v: FloorPlan) => {
    setFloorPlanState(v);
    try {
      if (v) localStorage.setItem("aegis.floorplan", JSON.stringify(v));
      else localStorage.removeItem("aegis.floorplan");
    } catch {
      toast("info", "Floor plan too large to remember; it will reset on reload");
    }
  };

  const saveOperator = (v: string) => {
    setOperator(v);
    try {
      localStorage.setItem("aegis.operator", v);
    } catch {
      /* ignore */
    }
  };

  const actor = operator.trim() || "demo-command";
  const busy = pending !== null || readOnly;
  const fighters = useMemo(() => state?.scenario.responders ?? [], [state]);
  const activeFighter = fighters.find((r) => r.id === fighterId)?.id ?? fighters[0]?.id ?? null;

  /** All mutations go through here; disabled while the read-only fallback is shown. */
  const act = useCallback(
    <R,>(label: string, fn: () => Promise<ActionResponse<R>>) => {
      if (readOnly) {
        toast("info", "Fallback view is read-only");
        return Promise.resolve(null);
      }
      return run(label, fn);
    },
    [readOnly, run, toast],
  );

  const select = (id: string | null) => {
    setSelected(id);
    if (id && fighters.some((r) => r.id === id)) setFighterId(id);
  };

  const toggleCell = useCallback(
    (row: number, col: number) => {
      const map = state?.scenario.scene_map;
      if (!map?.occupancy_grid) return;
      const grid = map.occupancy_grid;
      const cells = grid.cells.slice();
      const i = row * grid.width + col;
      cells[i] = cells[i] === 100 ? 0 : 100;
      const base = map.revision.replace(/-ui-[a-z0-9]+$/, "");
      const next: SceneMapT = { ...map, revision: `${base}-ui-${Date.now().toString(36)}`, occupancy_grid: { ...grid, cells } };
      void act("Map update", () => api.updateMap(next)).then((r) => {
        if (r) toast("info", `Cell (${col}, ${row}) ${cells[i] === 100 ? "blocked" : "cleared"} — replanning`);
      });
    },
    [state, act, toast],
  );

  const place = useCallback(
    (x_m: number, y_m: number) => {
      if (!state || !activeFighter) return;
      setPlacing(false);
      void act("Position update", () => api.moveResponder(activeFighter, { frame_id: state.scenario.frame_id, x_m, y_m })).then(
        (r) => r && toast("info", `${responderName(state, activeFighter)} marked at (${x_m}, ${y_m}) m`),
      );
    },
    [state, activeFighter, act, toast],
  );

  const decide = useCallback(
    (p: SimulationProposal, decision: "approve" | "reject") =>
      void act(decision === "approve" ? "Approve" : "Reject", () => api.decide(p.id, p.revision, decision, actor, p.epoch)).then((r) => {
        const a = r?.result as SimulationApproval | null | undefined;
        if (a && decision === "approve") toast("info", `Approved rev ${a.proposal_revision} — dispatch is a separate step (D)`);
      }),
    [act, actor, toast],
  );
  const dispatch = useCallback(
    (p: SimulationProposal, approvalId: string) => void act("Dispatch", () => api.dispatch(p.id, p.revision, approvalId, actor, p.epoch)),
    [act, actor],
  );
  const replan = () => void act("Replan", () => api.replan(actor));

  // Feed swapping: map + cameras share one primary viewport and a secondary grid.
  const camFeeds = useMemo(() => cameraFeeds(state, fighters), [state, fighters]);
  const feedIds = useMemo(() => [MAP_FEED_ID, ...camFeeds.map((f) => f.id)], [camFeeds]);
  const { order, primaryId, swapFeed, moving } = useFeedOrder(feedIds);
  const stageRef = useRef<HTMLDivElement>(null);
  const { slotRefs, rects } = useSlotRects(stageRef, feedIds.length, !!state);
  const [camStatus, setCamStatus] = useState<Record<string, FeedStatus>>({});
  const onCamStatus = useCallback((id: string, s: FeedStatus) => setCamStatus((p) => (p[id] === s ? p : { ...p, [id]: s })), []);
  // Map tools need the map on screen: bring it back to the primary viewport when they're switched on.
  useEffect(() => {
    if ((placing || editMap) && primaryId !== MAP_FEED_ID) swapFeed(MAP_FEED_ID);
  }, [placing, editMap, primaryId, swapFeed]);

  // Keyboard shortcuts (ignored while typing). Space is handled by push-to-talk.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key;
      if (k === "Escape") {
        setPlacing(false);
        setEditMap(false);
        setDrawer(false);
        setHelp(false);
        setSelected(null);
      } else if (k === "?") setHelp((h) => !h);
      else if (k === "a" || k === "A") {
        if (proposal?.status === "pending_approval" && pending === null) decide(proposal, "approve");
      } else if (k === "d" || k === "D") {
        const ap = proposal?.approval_id ? state?.approvals.find((x) => x.id === proposal.approval_id) : undefined;
        if (proposal?.status === "approved" && ap?.status === "active" && pending === null) dispatch(proposal, ap.id);
      } else if (k === "b" || k === "B") {
        setEditMap((v) => !v);
        setPlacing(false);
      } else if (k === "m" || k === "M") {
        setPlacing((v) => !v);
        setEditMap(false);
      } else if (k === "l" || k === "L") setDrawer((v) => !v);
      else if (/^[1-9]$/.test(k)) {
        const r = fighters[Number(k) - 1];
        if (r) setFighterId(r.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [proposal, state, pending, fighters, decide, dispatch]);

  const expand = (id: string) => {
    if (id !== MAP_FEED_ID) {
      setPlacing(false);
      setEditMap(false);
    }
    swapFeed(id);
    const rid = camFeeds.find((f) => f.id === id)?.responderId;
    if (rid) select(rid);
  };

  if (!state) {
    return (
      <main className="relative flex h-screen flex-col items-center justify-center gap-3 text-center">
        <ThemeToggle className="absolute right-3 top-3" />
        <Logo />
        <p className="text-sm text-muted">{link === "offline" ? "Cannot reach the AEGIS backend." : "Connecting to backend…"}</p>
        {link === "offline" && (
          <>
            <pre className="mono rounded border border-line bg-panel px-3 py-2 text-left text-[11px] text-muted">uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 8000</pre>
            <Button size="sm" onClick={() => setFallback(true)}>
              Show seeded fallback scenario (not live)
            </Button>
          </>
        )}
      </main>
    );
  }

  const mapStatus: { status: FeedStatus; label: string; color: string } = fallback
    ? { status: "connected", label: "SEEDED FALLBACK · NOT LIVE", color: "var(--warn)" }
    : {
        status: link === "live" || link === "polling" ? "connected" : link === "connecting" ? "connecting" : "disconnected",
        label: LINK[link].label.toUpperCase(),
        color: LINK[link].color,
      };
  const feedName = (id: string) => (id === MAP_FEED_ID ? "SCENE MAP" : camFeeds.find((f) => f.id === id)?.name ?? id);
  const feedStatusText = (id: string) => (id === MAP_FEED_ID ? mapStatus.label : FEED_STATUS_LABEL[camStatus[id] ?? "disconnected"].short);
  const mapView = (primary: boolean) => (
          <section className="panel relative flex h-full min-h-0 flex-col" aria-label="Scene map">
            <div className="flex items-center gap-2 border-b border-line px-2.5 py-1">
              <h2 className="label !text-text">Scene map</h2>
              <span className="mono shrink-0 text-[9.5px] font-semibold tracking-[0.12em]" style={{ color: mapStatus.color }} data-feed-status={mapStatus.status}>
                {mapStatus.label}
              </span>
              {primary && (
                <span className="mono truncate text-[10.5px] text-muted">
                  {state.scenario.casualties.length} victims · {fighters.length} firefighters · {state.scenario.frame_id}
                </span>
              )}
              <div className={`ml-auto flex shrink-0 items-center gap-2 ${primary ? "" : "hidden"}`}>
                {pending && <span className="mono blink text-[10.5px] text-accent">{pending}…</span>}
                <Button
                  size="sm"
                  variant={placing ? "warn" : "ghost"}
                  disabled={busy || !activeFighter}
                  onClick={() => {
                    setPlacing((v) => !v);
                    setEditMap(false);
                  }}
                  title="Shortcut: M"
                >
                  {placing ? "Click map…" : `Mark ${responderName(state, activeFighter ?? "")}`}
                </Button>
                <Button
                  size="sm"
                  variant={editMap ? "warn" : "ghost"}
                  disabled={busy || !state.scenario.scene_map?.occupancy_grid}
                  onClick={() => {
                    setEditMap((v) => !v);
                    setPlacing(false);
                  }}
                  title="Shortcut: B"
                >
                  {editMap ? "Done" : "Block route"}
                </Button>
              </div>
            </div>
            {primary && (editMap || placing) && (
              <div className="sim-stripe border-b border-warn/30 px-3 py-0.5 text-[11px] text-warn">
                {placing
                  ? `Click where ${responderName(state, activeFighter ?? "")} is, as seen on the bodycam. Esc to cancel.`
                  : "Click cells to toggle blocked / free. Each change publishes a new map revision and triggers replanning."}
              </div>
            )}
            <div className={`min-h-0 flex-1 ${primary ? "px-1.5 pb-8 pt-1.5" : "pointer-events-none p-1"}`}>
              <SceneMap
                state={state}
                proposal={proposal}
                selectedId={selected}
                onSelect={select}
                editMode={editMap}
                onToggleCell={toggleCell}
                now={now}
                onPlace={placing ? place : null}
                focusResponderId={activeFighter}
                ghostRoutes={diff?.ghostRoutes}
                floorPlan={floorPlan}
                flashing={flashing}
              />
            </div>
            {primary && <MapLegend ghost={!!diff?.ghostRoutes.length} />}
          </section>
  );

  return (
    <main className="flex h-screen min-h-[640px] flex-col gap-1.5 overflow-hidden p-1.5">
      <header className="flex items-center gap-3 rounded-md border border-line bg-panel px-2.5 py-1">
        <Logo />
        <div className="min-w-0 flex-1">
          <LoopStrip state={state} proposal={proposal} events={fallback ? [] : events} compact />
        </div>
        <ThemeToggle compact className="shrink-0" />
        <div className="sim-stripe mono shrink-0 rounded border border-warn/60 px-2 py-0.5 text-[10.5px] font-bold tracking-[0.16em] text-warn">SIMULATION</div>
        <button onClick={toggleMute} className="shrink-0 text-[13px]" title={muted ? "Alert sounds off" : "Alert sounds on"} aria-label="Toggle alert sound">
          {muted ? "🔕" : "🔔"}
        </button>
        <SettingsMenu
          flags={flags}
          onFlags={setFlags}
          fallback={fallback}
          onFallback={setFallback}
          floorPlan={floorPlan}
          onFloorPlan={setFloorPlan}
          onExport={() => download(`aegis-incident-e${state.epoch}-${new Date().toISOString().slice(0, 19).replace(/:/g, "")}.md`, buildReport(state, fallback ? [] : events, note, actor))}
          onHelp={() => setHelp(true)}
        />
        <div className="mono flex shrink-0 items-center gap-1 text-[10.5px]" style={{ color: LINK[link].color }} title={`Backend link: ${LINK[link].label}`}>
          <span className={`h-2 w-2 rounded-full ${link === "live" ? "" : "blink"}`} style={{ background: LINK[link].color }} />
          {LINK[link].label}
        </div>
        <input
          value={operator}
          onChange={(e) => saveOperator(e.target.value)}
          aria-label="Operator ID"
          title="Operator ID recorded on decisions (not authenticated)"
          className="mono w-28 shrink-0 rounded border border-line-strong bg-bg px-1.5 py-0.5 text-[10.5px] outline-none focus:border-accent"
        />
        <Button
          size="sm"
          disabled={busy}
          onClick={() => {
            if (window.confirm("Reset the simulation to the seeded demo scenario?")) void act("Reset", () => api.reset());
          }}
        >
          Reset
        </Button>
      </header>

      {(fallback || previewOn) && (
        <div className="sim-stripe flex items-center gap-3 rounded border border-warn/50 px-3 py-0.5 text-[11px] text-warn">
          {fallback && <b>FALLBACK — seeded scenario, not live. Actions disabled.</b>}
          {previewOn && <span>PREVIEW DATA ON — {[flags.sensorVitals && "sensor heart rate", flags.deviceStatus && "device status"].filter(Boolean).join(", ")} are mock values.</span>}
          {fallback && (
            <button onClick={() => setFallback(false)} className="ml-auto underline">
              return to live
            </button>
          )}
        </div>
      )}

      <div ref={stageRef} className="relative grid min-h-0 flex-1 grid-cols-[minmax(0,68fr)_minmax(0,32fr)] grid-rows-[minmax(0,1fr)_clamp(168px,24vh,224px)] gap-1.5">
        {/* primary viewport slot — top left, largest (the active feed is positioned over it) */}
        <div ref={slotRefs[0]} className="min-h-0" />

        {/* right — secondary feed grid + Ask AEGIS voice assistant + Responder Comms */}
        <div className="row-span-1 flex min-h-0 flex-col gap-1.5">
          <div className="panel p-1.5">
            <div className="grid grid-cols-2 gap-1.5">
              {order.slice(1).map((_, i) => (
                <div key={i} ref={slotRefs[i + 1]} className="aspect-video w-full" />
              ))}
            </div>
          </div>
          <VoiceAssistantPanel scenarioId={state.scenario_id} />
          <CommsPanel
            state={state}
            proposal={proposal}
            fighterId={activeFighter}
            onStatus={(rid, status) => void act("Responder update", () => api.updateResponder(rid, status))}
            placing={placing}
            onPlace={(on) => {
              setPlacing(on);
              if (on) setEditMap(false);
            }}
            busy={busy}
          />
        </div>

        {/* bottom — victim strip */}
        <VictimStrip
          state={state}
          proposal={proposal}
          selectedId={selected}
          onSelect={select}
          busy={busy}
          now={now}
          note={note}
          onNote={setNote}
          sensor={sensor}
          flashing={flashing}
          onAssess={(cid, sev) => void act("Assessment", () => api.assess(cid, sev, actor))}
          onAssociate={(tid, cid, create) => void act(create ? "Confirm victim" : "Link track", () => api.associate(tid, cid, create, actor))}
        />

        <CommandDock
          state={state}
          proposal={proposal}
          diff={diff}
          pending={pending}
          now={now}
          readOnly={readOnly}
          device={device}
          onDecide={decide}
          onDispatch={dispatch}
          onReplan={replan}
          onOpenDetails={() => setDrawer(true)}
        />

        {/* Feed layer: every feed stays mounted in a fixed DOM order; a swap only moves it over another slot. */}
        {feedIds.map((id) => {
          const slot = order.indexOf(id);
          const primary = slot === 0;
          const cam = camFeeds.find((f) => f.id === id);
          const rid = cam?.responderId;
          return (
            <FeedFrame
              key={id}
              id={id}
              name={feedName(id)}
              statusText={feedStatusText(id)}
              rect={rects[slot]}
              primary={primary}
              moving={moving.includes(id)}
              onExpand={expand}
              className={`${!primary && rid && rid === activeFighter ? "ring-2 ring-accent" : ""} ${rid && flashing.has(rid) ? "flash" : ""}`}
            >
              {cam ? <CameraView feed={cam} state={state} proposal={proposal} now={now} primary={primary} onStatus={onCamStatus} /> : mapView(primary)}
            </FeedFrame>
          );
        })}
      </div>

      {drawer && (
        <div className="fixed inset-0 z-40 flex justify-end bg-black/50" onClick={() => setDrawer(false)}>
          <aside className="scrollbar-thin flex h-full w-[440px] flex-col gap-2 overflow-y-auto border-l border-line bg-bg p-2" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between px-1">
              <span className="label !text-text">Details</span>
              <button onClick={() => setDrawer(false)} className="text-muted hover:text-text" aria-label="Close">
                ✕
              </button>
            </div>
            <ProposalPanel state={state} proposal={proposal} pending={pending} now={now} onDecide={decide} onDispatch={dispatch} onReplan={replan} />
            <div className="h-[360px] shrink-0">
              <TasksPanel state={state} busy={busy} onSimulate={(a, kind) => void act(`Simulated ${kind}`, () => api.simulateDevice(a.id, kind))} />
            </div>
            <div className="h-[420px] shrink-0">
              <Timeline events={events} />
            </div>
          </aside>
        </div>
      )}

      {help && <ShortcutHelp onClose={() => setHelp(false)} />}
      <AlertStack alerts={alerts} onDismiss={dismiss} />

      <div className="pointer-events-none fixed bottom-3 left-1/2 z-50 flex w-[28rem] -translate-x-1/2 flex-col gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            role="status"
            className={`pointer-events-auto rounded border px-3 py-2 text-xs shadow-lg ${
              t.kind === "error" ? "border-danger/60 bg-tone-danger text-danger-text" : "border-line-strong bg-panel-2 text-text"
            }`}
          >
            {t.text}
          </div>
        ))}
      </div>
    </main>
  );
}

function Logo() {
  return (
    <Link href="/" title="Back to AEGIS overview" className="flex shrink-0 items-center gap-1.5">
      <svg width="20" height="20" viewBox="0 0 24 24" aria-hidden>
        <path d="M12 2 L21 6 V12 C21 17 17 21 12 22 C7 21 3 17 3 12 V6 Z" fill="none" stroke="var(--accent)" strokeWidth="1.8" />
        <path d="M12 7 V17 M7 12 H17" stroke="var(--accent)" strokeWidth="1.8" />
      </svg>
      <span className="mono text-[14px] font-bold tracking-[0.2em]">AEGIS</span>
    </Link>
  );
}

function MapLegend({ ghost }: { ghost: boolean }) {
  const item = (el: React.ReactNode, text: string) => (
    <span className="flex items-center gap-1">
      {el}
      {text}
    </span>
  );
  return (
    <div className="mono pointer-events-none absolute bottom-1.5 left-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 rounded bg-bg/80 px-2 py-1 text-[9.5px] text-muted">
      {item(<span className="h-2 w-2 rounded-full bg-[var(--sev-immediate)]" />, "victim")}
      {item(<span className="h-2 w-2 rounded-full border border-dashed border-[var(--sev-unassessed)]" />, "unassessed")}
      {item(<span className="h-2 w-2 rounded-sm border-2 border-accent" />, "firefighter")}
      {item(<span className="w-3.5 border-t-2 border-dashed border-accent" />, "proposed")}
      {item(<span className="w-3.5 border-t-2 border-warn" />, "dispatched")}
      {item(<span className="w-3.5 border-t-2 border-ok" />, "ack'd")}
      {ghost && item(<span className="w-3.5 border-t border-dotted border-muted" />, "previous plan")}
      {item(<span className="h-2 w-2 border border-dashed border-warn" />, "CV detection")}
    </div>
  );
}
