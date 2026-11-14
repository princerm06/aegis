"use client";

import { useState } from "react";
import { Check, Clock, RotateCcw, Send } from "lucide-react";
import { ExampleTag, Section, SectionHeading } from "./Section";

type HwState = "idle" | "pending" | "acked";

const STEPS = [
  { label: "Incoming task notification", at: "pending" },
  { label: "LED indication", at: "pending" },
  { label: "Pending acknowledgment", at: "pending", waiting: true },
  { label: "Physical button press", at: "acked" },
  { label: "Acknowledgment received", at: "acked" },
  { label: "Dashboard status updated", at: "acked" },
] as const;

// 12×8 LED matrix glyphs (col,row)
const BANG: [number, number][] = [[5, 1], [6, 1], [5, 2], [6, 2], [5, 3], [6, 3], [5, 4], [6, 4], [5, 6], [6, 6]];
const TICK: [number, number][] = [[2, 4], [3, 5], [4, 6], [5, 5], [6, 4], [7, 3], [8, 2], [9, 1]];

function Board({ state, onPress }: { state: HwState; onPress: () => void }) {
  const pending = state === "pending";
  const acked = state === "acked";
  const lit = pending ? BANG : acked ? TICK : [];
  const litSet = new Set(lit.map(([c, r]) => `${c},${r}`));
  const ledColor = pending ? "var(--warn)" : acked ? "var(--accent)" : "var(--line-strong)";

  const press = () => pending && onPress();

  return (
    <svg viewBox="0 0 560 330" className="h-auto w-full" role="group" aria-label="Arduino UNO R4 WiFi responder device with a Grove LED and a Grove button">
      <defs>
        <filter id="hw-glow" x="-100%" y="-100%" width="300%" height="300%">
          <feGaussianBlur stdDeviation="6" />
        </filter>
      </defs>

      {/* cables */}
      <path d="M318 100 C360 100, 360 90, 400 90" fill="none" stroke="var(--line-strong)" strokeWidth="5" strokeLinecap="round" />
      <path d="M318 210 C360 210, 360 245, 400 245" fill="none" stroke="var(--line-strong)" strokeWidth="5" strokeLinecap="round" />
      {pending && <path d="M318 100 C360 100, 360 90, 400 90" pathLength={100} fill="none" stroke="var(--warn)" strokeWidth="3" strokeLinecap="round" className="lp-packet" />}
      {acked && <path d="M400 245 C360 245, 360 210, 318 210" pathLength={100} fill="none" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" className="lp-packet" />}

      {/* board */}
      <rect x="40" y="45" width="280" height="220" rx="10" fill="#0c2730" stroke="var(--accent)" strokeOpacity="0.35" />
      <rect x="26" y="82" width="26" height="30" rx="3" fill="#7f909f" />
      <text x="58" y="100" className="mono" fontSize="8" fill="#9fb0bf">USB-C</text>
      <rect x="24" y="190" width="36" height="42" rx="3" fill="#141b21" stroke="#2a3a47" />
      {Array.from({ length: 16 }, (_, i) => (
        <rect key={`t${i}`} x={130 + i * 11} y="54" width="7" height="7" fill="#141b21" stroke="#3a4a57" strokeWidth="0.6" />
      ))}
      {Array.from({ length: 14 }, (_, i) => (
        <rect key={`b${i}`} x={152 + i * 11} y="248" width="7" height="7" fill="#141b21" stroke="#3a4a57" strokeWidth="0.6" />
      ))}

      {/* LED matrix */}
      <rect x="96" y="96" width="112" height="78" rx="3" fill="#081a20" stroke="#1d3d47" />
      {Array.from({ length: 8 }, (_, r) =>
        Array.from({ length: 12 }, (_, c) => {
          const on = litSet.has(`${c},${r}`);
          return (
            <circle
              key={`${c}-${r}`}
              cx={106 + c * 8.5}
              cy={106 + r * 8.5}
              r="2.4"
              fill={on ? ledColor : "#16303a"}
              className={on && pending ? "blink" : undefined}
            />
          );
        }),
      )}
      <text x="96" y="190" className="mono" fontSize="8" fill="#9fb0bf">12×8 LED MATRIX</text>

      {/* MCU module */}
      <rect x="226" y="140" width="76" height="56" rx="3" fill="#1a232b" stroke="#3a4a57" />
      <text x="264" y="166" className="mono" fontSize="8" fill="#9fb0bf" textAnchor="middle">WiFi</text>
      <text x="264" y="178" className="mono" fontSize="8" fill="#9fb0bf" textAnchor="middle">module</text>

      {/* Grove ports */}
      <rect x="300" y="92" width="18" height="16" fill="#e5e7eb" opacity="0.85" />
      <rect x="300" y="202" width="18" height="16" fill="#e5e7eb" opacity="0.85" />
      <text x="74" y="226" className="mono" fontSize="10" fontWeight="600" fill="#e6eef5">ARDUINO UNO R4 WiFi</text>
      <text x="74" y="239" className="mono" fontSize="8" fill="#9fb0bf">responder device · Grove shield</text>

      {/* Grove LED */}
      <rect x="400" y="40" width="120" height="100" rx="4" fill="var(--panel-2)" stroke="var(--line-strong)" />
      <text x="410" y="56" className="mono" fontSize="8.5" fill="var(--muted)">GROVE LED</text>
      {state !== "idle" && <circle cx="460" cy="92" r="20" fill={ledColor} opacity="0.55" filter="url(#hw-glow)" className={pending ? "blink" : undefined} />}
      <circle cx="460" cy="92" r="13" fill={ledColor} stroke={state === "idle" ? "var(--faint)" : ledColor} className={pending ? "blink" : undefined} />
      <text x="460" y="128" className="mono" fontSize="8.5" fill={pending ? "var(--warn)" : acked ? "var(--accent)" : "var(--faint)"} textAnchor="middle">
        {pending ? "BLINKING · TASK" : acked ? "SOLID · ACKED" : "OFF"}
      </text>

      {/* Grove button (interactive) */}
      <rect x="400" y="190" width="120" height="110" rx="4" fill="var(--panel-2)" stroke={pending ? "var(--warn)" : "var(--line-strong)"} strokeDasharray={pending ? "4 3" : undefined} />
      <text x="410" y="206" className="mono" fontSize="8.5" fill="var(--muted)">GROVE BUTTON</text>
      <g
        role="button"
        tabIndex={pending ? 0 : -1}
        aria-disabled={!pending}
        aria-label="Press the Grove button to acknowledge the task"
        onClick={press}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            press();
          }
        }}
        style={{ cursor: pending ? "pointer" : "default", outline: "none" }}
        className="group"
      >
        <rect x="438" y="222" width="44" height="44" rx="4" fill="var(--panel)" stroke="var(--line-strong)" />
        <circle cx="460" cy="244" r="15" fill={acked ? "var(--accent-dim)" : "var(--line-strong)"} stroke={pending ? "var(--warn)" : "var(--faint)"} strokeWidth="2" className="transition-all group-hover:brightness-125 group-focus-visible:stroke-[var(--accent)]" />
        {pending && <circle cx="460" cy="244" r="15" fill="none" stroke="var(--warn)" className="pulse-ring" />}
      </g>
      <text x="460" y="288" className="mono" fontSize="8.5" fill={pending ? "var(--warn)" : "var(--faint)"} textAnchor="middle">
        {pending ? "CLICK TO PRESS" : acked ? "PRESSED" : "IDLE"}
      </text>
    </svg>
  );
}

export function Hardware() {
  const [state, setState] = useState<HwState>("idle");
  const reached = (at: "pending" | "acked") => (at === "pending" ? state !== "idle" : state === "acked");

  return (
    <Section id="hardware" seed={6}>
      <SectionHeading
        index="05"
        eyebrow="Physical response"
        title="From digital intelligence to physical response."
        lede="A dispatched task doesn't end on a screen. It lights an LED on the responder's device, and the loop closes only when someone physically presses the button."
      />

      <div className="mt-12 grid gap-6 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]">
        <div className="lp-card lp-corners p-4 sm:p-6">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <span className="label">Responder device · R-01</span>
            <ExampleTag>Interactive diagram</ExampleTag>
          </div>
          <Board state={state} onPress={() => setState("acked")} />
          <div className="mt-4 flex flex-wrap items-center gap-3">
            {state === "idle" && (
              <button
                type="button"
                onClick={() => setState("pending")}
                className="inline-flex items-center gap-2 lp-press rounded-[3px] bg-accent px-4 py-2 text-[13.5px] font-medium text-on-accent hover:bg-accent-hover"
              >
                <Send size={14} aria-hidden="true" /> Send example task
              </button>
            )}
            {state === "pending" && (
              <>
                <p className="text-[13.5px] text-warn">Task delivered. Press the Grove button on the device to acknowledge.</p>
                <button
                  type="button"
                  onClick={() => setState("acked")}
                  className="lp-press rounded-[3px] border border-warn/50 px-3 py-1.5 text-[12.5px] text-warn hover:bg-warn/10"
                >
                  Press button
                </button>
              </>
            )}
            {state === "acked" && (
              <button
                type="button"
                onClick={() => setState("idle")}
                className="mono inline-flex items-center gap-1.5 text-[11px] uppercase tracking-[0.12em] text-muted hover:text-text"
              >
                <RotateCcw size={12} aria-hidden="true" /> Reset
              </button>
            )}
          </div>
        </div>

        <div className="flex flex-col gap-6">
          <ol className="lp-card p-5" aria-label="Acknowledgment sequence">
            {STEPS.map((s, i) => {
              const done = reached(s.at) && !("waiting" in s && state === "pending");
              const waiting = "waiting" in s && state === "pending";
              return (
                <li key={s.label} className="flex items-center gap-3 border-b border-line/60 py-2.5 last:border-0">
                  <span
                    className={`mono flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-[10.5px] ${
                      waiting ? "border-dashed border-warn text-warn" : done ? "border-accent bg-accent text-on-accent" : "border-line-strong text-faint"
                    }`}
                  >
                    {done ? <Check size={12} aria-hidden="true" /> : waiting ? <Clock size={11} aria-hidden="true" /> : i + 1}
                  </span>
                  <span className={`text-[14px] ${waiting ? "text-warn" : done ? "text-text" : "text-faint"}`}>{s.label}</span>
                </li>
              );
            })}
          </ol>

          <div className="lp-card p-5">
            <p className="label">Command center · task row</p>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-3 rounded-[3px] border border-line bg-bg/60 px-3 py-3">
              <span className="mono text-[13px] text-text">R-01 → C-001</span>
              {state === "idle" && <span className="mono text-[11px] uppercase tracking-[0.1em] text-faint">No task dispatched</span>}
              {state === "pending" && (
                <span className="mono inline-flex items-center gap-1.5 rounded-[2px] border border-dashed border-warn/70 px-2 py-0.5 text-[11px] uppercase tracking-[0.1em] text-warn">
                  <Clock size={11} aria-hidden="true" /> Pending acknowledgment
                </span>
              )}
              {state === "acked" && (
                <span className="mono inline-flex items-center gap-1.5 rounded-[2px] border border-accent bg-accent/15 px-2 py-0.5 text-[11px] uppercase tracking-[0.1em] text-accent">
                  <Check size={11} aria-hidden="true" /> Acknowledged · via device
                </span>
              )}
            </div>
            <p className="mt-3 text-[12.5px] leading-relaxed text-muted">
              <span className="text-warn">Pending</span> means the task was sent but no one has confirmed it.{" "}
              <span className="text-accent">Acknowledged</span> means the responder pressed the button — the dashboard labels whether it came from
              the device or a rehearsal control.
            </p>
          </div>
        </div>
      </div>
    </Section>
  );
}
