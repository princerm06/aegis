"use client";

import { useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Camera, Cpu, LayoutDashboard, RadioTower } from "lucide-react";
import { Section, SectionHeading } from "./Section";

const LAYERS = [
  {
    key: "perception",
    name: "Perception",
    icon: Camera,
    role: "Sees the scene and places people on the floor plan.",
    desc: "A webcam feeds YOLO person detection; ByteTrack keeps identities stable across frames; OpenCV homography projects each track onto floor coordinates in meters.",
    items: ["Logitech webcam", "YOLO", "ByteTrack", "OpenCV", "Homography calibration"],
    sends: "Tracks + scene positions",
  },
  {
    key: "intelligence",
    name: "Intelligence",
    icon: Cpu,
    role: "Holds scenario state, recommends assignments, plans routes.",
    desc: "A FastAPI service in Python owns the revisioned scenario state. Google OR-Tools proposes assignments from human-confirmed priorities; A* plans obstacle-aware routes; a structured LLM advisory can explain — never decide.",
    items: ["FastAPI", "Python", "Google OR-Tools", "A* pathfinding", "LLM advisory"],
    sends: "Revisioned state over REST + WebSocket",
  },
  {
    key: "command",
    name: "Command",
    icon: LayoutDashboard,
    role: "Where a human reviews, approves, and dispatches.",
    desc: "A Next.js + TypeScript command center renders the scene in SVG, receives live state over WebSockets, and sends human decisions back over REST with idempotency keys.",
    items: ["Next.js", "TypeScript", "Tailwind", "SVG / Canvas", "REST", "WebSockets"],
    sends: "Approved dispatch commands",
  },
  {
    key: "physical",
    name: "Physical response",
    icon: RadioTower,
    role: "Puts the task in the responder's hands and confirms receipt.",
    desc: "An Arduino UNO R4 WiFi lights a Grove LED for an incoming task; the responder presses a Grove button to acknowledge, reported back over serial communication.",
    items: ["Arduino UNO R4 WiFi", "Grove button", "Grove LED", "Serial communication"],
    sends: "Delivered / acknowledged events",
  },
];

// Isometric plate geometry (viewBox 0 0 560 520). L1 at the bottom.
const CX = 170;
const W = 130;
const H = 55;
const T = 10;
const CY = [440, 320, 200, 80];

const top = (cy: number) => `${CX},${cy - H} ${CX + W},${cy} ${CX},${cy + H} ${CX - W},${cy}`;
const right = (cy: number) => `${CX + W},${cy} ${CX},${cy + H} ${CX},${cy + H + T} ${CX + W},${cy + T}`;
const left = (cy: number) => `${CX - W},${cy} ${CX},${cy + H} ${CX},${cy + H + T} ${CX - W},${cy + T}`;

export function TechnologyArchitecture() {
  const reduced = useReducedMotion();
  const [selected, setSelected] = useState(1);
  const [hover, setHover] = useState<number | null>(null);
  const focus = hover ?? selected;
  const L = LAYERS[selected];

  return (
    <Section id="technology" seed={3}>
      <div className="lp-grid lp-grid-fade pointer-events-none absolute inset-0 -z-10 opacity-60" aria-hidden="true" />
      <SectionHeading
        index="03"
        eyebrow="Technology"
        title="Intelligence across the entire response pipeline."
        lede="Four layers, each with a narrow job and a clear interface to the next. Data rises through the stack; acknowledgments flow back down."
      />

      <div className="mt-12 grid items-center gap-10 lg:grid-cols-[minmax(0,6fr)_minmax(0,5fr)]">
        {/* Isometric stack */}
        <div className="relative">
          <svg viewBox="0 0 560 520" className="h-auto w-full" role="group" aria-label="Architecture stack: Perception, Intelligence, Command, Physical response. Select a layer for details.">
            {LAYERS.map((layer, i) => {
              const cy = CY[i];
              const on = i === focus;
              const sel = i === selected;
              const n = layer.items.length;
              return (
                <g
                  key={layer.key}
                  className="lp-plate"
                  role="button"
                  tabIndex={0}
                  aria-pressed={sel}
                  aria-label={`${layer.name} layer`}
                  onClick={() => setSelected(i)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(i);
                    }
                  }}
                  onPointerEnter={() => setHover(i)}
                  onPointerLeave={() => setHover(null)}
                  onFocus={() => setHover(i)}
                  onBlur={() => setHover(null)}
                  style={{ transform: on ? "translateY(-12px)" : "none", opacity: on ? 1 : 0.62 }}
                >
                  <polygon points={left(cy)} style={{ fill: "color-mix(in srgb, var(--accent) 10%, var(--panel-2))" }} stroke="var(--line-strong)" />
                  <polygon points={right(cy)} style={{ fill: "color-mix(in srgb, var(--accent) 16%, var(--panel-2))" }} stroke="var(--line-strong)" />
                  <polygon
                    points={top(cy)}
                    style={{ fill: on ? "color-mix(in srgb, var(--accent) 16%, var(--panel))" : "color-mix(in srgb, var(--accent) 5%, var(--panel))" }}
                    stroke={on ? "var(--accent)" : "var(--line-strong)"}
                    strokeWidth={on ? 1.5 : 1}
                  />
                  {/* item chips on the plate surface */}
                  {layer.items.map((it, k) => {
                    const o = k - (n - 1) / 2;
                    const x = CX + o * 24;
                    const y = cy + o * 10;
                    return (
                      <polygon
                        key={it}
                        points={`${x},${y - 6} ${x + 12},${y} ${x},${y + 6} ${x - 12},${y}`}
                        fill={on ? "var(--accent)" : "var(--line-strong)"}
                        opacity={on ? 0.85 : 0.7}
                      />
                    );
                  })}
                  {/* label */}
                  <path d={`M${CX + W + 4} ${cy} H${CX + W + 22}`} stroke={on ? "var(--accent)" : "var(--line-strong)"} />
                  <text x={CX + W + 28} y={cy - 2} className="mono" fontSize="13" fontWeight={600} letterSpacing="2" fill={on ? "var(--text)" : "var(--muted)"}>
                    {layer.name.toUpperCase()}
                  </text>
                  <text x={CX + W + 28} y={cy + 14} className="mono" fontSize="10" fill="var(--faint)">
                    L{i + 1} · {n} components
                  </text>
                </g>
              );
            })}

            {/* upward data flow between plates (right side) */}
            {[0, 1, 2].map((i) => {
              const x = CX + W * 0.6;
              const y1 = CY[i] - H * 0.4;
              const y2 = CY[i + 1] + H * 0.4 + T;
              const d = `M${x} ${y1} V${y2}`;
              return (
                <g key={i} aria-hidden="true">
                  <path d={d} stroke="var(--accent)" strokeOpacity="0.35" />
                  {!reduced && <path d={d} pathLength={100} stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" className="lp-packet" style={{ animationDelay: `${i * 0.7}s` }} />}
                  <path d={`M${x - 4} ${y2 + 6} L${x} ${y2} L${x + 4} ${y2 + 6}`} fill="none" stroke="var(--accent)" />
                </g>
              );
            })}
            {/* acknowledgment back down: Physical → Intelligence (left side) */}
            <g aria-hidden="true">
              {(() => {
                const x = CX - W * 0.6;
                const d = `M${x} ${CY[3] + H * 0.4 + T} V${CY[1] - H * 0.4}`;
                return (
                  <>
                    <path d={d} stroke="var(--warn)" strokeOpacity="0.55" strokeDasharray="4 4" />
                    {!reduced && <path d={d} pathLength={100} stroke="var(--warn)" strokeWidth="3" strokeLinecap="round" className="lp-packet-slow" />}
                    <text x={x - 8} y={(CY[2] + CY[3]) / 2 + 30} className="mono" fontSize="9.5" fill="var(--warn)" textAnchor="end">
                      ACK
                    </text>
                  </>
                );
              })()}
            </g>
          </svg>
        </div>

        {/* Layer detail */}
        <div>
          <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:hidden" role="group" aria-label="Choose a layer">
            {LAYERS.map((layer, i) => (
              <button
                key={layer.key}
                type="button"
                aria-pressed={i === selected}
                onClick={() => setSelected(i)}
                className={`lp-press mono rounded-[3px] border px-2 py-2 text-[11px] uppercase tracking-[0.1em] ${
                  i === selected ? "border-accent/60 bg-accent/10 text-accent" : "border-line text-muted"
                }`}
              >
                L{i + 1} {layer.name.split(" ")[0]}
              </button>
            ))}
          </div>
          <div style={{ perspective: "1200px" }}>
            <AnimatePresence mode="wait" initial={false}>
              <motion.article
                key={L.key}
                initial={{ opacity: 0, z: -70, rotateY: -6 }}
                animate={{ opacity: 1, z: 0, rotateY: 0 }}
                exit={{ opacity: 0, z: -50, rotateY: 5 }}
                transition={{ duration: reduced ? 0 : 0.3, ease: [0.2, 0.7, 0.2, 1] }}
                className="lp-card lp-corners p-6 sm:p-7"
                aria-live="polite"
              >
                <header className="flex items-center justify-between">
                  <span className="flex items-center gap-3">
                    <span className="flex h-9 w-9 items-center justify-center rounded-[3px] border border-accent/40 bg-accent/10 text-accent">
                      <L.icon size={17} aria-hidden="true" />
                    </span>
                    <h3 className="mono text-[13px] font-medium uppercase tracking-[0.16em] text-text">{L.name}</h3>
                  </span>
                  <span className="mono text-[11px] text-faint">L{selected + 1} / 4</span>
                </header>
                <p className="mt-4 text-[15px] font-medium text-text">{L.role}</p>
                <p className="mt-2 text-[14px] leading-relaxed text-muted">{L.desc}</p>
                <ul className="mt-5 flex flex-wrap gap-2">
                  {L.items.map((it) => (
                    <li key={it} className="mono rounded-[2px] border border-accent/40 bg-accent/10 px-2 py-1 text-[11.5px] text-accent">
                      {it}
                    </li>
                  ))}
                </ul>
                <dl className="mono mt-5 grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 border-t border-line pt-4 text-[12px]">
                  <dt className="uppercase tracking-[0.1em] text-faint">Receives</dt>
                  <dd className="text-text">{selected === 0 ? "Camera frames" : LAYERS[selected - 1].sends}</dd>
                  <dt className="uppercase tracking-[0.1em] text-faint">Sends</dt>
                  <dd className="text-accent">{L.sends}</dd>
                  {(selected === 1 || selected === 3) && (
                    <>
                      <dt className="uppercase tracking-[0.1em] text-faint">Feedback</dt>
                      <dd className="text-warn">{selected === 3 ? "Acknowledgments → Intelligence" : "Acknowledgments from Physical"}</dd>
                    </>
                  )}
                </dl>
              </motion.article>
            </AnimatePresence>
          </div>
        </div>
      </div>
    </Section>
  );
}
