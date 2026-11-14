"use client";

import { useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Check, X, Send, RotateCcw, Cpu, UserCheck, Lock, ArrowRight, Clock } from "lucide-react";
import { ExampleTag, Section, SectionHeading } from "./Section";

type Phase = "review" | "approved" | "rejected" | "dispatched";

const REC = {
  revision: 3,
  casualty: "C-001",
  priority: "IMMEDIATE",
  responder: "R-01",
  availability: "Available",
  rationale: [
    "Highest human-confirmed priority among unassigned casualties.",
    "R-01 is available and has a feasible obstacle-aware route.",
    "R-02 remains free for the next confirmed casualty.",
  ],
};

function Row({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[130px_minmax(0,1fr)] gap-3 border-b border-line/70 py-2.5 last:border-0 sm:grid-cols-[150px_minmax(0,1fr)]">
      <dt className="label self-center">{k}</dt>
      <dd className="mono text-[13px] text-text">{children}</dd>
    </div>
  );
}

/** Left: machine advisory. Read-only by design — it has no controls. */
function RecommendationPanel() {
  return (
    <div data-side="left" className="lp-panel-3d relative rounded-[5px] border border-dashed border-accent/45 bg-panel p-6 sm:p-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="mono flex items-center gap-2 text-[12px] uppercase tracking-[0.16em] text-muted">
          <Cpu size={14} className="text-accent" aria-hidden="true" /> AI optimization recommendation
        </p>
        <ExampleTag />
      </div>
      <p className="mono mt-2 inline-flex rounded-[2px] bg-accent/10 px-1.5 py-0.5 text-[10px] uppercase tracking-[0.14em] text-accent">Machine · advisory only</p>
      <dl className="mt-5">
        <Row k="Revision">rev {REC.revision}</Row>
        <Row k="Casualty">{REC.casualty}</Row>
        <Row k="Priority">
          <span className="text-danger">{REC.priority}</span> <span className="text-muted">· confirmed by command</span>
        </Row>
        <Row k="Responder">
          {REC.responder} <span className="text-ok">· {REC.availability}</span>
        </Row>
        <Row k="Recommended">
          <span className="text-accent">
            {REC.responder} → {REC.casualty}
          </span>
        </Row>
        <Row k="Route">
          <span className="text-muted">Shown only when supplied by the optimizer</span>
        </Row>
      </dl>
      <div className="mt-5">
        <p className="label">Rationale</p>
        <ul className="mt-2 space-y-1.5">
          {REC.rationale.map((r) => (
            <li key={r} className="flex gap-2 text-[13.5px] leading-relaxed text-muted">
              <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-accent/70" aria-hidden="true" />
              {r}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Right: human authority. The only place a decision can be made. */
function InteractiveApprovalPanel() {
  const [phase, setPhase] = useState<Phase>("review");

  const status: Record<Phase, { text: string; cls: string; icon: ReactNode }> = {
    review: { text: "Awaiting command decision — nothing has been dispatched.", cls: "border-warn/40 bg-warn/5 text-warn", icon: <Clock size={14} /> },
    approved: {
      text: `Rev ${REC.revision} approved. Not yet dispatched — dispatch is a separate step.`,
      cls: "border-accent/40 bg-accent/5 text-accent",
      icon: <Check size={14} />,
    },
    rejected: { text: "Recommendation rejected. No task sent. The optimizer can propose a new revision.", cls: "border-line-strong bg-panel-2 text-muted", icon: <X size={14} /> },
    dispatched: {
      text: `Dispatch pending acknowledgment from ${REC.responder}'s device. (Example only — no real dispatch was sent.)`,
      cls: "border-warn/50 bg-warn/10 text-warn",
      icon: <Send size={14} />,
    },
  };

  const step = (n: number, label: string, done: boolean, current: boolean) => (
    <li className="flex items-center gap-3">
      <span
        className={`mono flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[11px] transition-colors ${
          done ? "border-accent bg-accent text-on-accent" : current ? "border-warn text-warn" : "border-line-strong text-faint"
        }`}
      >
        {done ? <Check size={12} aria-hidden="true" /> : n}
      </span>
      <span className={`text-[14px] ${done || current ? "text-text" : "text-faint"}`}>{label}</span>
    </li>
  );

  return (
    <div data-side="right" className="lp-panel-3d relative rounded-[5px] border border-line border-l-[3px] border-l-warn bg-bg p-6 sm:p-8">
      <p className="mono flex items-center gap-2 text-[12px] uppercase tracking-[0.16em] text-muted">
        <UserCheck size={14} className="text-warn" aria-hidden="true" /> Command authorization
      </p>
      <p className="mono mt-2 inline-flex rounded-[2px] bg-warn/10 px-1.5 py-0.5 text-[10px] uppercase tracking-[0.14em] text-warn">Human · decision authority</p>

      <ol className="mt-6 space-y-3.5">
        {step(1, "Review recommendation", true, false)}
        {step(2, phase === "rejected" ? "Rejected" : `Approve rev ${REC.revision}`, phase === "approved" || phase === "dispatched", phase === "review")}
        {step(3, "Dispatch to responder device", phase === "dispatched", phase === "approved")}
        {step(4, "Physical acknowledgment", false, phase === "dispatched")}
      </ol>

      <div className="mt-7 flex flex-wrap gap-2.5">
        <button
          type="button"
          disabled={phase !== "review"}
          onClick={() => setPhase("rejected")}
          className="lp-press inline-flex items-center gap-2 rounded-[3px] border border-line-strong px-4 py-2 text-[13.5px] text-text enabled:hover:border-danger/60 enabled:hover:text-danger disabled:opacity-35"
        >
          <X size={14} aria-hidden="true" /> Reject
        </button>
        <button
          type="button"
          disabled={phase !== "review"}
          onClick={() => setPhase("approved")}
          className="lp-press inline-flex items-center gap-2 rounded-[3px] border border-accent/60 bg-accent/10 px-4 py-2 text-[13.5px] text-accent enabled:hover:bg-accent/20 disabled:opacity-35"
        >
          <Check size={14} aria-hidden="true" /> Approve rev {REC.revision}
        </button>
        <button
          type="button"
          disabled={phase !== "approved"}
          onClick={() => setPhase("dispatched")}
          aria-describedby="dispatch-hint"
          className="lp-press inline-flex items-center gap-2 rounded-[3px] bg-accent px-4 py-2 text-[13.5px] font-medium text-on-accent enabled:hover:bg-accent-hover disabled:cursor-not-allowed disabled:bg-panel-2 disabled:text-faint"
        >
          {phase === "approved" ? <Send size={14} aria-hidden="true" /> : <Lock size={14} aria-hidden="true" />} Dispatch
        </button>
      </div>
      <p id="dispatch-hint" className="mt-2 text-[12px] text-faint">
        Dispatch unlocks only after an explicit approval of this exact revision.
      </p>

      <div className="mt-6" role="status" aria-live="polite">
        <AnimatePresence mode="wait" initial={false}>
          <motion.div
            key={phase}
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.22 }}
            className={`flex items-start gap-2.5 rounded-[3px] border px-4 py-3 text-[13.5px] ${status[phase].cls}`}
          >
            <span className={`mt-0.5 ${phase === "dispatched" ? "blink" : ""}`} aria-hidden="true">
              {status[phase].icon}
            </span>
            {status[phase].text}
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        {phase !== "review" ? (
          <button type="button" onClick={() => setPhase("review")} className="mono inline-flex items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-muted hover:text-text">
            <RotateCcw size={12} aria-hidden="true" /> Reset example
          </button>
        ) : (
          <span />
        )}
        {phase === "dispatched" && (
          <a href="#hardware" className="mono inline-flex items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-accent hover:underline">
            See acknowledgment <ArrowRight size={12} aria-hidden="true" />
          </a>
        )}
      </div>
      <p className="mono mt-4 text-right text-[10px] uppercase tracking-[0.12em] text-faint">Example only · nothing is sent</p>
    </div>
  );
}

export function HumanLoop() {
  return (
    <Section id="human-in-the-loop" seed={4}>
      <SectionHeading
        index="04"
        eyebrow="Human in the loop"
        title={
          <>
            AI recommends. <span className="text-accent">Humans decide.</span> Responders act.
          </>
        }
        lede="Try it: the optimizer's recommendation on the left has no controls. Only a commander, on the right, can approve, reject, or dispatch."
      />

      <div className="relative mt-12 grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_56px_minmax(0,1fr)] lg:gap-0">
        <RecommendationPanel />
        <div className="flex items-center justify-center lg:h-full" aria-hidden="true">
          <div className="mono flex items-center gap-2 text-[10px] uppercase tracking-[0.14em] text-faint lg:flex-col">
            <span className="h-px w-8 bg-line-strong lg:h-10 lg:w-px" />
            <ArrowRight size={14} className="rotate-90 text-warn lg:rotate-0" />
            <span className="lg:[writing-mode:vertical-rl]">for decision</span>
          </div>
        </div>
        <InteractiveApprovalPanel />
      </div>

      <blockquote className="mx-auto mt-14 max-w-3xl border-l-2 border-accent pl-5 text-[18px] leading-relaxed text-text sm:text-[20px]">
        AEGIS keeps critical decisions accountable by placing human authorization at the center of every dispatch.
      </blockquote>
    </Section>
  );
}
