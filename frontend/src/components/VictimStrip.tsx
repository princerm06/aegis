"use client";

import { useLayoutEffect, useMemo, useRef, useState } from "react";
import type { FieldNote, Tri } from "@/lib/fieldNotes";
import { SEVERITIES, SEVERITY_META, ageSeconds, humanize, responderName, shortId, trackLabel } from "@/lib/format";
import { signLines, type SignLine } from "@/lib/insights";
import type { SensorReading } from "@/lib/preview";
import type { Casualty, Severity, SimulationProposal, SimulationState, TrackRecord } from "@/lib/types";
import { Badge, Button } from "./ui";

interface Props {
  state: SimulationState;
  proposal: SimulationProposal | null;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onAssess: (casualtyId: string, severity: Severity) => void;
  onAssociate: (trackId: string, casualtyId: string, create: boolean) => void;
  note: (id: string) => FieldNote;
  onNote: (id: string, patch: Partial<FieldNote>) => void;
  sensor: (id: string) => SensorReading | null;
  flashing: Set<string>;
  busy: boolean;
  now: number;
}

const STALE_S = 30;
const TRI_STYLE: Record<Tri, string> = {
  yes: "border-danger/60 text-danger bg-danger/10",
  no: "border-ok/50 text-ok",
  unknown: "border-line-strong text-faint",
};
const SIGN_TONE: Record<SignLine["tone"], string> = { attention: "text-warn", neutral: "text-muted", untestable: "text-faint italic" };

export function VictimStrip(props: Props) {
  const { state, note, sensor, selectedId, onSelect, now } = props;
  const cas = state.scenario.casualties;
  const unlinked = state.tracks.filter((t) => !t.casualty_id);
  const count = (s: Severity) => cas.filter((c) => c.medical_assessment?.severity === s).length;
  const unassessed = cas.filter((c) => !c.medical_assessment).length;
  const bleeding = cas.filter((c) => note(c.id).bleeding === "yes" || signsFor(state, c.id)?.signs.visible_blood?.value === "detected").length;
  const pulsed = cas.filter((c) => note(c.id).pulse || sensor(c.id)).length;
  // Pending queue, freshest first. Display order only — nothing is auto-confirmed or discarded.
  const queue = useMemo(
    () =>
      state.tracks
        .filter((t) => !t.casualty_id)
        .sort((a, b) => +new Date(b.latest_observation.observed_at) - +new Date(a.latest_observation.observed_at)),
    [state.tracks],
  );
  const [queueOpen, setQueueOpen] = useState(true);

  const wrap = useRef<HTMLDivElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const [anchor, setAnchor] = useState(0);
  const editing = cas.find((c) => c.id === selectedId) ?? null;

  useLayoutEffect(() => {
    if (!editing || !scroller.current || !wrap.current) return;
    const card = scroller.current.querySelector<HTMLElement>(`[data-cid="${editing.id}"]`);
    if (!card) return;
    card.scrollIntoView({ block: "nearest", inline: "nearest" });
    const w = wrap.current.getBoundingClientRect();
    const c = card.getBoundingClientRect();
    setAnchor(Math.max(0, Math.min(c.left - w.left, w.width - 460)));
  }, [editing]);

  return (
    <section ref={wrap} className="panel relative flex min-w-0" aria-label="Victims and pending observations">
      {/* summary */}
      <div className="flex w-[150px] shrink-0 flex-col justify-between border-r border-line px-3 py-2">
        <div>
          <div className="label !text-text">Victims</div>
          <div className="mono text-3xl font-semibold leading-none">{cas.length}</div>
          {unlinked.length > 0 && (
            <button onClick={() => setQueueOpen((v) => !v)} className="text-[10.5px] text-warn hover:underline" title={queueOpen ? "Collapse observation queue" : "Expand observation queue"}>
              +{unlinked.length} unconfirmed {queueOpen ? "▾" : "▸"}
            </button>
          )}
        </div>
        <div className="mono space-y-0.5 text-[10.5px]">
          <div className="flex gap-2">
            {(["immediate", "delayed", "minor"] as Severity[]).map((s) => (
              <span key={s} style={{ color: SEVERITY_META[s].color }}>
                {SEVERITY_META[s].short} {count(s)}
              </span>
            ))}
          </div>
          <div className={unassessed ? "text-warn" : "text-faint"}>{unassessed} unassessed</div>
          <div className={bleeding ? "text-danger" : "text-faint"}>{bleeding} blood seen</div>
          <div className="text-faint">
            {pulsed}/{cas.length} pulse known
          </div>
        </div>
      </div>

      {/* confirmed casualties — visual priority, horizontal scroll */}
      <div ref={scroller} className="scrollbar-thin flex min-w-0 flex-1 gap-2 overflow-x-auto overflow-y-hidden p-2" aria-label="Confirmed victims">
        {cas.map((c) => (
          <VictimCard key={c.id} c={c} {...props} />
        ))}
        {cas.length === 0 && <p className="self-center px-4 text-xs text-muted">No confirmed victims yet.</p>}
      </div>

      {/* pending CV observations — separate queue, vertical scroll, collapsible */}
      {unlinked.length > 0 && queueOpen && (
        <div className="flex w-[252px] shrink-0 flex-col border-l border-line" aria-label="Pending observations">
          <div className="flex items-center gap-1.5 border-b border-line px-2 py-1">
            <span className="label !text-[9.5px] !text-warn">Observations</span>
            <span className="mono text-[10px] text-warn">{unlinked.length} pending</span>
            <button onClick={() => setQueueOpen(false)} className="mono ml-auto text-muted hover:text-text" aria-label="Collapse observation queue" title="Collapse">
              ›
            </button>
          </div>
          <div className="scrollbar-thin min-h-0 flex-1 space-y-1 overflow-y-auto p-1.5">
            {queue.map((t) => (
              <DetectionRow key={t.track_id} track={t} {...props} />
            ))}
          </div>
        </div>
      )}
      {unlinked.length > 0 && !queueOpen && (
        <button
          onClick={() => setQueueOpen(true)}
          className="flex w-8 shrink-0 flex-col items-center justify-center gap-1 border-l border-line text-warn hover:bg-panel-2"
          aria-label={`Expand observation queue, ${unlinked.length} pending`}
          title="Expand observation queue"
        >
          <span className="mono text-[11px] font-bold [writing-mode:vertical-rl]">OBS {unlinked.length}</span>
          <span className="mono text-[11px]">‹</span>
        </button>
      )}

      {editing && (
        <Editor
          key={editing.id}
          c={editing}
          {...props}
          style={{ left: anchor }}
          onClose={() => onSelect(null)}
          track={state.tracks.find((t) => t.casualty_id === editing.id)}
          now={now}
        />
      )}
    </section>
  );
}

function signsFor(state: SimulationState, cid: string) {
  const t = state.tracks.find((x) => x.casualty_id === cid);
  if (!t?.latest_visible_signs) return null;
  return { signs: t.latest_visible_signs, at: t.signs_observed_at ?? t.latest_observation.observed_at };
}

function PriorityBadge({ c }: { c: Casualty }) {
  const sev = c.medical_assessment?.severity ?? "unassessed";
  const m = SEVERITY_META[sev];
  return sev === "unassessed" ? (
    <Badge className="border-dashed !text-text">unassessed</Badge>
  ) : (
    <span
      className="mono rounded px-1.5 py-px text-[9.5px] font-bold uppercase tracking-wider"
      style={{ background: m.color, color: sev === "deceased" ? "var(--on-sev-deceased)" : "var(--on-sev)" }}
    >
      {m.label}
    </span>
  );
}

function PulseLine({ n, s, now }: { n: FieldNote; s: SensorReading | null; now: number }) {
  if (s)
    return (
      <span className="mono text-[11px] text-accent" title="Preview sensor data (mock)">
        ♥ {s.bpm} bpm <span className="rounded bg-warn/20 px-1 text-[8.5px] text-warn">PREVIEW</span>
      </span>
    );
  if (n.pulse)
    return (
      <span className="mono text-[11px] text-accent" title={humanize(n.pulse.source)}>
        ♥ {n.pulse.bpm} bpm <span className="text-faint">· reported {ageSeconds(n.pulse.at, now)}s</span>
      </span>
    );
  return <span className="mono text-[11px] text-faint">♥ — no pulse yet</span>;
}

function VictimCard({ c, state, proposal, selectedId, onSelect, note, sensor, flashing, now }: Props & { c: Casualty }) {
  const sev = c.medical_assessment?.severity ?? "unassessed";
  const n = note(c.id);
  const sel = selectedId === c.id;
  const track = state.tracks.find((t) => t.casualty_id === c.id);
  const sg = signsFor(state, c.id);
  const lines = sg ? signLines(sg.signs).sort((a, b) => (a.tone === "attention" ? -1 : 0) - (b.tone === "attention" ? -1 : 0)).slice(0, 2) : [];
  const task = state.assignments.find((a) => a.casualty_id === c.id && a.status !== "completed");
  const proposed =
    proposal && (proposal.status === "pending_approval" || proposal.status === "approved")
      ? proposal.assignments.find((a) => a.casualty_id === c.id)
      : undefined;
  const done = state.resolved_casualty_ids.includes(c.id);
  const reasons = proposal?.reasons ?? state.last_recommendation?.reasons ?? {};
  const camAge = track ? ageSeconds(track.latest_observation.observed_at, now) : null;

  return (
    <article
      data-cid={c.id}
      onClick={() => onSelect(sel ? null : c.id)}
      className={`flex w-[214px] shrink-0 cursor-pointer flex-col gap-1 rounded border bg-panel-2 p-2 transition ${
        sel ? "border-text/70" : "border-line hover:border-line-strong"
      } ${flashing.has(c.id) ? "flash" : ""}`}
      style={{ boxShadow: `inset 3px 0 0 ${SEVERITY_META[sev].color}` }}
    >
      <div className="flex items-center gap-1.5 pl-1">
        <span className="mono text-[13px] font-semibold">{shortId(c.id)}</span>
        {camAge !== null && (
          <span className={`mono text-[9.5px] ${camAge > STALE_S ? "text-warn" : "text-accent"}`}>
            ◉ {camAge > STALE_S ? `stale ${camAge}s` : `${Math.round(track!.latest_observation.confidence * 100)}%`}
          </span>
        )}
        <span className="ml-auto">
          <PriorityBadge c={c} />
        </span>
      </div>
      <div className="pl-1">
        <PulseLine n={n} s={sensor(c.id)} now={now} />
      </div>
      <div className="flex flex-wrap gap-1 pl-1">
        {(
          [
            ["bleed", n.bleeding],
            ["wound", n.wound],
            ["breath", n.breathing],
          ] as const
        ).map(([k, v]) => (
          <span key={k} className={`mono rounded border px-1 text-[9.5px] uppercase ${TRI_STYLE[v]}`}>
            {k} {v === "yes" ? "yes" : v === "no" ? "no" : "?"}
          </span>
        ))}
      </div>
      {lines.length > 0 && (
        <ul className="pl-1">
          {lines.map((l) => (
            <li key={l.key} className={`truncate text-[10px] ${SIGN_TONE[l.tone]}`}>
              cam: {l.text}
              {l.confidence !== null && <span className="mono text-faint"> {Math.round(l.confidence * 100)}%</span>}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-auto truncate pl-1 text-[10px] text-muted">
        {done ? (
          <span className="text-ok">✓ reached · complete</span>
        ) : task ? (
          <>
            <span className="text-text">{responderName(state, task.responder_id)}</span> · {task.status}
          </>
        ) : proposed ? (
          <span className="text-accent">proposed → {responderName(state, proposed.responder_id)}</span>
        ) : reasons[c.id]?.length ? (
          <>unassigned · {humanize(reasons[c.id][0])}</>
        ) : (
          "no task"
        )}
      </div>
    </article>
  );
}

function Editor({
  c,
  state,
  onAssess,
  note,
  onNote,
  busy,
  track,
  now,
  style,
  onClose,
}: Props & { c: Casualty; track?: TrackRecord; style: React.CSSProperties; onClose: () => void }) {
  const n = note(c.id);
  const [bpm, setBpm] = useState("");
  const sg = signsFor(state, c.id);
  return (
    <div
      className="absolute bottom-full z-30 mb-2 w-[460px] rounded-md border border-line-strong bg-panel p-3 shadow-2xl shadow-black/60"
      style={style}
      onClick={(e) => e.stopPropagation()}
    >
      <div className="mb-2 flex items-center gap-2">
        <span className="mono text-sm font-semibold">{shortId(c.id)}</span>
        <PriorityBadge c={c} />
        <span className="mono text-[10px] text-muted">
          ({c.position.x_m.toFixed(1)}, {c.position.y_m.toFixed(1)}) m
        </span>
        <button onClick={onClose} className="ml-auto text-muted hover:text-text" aria-label="Close">
          ✕
        </button>
      </div>

      <p className="label mb-1 !text-[9.5px]">Command priority (human decision)</p>
      <div className="grid grid-cols-5 gap-1">
        {SEVERITIES.map((s) => {
          const m = SEVERITY_META[s];
          const active = c.medical_assessment?.severity === s;
          return (
            <button
              key={s}
              disabled={busy || active}
              onClick={() => onAssess(c.id, s)}
              className="mono rounded border py-1 text-[10px] font-bold uppercase disabled:cursor-default"
              style={{
                borderColor: m.color,
                background: active ? m.color : "transparent",
                color: active ? (s === "deceased" ? "var(--on-sev-deceased)" : "var(--on-sev)") : m.color,
                opacity: busy && !active ? 0.5 : 1,
              }}
            >
              {m.label}
            </button>
          );
        })}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <div className="space-y-2">
          <p className="label !text-[9.5px]">Operator observations</p>
          <TriPicker label="Bleeding" v={n.bleeding} onChange={(v) => onNote(c.id, { bleeding: v })} />
          <TriPicker label="Visible wound" v={n.wound} onChange={(v) => onNote(c.id, { wound: v })} />
          <TriPicker label="Breathing" v={n.breathing} onChange={(v) => onNote(c.id, { breathing: v })} />
          <input
            defaultValue={n.woundNote}
            onBlur={(e) => e.target.value !== n.woundNote && onNote(c.id, { woundNote: e.target.value.slice(0, 120) })}
            placeholder="What's visible / reported…"
            className="w-full rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
          />
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              const v = Number(bpm);
              if (!bpm || !Number.isFinite(v) || v > 300) return;
              onNote(c.id, { pulse: { bpm: Math.round(v), at: new Date().toISOString(), source: "responder_report" } });
              setBpm("");
            }}
          >
            <input
              value={bpm}
              onChange={(e) => setBpm(e.target.value.replace(/[^0-9]/g, ""))}
              inputMode="numeric"
              placeholder="pulse bpm"
              className="mono w-24 rounded border border-line-strong bg-bg px-2 py-1 text-[11px] outline-none focus:border-accent"
            />
            <Button size="sm" disabled={!bpm}>
              Log reported pulse
            </Button>
          </form>
        </div>

        <div>
          <p className="label mb-1 !text-[9.5px]">Camera signs (advisory)</p>
          {sg ? (
            <>
              <p className="mono mb-1 text-[9.5px] text-faint">
                {track?.camera ? `${track.camera.camera_id}/${track.camera.local_track_id} · ` : ""}
                observed {ageSeconds(sg.at, now)}s ago · {sg.signs.window_s}s window
                {ageSeconds(sg.at, now) > STALE_S && <span className="text-warn"> · STALE</span>}
              </p>
              <ul className="space-y-0.5">
                {signLines(sg.signs).map((l) => (
                  <li key={l.key} className={`flex justify-between gap-2 text-[10.5px] ${SIGN_TONE[l.tone]}`}>
                    <span>{l.text}</span>
                    {l.confidence !== null && <span className="mono text-faint">{Math.round(l.confidence * 100)}%</span>}
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="text-[10.5px] text-faint">{track ? "No signs reported for this track." : "Not linked to a camera track."}</p>
          )}
          <p className="mt-2 text-[9.5px] leading-snug text-faint">
            Camera signs are unvalidated measurements and never set priority. Operator entries are stored in this browser only.
          </p>
        </div>
      </div>
    </div>
  );
}

function TriPicker({ label, v, onChange }: { label: string; v: Tri; onChange: (v: Tri) => void }) {
  return (
    <div className="flex items-center gap-2">
      <span className="w-20 text-[10.5px] text-muted">{label}</span>
      <div className="grid flex-1 grid-cols-3 overflow-hidden rounded border border-line-strong">
        {(["yes", "no", "unknown"] as Tri[]).map((o) => (
          <button key={o} onClick={() => onChange(o)} className={`mono py-0.5 text-[9.5px] uppercase ${v === o ? TRI_STYLE[o] : "text-faint hover:text-text"}`}>
            {o === "unknown" ? "?" : o}
          </button>
        ))}
      </div>
    </div>
  );
}

function DetectionRow({ track, state, busy, now, selectedId, onSelect, onAssociate, flashing }: Props & { track: TrackRecord }) {
  const [target, setTarget] = useState("");
  const age = ageSeconds(track.latest_observation.observed_at, now);
  const stale = age > STALE_S;
  const conf = Math.round(track.latest_observation.confidence * 100);
  const nextId = () => {
    const nums = state.scenario.casualties.map((c) => Number(c.id.match(/(\d+)$/)?.[1] ?? 0));
    return `casualty-${String(Math.max(0, ...nums) + 1).padStart(3, "0")}`;
  };
  return (
    <article
      onClick={() => onSelect(selectedId === track.track_id ? null : track.track_id)}
      title={`Track ${track.track_id}\ncamera ${track.camera?.camera_id ?? "unknown"} · ${track.camera?.local_track_id ?? "—"}\n${track.observation_count} obs · seen ${age}s ago`}
      className={`cursor-pointer rounded border border-dashed bg-panel-2 px-1.5 py-1 ${
        selectedId === track.track_id ? "border-warn" : "border-warn/50 hover:border-warn/80"
      } ${flashing.has(track.track_id) ? "flash" : ""}`}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <span className="mono min-w-0 flex-1 truncate text-[11.5px] font-semibold text-warn">{trackLabel(track)}</span>
        <span className={`mono shrink-0 text-[10px] ${stale ? "text-warn" : "text-accent"}`}>
          {conf}% · {age}s{stale ? " STALE" : ""}
        </span>
      </div>
      <div className="mt-1 flex gap-1" onClick={(e) => e.stopPropagation()}>
        <Button size="sm" variant="warn" disabled={busy} onClick={() => onAssociate(track.track_id, nextId(), true)} title="Confirm as a new victim">
          Confirm
        </Button>
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          aria-label={`Link ${trackLabel(track)} to an existing victim`}
          className="mono min-w-0 flex-1 rounded border border-line-strong bg-bg px-1 text-[10.5px]"
        >
          <option value="">same as…</option>
          {state.scenario.casualties.map((c) => (
            <option key={c.id} value={c.id}>
              {shortId(c.id)}
            </option>
          ))}
        </select>
        <Button size="sm" disabled={busy || !target} onClick={() => onAssociate(track.track_id, target, false)}>
          Link
        </Button>
      </div>
    </article>
  );
}
