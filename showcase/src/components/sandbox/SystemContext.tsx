import { Camera, CircuitBoard, GitBranch, ScanEye } from 'lucide-react';
import { CAMERA_OBSERVATIONS, EMS_RESEARCH, RECORDED_CV, SYSTEM_STAGES } from '@/lib/sandbox/context';
import type { Casualty } from '@/lib/sandbox/engine';

const details = 'rounded-xl border border-line bg-panel p-4 text-xs leading-5 text-muted';
const summary = 'cursor-pointer rounded-sm font-medium text-text focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent';

export function SystemIntroduction() {
  return (
    <section aria-label="AEGIS system context" data-testid="system-context" className="mb-5 rounded-2xl border border-accent/30 bg-panel p-4 sm:p-5">
      <div className="flex items-center gap-2 text-accent"><ScanEye size={16} aria-hidden="true" /><p className="mono text-[10px] uppercase tracking-widest">Camera-supported. AI-assisted. Human-authorized.</p></div>
      <p className="mt-3 max-w-4xl text-sm leading-6 text-text">AEGIS connects computer vision, human assessments, mathematical optimization, neural intelligence, and physical responder hardware to support simulated incident command.</p>
      <p className="mt-2 max-w-4xl text-xs leading-5 text-muted">The full HackPrinceton prototype operates locally with actual cameras, backend optimization, trained models, and connected Arduino devices. This public sandbox is an isolated, browser-only representation of that workflow—not the live system.</p>
      <a href="#human-review" className="mt-3 inline-block rounded-sm text-xs font-medium text-accent underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent">Begin casualty review ↓</a>
      <details className="mt-4 border-t border-line pt-3" data-testid="full-system-details">
        <summary className={`${summary} text-xs`}>How the Full AEGIS System Works <span className="ml-2 text-faint">/ 8 steps</span></summary>
        <ol className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {SYSTEM_STAGES.map(([title, description], index) => <li key={title} className="rounded-lg border border-line p-3"><p className="text-xs font-medium"><span className="mono mr-2 text-accent">{String(index + 1).padStart(2, '0')} →</span>{title}</p><p className="mt-1 text-xs leading-5 text-muted">{description}</p></li>)}
        </ol>
        <p className="mt-3 text-xs leading-5 text-muted">These stages describe the full prototype. Here, independent browser assignment enumeration and grid shortest paths stand in for backend computation; virtual controls stand in for device events.</p>
      </details>
    </section>
  );
}

export function CameraIntelligence({ casualties }: { casualties: Casualty[] }) {
  return (
    <section className="lp-card rounded-2xl border border-line bg-panel p-4 sm:p-5" aria-labelledby="camera-heading" data-testid="camera-intelligence">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 id="camera-heading" className="flex items-center gap-2 text-sm font-semibold"><Camera size={16} className="text-accent" aria-hidden="true" />Camera Intelligence</h2><span className="mono rounded-full border border-line px-2 py-1 text-[10px] text-warn">SYNTHETIC FIXTURES</span></div>
      <p className="mt-2 text-xs leading-5 text-muted">Illustrative person detections, not camera input or live inference. Frozen scenario snapshot at T+00:12; times below are last-observed offsets, not a live freshness feed. No confidence scores are asserted.</p>
      <div className="mt-3 grid gap-2 min-[360px]:grid-cols-2">
        {CAMERA_OBSERVATIONS.map(observation => {
          const confirmed = casualties.find(casualty => casualty.id === observation.casualtyId)?.confirmed ?? false;
          return <article key={observation.personId} className="rounded-lg border border-line p-3" data-testid={`observation-${observation.casualtyId}`}><div className="flex flex-wrap items-center justify-between gap-1"><h3 className="mono text-xs text-accent">{observation.personId} → {observation.casualtyId}</h3><span className="text-[10px] text-muted">{observation.timestamp}</span></div><p className="mono mt-1 text-[10px] text-faint">{observation.camera}</p><p className="mt-2 text-xs text-text">{observation.visibility}</p><p className="mt-1 text-xs leading-5 text-muted">Posture: {observation.posture} · Movement: {observation.movement}</p><p className={`mt-2 text-[11px] ${confirmed ? 'text-ok' : 'text-warn'}`} data-testid={`observation-confirmation-${observation.casualtyId}`}>{confirmed ? 'Human-confirmed in sandbox' : 'Awaiting explicit human confirmation'}</p></article>;
        })}
      </div>
      <p className="mt-3 text-xs leading-5 text-muted">A detected person is not automatically a confirmed casualty. Occluded/lost tracks cannot support current sign assessments. Use Human review below to confirm and set priorities.</p>
      <details className="mt-3 border-t border-line pt-3" data-testid="recorded-cv-details"><summary className={`${summary} text-xs`}>Recorded CV evidence / separate research clip</summary><p className="mt-3 text-xs leading-5 text-muted">Verified recorded output, not this scenario: track <strong className="text-text">{RECORDED_CV.track}</strong> in <span className="break-all">{RECORDED_CV.clip}</span> first reports lying at {RECORDED_CV.firstLyingAtSeconds}s, moving at {RECORDED_CV.firstMovingAtSeconds}s, and still at {RECORDED_CV.firstStillAtSeconds}s. These are first-observed clip offsets, not confidence or accuracy scores.</p><p className="mt-2 text-xs leading-5 text-muted">Recorded analysis run: <time dateTime={RECORDED_CV.runAtUtc}>{RECORDED_CV.runAtUtc}</time>. It is not mapped to C1–C4, used for priorities, or replayed as live inference.</p><p className="mt-2 text-[10px] leading-5 text-faint">Repository provenance: <code className="break-all">{RECORDED_CV.source}</code>. Exact source hash is documented in showcase/PROVENANCE.md.</p></details>
    </section>
  );
}

export function NeuralContext() {
  return (
    <details className={`${details} mt-4`} data-testid="neural-context">
      <summary className={summary}><span className="inline-flex items-center gap-2"><GitBranch size={14} className="text-accent" aria-hidden="true" />Neural Intelligence / full prototype vs this browser</span></summary>
      <dl className="mt-3 space-y-3">
        <div><dt className="font-medium text-text">OR-Tools CP-SAT + A*</dt><dd>Authoritative constraint-based assignment optimization and operational route planning. Only the mathematical proposal can be approved for dispatch.</dd></div>
        <div><dt className="font-medium text-text">Pairwise MLP</dt><dd>Learns responder–casualty compatibility scores for constrained advisory matching.</dd></div>
        <div><dt className="font-medium text-text">Bipartite GNN</dt><dd>Learns assignments from the graph of responders and casualties, with constrained decoding and independent feasibility checks.</dd></div>
      </dl>
      <p className="mt-3">When enabled, the actual backend compares learned advisory recommendations against mathematical optimization. Neural recommendations never authorize dispatch.</p>
      <p className="mt-3 border-t border-line pt-3 text-text">This sandbox runs independent assignment enumeration and breadth-first grid routing only. No neural weights, inference, recorded neural scores, or live OR-Tools execution.</p>
    </details>
  );
}

export function ResearchIntelligence() {
  return (
    <section className="mt-5 rounded-xl border border-line bg-panel p-4" aria-labelledby="research-heading" data-testid="research-intelligence">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2 id="research-heading" className="text-sm font-semibold">Research Intelligence / NYC EMS</h2><span className="mono text-[10px] text-accent">RECORDED EVALUATION · NOT EXECUTED HERE</span></div>
      <p className="mt-2 text-xs leading-5 text-muted">Separate XGBoost research predicts historical incident creation to first assignment duration—not arrival time, route travel, or responder-assignment optimization.</p>
      <dl className="mt-3 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          ['Source incidents', EMS_RESEARCH.sourceIncidents.toLocaleString('en-US')],
          ['Held-out incidents', EMS_RESEARCH.heldOutIncidents.toLocaleString('en-US')],
          ['XGBoost MAE', `${EMS_RESEARCH.xgboostMaeSeconds.toFixed(2)} seconds`],
          ['Temporal baseline MAE', `${EMS_RESEARCH.temporalMaeSeconds.toFixed(2)} seconds`],
        ].map(([label, value]) => <div key={label} className="rounded-lg border border-line p-3"><dt className="text-[11px] leading-5 text-muted">{label}</dt><dd className="mono mt-1 text-sm text-text">{value}</dd></div>)}
      </dl>
      <p className="mt-3 text-xs leading-5 text-warn">Aggregate gains were concentrated in {EMS_RESEARCH.severity8Incidents} rare severity-8 incidents; typical groups showed small gains or regressions. This is not evidence of broad clinical value. The sandbox does not execute XGBoost.</p>
      <details className="mt-3 border-t border-line pt-3" data-testid="research-details"><summary className={`${summary} text-xs`}>Evaluation scope & provenance</summary><p className="mt-3 text-xs leading-5 text-muted">The source snapshot covers January–March 2023, not the entire NYC EMS dataset. After quality filtering and label-availability exclusions, chronological training uses January–February, validation March 1–15, and held-out testing March 16–31. The temporal baseline is the training-only hour/weekday median selected on validation error.</p><p className="mt-2 text-xs leading-5 text-muted">Reported mean absolute errors are aggregate historical evaluation results, not live predictions or a claim of operational readiness. Source: <code className="break-all">{EMS_RESEARCH.source}</code>; caveats: <code className="break-all">{EMS_RESEARCH.auditSource}</code>. Verified excerpts and source hashes are documented in showcase/PROVENANCE.md.</p></details>
    </section>
  );
}

export function HardwareContext() {
  return <p className="mb-4 rounded-lg border border-line p-3 text-xs leading-5 text-muted"><CircuitBoard size={14} className="mr-2 inline text-accent" aria-hidden="true" />The full prototype integrates physical Arduino responder devices: <strong className="text-text">UNO R4 WiFi — Alpha</strong> and <strong className="text-text">UNO Q — Bravo</strong>. Devices receive approved assignments and transmit acknowledgment/completion events. Here, receipt, ACK, and DONE are browser-only button simulations; no hardware is connected.</p>;
}
