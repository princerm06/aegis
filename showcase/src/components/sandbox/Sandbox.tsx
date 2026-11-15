'use client';

import { useReducer } from 'react';
import Link from 'next/link';
import { ThemeToggle } from '@/components/theme';
import { CORRIDORS, createInitialState, sandboxReducer, type Priority } from '../../lib/sandbox/engine';
import { SandboxMap } from './SandboxMap';
import { CameraIntelligence, HardwareContext, NeuralContext, ResearchIntelligence, SystemIntroduction } from './SystemContext';

const control = 'rounded-lg border border-line bg-bg px-3 py-2 text-xs font-medium text-text transition hover:border-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:cursor-not-allowed disabled:opacity-40';
const panel = 'lp-card rounded-2xl border border-line bg-panel p-4 sm:p-5';

export function Sandbox() {
  const [state, dispatch] = useReducer(sandboxReducer, undefined, createInitialState);
  const revision = state.revision;
  const active = state.tasks.filter(task => task.status !== 'done' && task.status !== 'replaced');
  const history = state.tasks.filter(task => task.status === 'done' || task.status === 'replaced');
  const completed = state.tasks.filter(task => task.status === 'done').length;
  const confirmed = state.casualties.filter(casualty => casualty.confirmed).length;
  return (
    <main className="min-h-screen bg-bg text-text">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-8 sm:py-10">
        <header className="mb-6 flex flex-wrap items-start justify-between gap-4">
          <div><Link href="/" prefetch={false} className="mono text-xs tracking-widest text-muted hover:text-accent">← AEGIS / SHOWCASE</Link><h1 className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl">Command Center<span className="text-accent">.</span></h1><p className="mt-2 max-w-xl text-sm leading-6 text-muted">Explore human-authorized coordination in a synthetic incident. You set priorities. The browser suggests routes. Nothing moves without your approval.</p></div>
          <div className="flex flex-wrap items-center gap-3"><span className="mono rounded-full border border-line bg-panel px-3 py-2 text-xs text-accent" role="note">Interactive Simulation — No Real Emergency Dispatch</span><ThemeToggle /><button className={control} data-testid="reset" onClick={() => dispatch({ type: 'reset' })}>Reset scenario</button></div>
        </header>

        <SystemIntroduction />

        {state.showHint && <aside className="mb-5 flex items-start justify-between gap-4 rounded-xl border border-line bg-panel p-4 text-sm" aria-label="Getting started"><p className="leading-6"><strong className="text-accent">Your first dispatch.</strong> Confirm C1 and C2, choose First / Next priorities, then generate a recommendation. Review → approve revision → dispatch. Try blocking a crossing during an active assignment to explore authorized replanning.</p><button className={`${control} shrink-0`} data-testid="dismiss-hint" onClick={() => dispatch({ type: 'dismissHint' })} aria-label="Dismiss getting started hint">Dismiss</button></aside>}

        <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[['Incident', 'GRID / 04'], ['Confirmed', `${confirmed} / 4`], ['Active tasks', String(active.length).padStart(2, '0')], ['Tasks closed', String(completed).padStart(2, '0')]].map(([label, value]) => <div key={label} className="rounded-xl border border-line bg-panel px-4 py-3"><p className="mono text-[10px] uppercase tracking-widest text-muted">{label}</p><p className="mono mt-2 text-lg">{value}</p></div>)}
        </div>

        <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
          <div className="space-y-5">
            <section className={panel} aria-labelledby="map-heading"><div className="flex flex-wrap items-center justify-between gap-2"><h2 id="map-heading" className="text-sm font-semibold">01 / Incident map</h2><span className="mono text-[10px] text-muted">7 × 5 / GRID STEPS, NOT REAL DISTANCE</span></div><SandboxMap state={state} onAction={dispatch} /><div className="flex flex-wrap gap-4 text-xs text-muted"><span><span className="text-info">●</span> Alpha</span><span><span className="text-accent">●</span> Bravo</span><span>□ Synthetic casualty</span><span>Dashed route = advisory</span></div><div className="mt-4 flex flex-wrap gap-2">{CORRIDORS.map(corridor => <button key={corridor.id} className={control} data-testid={`corridor-${corridor.id}`} aria-pressed={state.blocked.includes(corridor.id)} onClick={() => dispatch({ type: 'corridor', id: corridor.id })}>{corridor.label}: <span className={state.blocked.includes(corridor.id) ? 'text-danger' : 'text-ok'}>{state.blocked.includes(corridor.id) ? 'blocked' : 'open'}</span></button>)}</div><p className="mt-3 text-xs leading-5 text-muted">Central closure forces a detour. East closure disconnects C2 and C4 from western staging. Crossing controls also support Enter and Space on the map.</p></section>

            <section className={panel} aria-labelledby="responders-heading"><h2 id="responders-heading" className="mb-4 text-sm font-semibold">02 / Virtual responders</h2><HardwareContext /><div className="space-y-3">{state.responders.map(responder => {
              const task = active.find(candidate => candidate.responderId === responder.id);
              return <div key={responder.id} className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-line p-3"><div><p className="text-sm font-semibold">{responder.name} <span className="ml-2 text-xs font-normal text-muted">{task ? task.status === 'blocked' ? 'Held · replacement required' : 'Busy · reserved' : 'Idle'}</span></p><p className="mono mt-1 text-[11px] text-muted">{responder.board} · virtual only</p></div><button className={control} data-testid={`availability-${responder.id}`} aria-pressed={responder.available} aria-label={`${responder.name} availability: ${responder.available ? 'available' : 'offline'}`} onClick={() => dispatch({ type: 'availability', id: responder.id })}>{responder.available ? 'Available' : 'Offline'}</button></div>;
            })}</div><p className="mt-3 text-xs leading-5 text-muted">Availability affects new recommendations, never cancels existing work. No hardware is connected.</p></section>
          </div>

          <div className="space-y-5">
            <CameraIntelligence casualties={state.casualties} />

            <section id="human-review" className={`${panel} scroll-mt-5`} aria-labelledby="casualties-heading"><h2 id="casualties-heading" className="text-sm font-semibold">03 / Human review</h2><p className="mt-2 text-xs leading-5 text-muted">Confirm each synthetic casualty and choose an operator-defined ordering. These are not clinical triage categories; no diagnosis or treatment is simulated.</p><div className="mt-4 space-y-3">{state.casualties.map(casualty => <div key={casualty.id} className="rounded-xl border border-line p-3"><div className="mb-3 flex items-center justify-between gap-2"><p className="text-sm"><span className="mono mr-2 text-accent">{casualty.id}</span>{casualty.label}</p>{state.tasks.some(task => task.casualtyId === casualty.id && task.status === 'done') && <span className="text-xs text-muted">Task closed</span>}</div><p className="mb-3 text-xs leading-5 text-muted">{casualty.observation}</p><div className="grid grid-cols-2 gap-2"><button className={control} data-testid={`confirm-${casualty.id}`} aria-pressed={casualty.confirmed} aria-label={`${casualty.id}: ${casualty.confirmed ? 'confirmed' : 'unconfirmed'}`} onClick={() => dispatch({ type: 'confirm', id: casualty.id })}>{casualty.confirmed ? '✓ Confirmed' : 'Confirm casualty'}</button><label className="sr-only" htmlFor={`priority-${casualty.id}`}>Operator priority for {casualty.id}</label><select id={`priority-${casualty.id}`} data-testid={`priority-${casualty.id}`} className={`${control} min-w-0`} value={casualty.priority} onChange={event => dispatch({ type: 'priority', id: casualty.id, priority: event.target.value as Priority })}><option value="unassessed">Unassessed</option><option value="first">First</option><option value="next">Next</option><option value="later">Later</option></select></div></div>)}</div></section>

            <section className={panel} aria-labelledby="plan-heading"><div className="flex items-center justify-between gap-2"><h2 id="plan-heading" className="text-sm font-semibold">04 / Recommendation desk</h2><span className="mono text-xs text-accent">REV {revision}</span></div><p className="mt-2 text-xs leading-5 text-muted">Browser algorithm: exhaustive distinct assignment enumeration with breadth-first shortest paths. Maximizes First, then Next, then Later assignments; minimizes total grid steps to break ties. Not live OR-Tools, ML, or a real-world optimizer.</p><button className={`${control} mt-4 w-full`} data-testid="recommend" onClick={() => dispatch({ type: 'recommend' })}>Generate advisory recommendation</button>
              <NeuralContext />
              {state.plan ? <div className="mt-4 space-y-3" data-testid="recommendation"><p className="mono text-xs text-muted">ADVISORY / REVISION {state.plan.revision}</p>{state.plan.assignments.map(assignment => <div key={assignment.responderId} className="rounded-lg border border-line p-3 text-sm"><strong className="capitalize">{assignment.responderId}</strong> → {assignment.casualtyId}<span className="ml-2 text-xs text-muted">{assignment.route.length - 1} steps</span>{active.some(task => task.responderId === assignment.responderId && task.status === 'blocked') && <p className="mt-2 text-xs text-warn">Replaces held task for this responder only after authorized dispatch.</p>}</div>)}{!state.plan.assignments.length && <p className="text-sm text-muted">No feasible assignments to authorize.</p>}{state.plan.warnings.map(warning => <p key={warning} className="text-xs leading-5 text-warn">{warning}</p>)}<div className="grid gap-2 sm:grid-cols-2"><button className={control} data-testid="approve" disabled={!state.plan.assignments.length || state.approvedRevision === revision} onClick={() => dispatch({ type: 'approve', revision })}>{state.approvedRevision === revision ? `✓ Revision ${revision} approved` : `Approve revision ${revision}`}</button><button className={`${control} border-accent text-accent`} data-testid="dispatch" disabled={!state.plan.assignments.length || state.approvedRevision !== revision} onClick={() => dispatch({ type: 'dispatch', revision })}>Dispatch virtual assignments</button></div></div> : <p className="mt-4 rounded-lg border border-dashed border-line p-3 text-xs leading-5 text-muted">No current recommendation. Any confirmation, priority, availability, map, or task change invalidates earlier approval.</p>}
            </section>
          </div>
        </div>

        <div role="status" aria-live="polite" data-testid="notice" className="my-5 rounded-xl border border-line bg-panel px-4 py-3 text-sm leading-6">{state.notice}</div>

        <section className={panel} aria-labelledby="tasks-heading"><div className="flex flex-wrap items-center justify-between gap-2"><h2 id="tasks-heading" className="text-sm font-semibold">05 / Virtual task ledger</h2><span className="mono text-[10px] text-muted">DISPATCH → RECEIPT → ACK → DONE</span></div><p className="mt-2 text-xs leading-5 text-muted">Each step is manually simulated. DONE closes a coordination task, not a medical outcome. Blocked tasks cannot progress; reopening a corridor never resumes them automatically.</p>
          {!state.tasks.length && <p className="mt-5 border-t border-line py-5 text-center text-sm text-muted">Awaiting your first authorized dispatch.</p>}
          <div className="mt-4 grid gap-3 md:grid-cols-2">{active.map(task => <article key={task.id} className="rounded-xl border border-line p-4" data-testid={`task-${task.id}`}><div className="flex flex-wrap justify-between gap-2"><h3 className="text-sm font-semibold"><span className="capitalize">{task.responderId}</span> → {task.casualtyId}</h3><span className="mono text-xs text-accent">{task.status.toUpperCase()}</span></div><p className="mono mt-2 text-[11px] text-muted">{task.id} / authorized revision {task.revision}</p><div className="mt-3 flex flex-wrap gap-2"><button className={control} data-testid={`receipt-${task.id}`} disabled={task.status !== 'dispatched'} onClick={() => dispatch({ type: 'progress', taskId: task.id, stage: 'received' })}>Virtual receipt</button><button className={control} data-testid={`ack-${task.id}`} disabled={task.status !== 'received'} onClick={() => dispatch({ type: 'progress', taskId: task.id, stage: 'acknowledged' })}>Virtual ACK</button><button className={control} data-testid={`done-${task.id}`} disabled={task.status !== 'acknowledged'} onClick={() => dispatch({ type: 'progress', taskId: task.id, stage: 'done' })}>Virtual DONE</button></div>{task.status === 'blocked' && <p className="mt-3 text-xs leading-5 text-warn">Route invalidated. Responder remains reserved until a new revision is approved and a replacement is explicitly dispatched.</p>}</article>)}</div>
          {history.length > 0 && <div className="mt-5 border-t border-line pt-4"><h3 className="mono mb-3 text-xs text-muted">PRESERVED HISTORY</h3><ul className="space-y-2">{history.map(task => <li key={task.id} className="flex flex-wrap justify-between gap-2 text-xs text-muted" data-testid={`history-${task.id}`}><span className="mono">{task.id} · {task.responderId} → {task.casualtyId}</span><span>{task.status === 'done' ? 'DONE · task closed' : 'Superseded by authorized replacement'}</span></li>)}</ul></div>}
        </section>
        <ResearchIntelligence />

        <footer className="mt-6 text-xs leading-6 text-muted">Synthetic data only. Independent per visitor, in React memory; reset or reload clears this session. No operational network connections, persistent simulation storage, camera, device connection, live inference, or operational dispatch. The shared showcase theme preference is saved separately. This demonstration is not emergency guidance.</footer>
      </div>
    </main>
  );
}
